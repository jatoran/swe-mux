# Read aloud and hands-free conversation

## What it is

Optional per-session reply synthesis (TTS, "read aloud") plus a workspace-level, browser-captured Conversation mode (STT, "hands-free"), isolated from history, Project, and transcript correctness.
Two independent halves share one `VoiceService`, one `voice_clips` store, and one browser playback element:

- **Read aloud (TTS):** agent replies → spoken audio clips the browser plays.
- **Conversation (STT):** microphone speech → buffered draft → the named Agent or text-surface sink, driven by spoken `Mux` wake-word commands.

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
- Temporary STT utterances are swept, but only on the legacy `sapi` path, which is the only one
  that still writes audio to disk. `asyncio.to_thread` cannot be cancelled, so an abandoned
  recognizer keeps its WAV open past the request and the inline unlink can lose; anything older
  than the timeout window is deleted on the next SAPI transcription. The Whisper path decodes
  from memory and leaves nothing to sweep.
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
  The silent unlock is transport setup, never public playback state, because capture uses that
  state to decide whether a new utterance could be speaker echo.
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
  auto gain) and stays armed across silence. The microphone is a
  device singleton, so the controller that owns it (`useConversation`) is held **once at the
  app root**, not once per pane. The global Talk control and pane-attached floating panel are views
  of that one controller. Moving the panel between focused panes never moves the capture state.
  Capture is released when the workspace
  unmounts, so a reload never leaves the browser's recording indicator lit.
- Audio arrives through an **`AudioWorklet`** that batches ~32 ms of microphone blocks on the
  audio thread and posts them to the main thread. A `ScriptProcessorNode` runs its callback on
  the main thread, so every endpointing decision used to be jittered by whatever the UI was
  doing; it remains only as the fallback for browsers without `AudioWorklet`. Both paths
  terminate in a muted gain node feeding the destination, because the graph is rendered by
  pulling backwards from the destination and a capture node with nothing downstream is not
  guaranteed to run.
- Both paths then share one resampler and one framing rule (`audioFrames.ts`): a streaming
  averaging decimator to 16 kHz that carries its partial output across blocks, and a frame
  assembler that emits exactly 512 samples (32 ms) — the only length Silero accepts. The
  decimator is streaming rather than per-block because block lengths are unrelated to the
  resample ratio, and rounding each block independently accumulates drift over a long
  dictation.
- The **draft is an append log of utterances**, not a string (`conversationDraft.ts`): `undo`
  must take back exactly the last phrase recognized, and a typed correction must survive the
  next utterance landing on top of it. A typed edit flattens the log to one segment, because
  per-phrase undo would otherwise start removing wording the user can no longer see; speech
  after an edit appends as its own segment, so the correction survives and `undo` takes back
  only the new speech.
- **Conversation mode and read aloud are independent switches and neither one moves the
  other.** Talk is mic → transcribe → PTY and needs only `stt_enabled`, so starting it does not
  require read aloud, does not set the pane to `auto`, and does not enable device autoplay.
  Starting it does call `unlockPlayback`, which is not a setting: it is the user gesture mobile
  browsers demand before any later programmatic `play()`, so the `read` command can speak at
  all. Only the `read` command needs TTS, and it says so if read aloud is off.
### Endpointing

- **Detection is Silero VAD v5 over `onnxruntime-web`** (`sileroVad.ts`), with the energy
  detector kept as a fallback. The runtime (~11 MB) and the model (~2.3 MB) load lazily on the
  first Talk start and are never part of the app bundle; the runtime is pinned to one thread
  because multi-threaded WASM needs `SharedArrayBuffer`, which needs COOP/COEP headers swe-mux
  does not send on the Tailscale Serve path. A load failure is not fatal: capture keeps running
  on the energy detector, because a microphone that refuses to open is worse than one that
  endpoints slowly. Silero's recurrent state is cleared between utterances.
