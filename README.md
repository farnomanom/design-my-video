# Design My Video

A native macOS app that reviews and improves video edits made in **Final Cut Pro**, and turns long videos into captioned vertical clips for social media. You bring a finished edit; the app analyses it, scores it out of 100, suggests timecoded improvements, applies the ones you approve to a new version of the Final Cut timeline, and renders it. Your original project is never changed.

Built in SwiftUI with a Python engine, version 1.4. Self-contained installers for Intel and Apple Silicon Macs work offline.

---

## What it does

### Edit & design mode
- **Analyse:** reads the rendered video and its Final Cut XML (FCPXML). It detects shots, measures audio, transcribes speech on the Mac (Whisper) and builds a picture of the edit's structure, pacing, titles and sound.
- **Score and review:** an AI reviewer scores the film out of 100 and writes timecoded suggestions with a reason for each, grounded in what's actually on screen. You choose the reviewer: Claude (Claude Code or the API), OpenAI, Gemini, or a local Ollama model, with keys kept in the macOS Keychain.
- **Edit in plain English:** type a request such as *"duck the music 8 dB under the interviews"* or *"cut the second answer"*. The planner turns it into a list of typed timeline operations. You review the plan, and it's applied to a **new version** of the timeline: volume and ducking, fades, transitions, titles, restyling, grade copying, trims, enable/disable, markers, plus re-cutting.
- **Re-cut without breaking sync:** cut, move and swap-take operations blade every connected item at each cut point, as Final Cut's own blade tool does: music stems, overlays, captions and connected storylines. The right-hand pieces are then re-anchored. On a real 10-minute timeline, about 1,600 sampled points had 0 sync errors after cuts and moves.
- **Remove filler words:** a word-for-word transcript of the dialogue (large-v3) becomes one cut per *um / uh / er*, ready to review before anything changes.
- **Render without Final Cut:** a built-in compositor reproduces the FCPXML: cuts, retiming, transforms and crops, opacity and blend modes, dissolves, alpha graphics, Basic Title text, an approximation of Final Cut's Color Board, and the audio mix with keyframes. Output is a preview, HD, HEVC 4K or ProRes, with optional loudness mastering (−23 LUFS broadcast or −14 LUFS social).
- **QC check:** [`tools/qc/qc_timeline.py`](tools/qc/qc_timeline.py) checks a timeline before export without changing it:
  - the file matches Final Cut's own format definition (DTD), and its length and frame alignment are right;
  - no media is missing;
  - no gaps or flash frames show;
  - captions: position, overlaps, rows that shift sideways, reading speed and spelling variants;
  - graphics that collide;
  - transitions, audio roles and fonts.

### Clips mode
An original take on AI clip-making:
1. Transcribe with word timings.
2. Have an AI pick and score self-contained moments for hook, flow, value and shareability, never cutting mid-sentence.
3. Reframe to 9:16, following the speaker's face (YuNet), with smoothing.
4. Add animated word-by-word captions in four styles and a hook title.
5. Master to −14 LUFS and export finished MP4s, at original quality or as smaller files for social.

---

## How it's built

```
app/                SwiftUI app (home screen, Edit & design, Clips, plan review, render sheet, manual, self-test)
engine.py           JSON-over-stdout engine the app drives (analyse, review, plan, apply, render, clips, selftest)
analyze.py          Stand-alone analysis CLI
dmv/                Engine modules
  edit.py           FCPXML document model + typed edit operations
  recut.py          Blade-through-everything re-cutting (cut_range, move_range, swap_take, filler cuts)
  render.py         FCPXML → video compositor (PyAV, NumPy, skia) with occlusion culling and parallel parts
  clips.py          Clip finder, face-tracked reframing, caption overlays, export
  planner.py        Natural-language → validated edit plan (Pydantic schema)
  review.py         Scoring and timecoded suggestions
  providers/        Claude Code, Anthropic API, OpenAI, Gemini, Ollama
  master_audio.py   Loudness measurement and mastering (ffmpeg ebur128 / alimiter, 4× oversampled true peak)
  runtime.py        Detects the bundled runtime, chip features (MLX, Apple GPU, VideoToolbox)
tools/installer/    Reproducible, hash-locked installer builds for Intel and Apple Silicon
tools/qc/           Timeline quality checks
tools/locks/        Pinned versions, hashes and security dossiers
docs/MANUAL.md      The in-app user manual (shown at launch)
```

**Main technologies:** Swift / SwiftUI, Python 3.12, FCPXML 1.11, faster-whisper and MLX Whisper, OpenCV (YuNet), PyAV and ffmpeg, skia-python, Pydantic, Claude.

## Security and supply chain

Every tool and package was security-checked before it was installed:
- Every Python wheel is **hash-locked** (SHA-256) and verified on download. Wheels are unpacked without running package code.
- **No release younger than 14 days** (a supply-chain guard); libraries the app is tested with are pinned.
- **pip-audit**, **osv-scanner** and **GuardDog** run on every lock; each flag is reviewed and recorded in a dossier (`tools/locks/`).
- Models come from pinned revisions with per-file checksums. They're never taken from unofficial mirrors, and TLS is never bypassed.
- Third-party binaries (ffmpeg, MediaInfo) have their signatures and checksums verified and are inspected before they're bundled.
- The installer build refuses to finish if any binary lacks the target chip's code.

## Build from source

Requires macOS 14 or newer, Xcode command-line tools and Python 3.12.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt                            # versions the app is tested with
bash app/build_app.sh            # builds build/Design My Video.app (development build, runs the engine from this folder)
```

Self-contained installers (bundled Python, libraries, ffmpeg and models):

```bash
.venv/bin/python tools/installer/resolve.py BUILD_DIR x86_64     # or arm64
.venv/bin/python tools/installer/models.py  BUILD_DIR
.venv/bin/python tools/installer/build.py   BUILD_DIR x86_64     # → Design My Video <version> (Intel).dmg
```

Speech models are not stored in this repository; `models.py` downloads and verifies them.

## Status

- Version 1.4.0. The Intel installer is tested end to end; the Apple Silicon installer is built and checked statically, and its testing on M-series hardware is in progress.
- The apps are ad-hoc signed and not notarized. On first launch, use **System Settings → Privacy & Security → Open Anyway**.

## Licence

© 2026 farnomanom. All rights reserved. This repository is shared for portfolio review only.
Third-party components keep their own licences: Inter is under the SIL Open Font License (`assets/fonts/Inter/LICENSE.txt`), and the bundled ffmpeg (GPL), MediaInfo, Python packages and models are listed in each installer's `THIRD_PARTY.md`.
