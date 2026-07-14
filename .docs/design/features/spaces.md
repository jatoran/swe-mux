# Spaces

## What it is

- A persistent, app-owned workflow group containing independently addressable sessions and
  one recursive viewport layout.
- A space is never a project container. Mixed-project membership is normal and requires no
  anchor, foreign-session warning, or artifact retargeting.

## Layout

- Layout v3 is a revisioned recursive tree. Leaves are `terminal`, `note`, or `preview`;
  horizontal/vertical splits tile leaves; a stable-ID `stack` displays ordered terminal
  leaves as tabs with one active child.
- Removing a viewport never kills its session. Killing a session removes its viewport and
  selects a surviving visible terminal without requiring refresh.
- The sidebar mirrors split/stack membership using connector rails and glyphs while each
  process remains one session row. Unpaned sessions remain visible below a dotted divider.

## Defaults and operations

- A space stores an optional `default_cwd` and `default_profile_id`. Space-level New terminal
  uses these before global defaults. A new tab or split instead receives the originating
  terminal's accepted live cwd explicitly.
- Create assigns a UUID and position. Rename/default/layout updates share the space API;
  layout writes require `layout_revision` and reject stale changes.
- Delete rejects `default`. A non-default space with live sessions requires explicit move or
  kill disposition. Its app-owned note is retained and appears as archived in the durable
  notes shelf.
- Legacy anchor database columns exist only for safe upgrade migration. They are omitted
  from API snapshots, accept no updates, influence no spawn/config/note behavior, and are
  cleared after legacy note migration.

## Key files

- Manager: `src/swe_mux/spaces.py`
- Persistence: `src/swe_mux/history.py`
- Layout schema: `src/swe_mux/layouts.py`
- Sidebar/renderer: `frontend/src/App.tsx`, `frontend/src/layout.ts`
