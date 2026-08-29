# Agent Context

## Purpose

Agent Context is the Project-selected, read-only view of descriptor-declared Project/global
instruction files and learned-memory files that a registered harness may carry between conversations. It makes
otherwise hidden provider state inspectable before switching agents. It does not inject context
into a session, infer what an already-running process loaded, or create a second memory authority.

Ordinary mutations are explicit whole-file copy, link, unlink, and restore operations between two distinct descriptor-declared Project-root instruction files.
The user chooses which file remains canonical before creating a link.
Copy remains the default one-time operation.
There is no automatic, watched, scheduled, or startup synchronization.

## Surface

The utility drawer's **Agent → Instructions** segment is titled **Instructions & Memory** in its body and follows Notes in
the project-scoped block. It contains:

- an initially expanded **Project instructions** disclosure for every distinct Project-root
  instruction file declared by a registered harness, including the declaring harnesses, typed
  availability, entrypoint kind, byte size, modification time, line-ending style,
  and whether each revision changed since this daemon run began;
- an initially collapsed **Global instructions** disclosure for the corresponding
  descriptor-declared global files, with the same read-only metadata;
- a `linked | in_sync | different | missing` comparison after normalizing CRLF/CR to LF;
- one initially collapsed **Memories** disclosure whose badge counts the complete provider
  inventory; expanding it shows each harness's declared memory provider and explicit capability
  state, with harness and entrypoint attribution on each available source, using the same high-contrast file rows as the instruction disclosures;
- a read-only preformatted viewer and manual rescan; one `sync…` button opens a focus-trapped
  modal containing copy-once controls, both canonical link directions, unlink confirmation, diff confirmation, platform caveats, and recent restore points.

On a fine-pointer desktop, right-clicking a row backed by a real regular file or a safe managed instruction link opens the same one-action **Open in default explorer** menu used by the Project file browser.
Managed links reveal their canonical target.
Missing, directory, and unsupported link rows cannot expose that action, and reveal changes no content.

Every body is rendered as text, never as an editor. Selecting or rescanning a source does not
write it. A Project must be selected; live session focus and runtime cwd do not retarget the
inventory.

## Source discovery

Instruction discovery comes from the harness registry.
Harnesses may declare a Project-root instruction filename, a corresponding global instruction
path, both, or neither.
Harnesses that share a filename share one inventory row with every reader attributed.
Nested instruction files are outside the contract.
Global files are inspectable only; manual synchronization is offered only between distinct
declared Project-root files.

Claude learned memory comes from `~/.claude/settings.json:autoMemoryDirectory` when explicitly
configured. Otherwise the daemon derives Claude's project directory under
`~/.claude/projects/<project-key>/memory`. For a Git worktree, `<project-key>` is based on the
primary checkout obtained from `git rev-parse --git-common-dir`, so sibling worktrees see the
same provider memory. Outside Git it uses the registered Project root. Only direct `.md` children
are listed, with `MEMORY.md` first.

Codex is intentionally conservative. Its descriptor tells the daemon to read only `[features].memories` from
`~/.codex/config.toml`. When enabled it reports `unsupported`: Codex currently exposes no stable,
documented project-memory file inventory. swe-mux does not reverse-engineer or read its private
SQLite memory store. When the flag is absent/false it reports `disabled`; malformed config is
`unreadable`.

Harness descriptors that declare no stable memory inventory return an explicit `unsupported`
provider result rather than inheriting another harness's source or appearing empty.

Provider paths never cross the HTTP boundary.
Global rows use stable `~/...` display labels instead of resolved host paths.
Browser reads address opaque source IDs that the daemon maps back through the descriptor-derived instruction allowlist or the freshly validated Claude memory filename shape.
Reads are UTF-8 and capped at 512 KiB.
Global instructions and memories reject every symlink.
A Project-root instruction symlink is readable only when its stored target is the exact relative filename of another declared root instruction file and that target is an existing non-symlink regular file.
Arbitrary, absolute, broken, external, self-referential, and chained links remain unsupported.
An inventory contains at most 128 Claude memory rows while retaining the complete count.
Blocking Git and filesystem work runs off the aiohttp event loop.

Reveal accepts only an opaque source ID and applies the same managed-link rules before delegating to the shared OS file-manager launcher.
Resolved host paths still never cross HTTP.

## Manual instruction operations

The daemon returns the currently allowed source-to-target directions for copy and link operations in each inventory.
Each direction is between two distinct descriptor-declared Project-root instruction files.
The UI does not own a fixed filename matrix.

