# Voice interaction roadmap

The plan for taking voice from "dictate into one agent pane" to "operate swe-mux hands-free".
Hands-free is the goal, not a convenience: the feature is judged on whether the workspace can be
navigated and understood without touching the screen.

Current behaviour is documented in `design/features/voice.md`.
This document records the decisions behind the remaining work and the order to do it in.

## Standing decisions

These are settled.
Re-open them with evidence, not preference.

- **No model in the command path.**
  Latency tolerance splits cleanly by kind: a command is a reflex and feels broken past ~500 ms,
  a question is a conversation and tolerates seconds.
  A router adds cost and a network dependency to the reflex path, where deterministic matching
  already wins, and buys almost nothing: people converge on three to five phrasings per action
  within a week.
- **The wake word is a hard mode boundary, and stays deterministic.**
  Failure is asymmetric. A command misheard as dictation is a visible stray line; dictation
  misheard as a command can approve a tool call, kill a session, or send half a thought.
  Nothing becomes an action without an explicit wake word.
- **If a model is ever added, it never emits an identifier and never executes.**
  It may produce words to be read aloud, or propose a target *name* that deterministic code
  resolves against real entities and a human confirms.
  A hallucinated session id inside `session.kill` is the failure this rules out.
- **Voice compiles to the existing command registry** (`frontend/src/commands.ts`), never to a
  parallel action table.
  A second surface drifts from the palette and keybindings within a month.
- **Guard the submit, not the buffer.**
  A draft survives a target change and the panel names its target.
  Auto-submitting on a focus change is destructive; silently clearing loses dictation. Neither is
  acceptable when nothing commits without an explicit `send`.
- **Navigation is single-utterance; mutation confirms.**
  The worst case for navigation is being on the wrong tab. Requiring confirmation there is what
  makes hands-free feel bureaucratic rather than fast.
- **Voice inherits the prompt queue's non-overridable reasons**
  (`NON_OVERRIDABLE_REASONS` in `src/swe_mux/prompt_queue.py`).
  Text delivered at an approval dialog can *answer* it; voice does not get a route around a
  boundary the queue already refuses to cross.
- **One read model for fleet status, not a second cache.**
  The status ledger, delivery readiness, the project card, and Tier 0 facts already are the cache.
  A parallel store would be a second source of truth for status, which this codebase has paid for
  twice (transcript-switch cross-attribution, `transcript_stale` false positives).
- **Freshness and confidence are computed by the system, never self-assessed.**
  Every field in a spoken status claim carries provenance and age.
  "Session 3 finished" versus "session 3 has looked idle for 4 minutes, last transcript write 6
  minutes ago" is the difference between a useful assistant and the false-positive ready
  notifications already fixed once.
- **Always-listening widens the trust boundary from same-user to same-room.**
  Standby is the default posture, and no destructive action happens without confirmation.

## Already shipped

Do not re-plan these.
Earlier drafts of this work listed them as open.

- The capture controller is app-level (`useConversation` in `frontend/src/ConversationControl.tsx`,
  held once in `App`). The mic is structurally singular and the pane-to-pane claim mutex is gone.
- Talk and read aloud are independent switches. Starting talk does not write `voice_mode`.
- The dictation draft is an editable floating surface with an utterance-log model
  (`frontend/src/conversationDraft.ts`) backing per-phrase undo and typed corrections.
- Both voice surfaces float over the terminal rather than taking pane rows, pinned by
  `frontend/test/renderer/pane-layout.spec.ts`.

## Phase 1 — STT latency

First, because everything else is judged through it, and because every item improves what exists
today regardless of what is built later.

Measured constants today: a 900 ms trailing-silence endpoint (`conversation.ts`, `finish` gate),
`beam_size=5` decoding, and a WAV written to disk before transcription (`src/swe_mux/voice.py`).

1. **Instrument four stages** before changing anything: speech-end → POST sent, POST → decode
   start, decode start → text, text → action.
   Expectation is that the endpoint is over half the total, but the fixes have very different
   payoffs depending on whether decode is 200 ms or 800 ms, and this path has no timing today.