- **The endpointing rules live in one frame-counted state machine** (`speechGate.ts`) that both
  detectors drive, so they end an utterance the same way. It counts 32 ms frames rather than
  reading a clock, which keeps the decision deterministic when inference falls a block behind —
  exactly when the endpoint matters most. A ~320 ms pre-roll is retained so the leading phoneme
  is not clipped, and a hard 30 s cap ends a monologue.
- **The two detectors are not interchangeable and the gate says so.** Silero ends an utterance
  after **352 ms** of trailing silence, entering speech at probability 0.5 and leaving at 0.35 so
  a marginal frame cannot flap the counter. The energy detector keeps its **900 ms** tail: that
  tail exists *because* RMS-over-noise-floor false-triggers on breath and room noise, so a
  shorter one would cut words in half. Its threshold is unchanged
  (`max(0.012, noiseFloor*3.2, playbackActive ? 0.035 : 0)`) — the raised floor during playback
  keeps the agent's own TTS from self-triggering.
- **Speculative decode.** After 160 ms of silence, Silero capture sends what it has so far to the
  routing decoder while still listening. If speech resumes, the gate voids the speculation and
  the in-flight request is aborted. The trigger is well under the endpoint on purpose: a decode
  begun at 300 ms cannot finish before a 352 ms endpoint, which would leave the grammar
  short-circuit unable to ever fire. Speculation is off for the energy detector, whose false
  triggers would make most speculative decodes garbage.
- **The suffix grammar is an endpoint signal.** A speculative transcript that already ends in a
  wake word plus a complete command phrase is strong evidence the utterance is over, so commands
  skip the rest of the trailing silence; anything else is discarded and the same audio arrives
  again moments later as the real utterance. `commitSpeculative` is the race guard: it refuses if
  speech resumed between the decode starting and the text arriving, which is what stops "…mux,
  send me the file" from submitting at the pause after "send". **Dictation always waits the full
  tail**, because only the wake-word grammar carries that evidence.
- **Push-to-talk** (hold `Ctrl`+`Alt`+`Space`) suspends endpointing entirely: the key release is
  the endpoint. It is the escape hatch for when detection is the problem rather than the fix — a
  noisy room, a deliberate mid-sentence pause. Captured on the window rather than through the
  command registry, which fires on press and cannot express a hold; window blur ends it, so a key
  released over another window cannot latch the microphone open.
- **Playback keeps the microphone open under a constrained duplex policy.**
  Silero probability is accepted during playback only when both probability and RMS clear the playback thresholds; the energy fallback retains its raised RMS floor.
  Capture records whether playback was active and whether the clip was agent or trusted application speech when an utterance began, then disables speculative decoding for that utterance.
  Agent speech permits only exact `mute`.
  Trusted application speech permits the closed read-only lookup/navigation grammar after stopping the current clip; dictation, mutation, and approval confirmation remain blocked.
  A rejected utterance without a wake word names that missing boundary instead of reporting the allowed-command class as if the command itself were unsafe.
- **The endpoint is acknowledged before any text exists**, by flipping the phase to `heard`.
  Silence after speaking reads as broken; the same silence after an acknowledgement reads as
  thinking.
- **Warm-up is reported rather than hidden.** Both cold starts are left in place — the ONNX
  runtime is a lazy download most sessions never need, and warming Whisper on the daemon would
  spend GPU for users who never dictate — so the states say what is true instead:
  - `talk:warming` covers the window between the microphone opening and the detector resolving.
    Capture is genuinely listening in that window, on the energy fallback and its 900 ms tail, so
    reporting `listening` would be true and still misleading. The phase is derived from the
    detector being unresolved, which keeps readiness to one source of truth; `null` (still
    loading) is deliberately distinct from having settled on `energy` (Silero failed).
  - A transcription still running after 1.2 s says the model is loading. Whisper models are
    cached for the life of the **daemon**, not the tab, so the first utterance after a restart or
    a redeploy waits on a model load — an unexplained pause there reads as a hang.
