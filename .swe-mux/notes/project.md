---
swe_mux_note = 1
kind = "projects"
id = "29a044bb-a06b-4216-95e4-39c5e91d48fb"
---
# swe-mux project notes

git commit. been a while

make it easy to copy path to clipboard from the file tab when long press the file or via hover-over on desktop showing a copy icon floating above the file

agent to agent communication in swe-mux. will be nice when i can have an agent do a task and then notify another with a message(such as when continuity agent finishes an update and wants to notify a specific swe-mux agent to update the package. will this be an mcp or what? cuz it has to know what agents are active, AND it will also need to spawn new agent sessions as well sometimes

swe-mux mcp would also make it easy for agents to see previous sessions, and concurrent sessions, and that adds utility



- [ ] the remaining roadmap updates starting with phase 3.5

- [ ] the session preserving reload update

- [ ] control plane updates

allow making command rail be multiple rows? on mobile maybe? and configuring what is on each row?


cleaning up ui of tts/speak functionality

ability to tap, on mobile, to a specific part of your chat input (this doesn't work on native codex on desktop, but it works on claude code.. hmm). i wonder if we can just make it work foe both? to move the carat to wherever you tap or click in your currently being typed message in these agent cli sessions?


on mobile agent sessions:
	want to fix tap and hold and drag highlighting not allowing you to drag if content is off of the current screen (somehow triggeting scroll while also highlighting)
		pretty hard problem probably. evaluate and discuss if this is reasonably feasible


- command rail functionality expansion
	- at end of command rail for agent sessions - a settings gear icon for configuring the rail options and ordering - probably in settings UI? since this would have to be configureble to be different for mobile vs desktop and also codex vs claude?

	- save project skills to the project's command rail defaults. basically configurable command rails. globally and per-project. and also per-platform (desktop or mobile). logical defaults so if you add a skill to the rail, or a command, it adds to both desktop/mobile. id want to add /new, and thrn any slash command i want .  this also needs to have toggles for claude vs codex, and inject them properly. on codex it has some slash commands, but skills are invoked with $.   so all this should be cleanly handled.
		- note: some command rail options are already specific to mobile, so make sure that stuff is maintained and extended into this new system's setup/defaults 


	- adding /branch to command rail, But also: handling it smarter. it should open the new session too when you do it. so youll have the original and branched convos open now. clsude is /branch. does codex have branch? if not, could we simulate our own branch functionality for it?

	- adding home/end and ctrl+home/ctrl+end to command rail

	- adding newline to commsnd rail. and button to clear current input. and button to clear AND copy current input

	- commands: revert to last reply (no code changes)





a command rail on the project and session notes panes, or on any file viewing pane really:
	create new codex/claude session and send selection
	queue message (once i build in the queue) - must pick a specific session
	send after delay - must pick a specific session and a delay amount
		maybe this and/or a schedule send to schedule something automatically


scheduled agent runs, and repeated ones on a schedule


- speak system updates:
	- user configurable trigger word + variants		- replace "mux" with "swe"?

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

a combined ledger that agents can be made aware of
	how made aware?
	
	that they can refer to. will help them with parallel work, provenance, decisions, etc.
	
can capture your entire update prompt and traversal timeline when parsing all your session transcripts and tagging them all


this could heavily encourage you giving positive feedback to the models
	"k x works, y works, z works"  just little notes you know will get logged and filed away


test swe-mux on CMR laptop. install tailscale there. see what needs to be hardened for another user/system to use it


Update for implementing control plaane automations and control plane roadmap
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