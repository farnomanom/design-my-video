"""Clips: turn a long video into short, captioned, reframed clips for social media.

Pipeline (each step is resumable; everything lives in one clip-project folder):
  1. transcribe   speech with word timings (faster-whisper, on the Mac)
  2. find         the AI reads the numbered sentences and picks self-contained moments that work on their
                  own, scoring each on Hook, Flow, Value and Shareability (0–25 each) with a title and
                  hashtags; without speech or without an AI, a local fallback ranks moments by speech
                  density and audio energy
  3. reframe      face tracking (OpenCV, .venv) gives a smooth, steady crop path that keeps the speaker
                  centred; "fit" layout keeps the whole frame over a blurred fill instead
  4. render       vertical/square/landscape MP4 with animated word captions, a hook title for the first
                  seconds, loudness set for social (−14 LUFS)
Clips are self-contained: no Final Cut involved; the app exports the finished MP4s.

project.json holds the settings and every clip (times, scores, title, status, files).
"""
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CLIPS_HOME = Path.home() / "Movies" / "Design My Video" / "Clips"
ASPECTS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350), "16:9": (1920, 1080)}
STYLES = ("bold", "karaoke", "typewriter", "minimal")
IVORY, INK, OCHRE = (242, 235, 221), (11, 11, 12), (217, 164, 65)


def emit(event, **kw):
    print(json.dumps({"event": event, **kw}), flush=True)


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,avg_frame_rate:format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True)
    j = json.loads(r.stdout or "{}")
    s = (j.get("streams") or [{}])[0]
    n, d = (s.get("avg_frame_rate") or "25/1").split("/")
    fps = float(n) / float(d) if float(d) else 25.0
    return {"width": int(s.get("width", 1920)), "height": int(s.get("height", 1080)), "fps": fps if fps > 1 else 25.0,
            "duration": float((j.get("format") or {}).get("duration", 0))}


# ------------------------------------------------------------------ project
class Project:
    def __init__(self, folder):
        self.dir = Path(folder)
        self.file = self.dir / "project.json"
        self.data = json.loads(self.file.read_text()) if self.file.exists() else {}

    def save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False))
        tmp.replace(self.file)

    @staticmethod
    def create(video, name=None, **settings):
        stem = name or Path(video).stem
        folder = CLIPS_HOME / f"{stem} ({time.strftime('%Y-%m-%d, %H.%M')})"
        p = Project(folder)
        p.data = {"kind": "clips", "name": stem, "video": str(Path(video).resolve()), "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                  "settings": settings, "source": probe(video), "clips": [], "status": "new"}
        p.save()
        return p


# ------------------------------------------------------------------ 1 transcribe
def transcribe(p, task=None):
    out = p.dir / "transcript.json"
    if out.exists():
        return json.loads(out.read_text())
    import tempfile
    size = p.data["settings"].get("whisper", "small")
    lang = p.data["settings"].get("language")
    dur = p.data["source"]["duration"]
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", p.data["video"], "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)
        from . import runtime
        if runtime.has_demucs():
            if task:
                task.update(percent=4, note="Separating the voice from music and noise…", force=True)
            clean = Path(tmp) / "voice.wav"
            try:
                runtime.isolate_voice(wav, clean)
                wav = clean
            except Exception:
                pass
        if task:
            task.update(percent=8, note=f"Transcribing ({'Apple MLX Whisper' if runtime.has_mlx() else size})…", force=True)
        rep = (lambda f, note: task.update(percent=8 + 47 * f, note=f"Transcribing · {note}")) if task else None
        language, segments, words = runtime.transcribe_words(wav, size, lang, report=rep, duration=dur)
    t = {"language": language, "segments": segments, "words": words}
    out.write_text(json.dumps(t, ensure_ascii=False))
    return t


