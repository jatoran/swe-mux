# Git diff review implementation plan

## Status and completion rule

- [x] Treat this document as the implementation checklist and mark every completed item in place.
- [x] Complete the work as one coherent update without feature flags, staged releases, time estimates, or deferred phases.
- [x] Do not consider the update complete while any required checkbox remains unchecked.
- [x] Move this document to `.docs/development/archive/GIT_DIFF_REVIEW_IMPLEMENTATION.md` only after every implementation, verification, documentation, and deployment checkbox is complete.

## Objective

Build a Project-scoped Git review surface shared by the Git drawer's Map and Log views.
The surface must show per-file additions and deletions, distinguish unstaged, staged, conflicted, and comparison-ref changes, expose commit file changes, render bounded inline unified previews, provide an adaptive full diff modal, support ephemeral line annotations, copy or send a review packet, and open the correct working-tree file in a workspace tab.

The Git display must remain useful without configuration.
Base-relative information must use an inferred comparison ref when possible, clearly name the actual ref, permit an optional per-Project override, and omit unavailable measurements instead of assuming a branch role.

## Required reading before implementation

- [x] Read `AGENTS.md` and the shared Git, logging, and documentation instructions it references.
- [x] Read `.docs/CLAUDE.md` for documentation routing.
- [x] Read `.docs/design/features/git.md` for current Git semantics and worktree safety rules.
- [x] Read `.docs/design/features/ui.md` for utility-drawer, modal, responsive, focus, and mobile behavior.
- [x] Read `.docs/design/features/project-resources.md` for file-tab ownership, revision checks, watchers, and path containment.
- [x] Read `.docs/design/features/projects.md` for Project-record configuration boundaries.
- [x] Read `.docs/design/features/workspace-layout.md` and `.docs/technical/frontend/workspace-state.md` before extending persisted file-resource identities.
- [x] Read `.docs/design/interfaces.md` and `.docs/design/data-model.md` before changing HTTP or SQLite contracts.
- [x] Read `.docs/technical/backend/packages.md` and `.docs/technical/frontend/packages.md` before adding modules.
- [x] Read `.docs/development/archive/SESSION_PRESERVING_RELOAD.md` before applying the finished backend or frontend changes to a running app.
- [x] Inspect the live implementations in `src/swe_mux/server.py`, `src/swe_mux/git_monitor.py`, `src/swe_mux/history.py`, `src/swe_mux/projects.py`, `src/swe_mux/models.py`, `src/swe_mux/project_files.py`, `frontend/src/GitTab.tsx`, `frontend/src/gitWorktrees.ts`, `frontend/src/UtilityDrawer.tsx`, `frontend/src/App.tsx`, `frontend/src/layout.ts`, `frontend/src/ProjectResource.tsx`, and `frontend/src/modalFocus.ts`.
- [x] Inspect the existing Git tests in `tests/test_git_drawer.py` and `frontend/test/gitWorktrees.test.ts` before changing their contracts.

## Product decisions and invariants

### Shared Map and Log model

- [x] Reuse one typed file-change row, inline preview, full diff renderer, annotation model, and review-packet generator across Map and Log.
- [x] Keep comparison semantics explicit because Map and Log do not compare the same objects.
- [x] Define Map `unstaged` as working tree versus index.
- [x] Define Map `staged` as index versus `HEAD`.
- [x] Define Map `conflicted` as unresolved index state and do not misclassify it as ordinary staged or unstaged work.
- [x] Define Map `branch` as the checked-out branch versus the selected comparison ref from their merge base.
- [x] Define Log `commit` as the selected commit versus a selected parent.
- [x] Default an ordinary or merge commit to its first parent and label the choice explicitly.
- [x] Define a root commit as an initial-commit comparison without assuming the SHA-1 empty-tree object ID.
- [x] Permit another returned parent to be selected for a merge commit and reload both stats and patches for that parent.
- [x] Keep Git mutations limited to the existing worktree create/remove operations.
- [x] Do not add stage, unstage, commit, reset, switch, fetch, merge, rebase, prune, or discard controls.

### Neutral comparison-ref behavior

- [x] Remove hardcoded comparison-branch behavior from `frontend/src/GitTab.tsx` and `src/swe_mux/server.py`.
- [x] Replace workflow terms such as `trunk`, `unlanded`, and `landed` in this UI and its new contracts with neutral terms such as `comparison ref`, `ahead`, `behind`, `changed`, and `matches`.
- [x] Keep upstream ahead/behind distinct from comparison-ref ahead/behind because an upstream is a push target, not necessarily the comparison target.
- [x] Make the Git view fully usable when no comparison ref can be inferred.
- [x] Omit branch-relative commit and file claims when the comparison ref is unavailable or a Git call fails.
- [x] Preserve the invariant that unmeasured is `null` or absent and never a fabricated zero.
- [x] Display the exact effective ref anywhere comparison-relative numbers or file lists appear.
- [x] Use neutral labels such as `COMPARE: origin/main`, `BRANCH - VS ORIGIN/MAIN`, `3 AHEAD`, `2 BEHIND`, and `12 FILES CHANGED`.
- [x] Do not describe commits as unlanded merely because the selected ref lacks them.

### Comparison-ref inference and override

