# Backend package responsibilities

Where daemon code lives and how to modify it.
The map is split by domain so a change touches one file; this page is the index, the dependency rules, and the composition boundary.

## Composition boundary

`src/swe_mux/server.py` is the aiohttp composition root.
It creates stores and managers, wires background workers, and registers the route table; it holds no handlers.
It should call domain packages rather than acquire their storage or process responsibilities.

The handlers live in `src/swe_mux/routes/`, one module per domain, described in [`packages/routes.md`](packages/routes.md).
The direction is one-way and enforced: `server.py` may import any route module, and a route module may not import `server.py` (`tests/test_route_modules.py`).
Transport input validation and domain-error-to-HTTP translation happen there and in `error_middleware`, whose shared error shapes are in `../../design/interfaces.md`.

## Dependency direction

Transport may depend on managers and stores.
Managers may depend on adapter and persistence contracts.
Platform modules remain below both.
Provider-native shapes stop at adapter and parser boundaries, and browser response models are assembled at the transport boundary.

Correct:

```python
# server.py validates the Project and delegates the state transition.
session = await manager.spawn(project_id=project.id, profile_id=profile_id)
```

Incorrect:

```python
# A route must not open mux.db directly or duplicate a store transaction.
sqlite3.connect(data_dir / "mux.db").execute("UPDATE projects ...")
```

Feature stores sharing `mux.db` use their own single-worker executor and connection plus the common operation coordinator described in `sqlite.md`.

## Domain maps

- [`packages/platform.md`](packages/platform.md) - the host seams: which host this is, pseudoterminal allocation, process-tree ownership, path identity, where secrets rest, firewall probing, the WSL bridge.
- [`packages/pty-and-sessions.md`](packages/pty-and-sessions.md) - the PTY supervisor, the PTY host, scrollback, the live session registry, recovery, arbitration, spawn and resume.
- [`packages/daemon-runtime.md`](packages/daemon-runtime.md) - the daemon process itself: startup, supervision, logging, traffic accounting, packaging support, the desktop shell, the CLI and doctor.
- [`packages/projects-and-worktrees.md`](packages/projects-and-worktrees.md) - Projects, files and notes, worktree setup, removal, and verification, Project actions, Agent Context, Project context.
- [`packages/git-and-landing.md`](packages/git-and-landing.md) - Git observation, mutation, provenance attribution, and the land queue.
- [`packages/history-and-observation.md`](packages/history-and-observation.md) - run history, transcripts and forking, the observation pipeline, approvals, and the durable status timeline.
- [`packages/automation-and-control-plane.md`](packages/automation-and-control-plane.md) - rules and observers, Tier 0 facts, deterministic detectors, the scan timeline, attention ranking, budgets, model endpoints, grants.
- [`packages/agent-surfaces.md`](packages/agent-surfaces.md) - the MCP tool surface, the prompt queue, auto-delivery, agent messaging, session control, scheduled runs.
- [`packages/harnesses.md`](packages/harnesses.md) - the harness registry, adapters and launchers, skills and environment inventory, provider accounts, usage collection.
- [`packages/processes-and-devices.md`](packages/processes-and-devices.md) - process inspection, Previews, clipboard, device presence, push, operational telemetry.
- [`packages/voice-and-assistant.md`](packages/voice-and-assistant.md) - TTS and STT, the Kokoro engine and its models, the Mux assistant.
- [`packages/routes.md`](packages/routes.md) - the HTTP and WebSocket route modules, the rules that keep the package a decomposition, and which module serves which surface.
- [`packages/runtime-rules.md`](packages/runtime-rules.md) - the rules every background worker, poller, and subprocess in the daemon obeys, and the measurements behind them.

## Related design

- `../../design/architecture.md`
- `../../design/interfaces.md`
- `../../design/features/sessions.md`
- `../../design/features/history.md`
- `../../design/features/project-actions.md`
