---
swe_mux_note = 1
kind = "projects"
id = "29a044bb-a06b-4216-95e4-39c5e91d48fb"
---
# swe-mux project notes

Agentic Controle Plane (ACP)

what happens with session transcripts/tracking when i /new or /clear an agent session within a session?
	im not sure we have this handled 

Test automations/control plane before proceeding through remaining roadmap
	Ensure everything is firing and working properly across the control plane/automation/observation hierarchy

codex has a "latest" button that scrolls you to bottom, and it works. claude has a "jump to bottom" button which works on desktop, but doesnt work on mobile. the ctrl+end command in the command rail still works on claude, but id like tapping that jump to bottom button to also work if we can do that?
	if there is a serious limitation here, then i'm fine deferring this, but if we can make that work, I'd like to

disable gesture shortcuts when rearranging right sidebar drawer tabs on mobile

gestures for switching between projects

task list - that an agent can check off and add comments too - maybe replace or supercede the observability inbox

- custom tasks - not just vscode tasks. probably good to just not rely on vscode conventions while still allowing importing of them though, but you can just define ur own swemux scripts for a project. agents can do that easily once the swemux mcp is up and can be queried for swe-mux development type info

- command rail quick-model changes


- should be able to resume sessions even when they weren't run in swe-mux
	
	
- can i tie certain claude/codex accounts to specific browsers and/or specific chrome profiles? so when it asks to auth, it only opens those browsers/profiles?

- allow users to select their text renderer from options (continuity, etc)


- preview pane - allow seeing the dev console logs and entering dev console commands



- setting a session to "auto-approve requests" - the harness will then automatically approve requests - basically "dangerously approve permissions" but at the control plane level

- Continuity-related Updates
	- continuity embedded: easy text search feature on command rail and gesture trigger (also hotkey ctrl+f on desktop). just for the currently focused window/note
	
	- continuity mobile embedded: any way to bring back the mobile drag handles on highlight select?
	
	- markdown horizontal breaks arent rendering, `---`



- ability to click file paths from agent chats and they open

- maybe congifurable gesture zones? swipe left top half of screen does one thing, swipe left bottom half of screen does another
	- what other ways to expose more UI in a clean and intuitive way?
	- and not just on mobile/gestures?
	- 2 finger drag down and up? would this be fine and 1 finger would still work for scroll?
	

- allow making command rail be multiple rows? on mobile and/or desktop? and configuring what is on each row


- [ ] control plane updates
- [ ] the remaining roadmap updates