The source must exist and be readable. The destination may be missing, in which case it is
created. A first click asks the daemon for a bounded unified diff and the SHA-256 revisions of
both files (`missing` is the destination sentinel). A separate confirmation submits those exact
revisions. If either file changed meanwhile, the daemon returns `409 revision_conflict` and
writes nothing; the UI rescans and requires a new review.

Commit replaces the complete destination. Logical source content is normalized, then written
using the destination's existing CRLF/LF convention (LF when creating it). Existing destination
permissions are retained where the platform supports them. The write uses a same-directory
temporary file, flush + fsync, and `os.replace`; a source or destination symlink is refused.

### Persistent links

A link preview uses the same descriptor direction and revision guard as copy.
The selected source must be an existing non-symlink regular file and remains canonical.
Commit replaces the other descriptor file with a relative symbolic link containing only the canonical filename.
Unlink materializes the canonical bytes back into the linked filename, so both paths remain ordinary independent files afterward.
Reversing canonical direction therefore requires unlinking the active relationship first.
Link, unlink, and restore all use same-directory staging and atomic replacement.
On Windows, link creation can require Developer Mode or symbolic-link privilege, and swe-mux reports the platform error without attempting elevation or changing either instruction file.
Git portability remains repository-dependent because a checkout with `core.symlinks=false` materializes a tracked link as a plain text file, and editors that save by atomic replacement can turn a live link back into a regular file.
Before every copy, link, unlink, or restore, the daemon records the exact prior destination entry under `<data_dir>/agent-context-backups/<hashed-project-id>/`.
The typed manifest distinguishes a missing path, regular-file bytes and mode, and a managed relative-link target.
Restore is revision-guarded and creates a new restore point for the state it replaces.
Restoring a missing record removes the path created by the later operation.
Restoring a link revalidates the canonical file and stages link creation before changing the current entry.
Backup IDs, not backup paths, cross HTTP, and the drawer lists the 20 newest valid manifests.

## Freshness and events

Existing Projects have their instruction revisions captured when the daemon starts.
A Project registered later takes its baseline on first inventory.
`changed_since_start` is informational, not a concurrency guard.
Every copy, link, unlink, and restore commit uses the current preview or inventory revision.

Successful copy, link, unlink, and restore operations emit `agent_context_changed` with Project identity, operation, target or direction, and resulting revision.
The initiating drawer refreshes immediately.
Other clients can manually rescan, and no filesystem watcher is kept alive for hidden provider directories.

### The inventory is cached on both ends

Opening the tab re-read and re-normalized every instruction file - up to four project files plus the
global ones, decoded, hashed, and compared against each other for the in-sync verdict - with nothing
retained on either side of the wire. This tab is not `keepMounted`, so every visit paid it in full, in
front of an empty pane.

The daemon memoizes the inventory per Project on a **stat signature** over exactly the files it reads
(`_inventory_signature`): path, `st_mtime_ns`, and size, for the project and global instruction files,
the Claude memory directory, and `~/.claude/settings.json`. Size beside mtime, because the two together
are what an editor moves and either alone is not. An absent file is part of the signature, so a file
appearing invalidates rather than reading as unchanged. The browser keeps the last reading per Project
in a bounded module-scoped store, the same shape the sibling Agent Environment segment already uses, so a
remount draws immediately and the fetch replaces it.

**`rescan` bypasses both.** That is what keeps the stat signature honest: it cannot see a same-size
rewrite landing in the same nanosecond as the read before it, and a reader who believes they are looking
at one presses rescan. Every ordinary write reaches the caches through `agent_context_changed` and the
path-filtered `project_files_changed` refresh above.

## Non-goals and future boundary

- no automatic instruction synchronization or automatic canonical-file choice;
- no editing of instructions or learned memory in the drawer;
- no provider-memory writes, deletion, migration, or merge;
- no claim that a running agent has loaded a newly changed file;
- no semantic search or bulk prompt injection through the MCP bridge.

The MCP `memory_sources` and `read_memory` tools expose the same per-Project inventory/read
contract so agents can inspect available provider sources.
They answer for the caller's own Project unless the call names another one or `"fleet"`
(`mux-mcp.md`); a fleet inventory labels every source with the Project it came from, and a
source id still belongs to exactly one Project.
They remain read-only and never silently inject, copy, or synchronize provider state.

## Code map

- Backend domain/safety: `src/swe_mux/agent_context.py`
- HTTP composition: `src/swe_mux/server.py`
- Drawer body/types: `frontend/src/AgentContextTab.tsx`, `frontend/src/agentContext.ts`
- Host/registration/icon: `frontend/src/UtilityDrawer.tsx`, `frontend/src/drawerTabs.ts`,
  `frontend/src/railIcons.tsx`
- Tests: `tests/test_agent_context.py`, `frontend/test/agentContext.test.ts`
