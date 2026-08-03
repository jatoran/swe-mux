# Agent Context

## Purpose

Agent Context is the Project-selected, read-only view of Project/global instruction files and
learned-memory files that Claude Code or Codex may carry between conversations. It makes
otherwise hidden provider state inspectable before switching agents. It does not inject context
into a session, infer what an already-running process loaded, or create a second memory authority.

The only ordinary mutation in the feature is a user-triggered whole-file copy between the two
root instruction files. There is no automatic, watched, scheduled, or startup sync.

## Surface

The utility drawer's **Context** tab is titled **Agent Context** in its body and follows Notes in
the project-scoped block. It contains:

- the Project-root `CLAUDE.md` and `AGENTS.md`, including typed availability, byte size,
  modification time, line-ending style, and whether each revision changed since this daemon
  run began;
- the fixed global instruction sources `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md` with the
  same read-only metadata;
- an `in_sync | different | missing` comparison after normalizing CRLF/CR to LF;
- one collapsed **Memories** disclosure whose badge counts the complete provider inventory;
  expanding it shows Claude's learned `MEMORY.md` and Markdown topic files plus provider status;
- an explicit Codex state (`disabled`, `unsupported`, or `unreadable`) rather than an empty list
  that implies no memory exists;
- a read-only preformatted viewer and manual rescan; one `sync…` button opens a focus-trapped
  modal containing both copy directions, diff confirmation, and recent restore points.

Every body is rendered as text, never as an editor. Selecting or rescanning a source does not
write it. A Project must be selected; live session focus and runtime cwd do not retarget the
inventory.

## Source discovery

Only four instruction sources are recognized: `<Project>/CLAUDE.md`, `<Project>/AGENTS.md`,
`~/.claude/CLAUDE.md`, and `~/.codex/AGENTS.md`. Nested instruction files are outside the
contract. Global files are inspectable only; manual synchronization remains between the two
Project-root files.

Claude learned memory comes from `~/.claude/settings.json:autoMemoryDirectory` when explicitly
configured. Otherwise the daemon derives Claude's project directory under
`~/.claude/projects/<project-key>/memory`. For a Git worktree, `<project-key>` is based on the
primary checkout obtained from `git rev-parse --git-common-dir`, so sibling worktrees see the
same provider memory. Outside Git it uses the registered Project root. Only direct `.md` children
are listed, with `MEMORY.md` first.

Codex is intentionally conservative. The daemon reads only `[features].memories` from
`~/.codex/config.toml`. When enabled it reports `unsupported`: Codex currently exposes no stable,
documented project-memory file inventory. swe-mux does not reverse-engineer or read its private
SQLite memory store. When the flag is absent/false it reports `disabled`; malformed config is
`unreadable`.

Provider paths never cross the HTTP boundary. The two global rows use fixed `~/…` display labels,
not resolved host paths. Browser reads address opaque source IDs that the daemon maps back through
the fixed instruction allowlist or the freshly validated Claude memory filename shape. Source
reads are UTF-8, regular-file only, reject symlinks, and cap each file at 512 KiB. An inventory
contains at most 128 Claude memory rows while retaining the complete count. Blocking Git and
filesystem work runs off the aiohttp event loop.

## Manual instruction sync

The two allowed directions are:

```text
CLAUDE.md → AGENTS.md
AGENTS.md → CLAUDE.md
```

The source must exist and be readable. The destination may be missing, in which case it is
created. A first click asks the daemon for a bounded unified diff and the SHA-256 revisions of
both files (`missing` is the destination sentinel). A separate confirmation submits those exact
revisions. If either file changed meanwhile, the daemon returns `409 revision_conflict` and
writes nothing; the UI rescans and requires a new review.

Commit replaces the complete destination. Logical source content is normalized, then written
using the destination's existing CRLF/LF convention (LF when creating it). Existing destination
permissions are retained where the platform supports them. The write uses a same-directory
temporary file, flush + fsync, and `os.replace`; a source or destination symlink is refused.

Before replacement the daemon stores the exact prior destination bytes, or a record that it was
missing, under `<data_dir>/agent-context-backups/<hashed-project-id>/`. Restore is also
revision-guarded and creates a new restore point for the state it replaces. Restoring the
"missing" record removes the file created by sync. Backup IDs, not backup paths, cross HTTP; the
drawer lists the 20 newest valid manifests.

## Freshness and events

Existing Projects have their instruction revisions captured when the daemon starts. A Project
registered later takes its baseline on first inventory. `changed_since_start` is informational,
not a concurrency guard; sync always uses the current preview revisions.

Successful sync/restore emits `agent_context_changed` with Project identity, operation, target
or direction, and resulting revision. The initiating drawer refreshes immediately. Other clients
can manually rescan; no filesystem watcher is kept alive for hidden provider directories.

## Non-goals and future boundary

- no automatic instruction synchronization or canonical-file policy;
- no editing of instructions or learned memory in the drawer;
- no provider-memory writes, deletion, migration, or merge;
- no claim that a running agent has loaded a newly changed file;
- no MCP exposure in this wave.

The planned MCP bridge may later expose the same project-scoped inventory/read contract so agents
can inspect all providers' memories. It must remain read-only and must not silently inject, copy,
or synchronize provider state.

## Code map

- Backend domain/safety: `src/swe_mux/agent_context.py`
- HTTP composition: `src/swe_mux/server.py`
- Drawer body/types: `frontend/src/AgentContextTab.tsx`, `frontend/src/agentContext.ts`
- Host/registration/icon: `frontend/src/UtilityDrawer.tsx`, `frontend/src/drawerTabs.ts`,
  `frontend/src/railIcons.tsx`
- Tests: `tests/test_agent_context.py`, `frontend/test/agentContext.test.ts`
