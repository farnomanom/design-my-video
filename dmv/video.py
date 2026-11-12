"""Picture analysis: probe, cut detection, per-sample colour stats, key frames, contact sheets."""
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

from .timecode import mmss


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    info = json.loads(out)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), {})
    num, _, den = (v.get("avg_frame_rate") or "25/1").partition("/")
    fps = float(num) / float(den or 1) if float(num or 0) else 25.0
    return {
        "duration": float(info["format"].get("duration", 0)),
        "width": v.get("width"),
        "height": v.get("height"),
        "fps": round(fps, 3),
        "videoCodec": v.get("codec_name"),
        "audioCodec": a.get("codec_name"),
        "audioChannels": a.get("channels"),
        "audioRate": int(a.get("sample_rate", 0) or 0),
        "sizeMB": round(int(info["format"].get("size", 0)) / 1e6, 1),
    }


def detect_cuts(path, duration, report=None, threshold=27.0):
    """Hard cuts via PySceneDetect content detector. Returns list of shot (start, end) seconds."""
    from scenedetect import ContentDetector, SceneManager, open_video

    video = open_video(str(path))
    fps = video.frame_rate
    total = max(1, int(duration * fps))
    sm = SceneManager()
    sm.auto_downscale = True
    sm.add_detector(ContentDetector(threshold=threshold, min_scene_len=int(fps * 0.4)))

    def cb(_img, frame_num):
        if report and frame_num % 50 == 0:
            report(frame_num / total, f"Detecting cuts · {mmss(frame_num / fps)} / {mmss(duration)}")

    sm.detect_scenes(video, callback=cb)
    scenes = sm.get_scene_list(start_in_scene=True)
    return [(s.get_seconds(), e.get_seconds()) for s, e in scenes] or [(0.0, duration)]


def colour_samples(path, duration, fps=2, w=160, h=90, report=None):
    """One ffmpeg pass at low res. Per sample: luma, contrast, saturation, warmth, crushed/clipped, motion."""
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(path), "-vf", f"fps={fps},scale={w}:{h}",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    size = w * h * 3
    samples, prev, i = [], None, 0
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        rgb = np.frombuffer(buf, np.uint8).reshape(h, w, 3).astype(np.float32) / 255.0
        luma = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
        hsv = cv2.cvtColor((rgb * 255).astype(np.uint8), cv2.COLOR_RGB2HSV)
        gray = (luma * 255).astype(np.uint8)
        t = i / fps
        samples.append({
            "t": round(t, 2),
            "luma": round(float(luma.mean()), 4),
            "contrast": round(float(luma.std()), 4),
            "sat": round(float(hsv[..., 1].mean() / 255), 4),
            "warmth": round(float(rgb[..., 0].mean() - rgb[..., 2].mean()), 4),
            "crushed": round(float((luma < 0.03).mean()), 4),
            "clipped": round(float((luma > 0.97).mean()), 4),
            "motion": round(float(np.abs(gray.astype(np.int16) - prev).mean() / 255), 4) if prev is not None else 0.0,
        })
        prev = gray.astype(np.int16)
        i += 1
        if report and i % 20 == 0:
            report(t / max(duration, 1), f"Reading colour · {mmss(t)} / {mmss(duration)}")
    proc.wait()
    return samples


def shot_stats(shots, samples):
    keys = ("luma", "contrast", "sat", "warmth", "crushed", "clipped", "motion")
    out = []
    for idx, (s, e) in enumerate(shots):
        ss = [x for x in samples if s <= x["t"] < e] or [min(samples, key=lambda x: abs(x["t"] - (s + e) / 2))]
        row = {"index": idx, "start": round(s, 3), "end": round(e, 3), "length": round(e - s, 3)}
        for k in keys:
            row[k] = round(float(np.mean([x[k] for x in ss])), 4)
        out.append(row)
    return out


def extract_frame(path, t, out_path, width=640):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-q:v", "3", str(out_path)],
        check=True,
    )


def contact_sheets(frames, out_dir, cols=4, rows=4, cell_w=480):
    """frames: list of (jpg_path, label). Writes sheet_XX.jpg grids and returns their paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    per = cols * rows
    paths = []
    for n in range(0, len(frames), per):
        chunk = frames[n:n + per]
        imgs = []
        for p, label in chunk:
            im = cv2.imread(str(p))
            if im is None:
                continue
            im = cv2.resize(im, (cell_w, int(cell_w * im.shape[0] / im.shape[1])))
            cv2.rectangle(im, (0, 0), (im.shape[1], 30), (0, 0, 0), -1)
            cv2.putText(im, label, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
            imgs.append(im)
        if not imgs:
            continue
        ch = max(i.shape[0] for i in imgs)
        blank = np.zeros((ch, cell_w, 3), np.uint8)
        imgs = [cv2.copyMakeBorder(i, 0, ch - i.shape[0], 0, 0, cv2.BORDER_CONSTANT) for i in imgs]
        imgs += [blank] * (per - len(imgs))
        grid = np.vstack([np.hstack(imgs[r * cols:(r + 1) * cols]) for r in range(rows)])
        grid = grid[: ch * ((len(chunk) + cols - 1) // cols)]
        p = out_dir / f"sheet_{n // per + 1:02d}.jpg"
        cv2.imwrite(str(p), grid, [cv2.IMWRITE_JPEG_QUALITY, 82])
        paths.append(p)
    return paths
