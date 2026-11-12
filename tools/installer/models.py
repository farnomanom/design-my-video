"""Fetch the models the installers carry, pinned to exact revisions and checked against published hashes.

  .venv/bin/python tools/installer/models.py BUILD_DIR

BUILD_DIR/models/common   speech models (base, small, large-v3) + YuNet face detector   — both installers
BUILD_DIR/models/arm64    MLX Whisper large-v3-turbo + Demucs htdemucs                  — Apple Silicon only
Writes BUILD_DIR/models/MODELS.json (source, revision, sha256 of every file).
"""
import hashlib
import re
import json
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
HF = [("common", "Systran/faster-whisper-base", "faster-whisper-base"),
      ("common", "Systran/faster-whisper-small", "faster-whisper-small"),
      ("common", "Systran/faster-whisper-large-v3", "faster-whisper-large-v3"),
      ("arm64", "mlx-community/whisper-large-v3-turbo", "mlx-whisper-large-v3-turbo")]
DEMUCS = ("https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/955717e8-8726e21a.th", "955717e8")   # name = sha256 prefix
YUNET_REPO, YUNET_PATH = "opencv/opencv_zoo", "models/face_detection_yunet/face_detection_yunet_2023mar.onnx"


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def get_json(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "dmv-installer"}), timeout=60))


def hf(repo, dest, log):
    info = get_json(f"https://huggingface.co/api/models/{repo}")
    rev = info["sha"]
    files = get_json(f"https://huggingface.co/api/models/{repo}/tree/{rev}")
    dest.mkdir(parents=True, exist_ok=True)
    rec = []
    local = ROOT / "models" / dest.name
    for f in files:
        if f["type"] != "file" or f["path"].startswith(".") or f["path"].endswith(".md"):
            continue
        want = (f.get("lfs") or {}).get("oid")
        out = dest / f["path"]
        if not out.exists() and local.exists() and (local / f["path"]).exists():
            shutil.copy2(local / f["path"], out)          # already on this Mac (still verified below)
        if not out.exists() or (want and sha256(out) != want):
            urllib.request.urlretrieve(f"https://huggingface.co/{repo}/resolve/{rev}/{f['path']}", out)
        got = sha256(out)
        if want and got != want:
            raise SystemExit(f"hash mismatch: {repo}/{f['path']}")
        if not want:   # small text files aren't LFS: check the git blob id instead
            blob = hashlib.sha1(b"blob %d\0" % out.stat().st_size + out.read_bytes()).hexdigest()
            if blob != f.get("oid"):
                raise SystemExit(f"blob mismatch: {repo}/{f['path']}")
        rec.append({"file": f["path"], "sha256": got, "bytes": out.stat().st_size})
    log.append({"source": f"huggingface.co/{repo}", "revision": rev, "files": rec})


def main(build):
    log = []
    m = Path(build) / "models"
    for group, repo, name in HF:
        hf(repo, m / group / name, log)
        print("ok", repo)
    # Demucs: Meta's server is the only official source. If it can't be reached securely (e.g. its certificate
    # has expired) the model is left out and the app runs without voice isolation; nothing insecure is fetched.
    d = m / "arm64" / "demucs"
    url, prefix = DEMUCS
    out = d / url.split("/")[-1]
    try:
        if not out.exists():
            d.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(url, out)
        got = sha256(out)
        if not got.startswith(prefix):
            out.unlink()
            raise RuntimeError("hash mismatch")
        log.append({"source": url, "files": [{"file": out.name, "sha256": got, "bytes": out.stat().st_size}]})
        print("ok demucs htdemucs")
    except Exception as e:
        shutil.rmtree(d, ignore_errors=True)
        print(f"SKIPPED demucs model: {e}")
    commit = get_json(f"https://api.github.com/repos/{YUNET_REPO}/commits?path={YUNET_PATH}&per_page=1")[0]["sha"]
    meta = get_json(f"https://api.github.com/repos/{YUNET_REPO}/contents/{YUNET_PATH}?ref={commit}")
    y = m / "common" / "yunet"
    y.mkdir(parents=True, exist_ok=True)
    out = y / "face_detection_yunet_2023mar.onnx"
    if not out.exists():
        urllib.request.urlretrieve(meta["download_url"], out)
    # stored with Git LFS: the pointer file at the pinned commit carries the model's real sha256 and size
    ptr = urllib.request.urlopen(f"https://raw.githubusercontent.com/{YUNET_REPO}/{commit}/{YUNET_PATH}", timeout=60).read().decode()
    oid = re.search(r"oid sha256:([0-9a-f]{64})", ptr).group(1)
    if sha256(out) != oid:
        out.unlink()
        raise SystemExit("yunet hash mismatch")
    log.append({"source": f"github.com/{YUNET_REPO}/{YUNET_PATH}", "revision": commit,
                "files": [{"file": out.name, "sha256": sha256(out), "bytes": out.stat().st_size}]})
    print("ok yunet")
    (m / "MODELS.json").write_text(json.dumps(log, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