- Each finished utterance is encoded as 16-bit PCM WAV (already at 16 kHz from the capture
  frames) and POSTed to `voice/transcribe`. Real utterances go through a serialized promise chain
  so two can never interleave into the draft out of order; speculative decodes deliberately
  bypass that queue, since waiting behind the previous utterance would put them past their own
  endpoint.

### Transcription (daemon, `src/swe_mux/voice.py`)

- `transcribe_wav` validates the upload first: mono 16-bit PCM at 16 kHz, ≤35 s, ≤2 MiB. One
  rate, not a range, because decoding runs from the raw PCM and accepting another would resample
  silently. **The Whisper path never writes the audio to disk** — validation returns the PCM
  frames and the decoder takes a numpy array — so "no audio is retained" holds by construction
  rather than by a cleanup sweep that can lose a race.
- Default engine is local **faster-whisper**, auto-preferring CUDA/float16 and
  falling back to CPU/int8 both at model load and at transcription time (CTranslate2 can see a
  GPU whose CUDA/cuDNN runtime DLLs are missing). Models download once from Hugging
  Face on first use, then run from the local cache; download, load, GPU-runtime, and
  CPU-fallback failures surface through the STT diagnostic without submitting the utterance.
- **Two decoders by job, chosen by the `X-Mux-Decode-Profile` request header.** A spoken command
  is a reflex and a dictated paragraph is read afterwards, so they get opposite trade-offs:
  - `command` (the routing pass, used by speculative decodes and the wake-word tester) decodes on
    `stt_routing_model` (default `small.en`) with greedy search.
  - `dictation` (the default) decodes on `stt_whisper_model` (default `turbo`), greedy under
    three seconds of audio and `beam_size=5` above it. Beam search costs roughly 10% on a
    one-second utterance and 30% on a twelve-second one, and buys accuracy that only shows up in
    the longer text.
  - The profiles hold **separate locks**, so a speculative routing decode can never queue the
    real utterance behind it — which would cost exactly the latency speculation exists to save.
  - A missing or unloadable routing model is not a failure: commands fall back to the dictation
    model, slower but correct.
- Recognition bias (`hotwords`) is a short technical word list. The routing pass adds the
  configured **wake words** and nothing else: a made-up trigger word is where a general model is
  weakest, while command phrases are ordinary English it already knows. Adding those phrases too
  was measured and reverted — the default set of 57 short, near-identical phrases ("send it",
  "send that", "send message") drove `small.en` into a repetition loop at roughly sixteen times
  the decode time. The wake-word contribution is capped for the same reason.
- Legacy offline Windows `System.Speech` (`sapi`) remains available via a generated PowerShell
  dictation script. It is the only path that still writes the utterance to disk, because the
  recognizer takes a file and nothing else, and therefore the only reason the stale-utterance
  sweep still exists.

### Latency instrumentation

Phase 1 of `development/archive/VOICE_INTERACTION_ROADMAP.md` is judged on one number — end of speech to
executed action — so that number is measured rather than estimated.

- A sample is assembled from both halves of the path. The browser is the only party that knows
  when speech actually stopped, and the daemon is the only party that can separate queueing from
  decoding, so the daemon returns `timings` on the transcribe response and the browser posts the
  merged record to `/voice/stt-latency` **after** the action has already run, where it cannot add
  to what it measures.
- The utterance is dated from **one trailing-silence window before the endpoint fired**, not from
  the endpoint. Dating it from the endpoint would hide the largest stage inside a stage nobody
  looks at.
- Four reported stages: `endpoint → sent` (trailing silence, encode, any queueing behind an
  earlier utterance), `sent → decoding` (transport and daemon queueing; a cold model load lands
  here), `decoding`, and `text → action`. The last is taken as the **residual**, so the four
  always sum to the total and a cost cannot quietly fall out of the breakdown.
- Every posted field is clamped on arrival. A readout that can be poisoned into showing
  impossible stages is worse than none, because it is still believed.
