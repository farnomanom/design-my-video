"""Face position track for a video section.
Uses the YuNet face detector when its model is installed (more accurate on turned, partly lit faces), and
OpenCV's Haar cascades otherwise. Hardware decoding on Apple Silicon.
Usage: python -m dmv.facetrack PATH START SECONDS VF STEP  -> JSON {"xs": [...]}"""
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np


def _frames(path, t, seconds, vf, step, h, fmt, ch):
    from . import runtime
    vf = f"{vf},fps={1 / step},scale=-2:{h}"
    p = subprocess.run(["ffmpeg", "-v", "error", *runtime.hwaccel(), "-ss", f"{t:.2f}", "-t", f"{seconds + 1:.2f}", "-i", path,
                        "-vf", vf, "-f", "rawvideo", "-pix_fmt", fmt, "-"], capture_output=True).stdout
    first = subprocess.run(["ffmpeg", "-v", "error", *runtime.hwaccel(), "-ss", f"{t:.2f}", "-i", path, "-vf", vf,
                            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", fmt, "-"], capture_output=True).stdout
    w = len(first) // (h * ch)
    n = len(p) // (w * h * ch) if w else 0
    return [np.frombuffer(p[i * w * h * ch:(i + 1) * w * h * ch], np.uint8).reshape(h, w, ch) if ch > 1 else
            np.frombuffer(p[i * w * h:(i + 1) * w * h], np.uint8).reshape(h, w) for i in range(n)], w


def track(path, t, seconds, vf, step=0.25, h=360):
    from . import runtime
    model = runtime.yunet_model()
    xs = []
    if model and hasattr(cv2, "FaceDetectorYN"):
        frames, w = _frames(path, t, seconds, vf, step, h, "bgr24", 3)
        if not frames:
            return xs
        det = cv2.FaceDetectorYN.create(model, "", (w, h), 0.7, 0.3, 5000)
        for f in frames:
            _, faces = det.detect(np.ascontiguousarray(f))
            if faces is None or not len(faces):
                xs.append(None)
                continue
            x, y, fw, fh = max(faces, key=lambda r: r[2] * r[3])[:4]   # biggest face = the speaker
            xs.append(float((x + fw / 2) / w))
        return xs
    frames, w = _frames(path, t, seconds, vf, step, h, "gray", 1)
    d = Path(cv2.data.haarcascades)
    dets = [cv2.CascadeClassifier(str(d / "haarcascade_frontalface_alt2.xml")),
            cv2.CascadeClassifier(str(d / "haarcascade_profileface.xml"))]
    for g in frames:
        best = None
        for c in dets:
            for (x, y, fw, fh) in c.detectMultiScale(g, 1.1, 4, minSize=(h // 12, h // 12)):
                if best is None or fw * fh > best[2] * best[3]:
                    best = (x, y, fw, fh)
        xs.append((best[0] + best[2] / 2) / w if best is not None else None)
    return xs


if __name__ == "__main__":
    path, t, sec, vf, step = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4], float(sys.argv[5])
    print(json.dumps({"xs": track(path, t, sec, vf, step)}))
