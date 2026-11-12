# Security dossier: priority-1 tools (11 Nov 2026)

Checked in a quarantine folder (downloaded, hashed, unpacked or mounted read-only, scanned, nothing run or installed).
Scanners: GuardDog 3.2.0, pip-audit 2.10.1, osv-scanner v2.7.1 (all from `tools/locks/LOCK.md`), macOS `codesign` / `spctl` / `pkgutil`, `otool`.
Not run: OpenSSF Scorecard (needs a read-only GitHub token).

## 1. OpenTimelineIO 0.17.0 + otio-fcpx-xml-adapter 1.0.0 — RECOMMEND INSTALL (low priority)
- Source: PyPI. Maintainer: Academy Software Foundation (Linux Foundation). Licence Apache-2.0.
- Version: 0.18.1 is newest but ships **no Intel Mac wheel**; 0.17.0 (24 Jun 2024) is the newest with one (`cp312 macosx_10_14_x86_64`). Building 0.18.1 from source would mean running its build scripts — not proposed.
- SHA-256 (match PyPI): `OpenTimelineIO-0.17.0-cp312-cp312-macosx_10_14_x86_64.whl` 9a9af4105a088c0ab131780e49db268db7e37871aac33db842de6b2b16f14e39; `otio_fcpx_xml_adapter-1.0.0-py3-none-any.whl` 9891fcba190e1a2ebc339d2da42398ce98927dfb0055d57d8b37ea8769477f73.
- Dependencies: none at runtime on Python 3.12.
- GuardDog: 0 risks (both). pip-audit and osv-scanner: no known vulnerabilities.
- Native code links only to `/usr/lib/libSystem` and `/usr/lib/libc++`.
- Code read: the adapter calls `ffprobe` (argument list, no shell) to read frame size; `otiotool --copy-media` can download a media URL if asked. Nothing runs at import.
- Limits: the FCPXML adapter (2023) reads FCPXML 1.x structure but not Motion titles/effects; our own `dmv/edit.py` stays the FCPXML engine. Value: interchange (EDL/OTIO/Premiere/Resolve) and a base for preview renders.
- Install plan: into `.venv` with `pip install --require-hashes --no-deps --only-binary=:all:`.

## 2. MediaInfo CLI 26.10 — RECOMMEND INSTALL
- Source: mediaarea.net official build `MediaInfo_CLI_26.10_Mac.dmg` (5 Oct 2026). Licence BSD-2-Clause. (Homebrew has no Intel bottle for this macOS, so brew would compile from source.)
- dmg SHA-256 2345a52f6e5281a15e9ef87acbe5756a9c4727d5629873c013f148a8f82525ab (no published checksum file; this is the reference).
- Signature: dmg, pkg and binary signed "Developer ID Application: MediaArea.net (C2SZ4M9843)", notarized by Apple, hardened runtime.
- pkg: no install scripts; payload is one file, `/usr/local/bin/mediainfo` (universal x86_64 + arm64), SHA-256 bafcf4b43df04dde8a7ab7f0e410ca44656ec14e9cff73c6f17f78fb8443cf9e.
- Links only CoreFoundation, libz, libSystem, libc++ (no network libraries). Embedded URLs are format references.
- Install plan: copy the binary to `tools/bin/mediainfo` (project only, no admin, the installer is not run).

## 3. CommandPost 2.1.0 — NOT RECOMMENDED NOW
- Source: GitHub CommandPost/CommandPost (MIT, 572 stars, active; last push 11 Aug 2026). dmg SHA-256 b1a3ca256053a083b59dd1d1db59b68d9b2ea8b83dc2e5214d0eba921eba5e64 (matches GitHub's published digest). App signed "LateNite Films Pty Ltd (A5HDJTY9X5)", notarized; deep signature valid; universal; needs macOS 13+.
- Concerns: very broad entitlements inherited from Hammerspoon (library validation disabled, unsigned executable memory, executable page protection disabled, Apple Events, plus camera, microphone, contacts, calendars, location, photos); needs Accessibility control of the whole Mac; auto-updates from an appcast on the repository's `develop` branch (supply-chain exposure). Latest release notes target Final Cut 12.x; this Mac runs Final Cut 11.0.1 (compatibility not confirmed).
- Decision: the need (see and operate Final Cut, trigger exports) is covered by Claude's built-in Computer use with per-app approval and no extra privileged software. Revisit only if we need unattended batch automation.

## 4. Computer use (Claude desktop app) — NO INSTALL
- Built into the Claude desktop app; currently OFF for this session (no computer-use tools available).
- The user turns it on: Claude app → Settings → This computer → Computer use. Each app (e.g. Final Cut Pro) is then approved individually.
