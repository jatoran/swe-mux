# Spaces

## What it is

- Persistent named session groups organized with sessions in the sidebar tree.

## Key concepts

- Default space: stable ID `default`; cannot be deleted.
- Layout: persisted JSON pane membership, with up to four explicit panes, replacement,
  detach, and temporary zoom.

## Operations

- Create assigns a UUID and next position.
- Rename/defaults/layout update through one patch operation.
- Delete rejects `default` and spaces containing live sessions.

## Key files

- Manager: `src/swe_mux/spaces.py`
- Persistence: `src/swe_mux/history.py`
- Sidebar tree and pane membership: `frontend/src/App.tsx`