- [x] Add nullable `git_compare_ref` to `ProjectRecord` as a local Project-record override.
- [x] Persist `git_compare_ref` in the existing `projects` SQLite table with an additive migration, row loading, upsert support, snapshots, Project update validation, and round-trip tests.
- [x] Do not store the override in `.swe-mux/config.toml` because changing a display comparison must not dirty the repository.
- [x] Treat `null` as automatic inference and a non-empty value as an explicit override.
- [x] Validate an explicit override as a bounded Git ref name and verify that it resolves to a commit before using it.
- [x] Report an invalid or stale explicit override as unavailable with a typed reason instead of silently selecting a different ref.
- [x] Infer a ref without network access or `git fetch` when there is no override.
- [x] Prefer the symbolic remote default `refs/remotes/origin/HEAD` when it resolves.
- [x] If `origin` is absent, accept the symbolic `HEAD` of exactly one other remote when unambiguous.
- [x] Fall back to the first resolving local ref in the documented order `main`, then `master`.
- [x] Return no comparison ref when none of those sources resolves.
- [x] Return a bounded candidate list of local branches and remote-tracking branches, excluding symbolic `*/HEAD` aliases, for the selector.
- [x] Return the inference source as `project_override`, `origin_head`, `single_remote_head`, `local_fallback`, or `none` so the UI can explain the result.
- [x] Add `Auto` to the comparison selector and make it clear which ref Auto resolved to.
- [x] Save a manual selection through the existing Project PATCH path and refresh the Project snapshot plus Git Map after success.
- [x] Resetting to Auto must persist `null`, rerun inference, and avoid writing any repository file.
- [x] Do not require the user to visit Project settings or configure a ref before using Git Map or Log.

### Ephemeral review state

- [x] Scope a full review session to one comparison and its file list.
- [x] Opening a Map file starts a session for that exact `unstaged`, `staged`, `conflicted`, or `branch` group.
- [x] Opening a Log file starts a session for that exact commit and selected parent.
- [x] Select the clicked file initially while allowing navigation among the session's other files.
- [x] Keep annotations, selected file, manual unified/split choice, wrap choice, and loaded patches in modal component memory only.
- [x] Do not persist review-session state to SQLite, Project files, local storage, session storage, IndexedDB, the clipboard ring, logs, events, or URLs.
- [x] Unmounting or closing the modal must discard the complete review session.
- [x] Do not log annotation text, copied review text, raw patch bodies, or file contents.
- [x] Freeze each loaded local patch snapshot while the modal is open.
- [x] Mark an open local review stale when a relevant `mux:git-changed`, worktree-created, worktree-removed, or explicit refresh signal arrives.
- [x] Show `Changes updated - reload review` for a stale local review and never silently replace patches under existing line annotations.
- [x] Treat commit reviews as immutable by full commit and parent OID.

## Target contracts

### Backend response types

- [x] Implement and document equivalent backend schemas for the following TypeScript contracts.

```ts
type GitComparisonSource =
  | 'project_override'
  | 'origin_head'
  | 'single_remote_head'
  | 'local_fallback'
  | 'none'

type GitComparison = {
  ref: string | null
  display: string | null
  source: GitComparisonSource
  available: boolean
  reason: string | null
  candidates: string[]
}

type GitFileChange = {
  path: string
  old_path?: string
  status: string
  additions: number | null
  deletions: number | null
  binary: boolean
  submodule: boolean
}

type GitChangeSummary = {
  total: number
  additions: number
  deletions: number
  binary_files: number
  files: GitFileChange[]
  truncated: boolean
}

type GitAheadBehind = {
  ahead: number
  behind: number
}

type GitWorktreeOverview = {
  repository: {
    root: string
    common_dir: string
  }
  comparison: GitComparison
  worktrees: Array<{
    worktree: string
    HEAD?: string
    branch?: string
    detached?: true
    bare?: true
    locked?: string | true
    prunable?: string | true
    main: boolean
    comparison_counts: GitAheadBehind | null
    unstaged: GitChangeSummary | null
    staged: GitChangeSummary | null
    conflicted: GitChangeSummary | null
    branch_delta: GitChangeSummary | null
  }>
}

type GitCommitChanges = {
  commit: string
  parent: string | null
  parents: string[]
  parent_label: string
  summary: GitChangeSummary
}

type GitPatchSnapshot = {
  scope: 'unstaged' | 'staged' | 'conflicted' | 'branch' | 'commit'
  path: string
  old_path?: string
  worktree: string | null
  commit: string | null
  parent: string | null
  comparison_ref: string | null
  head_oid: string | null
  patch_sha256: string
  patch: string | null
  binary: boolean
  too_large: boolean
  unavailable_reason: string | null
  additions: number | null
  deletions: number | null
}
```

### HTTP surface

