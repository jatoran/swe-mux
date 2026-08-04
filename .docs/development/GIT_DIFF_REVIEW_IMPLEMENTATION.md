# Git diff review implementation plan

## Status and completion rule

- [ ] Treat this document as the implementation checklist and mark every completed item in place.
- [ ] Complete the work as one coherent update without feature flags, staged releases, time estimates, or deferred phases.
- [ ] Do not consider the update complete while any required checkbox remains unchecked.
- [ ] Move this document to `.docs/development/archive/GIT_DIFF_REVIEW_IMPLEMENTATION.md` only after every implementation, verification, documentation, and deployment checkbox is complete.

## Objective

Build a Project-scoped Git review surface shared by the Git drawer's Map and Log views.
The surface must show per-file additions and deletions, distinguish unstaged, staged, conflicted, and comparison-ref changes, expose commit file changes, render bounded inline unified previews, provide an adaptive full diff modal, support ephemeral line annotations, copy or send a review packet, and open the correct working-tree file in a workspace tab.

The Git display must remain useful without configuration.
Base-relative information must use an inferred comparison ref when possible, clearly name the actual ref, permit an optional per-Project override, and omit unavailable measurements instead of assuming a branch role.

## Required reading before implementation

- [ ] Read `AGENTS.md` and the shared Git, logging, and documentation instructions it references.
- [ ] Read `.docs/CLAUDE.md` for documentation routing.
- [ ] Read `.docs/design/features/git.md` for current Git semantics and worktree safety rules.
- [ ] Read `.docs/design/features/ui.md` for utility-drawer, modal, responsive, focus, and mobile behavior.
- [ ] Read `.docs/design/features/project-resources.md` for file-tab ownership, revision checks, watchers, and path containment.
- [ ] Read `.docs/design/features/projects.md` for Project-record configuration boundaries.
- [ ] Read `.docs/design/features/workspace-layout.md` and `.docs/technical/frontend/workspace-state.md` before extending persisted file-resource identities.
- [ ] Read `.docs/design/interfaces.md` and `.docs/design/data-model.md` before changing HTTP or SQLite contracts.
- [ ] Read `.docs/technical/backend/packages.md` and `.docs/technical/frontend/packages.md` before adding modules.
- [ ] Read `.docs/development/archive/SESSION_PRESERVING_RELOAD.md` before applying the finished backend or frontend changes to a running app.
- [ ] Inspect the live implementations in `src/swe_mux/server.py`, `src/swe_mux/git_monitor.py`, `src/swe_mux/history.py`, `src/swe_mux/projects.py`, `src/swe_mux/models.py`, `src/swe_mux/project_files.py`, `frontend/src/GitTab.tsx`, `frontend/src/gitWorktrees.ts`, `frontend/src/UtilityDrawer.tsx`, `frontend/src/App.tsx`, `frontend/src/layout.ts`, `frontend/src/ProjectResource.tsx`, and `frontend/src/modalFocus.ts`.
- [ ] Inspect the existing Git tests in `tests/test_git_drawer.py` and `frontend/test/gitWorktrees.test.ts` before changing their contracts.

## Product decisions and invariants

### Shared Map and Log model

- [ ] Reuse one typed file-change row, inline preview, full diff renderer, annotation model, and review-packet generator across Map and Log.
- [ ] Keep comparison semantics explicit because Map and Log do not compare the same objects.
- [ ] Define Map `unstaged` as working tree versus index.
- [ ] Define Map `staged` as index versus `HEAD`.
- [ ] Define Map `conflicted` as unresolved index state and do not misclassify it as ordinary staged or unstaged work.
- [ ] Define Map `branch` as the checked-out branch versus the selected comparison ref from their merge base.
- [ ] Define Log `commit` as the selected commit versus a selected parent.
- [ ] Default an ordinary or merge commit to its first parent and label the choice explicitly.
- [ ] Define a root commit as an initial-commit comparison without assuming the SHA-1 empty-tree object ID.
- [ ] Permit another returned parent to be selected for a merge commit and reload both stats and patches for that parent.
- [ ] Keep Git mutations limited to the existing worktree create/remove operations.
- [ ] Do not add stage, unstage, commit, reset, switch, fetch, merge, rebase, prune, or discard controls.

### Neutral comparison-ref behavior

- [ ] Remove hardcoded comparison-branch behavior from `frontend/src/GitTab.tsx` and `src/swe_mux/server.py`.
- [ ] Replace workflow terms such as `trunk`, `unlanded`, and `landed` in this UI and its new contracts with neutral terms such as `comparison ref`, `ahead`, `behind`, `changed`, and `matches`.
- [ ] Keep upstream ahead/behind distinct from comparison-ref ahead/behind because an upstream is a push target, not necessarily the comparison target.
- [ ] Make the Git view fully usable when no comparison ref can be inferred.
- [ ] Omit branch-relative commit and file claims when the comparison ref is unavailable or a Git call fails.
- [ ] Preserve the invariant that unmeasured is `null` or absent and never a fabricated zero.
- [ ] Display the exact effective ref anywhere comparison-relative numbers or file lists appear.
- [ ] Use neutral labels such as `COMPARE: origin/main`, `BRANCH - VS ORIGIN/MAIN`, `3 AHEAD`, `2 BEHIND`, and `12 FILES CHANGED`.
- [ ] Do not describe commits as unlanded merely because the selected ref lacks them.

