# Observation inbox

## What it is

A per-Project capture surface for quick notes-to-self dropped while testing, with no AI.
Capture now without switching to the agent, then hand the batch over in one insert. A
project-owned resource file. Vision: `../../development/CONTROL_PLANE_ROADMAP.md` §6.9.

## Key concepts

- **Observation**: `{ id, body, done, created_at }`. Append-only capture; edits (toggle
  done, delete, reorder) replace the whole list under a revision check.
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
- The frontend reuses the terminal clipboard fallback: desktop copies silently, mobile /
  insecure contexts show the manual-copy overlay.

## API surface

```text
GET  /api/projects/{project_id}/observations
POST /api/projects/{project_id}/observations   {body}          append one
PUT  /api/projects/{project_id}/observations   {observations, revision}   replace
```

## Configuration

- Consumer id `observation_inbox` (no substrate deps). See `automation-enablement.md`.

## Key files

- Store (read/append/write, validation): `src/swe_mux/project_files.py`
- Endpoints: `src/swe_mux/server.py`
- UI (capture + list + batch/copy): `frontend/src/Observations.tsx`
- Command/menu wiring: `frontend/src/App.tsx` (`observations.open`)

## Relates to

- `project-resources.md` — the `.swe-mux/` project-owned resource model.
- `prompt-library.md` — the same insert-never-submit composer contract.
