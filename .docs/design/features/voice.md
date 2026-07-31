# Read aloud and hands-free conversation

## What it is

Optional per-session reply synthesis (TTS, "read aloud") plus a browser-captured,
daemon-transcribed Conversation mode (STT, "hands-free"), isolated from terminal, history,
Project, and transcript correctness. Two independent halves share one `VoiceService`,
one `voice_clips` store, and one browser playback element:

- **Read aloud (TTS):** agent replies → spoken audio clips the browser plays.
- **Conversation (STT):** microphone speech → transcribed text → the agent's PTY, driven by
  spoken `Mux` wake-word commands.

## Contract

Read aloud converts an agent session's completed replies into playable audio clips. It is
not an automation observer: observers stay annotate/notify-only behind the fixed OpenRouter
origin, while voice uses a separate synthesis-engine boundary and interactive per-session
state. The optional spoken-summary LLM call is the only OpenRouter traffic and records its
call and spend in the shared automation ledger under `builtin:voice-summary`, bounded by an
independent daily voice budget. Voice failures are typed `VoiceError` diagnostics; they never
affect the PTY, session state, transcripts, history, or projects.

## Read aloud (TTS)

### Generation model

- Per-session generation mode is volatile live-session state: `off`, `on_demand`, or `auto`.
  `null` inherits the configured global default (`tts_default_mode`) while the global TTS
  toggle (`tts_enabled`) is on. The mode is set through ordinary `PATCH /sessions/{id}` and
  dies with the session.
- `auto` subscribes to `turn_ended`, debounces one second per session (`DEBOUNCE_SECONDS`),
  and extracts the completed `last_turn` transcript slice. `last_reply_text` walks backward
  across turn boundaries so a synthetic provider acknowledgement never becomes the "latest
  reply". The summary/verbatim text is split at sentence/word boundaries into short ordered
  clips (`streaming_segments`, ≤420 chars); every clip emits readiness immediately, allowing
  the browser to start playback before later clips finish encoding. Manual generation remains
  one clip through `POST /sessions/{id}/voice/generate`. A per-session lock drops overlapping
  `auto` requests; two engine slots run concurrently (`_engine_semaphore`).
- Content is `summary` (spoken-word summary via OpenRouter, strict `{speech}` JSON schema,
  three-to-eight plain-English sentences) or `verbatim` (assistant text with markdown, code
  fences, links, and tables reduced to listenable prose by `speechify`, bounded by
  `tts_verbatim_max_chars`). The global `tts_content` is the default; each session can
  override it volatilely (`voice_content` via `PATCH /sessions/{id}`, toggled from the player
  strip), and verbatim never touches an LLM. Summary calls check the daily budget
  (`tts_daily_budget_usd`) before spending and need a model (`tts_summary_model` or the
  automation cheap model).
- Engines: `edge` (edge-tts neural voices, e.g. `en-AU-NatashaNeural`, with rate/pitch and
  optional `soften_stops` preprocessing that converts sentence-final periods to commas; MP3
  output, three cold-call retries) and `sapi` (offline Windows `System.Speech` through a
  generated PowerShell script; WAV output). Engine problems surface as typed unavailable/error
  status; terminals are unaffected.

### Storage and playback

- Clips are app-owned files under `<data_dir>/voice/` plus one `voice_clips` SQLite row
  (spoken text, engine/voice, trigger, tokens/cost for summaries, status, error). The store
  confines every `sqlite3` call to one dedicated worker thread (WAL, `synchronous=NORMAL`),
  mirroring `HistoryIndex`, so nothing blocks the event loop. Public snapshots
  (`clip_snapshot`) never expose daemon file paths. A byte-cap prune (`tts_cache_mb`) deletes
  oldest ready clips; stale failed rows expire after a day.
- Files and rows are reconciled, not assumed to agree. Prune only walks row-listed paths, so
  a failed synthesis (stored with an empty `file_path`) or a delete that lost a lock race on
  Windows would leave audio nothing can ever find again: synthesis failure unlinks its
  destination, an unlink that raises is logged rather than escaping as an unhandled task
  exception, and a sweep removes clip-directory files with no matching row.
