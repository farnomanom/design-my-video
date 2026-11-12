"""Media log: turns a folder of original footage into a searchable database.

For every clip: technical metadata, shots (with a key frame and picture-quality stats), a word-level
transcript and a per-second sound level. Everything lands in <log>/medialog.db (SQLite + full-text
search) plus one JSON file per clip. Logging is resumable: clips already logged with the same
size and date are skipped, so a stopped run picks up where it left off.
"""
import hashlib
import json
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

from .timecode import mmss
from .video import probe

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mxf", ".avi", ".mkv", ".mts", ".m2ts", ".3gp"}
AUDIO_EXT = {".wav", ".aif", ".aiff", ".mp3", ".m4a", ".aac", ".flac", ".caf"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic", ".psd", ".gif", ".bmp"}

SAMPLE_FPS = 3          # picture samples per second for shot detection and stats
SAMPLE_W, SAMPLE_H = 320, 180
MIN_SHOT = 0.6          # seconds

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips(
  id TEXT PRIMARY KEY, path TEXT, name TEXT, kind TEXT, duration REAL, width INT, height INT, fps REAL,
  codec TEXT, transfer TEXT, bitdepth INT, has_audio INT, speech_seconds REAL, language TEXT,
  loudness_db REAL, fingerprint TEXT, logged_at TEXT, description TEXT);
CREATE TABLE IF NOT EXISTS shots(
  clip_id TEXT, idx INT, start REAL, end REAL, thumb TEXT, luma REAL, contrast REAL, sat REAL,
  sharp REAL, motion REAL, description TEXT, framing TEXT, quality TEXT, tags TEXT,
  PRIMARY KEY(clip_id, idx));
CREATE TABLE IF NOT EXISTS segments(clip_id TEXT, idx INT, start REAL, end REAL, text TEXT, confidence REAL,
  PRIMARY KEY(clip_id, idx));
CREATE TABLE IF NOT EXISTS words(clip_id TEXT, start REAL, end REAL, word TEXT, prob REAL);
CREATE INDEX IF NOT EXISTS words_clip ON words(clip_id, start);
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(clip_id UNINDEXED, kind UNINDEXED, start UNINDEXED,
  end UNINDEXED, text);
"""


def discover(sources):
    """All media files under the given folders (or files), skipping Final Cut render/proxy caches."""
    out = []
    for src in sources:
        src = Path(src).expanduser()
        files = [src] if src.is_file() else sorted(p for p in src.rglob("*") if p.is_file())
        for p in files:
            parts = set(p.parts)
            if parts & {"Render Files", "Transcoded Media", "Analysis Files", "Peaks Data", "Thumbnail Media"}:
                continue
            if p.name.startswith("."):
                continue
            ext = p.suffix.lower()
            kind = "video" if ext in VIDEO_EXT else "audio" if ext in AUDIO_EXT else "image" if ext in IMAGE_EXT else None
            if kind:
                out.append((p, kind))
    return out


def fingerprint(p):
    st = p.stat()
    return f"{st.st_size}-{int(st.st_mtime)}"


def clip_id(p):
    return hashlib.sha1(str(p.resolve()).encode()).hexdigest()[:12]


def _stream_info(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)],
                         capture_output=True, text=True).stdout
    try:
        streams = json.loads(out).get("streams", [])
    except json.JSONDecodeError:
        streams = []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    pix = v.get("pix_fmt") or ""
    depth = 10 if "10" in pix else 12 if "12" in pix else 8 if v else None
    return {"transfer": v.get("color_transfer"), "bitdepth": depth,
            "hasAudio": any(s.get("codec_type") == "audio" for s in streams)}


# ---------- picture ----------

def _samples(path, duration, report=None):
    """One ffmpeg pass at SAMPLE_FPS, small frames. Yields (t, rgb uint8 HxWx3)."""
    base = ["ffmpeg", "-v", "error"]
    tail = ["-i", str(path), "-vf", f"fps={SAMPLE_FPS},scale={SAMPLE_W}:{SAMPLE_H}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    size = SAMPLE_W * SAMPLE_H * 3
    # CPU decoding: VideoToolbox fails on these 4K H.264 files and its fallback is ~4x slower.
    for attempt in ([],):
        proc = subprocess.Popen(base + attempt + tail, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        n = 0
        try:
            while True:
                buf = proc.stdout.read(size)
                if len(buf) < size:
                    break
                t = n / SAMPLE_FPS
                n += 1
                if report and n % 30 == 0:
                    report(min(1.0, t / max(duration, 1)), f"Shots · {mmss(t)} / {mmss(duration)}")
                yield t, np.frombuffer(buf, np.uint8).reshape(SAMPLE_H, SAMPLE_W, 3)
        finally:
            proc.stdout.close()
            proc.wait()
        if n:
            return


def _frame_stats(rgb, prev_gray):
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [16, 4, 4], [0, 180, 0, 256, 0, 256])
    cv2.normalize(hist, hist)
    g = gray.astype(np.float32) / 255
    stats = {
        "luma": float(g.mean()),
        "contrast": float(g.std()),
        "sat": float(hsv[..., 1].mean() / 255),
        "sharp": float(cv2.Laplacian(gray, cv2.CV_32F).var()),
        "motion": float(np.abs(gray.astype(np.int16) - prev_gray).mean() / 255) if prev_gray is not None else 0.0,
    }
    return gray.astype(np.int16), hist.flatten(), stats


def picture(path, duration, report=None):
    """Shots from histogram changes between samples, with mean stats per shot."""
    ts, hists, stats = [], [], []
    prev = None
    for t, rgb in _samples(path, duration, report):
        prev, h, s = _frame_stats(rgb, prev)
        ts.append(t); hists.append(h); stats.append(s)
    if not ts:
        return []
    diffs = [0.0] + [float(cv2.compareHist(hists[i - 1], hists[i], cv2.HISTCMP_BHATTACHARYYA)) for i in range(1, len(hists))]
    cuts = [0]
    for i in range(1, len(diffs)):
        local = np.median(diffs[max(1, i - 6):i + 6]) if i > 1 else 0
        if diffs[i] > 0.35 and diffs[i] > 2.5 * local and ts[i] - ts[cuts[-1]] >= MIN_SHOT:
            cuts.append(i)
    shots = []
    for k, a in enumerate(cuts):
        b = cuts[k + 1] if k + 1 < len(cuts) else len(ts)
        seg = stats[a:b]
        start = ts[a]
        end = ts[b] if b < len(ts) else duration
        mean = {key: round(float(np.mean([s[key] for s in seg])), 4) for key in seg[0]}
        mean["motion"] = round(float(np.mean([s["motion"] for s in seg[1:]] or [0])), 4)
        shots.append({"start": round(start, 2), "end": round(max(end, start + 0.04), 2), **mean})
    return shots


def keyframe(path, t, out, width=480):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                    "-vf", f"scale={width}:-2", "-q:v", "4", str(out)], check=False)
    return out.exists()


# ---------- sound ----------

def sound(path, duration, whisper, report=None):
    """Per-second level (dBFS) and, when there is speech, a word-level transcript."""
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
        if r.returncode != 0 or not wav.exists():
            return None
        import soundfile as sf

        y, sr = sf.read(str(wav), dtype="float32")
        if y.size == 0:
            return None
        n = int(np.ceil(len(y) / sr))
        level = []
        for i in range(n):
            chunk = y[i * sr:(i + 1) * sr]
            rms = float(np.sqrt(np.mean(chunk ** 2))) if chunk.size else 0
            level.append(round(20 * np.log10(max(rms, 1e-6)), 1))
        out = {"levelDb": level, "loudnessDb": round(float(np.percentile(level, 90)), 1) if level else None,
               "segments": [], "words": [], "language": None, "speechSeconds": 0.0}
        if whisper is None or max(level or [-120]) < -45:
            return out
        if report:
            report(0.0, "Transcribing")
        segs, info = whisper.transcribe(str(wav), vad_filter=True, beam_size=5, word_timestamps=True,
                                        condition_on_previous_text=False)
        for s in segs:
            out["segments"].append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip(),
                                    "confidence": round(float(np.exp(s.avg_logprob)), 2)})
            for w in s.words or []:
                out["words"].append({"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word.strip(),
                                     "prob": round(float(w.probability), 2)})
            if report:
                report(min(1.0, s.end / max(duration, 1)), f"Transcribing · {mmss(s.end)} / {mmss(duration)}")
        out["language"] = info.language if out["segments"] else None
        out["speechSeconds"] = round(sum(s["end"] - s["start"] for s in out["segments"]), 1)
        return out


# ---------- database ----------

def open_db(log_dir):
    db = sqlite3.connect(str(Path(log_dir) / "medialog.db"))
    db.executescript(SCHEMA)
    return db


def _store(db, rec):
    cid = rec["id"]
    for t in ("clips", "shots", "segments", "words"):
        db.execute(f"DELETE FROM {t} WHERE {'id' if t == 'clips' else 'clip_id'}=?", (cid,))
    db.execute("DELETE FROM search WHERE clip_id=?", (cid,))
    s = rec.get("sound") or {}
    db.execute("INSERT INTO clips VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        cid, rec["path"], rec["name"], rec["kind"], rec.get("duration"), rec.get("width"), rec.get("height"),
        rec.get("fps"), rec.get("codec"), rec.get("transfer"), rec.get("bitdepth"), int(bool(rec.get("hasAudio"))),
        s.get("speechSeconds"), s.get("language"), s.get("loudnessDb"), rec["fingerprint"], rec["loggedAt"], None))
    for i, sh in enumerate(rec.get("shots", [])):
        db.execute("INSERT INTO shots(clip_id, idx, start, end, thumb, luma, contrast, sat, sharp, motion) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (cid, i, sh["start"], sh["end"], sh.get("thumb"), sh["luma"], sh["contrast"], sh["sat"], sh["sharp"], sh["motion"]))
    for i, sg in enumerate(s.get("segments", [])):
        db.execute("INSERT INTO segments VALUES(?,?,?,?,?,?)", (cid, i, sg["start"], sg["end"], sg["text"], sg["confidence"]))
        db.execute("INSERT INTO search VALUES(?,?,?,?,?)", (cid, "speech", sg["start"], sg["end"], sg["text"]))
    db.executemany("INSERT INTO words VALUES(?,?,?,?,?)",
                   [(cid, w["start"], w["end"], w["word"], w["prob"]) for w in s.get("words", [])])
    db.execute("INSERT INTO search VALUES(?,?,?,?,?)", (cid, "name", 0, rec.get("duration") or 0, rec["name"]))
    db.commit()


def log_folder(sources, log_dir, whisper_size="small", task=None, emit=None):
    log_dir = Path(log_dir)
    (log_dir / "clips").mkdir(parents=True, exist_ok=True)
    (log_dir / "thumbs").mkdir(exist_ok=True)
    db = open_db(log_dir)
    files = discover(sources)
    todo = []
    for p, kind in files:
        cid = clip_id(p)
        j = log_dir / "clips" / f"{cid}.json"
        if j.exists():
            try:
                if json.loads(j.read_text()).get("fingerprint") == fingerprint(p):
                    continue
            except Exception:
                pass
        todo.append((p, kind, cid))
    durations = {}
    for p, kind, _ in todo:
        try:
            durations[p] = probe(p)["duration"] if kind != "image" else 0.0
        except Exception:
            durations[p] = 0.0
    total = sum(max(d, 1.0) for d in durations.values()) or 1.0
    done_w = 0.0
    whisper = None
    if any(k != "image" for _, k, _ in todo):
        from faster_whisper import WhisperModel

        if task:
            task.update(percent=0, note=f"Loading Whisper ({whisper_size})", force=True)
        from . import runtime
        whisper = WhisperModel(runtime.whisper_model(whisper_size), device="cpu", compute_type="int8")
    if emit:
        emit("log_start", files=len(files), todo=len(todo), skipped=len(files) - len(todo))
    for n, (p, kind, cid) in enumerate(todo, 1):
        dur = durations[p]
        weight = max(dur, 1.0)
        label = f"{n}/{len(todo)} {p.name}"

        def report(frac, note, _base=done_w, _w=weight):
            if task:
                # picture is ~40% of a clip's time, sound ~60%
                task.update(percent=100 * (_base + _w * frac) / total, note=f"{label} · {note}")

        rec = {"id": cid, "path": str(p.resolve()), "name": p.name, "kind": kind, "fingerprint": fingerprint(p)}
        t0 = time.time()
        try:
            if kind == "image":
                im = cv2.imread(str(p))
                if im is not None:
                    rec.update(width=im.shape[1], height=im.shape[0])
                th = log_dir / "thumbs" / f"{cid}_000.jpg"
                if keyframe(p, 0, th):
                    rec["shots"] = [{"start": 0, "end": 0, "thumb": str(th), "luma": 0, "contrast": 0, "sat": 0, "sharp": 0, "motion": 0}]
            else:
                info = probe(p)
                rec.update(duration=info["duration"], width=info["width"], height=info["height"], fps=info["fps"],
                           codec=info["videoCodec"] or info["audioCodec"], **_stream_info(p))
                if kind == "video" and info["width"]:
                    shots = picture(p, dur, lambda f, note: report(0.4 * f, note))
                    for i, sh in enumerate(shots):
                        th = log_dir / "thumbs" / f"{cid}_{i:03d}.jpg"
                        if keyframe(p, (sh["start"] + sh["end"]) / 2, th):
                            sh["thumb"] = str(th)
                    rec["shots"] = shots
                if rec.get("hasAudio", True):
                    rec["sound"] = sound(p, dur, whisper, lambda f, note: report(0.4 + 0.6 * f, note))
        except Exception as e:  # one bad file must not stop the log
            rec["error"] = f"{e.__class__.__name__}: {e}"
        rec["loggedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        rec["seconds"] = round(time.time() - t0, 1)
        (log_dir / "clips" / f"{cid}.json").write_text(json.dumps(rec))
        _store(db, rec)
        done_w += weight
        if emit:
            emit("clip", name=p.name, shots=len(rec.get("shots", [])),
                 speech=(rec.get("sound") or {}).get("speechSeconds", 0), error=rec.get("error"), seconds=rec["seconds"])
    db.close()
    return {"files": len(files), "logged": len(todo)}


def search(log_dir, query, limit=20):
    """Full-text search over speech and names, e.g. 'mother OR church'."""
    db = open_db(log_dir)
    rows = db.execute(
        "SELECT s.clip_id, c.name, s.kind, s.start, s.end, s.text FROM search s JOIN clips c ON c.id=s.clip_id "
        "WHERE search MATCH ? ORDER BY rank LIMIT ?", (query, limit)).fetchall()
    db.close()
    return [{"clipId": r[0], "clip": r[1], "kind": r[2], "start": r[3], "end": r[4], "text": r[5]} for r in rows]


def summary(log_dir):
    db = open_db(log_dir)
    q = lambda sql: db.execute(sql).fetchone()
    out = {
        "clips": q("SELECT COUNT(*) FROM clips")[0],
        "byKind": dict(db.execute("SELECT kind, COUNT(*) FROM clips GROUP BY kind").fetchall()),
        "footageMinutes": round((q("SELECT SUM(duration) FROM clips WHERE kind='video'")[0] or 0) / 60, 1),
        "shots": q("SELECT COUNT(*) FROM shots")[0],
        "speechMinutes": round((q("SELECT SUM(speech_seconds) FROM clips")[0] or 0) / 60, 1),
        "words": q("SELECT COUNT(*) FROM words")[0],
    }
    db.close()
    return out
