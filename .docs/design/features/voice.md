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

### One policy, three layers

Read aloud is decided in exactly three places, and they are read in order. They used to sit in
three unrelated surfaces - a checkbox in Settings, a chip on a pane, a button on a floating
strip - so "why is it talking" and "why is it silent" both needed all three answered, which is
the overwhelm this ordering exists to end.

1. **Master** (`tts_enabled`, Settings -> Voice). Off means *no session generates and nothing
   plays*, on any device. It is checked on the automatic path (`_consider`), on manual
   generation (`generate`), and on trusted application speech (`speak`) - a switch that only
   governs the paths that happen to consult it is not a master, which is what it was before
   Phase 15.
2. **Per-session participation** (`voice_mode`, the voice panel's `tts` tab). Answers *does
   this session generate*, and nothing else. `auto` no longer implies "and plays here": what is
   spoken is layer 3's question, so a session can be a full participant while being silent on
   the device you happen to be looking at.
3. **This device** (the browser's autoplay toggle, plus the focus rule below). Answers *does
   this browser speak, and for which session*.

The three are presented as one numbered block in Settings -> Voice -> Read aloud (TTS), each layer naming where its
per-item control lives. The block is the source of truth for the wording; the `tts` tab and the
pane strip say the same thing in one line each.

**Settings owns the switches; the `tts` tab operates them.** Layer 1 is edited in Settings ->
Voice and, in particular, can only be turned *off* there - the `tts` tab renders the standard
gate while it is off and a link back to the owner while it is on, because a grant may only ever
turn something on (`setting-links.md`). Layers 2 and 3 are per-session and per-device rather than
install-wide, so the tab edits them directly for the focused session.

### Generation model

- Per-session generation mode is volatile live-session state: `off`, `on_demand`, or `auto`.
  `null` inherits the configured global default (`tts_default_mode`) while the master
  (`tts_enabled`) is on. The mode is set through ordinary `PATCH /sessions/{id}` and
  dies with the session. It governs *generation*: `auto` generates on every completed reply,
  `on_demand` only when asked, `off` never. An explicit human request (the strip's `↻ speak`,
  `Speak last reply now`) is deliberately still honoured for an `off` session, because that is
  a direct instruction rather than participation - and because the shipped default for
  `tts_default_mode` is `off`, refusing there would have silenced the speak button install-wide.
- `auto` subscribes to `turn_ended`, debounces one second per session (`DEBOUNCE_SECONDS`),
  and reads `transcript_view.final_exchange`: the agent's newest assistant segment plus the
  message it was answering. That is the same reduction the drawer's Transcript tab renders and
  the rail's Copy reply copies, so what is spoken is what is on screen, cut at the same tool
  boundary. A synthetic provider acknowledgement is classified out before it can become the
  "latest reply". Verbatim speaks the segment; a summary is given the prompt alongside it, since
  a spoken update that opens by restating the wrong question is worse than a long one.
  Every automatic, manual, and trusted application response uses the same segmented stream.
  `streaming_segments` keeps any ordinary reply of at most 420 characters in one coherent clip.
  Longer replies emit one complete opening sentence first whenever it fits the 420-character clip bound, then continue in clips of at most 420 characters.
  Only a single sentence longer than that bound falls back to a word boundary.
  This keeps streaming from cutting a normal Voice Comms answer in the middle of a thought, which otherwise makes the continuation sound like a second generated answer.
  The first clip is emitted and returned before tracked background work synthesizes the remaining clips, so playback begins while the rest is still encoding.
  A per-session lock drops overlapping `auto` preparation requests, and two engine slots bound synthesis concurrency (`_engine_semaphore`).
- **Application speech opens on a much tighter clip** (`APPLICATION_FIRST_SEGMENT_CHARS`, the
  `first_max_chars` argument to `streaming_segments`), because the opening clip *is*
  time-to-first-sound. Synthesis time tracks characters: a 420-character opener measures 11-14 s
  of silence on Kokoro before a word is heard, against about a second for a short one. Only the
  first cut moves; the tail keeps the wide bound. Agent read-aloud deliberately keeps the wide
  opener too - the coherence of somebody else's prose matters more there than the first second,
  which is the trade this split exists to make separately for each.
- Content is `summary` (spoken-word summary via OpenRouter, strict `{speech}` JSON schema,
  three-to-eight plain-English sentences) or `verbatim` (assistant text with markdown, code
  fences, links, and tables reduced to listenable prose by `speechify`, bounded by
  `tts_verbatim_max_chars`). The global `tts_content` is the default; each session can
  override it volatilely (`voice_content` via `PATCH /sessions/{id}`, toggled from the player
  strip), and verbatim never touches an LLM. Summary calls check the daily budget
  (`tts_daily_budget`, the shared `{tokens?, usd?, mode}` spending shape from
  `design/features/budgets.md`) before spending and need a model. `tts_summary_model` is an
  **override**, not a pin: blank means the routed cheap model, so it never has to be set.
  It is edited in Settings → Voice → Spoken summary and indexed from
  Settings → Accounts → Models.
- Engines: `sapi` (the OS voice — offline Windows `System.Speech` through a generated
  PowerShell script; the default, because it speaks with no download and no network call) and
  `kokoro` (Kokoro-82M int8 through a **direct onnxruntime session** — no wrapper library,
  because every published Kokoro wrapper pulls a GPL espeak-ng payload). Both write WAV.
  The network `edge` engine is removed (Phase 10.5): it redistributed LGPL code and made
  unauthorized calls to a Microsoft endpoint; a config carrying `tts_engine = "edge"`
  migrates to `sapi`.
- **Kokoro's model is downloaded, never bundled** (`voice_models.py`): a pinned immutable
  Hugging Face revision, per-file SHA-256 verified while streaming, with explicit
  `not_downloaded → downloading → ready → error` state — a partial download can never be
  loaded. Settings → Voice → Voice and engine owns the download with visible progress
  (`voice_model_progress` events); `kokoro` is selectable but reports itself unavailable
  until the model is `ready`. English voices only, because the phonemizer is English-only.
- **Phonemization is lexicon-only misaki with `fallback=None`, and no espeak-ng package may
  enter the closure** (`kokoro_tts.py`). The engine refuses to construct if an espeak wrapper
  is importable. Out-of-vocabulary words go through a repair ladder — project lexicon
  respelling, compound splitter (camelCase, digits, hyphens, underscores), then spelling the
  word out letter by letter — and every replacement is re-verified recursively, so no token is
  ever silently dropped from speech. GPU execution providers (CUDA/DirectML) are used when
  onnxruntime reports them; CPU int8 measures RTF ~0.55-0.7.
  Engine problems surface as typed unavailable/error status; terminals are unaffected.
- **The ladder's lexicon rung is user-extensible, and its spelling floor is telemetered**
  (the fix for glued or invented names like `vaultspaces` being spelled letter by letter).
  `tts_lexicon` (word → respelling) merges over the built-in `PROJECT_LEXICON` with
  casefolded whole-word keys; a change hot-applies through `VoiceService.apply_lexicon`,
  which rebuilds the engine's merged map, drops the per-word resolution cache and the
  per-voice audition previews (both would otherwise serve pre-change speech until a daemon
  restart), and clears telemetry entries the new lexicon covers.
  Every synthesis whose final resolution involved the spelling floor reports the *top-level*
  word — the token an operator would actually respell — into `SpelledWordLog`, a bounded
  (200), deduplicated, counted JSON store at `<data_dir>/voice/spelled_words.json` that
  survives restarts, plus a `daemon.log` line. `GET /api/voice` surfaces the entries as
  `spelled_words`; Settings → Voice → Pronunciation lists them under the lexicon editor with a one-tap
  respell input that writes the lexicon entry. Telemetry is fail-safe: a reporter error is
  logged and speech proceeds.
- **A respelling must itself be pronounceable, and the editor tells the user before Save.**
  The ladder re-verifies every replacement, so a value made of invented words (the measured
  live failure: `swe → "swee"` — "swee" is not in misaki's dictionary) is silently rejected
  and the word spelled anyway. Two mechanisms close that loop:
  `POST /api/voice/lexicon/check` runs each draft entry through the *real* resolution
  machinery (`KokoroEngine.check_respelling`, advisory — model absence is a reported
  condition, telemetry untouched) and the editor shows ✓/✗ per row naming the unpronounceable
  pieces; `GET /api/voice/lexicon/preview?text=` auditions a value through the full pipeline
  with the configured voice (same-origin GET for the no-`media-src` CSP; spell-out reporting
  suppressed via `synthesize_wav(report_unknown=False)` so auditions never pollute telemetry).
- **Exact pronunciations use misaki's phoneme-link form `[word](/phonemes/)`** (e.g.
  `[swe](/swˈi/)` says "swee"). A link is atomic in the ladder (`replacement_pieces`):
  it is verified whole against the G2P and never whitespace-split (multi-word phonemes
  contain spaces) or repaired from the inside. And **trailing punctuation cannot defeat the
  lexicon**: `_WORD` absorbs `'_.-` tails, so a sentence-final `vaultspaces.` resolves its
  core against the `vaultspaces` entry and keeps the tail for prosody; the floor reports
  the core, not the punctuated token.
- **Nobody types phonemes by hand: the ✨ builder derives them from a phonetic spelling**
  (`phonics.py` + `KokoroEngine.build_respelling`, `POST /api/voice/lexicon/build`). The
  user spells the sound with plain letters ("swee", "kroh no tron" — or nothing, in which
  case the word itself is read as its own phonetic spelling) and deterministic English
  phonics rules — longest-match grapheme teams, silent-final-e lengthening, doubled-consonant
  collapse, first-vowel stress — emit misaki phonemes from a fixed alphabet verified against
  the pinned Kokoro tokenizer vocabulary. Pieces the G2P already knows pass through as text;
  only unknown pieces become links, and the result is re-checked with the real machinery
  before it is offered. Deliberately rule-based, not a trained model: no new dependency
  (espeak-ng stays banned), offline, and the same input always builds the same phonemes —
  the user tunes by ear with the ♪ audition, so predictability beats cleverness. Unmappable
  input (digits, apostrophes) is a reported verdict, never a guess.

### Storage and playback

- Clips are app-owned files under `<data_dir>/voice/` plus one `voice_clips` SQLite row
  (spoken text, engine/voice, trigger, tokens/cost for summaries, status, error, the stream
  identity below, and the message anchor below). The store
  confines every `sqlite3` call to one dedicated worker thread (WAL, `synchronous=NORMAL`),
  mirroring `HistoryIndex`, so nothing blocks the event loop. Public snapshots
  (`clip_snapshot`, `group_snapshot`) never expose daemon file paths. A byte-cap prune
  (`tts_cache_mb`) deletes the oldest streams whole; stale failed rows expire after a day.
- **A clip is a reply; a row is a segment** (`stream_id`, `segment_index`, `segment_count`;
  schema version 3).
  Segmenting is a latency device and nothing else, so it stops at the store boundary: every
  read a person sees is a *stream*, assembled by `VoiceStore.clip_groups` and rendered by
  `group_snapshot` as one clip whose text is its segments' text in spoken order, whose
  duration and bytes and tokens are their sums, and whose single `status` is their verdict.
  `segment_count` is carried by the opening segment alone and is NULL until the producer
  knows the total, which is what makes a live reply read as one clip being appended to
  rather than a row appearing per sentence.
  Grouping happens in SQL over stream keys rather than over a row window, because a `LIMIT`
  on rows cuts a stream in half at the edge of the window and presents its tail as a clip
  whose opening sentence is missing.
  Eviction and deletion take whole streams for the same reason.
  Ungroupable pre-schema-3 rows are **discarded** by the migration, audio included: they
  record no stream and no index, so their segments cannot be reassembled, and keeping them
  would list one reply as several clips in reverse spoken order permanently. Clips are a
  regenerable cache under a byte cap, so starting clean costs a re-synthesis and nothing else.
- **A completed stream is joined into one file** (`_join_stream`, `voice_audio.join_wav_files`).
  The joined audio is stored as a **new** clip id, inheriting the opening segment's identity
  and `created_at` (so a reply does not re-sort to the top of the list the moment it finishes
  being spoken), and the segments are marked `superseded_at` rather than deleted: they leave
  every listing at once, and their audio stays servable for `SUPERSEDED_CLIP_TTL_SECONDS`
  because a browser that queued those ids before the join is still going to ask for them, and
  answering 404 there cuts a reply off mid-sentence. Nothing is superseded until the joined
  clip is stored, so there is no instant in which a reply has no live row.
  The join declines - silently and without consequence - for a single-segment stream, an
  incomplete or failed one (the failure is part of what that clip says, and a joined file
  would present a truncated reply as a complete one), a missing segment file, or segments
  whose audio profiles disagree. Declining keeps the segments, which play in order anyway.
  `voice_clip_joined` tells clients to re-read; it never plays anything.
- **A clip is a rendering of one assistant message, and it records which one**
  (`source_ts`, `message_anchor`, both captured at generation time).
  `content_mode` is the clip's *kind* - `summary` or `verbatim` - and is part of its identity
  for the same reason, since the two are both legitimate audio for the same reply.
  Three things depend on the anchor and on nothing else.
  A **global clip list ordered by when each reply arrived** rather than by when its audio
  finished: a held backlog is synthesized in whatever order engine slots and summary calls free
  up, so synthesis order puts an hour-old update above the reply that just landed.
  A **per-message play button in the transcript** that finds existing audio instead of paying
  for it twice (`VoiceStore.anchored_group`, keyed on run + anchor + kind).
  It answers with a *complete stream*: answering with the newest ready row returned a
  segmented reply's last segment, so replaying a message spoke only its ending, and an
  incomplete stream is not offered at all because the reuse path never synthesizes the rest.
  And a **join point**: one clip model, with the pane strip (local), the `tts` tab (global), and
  the transcript's markers as three views over the same rows, none of which owns a clip.
  `source_ts` is nullable and backfills to NULL rather than to `created_at` - a clip made before
  the column existed has no source message, and inventing one would assert exactly the ordering
  the column exists to fix. Clips with no source message (application speech) fall back to
  `created_at`, which is also the tie-break inside one reply, where every segment of a stream
  shares one anchor and one source time and must stay in the order it will be spoken.
- **`status` is the daemon's synthesis lifecycle and nothing else**: `synthesizing` (written
  before the engine runs, so a clip is visible while it is being made rather than appearing only
  once it can play), then `ready` or `failed`. Every path out of synthesis writes its own
  verdict; a row still claiming `synthesizing` at connect belongs to a run that died, so
  `VoiceStore._migrate` retires it - synthesis cannot outlive the daemon that started it.
  `held`, `played` and `dismissed` are deliberately **not** stored: they are per-device facts (a
  clip played on the phone is unplayed on the desktop), so `voice.ts` keeps them in memory and
  the `tts` tab renders them over the daemon's row.
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
- **Within a device, playback is focus-driven and global: the focused session speaks, every
  other session holds its clip.** Three panes on `auto` used to talk over each other and over
  whatever the operator was actually reading. The app reports the focused agent session
  (`setPlaybackFocus`, driven by the same `focusedAgentSession` every other pane-scoped surface
  uses, so a note or a shell in focus means *no* session plays); `enqueueAutoplay` plays a clip
  whose session matches and otherwise **holds** it. The clip is already durable in `voice_clips`
  and on disk, so holding costs nothing and loses nothing - what is held is only the decision to
  speak it.
- **A held clip is surfaced as ready-to-play, never played retroactively.** Moving focus onto a
  pane does not start its backlog: arriving somewhere is not a request to be talked at. The
  `tts` tab grows a `▶ n held` button covering every session's backlog (click plays them oldest
  first, behind whatever is currently speaking rather than cutting it), each clip's own row
  offers to dismiss it, and the command palette carries `Read aloud: play clips held while you
  were elsewhere`. The backlog is bounded at `HELD_PER_SESSION` (5, newest kept) and is dropped
  by every switch that means "stop": `stopSessionPlayback` (the pane went off), `stopAllPlayback`
  (the master or the device toggle), and a muted device holds nothing in the first place - an
  "off" that leaves a play-me button behind is not off. The palette entry deliberately carries no
  count, because rendering one would subscribe `App` to every `timeupdate` the audio element
  fires.
