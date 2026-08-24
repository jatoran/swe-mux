# Projects and Groups

## What it is

Projects are the durable catalog of canonical folders swe-mux can own. The Projects manager
owns that catalog; the sidebar is a filtered active-navigation view of it. Optional Groups and
persisted ordering organize Project rows without acquiring behavioral ownership.

## Ownership model

- A Project has a stable ID, user-controlled name, one canonical existing folder, optional
  Group, persisted position, registration date, and `sidebar_visible` state.
- A Project is the only container for sessions, layout, notes, file resources, Project options,
  and Project-scoped history work. A session's `project_id` is immutable.
- Every spawn defaults to the canonical Project root.
  An explicit spawn may use a contained subdirectory or an exact Git-listed worktree root of the same repository.
  Later `cd` commands update runtime/Git telemetry only and never retarget ownership.
- A Group owns a name, order, and Project membership only. It never owns a folder, layout,
  session, note, setting, or behavior.
- Git scopes, repository IDs, and repository groups are derived metadata. They cannot replace
  or retarget the explicit Project.

## Project registry and sidebar

- The Projects manager lists every configured Project and is the only catalog-management UI.
  It can add, rename, group, open, show/hide, configure, and remove registrations from swe-mux.
- It is also the **only per-Project editor**. Its detail pane has two tabs: `Details` (name,
  folder, group, sidebar visibility, removal) and `Settings` (default backend, shell launch
  profile, one agent launch profile per harness that has one,
  prompt-library scope, notification sounds, additive ignore patterns). Settings holds global
  options exclusively and has no per-Project section; `Project settings…` on a Project's context
  menu opens this registry preselected to that Project's Settings tab (`project.settings`).
  Splitting these across two modals meant three doors to two overlapping surfaces, one of which
  bounced the user back out to the other.
- Both storage layers are edited in one form. A dual-layer field (backend, shell launch
  profile, each agent launch profile) shows
  one control plus a `this device` / `repo` selector saying where the value is stored; writing a
  value always clears the other layer, since a stale database override would silently outrank
  the value just chosen. Repo-only fields carry a static `repo` chip. Users pick a value and a
  home for it, never a layer to hunt through.
- Hiding a Project removes it from desktop/mobile navigation and numeric Project shortcuts. It
  preserves the registration, `.swe-mux/` content, layout, history, settings, and live sessions.
- The sidebar renders only visible Projects.
  Ungrouped Projects are root rows under the global `PROJECTS` header; explicit Groups render as named sections among them.
  `PROJECTS` is navigation chrome, not a synthetic Group, and cannot fold or move among Groups.
- **Under Manual order, Groups render after every root Project; under every other mode a Group is a peer of a root Project and sorts in among them.**
  Groups were always a block below the entire ungrouped list, ordered by a setting of their own, which meant no mode could lift a Group for the work inside it: under Recently used, a Group holding the last minute's work still sat beneath root Projects that had never been opened.
  Placement, not a better Group key, is what fixes that, so Group placement collapsed into the one Project sort mode.
  Manual keeps the two-tier tree because hand-placed Group positions are a separate order from hand-placed Project positions, with no single key to interleave them by.
  Interleaving splits the root into runs, and each run is its own list element, so a Project dropped between two Groups resolves to the root rather than to whichever Group it landed nearest.
- The `PROJECTS` header carries four controls: fold-everything, sort, a cogwheel opening the
  Projects registry, and `+` opening the registry and its Add-project dialog together.
  All four act on the tree as a whole, so none of them may scroll away with the list.
  Placement, reveal-on-hover, and the icon choices are in `ui.md`.
- The `PROJECTS` header's sort control (`⇅`) offers **one flat list of modes**, with no submenu: Manual order, Recently used, Name A→Z / Z→A, Newest / Oldest first.
  The chosen mode orders root Projects, the Projects inside every Group, and the Groups themselves among the root Projects.
  It replaced a nested `Sort Groups` submenu, which could only order Groups among Groups and so could not answer the question it was asked; a mismatched pair of modes was possible there and read as arbitrary.
- Project sorting is **one global mode**.
  It was per section originally, on the argument that a hand-arranged shortlist and a long alphabetical pile are both legitimate; that put a `⇅` on every Group header for a preference set the same everywhere, so the modes collapsed into one and the control moved off the headers.
  A device upgrading keeps whichever mode it had actually set (see `loadSidebarOrder`), rather than being silently reset to Manual: the retired per-section map is read first, then the retired Group-only mode, on the reasoning that a device with both was ordering its Projects by the Project setting.
  Neither retired key is written back, so each migration fires exactly once per device.
  The sort-mode selection is a device-local presentation preference.
