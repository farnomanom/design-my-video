"""Build a self-contained installer (.dmg) of Design My Video for one chip.

  .venv/bin/python tools/installer/build.py BUILD_DIR x86_64|arm64 [--version 1.4.0]

Needs (made by resolve.py and models.py): BUILD_DIR/wheels/<arch>, BUILD_DIR/models, BUILD_DIR/runtime (portable
Python + ffmpeg). The app is assembled inside an APFS disk image (BUILD_DIR may be on an ExFAT drive, which would
lose executable bits and symbolic links), then written out as a compressed read-only .dmg.

Layout inside the app:  Contents/Resources/engine/
    dmv-engine            launcher the app runs
    python/               portable CPython 3.12 + every library (hash-locked wheels, installed without running them)
    bin/                  ffmpeg, ffprobe (signed static builds), mediainfo
    app/                  engine.py, analyze.py, dmv/, assets (Inter fonts, logo), models/
    THIRD_PARTY.md        every component, version and licence
"""
import argparse
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".claude" / "progress"))
try:
    import bgjob   # progress on the background-jobs band above the Claude Code prompt
except Exception:
    bgjob = None
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PY = {"x86_64": "cpython-3.12.16+20261026-x86_64-apple-darwin-install_only_stripped.tar.gz",
      "arm64": "cpython-3.12.16+20261026-aarch64-apple-darwin-install_only_stripped.tar.gz"}
FF = {"x86_64": "amd64", "arm64": "arm64"}
LABEL = {"x86_64": "Intel", "arm64": "Apple Silicon"}
MIN_OS = "14.0"   # OpenCV 4.14 and MLX wheels need macOS 14 (Sonoma) or newer


def run(*a, **kw):
    return subprocess.run([str(x) for x in a], check=True, **kw)


def copytree(src, dst, ignore=("__pycache__", "._*", ".DS_Store", "*.pyc")):
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*ignore), dirs_exist_ok=True)


def install_wheels(build, arch, site):
    """Unpack the locked wheels into site-packages with pip (no code from them runs; --no-deps, offline)."""
    tmp = site.parent / "_wheels_tmp"
    vs = ("14_0", "13_0", "12_0", "11_0", "10_15", "10_14", "10_13", "10_9")
    plats = [x for v in vs for x in ("--platform", f"macosx_{v}_{arch}")] + \
            [x for v in vs for x in ("--platform", f"macosx_{v}_universal2")]
    wheels = sorted(str(w) for w in (build / "wheels" / arch).glob("*.whl") if not w.name.startswith("._"))
    run(ROOT / ".venv/bin/pip", "install", "--quiet", "--no-deps", "--no-index", "--only-binary=:all:", "--no-compile",
        "--target", tmp, *plats, "--python-version", "3.12", "--implementation", "cp", "--abi", "cp312", "--abi", "abi3",
        "--abi", "none", *wheels)
    for item in tmp.iterdir():
        dst = site / item.name
        if dst.exists():
            shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
        shutil.move(str(item), dst)
    shutil.rmtree(tmp)
    # bytecode for the target Python version (pure compile, nothing is imported)
    run(ROOT / ".venv/bin/python", "-m", "compileall", "-q", "-j", "0", site)
    return wheels


