#!/usr/bin/env python
"""Design My Video analyzer.

Turns a rendered edit (MP4/MOV) and/or its FCPXML into data and images for review:
  analysis/<name>/analysis.json   everything measured
  analysis/<name>/digest.md       compact human/Claude-readable summary
  analysis/<name>/transcript.srt  timed transcript
  analysis/<name>/frames/         one key frame per shot
  analysis/<name>/sheets/         contact sheets (16 frames each, timecoded)

Usage:
  .venv/bin/python analyze.py --video inputs/film.mp4 --fcpxml "inputs/Film.fcpxmld" [--end 603]
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

import numpy as np

from dmv import audio, fcpxml, video
from dmv.progress import Stages, Task
from dmv.timecode import mmss, tc

ROOT = Path(__file__).resolve().parent


def srt(segments):
    def t(s):
        ms = int(round(s * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "\n".join(f"{i}\n{t(s['start'])} --> {t(s['end'])}\n{s['text']}\n" for i, s in enumerate(segments, 1))


def colour_jumps(shots, top=12):
    out = []
    for a, b in zip(shots, shots[1:]):
        dl, dw, ds = b["luma"] - a["luma"], b["warmth"] - a["warmth"], b["sat"] - a["sat"]
        score = abs(dl) / 0.1 + abs(dw) / 0.05 + abs(ds) / 0.1
        out.append({"at": b["start"], "fromShot": a["index"], "toShot": b["index"], "score": round(score, 2),
                    "dLuma": round(dl, 3), "dWarmth": round(dw, 3), "dSat": round(ds, 3)})
    return sorted(out, key=lambda j: -j["score"])[:top]


def beat_sync(cuts, beats, window=0.08):
    if not cuts or len(beats) < 4:
        return None
    beats = np.array(beats)
    d = [float(np.min(np.abs(beats - c))) for c in cuts]
    on = sum(x <= window for x in d)
    chance = min(1.0, 2 * window / float(np.median(np.diff(beats))))
    return {"cuts": len(cuts), "onBeat": on, "ratio": round(on / len(cuts), 2), "chanceRatio": round(chance, 2),
            "windowSec": window}


def per_minute(values, key, dur):
    out = []
    for m in range(int(np.ceil(dur / 60))):
        v = [x[key] for x in values if m * 60 <= x["t"] < (m + 1) * 60 and x.get(key) is not None]
        out.append(round(float(np.mean(v)), 3) if v else None)
    return out


def build_digest(r):
    L = []
    w = L.append
    meta = r.get("video") or {}
    edit = r.get("timeline")
    w(f"# Analysis digest: {r['name']}\n")
    if meta:
        w(f"- Video: {meta['width']}x{meta['height']} @ {meta['fps']} fps, {mmss(meta['duration'])} "
          f"({meta['videoCodec']}/{meta['audioCodec']}, {meta['audioChannels']}ch, {meta['sizeMB']} MB)")
    if r.get("analysedUntil"):
        w(f"- Analysed up to {mmss(r['analysedUntil'])} (rest of file ignored)")
    if edit:
        w(f"- Timeline: {edit['project']} · {mmss(edit['duration'])} · {edit['resolution']} @ {edit['fps']} fps")
        w(f"- Items: {edit['counts']} · disabled {edit['disabledItems']} · lanes {edit['lanes'][0]}..{edit['lanes'][-1]}")
        w(f"- Transitions: {edit['transitions'] or 'none'}")
        w(f"- Video filters: {edit['videoFilters'] or 'none'}")
        w(f"- Generators: {edit['generators'] or 'none'}")
        if edit.get("tailNote"):
            w(f"- ⚠ {edit['tailNote']}")
    w("")

    shots = r.get("shots")
    if shots:
        lengths = [s["length"] for s in shots]
        w("## Pacing")
        w(f"- {len(shots)} shots · average {statistics.mean(lengths):.2f}s · median {statistics.median(lengths):.2f}s "
          f"· shortest {min(lengths):.2f}s · longest {max(lengths):.2f}s")
        w(f"- Cuts per minute: {r['cutsPerMinute']}")
        longest = sorted(shots, key=lambda s: -s["length"])[:6]
        w("- Longest shots: " + ", ".join(f"#{s['index']} {mmss(s['start'])} ({s['length']:.1f}s)" for s in longest))
        flashes = [s for s in shots if s["length"] < 0.5]
        if flashes:
            w(f"- {len(flashes)} shots under 0.5s: " + ", ".join(mmss(s["start"]) for s in flashes[:20]))
        w("")
        w("## Colour and exposure")
        w(f"- Per-minute luma: {r['colourPerMinute']['luma']}")
        w(f"- Per-minute warmth (R−B): {r['colourPerMinute']['warmth']}")
        w(f"- Per-minute saturation: {r['colourPerMinute']['sat']}")
        w("- Biggest shot-to-shot colour jumps (score >3 is noticeable):")
        for j in r["colourJumps"]:
            w(f"  - {tc(j['at'], meta.get('fps', 25))} shot #{j['fromShot']}→#{j['toShot']} score {j['score']} "
              f"(Δluma {j['dLuma']:+}, Δwarmth {j['dWarmth']:+}, Δsat {j['dSat']:+})")
        crushed = [s for s in shots if s["crushed"] > 0.25]
        clipped = [s for s in shots if s["clipped"] > 0.08]
        if crushed:
            w("- Heavy crushed blacks: " + ", ".join(f"#{s['index']} {mmss(s['start'])}" for s in crushed[:15]))
        if clipped:
            w("- Clipped highlights: " + ", ".join(f"#{s['index']} {mmss(s['start'])}" for s in clipped[:15]))
        w("")

    lo = r.get("loudness")
    if lo:
        w("## Loudness")
        w(f"- Integrated {lo['integratedLUFS']} LUFS · range {lo['loudnessRangeLU']} LU · sample peak {lo['samplePeakDBFS']} dBFS "
          f"(online target −14 LUFS, peak ≤ −1 dBFS)")
        if r.get("speechVsMusic"):
            sv = r["speechVsMusic"]
            w(f"- Mean short-term loudness: during speech {sv['speechLUFS']} LUFS · without speech {sv['nonSpeechLUFS']} LUFS")
        if r.get("loudSpikes"):
            w("- Loud spikes (>6 LU over integrated): " + ", ".join(mmss(t) for t in r["loudSpikes"][:20]))
        w("")

    mu = r.get("music")
    if mu:
        w("## Music")
        gk = mu["globalKey"]
        w(f"- Tempo ≈ {mu['tempoBPM']} BPM · key ≈ {gk['key']} {gk['mode']} (confidence {gk['confidence']})")
        if r.get("beatSync"):
            b = r["beatSync"]
            w(f"- Cuts on the beat (±{int(b['windowSec'] * 1000)} ms): {b['onBeat']}/{b['cuts']} = {b['ratio']} (chance ≈ {b['chanceRatio']})")
        w("- Sections:")
        for s in mu["sections"]:
            w(f"  - {mmss(s['start'])}–{mmss(s['end'])} · energy {s['energy']} ({s['energyDB']} dB) · {s['bpm'] or '–'} BPM "
              f"· {s['key']} · brightness {s['brightnessHz']} Hz · {s['moodHint']}")
        w("")

    if edit and edit.get("titles"):
        w("## Titles in the timeline")
        for t in edit["titles"]:
            w(f"- {mmss(t['start'])} ({t['duration']:.1f}s, lane {t['lane']}) {t['name'].split(' - ')[-1]}: “{t['text'][:80]}”")
        w("")

    tr = r.get("transcript")
    if tr and tr["segments"]:
        w(f"## Transcript (Whisper, {tr['language']})")
        for s in tr["segments"]:
            w(f"- {mmss(s['start'])} {s['text']}")
        w("")
    elif edit and edit.get("captions"):
        w("## Transcript (from FCPXML captions)")
        for c in edit["captions"]:
            w(f"- {mmss(c['start'])} {c['text']}")
        w("")

    if r.get("sheets"):
        w("## Contact sheets")
        for p in r["sheets"]:
            w(f"- {p}")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", help="Rendered edit (MP4/MOV)")
    ap.add_argument("--fcpxml", help="FCPXML file or .fcpxmld bundle")
    ap.add_argument("--out", help="Output folder (default analysis/<name>)")
    ap.add_argument("--name", help="Name for this analysis")
    ap.add_argument("--end", type=float, help="Only analyse up to this many seconds")
    ap.add_argument("--whisper", default="small", help="Whisper model: tiny, base, small, medium (default small)")
    ap.add_argument("--language", help="Force transcript language, e.g. en")
    ap.add_argument("--no-transcribe", action="store_true", help="Skip Whisper, use FCPXML captions instead")
    a = ap.parse_args()
    if not a.video and not a.fcpxml:
        ap.error("give --video and/or --fcpxml")

    name = a.name or Path(a.video or a.fcpxml).stem
    base = Path(os.environ.get("DMV_OUTPUT_DIR") or ROOT / "analysis")
    out = Path(a.out) if a.out else base / name
    (out / "frames").mkdir(parents=True, exist_ok=True)
    task = Task(f"Analysing “{name}”")
    weights = {"timeline": 2, "probe": 1, "audio": 4, "transcript": 30 if a.video and not a.no_transcribe else 0,
               "music": 8, "loudness": 4, "cuts": 25, "colour": 10, "frames": 12, "digest": 1}
    st = Stages(task, {k: v for k, v in weights.items() if v} if a.video else {"timeline": 1, "digest": 1})
    r = {"name": name}

    try:
        if a.fcpxml:
            st.start("timeline", "Reading FCPXML timeline")
            tl = fcpxml.parse(a.fcpxml)
            s = fcpxml.summarize(tl)
            long_tail = [i for i in tl["items"] if i["enabled"] and i["duration"] > 0.4 * s["duration"]
                         and i["kind"] == "asset-clip" and i["start"] > 0.3 * s["duration"]]
            if long_tail:
                t0 = long_tail[0]
                s["tailNote"] = (f"A single {t0['duration']:.0f}s clip “{t0.get('asset') or t0['name']}” starts at "
                                 f"{mmss(t0['start'])} on lane {t0['lane']}: likely a leftover render; the edit proper may end there.")
            # Keep the absolute path: the app needs it to write markers and edited versions later.
            s["file"] = str(Path(a.fcpxml).expanduser().resolve())
            r["timeline"] = s
            (out / "timeline_items.json").write_text(json.dumps(tl["items"], indent=1))

        if a.video:
            src = Path(a.video)
            st.start("probe", "Reading video details")
            meta = video.probe(src)
            r["video"] = meta
            dur = meta["duration"]
            tmp = Path(tempfile.mkdtemp(prefix="dmv_"))
            if a.end and a.end < dur:
                st.report(0.5, f"Trimming to {mmss(a.end)}")
                trimmed = tmp / f"trim{src.suffix}"
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-t", str(a.end), "-c", "copy", str(trimmed)], check=True)
                src, dur = trimmed, a.end
                r["analysedUntil"] = a.end
            fps = meta["fps"] or 25

            st.start("audio", "Extracting audio")
            wav48 = audio.extract_wav(src, tmp / "a48.wav", 48000, 2)
            wav22 = audio.extract_wav(src, tmp / "a22.wav", 22050, 1)
            wav16 = audio.extract_wav(src, tmp / "a16.wav", 16000, 1)

            if weights["transcript"]:
                st.start("transcript", "Transcribing")
                r["transcript"] = audio.transcribe(wav16, dur, a.whisper, a.language, report=st.report)
                (out / "transcript.srt").write_text(srt(r["transcript"]["segments"]))

            st.start("music", "Analysing music")
            r["music"] = audio.music(wav22, report=st.report)

            st.start("loudness", "Measuring loudness")
            lo = audio.loudness(wav48, report=st.report)
            r["loudness"] = lo
            segs = (r.get("transcript") or {}).get("segments") or [
                {"start": c["start"], "end": c["start"] + c["duration"]} for c in (r.get("timeline") or {}).get("captions", [])]
            if segs:
                mask = audio.speech_mask(segs, len(lo["shortTerm"]) + 3)
                sp = [x["lufs"] for x in lo["shortTerm"] if x["lufs"] is not None and mask[int(x["t"]) + 1]]
                ns = [x["lufs"] for x in lo["shortTerm"] if x["lufs"] is not None and not mask[int(x["t"]) + 1]]
                r["speechVsMusic"] = {"speechLUFS": round(float(np.mean(sp)), 1) if sp else None,
                                      "nonSpeechLUFS": round(float(np.mean(ns)), 1) if ns else None}
            if lo["integratedLUFS"] is not None:
                r["loudSpikes"] = [x["t"] for x in lo["shortTerm"] if x["lufs"] and x["lufs"] > lo["integratedLUFS"] + 6]

            st.start("cuts", "Detecting cuts")
            shots_t = video.detect_cuts(src, dur, report=st.report)

            st.start("colour", "Reading colour")
            samples = video.colour_samples(src, dur, report=st.report)
            shots = video.shot_stats(shots_t, samples)
            r["shots"] = shots
            r["colourSamples"] = samples
            r["colourPerMinute"] = {k: per_minute(samples, k, dur) for k in ("luma", "warmth", "sat", "contrast", "motion")}
            r["colourJumps"] = colour_jumps(shots)
            cut_times = [s["start"] for s in shots[1:]]
            r["cutsPerMinute"] = [sum(1 for c in cut_times if m * 60 <= c < (m + 1) * 60) for m in range(int(np.ceil(dur / 60)))]
            r["beatSync"] = beat_sync(cut_times, r["music"]["beats"])

            st.start("frames", "Saving key frames")
            frames = []
            for i, s in enumerate(shots):
                mid = s["start"] + min(s["length"] / 2, 1.5) if s["length"] > 3 else s["start"] + s["length"] / 2
                p = out / "frames" / f"shot_{s['index']:03d}.jpg"
                video.extract_frame(src, mid, p)
                s["frame"] = str(p.relative_to(out))
                frames.append((p, f"#{s['index']} {tc(s['start'], fps)} ({s['length']:.1f}s)"))
                st.report(i / len(shots), f"Saving key frames · {i + 1}/{len(shots)}")
            r["sheets"] = [str(p.relative_to(out)) for p in video.contact_sheets(frames, out / "sheets")]

        st.start("digest", "Writing digest")
        (out / "analysis.json").write_text(json.dumps(r, indent=1))
        (out / "digest.md").write_text(build_digest(r))
        task.done(f"Done · {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
        print(f"Analysis written to {out}", file=sys.stderr)
        return out
    except Exception as e:
        task.fail(f"Failed: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
