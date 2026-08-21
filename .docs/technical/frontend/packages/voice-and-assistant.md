# Frontend: voice and the Mux assistant

Index: `../packages.md`.
Design: `../../../design/features/voice.md`, `../../../design/features/assistant.md`.

## Voice conversation

`ConversationControl.tsx`, `VoiceCommandsButton.tsx`, `voiceComms.ts`, `voiceLaunch.ts`, `railVoice.ts`,
`terminalActions.ts`, `voiceConversationHistory.ts`, `spokenListContext.ts`, `voiceNavigation.ts`,
`voiceCommandReference.ts`, `conversationTarget.ts`, `conversation.ts`, `voiceIntents.ts`, `voiceQueries.ts`,
`fleetStatus.ts`, `insertTarget.ts`, `audioFrames.ts`, `speechGate.ts`, `utteranceCompleteness.ts`,
`utteranceDeferral.ts`, `sileroVad.ts`, `voiceCaptureWorklet.ts`, `voiceLatency.ts`, `wakeWordTest.ts`,
`VoiceLatencyReport.tsx`, `WakeWordTester.tsx`, `VoicePlayer.tsx`, `VoiceReadTab.tsx`, `voiceDock.ts`,
`voice.ts`, `mobileVoice.ts`

Scope: app-owned capture, draft, and history; a pane-attached floating view with a top fallback; follow and pin targets; registry-backed commands; typed fleet, help, and reply queries; guarded approvals; confirmed-speech barge-in; segmented playback; session-scoped Voice Comms; mobile HTTPS.

### Pure policy

- `voiceIntents.ts` - alias, slot, and ambiguity resolution.
- `voiceLaunch.ts` - name, number, and current-Project spawn aliases.
- `railVoice.ts` - safe focused-session rail adaptation (`actions-and-clipboard.md`).
- `voiceQueries.ts` - the closed natural query grammar and category-aware spoken formatting.
- `voiceComms.ts` - the short-response protocol and per-message wrapper.
- `voiceNavigation.ts` - live global-Project and Project-scoped-session indexes plus non-wrapping adjacent-session traversal derived from rendered sidebar order.
- `voiceCommandReference.ts` - the single complete catalog model combining configurable capture actions, fixed grammar, and live registry aliases, for Settings, the modal, and spoken discovery.
- `fleetStatus.ts` - the provenance and freshness projection.
- `conversationTarget.ts` - target policy and live run and read-aloud accessors.
- `voiceConversationHistory.ts` - bounded device-local history and the disclosure preference.
- `spokenListContext.ts` - validated five-minute device-local membership and paging state for spoken pages.

### Capture and delivery

`conversation.ts` owns the only stateful capture object, the duck-settle-confirm playback speech probe, and playback-control classification.
The worklet, resampler and framer, Silero, and the frame-counted gate remain separate pure or narrowly stateful layers.
`terminalActions.ts` owns the 180 ms paste-to-submit settle plus the generic request and acknowledgement envelope used by Talk rail actions.
`insertTarget.ts` owns one-shot non-DOM editor claims used by spoken Notes navigation.

### Playback

`voice.ts` owns requested-stream claims, ordered segment queues, autoplay, sidechain muting, and whole-stream suppression.

- A claim survives a non-positive `segment_count` - an open stream whose length is not yet known - until its closing segment or `voice_stream_closed`.
- A new segment queues whenever a clip is loaded and unfinished, rather than only while audio is audible.
- Every stop switch suppresses the whole claim map rather than the audible stream alone, **because a claim outlives its clip**.
- Autoplay is additionally focus-driven and global (`setPlaybackFocus`/`sessionPlaysHere`): the focused session plays here, and every other session's clip is **held** (bounded, newest kept), surfaced as ready-to-play by `VoicePlayer` and the command palette.
  A held clip is never started by a focus move, is dropped by every stop switch, and is overridden only by a Voice Comms pin (`setPinnedPlaybackSession`).
