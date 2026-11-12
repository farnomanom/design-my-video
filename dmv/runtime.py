"""What this machine can do, and where things are — so the same engine runs from the project folder on the
development Mac and from inside an installed app on any Intel or Apple Silicon Mac.

  python()          interpreter for a helper step (one bundled Python, or the project's two dev environments)
  whisper_model()   a local speech model (bundled, never downloaded at run time)
  hwaccel()         ffmpeg decode flags (VideoToolbox on Apple Silicon; software on Intel, where it corrupted frames)
  has_mlx()         Apple MLX Whisper available (Apple Silicon): much faster transcription with word timings
  has_demucs()      Demucs voice isolation available (Apple Silicon): voice separated from music before transcribing
  features()        everything above, for the self-test and the manual
"""
import functools
import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLED = os.environ.get("DMV_BUNDLED") == "1"
ARM = platform.machine() == "arm64"
MODELS = ROOT / "models"


def python(kind="main"):
    """kind 'main' (analysis, tracking) or 'graphics' (skia drawing). One interpreter when bundled."""
    if BUNDLED:
        return sys.executable
    dev = {"main": ROOT / ".venv" / "bin" / "python", "graphics": ROOT / "tools" / "envs" / "graphics" / "bin" / "python"}[kind]
    return str(dev) if dev.exists() else sys.executable


def whisper_model(size):
    """Local faster-whisper model for a size name. 'medium' maps to large-v3 (the bundles carry base, small, large-v3)."""
    size = {"medium": "large-v3", "best": "large-v3"}.get(size, size)
    local = MODELS / f"faster-whisper-{size}"
    if local.exists():
        return str(local)
    if BUNDLED:   # should not happen: fall back to the best model that is there
        for s in ("small", "base", "large-v3"):
            if (MODELS / f"faster-whisper-{s}").exists():
                return str(MODELS / f"faster-whisper-{s}")
    return size   # development Mac: faster-whisper fetches it by name


def hwaccel():
    return ["-hwaccel", "videotoolbox"] if ARM else ["-hwaccel", "none"]


@functools.lru_cache(None)
def has_mlx():
    if not ARM or not (MODELS / "mlx-whisper-large-v3-turbo").exists():
        return False
    try:
        import mlx_whisper  # noqa: F401
        return True
    except Exception:
        return False


@functools.lru_cache(None)
def has_demucs():
    if not ARM or not (MODELS / "demucs").exists():
        return False
    try:
        import demucs  # noqa: F401
        import torch  # noqa: F401
        return True
    except Exception:
        return False


def yunet_model():
    p = MODELS / "yunet" / "face_detection_yunet_2023mar.onnx"
    return str(p) if p.exists() else None


def transcribe_words(wav, size="small", language=None, prompt=None, report=None, duration=None):
    """Words with timings from a 16 kHz mono wav. MLX Whisper on Apple Silicon, faster-whisper elsewhere.
    Returns (language, segments, words) with words = [{start, end, word, prob}]."""
    if has_mlx():
        import mlx_whisper
        r = mlx_whisper.transcribe(str(wav), path_or_hf_repo=str(MODELS / "mlx-whisper-large-v3-turbo"), word_timestamps=True,
                                   language=language, initial_prompt=prompt, condition_on_previous_text=False, verbose=None)
        segs, words = [], []
        for s in r.get("segments", []):
            segs.append({"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"].strip()})
            for w in s.get("words", []):
                words.append({"start": round(w["start"], 2), "end": round(w["end"], 2), "word": w["word"].strip(),
                              "prob": round(float(w.get("probability", 1)), 2)})
        if report:
            report(1.0, "Transcribed with Apple MLX Whisper")
        return r.get("language", language), segs, words
    from faster_whisper import WhisperModel
    import numpy as np
    model = WhisperModel(whisper_model(size), device="cpu", compute_type="int8")
    kw = {"initial_prompt": prompt} if prompt else {}
    gen, info = model.transcribe(str(wav), language=language, vad_filter=True, beam_size=5, word_timestamps=True,
                                 condition_on_previous_text=False, **kw)
    segs, words = [], []
    for s in gen:
        segs.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip(),
                     "confidence": round(float(np.exp(s.avg_logprob)), 2)})
        for w in s.words or []:
            words.append({"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word.strip(),
                          "prob": round(float(w.probability), 2)})
        if report and duration:
            report(min(1.0, s.end / max(duration, 1)), f"{int(s.end // 60)}:{int(s.end % 60):02d}")
    return info.language, segs, words


def isolate_voice(wav_in, wav_out):
    """Demucs (htdemucs) on the Apple GPU: keep only the voice. Input/output: 16 kHz mono wav."""
    import numpy as np
    import soundfile as sf
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model
    model = get_model("htdemucs", repo=MODELS / "demucs")
    model.eval()
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:   # Demucs works at 44.1 kHz stereo
        up = Path(tmp) / "up.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav_in), "-ac", "2", "-ar", str(model.samplerate), str(up)], check=True)
        x, _ = sf.read(str(up), dtype="float32", always_2d=True)
        dev = "mps" if torch.backends.mps.is_available() else "cpu"
        wav = torch.from_numpy(x.T.copy())          # (channels, samples), as Demucs' own separate.py does
        ref = wav.mean(0)
        m, s = ref.mean(), ref.std() + 1e-8
        with torch.no_grad():
            out = apply_model(model, ((wav - m) / s)[None].to(dev), device=dev, split=True, overlap=0.1, progress=False)[0]
        vocals = (out[model.sources.index("vocals")].cpu() * s + m).numpy().T
        vw = Path(tmp) / "voc.wav"
        sf.write(str(vw), vocals, model.samplerate)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(vw), "-ac", "1", "-ar", "16000", str(wav_out)], check=True)


def features():
    import shutil
    f = {"chip": "Apple Silicon" if ARM else "Intel", "bundled": BUNDLED, "python": sys.version.split()[0],
         "ffmpeg": shutil.which("ffmpeg"), "hardwareDecode": ARM, "mlxWhisper": has_mlx(), "demucsVoiceIsolation": has_demucs(),
         "yunetFaces": yunet_model() is not None,
         "speechModels": sorted(p.name for p in MODELS.glob("faster-whisper-*")) if MODELS.exists() else []}
    try:
        import torch
        f["torch"] = torch.__version__
        f["appleGPU"] = bool(torch.backends.mps.is_available())
    except Exception:
        f["torch"] = None
    return f