- Temporary STT utterances are swept too. `asyncio.to_thread` cannot be cancelled, so a
  timed-out transcription keeps its WAV open past the request and the inline unlink can lose;
  a timeout surfaces as a typed `VoiceError` and anything older than the timeout window is
  deleted on the next transcription, keeping "audio is always deleted" true.
- `GET /voice/clips/{id}/audio` serves the file (range support via `FileResponse`); the
  browser player is one HTML5 audio element, so seeking is native. `voice_clip_ready` /
  `voice_clip_failed` events drive the UI and autoplay. Autoplay fires only for live events:
  the event stream replays recent persisted events on every (re)connect for state
  reconstruction, and those catch-up events are flagged `replay` so the client refreshes the
  clip list without re-playing old audio.
- Playback policy is per browser, not per session (`frontend/src/voice.ts`): one unlocked
  singleton audio element is reused so mobile browsers allow programmatic playback after any
  voice-UI gesture (`unlockPlayback` plays a silent WAV inside the gesture), and a
  localStorage device-autoplay toggle decides whether `auto` clips play on that client.
- Auto clips share a stream ID. Barge-in (`bargeInPlayback`) pauses playback, clears its
  queue, and suppresses later clips from the same reply while leaving manual replay available.
- **Turning read aloud off is immediate, at all three scopes.** The singleton element is
  shared, so clips are tagged with the session that owns them and each "off" switch stops
  exactly what it turns off: the pane's `tts:` chip going to `off` calls `stopSessionPlayback`
  (that session's clip is halted and its queued clips dropped; another pane's audio keeps
  playing, and a clip already queued for a still-enabled pane is promoted rather than
  stranded), while the device autoplay toggle and the global Settings switch call
  `stopAllPlayback`. All of them fire on the click, not when the PATCH lands or when the
  current clip finishes. A hard stop abandons the clip (unlike `pausePlayback`, which keeps it
  loaded to resume), so the strip reads as stopped and a later play restarts from zero.
- The autoplay path re-checks the pane's mode on arrival as well as on the daemon, because a
  clip synthesized just before the user hit `off` would otherwise land and start speaking after
  the switch was thrown.

## Conversation mode (STT)

### Capture pipeline (browser, `frontend/src/conversation.ts`)

- `PersistentVoiceCapture` opens `getUserMedia` (mono, echo cancellation, noise suppression,
  auto gain) and a `ScriptProcessorNode`, and stays armed across silence. Exactly one pane
  owns capture per browser; a `mux:conversation-claim` event stops any other pane.
- **Conversation mode and read aloud are independent switches and neither one moves the
  other.** Talk is mic → transcribe → PTY and needs only `stt_enabled`, so starting it does not
  require read aloud, does not set the pane to `auto`, and does not enable device autoplay.
  Starting it does call `unlockPlayback`, which is not a setting: it is the user gesture mobile
  browsers demand before any later programmatic `play()`, so the `read` command can speak at
  all. Only the `read` command needs TTS, and it says so if read aloud is off.
- Voice activity detection is energy-based on an adaptive noise floor (EMA). Speech starts
  when RMS exceeds `max(0.012, noiseFloor*3.2, playbackActive ? 0.035 : 0)` — the raised floor
  during playback keeps the agent's own TTS from self-triggering. A ~320 ms pre-roll is
  retained so the leading phoneme is not clipped. An utterance ends after ≥900 ms of trailing
  silence following ≥220 ms of speech, or a hard 30 s cap.
- Each finished utterance is averaged-downsampled to 16 kHz mono and encoded as 16-bit PCM
  WAV, then POSTed to `voice/transcribe`. Utterances are transcribed through a serialized
  promise chain so independent utterances never interleave.

### Transcription (daemon, `src/swe_mux/voice.py`)

- `transcribe_wav` validates the upload first: mono 16-bit PCM, 8–48 kHz, ≤35 s, ≤2 MiB.
  Audio is written to `<data_dir>/voice/stt/`, transcribed, then the audio and text files are
  **always deleted** — no audio or recognized draft is retained as telemetry.