- The Settings → Voice readout reports per-stage p50/p95/max plus a **separate command-only
  total**, since the exit criterion is stated for a short command and dictation decodes several
  times longer audio. Percentiles rather than a mean: one cold model load is a seven-second
  outlier. Samples are also written to `daemon.log`, which is what outlives a restart, and each
  carries the `X-Mux-Utterance-Id` that joins it to the daemon's own decode line.
- The dictation panel shows the last utterance's total, with the breakdown on hover.

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
- **The wake word is chosen from measurement, not from how it looks.** Settings → Voice carries a
  tester: speak N utterances, and each one goes through the same capture pipeline, the same
  transcribe endpoint on the same routing decoder the command path uses, and the matcher compiled
  from the live configuration. It reports the raw transcript per trial, which wake-word spelling
  (if any) was heard as a whole word, and which action fired. That split is the point: "the
  trigger came back as *bucks*" and "the trigger was heard but the phrase after it was not" are
  different problems with different fixes, and neither is visible from the configuration form. A
  transcript with no speech recognized is recorded as a trial rather than discarded, since it is
  the strongest evidence against a trigger word. Good wake words are two to three syllables,
  phonetically distinctive, rare in ordinary speech, and not a prefix of a common word.
- The **capture-control action set is fixed** (each is wired to code); only its trigger phrases change:
  `send`, `cancel`, `undo`, `mute`, `read`, `summary`, `verbatim`, `interrupt`, `help`,
  `standby`, `resume`, `stop`. Defaults ship `mux`/`mucks`/`max` as wake words with phrases
  matching the historical grammar.
- **Workspace commands use the existing command registry.**
  `voiceIntents.ts` strips leading filler, normalizes number words, resolves exact declared aliases and `{text}` slots, and returns `{match, candidates, confidence}`.
  The registry's low-priority catch-all delegates only to the closed grammar in `voiceQueries.ts`; literal command aliases and literal slot templates always outrank it.
  `App.tsx` generates focus commands for every live session and Project, drawer commands from `DRAWER_TABS`, and direct spawn commands for each Project/backend pair.
  The bridge selects a numbered ambiguity candidate or calls `runCommand(id)`; it never owns a second action table.
  A focus command changes the Phase 3 sink immediately, so later dictation follows the navigated session or Project.
- **Settings exposes the complete current command surface.**
  Settings → Voice renders configurable capture-control phrases, the fixed grammar shared with spoken help, and the live registry aliases for current Projects, sessions, workspace panels, launch targets, status, and approvals.
  `voiceCommandReference.ts` groups registry entries for display and omits only the internal `{text}` catch-all because the closed grammar is listed separately.
  Unavailable guarded commands remain visible with their current requirement, so discovery does not depend on first reaching the required state.
- **Spoken lookup is a bounded dialog, not open-ended intent inference.**
  The closed grammar covers command help; Project lists; live, active, working, ready, pending, approval, question, rate-limit, stuck, and failed session filters; overall/current/named Project scopes; entity status; navigation; and last-reply reading.
  Natural read-only forms such as `active sessions`, `list approvals`, `do I have pending sessions in the current project`, and `list Project Alpha sessions` normalize into those same typed queries.
  An unmatched wake-word query speaks its refusal as well as displaying it, so failure cannot look like silence.
  `pending sessions` is an input alias for sessions needing a human answer or approval; spoken output uses `needing you` so it cannot be confused with pending Queue messages.
  Numbered navigation is always available and never depends on a prior spoken list.
  `Project N` follows rendered visible-sidebar order, including the active Project and Group sort.
  Bare `Session N` follows the selected Project's rendered session order: pane traversal first, then unattached sessions by creation order.
  `Project N Session N` resolves both coordinates against one live index before running the existing session-focus command, so a missing session cannot partially change Projects.
  Pending optimistic session rows have no voice address because their identifiers and placement are not final.
  Result lists speak at most five entries, announce item boundaries and the end of the list, support `next page`, `repeat`, and `more detail`, and retain canonical addresses instead of renumbering filtered results.
  Overall lists use compound `Project N, Session N` addresses; Project-scoped lists use that Project's canonical `Session N` values.
  Five-minute validated device-local context preserves only list membership, paging position, and last speech across view remounts.
  Resolution priority is current focus, an exact unique visible name, then the live hierarchical index; ambiguity reports canonical addresses instead of inventing temporary navigation numbers.
  The closed parser tolerates joined `GoToProject` or `Project1` tokens and a duplicated entity word from punctuation-sensitive transcription.
