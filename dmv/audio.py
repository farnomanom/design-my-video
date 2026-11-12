"""Sound analysis: extraction, loudness, music features and sections, transcription."""
import subprocess

import numpy as np

from .timecode import mmss

KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def extract_wav(video, out, rate, channels):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vn", "-ac", str(channels), "-ar", str(rate), str(out)],
        check=True,
    )
    return out


def loudness(wav, report=None):
    import pyloudnorm as pyln
    import soundfile as sf

    data, rate = sf.read(str(wav), always_2d=True)
    meter = pyln.Meter(rate)
    integrated = meter.integrated_loudness(data)
    win, hop = 3 * rate, rate
    short = []
    n = max(1, (len(data) - win) // hop + 1)
    for k in range(n):
        seg = data[k * hop:k * hop + win]
        if len(seg) < rate * 0.5:
            break
        try:
            v = meter.integrated_loudness(seg)
        except Exception:
            v = float("-inf")
        short.append({"t": float(k), "lufs": round(float(v), 1) if np.isfinite(v) else None})
        if report and k % 30 == 0:
            report(k / n, f"Measuring loudness · {mmss(k)}")
    vals = np.array([s["lufs"] for s in short if s["lufs"] is not None and s["lufs"] > -70])
    peak = float(np.max(np.abs(data))) if len(data) else 0.0
    return {
        "integratedLUFS": round(float(integrated), 1) if np.isfinite(integrated) else None,
        "samplePeakDBFS": round(20 * np.log10(peak), 1) if peak > 0 else None,
        "loudnessRangeLU": round(float(np.percentile(vals, 95) - np.percentile(vals, 10)), 1) if len(vals) else None,
        "shortTerm": short,
    }


def _key(chroma_mean):
    best = (-2, "C", "major")
    for i in range(12):
        for prof, mode in ((MAJOR, "major"), (MINOR, "minor")):
            r = np.corrcoef(np.roll(prof, i), chroma_mean)[0, 1]
            if r > best[0]:
                best = (r, KEYS[i], mode)
    return {"key": best[1], "mode": best[2], "confidence": round(float(best[0]), 2)}


def music(wav, report=None):
    import librosa

    sr = 22050
    y, sr = librosa.load(str(wav), sr=sr, mono=True)
    dur = len(y) / sr
    hop = 512
    if report:
        report(0.05, "Analysing music · beats and tempo")
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=hop)
    beat_times = librosa.frames_to_time(beats, sr=sr, hop_length=hop)
    if report:
        report(0.4, "Analysing music · energy and brightness")
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    cent = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=hop)[0]
    mfcc = librosa.feature.mfcc(y=y, sr=sr, hop_length=hop, n_mfcc=13)
    if report:
        report(0.6, "Analysing music · harmony and key")
    chroma = librosa.feature.chroma_stft(y=y, sr=sr, hop_length=hop)
    fps = sr / hop
    n_sec = int(np.ceil(dur))

    def per_sec(a):
        a = np.atleast_2d(a)
        return np.stack([a[:, int(s * fps):max(int(s * fps) + 1, int((s + 1) * fps))].mean(axis=1) for s in range(n_sec)], axis=1)

    rms_s, cent_s, on_s = per_sec(rms)[0], per_sec(cent)[0], per_sec(onset)[0]
    mfcc_s, chroma_s = per_sec(mfcc), per_sec(chroma)
    rms_db = 20 * np.log10(np.maximum(rms_s, 1e-5))

    if report:
        report(0.8, "Analysing music · sections and mood")
    k = int(max(3, min(24, dur // 45)))
    feats = np.vstack([librosa.util.normalize(mfcc_s, axis=1), librosa.util.normalize(chroma_s, axis=1),
                       (rms_db - rms_db.mean()) / (rms_db.std() + 1e-6)])
    bounds = librosa.segment.agglomerative(feats, k) if n_sec > k else np.arange(n_sec)
    bounds = sorted(set(int(b) for b in bounds) | {0}) + [n_sec]
    lo, hi = np.percentile(rms_db, 33), np.percentile(rms_db, 66)
    sections = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        if b - a < 1:
            continue
        e = float(rms_db[a:b].mean())
        bt = beat_times[(beat_times >= a) & (beat_times < b)]
        local_bpm = float(60 / np.median(np.diff(bt))) if len(bt) > 3 else None
        key = _key(chroma_s[:, a:b].mean(axis=1))
        level = "low" if e < lo else "high" if e > hi else "mid"
        mood = {
            ("low", "minor"): "reflective / melancholic", ("low", "major"): "calm / warm",
            ("mid", "minor"): "pensive / building", ("mid", "major"): "steady / hopeful",
            ("high", "minor"): "intense / dramatic", ("high", "major"): "uplifting / energetic",
        }[(level, key["mode"])]
        sections.append({
            "start": a, "end": b, "energyDB": round(e, 1), "energy": level,
            "brightnessHz": int(cent_s[a:b].mean()), "bpm": round(local_bpm, 1) if local_bpm else None,
            "key": f'{key["key"]} {key["mode"]}', "moodHint": mood,
        })
    return {
        "duration": round(dur, 2),
        "tempoBPM": round(float(np.atleast_1d(tempo)[0]), 1),
        "beats": [round(float(b), 3) for b in beat_times],
        "globalKey": _key(chroma_s.mean(axis=1)),
        "perSecond": [
            {"t": s, "energyDB": round(float(rms_db[s]), 1), "brightnessHz": int(cent_s[s]), "onset": round(float(on_s[s]), 3)}
            for s in range(n_sec)
        ],
        "sections": sections,
    }


def transcribe(wav, duration, model_size="small", language=None, report=None):
    from faster_whisper import WhisperModel

    if report:
        report(0.0, f"Loading Whisper ({model_size})")
    from . import runtime
    if runtime.has_mlx():   # Apple Silicon: MLX Whisper, same output shape
        lang, segs, _ = runtime.transcribe_words(wav, model_size, language)
        if report:
            report(1.0, "Transcribed with Apple MLX Whisper")
        return {"language": lang, "languageProbability": 1.0, "segments": segs}
    model = WhisperModel(runtime.whisper_model(model_size), device="cpu", compute_type="int8")
    # condition_on_previous_text=False: with it on, Whisper can lose its place after a long music
    # section and silently drop all the speech that follows (seen on a music-documentary mix).
    segs, info = model.transcribe(str(wav), language=language, vad_filter=True, beam_size=5,
                                  condition_on_previous_text=False)
    out = []
    for s in segs:
        out.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip(),
                    "confidence": round(float(np.exp(s.avg_logprob)), 2)})
        if report:
            report(s.end / max(duration, 1), f"Transcribing · {mmss(s.end)} / {mmss(duration)}")
    return {"language": info.language, "languageProbability": round(info.language_probability, 2), "segments": out}


def speech_mask(segments, n_sec):
    mask = np.zeros(n_sec, bool)
    for s in segments:
        mask[int(s["start"]):min(n_sec, int(np.ceil(s["end"])))] = True
    return mask