- The same header's fold control folds or unfolds **every Project row and every Group**
  at once, so tidying a long sidebar is one click rather than one per row. It offers Expand only
  once nothing on screen is folded open; expanding clears the stored fold lists outright rather
  than subtracting the visible ids, so an id left behind by something hidden or deleted while
  folded cannot survive and re-fold it later. Only what is on screen is folded — a Project hidden
  from the sidebar has no row to collapse.
- **A Group is placed by the member that leads it under the active mode**, since a Group record itself holds only a name and a position and nothing dates it.
  Recently used takes the most recent explicit user action in it, so a Group ranks on the work the user initiated rather than its age.
  Newest first takes its newest Project and Oldest first its oldest, each ignoring undated members: a single Project with no recorded registration date must not pull a Group of old Projects to the bottom of Oldest first.
  Name modes compare the Group's own name against the Project names it sits among.
  A Group with nothing measurable in it reads as unmeasured and sorts last in either direction.
  Groups previously had no date modes at all, because their own setting had no key to offer them.
- Manual order is the default and the tie-break for every mode, so a sort never discards the
  arrangement underneath it.
  Root Projects precede Groups in that baseline, which is what an interleaving sort falls back to when two entries are equally unmeasured.
  Placing a Project or a Group by hand returns the sidebar to Manual and writes the order that was on screen, so the move survives instead of being re-sorted away.
  From a sorted tree that also re-splits the root into two tiers, since that is what Manual means; the arrangement the drag produced survives it, because each list keeps the relative order the drop put it in.
  Desktop pointer dragging and Project-menu Move up/down use the same persisted reorder contract.
  Mobile Project rows require a 325 ms hold before pickup; movement beyond the 8 px hold slop remains sidebar scrolling and never previews a reorder.
- Desktop Group headers combine collapse and reorder: press and move reorders, while press and release folds.
  Mobile Group headers fold on tap and open the Group menu on a hold, because Project rows are the sidebar's sole mobile reorder target and a phone has no right-click to give.
  The desktop drag swallows the click it ends with, and so does the mobile hold — the hold fires under a finger that is still down, so its trailing click would otherwise fold the Group behind the menu it just opened.
  A Group drag reorders among Groups only; Group positions are their own order, so there is no slot for a Group between two root Projects to write.
  Collapsing is presentation only: the folded Projects keep their place in the collapsed rail, numbered shortcuts, and every order.
- **A Project drag crosses Group boundaries.** It resolves its target from the pointer across the
  whole tree rather than being confined to the list it started in, so the one gesture both reorders
  within a section and moves a Project between sections. Which list it landed in is read off that
  list's `data-group-id`; the ungrouped root list carries the empty string.
  It was confined to its own list originally, on the argument that Group membership is an explicit
  decision rather than a drop side effect. In practice a Group could only be filled from a menu two
  levels away from the tree it rearranges, which is the surface the decision is actually made on.
  The Project menu's Group control and the registry's Group field remain as the explicit routes.
  The menu control is now a scrolling pop-out submenu rather than a native `<select>`, listing Ungrouped, every Group, and `Create new group` - which mints the Group *and* moves this Project into it, since a create that left the Project where it was would be a detour to the sidebar menu (`ui.md` § Menus and overlays).
- A drop commits **two writes, in this order**: `PATCH /api/projects/{id}` for the Group, then
  `PUT /api/projects/order` for the position. The reorder is validated against the positions it was
  planned from, and a Group write changes none of them, so the order is safe either way round.
  A drag that never left its Group sends only the reorder; one that only changed Group sends only
  the `PATCH`, because the ordering call returns early on an unchanged order — before it would
  demote the sort to Manual.
- A list with no row to sit beside — an empty Group, a folded one, or the root with every Project
  grouped — is a drop target in its own right, outlined whole rather than shown an insertion line
  there is no gap to draw. The Project keeps its slot in the global position order, since there is
  no sibling in there to sit before or after.