def sentences(words, gap=0.7, max_len=18.0):
    """Group words into sentences: end punctuation, a pause, or a length cap."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        end = re.search(r"[.?!…]['\"”’]?$", w["word"]) is not None
        pause = nxt is not None and nxt["start"] - w["end"] > gap
        if end or pause or nxt is None or cur[-1]["end"] - cur[0]["start"] > max_len:
            out.append({"start": cur[0]["start"], "end": cur[-1]["end"], "text": " ".join(x["word"] for x in cur),
                        "w0": i - len(cur) + 1, "w1": i})
            cur = []
    return out


# ------------------------------------------------------------------ 2 find
SYSTEM = ("You are a senior short-form video producer. From a long video's transcript you pick the moments that will "
          "work as stand-alone vertical clips on TikTok, Instagram Reels and YouTube Shorts. A good clip starts with a "
          "strong hook in its first seconds, makes sense without the rest of the video, has one clear idea or story, "
          "and ends on a satisfying beat. You never cut a sentence in half.")

PROMPT = """Video: “{name}”, {duration:.0f} seconds{genre}.
{focus}
Pick up to {count} clips, each {min_s:.0f}–{max_s:.0f} seconds long, that don't overlap. Use whole sentences: a clip is
sentences first..last (inclusive) from the numbered list. Score each clip from 0 to 25 on:
- hook: how strongly the first 3 seconds stop the scroll
- flow: makes sense on its own, clean start and end
- value: insight, emotion, humour or story payoff
- shareability: how likely people comment, save or share
Also give a short hook title (max 60 characters, no emojis, no quotes), a one-sentence reason, and 3–5 hashtags.

Reply with JSON only: {{"clips": [{{"first": 12, "last": 19, "hook": 21, "flow": 20, "value": 22, "shareability": 18,
"title": "…", "reason": "…", "hashtags": ["#…"]}}]}}

