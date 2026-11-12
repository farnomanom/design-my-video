# Design My Video — User Manual

Version 1.4 · Last updated 12 November 2026

Design My Video has two modes, both on the **Home** screen:

- **Review & edit an edit** — for a finished edit and its Final Cut timeline (described first below).
- **Make clips from a long video** — turns an interview, podcast, talk or vlog into short, captioned, vertical clips ready for social media (see *Make clips*).

In the first mode, Design My Video looks at a finished video edit and its Final Cut Pro timeline, scores the edit out of 100, explains what works and what doesn't, and — only for the suggestions you approve — writes the edits into a **new copy** of your Final Cut timeline. It can also **re-cut** (remove filler words, cut or move sections, swap takes) and **render a video file** of any version — always as new versions, only when you ask, and always shown to you as a plan first.

> **New here?** Read *Quick start* and *What it can't do* first. This guide opens every time the app starts; you can turn that off at the bottom of this window (or in Settings). You can always reopen it from **Help → Design My Video User Manual**.

## Quick start

1. **Export your edit** from Final Cut Pro as a video file (MP4 or MOV), and **export the timeline** (File → Export XML…, `.fcpxml` or `.fcpxmld`).
2. In the app, click **New analysis**, drop the video into the first box and the timeline into the second.
3. Choose the **AI reviewer** (Automatic uses Claude first), the **transcript quality**, and optionally **Analyse up to** a time (mm:ss) to skip end credits or extras.
4. Click **Analyse film**. Progress shows live; a 10-minute film takes roughly 10–30 minutes on this Mac, depending on transcript quality.
5. Read the **score and report** (**Open report** shows the full page). Tick the suggestions you want and click **Apply approved…**, or use **Edit with AI** to type a request (for example *"duck the music under her voice"*) and click **Plan edits**.
6. Check the **plan**. Nothing changes until you apply it. The result is a new version of your timeline; **Open in Final Cut** imports it.
7. To watch a version without Final Cut, click **Render video…**, choose the version and quality, then **Play render**.

## What it can do

### Analyse a finished edit
- Reads the video: shot changes, key frames and contact sheets, colour and brightness per shot, motion.
- Listens to it: speech transcript with word timings (Whisper, on your Mac), music tempo, energy and mood, loudness (LUFS) and peaks.
- Reads your Final Cut timeline: clips, titles, transitions, effects, audio roles and volumes, markers.

### Score and review
The AI reviewer scores the edit against a fixed rubric (100 points):

| Category | Points |
|---|---|
| Pacing & rhythm | 15 |
| Story flow & structure | 15 |
| Colour & grade cohesion | 15 |
| Typography & titles | 15 |
| Audio mix & music fit | 15 |
| Transitions & motion design | 10 |
| Mood & tone cohesion | 15 |

You get 8–20 timecoded suggestions, each with the reason, and an HTML report with frames. **Review again with…** re-runs the review with a different AI provider so you can compare.

### Send suggestions to Final Cut as markers
**Open markers in Final Cut** writes the suggestions into a copy of your timeline as markers at the right timecodes, so you can work through them in Final Cut yourself.

### Make the edits for you (15 edit types)
For approved suggestions or a typed request, the planner prepares edits from this list:

| Edit | What it does |
|---|---|
| Set volume | Change a clip's level |
| Duck | Lower music automatically under speech |
| Fade audio | Fade a clip's sound in or out |
| Add transition | Add a transition at a cut |
| Remove transition | Remove a transition (straight cut) |
| Add title | Add a text title at a time |
| Restyle title | Change font, size or colour of a title |
| Copy grade | Copy colour correction from one shot to another |
| Trim | Shorten a clip's start or end |
| Duplicate | Duplicate a clip |
| Enable / disable | Switch a clip on or off |
| Add marker | Add a note at a time |
| Cut section | Remove a section and close the gap (ripple delete) |
| Move section | Move a section to another point in the film |
| Swap take | Use a different moment or take for a clip, same place and length |

Every planned edit is listed for you to tick or untick before anything is written.

### Re-cut the edit (on request)
- **Remove filler words…** transcribes the dialogue word for word (on your Mac, with the large Whisper model) and plans one cut for every *um, uh, er, ah, hmm*. Each cut is listed with the words around it; untick any you want to keep. Expect 15–30 minutes for a 10-minute film the first time; the transcript is kept, so planning again is instant.
- **Cut, move or re-order** by typing it in **Edit with AI**: *"cut the second answer about school"*, *"move the last song to the start"*, *"tighten the opening by 10 seconds"*. The planner uses the transcript so cuts land between words.
- **Choose a better take** by asking for it: *"use a better take of her first answer"*. This uses a media log of your original footage (other takes of the same words); without one, the plan says so.
- **Everything stays in sync.** Each cut point is bladed through the whole timeline, like Final Cut's blade tool: the main clip, plus every music stem, overlay, caption and connected storyline that runs across it. Picture, sound and captions after a cut or move shift together. Tested on a real 10-minute documentary: zero sync errors across about 1,600 checked points.
- Transitions that sat exactly on a cut point are removed, and the plan tells you where. Music under a cut will jump at the cut: listen and smooth it in Final Cut if needed.

