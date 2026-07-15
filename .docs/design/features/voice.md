# Read aloud and dictation

## Contract

Read aloud converts an agent session's completed replies into playable audio clips. It is
not an automation observer: observers stay annotate/notify-only behind the fixed OpenRouter
origin, while voice uses a separate synthesis-engine boundary and interactive per-session
state. The optional spoken-summary LLM call is the only OpenRouter traffic and records its
call and spend in the shared automation ledger under `builtin:voice-summary`, bounded by an
independent daily voice budget. Voice failures are typed diagnostics; they never affect the
PTY, session state, transcripts, history, or projects.

## Generation model

- Per-session generation mode is volatile live-session state: `off`, `on_demand`, or `auto`.
  `null` inherits the configured global default while the global TTS toggle is on. The mode
  is set through ordinary `PATCH /sessions/{id}` and dies with the session.
- `auto` subscribes to `turn_ended`, debounces one second per session, extracts the
  `last_turn` transcript slice, and produces one clip per completed reply. Manual
  generation uses `POST /sessions/{id}/voice/generate` and returns the finished clip.
- Content is `summary` (spoken-word summary via OpenRouter, strict JSON schema) or
  `verbatim` (assistant text with markdown, code fences, links, and tables reduced to
  listenable prose, bounded by a character cap). The global setting is the default; each
  session can override it volatilely (`voice_content` via `PATCH /sessions/{id}`, toggled
  from the player strip), and verbatim never touches an LLM.
- Engines: `edge` (edge-tts neural voices, e.g. `en-AU-NatashaNeural`, with rate/pitch and
  optional soften-stops preprocessing that converts sentence-final periods to commas) and
  `sapi` (offline Windows System.Speech through PowerShell). Engine problems surface as
  typed unavailable/error status; terminals are unaffected.

## Storage and playback

- Clips are app-owned files under `<data_dir>/voice/` plus one `voice_clips` SQLite row
  (spoken text, engine/voice, trigger, tokens/cost for summaries, status, error). Public
  snapshots never expose daemon file paths. A byte-cap prune deletes oldest ready clips;
  stale failed rows expire after a day.
- `GET /voice/clips/{id}/audio` serves the file with range support; the browser player is
  an HTML5 audio element, so seeking is native. `voice_clip_ready` / `voice_clip_failed`
  events drive the UI and autoplay.
- Playback policy is per browser, not per session: one unlocked singleton audio element is
  reused so mobile browsers allow programmatic playback after any voice-UI gesture, and a
  localStorage device-autoplay toggle decides whether `auto` clips play on that client.

## Browser surface

- Agent panes show a `tts:` chip (off / tap / auto, click to cycle) and, when the mode is
  active, a one-row player strip: play/pause, seek bar, clip navigation, on-demand
  generate, and the device autoplay toggle. The session context menu and command palette
  expose the same operations; Settings → Voice owns engine, voice, content, budget, cache,
  and dictation configuration.
- Dictation (STT) is browser-side Web Speech recognition behind a per-pane mic chip on
  agent sessions. It requires a secure context (localhost, or optional Tailscale Serve
  HTTPS; plain tailnet HTTP cannot use the microphone) and inserts the transcript through
  the ordinary paste path without submitting. Mobile keyboards already provide dictation,
  so the chip hides on coarse pointers. No audio reaches the daemon.

## Key files

- `src/swe_mux/voice.py`
- `frontend/src/VoicePlayer.tsx`
- `frontend/src/voice.ts`
- `frontend/src/Settings.tsx`