- **Voice Comms pins its agent past the focus rule** (`setPinnedPlaybackSession`). Hands-free
  conversation is the one mode where focus is the wrong question - the operator is talking to
  that agent, so its replies are the point of the mode - and the pin is released when comms is
  turned off, alongside the autoplay and session-mode restore.
- Every segmented response shares a stream ID across its clips.
  A browser claims manual and application streams before making the request, so the first live readiness event can start playback without waiting for the HTTP response.
  The response remains a fallback if that event was delayed or lost.
  **Every path that asks for a reply claims its stream**, including the ones that used to
  pass no `stream_id` at all (the pane's palette entry, the voice `read reply` query, the
  transcript's per-message button): without a claim the daemon's later segments arrive on a
  stream the tab never said it wanted and are dropped, so a long reply spoke its opening
  sentence and stopped.
- **The client's unit of playback is the reply** (`playClipGroup`, `voiceGroups.ts`).
  A clip's parts are laid end to end into one timeline, so the transport reports the reply's
  duration and position rather than the current segment's, and a scrub crosses a segment
  boundary by playing the segment that covers the target from an offset and re-queueing the
  ones after it. Playing a reply takes the floor and clears the queue - the abandoned clip's
  element is re-pointed, so nothing will ever fire `ended` for its queued siblings - but it
  is a no-op when that reply is already in flight, which is what keeps the HTTP-response
  fallback from restarting a reply the live events already started.
  A reply still open claims its stream when played, so segments not yet synthesized join the
  end of it. The parts of one reply are also **held as one entry** (`HeldClip.partIds`): held
  per segment, a three-sentence answer reported "3 clips waiting" and consumed three of the
  five slots a session keeps. `played` is recorded against the stream as well as the parts,
  because the join replaces the segment ids and a reply the operator heard must not read as
  unplayed the moment that happens.
- **An application-speech stream can stay open and be appended to** (`SpeechStream`,
  `VoiceService.speak(continue_stream=…, final=…)`, `close_speech_stream`).
  The Mux assistant produces its reply over several seconds, so the stream's length is unknown
  while it runs: open segments carry `segment_count: 0` (unknown) in their *events* until the
  closing one carries the real total, and `voice_stream_closed` marks the end for the case
  where a stream finishes with no final clip.
  The clip *record* states the same fact as NULL rather than 0, and closing the stream writes
  the number of segments actually emitted - including a failing one, which is a row and
  carries the error - so a clip settles instead of waiting forever for segments nobody is
  making.
  Ordering is the invariant the type exists to hold - exactly one worker task drains one FIFO
  per stream, so clip indices are monotonic however the appends arrive. Two appends synthesizing
  concurrently would emit out of order whenever the shorter finished first, and the browser plays
  clips in arrival order, so the reply's second sentence would speak before its first.
  A segment that fails to synthesize ends its stream rather than skipping the gap, because
  speaking sentence three after sentence one failed reads the reply out of order.
  Client side, a non-positive `segment_count` means "still open" and a segment is queued whenever
  a clip is loaded and unfinished - not merely while audio is audible, since between assigning
  `src` and the `play` event the element is occupied while reporting otherwise, and a stream's
  segments arrive close enough together to hit that window.
- **Barge-in and every stop switch silence claimed streams, not just audible ones.**
  A claim outlives its clip: synthesis runs behind the request, so a stream with nothing playing yet is still going to speak.
  Suppressing only the audible stream let a backlog keep talking for minutes after the operator had released the microphone, with no gesture left that could stop it (2026-08-20).
  `bargeInPlayback` and `stopAllPlayback` therefore suppress every entry in the claim map, and an in-flight `speak` response cannot re-claim a suppressed stream through its playback fallback.
  Autoplayed agent read-aloud is untouched by barge-in - that is a separate switch, turned off by a pane's chip or the device toggle.
  `claimRequestedStream` is the non-interrupting counterpart to `beginRequestedStream`, for app-initiated speech that is *additional* rather than superseding.
- Barge-in is a hard stream stop.
  The first credible speech frame sidechain-mutes the singleton audio element instead of requiring the user's voice to overpower the phone speaker.
  Capture ignores three 32 ms frames while speaker echo drains, then requires three consecutive speech frames against the quiet microphone.
  Confirmation stops and abandons the current clip, clears the queue, and suppresses later clips from the same stream.
  The clean confirmation frames become the start of the new utterance and playback-contaminated pre-roll is discarded.
  If speech disappears when playback is muted, the sound was echo and playback is restored.
  Push-to-talk is already an explicit gesture, so it stops playback immediately without waiting for frame confirmation.
  Any confirmed speech stops playback before transcription finishes, even when the utterance is not a command.
  Bare `Mux, stop` maps to the playback-stop action and keeps Talk listening, while the explicit `stop listening` action releases capture.
  Playback controls retain their normal meaning when the utterance began over audio, so `stop listening` and `interrupt agent` cannot be discarded as playback echo after they already silenced it.
- **Turning read aloud off is immediate, at all three scopes.** The singleton element is
  shared, so clips are tagged with the session that owns them and each "off" switch stops
  exactly what it turns off: the session's mode going to `off` (from the `tts` tab, the session
  menu, or the palette) calls `stopSessionPlayback`
  (that session's clip is halted and its queued clips dropped; another pane's audio keeps
  playing, and a clip already queued for a still-enabled pane is promoted rather than
  stranded), while the device autoplay toggle and the global Settings switch call
  `stopAllPlayback`. All of them fire on the click, not when the PATCH lands or when the
  current clip finishes. A hard stop abandons the clip (unlike `pausePlayback`, which keeps it
  loaded to resume), so the strip reads as stopped and a later play restarts from zero.
  Each of those stops takes the held backlog with it for the same reason.