- Releasing outside every list commits nothing. The pointer must be inside a list's box or within
  `DROP_LIST_MARGIN` of one — which is what makes the seams between sections droppable — and a miss
  resets the plan to the baseline rather than leaving the last hovered target armed. With a Group
  change riding on the same gesture, a stray release must not reassign a Project.
- Mobile Project pickup closes open menus, gives short haptic feedback, previews the landing slot, and edge-scrolls the tree until release.
  Cancellation preserves the original order and Group.
- Mobile Project rows expose `⋮` immediately left of Run for the Project context menu.
  Project long-press is reserved for reorder; desktop right-click remains the pointer context-menu route.
  Mobile session long-press remains context-menu-only and never starts sidebar grouping or reorder.
- A Group header's only button is `✎` (rename), revealed on hover over that header (and on
  keyboard focus; touch always shows it, having no hover). It carried a `×` that deleted the Group and
  ungrouped its Projects; that sat a pixel from the fold toggle and dissolved a Group on a stray
  click, so no header button deletes a Group.
- **A Group has its own context menu**, opened by right-clicking anywhere in its section that is not a Project or session row, or by holding its header on mobile.
  It carries exactly three items: `Rename group…`, `Collapse group` / `Expand group`, and `Delete group…`.
  The first two mirror the header's `✎` and its fold click, so the menu is a second route to them rather than the only one; delete has no other home, which is why the menu exists.
  Removing the header `×` had left no delete path at all, and emptying a Group instead does not remove it, because empty Groups render.
- Deleting a Group is **two clicks in that menu**, and the confirm step states what survives before offering the button: the Projects return to the root list, and no folder, session, layout, or history is touched.
  An armed confirm is cleared whenever the menu opens, so it cannot be inherited by the next Group right-clicked.
  The optimistic update ungroups the Projects locally and a failure re-reads the registry rather than reconstructing what the daemon did.
  An unknown Group id is a request error rather than a server fault on both `DELETE` and `PATCH`, because either menu may have been drawn before another device deleted the Group.
- **A Group renders whether or not it holds anything.** An empty one shows its header plus a
  `Drag a Project here` hint, which is also its drop target. Empty Groups were filtered out of the
  tree, so creating a Group appeared to do nothing and the only way to fill it pointed at a section
  that was not on screen. The ungrouped root list follows the same rule in reverse: it renders
  whenever any Group exists, so a user who grouped every Project can still drag one back out.
- A folded Group reports both a live-session count and the strongest agent state inside it,
  in the collapsed rail's colours. A count alone would let an agent waiting for approval
  disappear behind the fold, which is the one thing collapsing must not hide.
- A drag only ever permutes the rows on screen; a Project hidden from the sidebar keeps the slot it
  already held rather than being reshuffled by a reorder the user could not see. (Empty Groups are
  now on screen and so are ordinary drag participants; `mergeVisibleOrder` stays in the Group path
  because it is what keeps this correct if anything is ever filtered out of the tree again.)
- "Recently used" reads the daemon-persisted `ProjectRecord.last_used_at` timestamp, so mobile and desktop rank the same explicit Project use.
  The sort-mode selection remains device-local; selecting Recently used on one browser does not change another browser's selected mode.
  A successful explicit prompt submission or user-initiated session start advances the timestamp through `POST /api/projects/{project_id}/used`.
  The daemon emits `project_used` with the resulting timestamp so every connected client updates without a fleet refetch.
  Opening or focusing a Project, session, note, file, preview, Queue, or other resource never changes it.
  Agent output, state transitions, session completion, session removal, history timestamps, and background automation never change it.
  A Project without a recorded action is unmeasured and retains manual-order tie-breaking.
  The timestamp survives browser storage loss, a daemon restart, and a desktop redeploy.
  Existing databases seed the new field from the latest non-imported session start because no older exact prompt-submit record exists.
- Device-local Group fold state is pruned against the registered Groups, so a Group id that is deleted and later reused cannot inherit a fold the user never applied.
  The prune is suppressed until the Group registry has actually loaded, for the same reason.
- Creating a Project validates the root and initializes `.swe-mux/config.toml` plus
  `.swe-mux/notes/project.md`. The registration is not inferred from Git or current cwd.
- Add project has two modes of one form: register a folder that exists, or create a new folder
  inside an existing parent. Create mode makes exactly one directory, so a mistyped deep path is
  an error rather than a silently materialized tree, and the duplicate-root and group checks run
  before anything is created. Two dialogs were rejected because each would need its own copy of
  the setup-command list below.
  The new folder's leaf is validated server-side under the shared Windows-safe leaf rules
  (`leaf_names.py`), because the dialog's folder field is free text and not every create path
  has a dialog; adopting an existing folder skips the leaf check.
