# Security dossier: Phase 7 installers (12 Nov 2026)

Everything the Intel and Apple Silicon installers bundle. Built by `tools/installer/resolve.py`, `models.py`, `build.py`.
Build workspace: a folder on an external ExFAT drive (builds run inside an APFS sparse image).

## Python packages
- Locks: `installer_x86_64.txt` (66 wheels, 240 MB) and `installer_arm64.txt` (81 wheels, 422 MB), every wheel with its SHA-256; every download verified against it.
- Supply-chain rule: no release younger than 14 days (16 packages pinned back via `tools/installer/constraints.txt`, e.g. anthropic 2.3.0, ctranslate2 4.8.4, regex 2026.9.29, pydantic 2.14.1, scenedetect 0.7.2).
- pip-audit 2.10.1 and osv-scanner 2.7.1: **no known vulnerabilities** in either set.
- PyTorch is **2.15.0** on Apple Silicon (the vulnerable 2.2.2 stays out: Demucs' Intel-only rule would have forced it, so Demucs is installed with `--no-deps` and its runtime dependencies listed explicitly). No PyTorch on Intel.
- scenedetect is installed with `--no-deps`: it asks for the full `opencv-python` 5.0, which would overwrite the headless OpenCV 4.14 the engine uses (Haar cascades + YuNet).
- GuardDog 3.2.0 on the 22 packages new to the project — 0 risks: demucs, docstring_parser, einops, jinja2, jiter, julius, lameenc, markupsafe, mlx, mlx-metal, mlx-whisper, more-itertools, networkx, opentelemetry-api, safetensors, sniffio, sphn. Flags reviewed and accepted:
  - torch (12): `functorch/dim/magic_trace.py` downloads a profiler only when a developer calls `magic_trace()` (never called); the rest are the inductor JIT compiling/loading its own generated code and an Intel-Xeon launcher helper.
  - setuptools (1): `develop` command runs pip (developer install).
  - anthropic (3): reads OS/arch for request headers.
  - tiktoken (1): reads a cache-directory environment variable.
  - regex (1): in its test suite.
- Wheels are unpacked with pip `--no-deps --no-index --only-binary=:all:` (no package code runs at install); bytecode compiled with `compileall` (no imports).

## Fixes found by testing (applied before the final builds)
- **PyAV**: the first resolve took av 19.0.2, which faster-whisper 1.2.1 can't use (`open() got an unexpected keyword argument 'metadata_errors'`), caught by an end-to-end clip test of the Intel installer. Every library the app already runs is now pinned to its tested version (`requirements.txt`, 42 pins in constraints.txt; av 14.2.0, numpy 2.2.6, numba 0.61.2 …); the 14-day rule still applies to the rest.
- **lameenc 1.9.0** cp312 "universal2" wheel contains only x86_64 code. Chip-specific wheels are now preferred over universal2, and build.py refuses to finish if any Mach-O in the engine lacks the target architecture.
- Final audit of both locks after these changes: no known vulnerabilities (pip-audit, osv-scanner).

## Results
- Intel: `Design My Video 1.4.0 (Intel).dmg` 3.84 GB (app 5.1 GB). Self-test from the mounted dmg: all checks pass. End-to-end through the installed engine: transcription (bundled model), AI clip picking via Claude Code, YuNet tracking (face in 100 % of samples), captioned 9:16 render — all OK.
- Apple Silicon: `Design My Video 1.4.0 (Apple Silicon).dmg` 5.47 GB (app 7.3 GB). Verified statically (every Mach-O arm64 or universal, signature valid, models and Info.plist correct). **Not run** — needs an Apple Silicon Mac; its first-launch self-test checks MLX, the Apple GPU and VideoToolbox there.

## Runtime and tools
| Item | Version | Verification |
|---|---|---|
| python-build-standalone (Astral) | CPython 3.12.16, release 20261026 (x86_64 + aarch64, install_only_stripped) | SHA-256 matches the release's SHA256SUMS |
| FFmpeg static (Martin Riedl) | 9.0.2, build 1789931006 (amd64) / 1789931890 (arm64), Sept 2026 | SHA-256 matches published .sha256; Developer ID "Martin Riedl (KU3N25YGLU)", hardened runtime; no third-party dylibs; built with x264, x265, libass, VideoToolbox. GPL — source links in THIRD_PARTY.md. Inspected statically only (running it was blocked by the safety classifier). |
| MediaInfo CLI | 26.10 universal | already vetted (DOSSIER-2026-11-11-priority1.md) |

## Models
| Model | Source (pinned revision) | Verification |
|---|---|---|
| faster-whisper base / small / large-v3 | huggingface.co/Systran/… (ebe41f70 / 536b0662 / edaa852e) | LFS SHA-256 per file; small files by git blob id |
| MLX Whisper large-v3-turbo (Apple Silicon) | huggingface.co/mlx-community/whisper-large-v3-turbo (a4aaeec0) | LFS SHA-256 |
| YuNet face_detection_yunet_2023mar.onnx | github.com/opencv/opencv_zoo (f12e1279) | SHA-256 from the Git LFS pointer at that commit |
| Demucs htdemucs (Apple Silicon) | dl.fbaipublicfiles.com (Meta, only official source) | **NOT INCLUDED**: the server's TLS certificate had expired at build time. Not fetched from unofficial mirrors (8-hex checksum is too weak and the file is a PyTorch pickle that can run code when loaded). Re-run `models.py` + `build.py arm64` once the certificate is renewed. |

## Not included (by decision)
- pyannote (speaker diarization): needs a Hugging Face account and licence acceptance; user chose to leave it out.
- WhisperX: depends on pyannote; MLX Whisper word timings are used instead.
- No notarization (needs a paid Apple Developer membership); apps are ad-hoc signed.
