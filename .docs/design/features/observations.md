# Observation inbox

## What it is

A per-Project capture surface for quick notes-to-self dropped while testing, with no AI.
Capture now without switching to the agent, then hand the batch over in one insert. A
project-owned resource file. Vision: `../../development/CONTROL_PLANE_ROADMAP.md` §6.9.

## Key concepts

- **Observation**: `{ id, body, done, created_at }`. Append-only capture; edits (toggle
  done, delete, reorder) replace the whole list under a revision check.
- **Typed request** (Phase 5): an item may also carry `kind: "spawn_request"` and a
  `request` payload (`prompt`, `backend`, `name`, `reason`, `cwd`, calling-session
  provenance, `status`). Written by `mux.requestSpawn` and inert by construction — it is
  text in the user's own file until a human decides. Approving it spawns through the
  ordinary spawn path with the prompt as `seed_text`; dismissing marks it decided. A
  request can be decided once (`already_decided` afterwards), and typed requests never join
  the "insert open items into agent" batch — they are decisions, not notes. See
  `agent-messaging.md`.
- **Batch handoff**: open (not-done) items are inserted into the focused agent's composer
  (terminal paste semantics, never submitted) or copied to the clipboard.
- **Entry point**: a Project's own context menu (sidebar right-click, or the mobile top-bar
  Project title) under `BROWSE THIS PROJECT`. The inbox carries the Project it was opened for
  rather than following the active one, so it cannot drift onto a different Project's file. It
  is not in the app menu: the app menu holds nothing that acts on a single Project.

## Data model

- File `<project>/.swe-mux/observations.json`: `{ version, observations: [...] }`.
  Bounded: at most 500 items, 2000 characters each. Ids are short safe tokens, unique.
- Not stored in SQLite — an ordinary project-owned file that outlives sessions.

## Operations

- Append is conflict-free (no revision check); full replace is revision-checked and
  returns `409 revision_conflict` on staleness.
- "Missing" and "unparseable" are different answers. A file that exists but does not parse
  (a hand edit, merge-conflict markers) reports `status: "malformed"` and refuses both
  writes with `409 observations_unreadable`. Reading it as an empty list meant the very next
  captured note rewrote the file with a single item and silently destroyed every prior
  observation — in a file whose whole content is the user's own notes.
- The frontend reuses the terminal clipboard fallback: desktop copies silently, mobile /
  insecure contexts show the manual-copy overlay.

## API surface

```text
GET  /api/projects/{project_id}/observations
POST /api/projects/{project_id}/observations   {body}          append one
PUT  /api/projects/{project_id}/observations   {observations, revision}   replace
POST /api/projects/{project_id}/observations/{observation_id}/decide
                                               {decision: approve|dismiss, …overrides}
```

The decision route records its own outcome without a revision check: it is the daemon
writing the result of an act it just performed, and losing that to a concurrent edit of an
unrelated note would leave a started session looking like a pending request.

## Configuration

- Consumer id `observation_inbox` (no substrate deps). See `automation-enablement.md`.

## Key files

- Store (read/append/write, validation, typed requests): `src/swe_mux/project_files.py`
- Endpoints (including the spawn-request decision): `src/swe_mux/server.py`
- Drafting side: `src/swe_mux/agent_messaging.py`
- UI (capture + list + batch/copy + request approval): `frontend/src/Observations.tsx`
- Command/menu wiring: `frontend/src/App.tsx` (`observations.open`)

## Relates to

- `project-resources.md` — the `.swe-mux/` project-owned resource model.
- `prompt-library.md` — the same insert-never-submit composer contract.
- `agent-messaging.md` — `mux.requestSpawn`, the drafts it writes here, and why spawn
  authority stays with the human.