2. **Replace the energy VAD with Silero** and drop the trailing silence to ~350 ms.
   The 900 ms tail exists *because* an RMS-over-noise-floor detector false-triggers on breath and
   room noise; an accurate detector does not need to ride that out. Largest single win.
3. **Speculative decode.** Begin transcribing at ~300 ms of silence while still listening, and
   discard the result if speech resumes.
   Spends idle GPU to hide inference inside the endpoint wait.
4. **Use the suffix grammar as an endpoint signal.** A speculative transcript that already ends in
   a complete wake word plus command phrase is strong evidence the utterance is over, so commands
   short-circuit the remaining silence. Dictation still waits the full tail.
5. **Adaptive beam size:** greedy under ~3 s, `beam_size=5` only for longer dictation.
6. **Two models by job:** a small English model for the routing pass, `turbo` for dictation text,
   run concurrently on the same audio.
7. **Decode from memory**, deleting the WAV write and its stale-utterance sweep.
   Also makes "no audio is retained" true by construction rather than by cleanup.
8. **`AudioWorklet` instead of `ScriptProcessorNode`**, which runs on the main thread and adds
   jitter exactly when the UI is busy.
9. **Instant speech-end acknowledgment** (tone or mic state flip) before any text exists.
   Two seconds of silence reads as broken; two seconds after "heard you" reads as thinking.
10. **Push-to-talk keybinding** as a desktop escape hatch with no endpointing at all.

Deferred until measurement justifies them: WebSocket audio streaming so decode overlaps speech,
and a prefix wake word with on-device wake-word detection.
The prefix form would let a local detector gate the pipeline so nothing is transcribed unless the
wake word fired, at the cost of the suffix ergonomics that make dictation work.
Supporting both (prefix for commands, suffix retained for `send`) is the compromise if it is ever
needed.

**Exit criterion:** under ~500 ms from end of speech to action for a short command.

## Phase 2 — Wake word and the tester

Ordered before the trigger word is changed, because wake-word choice is an ASR problem wearing a
configuration problem's clothes.

- **Tester in Settings → Voice:** capture N utterances, run them through the real transcribe
  endpoint and the real matcher, and report heard-versus-matched.
  Config already exists (`voice_wake_words`, `voice_commands`, `buildVoiceMatcher`); only the
  measurement surface is missing.
- **Choose the trigger word from that data.**
  Good wake words are two to three syllables, phonetically distinctive, rare in ordinary speech,
  and not a prefix of a common word.
  A bare "swe" is a poor candidate on every count and will return as sway/swee/sweet; the shipped
  `mux`/`mucks`/`max` variant set exists because the same problem was already hit once.

## Phase 3 — Global talk surface and targeting

- **Target follows the focused session** rather than pinning at start
  (`useConversation` currently binds its target in `start`).
- **The panel names its target**, and the draft survives a target change.
- **A target pin** ("stay on this one") for reading one pane while dictating to another, which is
  the common case on a desktop with splits.
- **Talk on/off persists as a workspace-level flag**, not a property of whichever pane owned it.
- **Lift the dictation panel to an app-level floating layer**: bottom sheet on mobile, corner card
  on desktop, above panes and below modals in the overlay z-band.
- **Retire the per-session `talk:` chip**, keep `tts:`, and add a mic control plus an optional
  gesture. A gesture cannot be the only trigger: it is discoverable once and invisible after.
- **Add the third sink**: text surfaces (note editor, scratchpad, queue composer).
  Naming it now is what keeps note dictation from being a retrofit.

The routing model is four sinks, not one: a session's PTY, a text surface, the app, and fleet
status. Target-follows-focus is the default binding for the first, not the architecture.

## Phase 4 — Command and navigation layer

The registry does the heavy lifting. `available` and `disabledReason` become spoken refusals for
free, and `searchCommands` is already a fuzzy matcher.