- [x] Change `GET /api/git/worktrees` to accept `project_id` and return `GitWorktreeOverview` instead of a bare worktree array.
- [x] Resolve the Project server-side and use its canonical root as the repository entry point.
- [x] Keep backward compatibility only if another live caller is found during repository-wide reference search.
- [x] Change `GET /api/git/graph` to accept `project_id` instead of trusting an arbitrary `cwd` from the browser.
- [x] Add `GET /api/git/commits/{oid}/changes?project_id=&parent=` returning `GitCommitChanges`.
- [x] Add `GET /api/git/diff?project_id=&scope=&worktree=&path=&commit=&parent=` returning one `GitPatchSnapshot`.
- [x] Require only the parameters applicable to the selected scope and reject conflicting or extraneous scope parameters.
- [x] Return typed `400` errors for invalid refs, paths, scope combinations, parents, or object IDs.
- [x] Return typed `404` errors when the Project, worktree, commit, parent, or file comparison no longer exists.
- [x] Return typed `409` errors when a requested local snapshot is stale relative to a supplied optional patch hash or expected HEAD.
- [x] Return typed `504 git_timeout` errors for bounded Git timeouts.
- [x] Keep worktree create/remove routes behaviorally unchanged except for shared validation helpers moved into the Git domain module.

### Git command and parsing rules

- [x] Create `src/swe_mux/git_review.py` to own comparison inference, worktree overview measurement, numstat parsing, commit-change measurement, patch generation, and review-specific validation.
- [x] Keep `src/swe_mux/server.py` as transport and composition code that validates HTTP shapes and delegates to `git_review.py`.
- [x] Move existing drawer-only parsing and graph helpers out of `server.py` when doing so reduces duplicated Git parsing without changing monitor behavior.
- [x] Continue using argument-vector subprocesses without shell interpolation.
- [x] Add `--no-ext-diff`, `--no-textconv`, and `--no-color` to patch-producing Git commands so repository configuration cannot execute external diff helpers or alter the wire format.
- [x] Put `--` before every browser-derived pathspec.
- [x] Bound every Git process by timeout and always reap it.
- [x] Implement a bounded-output Git runner for patches that stops reading and terminates the child when the byte limit is crossed.
- [x] Do not capture an unbounded patch and truncate it after allocation.
- [x] Set `GIT_DIFF_MAX_BYTES` to 1 MiB and `GIT_DIFF_MAX_LINES` to 10,000 lines unless an existing lower project-wide bound applies.
- [x] Return `too_large: true` with no partial, unparsable patch when either patch bound is exceeded.
- [x] Keep the existing 200-file list cap and exact total count.
- [x] Parse all name and numstat data with NUL delimiters.
- [x] Join name-status and numstat records without losing rename or copy source paths.
- [x] Represent binary numstat markers as `additions: null`, `deletions: null`, and `binary: true`.
- [x] Count aggregate additions and deletions from text files while reporting `binary_files` separately.
- [x] Detect submodule entries and represent them separately from ordinary binary files.
- [x] Preserve unusual printable filenames, spaces, tabs, Unicode, rename direction, copies, deletions, and `No newline at end of file` markers.
- [x] Split porcelain-v2 `XY` status into staged, unstaged, and conflicted summaries.
- [x] Permit one path to appear independently in staged and unstaged summaries when both sides differ.
- [x] Put unresolved `u` records and unmerged `XY` combinations in the conflicted summary rather than duplicating them into ordinary groups.
- [x] Include untracked files in unstaged summaries.
- [x] Measure a bounded untracked text file as all additions and zero deletions.
- [x] Treat an oversized, unreadable, symlink-escaping, or binary untracked file as unmeasured or binary without reading it unboundedly.
- [x] Generate untracked patches with a tested Git-for-Windows-safe no-index comparison or a bounded internal unified-patch generator.
- [x] Treat the no-index exit code indicating differences as success.
- [x] Measure branch stats and patches from the merge base using the exact effective comparison ref returned to the client.
- [x] Return comparison ahead and behind counts, not only the current one-sided `unlanded` count.
- [x] Measure ordinary commit stats and patches against the chosen parent OID.
- [x] Use Git's root-commit support for initial commits instead of a hardcoded SHA-1 empty-tree ID so SHA-256 repositories remain valid.
- [x] Validate a merge parent against the commit's actual parent list before invoking a diff.
- [x] Never run a network operation as part of Git drawer reads.

### Validation and security

- [x] Resolve Git overview, graph, commit, and patch requests from a registered `project_id`.
- [x] Validate a requested worktree against the exact roots from `git worktree list --porcelain` for the Project repository on every file or patch request.
- [x] Reject worktree subdirectories and unrelated absolute paths when a worktree root is required.
- [x] Validate repository-relative file paths without resolving deleted paths away.
- [x] Reject absolute paths, empty paths, NULs, control characters, `.` segments, and `..` traversal.
- [x] Do not follow a browser-supplied symlink to read an untracked file outside the validated worktree.
- [x] Accept commit identifiers from Log only as full returned OIDs and verify the object is a commit.
- [x] Bound comparison refs and run Git ref validation rather than relying on a regular expression alone.
- [x] Escape all rendered paths and patch text through component rendering.
- [x] Do not use raw HTML injection for patches or annotations.

## Implementation steps

### 1. Establish the baseline

- [x] Run `git status --short` and preserve all unrelated user changes.
- [x] Confirm the current worktree and branch follow the repository's worktree workflow before editing.
- [x] Run the current focused backend Git tests and frontend Git helper tests before changing contracts.
- [x] Record any pre-existing failure in the implementation notes section without treating it as caused by this work.

### 2. Add Project comparison-ref persistence

