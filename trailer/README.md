# swe-mux trailers

This folder contains the complete source, live captures, original score, intermediate assets, and final renders for the swe-mux trailers.

The 2:11 feature cut is built from the real swe-mux UI at `http://127.0.0.1:8765`.
It records the running project list, agent sessions, user content, quotas, telemetry, settings, process data, and mobile layout.
Treat the raw footage and final feature cut as private unless the visible data has been reviewed for publication.

The earlier 48-second concept cut remains available.
Its application screens are generated facsimiles rather than live footage.

## Feature-cut deliverables

- `output/swe-mux-feature-trailer-1080p.mp4` is the 1920x1080 H.264 feature trailer with AAC audio.
- `output/swe-mux-feature-trailer-preview.mp4` is the 960x540 review encode.
- `output/swe-mux-feature-trailer-score.wav` is the original 48 kHz stereo score.
- `output/swe-mux-feature-trailer-contact-sheet.jpg` is a twelve-frame overview.
- `live-captures/video/` contains timestamped recordings of real UI interactions.
- `live-captures/exploration/` contains the full desktop feature-surface inventory.
- `live-captures/inspection/` contains the initial desktop and mobile UI inventories.
- `build/feature-cut/` contains normalized clips, kinetic caption art, title cards, and the silent master.

## Feature coverage

The feature cut shows real project and agent status, provider accounts, quota reset evidence, resources, split panes, tab stacks, notes, agent context, Git, processes, transcripts, prompt templates, the prompt queue, mailbox and auto-delivery state, automation observers, files, clipboard history, skills and commands, alerts, usage telemetry, command-rail customization, read-aloud, hands-free speech, appearance settings, remote access, process previews, the command palette, broadcast discovery, and the mobile workflow.

Short captions identify the capability while the real interaction remains visible underneath.
The capture cursor is an injected recording aid and is not part of the application.

## Capture the live UI

Start with swe-mux already running on port 8765.
The capture script never starts, stops, rebuilds, or redeploys the app.

Run the complete capture set from the repository root:

```powershell
uv run python trailer/capture_live_ui.py
```

Record selected scenes:

```powershell
uv run python trailer/capture_live_ui.py 04_prompt_queue 08_voice_speech 11_mobile
```

The queue scene stages a draft and then cancels it.
It never arms or sends the demonstration message.
The split scene restores the original tab stack, and the voice scene restores read-aloud to off.

Use the still-state explorer when adding or debugging a surface:

```powershell
uv run python trailer/explore_live_ui.py settings_voice queue process_fleet
```

## Render the feature cut

Run this after at least one successful capture exists for every required scene:

```powershell
uv run python trailer/render_feature_cut.py
```

The renderer selects the newest successful recording for each scene.
Existing feature-cut outputs and build intermediates are moved into `trailer/.trash/` before replacement.

The score is synthesized deterministically by the render script.
It uses layered saw pads, square bass, glass arpeggios, four-on-the-floor drums, half-time breakdowns, impacts, risers, stereo movement, sidechain-style ducking, and a sustained logo resolve.

## Earlier concept cut

- `output/swe-mux-trailer-1080p.mp4` is the original 48-second concept trailer.
- `output/swe-mux-original-score.wav` is its original score.
- `output/contact-sheet.jpg` and `stills/` document its synthetic scenes.

Rebuild it with:

```powershell
uv run python trailer/render.py
```

The scripts require Python 3.11 or newer, Playwright with Chromium, Pillow, NumPy, FFmpeg, FFprobe, and the Windows Cascadia or Consolas fonts.
