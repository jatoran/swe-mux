# The Mux assistant

## What it is

A conversational operator for the whole workspace: the control plane given a chat surface and, behind the voice grammar's fallback tier, a voice.
It is not a coding agent and not an observer.
It never writes code, never touches a PTY directly, and never acts on its own initiative.
It converses, reads the fleet through existing read models, and drives existing daemon operations behind a per-class trust policy.
Asked for something that is coding, it routes: queue a message to an existing session, or spawn a new one.

## Contract

- **Fallback tier, never the reflex path.**
  The deterministic voice grammar (tier 1) and the fuzzy pass (tier 2, `voiceFuzzy.ts`) run in the client; only a wake-word utterance neither matched reaches the assistant.
  A spoken command's latency never includes a model call.
- **The model never emits an identifier and never executes.**
  Tools take project and session *names*; `resolve_session`/`resolve_project` map them onto live entities and answer ambiguity with candidate lists the assistant reads back.
  Session names are **display names**: the snapshot, `session_detail`, restatements, and resolution all apply the same rule every UI surface does (`session_titles.py` — a generated title wins while the session is auto-named), so the assistant never quotes a spawn id at a session the operator knows by its title, and a title it quotes always resolves back.
  Every side effect travels an existing path: the prompt queue, the spawn contract, the PTY interrupt operation, the graceful end operation, project-note writes.
  `NON_OVERRIDABLE_REASONS` and the approval floor therefore bind structurally, not by prompt.
- **Trust is enforced daemon-side per action class**, in `AssistantService._run_tool`:
  - *read* (session detail, transcripts, history search, note listing and reads, queue state): executes silently.
  - *navigation* (`run_ui_command`): dispatched to the operator's device (below), no confirmation.
  - *reversible* (queue an inert draft, append to or granularly edit a project note — `edit_project_note`: append, prepend, insert at a 1-indexed line, or replace a unique text span (`apply_note_edit`, pure) through the ordinary revisioned note write — spawn a session, create a project (`create_project`, below), or stage unsent composer text with `type_into_session`): follows `assistant_trust_reversible` — `auto`, `cancel_window` (default: announce, execute after ~6 s unless cancelled), or `confirm`.
  - *consequential* (armed send, interrupt, end session, `submit_session_composer` — pressing Enter on staged composer text is a send): always an explicit confirmation with a bounded TTL; this floor is deliberately not configurable.
  A pending or scheduled action is typed state (`assistant_actions` row) rendered as a card, and a daemon restart expires anything still pending — a confirmation minted by a dead daemon can never execute.
- **Dialog state is daemon-owned** (`assistant_dialogs`/`assistant_messages`/`assistant_actions` in SQLite, one worker thread like `voice_clips`).
  Any device resumes the same conversation; a dropped tab cannot orphan a half-confirmed action.
- **Freshness is computed by the system, never self-assessed.**
  The per-turn workspace snapshot (`fleet_snapshot`) carries ages derived from session records; `state_since == 0` reads as unknown, never as "just now".
  A session whose harness handed off to background agents carries `running_work_for` beside `state_age`, because `idle` with no `turn_running_for` is also the shape of a session an hour into a request, and answering "how long has that been going" from `state_age` alone reports the hand-off instead of the work (`features/status-detection.md`).
- **Budgeted like every model feature.**
  Calls run on the configured OpenRouter model (`assistant_model`, default `openai/gpt-5.6-terra`; tool calling verified against the live catalog), spend lands in the shared automation ledger under `builtin:assistant`, and the daily budget is checked before each call — an exhausted budget fails the turn closed.
- Failures are typed `AssistantError` and never touch PTY, session, transcript, history, or project state.

## The turn

`POST /api/assistant/dialogs/{id}/turns` records the user message and returns `202 {turn_id}`; everything else arrives over the ordinary event stream so every connected device renders the same turn:

`assistant_turn_started` → `assistant_sentence` (per sentence, **dual-form**: `display` and separately paced `speech`) → `assistant_tool_status` / `assistant_action` as tools run → `assistant_turn_done` (full display/speech plus usage) or `assistant_turn_failed`.

The loop behind it makes at most `MAX_MODEL_CALLS_PER_TURN` model calls per user turn, appending tool results between them; the prompt is the fixed short-response primer, the fleet snapshot plus the client's context (focused session, available UI command labels, bounded), and the last `assistant_context_messages` dialog messages.
Interrupt cancels the running task; nothing already executed is undone.