- The autoplay path re-checks the pane's participation on arrival as well as on the daemon,
  because a clip synthesized just before the user hit `off` would otherwise land and start
  speaking after the switch was thrown. Whether that clip then *plays* or is *held* is
  `enqueueAutoplay`'s decision alone, so the device toggle and the focus rule are not
  half-applied at the event handler.
- **Focus that has never been reported is not the same as no session focused.** `voice.ts`
  tracks the two separately: before the first `setPlaybackFocus` the pre-policy behaviour
  stands and a clip plays, so a client that reports focus a tick late (or a surface that never
  reports it) cannot swallow audio it would have spoken before this rule existed. A clip with
  no session attributed to it plays for the same reason - it cannot be held against a session
  nobody named.

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
- **A frame watchdog separates a dead capture from a quiet room** (`CaptureFrameWatchdog`).
  The audio graph pulls continuously, so silence still delivers blocks of zeros at a steady
  rate; no blocks at all means the capture path itself is dead — a suspended `AudioContext`,
  a released track, a killed graph.
  Without the watchdog the two rendered identically as `listening`, and the observed outage
  (2026-08-20) left a phone claiming to listen for minutes while the daemon's access log
  showed zero `transcribe` posts.
  The watchdog is symptom-level by design: `AudioContext` suspension under memory pressure is
  a hypothesis for the cause, so recovery does not depend on it being right.
  Every 2 s poll with no frame for 5 s reports a stall exactly once, attempts
  `context.resume()` on every poll, and a `statechange` away from `running` triggers an
  immediate resume attempt as well.
  The UI renders the `stalled` phase (never `listening`) with a detail that says whether the
  track was released (only a Talk restart can reacquire it) or merely silent, and each
  stall/recovery posts a bounded diagnostic to `POST /api/voice/capture-diagnostic` so the
  outage is in `daemon.log` at the moment it happens rather than reconstructed from the
  access log afterwards.
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
  detector kept as a fallback.
  The build emits the runtime (~11 MB) and model (~2.3 MB) as separate assets that the browser fetches lazily on the first Talk start; the runtime is pinned to one thread
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
  shorter one would cut words in half.
  Its ordinary threshold remains `max(0.012, noiseFloor*3.2)`.
  The fallback updates its noise floor only while playback is absent, because learning speaker output as room noise can raise the threshold beyond a real voice.
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
- **Chat patience** (`voice_chat_patience_ms`, default 1200, hot, surfaced as
  `chat_patience_ms` on `GET /api/voice`): while the assistant is the microphone's addressee
  (chat mode or the follow-up window), the *silence endpoint* waits
  max(detector tail, patience) before a plain utterance becomes an assistant turn — thinking
  out loud is not answered at every breath. The gate consults the value per frame
  (`SpeechGate`'s `extraTail` provider), so an addressee change applies immediately, and only
  the compared threshold moves — never the accumulated counters. Commands stay fast
  structurally: speculation still fires at 160 ms and a wake-worded command short-circuits
  the longer tail (energy-detector users, who have no speculation, wait the patience out).
  **An open assistant confirmation card suspends the patience entirely**, and lets a recognized
  `confirm`/`cancel` commit a speculative decode the way a wake-worded command does: the
  assistant asked a closed question, so the operator is answering rather than composing, and a
  one-word reply held for another 1.2 s is the difference between the confirmation feeling
  instant and feeling stuck. The real decode stays on the `dictation` profile — an answer that
  turns out to be conversational still has to be transcribed accurately, and the speculation
  already carries the latency win.
- **Unfinished-utterance deferral** (`utteranceCompleteness.ts`, dispatched from
  `ConversationControl.tsx`): the defining voice-agent complaint is that a pause becomes a reply,
  so the operator rushes to beat the endpoint.
  A **completeness heuristic runs before a chat turn is dispatched**, and an utterance that ends
  mid-clause - on a dangling conjunction, preposition, or article - earns exactly **one** adaptive
  patience extension instead of submitting.
  The design is deterministic-first and pre-model for two reasons: a model-arbitrated "are you
  done?" loop is the round-trip spam the feature exists to remove, and a model instructed to
  sometimes return nothing will return nothing when it should have answered.
  **The model is never told to withhold a reply**; it is taught one thing instead - to offer the
  brainstorm hold once when a turn reads as an unfinished thought (`assistant.md`).
  - **One deferral per utterance, structurally.** The decisions live in `DeferralPen`
    (`utteranceDeferral.ts`) and the effects stay in `ConversationControl.tsx`, so the invariant
    is tested rather than asserted. The held fragment resolves exactly once:
    merged into the next plain chat utterance, submitted alone when the extension expires, folded
    into the brainstorm buffer if the operator says "hold on", or dropped by cancel, standby, or
    Talk stopping. The merge path deliberately does **not** re-run the heuristic, so a chain of
    fragments cannot compound into an unbounded wait.
  - **The extension is the operator's own `voice_chat_patience_ms`, not a second knob** (floored
    at 600 ms, capped at 5 s): an unfinished thought waits its normal patience at the gate and one
    more patience-length window at the dispatch layer, so "how long before Mux answers" stays one
    number to turn. While the fragment is held, the gate's `endpointPatienceMs` returns
    patience + extension (capped at 10 s), so the second breath is not itself chopped in half.
  - **The release timer re-arms while speech is still arriving or an utterance is mid-decode**,
    because answering half a sentence whose other half is already in flight is the exact failure
    being fixed. It is bounded by a hard 15 s hold ceiling, so a detector wedged in "speaking"
    cannot hold a turn forever.
  - **Queue-merge stays the safety net** for fragments the rule set does not recognize: the
    daemon coalesces consecutive arrivals into one waiting turn, and barge-in already silences a
    reply to fragment one (`assistant.md`).
  - **Two guards keep the false-positive rate low without a parser.** Questions strand
    prepositions legitimately ("what is this for"), so a trailing `?` or an interrogative opener
    disqualifies the preposition rule - never the article or conjunction rules, because nothing
    ends on "the". And prepositions that double as verb particles ("I'm in", "come on") count
    only in an utterance of at least five words, where they read as a clause rather than an idiom.
    Words that are commonly sentence-final are absent from the lists on purpose ("yet", "though",
    bare "then", "that", "some"), and the two idioms that survive are exempted on the *preceding*
    token ("I think so", "it's been a while") rather than by loosening the word.
  - **Every deferral is logged with its trigger token**, on resolution rather than at the
    deferral, because the outcome is what judges it: `merged` caught a real trail-off while
    `submitted` cost the operator one extension for nothing, and the ratio of the two is the
    false-positive rate. Tune the lists from `POST /api/voice/deferral-diagnostic` records in
    `daemon.log`, not from intuition.
  - The chat panel shows the held fragment as an `unfinished · "and"` chip beside the phase, so a
    turn that is waiting rather than ignored is legible at a glance.