### Comparison-ref inference and override

- [ ] Add nullable `git_compare_ref` to `ProjectRecord` as a local Project-record override.
- [ ] Persist `git_compare_ref` in the existing `projects` SQLite table with an additive migration, row loading, upsert support, snapshots, Project update validation, and round-trip tests.
- [ ] Do not store the override in `.swe-mux/config.toml` because changing a display comparison must not dirty the repository.
- [ ] Treat `null` as automatic inference and a non-empty value as an explicit override.
- [ ] Validate an explicit override as a bounded Git ref name and verify that it resolves to a commit before using it.
- [ ] Report an invalid or stale explicit override as unavailable with a typed reason instead of silently selecting a different ref.
- [ ] Infer a ref without network access or `git fetch` when there is no override.
- [ ] Prefer the symbolic remote default `refs/remotes/origin/HEAD` when it resolves.
- [ ] If `origin` is absent, accept the symbolic `HEAD` of exactly one other remote when unambiguous.
- [ ] Fall back to the first resolving local ref in the documented order `main`, then `master`.
- [ ] Return no comparison ref when none of those sources resolves.
- [ ] Return a bounded candidate list of local branches and remote-tracking branches, excluding symbolic `*/HEAD` aliases, for the selector.
- [ ] Return the inference source as `project_override`, `origin_head`, `single_remote_head`, `local_fallback`, or `none` so the UI can explain the result.
- [ ] Add `Auto` to the comparison selector and make it clear which ref Auto resolved to.
- [ ] Save a manual selection through the existing Project PATCH path and refresh the Project snapshot plus Git Map after success.
- [ ] Resetting to Auto must persist `null`, rerun inference, and avoid writing any repository file.
- [ ] Do not require the user to visit Project settings or configure a ref before using Git Map or Log.

### Ephemeral review state

- [ ] Scope a full review session to one comparison and its file list.
- [ ] Opening a Map file starts a session for that exact `unstaged`, `staged`, `conflicted`, or `branch` group.
- [ ] Opening a Log file starts a session for that exact commit and selected parent.
- [ ] Select the clicked file initially while allowing navigation among the session's other files.
- [ ] Keep annotations, selected file, manual unified/split choice, wrap choice, and loaded patches in modal component memory only.
- [ ] Do not persist review-session state to SQLite, Project files, local storage, session storage, IndexedDB, the clipboard ring, logs, events, or URLs.
- [ ] Unmounting or closing the modal must discard the complete review session.
- [ ] Do not log annotation text, copied review text, raw patch bodies, or file contents.
- [ ] Freeze each loaded local patch snapshot while the modal is open.
- [ ] Mark an open local review stale when a relevant `mux:git-changed`, worktree-created, worktree-removed, or explicit refresh signal arrives.
- [ ] Show `Changes updated - reload review` for a stale local review and never silently replace patches under existing line annotations.
- [ ] Treat commit reviews as immutable by full commit and parent OID.

## Target contracts

### Backend response types

- [ ] Implement and document equivalent backend schemas for the following TypeScript contracts.

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

- [ ] Change `GET /api/git/worktrees` to accept `project_id` and return `GitWorktreeOverview` instead of a bare worktree array.
- [ ] Resolve the Project server-side and use its canonical root as the repository entry point.
- [ ] Keep backward compatibility only if another live caller is found during repository-wide reference search.
- [ ] Change `GET /api/git/graph` to accept `project_id` instead of trusting an arbitrary `cwd` from the browser.
- [ ] Add `GET /api/git/commits/{oid}/changes?project_id=&parent=` returning `GitCommitChanges`.
- [ ] Add `GET /api/git/diff?project_id=&scope=&worktree=&path=&commit=&parent=` returning one `GitPatchSnapshot`.
- [ ] Require only the parameters applicable to the selected scope and reject conflicting or extraneous scope parameters.
- [ ] Return typed `400` errors for invalid refs, paths, scope combinations, parents, or object IDs.
- [ ] Return typed `404` errors when the Project, worktree, commit, parent, or file comparison no longer exists.
- [ ] Return typed `409` errors when a requested local snapshot is stale relative to a supplied optional patch hash or expected HEAD.
- [ ] Return typed `504 git_timeout` errors for bounded Git timeouts.
- [ ] Keep worktree create/remove routes behaviorally unchanged except for shared validation helpers moved into the Git domain module.

### Git command and parsing rules

