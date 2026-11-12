"""Verbatim interview transcripts for filler-level cutting.

Standard Whisper leaves out "um", "uh", repeats and false starts, which are exactly what an editor
cuts. This pass uses large-v3 with a prompt written in verbatim style (Whisper copies the style of
its prompt), word timestamps, and no carry-over of previous text. It then finds every stretch of
voiced sound that no transcribed word covers: those are likely fillers, breaths or false starts the
model still skipped, flagged so they are never treated as silence.
"""
import json
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

from .timecode import mmss

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "models" / "faster-whisper-large-v3"
PROMPT = ("Umm, so, uh, I— I was, like, you know, mm, thinking about it. Hmm. "
          "And, er, yeah, I mean, it's— it's, uh, hard to say.")
FILLERS = {"um", "umm", "uh", "uhm", "er", "erm", "ah", "eh", "mm", "mmm", "hmm", "hm"}
FRAME = 0.01  # seconds per level frame


def _wav(src, out):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(out)], check=True)


def _levels(y, sr):
    n = int(FRAME * sr)
    k = len(y) // n
    rms = np.sqrt(np.mean(y[: k * n].reshape(k, n) ** 2, axis=1))
    return 20 * np.log10(np.maximum(rms, 1e-7))


def gap_sounds(db, words, min_len=0.12, pad=0.04):
    """Voiced runs that no word covers. Threshold = noise floor + 12 dB, floor = 10th percentile."""
    floor = float(np.percentile(db, 10))
    voiced = db > floor + 12
    covered = np.zeros_like(voiced)
    for w in words:
        a = max(0, int((w["start"] - pad) / FRAME))
        b = min(len(covered), int((w["end"] + pad) / FRAME) + 1)
        covered[a:b] = True
    free = voiced & ~covered
    out, i = [], 0
    while i < len(free):
        if free[i]:
            j = i
            while j < len(free) and free[j]:
                j += 1
            if (j - i) * FRAME >= min_len:
                seg = db[i:j]
                out.append({"start": round(i * FRAME, 2), "end": round(j * FRAME, 2),
                            "peakDb": round(float(seg.max()), 1), "meanDb": round(float(seg.mean()), 1)})
            i = j
        else:
            i += 1
    return out, round(floor, 1)


def transcribe(src, model=None, report=None, language="en"):
    from . import runtime
    if model is None and runtime.has_mlx():
        return _transcribe_apple(src, report, language)
    from faster_whisper import WhisperModel

    model = model or WhisperModel(runtime.whisper_model("large-v3") if not MODEL.exists() else str(MODEL),
                                  device="cpu", compute_type="int8", cpu_threads=6)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        _wav(src, wav)
        import soundfile as sf

        y, sr = sf.read(str(wav), dtype="float32")
    dur = len(y) / sr
    segs, info = model.transcribe(
        y, language=language, initial_prompt=PROMPT, word_timestamps=True, condition_on_previous_text=False,
        beam_size=5, vad_filter=True, vad_parameters={"min_silence_duration_ms": 300, "speech_pad_ms": 200})
    words, segments = [], []
    for s in segs:
        segments.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip(),
                         "confidence": round(float(np.exp(s.avg_logprob)), 2)})
        for w in s.words or []:
            t = w.word.strip()
            bare = t.lower().strip(".,!?;:—-…\"'")
            words.append({"start": round(w.start, 2), "end": round(w.end, 2), "word": t,
                          "prob": round(float(w.probability), 2), "filler": bare in FILLERS})
        if report:
            report(min(1.0, s.end / max(dur, 1)), f"{mmss(s.end)} / {mmss(dur)}")
    db = _levels(y, sr)
    sounds, floor = gap_sounds(db, words)
    return {"source": str(src), "duration": round(dur, 2), "language": info.language, "model": "large-v3",
            "prompt": PROMPT, "noiseFloorDb": floor, "segments": segments, "words": words,
            "gapSounds": sounds, "levelDb10ms": [round(float(v), 1) for v in db]}


def _transcribe_apple(src, report=None, language="en"):
    """Apple Silicon: Demucs isolates the voice (music and room noise out), then MLX Whisper transcribes it with
    the verbatim prompt. Levels and gap sounds are still measured on the original audio."""
    import soundfile as sf
    from . import runtime
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        _wav(src, wav)
        y, sr = sf.read(str(wav), dtype="float32")
        voice = wav
        if runtime.has_demucs():
            if report:
                report(0.02, "separating the voice")
            try:
                voice = Path(tmp) / "voice.wav"
                runtime.isolate_voice(wav, voice)
            except Exception:
                voice = wav
        lang, segments, words = runtime.transcribe_words(voice, "large-v3", language, prompt=PROMPT, report=report)
    for w in words:
        w["filler"] = w["word"].lower().strip(".,!?;:—-…\"'") in FILLERS
    db = _levels(y, sr)
    sounds, floor = gap_sounds(db, words)
    return {"source": str(src), "duration": round(len(y) / sr, 2), "language": lang, "model": "mlx-whisper-large-v3-turbo",
            "prompt": PROMPT, "noiseFloorDb": floor, "segments": segments, "words": words,
            "gapSounds": sounds, "levelDb10ms": [round(float(v), 1) for v in db]}


def run(sources, out_dir, task=None):
    from faster_whisper import WhisperModel

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(str(MODEL), device="cpu", compute_type="int8", cpu_threads=6)
    durs = {}
    for s in sources:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(s)],
                           capture_output=True, text=True)
        durs[s] = float(r.stdout.strip() or 0)
    total = sum(durs.values()) or 1
    done = 0.0
    results = []
    for n, s in enumerate(sources, 1):
        out = out_dir / (Path(s).stem + ".json")
        if out.exists():
            done += durs[s]
            continue
        t0 = time.time()

        def report(f, note, _b=done, _w=durs[s]):
            if task:
                task.update(percent=100 * (_b + _w * f) / total, note=f"{n}/{len(sources)} {Path(s).name} · {note}")

        rec = transcribe(s, model, report)
        rec["seconds"] = round(time.time() - t0, 1)
        out.write_text(json.dumps(rec))
        done += durs[s]
        fill = sum(w["filler"] for w in rec["words"])
        results.append({"clip": Path(s).name, "words": len(rec["words"]), "fillers": fill,
                        "gapSounds": len(rec["gapSounds"]), "seconds": rec["seconds"]})
        print(json.dumps({"event": "verbatim", **results[-1]}), flush=True)
    return results


if __name__ == "__main__":
    import sys

    from .progress import Task

    out = sys.argv[1]
    srcs = sys.argv[2:]
    task = Task("Verbatim interview transcripts (large-v3)")
    try:
        r = run(srcs, out, task)
        task.done(f"{len(srcs)} clips · {sum(x['fillers'] for x in r)} fillers found")
    except Exception as e:
        task.fail(str(e)[:200])
        raise