- **Push-to-talk** (hold `Ctrl`+`Alt`+`Space`) suspends endpointing entirely: the key release is
  the endpoint. It is the escape hatch for when detection is the problem rather than the fix — a
  noisy room, a deliberate mid-sentence pause. Captured on the window rather than through the
  command registry, which fires on press and cannot express a hold; window blur ends it, so a key
  released over another window cannot latch the microphone open.
- **Playback keeps the microphone open with confirmed-speech barge-in.**
  A possible voice clears the normal 0.5 Silero threshold and a low RMS floor, then immediately ducks app audio.
  Capture records the playback origin for diagnostics, waits three frames for echo to drain, and requires three consecutive accepted frames on the quiet microphone before treating the sound as human speech.
  Confirmation stops and suppresses the complete stream, trims contaminated pre-roll, and continues through the ordinary dictation and deterministic wake-word rules.
  Rejected echo restores playback without producing an utterance.
  Completed probes post bounded confirmed/rejected diagnostics to the daemon log, including detector, playback origin, peak probability, and peak RMS.
  Before confirmation, speculative decoding stays disabled.
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
- **`stt_enabled` defaults off** so a fresh install never downloads the several-hundred-MB Whisper
  model and the browser voice-activity runtime on the first Talk without warning. Enabling
  microphone input in Settings -> Voice is the explicit opt-in, and the Voice tab states that the
  first capture downloads a speech model. Existing configs keep their stored value. `tts_edge_voice`
  defaults to the neutral `en-US-JennyNeural` rather than a locale-specific voice; TTS is off by
  default, so this only takes effect once a user turns read-aloud on.
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
- The Settings → Voice → Testing and latency readout reports per-stage p50/p95/max plus a **separate command-only
  total**, since the exit criterion is stated for a short command and dictation decodes several
  times longer audio. Percentiles rather than a mean: one cold model load is a seven-second
  outlier. Samples are also written to `daemon.log`, which is what outlives a restart, and each
  carries the `X-Mux-Utterance-Id` that joins it to the daemon's own decode line.
- The dictation panel shows the last utterance's total, with the breakdown on hover.

### Command grammar and submission

- **Routing is three tiers.** Tier 1 is this deterministic grammar. Tier 2 is a conservative
  token-level fuzzy pass over the same compiled phrases (`voiceFuzzy.ts`: positional token
  alignment, an 0.78 similarity threshold, an ambiguity margin, and no `{text}` slot phrases —
  a fuzzy-captured slot would turn a misheard word into a mis-*targeted* action), which
  absorbs STT noise before an utterance costs a model call. Tier 3 is the Mux assistant
  (`assistant.md`): a wake-word utterance neither tier matched becomes a conversation turn
  instead of a spoken refusal, when the assistant is enabled. The reflex path never waits on
  a model.
- Commands are recognized only as an utterance **suffix**: a **wake word** followed by a
  known **command phrase** at the very end. Everything before it is buffered draft text, and
  commands accumulate across pauses. Both the wake words and the phrase→action mapping are
  **user-configurable** (daemon config `voice_wake_words` / `voice_commands`, edited in
  Settings → Voice, surfaced to the client via `/api/voice`). `buildVoiceMatcher`
  (`conversation.ts`) compiles them into one regex: wake-word alternation + phrase alternation
  matched longest-first, so `read the reply again` wins over `read`, and a bare wake word or an
  unmatched tail leaves the text as draft. `parseMuxVoice` is the default matcher (built from
  the `DEFAULT_WAKE_WORDS` / `DEFAULT_COMMANDS` fallbacks that mirror `config.py`).
- **The wake word is chosen from measurement, not from how it looks.** Settings → Voice → Testing
  and latency carries a
  tester: speak N utterances, and each one goes through the same capture pipeline, the same
  transcribe endpoint on the same routing decoder the command path uses, and the matcher compiled
  from the live configuration. It reports the raw transcript per trial, which wake-word spelling
  (if any) was heard as a whole word, and which action fired. That split is the point: "the
  trigger came back as *bucks*" and "the trigger was heard but the phrase after it was not" are
  different problems with different fixes, and neither is visible from the configuration form. A
  transcript with no speech recognized is recorded as a trial rather than discarded, since it is
  the strongest evidence against a trigger word. Good wake words are two to three syllables,
  phonetically distinctive, rare in ordinary speech, and not a prefix of a common word.
  Spoken tester trials retained `mux`; `mucks` and `max` remain recognition variants for that same wake word.
- The **capture-control action set is fixed** (each is wired to code); only its trigger phrases change:
  `send`, `append`, `cancel`, `undo`, `mute`, `read`, `summary`, `verbatim`, `interrupt`, `help`, `standby`, `resume`, `hold`, `proceed`, `comms_on`, `comms_off`, `stop`.
  Defaults ship `mux`/`mucks`/`max` as wake words with phrases matching the historical grammar.
  Schema 20 adds only the three new action definitions to an older saved command list and preserves every existing custom phrase or disabled action.
  Schema 21 adds bare `stop` only to the untouched stock `mute` phrase list; customized or disabled mute mappings remain unchanged.
  Schema 28 adds `hold`/`proceed` (the chat brainstorm pair, below) the same way.
- **Workspace commands use the existing command registry.**
  `voiceIntents.ts` strips leading filler, normalizes number words, resolves exact declared aliases and `{text}` slots, and returns `{match, candidates, confidence}`.
  When two slot templates match, the one with more fixed words wins, so `new Codex in Project 2 {text}` cannot be swallowed by the selected-Project `new Codex {text}` shorthand.
  The registry's low-priority catch-all delegates only to the closed grammar in `voiceQueries.ts`; literal command aliases and literal slot templates always outrank it.
  `App.tsx` generates focus commands for every live session and Project, drawer commands from `DRAWER_TABS`, idempotent open/close commands for the navigation sidebar and side panel, and direct spawn commands for each Project/backend pair.
  Navigation sidebar commands target the mobile overlay or desktop collapsed state according to the current responsive presentation.
  Side-panel commands use `open` and `close` rather than a spoken toggle, so a repeated recognition cannot reverse the requested state.
  Session launch accepts the Project name, the stable visible `Project N` address, or no Project qualifier for the selected Project, and the ordinary spawn path focuses the optimistic new tab immediately.
  The bridge selects a numbered ambiguity candidate or calls `runCommand(id)`; it never owns a second action table.
  A focus command changes the Phase 3 sink immediately, so later dictation follows the navigated session or Project.