- [ ] Create `src/swe_mux/git_review.py` to own comparison inference, worktree overview measurement, numstat parsing, commit-change measurement, patch generation, and review-specific validation.
- [ ] Keep `src/swe_mux/server.py` as transport and composition code that validates HTTP shapes and delegates to `git_review.py`.
- [ ] Move existing drawer-only parsing and graph helpers out of `server.py` when doing so reduces duplicated Git parsing without changing monitor behavior.
- [ ] Continue using argument-vector subprocesses without shell interpolation.
- [ ] Add `--no-ext-diff`, `--no-textconv`, and `--no-color` to patch-producing Git commands so repository configuration cannot execute external diff helpers or alter the wire format.
- [ ] Put `--` before every browser-derived pathspec.
- [ ] Bound every Git process by timeout and always reap it.
- [ ] Implement a bounded-output Git runner for patches that stops reading and terminates the child when the byte limit is crossed.
- [ ] Do not capture an unbounded patch and truncate it after allocation.
- [ ] Set `GIT_DIFF_MAX_BYTES` to 1 MiB and `GIT_DIFF_MAX_LINES` to 10,000 lines unless an existing lower project-wide bound applies.
- [ ] Return `too_large: true` with no partial, unparsable patch when either patch bound is exceeded.
- [ ] Keep the existing 200-file list cap and exact total count.
- [ ] Parse all name and numstat data with NUL delimiters.
- [ ] Join name-status and numstat records without losing rename or copy source paths.
- [ ] Represent binary numstat markers as `additions: null`, `deletions: null`, and `binary: true`.
- [ ] Count aggregate additions and deletions from text files while reporting `binary_files` separately.
- [ ] Detect submodule entries and represent them separately from ordinary binary files.
- [ ] Preserve unusual printable filenames, spaces, tabs, Unicode, rename direction, copies, deletions, and `No newline at end of file` markers.
- [ ] Split porcelain-v2 `XY` status into staged, unstaged, and conflicted summaries.
- [ ] Permit one path to appear independently in staged and unstaged summaries when both sides differ.
- [ ] Put unresolved `u` records and unmerged `XY` combinations in the conflicted summary rather than duplicating them into ordinary groups.
- [ ] Include untracked files in unstaged summaries.
- [ ] Measure a bounded untracked text file as all additions and zero deletions.
- [ ] Treat an oversized, unreadable, symlink-escaping, or binary untracked file as unmeasured or binary without reading it unboundedly.
- [ ] Generate untracked patches with a tested Git-for-Windows-safe no-index comparison or a bounded internal unified-patch generator.
- [ ] Treat the no-index exit code indicating differences as success.
- [ ] Measure branch stats and patches from the merge base using the exact effective comparison ref returned to the client.
- [ ] Return comparison ahead and behind counts, not only the current one-sided `unlanded` count.
- [ ] Measure ordinary commit stats and patches against the chosen parent OID.
- [ ] Use Git's root-commit support for initial commits instead of a hardcoded SHA-1 empty-tree ID so SHA-256 repositories remain valid.
- [ ] Validate a merge parent against the commit's actual parent list before invoking a diff.
- [ ] Never run a network operation as part of Git drawer reads.

### Validation and security

- [ ] Resolve Git overview, graph, commit, and patch requests from a registered `project_id`.
- [ ] Validate a requested worktree against the exact roots from `git worktree list --porcelain` for the Project repository on every file or patch request.
- [ ] Reject worktree subdirectories and unrelated absolute paths when a worktree root is required.
- [ ] Validate repository-relative file paths without resolving deleted paths away.
- [ ] Reject absolute paths, empty paths, NULs, control characters, `.` segments, and `..` traversal.
- [ ] Do not follow a browser-supplied symlink to read an untracked file outside the validated worktree.
- [ ] Accept commit identifiers from Log only as full returned OIDs and verify the object is a commit.
- [ ] Bound comparison refs and run Git ref validation rather than relying on a regular expression alone.
- [ ] Escape all rendered paths and patch text through component rendering.
- [ ] Do not use raw HTML injection for patches or annotations.

## Implementation steps

### 1. Establish the baseline

- [ ] Run `git status --short` and preserve all unrelated user changes.
- [ ] Confirm the current worktree and branch follow the repository's worktree workflow before editing.
- [ ] Run the current focused backend Git tests and frontend Git helper tests before changing contracts.
- [ ] Record any pre-existing failure in the implementation notes section without treating it as caused by this work.

### 2. Add Project comparison-ref persistence

- [ ] Add `git_compare_ref: str | None` to `ProjectRecord` in `src/swe_mux/models.py`.
- [ ] Add nullable `git_compare_ref` to the canonical `projects` schema in `src/swe_mux/history.py`.
- [ ] Add an idempotent `ALTER TABLE projects ADD COLUMN git_compare_ref TEXT` migration for existing databases.
- [ ] Load, upsert, snapshot, and update the field through `HistoryIndex`, `ProjectManager`, and the Project API.
- [ ] Validate PATCH values as `null` or a bounded non-empty string before Git-specific resolution validation.
- [ ] Add the field to `frontend/src/types.ts`.
- [ ] Ensure Projects Manager edits preserve an existing Git comparison override when saving unrelated fields.
- [ ] Add persistence, migration, snapshot, null-reset, and invalid-input tests.

### 3. Implement the Git review domain module