Numbered sentences (start–end seconds):
{sentences}
"""


CLIP_SCHEMA = {"type": "object", "required": ["clips"], "properties": {"clips": {"type": "array", "items": {
    "type": "object", "required": ["first", "last", "hook", "flow", "value", "shareability", "title", "reason"],
    "properties": {"first": {"type": "integer"}, "last": {"type": "integer"},
                   "hook": {"type": "integer", "minimum": 0, "maximum": 25}, "flow": {"type": "integer", "minimum": 0, "maximum": 25},
                   "value": {"type": "integer", "minimum": 0, "maximum": 25},
                   "shareability": {"type": "integer", "minimum": 0, "maximum": 25},
                   "title": {"type": "string"}, "reason": {"type": "string"},
                   "hashtags": {"type": "array", "items": {"type": "string"}}}}}}}


def _ai_pick(p, sents, task=None):
    from . import providers
    from .review import _extract_json
    s = p.data["settings"]
    prov = providers.pick(s.get("provider", "auto"))
    if task:
        task.update(percent=58, note=f"Finding the best moments with {providers.LABELS[prov.NAME]}…", force=True)
    text = "\n".join(f"[{i}] {x['start']:.1f}–{x['end']:.1f} {x['text']}" for i, x in enumerate(sents))
    focus = f"The editor wants: {s['prompt']}\n" if s.get("prompt") else ""
    req = {"system": SYSTEM, "prompt": PROMPT.format(name=p.data["name"], duration=p.data["source"]["duration"],
                                                    genre=f" ({s['genre']})" if s.get("genre") else "", focus=focus,
                                                    count=s.get("count", 10), min_s=s.get("min", 20), max_s=s.get("max", 60),
                                                    sentences=text),
           "images": [], "model": s.get("model"), "schema": CLIP_SCHEMA}
    raw = prov.review(req)
    j = json.loads(_extract_json(raw))
    return j.get("clips", []), providers.LABELS[prov.NAME]


def _local_pick(p, sents, words):
    """No AI available, or no speech: rank windows by speech density and loudness."""
    s = p.data["settings"]
    dur = p.data["source"]["duration"]
    lo, hi, count = s.get("min", 20), s.get("max", 60), s.get("count", 10)
    energy = _energy(p.data["video"], dur)
    picks = []
    if sents:
        for i in range(len(sents)):
            for j in range(i, len(sents)):
                d = sents[j]["end"] - sents[i]["start"]
                if d > hi:
                    break
                if d >= lo:
                    a, b = int(sents[i]["start"]), int(math.ceil(sents[j]["end"]))
                    e = float(np.mean(energy[a:b])) if b > a else 0
                    nw = sum(1 for w in words if sents[i]["start"] <= w["start"] < sents[j]["end"])
                    score = nw / d * 10 + e / 4
                    picks.append((score, i, j))
    else:
        step = max(1, int(lo // 2))
        for a in range(0, max(1, int(dur - lo)), step):
            b = min(int(dur), a + int((lo + hi) / 2))
            picks.append((float(np.mean(energy[a:b])), a, b))
    picks.sort(reverse=True)
    chosen, used = [], []
    for score, i, j in picks:
        if sents:
            a, b = sents[i]["start"], sents[j]["end"]
        else:
            a, b = float(i), float(j)
        if any(a < y and x < b for x, y in used):
            continue
        used.append((a, b))
        rank = 60 + 30 * (1 - len(chosen) / max(1, count))
        q = int(rank / 4)
        chosen.append({"first": i, "last": j, "hook": q, "flow": q, "value": q, "shareability": q,
                       "title": (sents[i]["text"][:57] + "…") if sents and len(sents[i]["text"]) > 60 else (sents[i]["text"] if sents else f"Moment at {int(a // 60)}:{int(a % 60):02d}"),
                       "reason": "Chosen on this Mac by speech density and energy (no AI reviewer available).",
                       "hashtags": [], "_a": a, "_b": b})
        if len(chosen) >= count:
            break
    return chosen


def _energy(video, dur):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
                       capture_output=True)
    y = np.frombuffer(r.stdout, np.float32)
    n = max(1, int(dur))
    sec = 8000
    k = min(n, len(y) // sec)
    if k == 0:
        return np.zeros(n)
    rms = np.sqrt(np.mean(y[: k * sec].reshape(k, sec) ** 2, axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-6))
    out = np.full(n, float(db.min()))
    out[:k] = db
    return out - out.min()


def find(p, task=None):
    t = transcribe(p, task)
    words = t["words"]
    sents = sentences(words)
    label = "On this Mac"
    picks = None
    if len(words) >= 30:
        try:
            picks, label = _ai_pick(p, sents, task)
        except Exception as e:
            p.data["aiError"] = str(e)[:300]
    if not picks:
        picks = _local_pick(p, sents, words)
        label = "On this Mac"
    s = p.data["settings"]
    lo, hi = s.get("min", 20), s.get("max", 60)
    clips, used = [], []
    for c in picks:
        if "_a" in c:
            a, b = c["_a"], c["_b"]
        else:
            i, j = int(c.get("first", 0)), int(c.get("last", 0))
            if not (0 <= i <= j < len(sents)):
                continue
            a, b = sents[i]["start"], sents[j]["end"]
        a, b = max(0.0, a - 0.15), min(p.data["source"]["duration"], b + 0.35)   # breathe in, let the last word land
        if b - a < lo * 0.7 or b - a > hi * 1.35 or any(a < y and x < b for x, y in used):
            continue
        used.append((a, b))
        total = sum(int(c.get(k, 0)) for k in ("hook", "flow", "value", "shareability"))
        clips.append({"id": f"c{len(clips) + 1:02d}", "start": round(a, 2), "end": round(b, 2),
                      "score": max(0, min(100, total)),
                      "scores": {k: int(c.get(k, 0)) for k in ("hook", "flow", "value", "shareability")},
                      "title": (c.get("title") or "").strip()[:80], "reason": c.get("reason", ""),
                      "hashtags": c.get("hashtags") or [], "status": "found",
                      "text": " ".join(w["word"] for w in words if a <= w["start"] < b)})
    clips.sort(key=lambda c: -c["score"])
    for n, c in enumerate(clips, 1):
        c["id"] = f"c{n:02d}"
    p.data.update(clips=clips, picker=label, status="found")
    p.save()
    return clips


# ------------------------------------------------------------------ 3 reframe
def track(p, clip, step=0.2):
    """Horizontal subject path (0..1 of the source width) for the clip, smoothed like a camera operator."""
    from . import runtime
    r = subprocess.run([runtime.python("main"), "-m", "dmv.facetrack", p.data["video"], str(clip["start"]),
                        str(clip["end"] - clip["start"]), "null", str(step)], capture_output=True, text=True, cwd=str(ROOT))
    try:
        raw = json.loads(r.stdout.strip().splitlines()[-1])["xs"]
    except Exception:
        raw = []
    xs = np.array([np.nan if v is None else v for v in raw], float)
    found = float(np.mean(~np.isnan(xs))) if len(xs) else 0.0
    if len(xs) == 0 or found < 0.15:
        return {"xs": [0.5], "step": step, "found": found}
    idx = np.arange(len(xs))
    good = ~np.isnan(xs)
    xs = np.interp(idx, idx[good], xs[good])
    # steady camera: hold position until the subject drifts out of a dead zone, then ease over
    held, cur = [], xs[0]
    for v in xs:
        if abs(v - cur) > 0.06:
            cur += (v - cur) * 0.25
        held.append(cur)
    k = max(1, int(0.8 / step))
    sm = np.convolve(np.pad(held, (k, k), mode="edge"), np.ones(2 * k + 1) / (2 * k + 1), mode="same")[k:-k]
    return {"xs": [round(float(v), 4) for v in sm], "step": step, "found": round(found, 2)}


# ------------------------------------------------------------------ 4 render (graphics environment)
def caption_groups(words, a, b, max_words=4, max_gap=0.6):
    ws = [w for w in words if a <= w["start"] < b]
    groups, cur = [], []
    for w in ws:
        if cur and (len(cur) >= max_words or w["start"] - cur[-1]["end"] > max_gap or re.search(r"[.?!,]$", cur[-1]["word"])):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def render_clip(p, clip, task=None):
    """Runs in tools/envs/graphics (skia). Writes clip MP4 + thumbnail; returns paths."""
    import cv2
    import skia
    s = p.data["settings"]
    W, H = ASPECTS.get(s.get("aspect", "9:16"), ASPECTS["9:16"])
    style = s.get("style", "bold")
    layout = s.get("layout", "fill")
    src = p.data["source"]
    a, b = clip["start"], clip["end"]
    fps = 30.0 if src["fps"] > 26 else 25.0
    words = json.loads((p.dir / "transcript.json").read_text())["words"]
    groups = caption_groups(words, a, b)
    path = clip.get("track") or {"xs": [0.5], "step": 0.2}
    xs, step = np.array(path["xs"], float), path["step"]
    # source scaled so the output height is filled (fill) or width fitted (fit)
    sw, sh = src["width"], src["height"]
    if layout == "fill":
        sc = max(H / sh, W / sw)
    else:
        sc = W / sw
    fw, fh = int(round(sw * sc / 2) * 2), int(round(sh * sc / 2) * 2)
    crop = ""
    if layout == "fill":
        # only decode-scale the band the speaker moves through (saves most of the 4K scaling work)
        need = W / sc
        lo_x = max(0.0, float(xs.min()) * sw - need / 2 - 8)
        hi_x = min(float(sw), float(xs.max()) * sw + need / 2 + 8)
        if hi_x - lo_x < need:
            lo_x, hi_x = max(0.0, min(sw - need, lo_x)), min(float(sw), max(need, hi_x))
        cw = int(min(sw, round((hi_x - lo_x) / 2) * 2))
        cx0 = int(max(0, min(sw - cw, round(lo_x / 2) * 2)))
        crop = f"crop={cw}:{sh}:{cx0}:0,"
        band_x0 = cx0 * sc
        fw = int(round(cw * sc / 2) * 2)
    else:
        band_x0 = 0.0
    out_dir = p.dir / "clips"
    out_dir.mkdir(exist_ok=True)
    safe = re.sub(r"[^\w\- ]+", "", clip.get("title") or clip["id"]).strip()[:50] or clip["id"]
    raw = out_dir / f"{clip['id']} {safe}.tmp.mp4"
    final = out_dir / f"{clip['id']} {safe}.mp4"
    from . import runtime
    dec = subprocess.Popen(["ffmpeg", "-v", "error", *runtime.hwaccel(), "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", p.data["video"],
                            "-an", "-vf", f"fps={fps:g},{crop}scale={fw}:{fh}:flags=bicubic", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", f"{fps:g}",
                            "-i", "-", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", p.data["video"], "-map", "0:v", "-map", "1:a?",
                            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
                            "-shortest", "-movflags", "+faststart", str(raw)], stdin=subprocess.PIPE)
    surf = skia.Surface(W, H)
    fonts = _fonts(W)
    n = int(round((b - a) * fps))
    thumb_at = n // 3
    thumb = None
    cache = {"key": None, "ov": None}
    lut = None   # auto exposure: one gentle curve per clip so dark, ungraded footage reads well
    for i in range(n):
        buf = dec.stdout.read(fw * fh * 3)
        if len(buf) < fw * fh * 3:
            break
        f = np.frombuffer(buf, np.uint8).reshape(fh, fw, 3)
        t = i / fps
        if layout == "fill":
            cx = float(np.interp(t / step, np.arange(len(xs)), xs)) * sw * sc - band_x0
            x0 = int(np.clip(cx - W / 2, 0, max(0, fw - W)))
            y0 = (fh - H) // 2
            frame = np.array(f[y0:y0 + H, x0:x0 + W])
        else:   # fit: whole picture, blurred and darkened copy fills the rest
            cover = max(W / fw, H / fh)
            bg = cv2.resize(f, (int(fw * cover) + 2, int(fh * cover) + 2), interpolation=cv2.INTER_LINEAR)
            bx, by = (bg.shape[1] - W) // 2, (bg.shape[0] - H) // 2
            frame = (cv2.GaussianBlur(np.ascontiguousarray(bg[by:by + H, bx:bx + W]), (0, 0), 28) * 0.5).astype(np.uint8)
            y0 = max(0, (H - fh) // 2)
            frame[y0:y0 + min(fh, H), :min(fw, W)] = f[:min(fh, H), :min(fw, W)]
        if s.get("balance", True):
            if lut is None:
                lut = _balance_lut(frame)
            frame = cv2.LUT(frame, lut)
        # captions + hook are drawn on a transparent overlay only when what they show changes
        key = (_caption_key(groups, a + t, style), _hook_key(t) if s.get("hook", True) and clip.get("title") else None)
        if key != cache["key"]:
            c = surf.getCanvas()
            c.clear(skia.Color4f(0, 0, 0, 0))
            _draw_captions(c, groups, a + t, W, H, style, fonts)
            if key[1] is not None:
                _draw_hook(c, clip["title"], t, W, H, fonts)
            ov = surf.makeImageSnapshot().toarray(colorType=skia.kBGRA_8888_ColorType)   # premultiplied BGRA
            rows = np.flatnonzero(ov[:, :, 3].max(axis=1))
            cache.update(key=key, ov=(ov, rows[0], rows[-1] + 1) if len(rows) else None)
        if cache["ov"] is not None:
            ov, r0, r1 = cache["ov"]
            al = ov[r0:r1, :, 3:4].astype(np.float32) / 255
            frame[r0:r1] = np.clip(frame[r0:r1].astype(np.float32) * (1 - al) + ov[r0:r1, :, :3], 0, 255).astype(np.uint8)
        enc.stdin.write(frame.tobytes())
        if i == thumb_at:
            thumb = frame.copy()
        if task and i % 30 == 0:
            task(i / max(1, n))
    enc.stdin.close()
    enc.wait()
    dec.kill()
    # social loudness
    from .master_audio import master
    try:
        master(str(raw), str(final), ceiling=-1.0, target_lufs=-14.0)
        raw.unlink(missing_ok=True)
    except Exception:
        raw.replace(final)
    jpg = final.with_suffix(".jpg")
    if thumb is not None:
        cv2.imwrite(str(jpg), cv2.resize(thumb, (W // 3, H // 3)), [cv2.IMWRITE_JPEG_QUALITY, 85])
    return str(final), str(jpg)


_TF = {}


def _tf(name):
    import skia
    if name not in _TF:
        f = ROOT / "assets" / "fonts" / "Inter" / "extras" / "ttf" / f"{name}.ttf"
        _TF[name] = skia.Typeface.MakeFromFile(str(f)) if f.exists() else skia.Typeface.MakeDefault()
    return _TF[name]


def _fonts(W):
    import skia
    k = W / 1080
    return {"cap": skia.Font(_tf("InterDisplay-ExtraBold"), 74 * k), "capmin": skia.Font(_tf("InterDisplay-SemiBold"), 58 * k),
            "type": skia.Font(skia.FontMgr().matchFamilyStyle("American Typewriter", skia.FontStyle.Normal()) or _tf("Inter-Regular"), 60 * k),
            "hook": skia.Font(_tf("InterDisplay-Bold"), 60 * k), "k": k}


def _paint(rgb, a=1.0):
    import skia
    return skia.Paint(AntiAlias=True, Color4f=skia.Color4f(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, a))


def _balance_lut(frame, target=0.42):
    """Gamma so the clip's median brightness sits near `target` (the house grade's mid target), clamped
    to a gentle range; leaves well-exposed footage alone."""
    lum = (0.114 * frame[:, :, 0] + 0.587 * frame[:, :, 1] + 0.299 * frame[:, :, 2]) / 255
    med = float(np.clip(np.median(lum), 0.02, 0.98))
    gamma = float(np.clip(math.log(target) / math.log(med), 0.6, 1.25))
    if abs(gamma - 1) < 0.06:
        gamma = 1.0
    x = np.arange(256) / 255.0
    return np.clip(255 * x ** gamma + 0.5, 0, 255).astype(np.uint8)


def _caption_key(groups, t, style):
    gi = next((i for i, g in enumerate(groups) if g[0]["start"] - 0.05 <= t <= g[-1]["end"] + 0.35), None)
    if gi is None:
        return None
    g = groups[gi]
    spoken = sum(1 for w in g if w["start"] <= t + (0.02 if style == "typewriter" else 0))
    active = next((i for i, w in enumerate(g) if w["start"] <= t < w["end"] + 0.05), -1)
    blink = (int(t * 2) % 2 == 0 or g[-1]["end"] > t) if style == "typewriter" else None
    return (gi, spoken, active, blink)


def _hook_key(t):
    if t >= 3.2:
        return None
    a = min(1.0, t / 0.35) * min(1.0, max(0.0, (3.2 - t) / 0.4))
    return round(a * 24)


def _draw_captions(c, groups, t, W, H, style, F):
    import skia
    g = next((g for g in groups if g[0]["start"] - 0.05 <= t <= g[-1]["end"] + 0.35), None)
    if g is None:
        return
    k = F["k"]
    y = H * (0.70 if H > W else 0.80)
    if style == "typewriter":
        shown = [w["word"] for w in g if w["start"] <= t + 0.02]
        if not shown:
            return
        font = F["type"]
        txt = " ".join(shown)
        full = " ".join(w["word"] for w in g)
        x = (W - font.measureText(full)) / 2
        c.drawString(txt, x + 3 * k, y + 3 * k, font, _paint((0, 0, 0), 0.6))
        c.drawString(txt, x, y, font, _paint(IVORY))
        cx = x + font.measureText(txt + " ")
        if int(t * 2) % 2 == 0 or g[-1]["end"] > t:
            c.drawRect(skia.Rect.MakeXYWH(cx, y - 44 * k, 26 * k, 50 * k), _paint(OCHRE))
        return
    font = F["capmin"] if style == "minimal" else F["cap"]
    texts = [w["word"].upper() if style in ("bold", "karaoke") else w["word"] for w in g]
    widths = [font.measureText(x) for x in texts]
    space = font.measureText(" ") * 1.25
    total = sum(widths) + space * (len(texts) - 1)
    lines = [list(range(len(texts)))]
    if total > W * 0.84 and len(texts) > 1:
        h = len(texts) // 2
        lines = [list(range(h)), list(range(h, len(texts)))]
    lh = font.getSize() * 1.15
    y0 = y - (len(lines) - 1) * lh / 2
    for li, idxs in enumerate(lines):
        lw = sum(widths[i] for i in idxs) + space * (len(idxs) - 1)
        x = (W - lw) / 2
        yy = y0 + li * lh
        for i in idxs:
            w = g[i]
            active = w["start"] <= t < w["end"] + 0.05
            spoken = w["start"] <= t
            if style == "karaoke" and active:
                pad = 10 * k
                c.drawRoundRect(skia.Rect.MakeXYWH(x - pad, yy - font.getSize() * 0.82, widths[i] + 2 * pad, font.getSize() * 1.05),
                                12 * k, 12 * k, _paint(OCHRE))
            if style != "minimal":
                stroke = _paint((0, 0, 0), 0.85)
                stroke.setStyle(skia.Paint.kStroke_Style)
                stroke.setStrokeWidth(9 * k)
                stroke.setStrokeJoin(skia.Paint.kRound_Join)
                c.drawString(texts[i], x, yy, font, stroke)
            else:
                c.drawString(texts[i], x + 2 * k, yy + 3 * k, font, _paint((0, 0, 0), 0.55))
            if style == "bold":
                col = OCHRE if active else (IVORY if spoken else (200, 200, 200))
            elif style == "karaoke":
                col = INK if active else IVORY
            else:
                col = IVORY
            scale = 1.0   # the colour carries the emphasis; growing the word would squeeze its spaces
            if scale != 1.0:
                c.save()
                c.translate(x + widths[i] / 2, yy - font.getSize() * 0.35)
                c.scale(scale, scale)
                c.drawString(texts[i], -widths[i] / 2, font.getSize() * 0.35, font, _paint(col))
                c.restore()
            else:
                c.drawString(texts[i], x, yy, font, _paint(col))
            x += widths[i] + space


def _draw_hook(c, title, t, W, H, F):
    import skia
    a = min(1.0, t / 0.35) * min(1.0, max(0.0, (3.2 - t) / 0.4))
    if a <= 0:
        return
    font, k = F["hook"], F["k"]
    words = title.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if font.measureText(test) > W * 0.78 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = test
    lines.append(cur)
    lh = font.getSize() * 1.2
    pad = 28 * k
    bw = max(font.measureText(x) for x in lines) + 2 * pad
    bh = lh * len(lines) + pad
    x0 = (W - bw) / 2
    y0 = H * (0.12 if H > W else 0.08) + (1 - a) * 20 * k
    c.drawRoundRect(skia.Rect.MakeXYWH(x0, y0, bw, bh), 18 * k, 18 * k, _paint(IVORY, 0.94 * a))
    c.drawRect(skia.Rect.MakeXYWH(x0, y0 + bh - 6 * k, bw, 6 * k), _paint(OCHRE, a))
    for i, ln in enumerate(lines):
        c.drawString(ln, (W - font.measureText(ln)) / 2, y0 + pad * 0.6 + lh * (i + 0.78), font, _paint(INK, a))


# ------------------------------------------------------------------ export
SMALL_VIDEO_KBPS = 3000   # ≈ 25 MB per minute with the audio; plenty for social platforms, which re-compress anyway


def export_small(src, dst, progress=None):
    """Re-encode a finished clip to a social-sized file (H.264 ~3 Mbit/s, AAC 160 kbit/s), same picture and sound."""
    dur = probe(src)["duration"] or 1
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c:v", "libx264", "-preset", "medium",
                          "-b:v", f"{SMALL_VIDEO_KBPS}k", "-maxrate", f"{int(SMALL_VIDEO_KBPS * 1.5)}k",
                          "-bufsize", f"{SMALL_VIDEO_KBPS * 3}k", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                          "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(dst)],
                         stdout=subprocess.PIPE, text=True)
    for line in p.stdout:
        if line.startswith("out_time_us=") and progress:
            try:
                progress(min(1.0, int(line.split("=")[1]) / 1e6 / dur))
            except ValueError:
                pass
    if p.wait() != 0:
        raise RuntimeError(f"Couldn't make the smaller copy of {Path(src).name}")


# ------------------------------------------------------------------ graphics-environment entry point
if __name__ == "__main__":
    # python -m dmv.clips render PROJECT_DIR CLIP_ID   (needs skia: tools/envs/graphics)
    cmd, folder, cid = sys.argv[1], sys.argv[2], sys.argv[3]
    pr = Project(folder)
    clip = next(c for c in pr.data["clips"] if c["id"] == cid)
    prog = pr.dir / f".{cid}.progress"
    def _p(x):
        prog.write_text(f"{x:.4f}")
    mp4, jpg = render_clip(pr, clip, _p)
    prog.unlink(missing_ok=True)
    print(json.dumps({"mp4": mp4, "jpg": jpg}))
