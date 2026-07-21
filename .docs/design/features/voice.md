# Read aloud and hands-free conversation

## What it is

Optional per-session reply synthesis plus a browser-captured, daemon-transcribed Conversation
mode, isolated from terminal, history, Project, and transcript correctness.

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
- `auto` subscribes to `turn_ended`, debounces one second per session, and extracts the
  completed `last_turn` transcript slice. The summary/verbatim text is split at sentence/word
  boundaries into short ordered clips; every clip emits readiness immediately, allowing the
  browser to start playback before later clips finish encoding. Manual generation remains one
  clip through `POST /sessions/{id}/voice/generate`.
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
  events drive the UI and autoplay. Autoplay fires only for live events: the event stream
  replays recent persisted events on every (re)connect for state reconstruction, and those
  catch-up events are flagged `replay` so the client refreshes the clip list without
  re-playing old audio. Without the flag, reopening the app would replay the last `auto`
  clip.
- Playback policy is per browser, not per session: one unlocked singleton audio element is
  reused so mobile browsers allow programmatic playback after any voice-UI gesture, and a
  localStorage device-autoplay toggle decides whether `auto` clips play on that client.
- Auto clips share a stream ID. Barge-in pauses playback, clears its queue, and suppresses
  later clips from the same reply while leaving manual replay available.

## Browser surface

- Agent panes lead the terminal's bottom rail with the `tts:` chip (off / tap / auto) and
  `talk:` Conversation toggle before terminal keys, Copy reply, and Paste. When TTS is
  active, a one-row player strip (play/pause, seek bar, clip navigation, on-demand generate,
  device autoplay toggle) sits directly above that rail, inside the pane below the terminal,
  so enabling read aloud shortens the terminal rather than overlaying it. The session context
  menu and command palette expose the same playback operations; Settings → Voice owns engines,
  voice, language/model, content, budget, and cache configuration.
- On mobile agent panes, the horizontally scrollable bottom rail also exposes `speak`,
  summary/verbatim selection, per-device autoplay, and `audio…` Settings. `tts:setup` and
  `talk:setup` remain visible when their global feature is disabled, preventing an unavailable
  feature from becoming undiscoverable on devices without a desktop context menu.
- Conversation mode uses `getUserMedia` plus Web Audio voice activity detection. It remains
  armed across silence, sends only bounded mono PCM WAV utterances, accumulates transcriptions
  across pauses, and recognizes commands only as an utterance suffix beginning with the `Mux`
  wake word. Commands: `send`/`submit`; `cancel`/`clear`; `undo` the latest transcribed phrase;
  `mute` current playback; `read reply`; select `summary` or `verbatim`; show `help`; `interrupt`
  the agent; or `stop listening`/`sleep`. Close phonetic `Mux` transcriptions remain accepted
  internally. Exactly one pane owns capture per browser. Enabling it selects `auto` TTS and
  device autoplay.
- muxd transcribes speech with local faster-whisper (`turbo`, default), automatically preferring
  CUDA/float16 and falling back to CPU/int8. Decoding uses software-development hotwords and
  keeps independent utterances from contaminating one another. Legacy offline Windows
  System.Speech remains available as `sapi`. Audio is deleted after each transcription;
  no audio or recognized draft is retained as telemetry. A submitted prompt uses an idempotent
  utterance ID, writes text plus one Enter atomically, and advances the human-input boundary.
- The selected Whisper model downloads once from Hugging Face on first use, then runs from the
  local cache. Download, model-load, GPU-runtime, and CPU-fallback failures surface through the
  STT diagnostic without retaining or submitting the affected utterance.
- Speech detection during playback triggers barge-in before transcription. `Mux, interrupt`
  additionally sends one Ctrl-C; ordinary speech only stops playback and buffers the follow-up.
- Capture requires a secure context (localhost or Tailscale Serve HTTPS). Plain tailnet HTTP
  cannot request the microphone. Browser/PWA background survival is not guaranteed.

## Key files

- `src/swe_mux/voice.py`
- `frontend/src/VoicePlayer.tsx`
- `frontend/src/voice.ts`
- `frontend/src/ConversationControl.tsx`
- `frontend/src/conversation.ts`
- `frontend/src/Settings.tsx`