- [ ] Add `src/swe_mux/git_review.py` with typed dataclasses or TypedDicts for comparison, stats, file changes, commit changes, and patch snapshots.
- [ ] Centralize bounded Git execution and error translation used by the new review reads.
- [ ] Reuse the existing monitor runner only when its output and timeout contracts are sufficient.
- [ ] Keep live session polling in `src/swe_mux/git_monitor.py`; do not add review-sized work to its five-second loop.
- [ ] Implement comparison-ref inference and candidate enumeration.
- [ ] Implement porcelain-v2 staged, unstaged, conflicted, rename, and untracked classification.
- [ ] Implement NUL-safe numstat parsing and name-status joining.
- [ ] Implement worktree overview aggregation with concurrency capped at four.
- [ ] Implement comparison ahead/behind counts for every non-bare checked-out branch.
- [ ] Implement lazy commit-change summaries for root, ordinary, and merge commits.
- [ ] Implement bounded single-file patches for every scope.
- [ ] Compute `patch_sha256` from the exact returned patch bytes.
- [ ] Return `head_oid` for local snapshots and exact commit/parent OIDs for commit snapshots.
- [ ] Add structured duration and result logs containing operation, Project id, repository/worktree identity, scope, ref or OID, path, counts, result, timeout, and truncation state.
- [ ] Exclude patch bodies, file contents, and annotation or review-packet text from all logs.

### 4. Wire and document backend routes

- [ ] Register the overview, commit-changes, and diff routes in `src/swe_mux/server.py`.
- [ ] Update graph routing to resolve a Project and delegate repository access.
- [ ] Use one error mapping for invalid input, unavailable measurements, timeout, oversized patch, and stale snapshot.
- [ ] Preserve `git_changed`, `worktree_created`, and `worktree_removed` event behavior.
- [ ] Return comparison-ref metadata with each overview so the frontend never reconstructs inference.
- [ ] Add request-level tests proving arbitrary cwd, ref, parent, and path injection cannot escape validation.

### 5. Add the frontend dependency behind a compatibility gate

- [ ] Install `react-diff-view` as a production frontend dependency and update `frontend/package.json` plus `frontend/package-lock.json` through npm.
- [ ] Use the existing Preact compatibility aliases from `frontend/vite.config.ts` and `frontend/tsconfig.json`; add explicit subpath aliases only if the built package requires them.
- [ ] Create a minimal checked-in component test exercising unified render, split render, line events, selection, and widgets under Preact.
- [ ] Run TypeScript, frontend tests, and a production build before building the full review UI.
- [ ] Inspect the production bundle and confirm it does not include a second React runtime.
- [ ] Import the diff renderer and its CSS lazily when the first inline preview or full modal opens.
- [ ] Do not enable syntax highlighting initially.
- [ ] If `react-diff-view` fails Preact compatibility, widget behavior, or duplicate-runtime checks, remove it from the dependency manifest and use the zero-dependency `diff` parser with a Preact-native table renderer that satisfies the same contracts.
- [ ] Do not ship compatibility shims that leave both React and Preact runtimes in the bundle.

### 6. Build pure frontend models and helpers

- [ ] Extend or split `frontend/src/gitWorktrees.ts` so response parsing remains defensive and UI components do not consume unchecked API objects.
- [ ] Add `frontend/src/gitReview.ts` for comparison scopes, annotation anchors, patch snapshots, responsive view-mode decisions, stale-state reduction, and review-packet generation.
- [ ] Represent annotation anchors as `{path, side: 'old'|'new', start, end, patchHash}`.
- [ ] Prevent a selected range from crossing files, sides, or snapshots.
- [ ] Use old-side line numbers for deletions and new-side line numbers for additions.
- [ ] Preserve both line coordinates for context rows and use the gutter the user selected.
- [ ] Generate stable annotation keys without using annotation text.
- [ ] Format text file stats as `+N -M`, binary files as `binary`, and unavailable stats as `unmeasured`.
- [ ] Generate review packets deterministically from frozen snapshots and annotations.
- [ ] Add pure tests for every parser, formatter, anchor, range, responsive, stale-state, and packet rule.

### 7. Upgrade Map

- [ ] Replace the current combined `LOCAL - NOT COMMITTED` group with `CONFLICTS`, `UNSTAGED`, and `STAGED` groups as applicable.
- [ ] Rename the branch group to `BRANCH - VS <effective ref>`.
- [ ] Hide the branch group and comparison counts when no ref is available while leaving local groups intact.
- [ ] Show per-group file count, aggregate additions, aggregate deletions, and binary-file count.
- [ ] Show `+N -M` or `binary` on every file row.
- [ ] Show neutral ahead and behind comparison metrics on the compact worktree row.
- [ ] Keep upstream divergence separately labelled.
- [ ] Add the comparison selector to the Git toolbar with Auto, candidates, current source, unavailable explanation, save state, and reset behavior.
- [ ] Preserve worktree expansion, creation, removal safeguards, live-session counts, detached labels, locks, and prunable warnings.
- [ ] Do not let comparison-selector interaction collapse a worktree or trigger another row action.

### 8. Upgrade Log