- **Last-reply reading supports a one-shot content choice.**
  `read the last reply` uses the session/global effective mode, while an explicit `summary` or `verbatim` applies to that clip only and does not mutate the session preference.
  Reading may target the focused Agent, an exact visible session name, or the selected Project's canonical session number.
  Summary remains the only model-backed lookup; fleet, help, status, navigation, and verbatim speech remain deterministic.
- **Fleet status is a model-free read projection.**
  `fleetStatus.ts` recomputes from the same session and Project snapshots the UI already receives.
  Each projected field retains provenance, observation age, and confidence.
  Spoken one-line and detailed rundowns are fixed templates, and state-referential navigation uses a closed predicate set over the same projection.
- **Approval is a two-step mutation.**
  The first command requires the focused session to show a stabilized approval, extracts and restates the current operation, and creates a one-use 20-second challenge.
  The second command rechecks session id, agent-run id, PTY approval classification, and the exact prompt fingerprint before writing Enter.
  Cancel only removes the voice challenge and leaves the provider prompt unchanged; no approve-all command exists.
- **Three run states.** Active (default) buffers speech and runs every command.
  `standby` keeps the mic and transcription running but **discards every utterance except a `resume` (or `stop`) command**, so it stays listening yet does nothing until woken.
  `stop` fully tears capture down and releases the mic; only an explicit mic-control or bound-command gesture can re-open it because browsers forbid silent reacquisition.
  The draft buffer survives standby.
- **Capture and target have separate lifetimes.** Talk is one workspace-level browser flag.
  The target follows the focused live Agent, Continuity editor, Scratchpad, Markdown editor, or Queue composer without restarting capture, and the editable draft survives every target change.
  A pin freezes the exact current sink until explicitly released.
- **A text target is a buffer sink, not an execution path.** Send inserts the trimmed voice draft at that surface's caret and clears the voice draft.
  In a Queue composer this only fills the composer; staging, arming, and delivery remain separate explicit Queue actions.
  Agent-only commands (`read`, `summary`, `verbatim`, `interrupt`) refuse a text target and keep the draft.
- All utterances decode through the session-free `POST /api/voice/transcribe` route.
  The target is resolved only when an action needs it, so a focus change during capture cannot send audio to a stale per-session route.
- `POST voice/submit` is reconnect-safe: an idempotent `utterance_id` (`claim_submission`, a
  512-entry dedup ring) prevents double-sends, control characters are rejected, and the
  human-input boundary is advanced (`voice_prompt_submitted`). Single-line text plus one Enter
  (`{text}\r`) is written atomically. A **multi-line** body takes the queue's delivery bytes
  instead (`paste_payload` + `SUBMIT_DELAY_SECONDS` + a separate `\r`): recognition never emits
  a newline, but an edited draft can, and a raw newline submits the prompt early — sending the
  agent half a message and typing the rest at whatever it shows next. Before claiming the id,
  the handler rejects the prompt queue's non-overridable readiness reasons, including approval,
  question, ended-run, and non-agent targets. `POST voice/interrupt`
  writes a lone `\x03`. Both require a live Claude/Codex session.
- Playback carries an explicit `agent` or `system` origin into the capture marks.
  Speech that begins during agent-reply playback may only recognize exact `mute`; every other transcript is discarded as possible echo.
  Trusted application speech from help, fleet, and navigation lists may be interrupted by the closed read-only lookup/navigation grammar, which first stops playback and then resolves the command.
  Dictation, mutations, and approval confirmation are always rejected during playback.

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

