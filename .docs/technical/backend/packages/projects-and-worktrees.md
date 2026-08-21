# Backend: Projects, files, worktrees, and Project context

Index: `../packages.md`.
Design: `../../../design/features/projects.md`, `../../../design/features/project-resources.md`, `../../../design/features/project-actions.md`, `../../../design/features/agent-context.md`, `../../../design/features/project-card.md`.

Each entry lists what the module owns, then **Not:** what it deliberately does not.

## Projects and naming

### `projects.py`

Project and Group validation and lifecycle, including tombstoned removal, same-root identity restoration, monotone shared explicit-use recency, and leaf-validated create-mode folder minting.

**Not:** Git-derived identity, or file content.

### `leaf_names.py`

The shared Windows-safe filesystem-leaf validator - invalid characters, reserved device stems, caller-supplied control-directory refusals - and the deterministic name-to-folder normalization mirrored by the frontend's `suggestFolderName`.

**Not:** path containment (`path_identity.py`), or any filesystem access.

## Files, notes, and watches

### `project_files.py`

- Safe Project config, and missing-root read safety.
- Flat Project-note collection, legacy-note migration, and lazy global Scratchpad storage.
- Recoverable note deletion into `notes/trash/`, plus the last-note `ProjectNoteProtected` refusal.
- The tree, exclusive leaf-only file and folder creation, and bounded recursive name and content search.
- Revision-checked text reads and writes.
- The bounded validator behind `POST /notes/save-loop-diagnostic`, which logs one browser-reported
  note save loop at WARNING.
  The daemon cannot detect the episode itself: every write in it is individually legitimate, and
  only the browser knows whether a human touched the note (`noteEditGuard.ts`).
- Allowlisted image inspection and content, with byte, dimension, pixel, and frame limits.

`read_project_config_values` is the named accessor for the *values* rather than the envelope.
Handing a consumer the envelope reads as "this Project configured nothing" with no symptom, which is how `[worktree] verify_command` came to be inert for the land gate.

Once a route has resolved an explicit Project, these helpers must receive that canonical identity - `_registered_identity(project)` into the `project=` keyword.
Git discovery answers "which worktree contains this path", which is the wrong question once the owner is known: a Project registered *inside* a larger worktree resolves to the enclosing toplevel, and every derived path lands in the wrong Project.

**Not:** layout placement, browser drafts, generic browser MIME rendering, or generic browser-file overwrite, move, and delete operations.

### `project_watcher.py`

Leased non-recursive directory watches keyed by Project, exact root, path set, and watch id.

**Not:** a recursive Project crawl, or deciding whether a requested root is a listed Git worktree.

### `layouts.py`

Layout-v6 validation and migrations.

**Not:** UI focus or drag state.

## Worktree commands

### `worktree_setup.py`

Project-config and convention setup resolution, supervised pre-spawn subprocess execution, timeout and tree reap, bounded output capture, and the public result and terminal prelude.

**Not:** Git worktree creation, harness trust, session spawning, or command resolution and bounded execution themselves (`worktree_exec.py`).

### `worktree_exec.py`

The shared seam under both repository-declared worktree commands: `[worktree]`-override-or-executable-script resolution, shebang resolution on Windows, head-and-tail bounded output capture with an optional `on_chunk` observer whose exceptions are contained, and one timeout and tree-reap subprocess runner that returns the exit status exactly as given.
A throw in the observer would abandon a half-read pipe and block the process it is draining.

**Not:** authority of any kind - bootstrap has none to check and verification's lives in `worktree_verify.py` - or interpreting what the observed bytes mean (`verify_progress.py`).

### `worktree_graveyard.py`

Where a removed checkout goes so the deletion does not have to happen in front of anyone: the graveyard's location under the repository's common Git directory, the single atomic rename that buries a tree, the restore that undoes it, and the idempotent purge.

The purge clears the read-only bit before retrying a file (Git writes loose objects read-only, and Windows cannot unlink one at all), counts what it could not delete rather than raising, and never removes the graveyard root - a purge racing a burial must not delete the directory another removal is renaming into.

**Not:** deciding *whether* a worktree may be buried, which is Git's set of refusals and lives with the removal route in `server.py`; running Git; or scheduling the purge.

### `worktree_verify.py`

The land gate's authority: `[worktree].verify_command` and `.worktree-verify` resolution, the machine-local exact-content approval store (a digest over source-kind plus bytes, with a retained snapshot for the diff, un-approved by any edit), and the typed run result whose exit code is never re-derived and which also carries the steps the run announced and its line count.

**Not:** execution mechanics (`worktree_exec.py`), when a gate runs (`land_queue.py`), how progress is read (`verify_progress.py`), or HTTP.

### `verify_progress.py`

What a *running* verification gate says about itself, read off its own output stream.

- `=== name ===` step boundaries, with exactly three equals signs each side, and a captured name carrying one is rejected, so pytest's own section rules are not read as steps.
- Per-step and per-run elapsed time.
- A line count for a gate that announces nothing.
- A snapshot whose `expected_step_count` is `None` unless a byte-identical run has already passed, withdrawn again the moment a run overruns that plan.

It is bounded (tracked steps, name length, line buffer) and exception-free by construction, because it runs inside the pipe reader.

**Not:** any estimate, any percentage, deciding when to record a plan or where to store one (`land_queue.py`, `land_store.py`), or the process itself.

## Actions and setup commands

### `project_actions.py`

Inert task import, normalization, per-file exact-bytes trust with retained approved snapshots, run-time input substitution, resolution of a spoken action name to one action or to candidates with its trust and input refusals (`preview_action_run`), per-step spawn requests (shell quoting, PATH and shim resolution), and the shipped authoring reference asset.

**Not:** automatic execution, UI placement, session ownership, or timeout enforcement, since the server owns the timer.

### `project_init.py`

User-authored setup commands from the daemon config: the catalog, id selection in configured order, and one step per command.

**Not:** trust fingerprints, repository reads (there are none), or spawn execution.

## Instructions and context

### `agent_context.py`

Descriptor-driven read-only Project and global instruction and provider-memory inventory, fixed-path opaque source reads and reveal resolution, complete memory counts, normalized Project-root comparisons, preview and revision-guarded whole-file synchronization across declared instruction files, atomic replace and data-dir restore points, and the shared source operations used by MCP.

**Not:** arbitrary browser-supplied paths, global-instruction or learned-memory writes, automatic sync, or private provider store formats.

### `project_context.py`

The fixed `.swe-mux/project-context.md` path, a bounded UTF-8 Markdown read, blank-file initialization, atomic revision-checked writes, the setup prompt, and the scan prompt prefix.

Project context is explicit user-owned data rather than inferred substrate.
It reads one bounded fixed Markdown path, never crawls the repository, and degrades to empty context on an invalid or unavailable file.
The editor's setup prompt can ask an agent to author the file, but swe-mux itself performs no generation and spends no tokens on context.

**Not:** repository crawling, inferred context, model calls, arbitrary paths, or UI rendering.

### `project_card.py`

Retained only so `PROJECT_CARD_RULE_ID` keeps naming its spender; no card is constructed at runtime and nothing else imports it.

**Not:** active Project context, scan input, or automation enablement.