- [ ] Make commit rows keyboard and pointer expandable while connector-only graph rows remain inert.
- [ ] Fetch commit file changes only on expansion.
- [ ] Cache successful summaries by full commit OID and selected parent OID for the mounted Git tab.
- [ ] Keep one expanded commit at a time unless testing proves multiple expanded commits remain cheap and clear.
- [ ] Show aggregate file count, additions, deletions, and binary-file count in the expanded commit.
- [ ] Show `+N -M`, status, rename source, and binary state on every commit file row.
- [ ] Label root commits as `initial commit`.
- [ ] Label ordinary commits with their single parent abbreviation.
- [ ] Label merge commits `vs first parent` by default and provide an explicit parent selector.
- [ ] Invalidate only mutable Map caches on Git events; immutable commit summaries may remain cached by OID.
- [ ] Preserve the current bounded 80-to-200 commit graph and Git-supplied lane topology.

### 9. Build reusable file rows and inline previews

- [ ] Add a reusable Git file-row component used by every Map group and expanded Log commit.
- [ ] Make the filename action open the full review modal at that file.
- [ ] Add a distinct caret action that toggles an inline preview.
- [ ] Add a distinct `Open current file` action with a clear tooltip and disabled reason.
- [ ] Stop event propagation between filename, caret, open-file, and parent expansion actions.
- [ ] Render inline previews as unified diffs only.
- [ ] Keep line wrapping off and expose horizontal scrolling.
- [ ] Cap inline preview height between 300 and 400 CSS pixels with internal vertical scrolling.
- [ ] Render a bounded subset of hunks or at most 500 diff rows inline.
- [ ] Show an explicit omitted-hunks message when the inline preview is partial.
- [ ] Permit only one inline preview per change group or expanded commit to limit DOM and parser cost.
- [ ] Add `Open full diff` inside the preview.
- [ ] Do not permit annotation editing inside the inline preview.
- [ ] Show typed binary, unavailable, deleted, and oversized states instead of an empty diff.

### 10. Build the full review modal

- [ ] Add `frontend/src/GitReviewModal.tsx` using the repository's modal layer and `useModalFocus` conventions.
- [ ] Render the modal atop the mobile utility drawer without unmounting the Git tab, so closing returns to the same Map or Log position.
- [ ] Make the modal full-screen on mobile and bounded to the viewport on desktop.
- [ ] Add an accessible heading naming Project, scope, worktree or commit, parent or comparison ref, and selected file.
- [ ] Add a desktop file navigator and a compact mobile file selector.
- [ ] Add previous and next file controls with keyboard support.
- [ ] Add unified/split and wrap toggles.
- [ ] Use `ResizeObserver` on the diff content region, not global viewport width, for automatic layout selection.
- [ ] Default to split at content widths of at least 900 CSS pixels and unified under that threshold.
- [ ] Preserve an explicit user toggle for the lifetime of the modal even if the container later resizes.
- [ ] Allow split mode on a narrow display with horizontal scrolling rather than silently overriding the user.
- [ ] Keep wrapping off by default.
- [ ] Load a file patch on demand and cache it by scope, locator, commit/parent, and patch hash for the modal lifetime.
- [ ] Show loading, retry, stale, binary, oversized, and unavailable states.
- [ ] Disable annotation controls when no textual patch is present.
- [ ] Trap focus, restore prior focus on close, close on Escape, and label all controls for assistive technology.

### 11. Add line annotations

- [ ] Make old and new line-number gutters the annotation targets.
- [ ] Leave code-cell clicks available for normal text selection and copying.
- [ ] Open a compact inline composer directly under the selected line or range instead of opening a nested modal.
- [ ] Support click for one line and Shift-click for a same-side contiguous range.
- [ ] Provide Save, Cancel, Edit, and Delete annotation actions.
- [ ] Show saved annotation widgets under their anchored diff rows.
- [ ] Show an annotation count in the modal footer and beside files containing annotations.
- [ ] Keep annotations while moving among files inside the same modal.
- [ ] Clear every annotation without confirmation when the modal closes, as required by the ephemeral contract.
- [ ] Offer an explicit `Clear annotations` action while the modal is open.
- [ ] Do not place annotation text in DOM attributes, analytics, errors, logs, or event payloads.
- [ ] Handle a stale local review by keeping existing annotations visible on the frozen patch while disabling silent refresh.

### 12. Generate, copy, and send review packets

- [ ] Add `Copy review packet` to the modal footer.
- [ ] Add `Send to agent...` using the existing `SendToAgentRequest` and explicit target/delivery flow.
- [ ] Generate the same bounded packet for clipboard and send-to-agent actions.
- [ ] Include Project name and id, repository root, worktree root when applicable, scope, effective comparison ref or full commit and parent OIDs, HEAD OID for local work, and patch SHA-256.
- [ ] Group annotations by file in stable file and line order.
- [ ] Include old/new side, exact line range, annotation text, and a bounded unified hunk excerpt for every annotation.
- [ ] Include a reproducible Git comparison description without constructing an unsafe shell command from unescaped paths.
- [ ] Mark omitted context, truncated files, oversized patches, and stale local snapshots explicitly.
- [ ] Do not include complete patches by default.
- [ ] Add an `Include full loaded patches` option that remains subject to the existing review-packet size bound and names any omitted material.
- [ ] Add `Copy raw patch` for the selected loaded file as a separate action.
- [ ] Use `withoutClipboardCapture` so review packets and raw patches do not evict the user's clipboard-history snippets.
- [ ] Show an in-modal recovery textarea when the browser refuses the clipboard write.
- [ ] Keep the modal open after copy or send so the user can continue reviewing.
- [ ] Never submit text directly to a PTY from the diff modal.