- **Read aloud remains session-scoped.** Each Agent pane header carries only its `tts:` chip (off / tap / auto).
  The player strip (play/pause, seek, clip navigation, on-demand generate, verbatim/summary, device autoplay) floats from the pane's zero-height `.voice-overlay-anchor`.
  It never changes terminal geometry.
- **Conversation state is app-level and its primary view is pane-attached.** The Talk toggle sits directly before Run in the mobile toolbar and desktop app header.
  While Talk is on and an Agent pane is focused, the panel floats at the top of that pane in the same `.voice-overlay` stack as the read-aloud player strip.
  The zero-height anchor keeps both surfaces out of terminal layout.
  A fixed top `.conversation-layer` is used only when the active sink has no visible terminal pane, such as a note or Queue composer.
  Capture, draft, target pin, and history stay mounted across Project, pane, and target changes.
- **Talk keeps a reviewable conversation history.** Every recognized utterance and every final Mux outcome is stored in a device-local, app-wide 120-entry ring.
  Lists and help retain their line-broken display text while TTS receives the separately paced speech form.
  Last-reply requests retain the generated reply text, not only a playback status message.
  The panel opens with history visible, follows the newest entry, and provides an explicit clear action.
- **The panel names its sink.** The `to:` row carries the Agent or text-surface label, its pin control, and an unavailable state.
  Send is disabled when the named target disappeared.
  Unpin resumes focus-following without changing the draft.
- **The dictation draft is always editable.** The live `<textarea>` has no edit mode.
  Capture keeps running while typing; an utterance that lands mid-edit appends at the end with the caret and selection preserved.
  One line grows to five, then scrolls internally.
- **Voice stays primary.** `Mux, send` and the panel's Send button commit the same draft to the named sink.
  `Ctrl`/`Cmd`+`Enter` sends from the textarea; `Escape` releases its keyboard focus.
  faster-whisper returns whole utterances rather than partial words, so the panel signals
  arrival with a brief border flash instead of animating a stream it does not receive.
- The player strip and Talk panel each end with a gear into Settings → Voice.
  Disabled read aloud keeps `tts:setup` in Agent headers; disabled Conversation turns the global mic into `Set up voice`.
- `voice.toggleTalk` and `voice.toggleTargetPin` are ordinary registered commands exposed to the palette, keybindings, and optional mobile gesture slots.
- Browser/PWA background survival is not guaranteed; capture stops if the tab is suspended.

## Session sounds (unrelated audio path)

`frontend/src/sessionSounds.ts` plays short local notification tones for root-agent lifecycle
events (turn complete, waiting, attention/approval, failure, quota reset). It is entirely
client-side (bundled MP3s or a user-uploaded ≤512 KiB clip), per-device via localStorage, with
quiet hours and a 10 s per-event debounce. It shares nothing with the TTS/STT pipeline above
and never touches the daemon or an LLM.

## HTTP surface

- `GET  /api/voice` — engine/STT availability, content/mode defaults, spend, cache stats.
- `POST /api/sessions/{sid}/voice/transcribe` — WAV utterance → `{text, timings}`; audio is never
  written to disk. Optional `X-Mux-Decode-Profile` (`command`/`dictation`) and
  `X-Mux-Utterance-Id` headers.
- `POST /api/voice/transcribe`: the target-independent decoder used by workspace Conversation capture and the wake-word tester.
- `GET|POST|DELETE /api/voice/stt-latency` — the end-of-speech-to-action stage breakdown: report,
  record one browser-measured sample, start a fresh run.