def third_party(site, wheels, arch):
    rows = []
    for d in sorted(site.glob("*.dist-info")):
        meta = (d / "METADATA").read_text(errors="ignore") if (d / "METADATA").exists() else ""
        name = re.search(r"^Name: (.+)$", meta, re.M)
        ver = re.search(r"^Version: (.+)$", meta, re.M)
        lic = re.search(r"^License-Expression: (.+)$", meta, re.M) or re.search(r"^License: (.+)$", meta, re.M)
        cls = re.findall(r"^Classifier: License :: (.+)$", meta, re.M)
        rows.append(f"| {name.group(1) if name else d.name} | {ver.group(1) if ver else ''} | "
                    f"{(lic.group(1)[:60] if lic else '') or (cls[-1] if cls else 'see package')} |")
    return ("# Third-party components\n\nDesign My Video for " + LABEL[arch] + " Macs bundles these free, open-source components.\n\n"
            "| Component | Version | Licence |\n|---|---|---|\n"
            "| CPython (python-build-standalone 20261026) | 3.12.16 | PSF-2.0 |\n"
            "| FFmpeg (static build by Martin Riedl, ffmpeg.martin-riedl.de) | 9.0.2 | GPL-3.0-or-later (built with x264, x265). Source: https://ffmpeg.org/download.html and https://ffmpeg.martin-riedl.de |\n"
            "| MediaInfo CLI (MediaArea) | 26.10 | BSD-2-Clause |\n"
            "| Inter typeface | 4.1 | SIL Open Font License 1.1 |\n"
            "| faster-whisper models (Systran: base, small, large-v3) | — | MIT |\n"
            "| YuNet face detector (OpenCV Zoo) | 2023mar | MIT |\n"
            + ("| MLX Whisper large-v3-turbo (mlx-community, from OpenAI Whisper) | — | MIT |\n"
               + ("| Demucs htdemucs model (Meta) | — | MIT |\n" if (site.parent.parent.parent.parent / "app/models/demucs").exists() else "")
               if arch == "arm64" else "")
            + "\n".join(rows) + "\n")


def check_arch(root, arch):
    """Every Mach-O file must contain code for the target chip (a mislabeled wheel is caught here)."""
    want = "arm64" if arch == "arm64" else "x86_64"
    wrong = []
    for p in root.rglob("*"):
        if not p.is_file() or p.is_symlink() or p.suffix in (".py", ".pyc", ".txt", ".json", ".md", ".onnx", ".bin", ".npz",
                                                                ".safetensors", ".th", ".yaml", ".ttf", ".png"):
            continue
        with open(p, "rb") as f:
            magic = f.read(4)
        if magic not in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe"):
            continue
        out = subprocess.run(["lipo", "-archs", str(p)], capture_output=True, text=True).stdout.split()
        if want not in out:
            wrong.append(str(p.relative_to(root)))
    return wrong


LAUNCHER = """#!/bin/bash
# Design My Video engine launcher (inside the app). Everything it needs is in this folder.
DIR="$(cd "$(dirname "$0")" && pwd)"
export PATH="$DIR/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin"
export DMV_BUNDLED=1 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export HF_HOME="$HOME/Library/Caches/Design My Video/huggingface" TORCH_HOME="$HOME/Library/Caches/Design My Video/torch"
exec "$DIR/python/bin/python3" "$DIR/app/engine.py" "$@"
"""

README = """Design My Video {version} for {label} Macs
============================================

INSTALL
1. Drag "Design My Video" onto the Applications folder.
2. Double-click the app in Applications. macOS says it "could not verify" the app: click Done (NOT Move to Trash).
   Then open System Settings → Privacy & Security, scroll down to Security, click "Open Anyway" next to
   "Design My Video was blocked", enter your password and click Open. Once per version.
   (macOS 15 Sequoia and later removed the right-click → Open shortcut.)
   Or in Terminal, once: xattr -dr com.apple.quarantine "/Applications/Design My Video.app"
   (The app isn't notarized by Apple: that needs a paid Apple Developer membership. It is ad-hoc signed.)
3. On first launch the app checks itself (a few seconds). Help → Run Self-Test repeats the check any time.

THIS VERSION
{features}

NEEDS
- macOS 14 (Sonoma) or newer, {need}.
- About {gb} GB of disk space once installed.
- For AI reviews, edit planning and clip picking: Claude Code signed in (recommended, no key needed), or an
  Anthropic / OpenAI / Gemini API key in Settings, or Ollama for fully offline use.
- Everything else (transcription, analysis, editing, rendering, clipping) runs on the Mac, offline.

The user manual opens with the app (Help → Design My Video User Manual).
Third-party components and licences: inside the app, Contents/Resources/engine/THIRD_PARTY.md.
"""