### 13. Open the correct current file

- [ ] Treat Map files as belonging to the exact worktree row, not merely to the canonical Project root.
- [ ] Treat Log files as historical paths and label the action `Open current file` because it opens a working copy, not the blob at the commit.
- [ ] For Log, default the current-file target to the canonical Project worktree and state that target in the tooltip.
- [ ] Disable Log current-file opening for deleted or currently absent paths.
- [ ] Add a persisted `worktree-file` resource identity for Map files outside the canonical Project root.
- [ ] Encode the validated worktree root and repository-relative path unambiguously in the resource identity without allowing delimiter or traversal ambiguity.
- [ ] Preserve existing canonical `file:` resource identities unchanged.
- [ ] Extend `frontend/src/layout.ts`, layout parsing/migration tests, resource titles, drag behavior, close behavior, and workspace rendering for `worktree-file`.
- [ ] Add Project file API support for an optional exact worktree root or dedicated worktree-file routes.
- [ ] Validate the root against `git worktree list --porcelain` for the Project repository on every read, content, save, reveal, copy, and watch operation.
- [ ] Reuse bounded file inspection, image rules, revision-checked writes, and context-menu behavior after root validation.
- [ ] Keep `.swe-mux` notes, config, ignores, and Project ownership anchored to the canonical Project root.
- [ ] Do not allow a worktree-file locator to broaden Project Actions, arbitrary file browsing, or session spawn containment.
- [ ] Include the worktree root in file-change watcher identity so equal relative paths in two worktrees never cross-refresh each other.
- [ ] Show a recoverable unavailable tab when the worktree was removed or no longer belongs to the repository.
- [ ] Verify that opening `src/example.ts` from two sibling worktrees creates two distinct tabs and reads the correct bytes from each.

### 14. Integrate through the utility drawer

- [ ] Extend `GitTab` props with `onOpenFile`, `onOpenWorktreeFile`, and `onSendToAgent` callbacks as required by the final component split.
- [ ] Pass those callbacks through `frontend/src/UtilityDrawer.tsx` from `frontend/src/App.tsx`.
- [ ] Preserve the existing mobile behavior where the drawer remains available behind the full-screen modal and returns after close.
- [ ] Opening a workspace file tab from mobile may close the drawer according to existing Files-tab behavior.
- [ ] Avoid adding Git-specific global state to `App.tsx`; keep review-session state within the Git surface.
- [ ] Keep Map and Log scroll positions stable when opening and closing the review modal.

### 15. Styling, accessibility, and responsive behavior

- [ ] Add Git review styles to `frontend/src/style.css` using the existing color variables and monospace typography.
- [ ] Keep status meaning available through text or shape and never color alone.
- [ ] Ensure additions, deletions, binary markers, annotation indicators, selected rows, and stale warnings meet readable contrast in the existing theme.
- [ ] Keep file paths truncatable in compact rows and fully available through title or accessible text.
- [ ] Provide visible keyboard focus on commit rows, file rows, line gutters, toggles, selectors, annotation controls, and modal actions.
- [ ] Ensure file and commit expansion use `aria-expanded` and associate controls with expanded regions.
- [ ] Ensure annotation widgets and copy/send results use appropriate live-region behavior without reading the entire diff.
- [ ] Respect `prefers-reduced-motion`.
- [ ] Test at the minimum docked drawer width, maximum drawer width, desktop modal width, narrow mobile portrait, and mobile landscape.
- [ ] Confirm nested inline-preview scrolling does not trap page or drawer scrolling on touch devices.

### 16. Performance and refresh behavior

- [ ] Keep Map overview work on explicit drawer reads and events, not in the session Git monitor.
- [ ] Keep commit-change lists lazy and cache them by immutable OID and parent.
- [ ] Keep patches lazy and fetch one file only when inline preview or modal navigation requests it.
- [ ] Cancel or ignore stale frontend requests by generation token when Project, comparison ref, commit parent, or selected file changes.
- [ ] Bound concurrent worktree measurements and commit/patch requests.
- [ ] Avoid rendering every hidden diff in the DOM.
- [ ] Confirm a large but allowed patch does not block drawer interaction unacceptably.
- [ ] Confirm an oversized patch is rejected before frontend parsing.
- [ ] Preserve explicit Refresh behavior for worktrees or commits created outside swe-mux events.
- [ ] Do not add a browser polling timer.

## Test matrix

### Backend unit and route tests