- Default engine is local **faster-whisper** (`turbo`), auto-preferring CUDA/float16 and
  falling back to CPU/int8 both at model load and at transcription time (CTranslate2 can see a
  GPU whose CUDA/cuDNN runtime DLLs are missing). Decoding uses beam size 5, temperature 0,
  `condition_on_previous_text=False`, and software-development hotwords (product name, agent
  names, and the command verbs) to bias recognition. The model downloads once from Hugging
  Face on first use, then runs from the local cache; download, load, GPU-runtime, and
  CPU-fallback failures surface through the STT diagnostic without submitting the utterance.
- Legacy offline Windows `System.Speech` (`sapi`) remains available via a generated PowerShell
  dictation script.

### Command grammar and submission

- Commands are recognized only as an utterance **suffix**: a **wake word** followed by a
  known **command phrase** at the very end. Everything before it is buffered draft text, and
  commands accumulate across pauses. Both the wake words and the phrase→action mapping are
  **user-configurable** (daemon config `voice_wake_words` / `voice_commands`, edited in
  Settings → Voice, surfaced to the client via `/api/voice`). `buildVoiceMatcher`
  (`conversation.ts`) compiles them into one regex: wake-word alternation + phrase alternation
  matched longest-first, so `read the reply again` wins over `read`, and a bare wake word or an
  unmatched tail leaves the text as draft. `parseMuxVoice` is the default matcher (built from
  the `DEFAULT_WAKE_WORDS` / `DEFAULT_COMMANDS` fallbacks that mirror `config.py`).
- The **action set is fixed** (each is wired to code); only its trigger phrases change:
  `send`, `cancel`, `undo`, `mute`, `read`, `summary`, `verbatim`, `interrupt`, `help`,
  `standby`, `resume`, `stop`. Defaults ship `mux`/`mucks`/`max` as wake words with phrases
  matching the historical grammar.
- **Three run states.** Active (default) buffers speech and runs every command. `standby`
  keeps the mic and transcription running but **discards every utterance except a `resume` (or
  `stop`) command** — so it stays listening yet does nothing until woken. `stop` fully tears
  capture down and releases the mic (only a Talk-button gesture can re-open it, since browsers
  forbid silent re-acquire). The draft buffer survives standby.
- `POST voice/submit` is reconnect-safe: an idempotent `utterance_id` (`claim_submission`, a
  512-entry dedup ring) prevents double-sends, control characters are rejected, and text plus
  one Enter (`{text}\r`) is written atomically while advancing the human-input boundary
  (`voice_prompt_submitted`). `POST voice/interrupt` writes a lone `\x03`. Both require a live
  Claude/Codex session.
- Speech detected during playback triggers barge-in **before** transcription, so the user can
  talk over the agent; the `interrupt` command additionally sends the Ctrl-C.

## Mobile secure context (why HTTPS is required)

Browser microphone capture (`getUserMedia`) needs a **secure context**. `conversationCapability`
checks `window.isSecureContext`; on plain tailnet HTTP it refuses and the Talk button routes
into mobile-voice setup instead.