## UI command dispatch

Focus, drawer tabs, and panels are per-device UI state the daemon cannot run.
The `run_ui_command` tool records a `dispatched` action and waits (bounded) for a device acknowledgement; the client executor resolves the phrase with `planUiCommand` (`uiCommand.ts`) — registry aliases first, then the closed query grammar (which owns "open project X" navigation and answers entity misses with candidates), then the fuzzy pass, then an exact label match — runs the plan, and reports `POST /api/assistant/actions/{id}/ui-result`.
The `{text}` catch-all is excluded from that ladder by construction: for a dispatched command it matches anything, and it once turned "move to project X" into a voice lookup instead of a failure the assistant could react to.
The per-turn context also names the reliable command shapes ("open project <name>", "open the <tab> tab", …) so the model prefers them over free paraphrase.
No connected client is an honest tool failure, not a silent success.

## Client-executed terminal work

Three more kinds execute on the operator's device, because the mounted pane owns PTY
writes (bracketed paste, replay, ownership claims, acknowledged results) and pane
placement is per-device layout state — the daemon never types into a PTY for the
assistant and never picks a pane:

- `type_into_session` stages text in a session's composer **without** a carriage
  return, via the same `insertIntoTerminal(…, submit=false)` primitive voice "append"
  uses; repeated calls accumulate, and nothing reaches the agent. The session's
  terminal must be mounted on the device — an unmounted pane reports an honest failure
  the assistant relays ("focus it first").
- `submit_session_composer` presses the same Enter the mobile Send control uses
  (`sendKey('\r')` through the pane), sending whatever is staged. It is a send, so it
  sits on the consequential always-confirm floor.
- `spawn_session` from a turn with a connected workspace dispatches to that device's
  own launch path (`spawnTerminal`), so the new session opens as a **tab in the
  currently active pane** with the optimistic leaf and focus every other launch entry
  point gets — instead of the layout reconciler's default new pane. There is
  deliberately no daemon fallback when the dispatch fails: a lost acknowledgement plus
  a daemon retry would spawn twice. A turn with no `client_id` (old client, headless)
  keeps the daemon `spawn_op` path.

Every client-executed action is stamped with the originating tab's per-tab
`client_id` (sent in the turn's `client_context`, persisted in the action's
arguments so a later confirm still targets the same tab); executors on other devices
ignore it — an untargeted broadcast would type into every mounted copy of a pane and
spawn one session per open workspace. Mutation rows keep their persisted status; a
synthetic `dispatched` `assistant_action` event carries the work (with
`target_session_id`/`project_id` extras — `session_id` is a first-class MuxEvent
field the bus lifts out of the payload) and the device reports back through the same
`ui-result` endpoint UI commands use.

## Creating projects