- [ ] Test comparison override, Auto reset, origin HEAD, single remote HEAD, local fallback, no candidate, stale override, and invalid ref behavior.
- [ ] Test Project schema creation, migration from a database without `git_compare_ref`, row round-trip, PATCH validation, and unrelated-update preservation.
- [ ] Test porcelain-v2 classification for index-only, worktree-only, both, untracked, rename, copy, delete, type change, conflict, and unusual filenames.
- [ ] Test NUL numstat parsing for text, binary, rename, copy, Unicode, spaces, tabs, and empty files.
- [ ] Test aggregate additions, deletions, binary counts, exact totals, and 200-file truncation.
- [ ] Test branch comparison against local and remote-tracking refs with ahead and behind counts.
- [ ] Test absent comparison refs and Git failures never produce zero-valued clean claims.
- [ ] Test ordinary, root, and merge commit summaries.
- [ ] Test first-parent default and rejection of a parent that is not attached to the commit.
- [ ] Test staged, unstaged, branch, commit, deletion, rename, untracked, binary, submodule, and no-newline patches.
- [ ] Test patch timeout, byte cap, line cap, and child-process reaping.
- [ ] Test `--no-ext-diff`, `--no-textconv`, `--no-color`, and `--` placement in invoked arguments.
- [ ] Test exact worktree-root validation, traversal rejection, symlink escape rejection, invalid OID/ref rejection, and Project isolation.
- [ ] Test worktree-file reads, writes, revision conflicts, watchers, removed-worktree recovery, and same-relative-path isolation.
- [ ] Test logs contain identifiers, result, duration, and counts but never patch or annotation content.

### Frontend pure tests

- [ ] Test defensive parsing of all new overview, summary, comparison, commit, and patch response shapes.
- [ ] Test status and numstat formatting, including binary and unavailable values.
- [ ] Test comparison labels and Auto source explanations.
- [ ] Test annotation single-line, range, old/new side, invalid cross-side range, edit, delete, and deterministic ordering.
- [ ] Test review-packet contents, bounds, truncation notices, stale markers, and omission of unannotated full patches by default.
- [ ] Test automatic layout threshold, explicit override persistence for the modal lifetime, and reset on modal close.
- [ ] Test stale-event reduction does not replace the frozen patch.
- [ ] Test canonical file and worktree-file resource ID round-trips and malformed-ID rejection.
- [ ] Register every new frontend test module in `frontend/test/all.ts`.

### Component and renderer tests

- [ ] Test `react-diff-view` under Preact for unified, split, line events, widgets, no wrapping, and text selection.
- [ ] Test Map renders conflicts, unstaged, staged, comparison changes, stats, binary markers, and unavailable comparison state.
- [ ] Test Log expansion is lazy, parent-aware, cached, and non-interactive on connector rows.
- [ ] Test file-row filename, preview caret, and open-file actions do not trigger each other.
- [ ] Test inline preview height, scrolling, one-open-preview rule, omitted-hunk message, and full-modal action.
- [ ] Test modal file navigation, adaptive layout, explicit toggle, wrap toggle, retry, and close-state destruction.
- [ ] Test annotation composer focus, Save, Cancel, Edit, Delete, Shift-range, count, and file badge.
- [ ] Test clipboard success, clipboard refusal recovery, raw-patch copy, and clipboard-ring bypass.
- [ ] Test send-to-agent opens the existing explicit dialog with the generated packet and never writes directly to a PTY.
- [ ] Test keyboard navigation, Escape, focus trap, focus restoration, accessible names, and live regions.
- [ ] Add Playwright coverage for wide split mode, narrow unified mode, narrow manual split with horizontal scrolling, and mobile full-screen presentation.

### Full verification

- [ ] Run `uv run pytest tests -q -m "not live_agent and not live_subagent and not live_telemetry and not live_quota"`.
- [ ] Run `uv run ruff check src/swe_mux tests packaging`.
- [ ] Run `uv run mypy`.
- [ ] Run `npx tsc --noEmit` from `frontend/`.
- [ ] Run `npm test` from `frontend/`.
- [ ] Run the relevant Playwright renderer tests from `frontend/`.
- [ ] Run `npm run build` from `frontend/`.
- [ ] Confirm the production build contains one Preact runtime and lazy-loads the diff renderer.
- [ ] Run `.worktree-verify` before integrating a completed worktree branch.

## Manual acceptance checklist