- swe-mux provisions a private **Tailscale Serve** listener on **HTTPS 443**
  (`https://<device>.ts.net/`) that terminates TLS with a Tailscale-issued (Let's Encrypt)
  cert and proxies to the daemon's loopback port. See `remote-access.md` for the boundary
  detail. 443 is required, **not** the swe-mux port: the daemon binds its port directly on the
  Tailscale IPv4 address for the HTTP fallback, so a Serve listener on that same port would
  collide with the host socket. Serve is brought up automatically at daemon startup
  (`_auto_enable_mobile_voice`, best-effort, idempotent) and via the Settings → Voice button
  for one-time Tailscale HTTPS approval or repair (`enable_mobile_voice_serve`).
- Direct `http://<100.x>:<port>/` stays as a fallback for everything except the microphone.
- The phone must resolve the `.ts.net` hostname over **MagicDNS** ("Use Tailscale DNS" ON in
  the Tailscale app; Android **Private DNS** must be Automatic/Off, not a fixed provider). The
  cert is hostname-bound, so the raw `100.x` IP cannot serve valid HTTPS — there is no
  server-side-only workaround.

## Browser surface

- Every voice affordance lives at the **top** of an agent pane. The pane header carries a
  `.pane-voice` group — the `tts:` chip (off / tap / auto) and the `talk:` Conversation toggle
  — between the cwd and the note/proc/⋯ tools. When TTS is active, the one-row player strip
  (play/pause, seek bar, clip navigation, on-demand generate, verbatim/summary switch, device
  autoplay toggle) expands as its own row *directly beneath* that header, so enabling read
  aloud shortens the terminal rather than overlaying it. The session context menu and command
  palette expose the same playback operations; Settings → Voice owns engines, voice,
  language/model, content, budget, and cache config.
- The chips are in the header and not the bottom command rail because that rail is a
  horizontal scroller the user pages through to reach terminal keys: voice chips both occupied
  its most valuable leading slots and scrolled out of reach. The header group is itself a
  scroller, which is what keeps a long chip set (or a live transcript readout) from pushing the
  pane tools out of a bar that must never wrap — see `ui.md`.
- On touch, the group additionally exposes `audio…` Settings, since a phone has no desktop
  context menu. It does **not** repeat `speak`, summary/verbatim, or autoplay: those render
  only when the player strip renders, and the strip is now the very next row. `tts:setup` and
  `talk:setup` replace their chips when the global feature is disabled, so an unavailable
  feature stays discoverable.
- Browser/PWA background survival is not guaranteed; capture stops if the tab is suspended.

## Session sounds (unrelated audio path)

`frontend/src/sessionSounds.ts` plays short local notification tones for root-agent lifecycle
events (turn complete, waiting, attention/approval, failure, quota reset). It is entirely
client-side (bundled MP3s or a user-uploaded ≤512 KiB clip), per-device via localStorage, with
quiet hours and a 10 s per-event debounce. It shares nothing with the TTS/STT pipeline above
and never touches the daemon or an LLM.

## HTTP surface

- `GET  /api/voice` — engine/STT availability, content/mode defaults, spend, cache stats.
- `POST /api/sessions/{sid}/voice/transcribe` — WAV utterance → recognized text (audio discarded).
- `POST /api/sessions/{sid}/voice/submit` — idempotent voice prompt commit to the PTY.
- `POST /api/sessions/{sid}/voice/interrupt` — send Ctrl-C to the agent.
- `POST /api/sessions/{sid}/voice/generate` — synthesize one clip of the last reply on demand.
- `GET  /api/sessions/{sid}/last-reply` — normalized assistant text (no terminal OSC 52).
- `GET  /api/voice/clips`, `GET /api/voice/clips/{id}/audio`, `DELETE /api/voice/clips/{id}`.
- `POST /api/remote/mobile-voice/enable` — configure/repair the Tailscale Serve HTTPS address.

## Config knobs (`config.py`)

`tts_enabled`, `tts_default_mode`, `tts_content`, `tts_engine`, `tts_edge_voice`/`_rate`/
`_pitch`, `tts_soften_stops`, `tts_sapi_voice`/`_rate`, `tts_summary_model`,
`tts_summary_max_tokens`, `tts_verbatim_max_chars`, `tts_daily_budget_usd`, `tts_cache_mb`;
`stt_enabled`, `stt_engine`, `stt_language`, `stt_whisper_model`; `voice_wake_words`,
`voice_commands` (configurable wake words and per-action trigger phrases).

## Key files

- `src/swe_mux/voice.py` — `VoiceService` (TTS generate + STT transcribe), `VoiceStore`.
- `src/swe_mux/server.py` — voice HTTP handlers.
- `src/swe_mux/tailscale.py`, `src/swe_mux/__main__.py` — mobile HTTPS Serve setup/auto-start.
- `frontend/src/voice.ts` — singleton playback, autoplay, barge-in.
- `frontend/src/VoicePlayer.tsx` — per-pane player strip.
- `frontend/src/ConversationControl.tsx` — Talk button, capture→transcribe→command loop.
- `frontend/src/conversation.ts` — VAD capture, WAV encoding, `Mux` command parser.
- `frontend/src/mobileVoice.ts`, `frontend/src/Settings.tsx` — mobile setup + configuration UI.