ability to tap, on mobile in agent sessions, to a specific part of your chat input (this doesn't work on native codex on desktop, but it works on claude code.. hmm). i wonder if we can just make it work foe both? to move the carat to wherever you tap or click in your currently being typed message in these agent cli sessions?


on mobile agent sessions:
	want to fix tap and hold and drag highlighting not allowing you to drag if content is off of the current screen (somehow triggeting scroll while also highlighting)
		pretty hard problem probably. evaluate and discuss if this is reasonably feasible

agents with swemux mcp send tasks to other projects (swe-mux sending a task to continuity)

scheduled agent runs, and repeated ones on a schedule

jump to previous user messages in agent chats

- speak system updates:
	- user configurable trigger word + variants		- replace "mux" with "swe"?

	- expanding the voice system for use navigating swe-mux, going to open/active sessions, starting new sessions, etc.

  - Mux, send / submit — submits buffered speech.
  - Mux, cancel / clear — clears the entire draft.
  - Mux, undo / delete last phrase — removes the latest dictated chunk.
  - Mux, mute / stop speaking — stops playback but keeps listening.
  - Mux, read reply — reads the agent’s latest reply.
  - Mux, summary mode — switches spoken replies to summaries.
  - Mux, verbatim mode — reads replies verbatim.
  - Mux, interrupt — stops playback and sends Ctrl-C to the agent.
  - Mux, help / list commands — displays the command list.
  - Mux, stop listening / sleep — turns Conversation mode off.



knowledge graph building?

memory building?

- a combined ledger that agents can be made aware of
	- how made aware?
	
	- that they can refer to. will help them with parallel work, provenance, decisions, etc.
	
can capture your entire update prompt and traversal timeline when parsing all your session transcripts and tagging them all


this could heavily encourage you giving positive feedback to the models
	"k x works, y works, z works"  just little notes you know will get logged and filed away


test swe-mux on CMR laptop. install tailscale there. see what needs to be hardened for another user/system to use it





- is it possible to detatch a session from swe-mux into an external terminal?
	- i could always resume of course but just wondering if there's a seamless way to do thsi that wouldnt break the claude code caching or whatever - or that could keep the process continuous?
		- probably not?



- openrouter call - can i set this to go through my generative gateway instead of openrouter? while still leaving openrouter routing open for other people that use this and done have generative gateway?


- optional resumption of any chat sessions that were open when you closed swe-mux
	- it starts UI and says x,y,z were open, do you want to reopen them? and you can say yes to all, not to all, or check the ones you watn to re-open




git/source control pane

git commit message generation - based on changes and session ssince that change and the annotations since that change
	and eventually when we build in source control, you can hti a button next to the commit message and it will generaet that based upon the knowledge we have accumulated already (if hte project ahs those automations enabled)
	THIS UPDATE SHOULD LEVERAGE THE CONTROL PLANE UPDATES

	

- getting STT global and actually doing other things in swe-mux UI with it
	- To get your desired "talk is one global thing that follows the focused session, without bleeding":
	
	- Lift talk to a single app-level controller. Move the mic/capture ownership out of the per-pane ConversationControl into one App-level instance that targets activeId dynamically. This is the core change and it's what makes talk survive mobile tab switches (the per-pane component can't, because its pane unmounts). The mutex/claim machinery then goes away — there's structurally one mic.
	- On focus switch while talk is live: finalize-then-retarget. When activeId changes, commit the current buffer against the origin session (either auto-submit or clear — I'd clear and show a brief "buffer dropped on session switch" note rather than silently submitting a half-formed thought), then rebind the target to the new focused session with an empty buffer. This is what guarantees no bleed: a buffer is never carried across the switch.
	- Decouple talk from persisted TTS. Stop force-writing voice_mode='auto' (line 154). Instead, if the focused session's TTS is off, drive playback with a transient in-memory "talk is active" flag for that session, and restore on talk-off. Otherwise turning talk on permanently mutates the per-session TTS you just told me you want independent.
	- Persist the talk on/off intent globally (a single client-side or per-user flag), so "talk is on" is a property of the workspace, not of whichever pane happened to own it — matching your mental model.
	
	- Scope: (1) and (2) are the real work (one refactor of ConversationControl into an App-level singleton plus a focus-change effect). (3) and (4) are small. Want me to write this up as a concrete implementation plan, or start with just the mobile "talk follows the focused session" behavior since that's the most visible gap?



- Transcript-first agent view
	- Claude/Codex sessions should eventually have a clean message transcript and native multiline composer by default, with “Live terminal” as a toggle. Raw terminal streaming remains invaluable, but it should not be the primary phone interface.

	- something like this in it:
		- a heatmap/sidebar for agent chats for jumping around to user/agent replies quickly
			- maintained by the system, overlaid on window or something
			- also able to selectively copy them to clipboard without even jumping to them or having to do `/copy`
			- probably a local processing of raw transcripts involved to make this possible and actually performant?

- AMBIENT AGENT IDEAS
	- Monitor the active transcript at intervals - giving a running status of what it is actually doing
		- The agent would need to hold a bit of context, but it just needs to give a small annotation on what is happening. Specifically what the agent is chasing down in that moment, tracing its path as it does different things and WHY it does them

- Future:
	- schedule new sessions and messages for a space/project. cron schedule or one-offs, etc

	- opencode integration

	- redo tutorial + mobile tutorial


Update message for continuing implementation of control plane automations and control plane roadmap. outdated now a bit?
 ```
  # Handoff: swe-mux control-plane development — implement Step 3 (deterministic consumers)

  You are taking over development of **swe-mux**, a Windows-native, browser-based terminal multiplexer for long-lived Claude Code / Codex / PowerShell sessions. The `muxd` aiohttp daemon owns every ConPTY; a Preact frontend (xterm.js, no React) talks to it over HTTP/WS. Backend is ~50 Python modules under `src/swe_mux/`; frontend under `frontend/src/`. Tests are pytest (`asyncio_mode = "auto"`), frontend uses `npx tsc --noEmit` + `npm run build`. CI gates: `uv run ruff check`, `uv run mypy`, `uv run pytest`.

  ## Read these first
  - **`.docs/development/CONTROL_PLANE_ROADMAP.md`** — the authoritative plan. START AT §9 (the status checklist). Steps 0–2 are done; you are implementing **Step 3 · Deterministic consumers**. Also read §5 (substrate), §6.1/6.3/6.4/6.5 (the four consumers), §8 (enablement), §2 (evidence base / rationale).
  - **`.docs/CLAUDE.md`** — doc routing table. It maps change-types → which docs to update. FOLLOW IT: any change touching these areas requires updating the named design docs.
  - **`.docs/design/features/tier0-facts.md`** and **`automation-enablement.md`** — the substrate you'll read from.

  ## What this session accomplished (all shipped: code + tests + docs agree)
  - **Step 0 — Enablement framework.** `src/swe_mux/automation_registry.py` (the dependency DAG: substrate/consumer registry, cycle-checked, `resolve()` → enabled/blocked). Per-project opt-in lives in `.swe-mux/config.toml` under `automations = { id = bool }` (parse/serialize/validate in `src/swe_mux/project_files.py`, helper `project_automations()`). Tests: `tests/test_control_plane_enablement.py`.
  - **Step 1 — Tier 0 substrate.** `src/swe_mux/tier0_store.py` — deterministic fact capture on shared `mux.db`, table `tier0_facts` (id, session_id, agent_run_id, project_id, kind, target, content_hash, fingerprint, detail_json, **source_seq** [pointer into event log], source_ref, created_at). Gated per-project (only captures if the owning project opted `tier0` in; resolver `tier0_enabled` wired in `server.py`, 5s TTL cache, off-loop). Race-free content hashing + normalized target extracted AT THE ADAPTER BOUNDARY in `src/swe_mux/observation.py` (`tool_call_evidence()`, wired into the Claude and Codex `tool_use` emit sites). The `fingerprint` folds in content_hash so identical repeated edits share a fingerprint (loop signal). Store follows the codebase's standard pattern: single-worker ThreadPoolExecutor + `sqlite_store` operation coordinator; failures can never break the event loop.
  - **Step 2 — helps-today siblings.** (a) **Observation inbox**: `.swe-mux/observations.json` store (project_files.py: read/append/write_observations), endpoints `d}/observations`, UI`frontend/src/Observations.tsx` (command `observations.open`, in App menu). (b) **Preview screenshot capture**: `src/swe_mux/preview_capture.py` (optional Playwright, `preview-capture` extra), endpoint `POST /api/previews/{id}/capture`, saves PNG into `<project>/.swe-mux/preview-shots/`, copies a reference to clipboard (reuses `copyPreparedText` + `.prepared-clip + drag-region capture in`frontend/src/PreviewPane.tsx`. Playwright + Chromium are installed locally; `PLAYWRIGHT_BROWSERS_PATH` user env var is set for the frozen desktop build.
  - Also this session: reworked the Automation modal UI (three-group nav Configure/Attend/Review + `?` help modal +  demoted diagnostics) in `frontend/src/Automaumentation` (created`features/tier0-facts.md`, `automation-enablement.md`, `observations.md`; patched interfaces/data-model/00_OVERVIEW/processes-and-previews/automation/CLAUDE.md/technical-packages); renamed the ideas doc → `CONTROL_PLANE_ROADMAP.md` with a completion checklist.

  ## Your task: implement Step 3 — deterministic consumers (no model, best value-to-risk)
  Build order (per roadmap §9, easiest/highest-value first):
  1. **Loop/stall (deterministic half)** (§6.4) — pure query over `tier0_facts` fingerprints: fire when a fingerprint repeats ≥3× AND a no-progress gate holds (failing-test set didn't shrink / no new diagnostic / no target-relevant diff). Hybrid, gated on progress — do NOT fire on semantic similarity alone. Also add premature-termination detection (turn ended + completion claim + open todos + no verification).
  2. **Declared-vs-verified** (§6.3) — Tier 0 test-result facts (`tool_result` success/exit already captured) + a completion-claim regex on assistant text. Keep "declared done", "tests passed", "actually correct" as three separate facts. Never collapse to a single ✓.                                                                               3. **Doc-debt ledger** (§6.5) — Tier 0 filesDE.md` routing entries → mark docs dirtywith a diff pointer. Accumulate a ledger; DON'T nag per-turn. Cheapest in this repo (the routing table is a literal lookup).
  4. **Provenance graph** (§6.1) — **BLOCKED** on a Step 1 GAP: Tier 0 captures write-side content hashes but NOT git commit/tree hashes or read-side file hashes (`git_changed` only carries branch/dirty/ahead/behind). Extend Tier 0 capture FIRST, then build. Emit factual lineage only ("B wrote hash X; A's test ran against snapshot containing X")NEVER a causal blame label.

  ## Critical constraints (design laws — do not violate)                                                             - **Out-of-band only.** Consumers observe/ane into a PTY, approve, spawn, or mutate aproject. No `write_pty`.                                                                                           - **Deterministic detector, model describer.f. No LLM calls. Findings are deterministic.
  - **Per-project opt-in gate.** Each consumer is a registry id in `automation_registry.py` with `requires: ["tier0"]` (already registered: `loop_detection`, `declared_vs_verified`, `doc_debt`, `provenance_graph`). A consumer must only run for a session whose project opted it in — mirror the `tier0_enabled` gate resolver in `server.py`.
  - **Display surface = the existing `annotations` table.** Consumers WRITE annotations; the UI renders them. Do NOT invent a new surface. (The attention-rankingthem is Step 7 — not yet built.)
  - **Everything off the event loop**, behind the shared SQLite operation coordinator. Optional/degraded paths fail closed and never break terminals.

  ## Watch out for
  - **No opt-in UI exists yet** — enabling a consumer today = hand-editing `.swe-mux/config.toml`. Fine for building/testing.
  - **Known doc/code divergence:** `features/automation.md` describes a continuous scan-derived titler, but `automation.py` still does one-shot and `tests/test_automation_phase6.py::test_builtin_titler_reserves_one_paid_call_per_agent_run` asserts the old behavior. Don't be confused by it; it's flagged in roadmap §9 "Known gaps."
  - Study `operational_telemetry.py` for the canonical event-consuming store pattern before writing a new consumer store/worker.
  - One pre-existing test failure earlier in the working tree came from uncommitted `frontend/src/App.tsx` edits (a contract test asserting a literal string); it has since resolved. If you see it, it's not yours.

  ## Definition of done for Step 3
  Implementation + tests + docs agree, then check the boxes in `CONTROL_PLANE_ROADMAP.md` §9. Add/update the feature docs per the routing table (loop/stall, declrovenance likely warrant coverage or ashared `features/deterministic-consumers.md`). Run ruff + mypy + pytest + frontend typecheck/build green. Do NOT mark a box done from code presence alone. Git: user handles all commits/pushes — never run mutating git.
  ```