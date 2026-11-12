"""Audio mastering on export: keep every peak at or below a ceiling (true peak), picture untouched.

Final Cut can't put a limiter on the whole mix from an XML file, so this runs on the exported film:
  1 measure loudness and true peak (EBU R128, ffmpeg ebur128)
  2 limit: upsample x4 (so inter-sample peaks are caught), brick-wall limiter, back to 48 kHz
  3 optional gain first (e.g. to reach a broadcast loudness target)
  4 remux with the original video stream copied bit for bit, then measure again to prove it
Run: .venv/bin/python -m dmv.master_audio IN.mov OUT.mov [--ceiling -2] [--target-lufs -23]
"""
import argparse
import json
import re
import subprocess


def measure(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0", "-af",
                        "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True)
    tail = r.stderr[r.stderr.rfind("Summary:"):]
    get = lambda pat: float(re.search(pat, tail).group(1)) if re.search(pat, tail) else None
    return {"integratedLUFS": get(r"I:\s+(-?[\d.]+) LUFS"), "truePeakDBTP": get(r"Peak:\s+(-?[\d.]+) dBFS"),
            "rangeLU": get(r"LRA:\s+(-?[\d.]+) LU")}


def master(src, out, ceiling=-2.0, target_lufs=None, margin=0.3):
    before = measure(src)
    gain = 0.0
    if target_lufs is not None and before["integratedLUFS"] is not None:
        gain = target_lufs - before["integratedLUFS"]
    limit = 10 ** ((ceiling - margin) / 20)
    af = (f"volume={gain:.2f}dB,aresample=192000,alimiter=limit={limit:.5f}:attack=1:release=60:level=disabled,"
          f"aresample=48000")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:v?", "-map", "0:a:0", "-c:v", "copy",
                    "-af", af, "-c:a", "pcm_s24le" if str(out).endswith((".mov", ".wav")) else "aac",
                    *([] if str(out).endswith((".mov", ".wav")) else ["-b:a", "320k"]), str(out)], check=True)
    after = measure(out)
    return {"before": before, "after": after, "gainDB": round(gain, 2), "ceilingDBTP": ceiling,
            "pass": after["truePeakDBTP"] is not None and after["truePeakDBTP"] <= ceiling}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("out")
    ap.add_argument("--ceiling", type=float, default=-2.0)
    ap.add_argument("--target-lufs", type=float)
    a = ap.parse_args()
    print(json.dumps(master(a.src, a.out, a.ceiling, a.target_lufs), indent=1))