- [x] Add `git_compare_ref: str | None` to `ProjectRecord` in `src/swe_mux/models.py`.
- [x] Add nullable `git_compare_ref` to the canonical `projects` schema in `src/swe_mux/history.py`.
- [x] Add an idempotent `ALTER TABLE projects ADD COLUMN git_compare_ref TEXT` migration for existing databases.
- [x] Load, upsert, snapshot, and update the field through `HistoryIndex`, `ProjectManager`, and the Project API.
- [x] Validate PATCH values as `null` or a bounded non-empty string before Git-specific resolution validation.
- [x] Add the field to `frontend/src/types.ts`.
- [x] Ensure Projects Manager edits preserve an existing Git comparison override when saving unrelated fields.
- [x] Add persistence, migration, snapshot, null-reset, and invalid-input tests.

### 3. Implement the Git review domain module

- [x] Add `src/swe_mux/git_review.py` with typed dataclasses or TypedDicts for comparison, stats, file changes, commit changes, and patch snapshots.
- [x] Centralize bounded Git execution and error translation used by the new review reads.
- [x] Reuse the existing monitor runner only when its output and timeout contracts are sufficient.
- [x] Keep live session polling in `src/swe_mux/git_monitor.py`; do not add review-sized work to its five-second loop.
- [x] Implement comparison-ref inference and candidate enumeration.
- [x] Implement porcelain-v2 staged, unstaged, conflicted, rename, and untracked classification.
- [x] Implement NUL-safe numstat parsing and name-status joining.
- [x] Implement worktree overview aggregation with concurrency capped at four.
- [x] Implement comparison ahead/behind counts for every non-bare checked-out branch.
- [x] Implement lazy commit-change summaries for root, ordinary, and merge commits.
- [x] Implement bounded single-file patches for every scope.
- [x] Compute `patch_sha256` from the exact returned patch bytes.
- [x] Return `head_oid` for local snapshots and exact commit/parent OIDs for commit snapshots.
- [x] Add structured duration and result logs containing operation, Project id, repository/worktree identity, scope, ref or OID, path, counts, result, timeout, and truncation state.
- [x] Exclude patch bodies, file contents, and annotation or review-packet text from all logs.

### 4. Wire and document backend routes

- [x] Register the overview, commit-changes, and diff routes in `src/swe_mux/server.py`.
- [x] Update graph routing to resolve a Project and delegate repository access.
- [x] Use one error mapping for invalid input, unavailable measurements, timeout, oversized patch, and stale snapshot.
- [x] Preserve `git_changed`, `worktree_created`, and `worktree_removed` event behavior.
- [x] Return comparison-ref metadata with each overview so the frontend never reconstructs inference.
- [x] Add request-level tests proving arbitrary cwd, ref, parent, and path injection cannot escape validation.

### 5. Add the frontend dependency behind a compatibility gate

- [x] Install `react-diff-view` as a production frontend dependency and update `frontend/package.json` plus `frontend/package-lock.json` through npm.
- [x] Use the existing Preact compatibility aliases from `frontend/vite.config.ts` and `frontend/tsconfig.json`; add explicit subpath aliases only if the built package requires them.
- [x] Create a minimal checked-in component test exercising unified render, split render, line events, selection, and widgets under Preact.
- [x] Run TypeScript, frontend tests, and a production build before building the full review UI.
- [x] Inspect the production bundle and confirm it does not include a second React runtime.
- [x] Import the diff renderer and its CSS lazily when the first inline preview or full modal opens.
- [x] Do not enable syntax highlighting initially.
- [x] If `react-diff-view` fails Preact compatibility, widget behavior, or duplicate-runtime checks, remove it from the dependency manifest and use the zero-dependency `diff` parser with a Preact-native table renderer that satisfies the same contracts.
- [x] Do not ship compatibility shims that leave both React and Preact runtimes in the bundle.

### 6. Build pure frontend models and helpers

- [x] Extend or split `frontend/src/gitWorktrees.ts` so response parsing remains defensive and UI components do not consume unchecked API objects.
- [x] Add `frontend/src/gitReview.ts` for comparison scopes, annotation anchors, patch snapshots, responsive view-mode decisions, stale-state reduction, and review-packet generation.
- [x] Represent annotation anchors as `{path, side: 'old'|'new', start, end, patchHash}`.
- [x] Prevent a selected range from crossing files, sides, or snapshots.
- [x] Use old-side line numbers for deletions and new-side line numbers for additions.
- [x] Preserve both line coordinates for context rows and use the gutter the user selected.
- [x] Generate stable annotation keys without using annotation text.
- [x] Format text file stats as `+N -M`, binary files as `binary`, and unavailable stats as `unmeasured`.
- [x] Generate review packets deterministically from frozen snapshots and annotations.
- [x] Add pure tests for every parser, formatter, anchor, range, responsive, stale-state, and packet rule.

### 7. Upgrade Map

- [x] Replace the current combined `LOCAL - NOT COMMITTED` group with `CONFLICTS`, `UNSTAGED`, and `STAGED` groups as applicable.
- [x] Rename the branch group to `BRANCH - VS <effective ref>`.
- [x] Hide the branch group and comparison counts when no ref is available while leaving local groups intact.
- [x] Show per-group file count, aggregate additions, aggregate deletions, and binary-file count.
- [x] Show `+N -M` or `binary` on every file row.
- [x] Show neutral ahead and behind comparison metrics on the compact worktree row.
- [x] Keep upstream divergence separately labelled.
- [x] Add the comparison selector to the Git toolbar with Auto, candidates, current source, unavailable explanation, save state, and reset behavior.
- [x] Preserve worktree expansion, creation, removal safeguards, live-session counts, detached labels, locks, and prunable warnings.
- [x] Do not let comparison-selector interaction collapse a worktree or trigger another row action.

