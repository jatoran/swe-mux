# Public roadmap audit

Reviewed against source version 0.2.6 on 2026-09-06.
The public page is generated from `site/content/roadmap.html`.
This review distinguishes shipped functionality from the remaining work rather than relying on unchecked historical plan boxes.

| Previous item | Result | Evidence |
|---|---|---|
| macOS | Browser/daemon support already exists; native desktop UI and installers are now explicitly planned for Linux and macOS | `.docs/design/features/desktop-shell.md`, `pyproject.toml`, `.github/workflows/ci.yml` |
| Windows window/tray without reinstall | Moved to Already available | `src/swe_mux/desktop.py`, `src/swe_mux/onboarding.py`, `pyproject.toml` |
| Agent reach without MCP and authenticated loopback | Agent-mode CLI authentication shipped; describe that bounded claim rather than claiming every operator route has a new authentication layer | `src/swe_mux/agent_surfaces.py`, `src/swe_mux/agent_authority.py`, `.docs/design/features/agent-skill-delivery.md` |
| Session restore | Cold and inactive recovery shipped; it restores records and resumability, not running processes after power loss | `src/swe_mux/session_recovery.py`, `.docs/design/features/session-recovery.md` |
| Hooks on mux signals | Universal hooks over normalized events already ship | `.docs/design/features/automation.md`, `src/swe_mux/automation.py` |
| Plugin capabilities | Added explicit expansion; current action, terminal-pane, event, startup and link capabilities remain listed as available | `src/swe_mux/plugin_manifest.py`, `src/swe_mux/plugins.py`, `.docs/design/features/plugins.md` |
| Git operations | Keep staging, commits and general ignore editing planned; current Git routes support review, initialization and worktrees | `src/swe_mux/routes/git.py`, `frontend/src/GitTab.tsx` |
| Delta updates | Keep transfer reduction planned; selective replacement already exists | `src/swe_mux/bundle_apply.py`, `.docs/development/ROADMAP.md` Phase 21 |
| Portable verification | Reframe as easier setup; repository-neutral setup/verify commands and landing already exist | `src/swe_mux/worktree_verify.py`, `frontend/src/landSetupPrompt.ts`, `.docs/development/ROADMAP.md` Phase 19 |
| Assistant model routing | Keep automatic per-request selection planned; endpoint/model choice is already configurable | `.docs/design/features/assistant.md`, `src/swe_mux/assistant.py` |
| Deeper analysis and shared browser | Retain as future directions, without unsupported uniqueness claims | `.docs/development/ROADMAP.md` Phases 13 and 19, `src/swe_mux/code_graph.py` |

The roadmap states direction without adding release dates or claiming the planned native applications already exist.