- **A successful creation lands in the new Project's workspace** (operator decision 2026-08-22).
  It selects the Project, closes the create form, closes Manage projects, and closes the mobile
  sidebar. The sidebar's `+` opens Manage projects only as a backdrop for the form, so submitting
  used to reveal the settings editor for a Project nobody has looked at yet - a configuration
  screen offered before the thing being configured has been seen. The registry stays one click
  away in the sidebar, and the guided tour's own next step reopens it by name when it wants it.
- The Mux assistant's `create_project` tool is a second entry point into the same create flow,
  constrained to name-only: the folder leaf is derived from the spoken name and the parent is
  always the configured `new_project_parent` (Settings → Projects), never a model-supplied
  path. Empty disables it and the tool's refusal names the setting; the dialog is unaffected
  and may name any parent. Details and the trust classification: `assistant.md`.
- Removing a Project from swe-mux is a registration operation, not filesystem deletion.
  It requires every live session to be closed, removes the Project from the active registry, and never deletes or recreates the canonical folder or `.swe-mux/` content.
  Historical conversations, device settings, layout, name, and canonical root remain attached to a tombstoned Project identity in SQLite.
  History renders that identity as removed rather than unassigned.
  Registering the same canonical root later restores the original Project ID, retained settings, layout, and history while applying the newly submitted name and Group.
  Historical sessions never block removal.
  A missing canonical root is exposed as `root_available=false`; Project resource reads must report or render unavailability without creating any directory.
  Deleting a Group ungroups its active Projects and changes nothing else.

## Project setup commands

Add project offers the user's own setup commands as checkboxes, unchecked unless their
definition opts in. They are defined in Settings → General, stored in the machine-local daemon
config (`project_init_scripts`: `id`, `label`, `command`, `default_enabled`), and never read
from a folder. There is no built-in scaffolding matrix — git, a workspace file, a virtualenv,
and anything else are whatever the user writes in a command.

Each selected command becomes one ordinary one-shot shell terminal owned by the new Project and
rooted at its canonical folder, started in configured order. Start order is all that is
promised: a step that must finish before the next begins belongs in the same command, using the
shell's own `&&` or `;`. The registration is already durable when they run, so a command that
fails to launch is reported and never unwinds the Project.

This is not an exception to the ordinary repository-configuration boundary. A Project Action is imported
from the checkout and therefore needs exact-content approval; a setup command is typed by the
user into their own settings, so authoring it is the authorization. Execution reuses the Project
Action spawn contract, and nothing here reads repository content.

## Configuration boundary

Project-owned `.swe-mux/config.toml` is versioned and permits typed portable options:
`default_shell_profile`, `default_agent_profiles`, `preferred_backend`, `prompt_library_scope`,
`notification_sounds_enabled`, additive `ignore_patterns`, and the narrow `[worktree].setup_command` launch hook.
Effective precedence is explicit
request where supported, database Project override, portable Project value, then global default.

`default_agent_profiles` is a **selection, not a definition**: it names a launch profile the
user authored on this machine and carries no argv of its own.
Argv for an agent CLI is an authority field, so repository-supplied argv would be an
escalation, while naming a locally-authored profile is the same kind of statement
`preferred_backend` already makes (`launch-profiles.md`).

`interject_grant` (`off` by default, or `granted`) is the Project's standing permission for a
peer agent to have a message delivered into a *running* turn here rather than at the next
prompt (`agent-messaging.md`).
It is its own field rather than a level of `session_control_grant` because being written to
mid-turn is a property of a working repository, and folding it into the actuation grant would
hand it to every Project that wanted interrupt/end.
It authorizes nobody to write anything by itself: the install master switch, the receiving
session's own opt-out, and the readiness tracker's mid-turn predicate all still apply.

Repository-owned `.swe-mux/config.toml` cannot authorize general commands, executables, hooks, network bindings, automatic actions, credentials, or secrets.
The sole command exception is `[worktree].setup_command`, which runs only after an explicit user create-and-spawn worktree request and before that worktree's session starts.
The Projects manager exposes it under Git and worktrees and states that it is committed executable configuration.
Blank uses an executable `.worktree-setup` convention instead.
Separately, `.vscode/tasks.json`, root
`package.json` scripts, and `.swe-mux/actions.toml` may execute only after the user selects a
Project Action and locally approves the exact current task-file fingerprint. They never execute
on discovery, Project open, or daemon startup.