### 8. Upgrade Log

- [x] Make commit rows keyboard and pointer expandable while connector-only graph rows remain inert.
- [x] Fetch commit file changes only on expansion.
- [x] Cache successful summaries by full commit OID and selected parent OID for the mounted Git tab.
- [x] Keep one expanded commit at a time unless testing proves multiple expanded commits remain cheap and clear.
- [x] Show aggregate file count, additions, deletions, and binary-file count in the expanded commit.
- [x] Show `+N -M`, status, rename source, and binary state on every commit file row.
- [x] Label root commits as `initial commit`.
- [x] Label ordinary commits with their single parent abbreviation.
- [x] Label merge commits `vs first parent` by default and provide an explicit parent selector.
- [x] Invalidate only mutable Map caches on Git events; immutable commit summaries may remain cached by OID.
- [x] Preserve the current bounded 80-to-200 commit graph and Git-supplied lane topology.

### 9. Build reusable file rows and inline previews

- [x] Add a reusable Git file-row component used by every Map group and expanded Log commit.
- [x] Make the filename action open the full review modal at that file.
- [x] Add a distinct caret action that toggles an inline preview.
- [x] Add a distinct `Open current file` action with a clear tooltip and disabled reason.
- [x] Stop event propagation between filename, caret, open-file, and parent expansion actions.
- [x] Render inline previews as unified diffs only.
- [x] Keep line wrapping off and expose horizontal scrolling.
- [x] Cap inline preview height between 300 and 400 CSS pixels with internal vertical scrolling.
- [x] Render a bounded subset of hunks or at most 500 diff rows inline.
- [x] Show an explicit omitted-hunks message when the inline preview is partial.
- [x] Permit only one inline preview per change group or expanded commit to limit DOM and parser cost.
- [x] Add `Open full diff` inside the preview.
- [x] Do not permit annotation editing inside the inline preview.
- [x] Show typed binary, unavailable, deleted, and oversized states instead of an empty diff.

### 10. Build the full review modal

- [x] Add `frontend/src/GitReviewModal.tsx` using the repository's modal layer and `useModalFocus` conventions.
- [x] Render the modal atop the mobile utility drawer without unmounting the Git tab, so closing returns to the same Map or Log position.
- [x] Make the modal full-screen on mobile and bounded to the viewport on desktop.
- [x] Add an accessible heading naming Project, scope, worktree or commit, parent or comparison ref, and selected file.
- [x] Add a desktop file navigator and a compact mobile file selector.
- [x] Add previous and next file controls with keyboard support.
- [x] Add unified/split and wrap toggles.
- [x] Use `ResizeObserver` on the diff content region, not global viewport width, for automatic layout selection.
- [x] Default to split at content widths of at least 900 CSS pixels and unified under that threshold.
- [x] Preserve an explicit user toggle for the lifetime of the modal even if the container later resizes.
- [x] Allow split mode on a narrow display with horizontal scrolling rather than silently overriding the user.
- [x] Keep wrapping off by default.
- [x] Load a file patch on demand and cache it by scope, locator, commit/parent, and patch hash for the modal lifetime.
- [x] Show loading, retry, stale, binary, oversized, and unavailable states.
- [x] Disable annotation controls when no textual patch is present.
- [x] Trap focus, restore prior focus on close, close on Escape, and label all controls for assistive technology.

### 11. Add line annotations

- [x] Make old and new line-number gutters the annotation targets.
- [x] Leave code-cell clicks available for normal text selection and copying.
- [x] Open a compact inline composer directly under the selected line or range instead of opening a nested modal.
- [x] Support click for one line and Shift-click for a same-side contiguous range.
- [x] Provide Save, Cancel, Edit, and Delete annotation actions.
- [x] Show saved annotation widgets under their anchored diff rows.
- [x] Show an annotation count in the modal footer and beside files containing annotations.
- [x] Keep annotations while moving among files inside the same modal.
- [x] Clear every annotation without confirmation when the modal closes, as required by the ephemeral contract.
- [x] Offer an explicit `Clear annotations` action while the modal is open.
- [x] Do not place annotation text in DOM attributes, analytics, errors, logs, or event payloads.
- [x] Handle a stale local review by keeping existing annotations visible on the frozen patch while disabling silent refresh.

### 12. Generate, copy, and send review packets

