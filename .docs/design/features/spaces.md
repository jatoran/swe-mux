# Spaces

## What it is

- Persistent named session groups organized with sessions in the sidebar tree.

## Key concepts

- Default space: stable ID `default`; cannot be deleted.
- Layout: persisted version-2 recursive tiling tree with optimistic revision. Leaf kinds
  are `terminal`, `note`, and `preview`; split nodes retain direction and ratio. Version-1
  pane arrays migrate on read. Validation caps trees at 64 leaves and depth 24.

## Operations

- Create assigns a UUID and next position.
- Rename/defaults/layout update through one patch operation.
- `default_profile_id` and `default_cwd` override global terminal defaults for new shell
  sessions; explicit spawn request values retain priority.
- Concurrent layout patches supply `layout_revision`; stale updates are rejected instead
  of silently overwriting another browser.
- Browser reconnect and daemon restart restore the tree; missing/exited terminal leaves
  collapse predictably without terminating displaced or detached live sessions.
- Note leaves use stable scope/resource IDs and share the same optimistic layout revision,
  split-ratio persistence, detach, and collapse behavior as other leaves. Removing a note
  leaf never deletes its project-local Markdown.
- Delete rejects `default`. A non-default space with live sessions requires an explicit
  atomic disposition: move them to another existing space or kill them. Ended history
  records are rehomed to `default`, so no record retains a deleted space reference.

## Key files

- Manager: `src/swe_mux/spaces.py`
- Persistence: `src/swe_mux/history.py`
- Layout schema/validation: `src/swe_mux/layouts.py`
- Sidebar tree and recursive renderer: `frontend/src/App.tsx`, `frontend/src/layout.ts`