`git_compare_ref` is a nullable machine-local Project-record field.
It controls Git review display comparisons and never belongs in `.swe-mux/config.toml`, because changing a review base must not dirty the repository.
`null` means Auto inference; a non-empty bounded string is an explicit ref that the Git review domain validates and resolves before use.
Resetting the Git selector to Auto persists `null` through the ordinary Project PATCH path.

## API shape

```text
GET|POST /api/projects
PATCH|DELETE /api/projects/{project_id}
PUT /api/projects/order
GET|POST /api/project-groups
PUT /api/project-groups/order
PATCH|DELETE /api/project-groups/{group_id}
GET|PUT /api/project/config?cwd=<root>
```

The registry reads and writes both layers directly: `PATCH /api/projects/{id}` for database
overrides (`default_backend`, `default_profile_id`, `default_agent_profiles`,
`git_compare_ref`, sent as `null` to clear) and `PUT /api/project/config` for the portable file.

### Writing the portable file

`.swe-mux/config.toml` has several independent writers that own disjoint keys: the registry's
own form, its automation opt-ins, its agent authority table, a grant gate
(`features/setting-links.md`), the land queue's verify command
(`features/land-queue.md`), the configurator agent (`features/configurator.md`), and the file
browser's per-Project ignore (`features/project-resources.md`).
An editor therefore writes **fields**, not the document.

`PUT /api/project/config` takes `changes` (the fields to set, `null` to remove one) together
with `base` (what the caller believed those same fields held).
A field whose stored value has moved away from `base` is refused by name with `409
revision_conflict`, carrying `conflicts` and the current file as `current`; a field nobody
else touched is applied whatever else changed in the file.
`base` is required whenever `changes` is sent, because defaulting it to "no base" would turn
the guard off for whoever omitted it.

`values` with `revision` remains for the whole-document write: it replaces the file under a
whole-file content digest, which is the right guard for a caller that reads and writes in one
breath, and is what an older client sends.
It is the wrong guard for a caller holding a cached read across an editing session, where a
whole-file digest reports every other writer as a collision with every field - the failure
that reached operators as "project config changed externally; reload before saving" on their
own second edit, recoverable only by closing and reopening the panel.

The registry holds **one** copy of the file for the whole panel and refreshes it on the
daemon's `project_configuration_changed` broadcast, so an edit made by any of the other
writers - including from another device - reaches an open panel rather than staling it.
A conflict resyncs that copy before it is reported, so the operator's next click acts on the
current file.

Project layout writes are revision checked. Whole-order reorder writes are validated as a
complete permutation of every registered Project ID and normalized transactionally. Group
reordering takes the same contract, including the `expected_order` guard that answers `409
order_conflict` when a second device already moved something — two devices each writing a full
permutation would otherwise let the loser silently win.

Project payloads carry `created_at` (registration), daemon-persisted `last_used_at` (explicit user use), derived `last_activity` (latest session activity from history), `history_count`, and `root_available`.
Time fields use epoch seconds with `0` when unknown.
`POST /api/projects` returns `restored=true` and HTTP 200 when the canonical root revives a tombstoned Project; a new registration returns `restored=false` and HTTP 201.
`DELETE /api/projects/{id}` tombstones the registration and returns `history_preserved`; a live-session conflict is HTTP 409 with `code=project_has_live_sessions` plus the blocking session identities.
`last_activity` is derived on read rather than stored, because history already dates every
session and a second write path could only drift from it.

## Key files

- `src/swe_mux/projects.py`
- `src/swe_mux/history.py`
- `src/swe_mux/project_files.py`
- `src/swe_mux/project_init.py`
- `frontend/src/ProjectsManager.tsx`
- `frontend/src/projectCreate.ts`
- `frontend/src/projectSort.ts`
- `frontend/src/App.tsx`
- `src/swe_mux/project_actions.py`

## Relates to

- `project-resources.md`: Project-owned notes, files, ignores, and watches.
- `workspace-layout.md`: Project-owned pane/tab placement.
- `sessions.md`: immutable Project ownership and spawning.
- `project-actions.md`: explicit trusted repository task execution.