FEATURES = {
    "x86_64": "- Speech: Whisper base / small / large-v3 (on the processor).\n- Faces: YuNet detector.\n"
              "- Video: software decoding (most reliable on Intel), x264/x265 encoding.",
    "arm64": "- Speech: Apple MLX Whisper large-v3-turbo on the Apple GPU (fast, word timings), plus Whisper base / small / large-v3.\n"
             "- Voice isolation: Demucs (htdemucs) on the Apple GPU separates voice from music before transcribing,\n"
             "  so fillers and clip captions are found even under music.\n- PyTorch 2.15 with Apple GPU (Metal) acceleration.\n"
             "- Video: VideoToolbox hardware decoding for much faster renders and clips; x264/x265 encoding.\n- Faces: YuNet detector.",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("build")
    ap.add_argument("arch", choices=["x86_64", "arm64"])
    ap.add_argument("--version", default="1.4.0")
    a = ap.parse_args()
    build, arch = Path(a.build), a.arch
    t0 = time.time()
    label = LABEL[arch]
    job = bgjob.Job(f"installer-{arch}", f"Building the {label} installer", project="Design My Video") if bgjob else None

    def step(pct, note):
        if job:
            job.update(pct, note, force=True)
    # 1. an APFS workspace (sparse image) on the build drive
    work_img = build / f"work-{arch}.sparseimage"
    mnt = Path(f"/Volumes/DMV-work-{arch}")
    if mnt.exists():
        run("hdiutil", "detach", mnt, "-force")
    if work_img.exists():
        work_img.unlink()
    step(2, "making the build workspace")
    run("hdiutil", "create", "-quiet", "-type", "SPARSE", "-fs", "APFS", "-size", "20g", "-volname", f"DMV-work-{arch}", work_img)
    run("hdiutil", "attach", "-quiet", "-nobrowse", "-mountpoint", mnt, work_img)
    try:
        src = mnt / "dmg"
        app = src / "Design My Video.app"
        step(4, "compiling the app")
        # 2. the app shell (universal Swift binary, icon, manual) from build_app.sh
        run("bash", ROOT / "app/build_app.sh", cwd=ROOT, stdout=subprocess.DEVNULL)
        shutil.copytree(ROOT / "build/Design My Video.app", app, symlinks=True)
        info_p = app / "Contents/Info.plist"
        info = plistlib.loads(info_p.read_bytes())
        info.pop("DMVDevProjectPath", None)
        info.update(CFBundleShortVersionString=a.version, CFBundleVersion=a.version, DMVEngineArch=arch,
                    LSMinimumSystemVersion=MIN_OS, CFBundleDisplayName="Design My Video")
        if arch == "arm64":
            info["LSRequiresNativeExecution"] = True
        info_p.write_bytes(plistlib.dumps(info))
        eng = app / "Contents/Resources/engine"
        eng.mkdir(parents=True)
        step(12, "unpacking Python and the locked libraries")
        # 3. portable Python + locked libraries
        with tarfile.open(build / "runtime" / PY[arch]) as t:
            t.extractall(eng, filter="tar")
        site = eng / "python/lib/python3.12/site-packages"
        wheels = install_wheels(build, arch, site)
        step(25, "adding ffmpeg and MediaInfo")
        # 4. tools
        (eng / "bin").mkdir()
        for tool in ("ffmpeg", "ffprobe"):
            shutil.copy2(build / "runtime/ffmpeg" / FF[arch] / tool, eng / "bin" / tool)
            os.chmod(eng / "bin" / tool, 0o755)
        shutil.copy2(ROOT / "tools/bin/mediainfo", eng / "bin/mediainfo")
        os.chmod(eng / "bin/mediainfo", 0o755)
        # 5. engine code, fonts, logo
        code = eng / "app"
        code.mkdir()
        for f in ("engine.py", "analyze.py"):
            shutil.copy2(ROOT / f, code / f)
        copytree(ROOT / "dmv", code / "dmv")
        copytree(ROOT / "assets/fonts/Inter/extras/ttf", code / "assets/fonts/Inter/extras/ttf")
        shutil.copy2(ROOT / "assets/fonts/Inter/LICENSE.txt", code / "assets/fonts/Inter/LICENSE.txt")
        if (ROOT / "assets/logos").exists():
            copytree(ROOT / "assets/logos", code / "assets/logos")
        step(28, "copying the speech and vision models")
        # 6. models
        models = code / "models"
        models.mkdir()
        for group in ("common",) + (("arm64",) if arch == "arm64" else ()):
            for m in sorted((build / "models" / group).iterdir()):
                if not m.name.startswith("._"):
                    copytree(m, models / m.name)
        if arch == "arm64" and (models / "demucs").exists():
            shutil.copy2(site / "demucs/remote/htdemucs.yaml", models / "demucs/htdemucs.yaml")
        shutil.copy2(build / "models/MODELS.json", models / "MODELS.json")
        step(55, "writing the launcher and licence notices")
        # 7. launcher, notices, lock
        (eng / "dmv-engine").write_text(LAUNCHER)
        os.chmod(eng / "dmv-engine", 0o755)
        (eng / "THIRD_PARTY.md").write_text(third_party(site, wheels, arch))
        shutil.copy2(build / "resolve" / f"lock_{arch}.txt", eng / "LOCK.txt")
        step(57, "checking every program and library is built for this chip")
        wrong = check_arch(eng, arch)
        if wrong:
            raise RuntimeError(f"{len(wrong)} files are not built for {arch}: " + ", ".join(wrong[:5]))
        # 8. sign (ad-hoc) and wrap
        run("codesign", "--force", "--sign", "-", app)
        size_gb = round(sum(f.stat().st_size for f in app.rglob("*") if f.is_file()) / 1e9, 1)
        os.symlink("/Applications", src / "Applications")
        (src / "Read Me First.txt").write_text(README.format(
            version=a.version, label=label, gb=size_gb,
            features=FEATURES[arch] if (arch != "arm64" or (models / "demucs").exists()) else FEATURES[arch].replace(
                "- Voice isolation: Demucs (htdemucs) on the Apple GPU separates voice from music before transcribing,\n"
                "  so fillers and clip captions are found even under music.\n", ""),
            need="Apple Silicon (M1, M2, M3, M4 or later)" if arch == "arm64" else "Intel processor (also runs on Apple Silicon via Rosetta, but the Apple Silicon version is much faster there)"))
        out = build / f"Design My Video {a.version} ({label}).dmg"
        if out.exists():
            out.unlink()
        step(60, "compressing the disk image")
        # -puppetstrings prints "PERCENT:n" lines: map them onto 60–99 %
        p = subprocess.Popen(["hdiutil", "create", "-puppetstrings", "-volname", f"Design My Video ({label})", "-srcfolder", str(src),
                              "-format", "ULFO", "-fs", "HFS+", str(out)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in p.stdout:
            m = re.match(r"PERCENT:(-?[\d.]+)", line.strip())
            if m and job and float(m.group(1)) >= 0:
                job.update(60 + 39 * float(m.group(1)) / 100, f"compressing the disk image · {float(m.group(1)):.0f} %")
        if p.wait() != 0:
            raise RuntimeError("hdiutil could not create the disk image")
        report = {"dmg": str(out), "bytes": out.stat().st_size, "appGB": size_gb, "wheels": len(wheels),
                  "minutes": round((time.time() - t0) / 60, 1)}
        (build / f"build_{arch}.json").write_text(json.dumps(report, indent=1))
        print(json.dumps(report))
        if job:
            job.done(f"{report['bytes'] / 1e9:.1f} GB · {report['minutes']} min")
    except BaseException as e:
        if job:
            job.fail(str(e)[:150])
        raise
    finally:
        run("hdiutil", "detach", "-quiet", mnt, "-force")
        work_img.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