`create_project` mints a project that does not exist yet — the one assistant mutation that touches the filesystem — and its whole safety story is one constraint: **the model supplies a name, never a path.**
The folder leaf is derived from the name by the same deterministic normalization the Add-project dialog suggests (`leaf_names.suggest_folder_name`, spaces → hyphens), validated under the shared Windows-safe leaf rules, and joined to the one configured parent (`new_project_parent`, Settings → Projects).
Unset, missing, duplicate-root, and existing-non-empty targets are all answered at preflight — the refusal names the setting — so a card never pends for something that cannot execute; adopting populated folders stays the Add-project dialog's job.
The restatement carries the exact absolute path (and, when the root matches a tombstoned registration, that the removed project's identity and history revive), so the operator confirms what lands on disk rather than a name to resolve.
Execution is the ordinary registration path (`ProjectManager.register` with `create_missing`, emitting the same `project_created`/`project_restored` events as `POST /projects`); setup commands never run from the assistant — the result says so and points at the Run menu.
An optional `git: true` chains the one-time repository initialization with its contract intact: nothing staged, no commit made, and an init failure reports without unwinding the registration.
Reversal is the same as spawn's class implies: removal is a registration tombstone that deletes nothing on disk, and the minted folder is empty.

## Voice attachment

The assistant is text-first and voice-attached, not voice-only:

- In the voice overlay, a `talk`/`chat` mode toggle switches the same floating panel between the dictation draft and the conversation view (`AssistantPanel`); the chat is also reachable with the microphone off (`assistant.toggle`).
  **Chat is the default mode** (device-local, persisted; a deliberate switch sticks): the assistant lane is the primary one, and talk — free, deterministic, model-less — stays one tab away as the degradation path for budget exhaustion, provider outages, and verbatim dictation. Talk mode is deliberately not removed: the tier-1 grammar it carries is load-bearing inside chat mode too ("Mux, stop", confirm/cancel, navigation), and the assistant's composer tools execute through the same acknowledged terminal path.
  Chat mode is bounded to roughly half the viewport — a dialog consulted beside the terminals, never a takeover — and collapses to its header (device-local, persisted); the collapsed body stays mounted so streaming, card speech, and earcons keep working while folded.
- **Thinking out loud is not answered at every pause.** Two deterministic client mechanisms
  (both in `voice.md`): `voice_chat_patience_ms` lengthens the endpoint tail while the
  assistant is the addressee (commands keep short-circuiting it), and the `hold`/`proceed`
  brainstorm pair buffers plain speech until a "go ahead" cue releases it as one consolidated
  turn. Deliberately not an assistant tool: a wait tool runs *inside* a turn, so every pause
  would still cost a model call — the same reason confirm/cancel keeps the model out of the loop.
- **The mode toggle is the microphone's addressee switch.**
  While chat mode is open with Talk active, every plain utterance is a conversation turn and the dictation draft is deliberately deaf — the two modes never both hear the same speech.
  A wake-word utterance keeps its normal meaning in either mode ("Mux, stop" still kills playback mid-dialog), and the chat header shows `mic→assistant` while the routing holds.
- With Talk active, `assistant_turn_done` speech plays through the existing application-speech pipeline (client-claimed stream, segmented clips, barge-in unchanged).
- A **follow-up window** (~8 s after a spoken reply) routes the next wake-word-free utterance back to the assistant in dictation mode too — one addressee removes the ambiguity the wake word exists to resolve.
- **Spoken confirmation is deterministic.**
  A pending or scheduled card is spoken aloud with its restatement; a bare `confirm`/`cancel` (a closed word set, `spokenConfirmation` in `assistant.ts`) resolves the newest open card directly against the confirm/cancel endpoints — the model is never in that loop, so it cannot "confirm" by talking about it.
  Anything conversational ("yes but change the wording") falls through to the model as an ordinary turn.
- Earcons (`earcons.ts`, WebAudio oscillator blips — no assets, no fetch) acknowledge the endpoint instantly and mark turn completion and pending actions, which is what makes 1-2 s of model latency feel attended rather than dead.

## HTTP surface

- `GET  /api/assistant` — enabled, model, budget, spend, trust level, diagnostic.
- `GET|POST /api/assistant/dialogs` — list, create.
- `GET  /api/assistant/dialogs/{id}` — messages, actions, whether a turn is running.
- `POST /api/assistant/dialogs/{id}/turns` — `{text, client_context}` → `202 {turn_id}`.
- `POST /api/assistant/dialogs/{id}/interrupt`
- `POST /api/assistant/actions/{id}/confirm | /cancel | /ui-result`

## Config knobs (`config.py`)

`assistant_enabled` (off by default, like every model-cost feature), `assistant_model`,
`assistant_daily_budget_usd`, `assistant_max_output_tokens`, `assistant_context_messages`,
`assistant_trust_reversible`.
`create_project` additionally reads `new_project_parent` (Settings → Projects, not an assistant knob): shape-validated at save, existence-checked at use, and empty disables assistant project creation.

## Key files

- `src/swe_mux/assistant.py` — `AssistantService` (turn loop, tool bridge, trust policy, resolution), `AssistantStore`, the tool definitions, the primer.
- `src/swe_mux/openrouter.py` — `complete_tools`, the bounded tool-calling completion.
- `src/swe_mux/server.py` — assistant HTTP handlers and service wiring (note read/append closures, history search, spawn/interrupt/end operations shared with session control).
- `frontend/src/assistant.ts` — client dialog view, event reducer, follow-up window, API calls.
- `frontend/src/AssistantPanel.tsx` — the conversation view and action cards.
- `frontend/src/voiceFuzzy.ts` — tier 2, the conservative fuzzy pass in front of the fallback.
- `frontend/src/earcons.ts` — the synthesized acknowledgment sounds.
- `frontend/src/App.tsx` — tier wiring in the voice catch-all, the UI-action executor, surface placement.
- `tests/test_assistant.py`, `frontend/test/assistantEvents.test.ts`, `frontend/test/voiceFuzzy.test.ts`.