- `voice.ts` also keeps the **per-device** half of a clip's state - held, playing, played (heard to the end, never merely started), dismissed - bounded and unpersisted, because a clip played on the phone is unplayed on the desktop and the daemon's row must not claim either. `VoiceReadTab.tsx` renders it over the daemon's `synthesizing`/`ready`/`failed`.

### Diagnostics

`voiceLatency.ts` joins browser and daemon timing and playback origin.
Completed barge-in probes post bounded detector, origin, and peak measurements to the daemon log.
`wakeWordTest.ts` scores real routing-decoder trials.

`CaptureFrameWatchdog` (in `conversation.ts`, clock-injected and pure) separates a dead capture from a quiet room by raw-block liveness: a stall renders the `stalled` phase instead of `listening`, attempts `context.resume()`, and posts bounded stalled and recovered reports to `/api/voice/capture-diagnostic`.

`utteranceCompleteness.ts` is the pure unfinished-utterance rule set - dangling conjunction, preposition, or article, plus the question and length guards - and the patience and extension arithmetic.
`utteranceDeferral.ts`'s clock-injected `DeferralPen` owns the one-deferral-per-utterance decisions (offer, release, take), while `ConversationControl.tsx` keeps the effects: the single re-arming release timer, the assistant dispatch, and the resolution report to `/api/voice/deferral-diagnostic`.

## Mux assistant

`assistant.ts`, `assistantSpeech.ts`, `AssistantPanel.tsx`, `voiceFuzzy.ts`, `earcons.ts`

`assistant.ts` is the browser-free client model:

- Dialog, action, and message types.
- The pure `applyAssistantEvent` reducer the panel folds `mux:assistant-event` window events through.
- The persisted current-dialog id.
- The follow-up window: a short wake-word-free window after a spoken reply.
- The closed spoken-verdict grammar, forgiving about filler, strict about meaning, with cancel beating a wrapping affirmative.
- Every API call.

`startNewDialog` is the single new-conversation path both surfaces use - the panel's `new` button and the `assistant.newConversation` voice alias, whose phrases and spoken reply are declared beside it.
It unremembers the dialog id, creates the next one, and announces `mux:assistant-dialog-reset` so the panel - the only holder of the view being cleared - reacts rather than clearing itself, which keeps the two surfaces from growing two notions of "the current dialog".
The dispatch is `typeof window` guarded because the module is imported by DOM-free tests.

`assistantSpeech.ts` owns **one speech stream per turn**: sentence appends serialized behind one another, the card announcement joining the open stream (or a loose non-interrupting one, closed on idle) rather than a second one that would hard-stop it, the spoken verdict deliberately taking the floor instead, and an append that stops the moment playback drops the stream's claim.

`AssistantPanel.tsx` renders the daemon-owned dialog inside the voice panel's `chat` mode and drives that speech from the sentence events as they land: typed pending and scheduled action cards with the cancel-window countdown, interrupt, and new-dialog.
An `assistant_notice` - the assistant speaking outside any turn, today a Project Action's outcome arriving after its steps finish - renders as a finished message and is spoken on the announcement path, so an outcome landing mid-sentence joins the stream instead of cutting it.
The conversation a new-dialog cleared is stashed into a collapsed `previous conversation` disclosure instead of being dropped, which is the property that lets clearing context run with no confirmation from either surface.

`voiceFuzzy.ts` is tier 2 of voice routing: positional token-similarity over the compiled grammar with a threshold and ambiguity margin, deliberately excluding `{text}` slot phrases.
`earcons.ts` synthesizes the acknowledgment blips in WebAudio, with no assets and no fetch.

`App.tsx` owns the tier wiring in the `voice.query` catch-all, the UI-action executor that resolves dispatched `run_ui_command` labels against the live registry and reports back, and the shared surface placement.
`ConversationControl.tsx` owns the `talk`/`chat`/`tts` tab strip and the follow-up routing inside `handleTranscript`.
It also owns `VoiceControl`, the single top-bar voice button whose plain click toggles the panel and whose ctrl+click or 550 ms hold toggles capture, with the lit state bound to capture alone (`../../../design/features/voice.md`).