- `POST /api/sessions/{sid}/voice/submit` — idempotent voice prompt commit to the PTY.
- `POST /api/sessions/{sid}/voice/approval` - prepare, confirm, or cancel one guarded approval.
- `POST /api/sessions/{sid}/voice/interrupt` — send Ctrl-C to the agent.
- `POST /api/sessions/{sid}/voice/generate` - synthesize one clip of the last reply on demand; optional `{content_mode: summary|verbatim}` is one-shot and does not change the session preference.
- `POST /api/voice/speak` - synthesize trusted application text without a model call.
- `GET  /api/sessions/{sid}/last-reply` — normalized assistant text (no terminal OSC 52).
- `GET  /api/voice/clips`, `GET /api/voice/clips/{id}/audio`, `DELETE /api/voice/clips/{id}`.
- `POST /api/remote/mobile-voice/enable` — configure/repair the Tailscale Serve HTTPS address.

## Config knobs (`config.py`)

`tts_enabled`, `tts_default_mode`, `tts_content`, `tts_engine`, `tts_edge_voice`/`_rate`/
`_pitch`, `tts_soften_stops`, `tts_sapi_voice`/`_rate`, `tts_summary_model`,
`tts_summary_max_tokens`, `tts_verbatim_max_chars`, `tts_daily_budget_usd`, `tts_cache_mb`;
`stt_enabled`, `stt_engine`, `stt_language`, `stt_whisper_model` (dictation),
`stt_routing_model` (spoken commands; blank falls back to the dictation model);
`voice_wake_words`, `voice_commands` (configurable wake words and per-action trigger phrases).

## Key files

- `src/swe_mux/voice.py` — `VoiceService` (TTS generate + STT transcribe), `VoiceStore`, the
  decode profiles, and the latency report helpers.
- `src/swe_mux/server.py` — voice HTTP handlers.
- `src/swe_mux/tailscale.py`, `src/swe_mux/__main__.py` — mobile HTTPS Serve setup/auto-start.
- `frontend/src/voice.ts` — singleton playback, autoplay, barge-in.
- `frontend/src/voiceIntents.ts`, `frontend/src/voiceQueries.ts`, `frontend/src/voiceNavigation.ts`, `frontend/src/fleetStatus.ts` - deterministic registry resolution, typed spoken lookup/paging/help, canonical hierarchical indexes, and fleet speech projection.
- `frontend/src/voiceConversationHistory.ts` - bounded device-local storage for recognized utterances and Mux outcomes, plus the persisted open or collapsed state of the Talk history disclosure.
- `frontend/src/spokenListContext.ts` - validated five-minute device-local membership and paging context for recent spoken lists.
- `frontend/src/VoicePlayer.tsx` — per-pane player strip.
- `frontend/src/ConversationControl.tsx`: `useConversation` (the app-root capture controller, target pin, command loop, speculative decoding, push-to-talk, and Talk history), `ConversationToggle` (toolbar control), `ConversationSurface` (pane placement or top fallback), and `DictationPanel` (draft and history surface).
- `frontend/src/conversationTarget.ts`, `frontend/src/insertTarget.ts`: pure target resolution plus the shared terminal/editor focus ledger used by Agent, note, Scratchpad, Markdown, and Queue sinks.
- `frontend/src/conversationDraft.ts` — the utterance-log draft model behind undo and editing.
- `frontend/src/conversation.ts` — `PersistentVoiceCapture` and the `Mux` command matcher.
- `frontend/src/audioFrames.ts` — streaming resampler and 512-sample framing.
- `frontend/src/speechGate.ts` — the frame-counted endpointing state machine and both gate
  configurations.
- `frontend/src/sileroVad.ts`, `frontend/src/voiceCaptureWorklet.ts` — the ONNX detector and the
  audio-thread capture worklet.
- `frontend/src/voiceLatency.ts`, `frontend/src/VoiceLatencyReport.tsx` — the stage sample and its
  readout.
- `frontend/src/wakeWordTest.ts`, `frontend/src/WakeWordTester.tsx` — trial scoring and the
  Settings surface.
- `frontend/test/renderer/voice-capture.spec.ts` — pins the two failures that are silent in
  production: the ONNX runtime not loading, and the capture worklet not being rendered by the
  audio graph.
- `frontend/src/mobileVoice.ts`, `frontend/src/Settings.tsx` — mobile setup + configuration UI.