- **Clearing the assistant's context is one of those registry aliases.**
  `assistant.newConversation` ("Mux, new conversation", "Mux, clear context") calls the same new-dialog path the panel's `new` button uses, deterministically and with no model call.
  Alone among assistant acts it carries no confirmation, because the prior dialog is unremembered rather than deleted and stays readable in the panel; the spoken reply says both halves, which is what makes the missing confirmation honest (`assistant.md`).
- **Safe Action rail items join that same registry only while a session is focused.**
  `railVoice.ts` resolves the focused Project's Action configuration for the current device and backend, deduplicates entries placed on the Rail or Drawer layout, and adapts only an explicit safe subset to registry commands.
  The shipped subset is terminal copy/paste plus non-destructive terminal keys: Escape, Enter, Tab, Ctrl+C, arrows, cursor navigation, restore input, newline, and the Markdown insertion helpers.
  Non-submitting configured agent `skill` and `slash` entries derive deterministic aliases from their command name and preserve the rail item's backend-specific payload, so `Mux, learn` inserts `$learn` in Codex and `/learn` in Claude when that item is configured.
  A configured entry that submits requires an explicit `voicePhrases` opt-in instead of becoming executable from its label alone.
  Literal text, prompt templates, composer-clearing keys, attachments, keyboard mode, relaunch, branch, clipboard-history UI, copy-input, reply-copy helpers, and end-session never cross this adapter.
  The rail's `^U` is the case that makes the exclusion an active rule rather than a side effect of its type: it is a raw key, so it would become voice-reachable the moment it carried a phrase, and it deliberately carries none - a spoken caller cannot see the draft they would be destroying.
  Restore input is voiced precisely because it is the recovering half of that pair.
  Execution goes back through the mounted `TerminalPane` action bus instead of writing to the PTY directly, and Talk waits for an acknowledgement before reporting success.
  Voice Paste reads clipboard text only and cannot take the visible Paste button's image-attachment branch.
  A missing pane, missing copy selection, or blocked browser clipboard is therefore reported as a failure instead of a false success.
- **Every discovery surface exposes one complete current command catalog.**
  `voiceCommandReference.ts` combines saved or draft capture-control phrases, fixed query grammar, and live registry aliases for current Projects, sessions, workspace panels, launch targets, status, approvals, and the focused session's safe rail actions.
  Settings → Voice, both `? Commands` dialogs, and spoken help consume that model instead of maintaining separate lists.
  The internal `{text}` query catch-all is omitted because the closed grammar is listed explicitly.
  Unavailable guarded commands remain visible with their current requirement, so discovery does not depend on first reaching the required state.
  A full spoken help request puts every phrase in Talk history and speaks the available groups and counts; a category request such as `voice commands for sessions` speaks that category's complete current entries.
- **Spoken lookup is a bounded dialog, not open-ended intent inference.**
  The closed grammar covers command help; Project lists; live, active, working, ready, pending, approval, question, rate-limit, stuck, and failed session filters; overall/current/named Project scopes; entity status; navigation; and last-reply reading.
  Natural read-only forms such as `active sessions`, `list approvals`, `do I have pending sessions in the current project`, and `list Project Alpha sessions` normalize into those same typed queries.
  An unmatched wake-word query speaks its refusal as well as displaying it, so failure cannot look like silence.
  `pending sessions` is an input alias for sessions needing a human answer or approval; spoken output uses `needing you` so it cannot be confused with pending Queue messages.
  Numbered navigation is always available and never depends on a prior spoken list.
  `Project N` follows rendered visible-sidebar order, including the active Project and Group sort.
  Bare `Session N` follows the selected Project's rendered session order: pane traversal first, then unattached sessions by creation order.
  `go to next session` and `go to previous session` move through that same selected-Project order from the focused session and stop at the first or last entry without wrapping.
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
- **Brainstorm hold** (`hold`/`proceed`, chat-addressee only) is standby's accumulating sibling: transcription continues and plain speech **buffers instead of becoming assistant turns**, until a `proceed` cue releases the whole buffer as **one** consolidated turn — the model sees the entire train of thought instead of answering fragment one before fragment two exists.
  Deterministic client state, never a model decision (the same rule as confirm/cancel): entry is the wake-worded `hold` action or a bare exact-match phrase ("hold on", "let me think" — `HOLD_ENTER_PHRASES`, matched only when the utterance *is* the phrase); release is `proceed` or a bare "go ahead"/"what do you think" (`HOLD_RELEASE_PHRASES`).
  While held, every other wake-worded command keeps its meaning ("Mux, stop" still works mid-brainstorm), `cancel` clears the buffer without leaving hold, and the chat panel header shows a `holding · Nw` chip with `go ahead`/`discard` buttons.
  The buffer is never lost to a failure: a failed or refused release (chat mode closed underneath) keeps the buffer and the hold.
  The wake-worded `hold` outside chat mode explains itself rather than engaging — in talk mode the dictation draft already waits for `send`.
- **Capture and target have separate lifetimes.** Talk is one workspace-level browser flag.
  The target follows the focused live Agent, Continuity editor, Scratchpad, Markdown editor, or Queue composer without restarting capture, and the editable draft survives every target change.
  A pin freezes the exact current sink until explicitly released.
- **A text target is a buffer sink, not an execution path.** Send and Append both insert the trimmed voice draft at that surface's caret and clear the voice draft.
  In a Queue composer this only fills the composer; staging, arming, and delivery remain separate explicit Queue actions.
  Agent-only commands (`read`, `summary`, `verbatim`, `interrupt`) refuse a text target and keep the draft.
- All utterances decode through the session-free `POST /api/voice/transcribe` route.
  The target is resolved only when an action needs it, so a focus change during capture cannot send audio to a stale per-session route.
- Agent Send and Append first call the side-effect-free `voice/prepare-submit` guard, which applies the existing live-Agent, bounded-text, and non-overridable approval/question safety checks.
  After that guard, they route through the mounted `TerminalPane` by a request/acknowledgement event.
  The pane appends with its existing bracketed-paste repair and normal xterm `onData` path, which preserves PTY ownership, replay buffering, broadcast policy, and the same carriage return used by the mobile Send control.
  Send waits 180 ms after bracketed paste before issuing that carriage return because interactive TUIs such as Codex commit pasted composer text on a later input/render turn.
  Send appends and then submits; Append performs the same insertion without the carriage return.
  The pane acknowledges Send only after the delayed carriage return has been emitted.
  The Talk draft clears only after that acknowledgement, and a missing or replaced pane leaves the draft intact.
  The older `POST voice/submit` route remains as a bounded compatibility API with idempotency and readiness checks, but the Talk client no longer uses it because a daemon write cannot append to an application composer already holding local text.
  `POST voice/interrupt` writes a lone `\x03` and requires a live Claude/Codex session.
- **Voice Comms is explicit, session-scoped conversational prompting.**
  The Talk toggle or `Mux, voice comms on` pins the focused Agent, sets that session to automatic verbatim read-aloud, enables device autoplay, and remembers the prior pin and read-aloud state for restoration.
  The first appended voice message for each agent run carries the short-response protocol immediately before a `[voice]`-prefixed message; later voice messages in that run carry only the prefix.
  The protocol requests one or two natural spoken sentences, the answer first, no markdown/list/code/path detail unless requested, and at most one clarification question.
  Comms playback remains verbatim and never invokes the summary model.
  A normal one-to-two-sentence Comms reply stays in one audio clip; only replies beyond the ordinary 420-character clip bound enter the segmented continuation path.
  `Mux, voice comms off` restores the prior session mode, content mode, device autoplay state, and target pin.
- Playback carries an explicit `agent` or `system` origin into capture diagnostics.
  A sidechain probe distinguishes likely speaker echo from speech after ducking the speaker, rather than demanding that speech beat a fixed playback RMS threshold.
  Confirmed user speech stops either origin immediately and continues as an ordinary utterance, so it may become dictation or a wake-word command under the normal deterministic safety boundary.

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