### Render a video file (on request)
**Render video…** makes a video of the original or any version straight from the timeline, without Final Cut.
- **Quality:** Preview 720p MP4 (fastest), HD 1080p MP4, 4K HEVC MP4, or ProRes 422 HQ at full size.
- **Loudness:** leave as mixed, **Broadcast** (−23 LUFS, peaks ≤ −3 dBTP) or **Social media** (−14 LUFS, peaks ≤ −1 dBTP), measured and set after the render.
- **What's included:** every cut and speed change, position, scale, rotation and crop, opacity and blend modes, Cross Dissolve and Fade To Color, your graphics (including transparency), still photos, Basic Title text (fonts, colours, shadows; your typewriter captions), per-shot Color Board exposure balance, and the sound mix with clip volumes and keyframes.
- **What's not included** (listed in the render's report): LUTs and other colour effects beyond the Color Board exposure, Motion templates other than Basic Title (for example the History Timeline chapter cards), Sharpen and other video effects, audio effects such as the Limiter, and retimed audio.
- **Time on this Mac:** a 10-minute film takes roughly 10–20 minutes as a preview; HD, 4K and ProRes take longer. Renders are saved in the analysis folder under `renders`.
- For the final broadcast master, export from Final Cut, which reproduces everything.

### Versions and safety
- **A new project by default.** Each Apply makes a new, numbered version of the timeline; your original file is untouched.
- **Overwriting the original** is possible only behind two separate confirmations, and the app keeps no backup when you do it.
- Every version is checked against Final Cut's own XML format rules before it is saved.
- Timers show how long analysis, review and edits took.
- Analyses and versions can be moved to the Trash from the sidebar or Settings (your videos and original timelines are never touched).

### AI providers
- **Automatic (recommended):** Claude through Claude Code if it is installed and signed in (no key needed), then an Anthropic API key, then OpenAI, Google Gemini, or a local Ollama model.
- API keys are stored in your Mac's **Keychain**, never in files.
- Ollama runs fully offline and free, with lower review quality.

## Make clips

Click **New clips** (Home or sidebar), drop in a long video, choose the options, then **Find clips**.

| Option | Choices |
|---|---|
| Clips | 1–20 |
| Length | Under 30 s, 30–60 s, 60–90 s, or Auto (20–60 s) |
| Shape | 9:16 vertical, 1:1 square, 4:5 portrait, 16:9 wide |
| Framing | **Follow the speaker** (the crop tracks her face like a steady camera operator) or **Whole picture, blurred fill** |
| Captions | **Bold pop** (big words, the spoken word lights up in ochre), **Highlight box**, **Typewriter** (our house look with the ochre cursor), **Minimal** |
| Hook title | The clip's title shown for its first 3 seconds |
| Focus | Optional: what the clips should be about, e.g. *"advice for new artists"* |

**What happens:**
1. The app transcribes the video with word timings, on your Mac.
2. The AI picker (Claude first) reads the numbered sentences and chooses moments that stand on their own, never cutting a sentence in half. It scores each from 0 to 100: **Hook**, **Flow**, **Value** and **Shareability**, 25 points each. Each clip also gets a title, a reason and hashtags. With no AI available, or a video without speech, a local fallback ranks moments by speech and energy.
3. Each clip is reframed, captioned word by word, given its hook title, set to social loudness (−14 LUFS) and rendered as an MP4 with a thumbnail.

**The clip grid** shows every clip with its score, title and length. Click one to play it, see the score breakdown, reason and transcript, copy the title and hashtags, and **adjust** the start and end (0.5 s steps) and re-render. **Restyle all** re-renders every clip with another caption style, shape, framing or hook setting.

**Export:** **Export clip…** saves one finished clip and **Export all…** saves every finished clip into a folder. Before each export you choose the file size:
- **Original quality** — exactly as rendered, about 100 MB per minute.
- **Smaller files for social media** — re-encoded to about 25 MB per minute; it looks the same once posted, because the platforms re-compress uploads anyway. Takes about 1½ minutes per minute of clip on this Mac.

