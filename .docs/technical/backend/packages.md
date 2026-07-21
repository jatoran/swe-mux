# Backend package responsibilities

## Composition boundary

`src/swe_mux/server.py` is the aiohttp composition root. It creates stores/managers, wires
background workers, validates transport input, and translates domain errors to HTTP/WS results.
It should call domain packages rather than acquire their storage or process responsibilities.

## Package map

| Package | Owns | Does not own |
|---|---|---|
| `desktop.py` | Windows tray/WebView lifecycle, single instance, login startup, daemon child supervision | PTYs, HTTP composition, Project/session state |
| `__main__.py` | daemon argument/config resolution and reusable aiohttp site lifecycle | desktop window/tray state |
| `session.py` | live session registry, spawn/stop, PTY fanout, bounded replay, interactive vs one-shot exit lifecycle | provider transcript parsing, Project mutation |
| `pty_host.py` | ConPTY/process creation, resize, low-level I/O, root exit status, dead-host release | HTTP, SQLite, layout |
| `subprocess_flags.py` | consoleless flags for daemon-owned Windows background commands | interactive ConPTY children |
| `build_support.py` | lock-safe staged frontend publication for desktop packaging | Vite compilation, runtime asset serving |
| `projects.py` | Project/Group validation and lifecycle | Git-derived identity, file content |
| `project_files.py` | safe Project config, notes, tree, bounded recursive name/content search, file reads/writes | layout placement, browser drafts |
| `project_watcher.py` | leased non-recursive directory watches | recursive Project crawl |
| `project_actions.py` | inert task import, normalization, exact fingerprint trust | automatic execution, UI placement |
| `action_runner.py` | apply validated cwd/env and launch one normalized task step | discovery, trust, session ownership |
| `history.py` | shared schema, Project/layout persistence, run history, search index | live PTY lifecycle |
| `history_backfill.py` | bounded cancellable complete-history jobs | durable job scheduling, native file mutation |
| `transcript_view.py` | bounded Claude/Codex conversation parsing | process state, transcript writes |
| `layouts.py` | layout-v6 validation and migrations | UI focus or drag state |
| `operational_telemetry.py` | process/quota/reset/context/tool evidence | credentials, automatic process killing |
| `provider_accounts.py` | saved auth snapshots, explicit switching, safe quota reads | concurrent provider homes |
| `voice.py` | completed-reply TTS segments, bounded Whisper STT with GPU/CPU fallback, temporary audio lifecycle, voice-submit idempotency | browser microphone permission, PTY ownership |
| `tailscale.py` | direct-tailnet discovery/status and ephemeral certificate preparation for the daemon's direct private HTTPS listener | ACL/policy changes, Serve/Funnel enablement, browser permission |
| `processes.py` | descendant inspection/actions; Project-wide loopback registration, discovery, listener attribution, and route maps | proxy transport, authoritative ownership from PID alone |
| `adapters/` | provider command/resume/transcript/state normalization | public HTTP shapes |

Feature stores sharing `mux.db` use their own single-worker executor/connection and the common
operation coordinator described in `sqlite.md`.

## Dependency direction

Transport may depend on managers/stores; managers may depend on adapter and persistence
contracts; platform modules remain below both. Provider-native shapes stop at adapter/parser
boundaries. Browser response models are assembled at the transport boundary.

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

## Background-work rules

- Blocking ConPTY creation, filesystem scans, Git probes, and SQLite work stay off the asyncio
  event loop.
- Interactive readiness and durable registration are distinct. Once a ConPTY is usable, publish
  the in-memory session and return; serialize history registration behind it.
- A packaged one-shot Project Action must use the console-subsystem `swe-mux-action.exe` entry as
  its ConPTY root. Do not launch it through windowed `swe-mux.exe`: Windows may allocate a separate
  visible console for descendants and leave the in-app terminal blank. Exit code zero maps to
  completed/exited; nonzero remains crashed and observable.
- Frozen Windows startup may surface pywinpty's private `pyo3_runtime.PanicException` once with
  `ERROR_SEM_NOT_FOUND`; `pty_host.py` retries only that exact allocation panic with a strict bound.
  Other `BaseException` values retain normal control-flow semantics.
- PTY attach/input paths never wait for observational event persistence.
- Natural root exit captures status before detaching the dead ConPTY from the retained session.
  Final output drains through the reader's local handle; finalization cancels a frozen read after
  root exit so that local reference cannot leak. `pty_host.py` also binds pywinpty's daemon-sibling
  `OpenConsole` helper (or its delayed frozen-build `conhost` replacement) by creation time and
  revalidates PID, creation time, executable, and parent before reaping it. Durable/in-memory
  scrollback, not an OS pseudoconsole reference, supplies ended-session replay.
- Every poller/scan has an explicit bound, cancellation/stop path, freshness contract, and
  unavailable result. Optional integrations cannot make terminal operations fail.
- Voice STT/TTS subprocesses and local models stay off the event loop. Incoming WAV duration,
  encoding, and bytes are validated before transcription; temporary utterances are deleted on
  success, error, or cancellation.
- Desktop presentation and daemon lifetime remain separate processes. Close/minimize hides the
  WebView; only authenticated loopback Quit stops the daemon. Never expose shutdown through the
  ordinary remote-control authority.
- Windowed builds must route only allowlisted internal module entrypoints through `desktop.py`;
  daemon-owned maintenance subprocesses use `subprocess_flags.py`, while interactive commands
  remain under ConPTY so suppressing console flashes never suppresses terminal output.
- Preview registration identity is Project endpoint, not clicked terminal. Resolve listener
  ownership across live sessions before attachment; do not weaken the iframe sandbox or let a
  browser dial raw loopback for cross-service traffic.
- Once a route has resolved an explicit Project, Project-resource helpers must receive/use that
  canonical identity. Re-running Git discovery on `project.root` can silently retarget a nested
  registered Project to its enclosing worktree; this remains a known note-path defect until the
  helpers accept the explicit Project identity.

## Related design

- `../../design/architecture.md`
- `../../design/interfaces.md`
- `../../design/features/sessions.md`
- `../../design/features/history.md`
- `../../design/features/project-actions.md`
