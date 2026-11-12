"""Resolve, lock and download the engine's Python packages for one chip (Intel x86_64 or Apple Silicon arm64).

Runs on any Mac: pip resolves for the target platform without installing anything. Every wheel is checked
against the hash in the lock. Packages published in the last 14 days are avoided (constraints.txt), and
Demucs is added without its dependency rules (its rules for Intel would cap PyTorch at a vulnerable 2.2).

  .venv/bin/python tools/installer/resolve.py BUILD_DIR x86_64|arm64
"""
import concurrent.futures as cf
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
COMMON = HERE / "common.txt"
EXTRA = {"x86_64": ["opentimelineio==0.17.0"],
         "arm64": ["opentimelineio", "torch==2.15.0", "mlx-whisper", "einops", "julius", "lameenc", "safetensors", "sphn",
                   "pyyaml", "tqdm", "huggingface-hub"]}
# Installed without their dependency rules (their other dependencies are listed explicitly instead):
#  demucs      its Intel-Mac rule caps PyTorch at the vulnerable 2.2 even when resolving for Apple Silicon
#  scenedetect asks for the full opencv-python, which would overwrite the headless OpenCV the engine uses
NO_DEPS = {"x86_64": [("scenedetect", "0.7.2")], "arm64": [("demucs", "4.1.0"), ("scenedetect", "0.7.2")]}
SCENEDETECT_DEPS = ["click", "platformdirs", "tqdm"]


def pypi_wheel(name, version):
    d = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30))
    w = next(f for f in d["urls"] if f["filename"].endswith("-py3-none-any.whl"))
    return w["url"], w["digests"]["sha256"]


def resolve(build, arch):
    r = build / "resolve"
    r.mkdir(parents=True, exist_ok=True)
    # chip-specific wheels first: some "universal2" wheels hold only one architecture (lameenc 1.9.0 cp312)
    vs = ("14_0", "13_0", "12_0", "11_0", "10_15", "10_14", "10_13", "10_9")
    plats = [x for v in vs for x in ("--platform", f"macosx_{v}_{arch}")] + \
            [x for v in vs for x in ("--platform", f"macosx_{v}_universal2")]
    rep = r / f"{arch}.json"
    cmd = [str(ROOT / ".venv/bin/pip"), "install", "--dry-run", "--ignore-installed", "--quiet", "--report", str(rep),
           "--target", str(r / f"t_{arch}"), *plats, "--python-version", "3.12", "--implementation", "cp",
           "--abi", "cp312", "--abi", "abi3", "--abi", "none", "--only-binary=:all:",
           "-r", str(COMMON), "-c", str(HERE / "constraints.txt"), *EXTRA[arch], *SCENEDETECT_DEPS]
    subprocess.run(cmd, check=True)
    rows, urls = [], []
    for x in json.loads(rep.read_text())["install"]:
        m, di = x["metadata"], x["download_info"]
        h = di["archive_info"].get("hashes", {}).get("sha256") or di["archive_info"]["hash"].split("=")[1]
        rows.append(f"{m['name']}=={m['version']} --hash=sha256:{h}")
        urls.append((di["url"], h))
    for n, v in NO_DEPS.get(arch, []):
        u, h = pypi_wheel(n, v)
        rows.append(f"{n}=={v} --hash=sha256:{h}  # installed with --no-deps")
        urls.append((u, h))
    (r / f"lock_{arch}.txt").write_text("\n".join(sorted(rows, key=str.lower)) + "\n")
    (r / f"plain_{arch}.txt").write_text("\n".join(sorted(x.split(" ")[0] for x in rows)) + "\n")
    return urls


def download(build, arch, urls):
    d = build / "wheels" / arch
    d.mkdir(parents=True, exist_ok=True)
    keep = set()

    def get(item):
        url, h = item
        fn = d / url.split("/")[-1].split("#")[0]
        keep.add(fn.name)
        if fn.exists() and hashlib.sha256(fn.read_bytes()).hexdigest() == h:
            return True
        urllib.request.urlretrieve(url, fn)
        ok = hashlib.sha256(fn.read_bytes()).hexdigest() == h
        if not ok:
            fn.unlink()
        return ok
    with cf.ThreadPoolExecutor(6) as ex:
        res = list(ex.map(get, urls))
    for f in list(d.iterdir()):   # drop wheels no longer in the lock (and the drive's "._" metadata files)
        if f.name not in keep:
            f.unlink(missing_ok=True)
    if not all(res):
        raise SystemExit(f"{arch}: {res.count(False)} wheels failed their hash check")
    print(arch, len(urls), "wheels,", round(sum(f.stat().st_size for f in d.iterdir() if not f.name.startswith("._")) / 1e6), "MB, all hashes verified")


if __name__ == "__main__":
    build, arch = Path(sys.argv[1]), sys.argv[2]
    download(build, arch, resolve(build, arch))