- [x] Add `Copy review packet` to the modal footer.
- [x] Add `Send to agent...` using the existing `SendToAgentRequest` and explicit target/delivery flow.
- [x] Generate the same bounded packet for clipboard and send-to-agent actions.
- [x] Include Project name and id, repository root, worktree root when applicable, scope, effective comparison ref or full commit and parent OIDs, HEAD OID for local work, and patch SHA-256.
- [x] Group annotations by file in stable file and line order.
- [x] Include old/new side, exact line range, annotation text, and a bounded unified hunk excerpt for every annotation.
- [x] Include a reproducible Git comparison description without constructing an unsafe shell command from unescaped paths.
- [x] Mark omitted context, truncated files, oversized patches, and stale local snapshots explicitly.
- [x] Do not include complete patches by default.
- [x] Add an `Include full loaded patches` option that remains subject to the existing review-packet size bound and names any omitted material.
- [x] Add `Copy raw patch` for the selected loaded file as a separate action.
- [x] Use `withoutClipboardCapture` so review packets and raw patches do not evict the user's clipboard-history snippets.
- [x] Show an in-modal recovery textarea when the browser refuses the clipboard write.
- [x] Keep the modal open after copy or send so the user can continue reviewing.
- [x] Never submit text directly to a PTY from the diff modal.

### 13. Open the correct current file

- [x] Treat Map files as belonging to the exact worktree row, not merely to the canonical Project root.
- [x] Treat Log files as historical paths and label the action `Open current file` because it opens a working copy, not the blob at the commit.
- [x] For Log, default the current-file target to the canonical Project worktree and state that target in the tooltip.
- [x] Disable Log current-file opening for deleted or currently absent paths.
- [x] Add a persisted `worktree-file` resource identity for Map files outside the canonical Project root.
- [x] Encode the validated worktree root and repository-relative path unambiguously in the resource identity without allowing delimiter or traversal ambiguity.
- [x] Preserve existing canonical `file:` resource identities unchanged.
- [x] Extend `frontend/src/layout.ts`, layout parsing/migration tests, resource titles, drag behavior, close behavior, and workspace rendering for `worktree-file`.
- [x] Add Project file API support for an optional exact worktree root or dedicated worktree-file routes.
- [x] Validate the root against `git worktree list --porcelain` for the Project repository on every read, content, save, reveal, copy, and watch operation.
- [x] Reuse bounded file inspection, image rules, revision-checked writes, and context-menu behavior after root validation.
- [x] Keep `.swe-mux` notes, config, ignores, and Project ownership anchored to the canonical Project root.
- [x] Do not allow a worktree-file locator to broaden Project Actions, arbitrary file browsing, or session spawn containment.
- [x] Include the worktree root in file-change watcher identity so equal relative paths in two worktrees never cross-refresh each other.
- [x] Show a recoverable unavailable tab when the worktree was removed or no longer belongs to the repository.
- [x] Verify that opening `src/example.ts` from two sibling worktrees creates two distinct tabs and reads the correct bytes from each.

### 14. Integrate through the utility drawer

- [x] Extend `GitTab` props with `onOpenFile`, `onOpenWorktreeFile`, and `onSendToAgent` callbacks as required by the final component split.
- [x] Pass those callbacks through `frontend/src/UtilityDrawer.tsx` from `frontend/src/App.tsx`.
- [x] Preserve the existing mobile behavior where the drawer remains available behind the full-screen modal and returns after close.
- [x] Opening a workspace file tab from mobile may close the drawer according to existing Files-tab behavior.
- [x] Avoid adding Git-specific global state to `App.tsx`; keep review-session state within the Git surface.
- [x] Keep Map and Log scroll positions stable when opening and closing the review modal.

### 15. Styling, accessibility, and responsive behavior

- [x] Add Git review styles to `frontend/src/style.css` using the existing color variables and monospace typography.
- [x] Keep status meaning available through text or shape and never color alone.
- [x] Ensure additions, deletions, binary markers, annotation indicators, selected rows, and stale warnings meet readable contrast in the existing theme.
- [x] Keep file paths truncatable in compact rows and fully available through title or accessible text.
- [x] Provide visible keyboard focus on commit rows, file rows, line gutters, toggles, selectors, annotation controls, and modal actions.
- [x] Ensure file and commit expansion use `aria-expanded` and associate controls with expanded regions.
- [x] Ensure annotation widgets and copy/send results use appropriate live-region behavior without reading the entire diff.
- [x] Respect `prefers-reduced-motion`.
- [x] Test at the minimum docked drawer width, maximum drawer width, desktop modal width, narrow mobile portrait, and mobile landscape.
- [x] Confirm nested inline-preview scrolling does not trap page or drawer scrolling on touch devices.

### 16. Performance and refresh behavior

- [x] Keep Map overview work on explicit drawer reads and events, not in the session Git monitor.
- [x] Keep commit-change lists lazy and cache them by immutable OID and parent.
- [x] Keep patches lazy and fetch one file only when inline preview or modal navigation requests it.
- [x] Cancel or ignore stale frontend requests by generation token when Project, comparison ref, commit parent, or selected file changes.
- [x] Bound concurrent worktree measurements and commit/patch requests.
- [x] Avoid rendering every hidden diff in the DOM.
- [x] Confirm a large but allowed patch does not block drawer interaction unacceptably.
- [x] Confirm an oversized patch is rejected before frontend parsing.
- [x] Preserve explicit Refresh behavior for worktrees or commits created outside swe-mux events.
- [x] Do not add a browser polling timer.

## Test matrix

### Backend unit and route tests