- **Read aloud is generated per session and operated from exactly one place: the voice dock's `tts` tab.**
  The pane carries nothing - no `tts:` chip, and no player strip.
  Both were per-session controls drawn once per *visible pane*, so a four-way split answered "what is this session doing with audio" four times, about four different sessions, none of them necessarily the one being listened to.
  The tab follows focus instead, and holds everything the strip had that nothing else did: the transport (play/pause, scrub, elapsed/total for whichever clip this device has loaded) and on-demand generation (`↻ speak`).
  `↻ speak` is gated on a focused agent and the master switch only, never on the session's own mode: the daemon's manual path checks `tts_enabled` and an observable transcript and nothing else, so a session set to `off` can still be asked to speak once - which the strip could not offer, being drawn only when the mode was already on.
  Removing the strip took nothing off the pane's geometry budget either way: it floated from a zero-height anchor, and the anchor is gone with it.
- **Which sessions participate is reported outside the tab**, by a mark on every sidebar row and workspace tab (the `voice` row field, `features/ui.md`).
  This is the half a focus-following control panel cannot answer: the tab edits one session's mode, the marks report the whole fleet's at a glance.
  The mark reads the resolved mode - stored `voice_mode`, else the global default, and `off` outright whenever the master switch is off - so it never claims a session speaks when the daemon would not generate for it.
- **There is one voice surface, and it is app-level: the voice dock** (`.voice-dock`, `voiceDock.ts`).
  It holds the dictation draft and the assistant conversation as two bodies behind the talk/chat tabs, and it is mounted **once**, at one fixed place in the tree, for the life of the app.
  It hangs from `.voice-dock-anchor`, a zero-height grid item in the main stage's own cell, so it floats over the top of the workspace and never takes a track: a pane's row count *is* its PTY's row count, and a surface that took a row would resize a live agent's terminal every time it opened.
  Its mount point is fixed rather than following the sink's pane, which it once did: a focus change then remounted the assistant view inside it - see the announced-card rule in `assistant.md` for why that is a correctness problem and not a cosmetic one.
  Capture, draft, target pin, and history stay mounted across Project, pane, and target changes, as they always did.
- **Three axes, deliberately separate: capture, body, and size.**
  - *Capture* is the microphone. Its primary control is the mic button in the dock's own header; the top-bar control reaches the same toggle behind ctrl+click or a long press.
  - *Body* is the talk/chat/tts tabs (`VoicePanelMode`): the dictation draft, the assistant conversation, or read aloud's operational panel.
    For the two conversational bodies it is also the addressee - who plain speech reaches.
    With capture off there is no draft to dictate into, so the dock shows the assistant whatever the stored value says; the stored value is left alone.
    `read` is unaffected by capture in either direction: it needs no microphone and says nothing about one.
  - *Size* is the dock state: `full`, `peek`, or `chip`. It is presentation only.
  They were one thing before, and the cost was concrete: the surface rendered only while capture ran and its close button appeared only while capture was stopped, so the sole way to clear the panel off the workspace mid-conversation was `stop mic`.
- **Exactly one body is ever the addressee: the dictation draft** (`voiceAddressee`).
  The draft has no surface but its own, so speech may only land there while it is the body on screen; every other body - the assistant, and the `tts` panel, which is a control surface with no conversation behind it - leaves the assistant as the addressee.
  That is the shipped chat rule generalized rather than a new one, and the dock's header states it (`mic→assistant`) rather than redirecting speech silently.
- **Collapsing is not closing.** At `chip` the dock is `display:none` and the workspace is completely clear, while the dialog keeps streaming, speaking, and opening cards from the same mounted component.
  Hiding rather than unmounting is load-bearing, not an optimisation: the set of cards this device has already announced lives in `AssistantPanel`, and a remount reads as a device that has never seen an open card and speaks its line again.
  The way back is the **one voice control** in both top bars, which carries a count while confirmation cards are open and a dot when a reply landed while collapsed.
  It reopens into the size it was collapsed from, so a deliberate `peek` does not come back as a full panel.
- **`peek` is one row: the newest line, plus every open confirmation card with its buttons and countdown, and no composer.**
  The cards are the reason peek exists rather than a straight open/closed toggle - they are the only part of a conversation that expires, and a scheduled one runs on its own.
  A card opening therefore raises the dock to at least `peek`, one-way and never further, because a countdown nobody can see is a decision made by timeout.
- **There is one voice control in the top bar, and the modifier separates its two jobs.**
  Plain click toggles the panel and never touches capture, because that is the common action.
  Ctrl/Cmd+click toggles capture, as does a **long press** - the same 550 ms hold the sidebar, the rail, and the Run button use, and the touch route to capture, since ctrl+click does not exist on a phone.
  A hold that fired swallows the click that follows it, so one gesture never does both.
  **The colour means exactly one thing: capture is live.**
  Opening or closing the panel never changes it; the open panel gets a deliberately colourless treatment and `aria-expanded`, while capture gets the green and `aria-pressed`.
  A control that lit up for two reasons could not answer the one question a microphone has to answer.
  This reverses the rule the dock shipped with - that the panel chip and the microphone are separate buttons with separate jobs - because in use two voice buttons on the app's tightest row read as one difference to be remembered rather than two clear controls.
  The separation survives as the modifier rather than as the button, and `frontend/test/voiceDock.test.ts` pins the new rule in place of the old one.
- **Mobile capture control: the in-modal mic is primary, the top-bar hold is the shortcut.**
  The panel's own microphone starts as well as stops (the header used to carry only a `stop mic`, so the panel could release the microphone but never take it), and it is what a touch user is expected to reach for; the long press exists so capture is one gesture away without opening anything.
- **The microphone may open the dictation draft, and only that.** Starting Talk from the collapsed state opens the dock when the addressee is dictation, because the draft has no other surface; it is a loan, returned when capture stops, and any dock action by the operator clears the loan for good.
  An assistant-addressed microphone leaves a collapsed dock collapsed, which is the whole point of collapsing it: the assistant speaks its replies.
  A spoken question routed to the assistant never reopens a collapsed dock either - the reply is spoken and the control marks it unread.
- **Talk keeps a reviewable conversation history.** Every recognized utterance and every final Mux outcome is stored in a device-local, app-wide 120-entry ring.
  Lists and help retain their line-broken display text while TTS receives the separately paced speech form.
  Last-reply requests retain the generated reply text, not only a playback status message.
  The panel opens with history visible, follows the newest entry, and provides an explicit clear action.
  The header shows only the `talk:<phase>` badge and last latency; transient detail remains available to assistive technology and in the phase tooltip instead of repeating history text.
- **The panel names its sink.** The `to:` row carries the Agent or text-surface label, its pin control, and an unavailable state.
  Send is disabled when the named target disappeared.
  Unpin resumes focus-following without changing the draft.
- **The dictation draft is always editable.** The live `<textarea>` has no edit mode.
  Capture keeps running while typing; an utterance that lands mid-edit appends at the end with the caret and selection preserved.
  One line grows to five, then scrolls internally.
- **Voice stays primary.** `Mux, send` and the panel's Send button commit the same draft to the named sink.
  For an Agent sink they append to the existing composer and submit through the same terminal path as the mobile Send control.
  `Mux, append` and the panel's Append button append without submitting; text targets always remain append-only.
  `Ctrl`/`Cmd`+`Enter` sends from the textarea; `Escape` releases its keyboard focus.
  faster-whisper returns whole utterances rather than partial words, so the panel signals
  arrival with a brief border flash instead of animating a stream it does not receive.
- The voice dock exposes a `? Commands` action backed by the complete live catalog shown in Settings, plus a gear into Settings → Voice.
  The command catalog is a viewport modal and is not a utility-drawer tab.
  Spoken drawer aliases always open the named tab rather than toggling it closed.
  Spoken `open Notes` also claims the selected drawer note as the current text sink without raising the mobile keyboard; a later pointer or keyboard focus change overrides that claim normally.
  Disabled read aloud is fixed from the `tts` tab's gate rather than from a pane chip; disabled Conversation leaves the top-bar control opening the panel normally and routes its capture gesture to the microphone switch instead.
- **The `tts` tab is read aloud's operational surface** (`VoiceReadTab.tsx`), beside talk and chat.
  It carries, in order: the focused session's participation (`voice_mode`) and content mode (`voice_content`), this device's autoplay toggle and its `▶ n held` shortcut, a link to the master switch's owner in Settings, and the **global clip list**.
  The list is every clip the daemon holds, fleet-wide, ordered by the source message's arrival and never by synthesis time, each row showing its kind (`summary`/`verbatim`), its state, its text, and a play control.
  A row's state is the daemon's `synthesizing`/`failed`, or - once synthesis has settled - what this device did with it: `held`, `playing`, `played`, `dismissed`.
  The two are separate because they disagree by design: a clip is `ready` on the daemon forever while `played` is true on the laptop and false on the phone.
  While `tts_enabled` is off the tab renders a `GrantGate` for `voice.tts` rather than an empty list, which is the invariant in `setting-links.md`: a gated surface never renders as merely empty.
  The tab is mounted once by `App` and hidden rather than dropped, like the assistant body, because it holds the clip list it has fetched and its subscription to `mux:voice-clip`.