Each clip is saved with a text file holding its title and hashtags, ready to post. The app remembers your last choice. Clips mode works entirely inside the app: no Final Cut needed.

Clip projects are saved in `Movies/Design My Video/Clips`. On this Mac, transcription takes a few minutes per 10 minutes of video, and each 30–60 s vertical clip takes a few minutes to render.

**Not included:** pasting a YouTube or social link (download the file first), auto-posting to social accounts, B-roll or emoji inserts, and tracking anything other than faces. Music-only or dialogue-free videos get the local fallback, which is simpler than the AI picker.

## What it can't do

- **It never re-cuts on its own.** Cuts, moves and take swaps happen only when you ask, as a plan you approve, in a new version. Clips on the main storyline with a speed change can't be split by a cut; the plan tells you to cut those in Final Cut.
- **Its renders are previews of Final Cut, not replacements.** LUTs, Motion templates, most video effects and audio effects are not reproduced (see *Render a video file*). Export the broadcast master from Final Cut.
- **Final Cut won't refresh an imported project.** After an edit, import the new version; delete or rename the older import first if the names clash.
- **It can't apply some Final Cut effects from a timeline file**, for example a Custom LUT or the Limiter's output ceiling. The app tells you when you need to do a step by hand.
- **It can't see your role switches.** Audio roles you turn off in Final Cut's timeline index are not saved in the XML. Remove unwanted audio from the timeline if it must stay off.
- **Scores are an informed opinion, not a verdict.** The AI looks at contact-sheet frames (up to 24 sheets) and the transcript, not every frame in motion. Treat the score as a second pair of eyes.
- **No broadcast certification.** It measures loudness, but it does not certify flashing-image safety (Harding/PSE) or produce broadcaster delivery files (for example AS-11 for UK broadcasters). Use a post house for those.
- **No paid plug-ins or stock libraries.** Everything it uses is free; it can't add paid Motion templates, fonts or music.
- **Not notarized by Apple.** On first launch, macOS blocks it until you click **Open Anyway** in System Settings → Privacy & Security (see *Installing on another Mac*).
- **Voice isolation (Demucs) isn't in the 1.4.0 Apple Silicon installer yet:** the software is included, but its model's official download server had an expired security certificate when the installers were built, and the model is never fetched from unofficial copies. It will be added in the next build.

## Installing on another Mac

There are two installers. Use the one for the Mac's chip ( → About This Mac → Chip / Processor):

| Installer | For | What's special |
|---|---|---|
| **Design My Video 1.4.0 (Intel).dmg** | Intel Macs | Whisper speech models on the processor, YuNet face tracking, software decoding (most reliable on Intel) |
| **Design My Video 1.4.0 (Apple Silicon).dmg** | M1, M2, M3, M4 and later | Everything in the Intel version, plus **Apple MLX Whisper** (large-v3-turbo on the Apple GPU: much faster transcription with word timings), **PyTorch 2.15 with Apple GPU acceleration**, and **VideoToolbox hardware decoding** for much faster renders and clips |

Both need **macOS 14 (Sonoma) or newer**. Everything is inside the installer — Python, every library, ffmpeg, MediaInfo, fonts and models — so nothing is downloaded during installation and the app works offline (except the AI reviewer, which uses Claude, an API, or a local Ollama).

**To install:**
1. Open the .dmg and drag the app onto **Applications**. Don't open it from the .dmg window.
2. Double-click the app in Applications. macOS says *"Design My Video" Not Opened — Apple could not verify…*. Click **Done**, not Move to Trash.
3. Open **System Settings → Privacy & Security** and scroll down to *Security*. Next to *"Design My Video" was blocked…*, click **Open Anyway**, enter your Mac password, then click **Open**.

You only need to do this once per version. macOS 15 (Sequoia) and later removed the old right-click → Open shortcut. The warning appears because the app is ad-hoc signed and not notarized by Apple: notarization needs a paid Apple Developer membership. The app is not damaged.

Alternative (Terminal, once): `xattr -dr com.apple.quarantine "/Applications/Design My Video.app"`

**Self-test:** the first time each version opens, it checks every part of itself in a few seconds and only speaks up if something is wrong. **Help → Run Self-Test** shows the full list any time. Opening the Apple Silicon version on an Intel Mac shows a message pointing to the Intel version.

## Your privacy

- Analysis (video, audio, transcript) runs **on your Mac**.
- When the review runs, the contact sheets (still frames) and the written digest (transcript and measurements) are sent to the AI provider you chose. Choose **Ollama** if nothing may leave the Mac.
- Keys live in the Keychain. The app never uploads your video file.

## Settings