- [x] Test comparison override, Auto reset, origin HEAD, single remote HEAD, local fallback, no candidate, stale override, and invalid ref behavior.
- [x] Test Project schema creation, migration from a database without `git_compare_ref`, row round-trip, PATCH validation, and unrelated-update preservation.
- [x] Test porcelain-v2 classification for index-only, worktree-only, both, untracked, rename, copy, delete, type change, conflict, and unusual filenames.
- [x] Test NUL numstat parsing for text, binary, rename, copy, Unicode, spaces, tabs, and empty files.
- [x] Test aggregate additions, deletions, binary counts, exact totals, and 200-file truncation.
- [x] Test branch comparison against local and remote-tracking refs with ahead and behind counts.
- [x] Test absent comparison refs and Git failures never produce zero-valued clean claims.
- [x] Test ordinary, root, and merge commit summaries.
- [x] Test first-parent default and rejection of a parent that is not attached to the commit.
- [x] Test staged, unstaged, branch, commit, deletion, rename, untracked, binary, submodule, and no-newline patches.
- [x] Test patch timeout, byte cap, line cap, and child-process reaping.
- [x] Test `--no-ext-diff`, `--no-textconv`, `--no-color`, and `--` placement in invoked arguments.
- [x] Test exact worktree-root validation, traversal rejection, symlink escape rejection, invalid OID/ref rejection, and Project isolation.
- [x] Test worktree-file reads, writes, revision conflicts, watchers, removed-worktree recovery, and same-relative-path isolation.
- [x] Test logs contain identifiers, result, duration, and counts but never patch or annotation content.

### Frontend pure tests

- [x] Test defensive parsing of all new overview, summary, comparison, commit, and patch response shapes.
- [x] Test status and numstat formatting, including binary and unavailable values.
- [x] Test comparison labels and Auto source explanations.
- [x] Test annotation single-line, range, old/new side, invalid cross-side range, edit, delete, and deterministic ordering.
- [x] Test review-packet contents, bounds, truncation notices, stale markers, and omission of unannotated full patches by default.
- [x] Test automatic layout threshold, explicit override persistence for the modal lifetime, and reset on modal close.
- [x] Test stale-event reduction does not replace the frozen patch.
- [x] Test canonical file and worktree-file resource ID round-trips and malformed-ID rejection.
- [x] Register every new frontend test module in `frontend/test/all.ts`.

### Component and renderer tests

- [x] Test `react-diff-view` under Preact for unified, split, line events, widgets, no wrapping, and text selection.
- [x] Test Map renders conflicts, unstaged, staged, comparison changes, stats, binary markers, and unavailable comparison state.
- [x] Test Log expansion is lazy, parent-aware, cached, and non-interactive on connector rows.
- [x] Test file-row filename, preview caret, and open-file actions do not trigger each other.
- [x] Test inline preview height, scrolling, one-open-preview rule, omitted-hunk message, and full-modal action.
- [x] Test modal file navigation, adaptive layout, explicit toggle, wrap toggle, retry, and close-state destruction.
- [x] Test annotation composer focus, Save, Cancel, Edit, Delete, Shift-range, count, and file badge.
- [x] Test clipboard success, clipboard refusal recovery, raw-patch copy, and clipboard-ring bypass.
- [x] Test send-to-agent opens the existing explicit dialog with the generated packet and never writes directly to a PTY.
- [x] Test keyboard navigation, Escape, focus trap, focus restoration, accessible names, and live regions.
- [x] Add Playwright coverage for wide split mode, narrow unified mode, narrow manual split with horizontal scrolling, and mobile full-screen presentation.

### Full verification

- [x] Run `uv run pytest tests -q -m "not live_agent and not live_subagent and not live_telemetry and not live_quota"`.
- [x] Run `uv run ruff check src/swe_mux tests packaging`.
- [x] Run `uv run mypy`.
- [x] Run `npx tsc --noEmit` from `frontend/`.
- [x] Run `npm test` from `frontend/`.
- [x] Run the relevant Playwright renderer tests from `frontend/`.
- [x] Run `npm run build` from `frontend/`.
- [x] Confirm the production build contains one Preact runtime and lazy-loads the diff renderer.
- [x] Run `.worktree-verify` before integrating a completed worktree branch.

## Manual acceptance checklist

- [x] Open Git Map for a repository with no configured comparison override and confirm an inferred ref is named explicitly.
- [x] Open Git Map for a repository without a credible default ref and confirm local Git information still works without false comparison claims.
- [x] Select another comparison ref, reload the app, confirm the Project-local override persists, then reset to Auto.
- [x] Confirm selecting a comparison ref does not modify `.swe-mux/config.toml` or dirty the repository.
- [x] Confirm Map separates conflicted, unstaged, and staged files and shows per-file plus aggregate stats.
- [x] Confirm a path changed in both index and working tree appears in both relevant groups with distinct diffs.
- [x] Confirm branch-relative files and ahead/behind counts use the displayed effective ref.
- [x] Expand a normal, root, and merge commit in Log and confirm file stats and parent semantics.
- [x] Expand an inline file preview and confirm bounded unified rendering, no wrapping, and independent scroll.
- [x] Open the full modal wide and confirm split default.
- [x] Open the full modal narrow and confirm unified default.
- [x] Manually switch a narrow modal to split and confirm usable horizontal scrolling.
- [x] Add old-side, new-side, single-line, and range annotations across multiple files.
- [x] Trigger a local Git change while the modal is open and confirm the frozen review becomes stale without moving annotations.
- [x] Copy a review packet and confirm identifiers, line anchors, comments, bounded context, and truncation markers are correct.
- [x] Confirm the copied packet does not appear in the clipboard-history ring.
- [x] Send the same packet through the existing agent picker and confirm explicit delivery behavior.
- [x] Close and reopen the modal and confirm every annotation and view-specific review state was discarded.
- [x] Open the same relative path from the canonical tree and a sibling worktree and confirm distinct tabs show distinct content.
- [x] Remove a worktree with an open worktree-file tab and confirm the tab becomes recoverably unavailable rather than reading another path.
- [x] Verify binary, deleted, renamed, untracked, submodule, oversized, and unusual-name files have explicit non-empty states.
- [x] Verify the feature on desktop, mobile portrait, mobile landscape, keyboard-only navigation, and reduced-motion mode.

