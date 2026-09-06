# Public demo and feature capture

## Runtime boundary

`frontend/vite.demo.config.ts` compiles the production frontend against `frontend/src/demo/`.
`main.tsx` installs fake fetch and WebSocket adapters before importing `App.tsx`.
The fake daemon supplies typed payloads for every reachable surface.
Unknown behavior must produce an explicit error or accurate empty state rather than a malformed success object.
The error boundary reports a broken surface with a reload path.

All Projects, sessions, transcripts, process details and assistant replies are invented.
The demo cannot prove native CLI compatibility, speech recognition, restart survival or verification speed.
The compact simulated composer displays a newline as an arrow while retaining the newline in the draft.
It handles word deletion, cursor edits and bracketed paste without submitting intermediate lines.

## Controls and focus

`keymapControls.ts` exposes `window.__demoControls` only in the demo build.
The homepage loads the preset catalogue from the desktop frame; `DemoKeymaps.tsx` renders it in the standalone bar.
The selected preset comes from the shared demo store and hints come from the host-resolved map.
Preset changes produce the normal configuration event, which refreshes the application keymap immediately.
The host map is generated from the daemon's actual preset documents; unavailable browser chords are not advertised as usable.
Changing the preset returns focus to the active terminal.

The homepage frames are same-origin and share a page-specific BroadcastChannel group.
Presence is reported separately for each frame.
A hidden frame releases the scenario director so it cannot block the visible frame's walkthrough.
Only one frame drives shared simulated state at a time.

The standalone bar offers a return link, scenarios, keyboard presets on desktop and reset.
The app's available height accounts for the toolbar; the phone toolbar uses a single row.
Reset clears demo state and presentation, not the installed application's data.

## Walkthroughs

`scenarios.ts` owns ordered beats, explanatory copy, commands and simulated backend mutations.
`director.ts` runs one scenario, with token cancellation preventing an abandoned run from continuing.
`DemoDirector.tsx` renders captions, playback controls and progress.
`DemoShow.tsx` measures anchored callouts and input highlights.

Pause preserves the current caption and highlight.
An action already in flight finishes before the next wait.
Next shortens a running wait or advances one beat while paused.
Replay prepares a new run; takeover/resume does not repeat the action already performed.
Callout overlays never intercept the visitor's pointer input.

The catalogue includes task-focused status, input, image attachment, clipboard, history, queue, coordination, preview, landing, failed landing, keyboard and assistant examples.
The homepage's static option list is checked against that catalogue.
Landing proceeds through request, reconciliation, verification and a held result; its failure variant records a failed request and queues the result back to the owning agent.

## Capture

Build the demo, then run:

```text
node frontend/scripts/capture-demo.mjs --scenario land
node frontend/scripts/capture-demo.mjs --scenario status --surface phone
node frontend/scripts/capture-demo.mjs --scenario queue --check
```

The recorder is headless by default and serves only the worktree's static website on an ephemeral loopback port.
`deterministic=1` fixes the fixture clock and random seed.
`capture=1` omits the entire tutorial view and standalone toolbar before rendering.
The scenario engine still performs the workflow, but its cards, controls, highlights and pointer effects are never mounted.
The recorder watches for tutorial overlays throughout a capture and fails if any appear.
Stills wait for the beat's action to complete before capture.
The manifest records the scenario, surface, seed, captions and resulting fixture fingerprint.
The local diagnostic file records lifecycle events without user input content.

Capture output is under `trailer/demo-capture/`, ignored by Git.
Replacement output moves to `.trash/` after validating that the path is inside the project.
Only reviewed exports are copied into `site/img/`.
The showcase exporter records the built demo identity so a later rebuild can be compared with its media.

## Diagnostics and verification

`diagnostics.ts` stores at most 100 local records, rolling over older entries.
Records include timestamp, severity, component, operation and scenario/preset identifier.
There is no network reporting or captured prompt text.
Storage failure does not prevent use of the demo; operational failures also emit a console warning.

Unit checks cover draft input, ordered beats and fixture contracts.
Headless renderer checks cover physical key delivery, preset changes, pause/step/replay, failures and mirrored frames.
The website gate checks viewport overflow, links, media reachability and generated pages.
Use separate ephemeral test ports and keep verification below normal process priority.

The attachment example creates an invented image and dispatches it through the native paste handler.
The fake upload route returns metadata without retaining or transmitting the bytes.
Tutorial captions remain available in the interactive demo and are absent from captured media.