- **AI reviewer:** default provider, model per provider, API keys.
- **Storage:** where analyses and versions are saved (default `Movies/Design My Video`), and *Move All to Trash*.
- **Show the user manual when the app opens:** on by default.

## Linked tools

All tools are free, pinned to exact versions and security-checked before installation (see `tools/locks/LOCK.md`).

**Used by the app**

| Tool | Used for |
|---|---|
| ffmpeg 9 | Reading video and audio, frames, loudness measurement |
| Python 3.12 engine | Runs the analysis, review and edits |
| faster-whisper | Speech transcript with word timings, on your Mac |
| PySceneDetect, OpenCV | Shot changes, frames, faces; compositing renders |
| skia-python | Drawing titles and captions in renders |
| faster-whisper large-v3 | Word-for-word transcript for filler removal |
| faster-whisper (base/small/large-v3) | Word-timed transcripts (all Macs) |
| Apple MLX Whisper large-v3-turbo | Fast transcription on the Apple GPU (Apple Silicon version) |
| PyTorch 2.15 (Apple GPU) | GPU acceleration for audio models (Apple Silicon version) |
| YuNet (OpenCV) | Face tracking for clip reframing |
| ffmpeg 9.0.2 (signed static build) | Bundled in the installers; VideoToolbox hardware decoding on Apple Silicon |
| Inter type family | Clip captions and hook titles |
| librosa, pyloudnorm | Music tempo and energy, loudness |
| Claude / Anthropic, OpenAI, Gemini, Ollama | AI review and edit planning (your choice) |

**Used in Claude-assisted design work** (run by Claude from this project; not buttons in the app yet)

| Tool | Used for |
|---|---|
| skia-python, colour-science | Animated titles, banners, lower thirds, outros; colour look (LUT) |
| Inter type family (SIL Open Font Licence) | House typeface for graphics |
| DeepFilterNet | Dialogue noise reduction |
| MediaInfo 26.10 | Checking delivery file specs (codec, frame rate, audio layout) |
| OpenTimelineIO 0.17 | Timeline exchange with Premiere, Resolve and EDL |
| osv-scanner, pip-audit, GuardDog, Scorecard | Security checks before anything is installed |

## Troubleshooting

- **Analysis stops at the start:** check the video plays in QuickTime and the timeline file is a Final Cut XML export.
- **"Not set up" next to a provider:** add its key in Settings, or sign in to Claude Code.
- **The new version doesn't appear in Final Cut:** use File → Import → XML; if a project with the same name exists, rename the old one first.
- **Media shows as missing in Final Cut:** reconnect the drive that holds your footage before importing.

## Coming next

- Demucs voice isolation in the Apple Silicon installer (as soon as its official server is reachable securely).
- Tuning the review from editors' feedback.
- Bringing the design-pass tools into the app: caption styles, graphics templates, loudness mastering, outros.
- Clips: brand templates (your fonts, colours, logo, intro and outro), B-roll suggestions, and multi-speaker framing.
- Smoothing music across cuts automatically (short audio crossfades).
- Flashing-image (PSE) pre-check.

## What's new

- **1.4 — 12 Nov 2026 (manual update):** First-launch steps rewritten for macOS 15 and later (System Settings → Privacy & Security → Open Anyway). Right-click → Open no longer works there.
- **1.4 — 12 Nov 2026:** Installers for **Intel** and **Apple Silicon** Macs, fully self-contained and offline. Apple Silicon version adds Apple MLX Whisper, PyTorch with Apple GPU acceleration and VideoToolbox hardware decoding. YuNet face tracking for clips (both). First-launch self-test and **Help → Run Self-Test**. Every bundled library is locked to exact versions and hashes, security-audited, and at least two weeks old.
- **1.3 — 12 Nov 2026:** New **Home** screen with two modes. New **Make clips** mode: AI-picked, scored clips with speaker-following reframe, four animated caption styles, hook titles, social loudness, per-clip adjust and re-render, and **Export clip / Export all** with a choice of original quality or smaller social-sized files. Clips need no Final Cut.
- **1.2 — 11 Nov 2026:** Re-cutting: **Remove filler words…**, plus three new edit types (Cut section, Move section, Swap take) that keep picture, sound and captions in sync. **Render video…**: preview or finished video files of any version, with broadcast or social loudness.
- **1.1 — 11 Nov 2026:** This user manual, shown at start-up (can be turned off). Linked tools added: MediaInfo 26.10, OpenTimelineIO 0.17.
- **1.0 — 10 Nov 2026:** First release: analysis, scoring out of 100, HTML report, markers to Final Cut, 12 edit types with plan review, versions, AI provider choice with Keychain keys.