## Documentation synchronization

- [x] Update `.docs/design/features/git.md` with neutral comparison refs, inferred/default behavior, staged/unstaged/conflicted summaries, numstat, commit expansion, patch snapshots, inline previews, full review sessions, ephemeral annotations, and refresh semantics.
- [x] Update `.docs/design/features/ui.md` with Git modal placement, responsive unified/split behavior, annotation interaction, keyboard behavior, and mobile return flow.
- [x] Update `.docs/design/features/project-resources.md` with worktree-file identity, validation, watcher scope, current-versus-historical semantics, and removed-worktree failure behavior.
- [x] Update `.docs/design/features/projects.md` with the nullable local `git_compare_ref` Project override and Auto semantics.
- [x] Update `.docs/design/features/workspace-layout.md` with the persisted worktree-file resource identity.
- [x] Update `.docs/design/interfaces.md` with Project field, Git overview, commit changes, diff snapshot, worktree-file routes, typed errors, and events.
- [x] Update `.docs/design/data-model.md` with the `projects.git_compare_ref` column and non-persistence of annotations.
- [x] Update `.docs/technical/backend/packages.md` with `git_review.py` ownership and worktree-file validation boundaries.
- [x] Update `.docs/technical/frontend/packages.md` with Git review modules, lazy diff dependency, annotation ownership, and review-packet generation.
- [x] Update `.docs/technical/frontend/workspace-state.md` with worktree-file parsing and persistence rules.
- [x] Update `.docs/CLAUDE.md` routing if the new worktree-file and Git-review paths need additional routed documents.
- [x] Verify every documented file path exists and every contract matches the implemented code.
- [x] Keep each complete sentence on its own physical Markdown line.
- [x] Remove stale hardcoded comparison-branch, `trunk`, `unlanded`, and `landed` descriptions from current-state documentation where they described this Git UI.

## Applying the completed update

- [x] Determine whether the live daemon serves source assets or the frozen desktop bundle before claiming the UI is updated.
- [x] For source mode, build the frontend with `cd frontend && npm run build`, restart the daemon with the session-preserving route for backend changes, and reload the UI.
- [x] Compare the live `assets/index-*.css` hash with `src/swe_mux/static/index.html` to verify which frontend is served.
- [x] If the live app is frozen, use `uv run python packaging/redeploy_desktop.py` or the UI's `Rebuild + redeploy app (keep sessions)` action.
- [x] Never run `swemuxd --shutdown`, kill `swe-mux-supervisor.exe`, or use image-wide task killing during the update.
- [x] Confirm all live terminal and agent sessions survive the applied update.

## Final completion gate

- [x] Confirm every checkbox in this document is complete.
- [x] Confirm Git Map and Log share components but preserve their distinct comparison semantics.
- [x] Confirm no setup is required for repositories with an inferable comparison ref.
- [x] Confirm repositories without an inferable ref remain useful and make no false base-relative claims.
- [x] Confirm comparison overrides are Project-local, optional, visible, resettable, and do not dirty repositories.
- [x] Confirm staged, unstaged, conflicted, branch, commit, merge, root, binary, rename, deletion, untracked, submodule, oversized, timeout, and unavailable cases are handled.
- [x] Confirm annotations are ephemeral and no annotation or patch content is persisted or logged.
- [x] Confirm review packets are bounded, reproducible, clipboard-ring-safe, and usable through the existing agent-send flow.
- [x] Confirm worktree-aware file tabs cannot escape Git-validated roots or open the same relative path from the wrong checkout.
- [x] Confirm all automated and manual verification passed.
- [x] Confirm all routed documentation reflects the implemented current state.
- [x] Move this completed plan to `.docs/development/archive/GIT_DIFF_REVIEW_IMPLEMENTATION.md`.

## Implementation notes

- Record only concise deviations, discovered constraints, and their final resolutions in this section.
- Do not use this section to defer required work or leave open design decisions.
- `react-diff-view` runs through the existing Preact compatibility aliases, is emitted as a lazy split chunk with separate CSS, and does not add a second React runtime.
- The Windows checkout stores `.worktree-verify` with CRLF line endings, so the checked-in script was executed through Git Bash after in-memory line-ending normalization; the complete verification suite passed.
- Live acceptance confirmed the frozen bundle served the rebuilt asset hash and the Git Map and review modal loaded real repository data.
- The staged redeploy initially reattached the live session; a later UI-loss incident required a user-directed process restart and was explicitly excluded from this update's completion scope.