- **Generate entity commands:** `session.focus:<id>`, `project.focus:<id>`, and
  `drawer.show:<tabId>` from `DRAWER_TABS` (only `clipboard` has a direct command today).
  These improve the palette for keyboard users too, which is the sign the abstraction is right.
- **Bridge = resolve target → focus it → `runCommand(id)`.**
  `commandSession` and `commandProject` already fall back to the focused session and active
  project (`App.tsx`), so focusing is sufficient to retarget the whole existing registry without
  touching a single handler.
- **`voiceIntents.ts`:** spoken-text normalization (filler stripping, number words), slot templates
  (`go to X`, `open X`, `new terminal in X`), and a deterministic resolver returning
  `{match, candidates, confidence}`. Pure functions, testable in the existing `node:test` style.
  Note that `searchCommands` is tuned for typed prefixes, not spoken sentences, and needs the
  normalization pass in front of it.
- **A headless spawn command** carrying `{project, backend, seed_text}` directly to the spawn API.
  `session.quickLaunch` opens a modal, and a modal is unusable hands-free.
  This is the user's own voice, so it does not go through MCP `request_spawn`, which deliberately
  writes an inert draft because its caller is an agent.
- **Ambiguity speaks a short numbered list** rather than guessing.
- **A navigation command retargets the dictation sink**, so "go to backend" then talking sends
  text to backend.
- Optional: a local embedding match over configured phrases for phrasing variance, offline and in
  milliseconds. This is the non-model answer to the only problem a router would have solved.

## Phase 5 — Fleet status, model-free

- **One read-model projection** composing the existing control plane into a small snapshot, with
  per-field freshness and provenance, invalidated by the events that already drive the UI.
- **A templated spoken rundown** from that structured data.
  A deterministic sentence ("three running, one waiting for approval in swe-mux, one idle twenty
  minutes") is likely most of the value, at zero latency and with no capacity to invent a session
  that finished.
- **Templated state-referential targeting.**
  "The one waiting for approval" and "the stuck one" are not open-ended language: the status
  system defines a closed set of roughly eight predicates, so these are templates over fields that
  already exist.

## Phase 6 — Guarded mutations

- Voice may surface and navigate to approvals.
- Answering one requires a two-step confirmation that restates the actual operation.
- No blanket "approve all", ever.

## Cross-cutting: prototype full duplex early

Once anything is read back, TTS plays while the mic is open, and a phone speaker with imperfect
echo cancellation will transcribe the agent's own voice.
This is the single most likely thing to make hands-free feel broken, and the answer decides
whether Phase 5 rundowns are spoken in full or reduced to one line with detail on request.
Prototype it before building rundown content.

## Rejected

- **An LLM intent router in the command path.** Adds a network dependency and a new misfire class
  to the path used a hundred times a day, in exchange for phrasing variance that templates and a
  local embedding match already cover.
- **Auto-submit or clear-on-switch when the talk target changes.** One is destructive, the other
  loses work; naming the target and guarding the submit costs nothing and does neither.
- **A voice-specific action table.** Drifts from the palette and keybindings.
- **A dedicated status cache for the fleet agent.** The control plane is that cache.
- **Model self-verification of status accuracy.** A model asked to check its own confidence
  confabulates it. Provenance is a system property.

## Key files

- `frontend/src/conversation.ts` — VAD capture, WAV encoding, wake word and command matcher.
- `frontend/src/ConversationControl.tsx` — `useConversation` controller, chip, dictation panel.
- `frontend/src/conversationDraft.ts` — the utterance-log draft model.
- `frontend/src/commands.ts` — command registry, `runCommand`, `searchCommands`.
- `frontend/src/drawerTabs.ts` — `DRAWER_TABS`, the source for drawer entity commands.
- `src/swe_mux/voice.py` — `VoiceService`, `transcribe_wav`, the faster-whisper call.
- `src/swe_mux/prompt_queue.py` — `NON_OVERRIDABLE_REASONS`, the delivery boundary voice inherits.
- `design/features/voice.md` — current behaviour. `design/features/status-detection.md` and
  `design/features/delivery-readiness.md` — the Phase 5 read model's sources.
