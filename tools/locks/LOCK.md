# Tool lock: Design My Video

Installed 10 Nov 2026 after approval of the security dossiers. Each item is pinned to an exact version and SHA-256. Python packages were installed as wheels only, with `--require-hashes`, from the lock files in this folder (`security.txt`, `graphics.txt`). Nothing was installed system-wide.

| Tool | Version / pin | SHA-256 | Location | Status |
| --- | --- | --- | --- | --- |
| osv-scanner | v2.7.1 darwin_amd64 | b8420d5485ba792626315f1bf9a5a2046e971d38eea75bf9090c38852dbc3b1b (matches published SHA256SUMS) | tools/security/bin/osv-scanner | Installed |
| OpenSSF Scorecard | v5.6.0 darwin_amd64 | 6eb92048bc537810d383d7e28233b2f118e78b6fef9e47b2c427c4d01fc096ad (matches published checksums) | tools/security/bin/scorecard | Installed; needs a read-only GitHub token to run |
| GuardDog | 3.2.0 | e503812dc8cf79b74327b7a85a819d62bfb420e3fa8739a7f2f29a93a372c84d | tools/envs/security | Installed (vetting only) |
| pip-audit | 2.10.1 | 99ef3f600a317c1945f1e89e227ef26e1c2d618429b8bd3fa6f4f7c440c4611a | tools/envs/security | Installed (vetting only) |
| skia-python | 144.1 cp312 macOS x86_64 | 89b24f1cb56c3a3a42d30eb3aed91d89061dc236290d4ada9e0ba6aa788c0ba6 | tools/envs/graphics | Installed |
| colour-science | 0.4.7 | fe2383d86e507fc11a4b5206ff31a7399031e0dce527e22ec06eeb491d45d9a7 | tools/envs/graphics | Installed |
| DeepFilterNet | v0.5.6 x86_64-apple-darwin | d3be84003acb7c23e738ad7f70a158ec779a8d233a82e7fa3e717d112eb5b50f (no published checksum; this is the reference) | tools/deepfilternet/deep-filter | Installed |
| Inter | v4.1 (tag commit e3a3d4c57d5ecc01453a575621882a384c1995a3) | Inter-4.1.zip 9883fdd4a49d4fb66bd8177ba6625ef9a64aa45899767dde3d36aa425756b11e (no published checksum) | assets/fonts/Inter | Installed (project only, not added to the system) |
| faster-whisper large-v3 | Systran/faster-whisper-large-v3, model.bin | 69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1 | models/faster-whisper-large-v3 | Installed: checksum matches (downloaded 10 Nov 2026) |
| EA IRIS | electronicarts/IRIS @ d96978ac1107f3463b77f69a9c1b1ec5d45291a0 | pinned by commit | not built | On hold: build needs vcpkg + OpenCV/ffmpeg from source |
| iris-pse-detection (Python port, fallback) | 1.1.2 | 633517f6a24dde5288bbdce59b7bed3eba3e9d9a115a7f9a9e7323f13e15fdef | not installed | Scanned clean; full code read + test against EA's test videos pending |
| OpenTimelineIO | 0.17.0 cp312 macOS x86_64 (newest with an Intel wheel) | 9a9af4105a088c0ab131780e49db268db7e37871aac33db842de6b2b16f14e39 | .venv (lock: timeline.txt) | Installed 11 Nov 2026 |
| otio-fcpx-xml-adapter | 1.0.0 | 9891fcba190e1a2ebc339d2da42398ce98927dfb0055d57d8b37ea8769477f73 | .venv (lock: timeline.txt) | Installed 11 Nov 2026; cannot read the test timeline (fails on generator clips in the primary storyline) — build OTIO timelines from dmv/edit.py instead |
| MediaInfo CLI | 26.10 universal, MediaArea Developer ID C2SZ4M9843, notarized | binary bafcf4b43df04dde8a7ab7f0e410ca44656ec14e9cff73c6f17f78fb8443cf9e (dmg 2345a52f6e5281a15e9ef87acbe5756a9c4727d5629873c013f148a8f82525ab) | tools/bin/mediainfo | Installed 11 Nov 2026 (binary copied from the pkg; installer not run) |
| CommandPost | 2.1.0 | dmg b1a3ca256053a083b59dd1d1db59b68d9b2ea8b83dc2e5214d0eba921eba5e64 | not installed | Rejected for now: broad entitlements, auto-update from develop branch; Computer use covers the need |

## Scan results (10 Nov 2026)

- pip-audit and osv-scanner: graphics lock and the project's existing `.venv` have no known vulnerabilities. The security environment has cryptography 48.0.1 (2 high, 1 moderate; fixed in 49/50, which have no Intel Mac build), as noted in the dossier.
- GuardDog: colour-science, skia-python and iris-pse-detection: no risks. guarddog and pip-audit: low-risk flags explained by their own function (reading a GitHub token; running pip silently). skia-python is mostly compiled code, which GuardDog can only partly inspect.
- GuardDog's own sandbox can't read its install folder on this Mac, so wheels were unzipped by hand (no code run) and the extracted folders scanned.
- SLSA provenance for osv-scanner and Scorecard was not verified: that needs slsa-verifier, which hasn't been vetted. The checksums match.
- DeepFilterNet binary links only to macOS system libraries; the only web addresses in it are error-message links.


Dossier for the 11 Nov 2026 additions: `DOSSIER-2026-11-11-priority1.md`.

| GitHub CLI (gh) | 2.103.3 (26 Oct 2026, ≥ 14 days) | SHA-256 matches the release's checksums.txt; Developer ID "GitHub (VEKTX9H2N7)", notarized, hardened runtime; all published advisories (to GHSA-4mq3, 30 Sep 2026) patched in ≤ 2.102.0 | `~/.local/bin/gh` (user tool, not in the app) | Installed 12 Nov 2026 to publish the private portfolio repository [USER] |