- **Any reply can be played from the reader** (`TranscriptTab.tsx`, `transcriptAudio.ts`).
  Each assistant message carries two markers in the same hovering chip row as Copy and Select - one per kind - because the choice between a spoken summary and the reply read out is the choice this surface exists to offer, and the two cost different things: a summary is one model call against the daily read-aloud budget, verbatim never touches a model, and each chip's tooltip says so before it is pressed.
  A marker has four states: nothing yet, being made, ready, and a failed attempt worth retrying.
  `failed` is deliberately distinct from nothing-yet, because retrying is a decision and a summary that died on a budget wall would otherwise re-spend on every click.
  Only `ready` is painted as a play button, so a glance down the column answers "which of these has audio" without reading four words per message.
  **A ready marker plays; it never regenerates.**
  That is what anchoring a clip to its message buys: automatic read-aloud and this button produce identical audio for the same reply, so the daemon answers the second request out of the store (`anchored_clip`, keyed on run + anchor + kind) rather than by spending again.
  A clip the daemon is already making reads as *being made* even though this reader did not ask for it - the automatic path may be making exactly this one, and offering a generate button beside it would pay twice - while a request this tab made and has not seen land is local state, since a clip another device is generating is not something this one can observe until it arrives.
  Clips made here are ordinary clips: they carry the same anchor, appear in the `tts` tab's global list, and are pruned by the same byte cap.
  Each request opens its own stream, so playing a message never joins or cuts whatever a pane's automatic read-aloud happens to be speaking.
  Markers are drawn for **replies only** (a prompt is something the operator wrote) and only while read aloud is on: this is a per-item surface repeated once per reply, so it carries no gate of its own and the one gate for the master switch lives in the `tts` tab (`setting-links.md`).
- **Nothing drawn inside the workspace paints over the voice dock.**
  The dock's anchor sits at `z-index: 30` - above the pane stack's focus ring (25) and above the overflow rails' passive edge glows (29), which are `position:absolute` inside a rail that establishes no stacking context and therefore used to draw on top of the panel while the tab strip underneath it sat correctly beneath.
  The ceiling is unchanged in spirit: context menus (35+) and every overlay (80+) still cover it, because a dialog the dock paints over is a dialog whose own header swallows taps.
  The command palette moved with it, from 25 to 82: at 25 it was under those same rail arrows, and it is a modal overlay rather than a workspace decoration.
- `voice.toggleTalk` and `voice.toggleTargetPin` are ordinary registered commands exposed to the palette, keybindings, and optional mobile gesture slots.
  So are the dock's own: `assistant.toggle` (chip ↔ last expanded size, keeping its id because it is reachable from saved keybindings and gesture slots), `voice.dockExpand`, and `voice.dockCollapse`.
  None of them touches capture.
- Browser/PWA background survival is not guaranteed; capture stops if the tab is suspended.

## Settings surface (Settings → Voice)

Voice is the largest tab in the panel, and it configures two independent halves plus the assistant that sits behind one of them.
It is therefore **one `<section>` per concern**, in reading order, rather than one long scroll of headings - which is what it was, and what made every control in it equally hard to find.
The tab's `<h3>`s are what the panel's section rail is derived from (`features/ui.md`), so the section list *is* the index:

| Section | What it owns |
|---|---|
| Read aloud (TTS) | Engine/clip/spend status, and the three-layer policy block above |
| Voice and engine | `tts_engine` and whichever of the SAPI or Kokoro voice controls it selects, the Kokoro model download, the clip cache limit |
| Pronunciation | `tts_lexicon` and the spelled-word telemetry with its one-tap respell |
| Spoken summary | `tts_content`, the summary model and budgets, the verbatim cap |
| Microphone and wake words | `stt_enabled`, the decoders and language, the STT status readout, `voice_wake_words` |
| Command phrases | `voice_commands` - one row per fixed action |
| Command reference | The complete live catalog (folded) |
| Mux assistant | `assistant_*` and `voice_chat_patience_ms` (`assistant.md`) |
| Testing and latency | The wake-word tester and the stage readout (folded) |
| Mobile voice | Tailscale Serve setup and the phone DNS checklist (folded) |

Three rules hold this shape, and each answers a way the previous single section went wrong:

- **The read-aloud policy is one unit.** The three layers are only useful read together, so they stay one numbered block under the first heading and are never split across sections. This is the same rule as the ordering in *One policy, three layers* above, stated for the surface.
- **The pronunciation lexicon owns a section.** It used to be an `<h4>` inside the Kokoro branch of the engine block, which is the one place nobody looks when a project name comes out spelled letter by letter. It stays a Kokoro repair - the OS voice has its own dictionary and never reads `tts_lexicon` - so under SAPI the section says so and offers the engine control rather than rendering empty.
- **Reference folds; controls do not - and so does a control that is long rather than rarely read.** The command catalog, the two measuring instruments, and the one-time mobile setup are read rarely and are long, so each keeps its heading (and therefore its rail entry) and collapses its body behind a `<details class="settings-disclosure">`. Two Kokoro-only controls fold for the other reason: the voice picker's fifty-odd chips and the pronunciation editor with its spelled-word history each buried everything below them, so both collapse by default, the voice picker naming the current selection on its summary so the closed state still answers which voice is selected. A `data-setting` mark deliberately stays outside every collapsed one: `revealSetting` does open the disclosures above its target, but a switch a gate just promised should be on screen when the panel lands.
- **The budget control is a row of chips, at every width.** `.settings-content label:not(.check)` re-grids every label in the panel into a two-column form row and out-specifies the `.budget-control` scoping that was meant to exempt these, so the tokens/dollars mode radios rendered as tall two-column rows and each axis stranded its one-word label in a 165px (38% on a phone) column. The rules are scoped one class deeper instead - no `!important`, because the fix is to be more specific than the panel's own label rule rather than to shout over it - and `voice-settings.spec.ts` measures it at phone width.

`frontend/test/renderer/voice-settings.spec.ts` pins all four: the section list and its rail, the policy block, the lexicon's own section under both engines, and that nothing deep-linkable folds away.

## Session sounds (unrelated audio path)

`frontend/src/sessionSounds.ts` plays short local notification tones for root-agent lifecycle
events (turn complete, waiting, attention/approval, failure, quota reset). It is entirely
client-side (bundled MP3s or a user-uploaded ≤512 KiB clip), per-device via localStorage, with
quiet hours and a 10 s per-event debounce. It shares nothing with the TTS/STT pipeline above
and never touches the daemon or an LLM.

## HTTP surface

- `GET  /api/voice` — engine/STT availability, content/mode defaults, spend, cache stats,
  and the Kokoro model state.
- `GET|POST /api/voice/models/kokoro[/download]` — the pinned Kokoro download's state, and
  starting it (idempotent while running; progress rides `voice_model_progress` events).
- `GET /api/voice/models/kokoro/preview?voice=` — one audition WAV, synthesized with the
  requested voice regardless of the configured engine and cached per voice on the daemon.
  A GET a media element points at directly, because the document CSP has no `media-src`:
  `default-src 'self'` governs media, so a `blob:` source is refused while this URL plays.
  Settings → Voice → Voice and engine renders the voices as a tap-to-audition picker (theme-picker style: a tap
  plays the sample and sets the draft selection; nothing commits until Save).
- `POST /api/voice/lexicon/check` — advisory per-entry pronunciation verdicts for the
  lexicon editor (`{entries: {word: respelling}}` → per-word `ok`/`phonemes`/`spoken_as`/
  `unspeakable`); `GET /api/voice/lexicon/preview?text=` — audition one respelling value
  (bounded to 200 chars) through the full pipeline with the configured Kokoro voice,
  uncached, spell-out telemetry suppressed.
- `POST /api/sessions/{sid}/voice/transcribe` — WAV utterance → `{text, timings}`.
  Whisper decodes from memory; the optional legacy SAPI engine uses bounded temporary files.
  Optional `X-Mux-Decode-Profile` (`command`/`dictation`) and
  `X-Mux-Utterance-Id` headers.
- `POST /api/voice/transcribe`: the target-independent decoder used by workspace Conversation capture and the wake-word tester.
- `GET|POST|DELETE /api/voice/stt-latency` — the end-of-speech-to-action stage breakdown: report,
  record one browser-measured sample, start a fresh run.