- [ ] Open Git Map for a repository with no configured comparison override and confirm an inferred ref is named explicitly.
- [ ] Open Git Map for a repository without a credible default ref and confirm local Git information still works without false comparison claims.
- [ ] Select another comparison ref, reload the app, confirm the Project-local override persists, then reset to Auto.
- [ ] Confirm selecting a comparison ref does not modify `.swe-mux/config.toml` or dirty the repository.
- [ ] Confirm Map separates conflicted, unstaged, and staged files and shows per-file plus aggregate stats.
- [ ] Confirm a path changed in both index and working tree appears in both relevant groups with distinct diffs.
- [ ] Confirm branch-relative files and ahead/behind counts use the displayed effective ref.
- [ ] Expand a normal, root, and merge commit in Log and confirm file stats and parent semantics.
- [ ] Expand an inline file preview and confirm bounded unified rendering, no wrapping, and independent scroll.
- [ ] Open the full modal wide and confirm split default.
- [ ] Open the full modal narrow and confirm unified default.
- [ ] Manually switch a narrow modal to split and confirm usable horizontal scrolling.
- [ ] Add old-side, new-side, single-line, and range annotations across multiple files.
- [ ] Trigger a local Git change while the modal is open and confirm the frozen review becomes stale without moving annotations.
- [ ] Copy a review packet and confirm identifiers, line anchors, comments, bounded context, and truncation markers are correct.
- [ ] Confirm the copied packet does not appear in the clipboard-history ring.
- [ ] Send the same packet through the existing agent picker and confirm explicit delivery behavior.
- [ ] Close and reopen the modal and confirm every annotation and view-specific review state was discarded.
- [ ] Open the same relative path from the canonical tree and a sibling worktree and confirm distinct tabs show distinct content.
- [ ] Remove a worktree with an open worktree-file tab and confirm the tab becomes recoverably unavailable rather than reading another path.
- [ ] Verify binary, deleted, renamed, untracked, submodule, oversized, and unusual-name files have explicit non-empty states.
- [ ] Verify the feature on desktop, mobile portrait, mobile landscape, keyboard-only navigation, and reduced-motion mode.

## Documentation synchronization

- [ ] Update `.docs/design/features/git.md` with neutral comparison refs, inferred/default behavior, staged/unstaged/conflicted summaries, numstat, commit expansion, patch snapshots, inline previews, full review sessions, ephemeral annotations, and refresh semantics.
- [ ] Update `.docs/design/features/ui.md` with Git modal placement, responsive unified/split behavior, annotation interaction, keyboard behavior, and mobile return flow.
- [ ] Update `.docs/design/features/project-resources.md` with worktree-file identity, validation, watcher scope, current-versus-historical semantics, and removed-worktree failure behavior.
- [ ] Update `.docs/design/features/projects.md` with the nullable local `git_compare_ref` Project override and Auto semantics.
- [ ] Update `.docs/design/features/workspace-layout.md` with the persisted worktree-file resource identity.
- [ ] Update `.docs/design/interfaces.md` with Project field, Git overview, commit changes, diff snapshot, worktree-file routes, typed errors, and events.
- [ ] Update `.docs/design/data-model.md` with the `projects.git_compare_ref` column and non-persistence of annotations.
- [ ] Update `.docs/technical/backend/packages.md` with `git_review.py` ownership and worktree-file validation boundaries.
- [ ] Update `.docs/technical/frontend/packages.md` with Git review modules, lazy diff dependency, annotation ownership, and review-packet generation.
- [ ] Update `.docs/technical/frontend/workspace-state.md` with worktree-file parsing and persistence rules.
- [ ] Update `.docs/CLAUDE.md` routing if the new worktree-file and Git-review paths need additional routed documents.
- [ ] Verify every documented file path exists and every contract matches the implemented code.
- [ ] Keep each complete sentence on its own physical Markdown line.
- [ ] Remove stale hardcoded comparison-branch, `trunk`, `unlanded`, and `landed` descriptions from current-state documentation where they described this Git UI.

## Applying the completed update

- [ ] Determine whether the live daemon serves source assets or the frozen desktop bundle before claiming the UI is updated.
- [ ] For source mode, build the frontend with `cd frontend && npm run build`, restart the daemon with the session-preserving route for backend changes, and reload the UI.
- [ ] Compare the live `assets/index-*.css` hash with `src/swe_mux/static/index.html` to verify which frontend is served.
- [ ] If the live app is frozen, use `uv run python packaging/redeploy_desktop.py` or the UI's `Rebuild + redeploy app (keep sessions)` action.
- [ ] Never run `muxd --shutdown`, kill `swe-mux-supervisor.exe`, or use image-wide task killing during the update.
- [ ] Confirm all live terminal and agent sessions survive the applied update.

## Final completion gate

- [ ] Confirm every checkbox in this document is complete.
- [ ] Confirm Git Map and Log share components but preserve their distinct comparison semantics.
- [ ] Confirm no setup is required for repositories with an inferable comparison ref.
- [ ] Confirm repositories without an inferable ref remain useful and make no false base-relative claims.
- [ ] Confirm comparison overrides are Project-local, optional, visible, resettable, and do not dirty repositories.
- [ ] Confirm staged, unstaged, conflicted, branch, commit, merge, root, binary, rename, deletion, untracked, submodule, oversized, timeout, and unavailable cases are handled.
- [ ] Confirm annotations are ephemeral and no annotation or patch content is persisted or logged.
- [ ] Confirm review packets are bounded, reproducible, clipboard-ring-safe, and usable through the existing agent-send flow.
- [ ] Confirm worktree-aware file tabs cannot escape Git-validated roots or open the same relative path from the wrong checkout.
- [ ] Confirm all automated and manual verification passed.
- [ ] Confirm all routed documentation reflects the implemented current state.
- [ ] Move this completed plan to `.docs/development/archive/GIT_DIFF_REVIEW_IMPLEMENTATION.md`.

## Implementation notes

- Record only concise deviations, discovered constraints, and their final resolutions in this section.
- Do not use this section to defer required work or leave open design decisions.