- `POST /api/voice/barge-in-diagnostic` - bounded confirmed/rejected playback probe diagnostics written to `daemon.log`.
- `POST /api/voice/capture-diagnostic` - bounded stalled/recovered capture watchdog reports; a stall is written to `daemon.log` at WARNING because it is the durable evidence a dead microphone leaves.
- `POST /api/voice/deferral-diagnostic` - one resolved unfinished-utterance deferral: the trigger token, its kind, the word count, how long it was held, and the outcome (`merged`, `submitted`, `held`, `discarded`) that judges it. Written to `daemon.log`, because the completeness heuristic is a word list and a word list is only tunable against a measured false-positive rate.
- `POST /api/sessions/{sid}/voice/prepare-submit` - side-effect-free safety validation before Talk uses the mounted terminal path.
- `POST /api/sessions/{sid}/voice/submit` - compatibility-only idempotent voice prompt commit to the PTY.
- `POST /api/sessions/{sid}/voice/approval` - prepare, confirm, or cancel one guarded approval.
- `POST /api/sessions/{sid}/voice/interrupt` — send Ctrl-C to the agent.
- `POST /api/sessions/{sid}/voice/generate` - start segmented last-reply synthesis; optional `{content_mode: summary|verbatim, stream_id: UUID}` values are one-shot and do not change the session preference.
- `POST /api/voice/speak` - start, extend, or close one segmented trusted application-speech
  stream without a model call. `{text, stream_id?, continue_stream?, final?}`: the default form
  opens a stream and returns its opening clip, `continue_stream` appends to an open one and
  returns an acknowledgement rather than a clip, and empty text with `final` closes it.
  `final: false` leaves the stream open, which is what lets an assistant turn speak sentence by
  sentence; `voice_stream_closed` reports the end.
- `GET  /api/sessions/{sid}/last-reply` — the newest assistant segment, cut at its tool boundary and identical to the Transcript tab's last agent message (no terminal OSC 52).
- `GET  /api/voice/clips` — one item per *stream*, newest reply first, each carrying its
  segments as `parts` (in spoken order) plus the summed text, duration, bytes and tokens and
  one rolled-up status. `limit` counts replies, not rows.
- `GET  /api/voice/clips/{id}/audio` — one segment's audio, addressed by row id, including a
  superseded segment until the sweep takes it.
- `DELETE /api/voice/clips/{id}` — deletes the whole stream the clip belongs to.
- `POST /api/remote/mobile-voice/enable` — configure/repair the Tailscale Serve HTTPS address.

## Config knobs (`config.py`)

`tts_enabled`, `tts_default_mode`, `tts_content`, `tts_engine` (`sapi`/`kokoro`),
`tts_kokoro_voice`/`_speed`, `tts_lexicon` (user pronunciation respellings, merged over the
built-in project lexicon; hot-applied with cache invalidation), `tts_sapi_voice`/`_rate`, `tts_summary_model`,
`tts_summary_max_tokens`, `tts_verbatim_max_chars`, `tts_daily_budget` (tokens, dollars, or
first-hit; `usd` by default, which is the unit it enforced before the shape existed),
`tts_cache_mb`;
`stt_enabled`, `stt_engine`, `stt_language`, `stt_whisper_model` (dictation),
`stt_routing_model` (spoken commands; blank falls back to the dictation model);
`voice_wake_words`, `voice_commands` (configurable wake words and per-action trigger phrases),
`voice_chat_patience_ms` (extra endpoint patience while the assistant is the addressee, and the
size of the single extension an unfinished utterance earns - deliberately one number, not two).
The Mux assistant's knobs (`assistant_*`) live with it in `assistant.md`.

## Key files

- `src/swe_mux/voice.py` — `VoiceService` (TTS generate + STT transcribe), `VoiceStore`,
  `SpeechStream` and the open-stream worker, the stream-as-clip reads (`clip_groups`,
  `anchored_group`, `group_state`, `group_snapshot`) and the join, the decode profiles, and
  the latency report helpers.
- `src/swe_mux/voice_audio.py` — WAV concatenation for a completed stream, and the audio
  profile check that decides whether its segments can be joined at all.
- `src/swe_mux/kokoro_tts.py` — the direct-onnxruntime Kokoro engine, the espeak-free G2P
  constraint, and the out-of-vocabulary repair ladder.
- `src/swe_mux/voice_models.py` — the pinned, hash-verified Kokoro model download state machine.
- `src/swe_mux/server.py` — voice HTTP handlers.
- `src/swe_mux/tailscale.py`, `src/swe_mux/__main__.py` — mobile HTTPS Serve setup/auto-start.
- `frontend/src/voice.ts` — singleton playback, autoplay, barge-in, open-stream queueing, and
  the reply-level controls (`playClipGroup`, `seekWithinGroup`, `clipGroupDeviceState`).
- `frontend/src/voiceGroups.ts` — a reply's segments as one timeline, as pure arithmetic:
  spans, duration, position, and which segment covers a point on the scrub bar. Covered by
  `frontend/test/voiceGroups.test.ts`.
- `frontend/src/assistantSpeech.ts` — one speech stream per assistant turn (`assistant.md`).
- `frontend/src/voiceIntents.ts`, `frontend/src/voiceQueries.ts`, `frontend/src/voiceNavigation.ts`, `frontend/src/fleetStatus.ts` - deterministic registry resolution, typed spoken lookup/paging/help, canonical hierarchical indexes, and fleet speech projection.
- `frontend/src/voiceConversationHistory.ts` - bounded device-local storage for recognized utterances and Mux outcomes, plus the persisted open or collapsed state of the Talk history disclosure.
- `frontend/src/voiceDock.ts` - the dock's size axis and its body/addressee types: the pure reducer (`reduceVoiceDock`), the loan rule for capture-opened docks, the card floor, `voiceBodyVariant`, `voiceAddressee` (only the dictation draft is ever the addressee), and device-local persistence. Covered by `frontend/test/voiceDock.test.ts` and `frontend/test/renderer/voice-dock.spec.ts`.
- `frontend/src/spokenListContext.ts` - validated five-minute device-local membership and paging context for recent spoken lists.
- `frontend/src/voiceMode.ts` - the pure resolver every surface shares (`resolveVoiceMode`, `voiceModeLabel`): stored mode, else the global default, and `off` whenever the master switch is off. Dependency-free because the sidebar row's token engine imports it and must not pull in playback state.
- `frontend/src/transcriptAudio.ts` - the reader's per-message markers as pure functions: the clip index keyed by `message_anchor`, the four marker states and which one plays, and the spend each kind states before it is pressed. Covered by `frontend/test/transcriptAudio.test.ts`.
- `frontend/src/ConversationControl.tsx`: `useConversation` (the app-root capture controller, target pin, command loop, speculative decoding, push-to-talk, and Talk history), `VoiceControl` (the one top-bar voice button: click opens the panel, ctrl+click or a hold toggles capture, colour means capture alone), and `VoiceDock` (the one voice surface: header with the panel's own microphone and the talk/chat/tts tabs, the dictation body, and the assistant and read-aloud slots).
- `frontend/src/VoiceReadTab.tsx` - the `tts` tab, read aloud's only control surface: the focused session's participation and content mode, on-demand generation, the transport for the loaded reply (spanning its segments), this device's autoplay, the gate/link split with the master switch's owner in Settings, and the global clip list - one row per reply, with its arrival ordering and its daemon-state/device-state split.
- `frontend/src/conversationTarget.ts`, `frontend/src/insertTarget.ts`: pure target resolution plus the shared terminal/editor focus ledger used by Agent, note, Scratchpad, Markdown, and Queue sinks.
- `frontend/src/conversationDraft.ts` — the utterance-log draft model behind undo and editing.
- `frontend/src/conversation.ts` — `PersistentVoiceCapture` and the `Mux` command matcher.
- `frontend/src/audioFrames.ts` — streaming resampler and 512-sample framing.
- `frontend/src/speechGate.ts` — the frame-counted endpointing state machine and both gate
  configurations.
- `frontend/src/utteranceCompleteness.ts` - the pure completeness heuristic (dangling
  conjunction, preposition, article), its two false-positive guards, and the patience/extension
  arithmetic. No timers, no capture, no dialog: the whole rule set is unit-testable from a
  string.
- `frontend/src/utteranceDeferral.ts` - `DeferralPen`, the holding pen for the one unfinished
  utterance. Clock-injected like `CaptureFrameWatchdog`, and deliberately effect-free: it never
  dispatches a turn, posts a diagnostic, or owns a timer. It answers what should happen to an
  utterance, whether a release is due, and who holds the fragment now, so the structural claim
  (at most one deferral per utterance) is testable without a microphone.
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
- `frontend/test/renderer/voice-settings.spec.ts` — the Settings → Voice section structure above:
  the section list and its rail, the read-aloud policy block, the lexicon's own section under both
  engines, and that no `data-setting` mark folds away behind a closed disclosure.
