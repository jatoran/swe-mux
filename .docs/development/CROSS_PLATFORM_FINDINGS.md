# Cross-platform: what it would actually take

A findings record, not a plan.
Measured 2026-08-06 against `src/swe_mux/` and the herdr reference checkout.
Nothing here is scheduled.

## swe-mux's actual coupling is small

Of ~59,000 lines in `src/swe_mux/`, roughly 2,000 sit in eight Windows-coupled modules, and
several of those are mostly portable logic:

| Module | Lines | Windows-specific part |
| --- | --- | --- |
| `pty_host.py` | 440 | `import winpty` at line 14; the poll ladder, coalescing, and drain loop are portable |
| `desktop.py` | 676 | pywebview + pystray tray |
| `win_jobobj.py` | 227 | Job Objects for process-tree reaping |
| `timer_resolution.py` | 110 | `timeBeginPeriod` |
| `subprocess_flags.py` | 85 | `CREATE_NO_WINDOW` and friends |
| `secret_store.py` | - | DPAPI via `ctypes.windll.crypt32` |
| `launchers.py` | - | writes `.cmd` shims |
| `profiles.py` | - | the PowerShell PATH re-assertion guard |

There are only **23 `sys.platform` / `os.name ==` / `platform.system()` checks across 16 files**.

Three things are already portable or already anticipated:

- `pyproject.toml` carries `sys_platform == 'win32'` markers on `pywinpty`, `pillow`, `pystray`,
  and `pywebview`, so a non-Windows install already resolves.
- Supervisor IPC is TCP over loopback (`supervisor_client.py:285`), not named pipes.
- The daemon is separable from the desktop shell: `muxd` is its own entry point and the desktop
  dependencies are an optional extra.

**The single hard blocker for importing the package on Linux is `import winpty` at
`pty_host.py:14`, reached from `session.py:47`.**

## What herdr pays for the property

Not free, and not a gift from Rust:

- **539 `#[cfg(windows/unix/target_os)]` sites across 71 of its 235 source files.**
- `src/platform/windows.rs` is 3,109 lines against `linux.rs` 1,276 and `macos.rs` 1,212, so
  Windows costs roughly 2.5x each Unix target.
- Its PTY layer is two separate implementations behind one struct: `#[cfg(unix)] mod unix` versus
  `#[cfg(windows)] portable_pty`.
- Its README still labels Windows **beta**.

herdr is cross-platform because it wrote every feature three times from the start, not because the
language made it cheap.

## The seams

- **PTY backend.** Split `pty_host.py` into a protocol plus a winpty implementation and a Unix one
  (stdlib `pty` + `os.openpty`, or `ptyprocess`). The poll ladder, the 256 KB coalescing handoff,
  and the drain loop stay shared. Highest value: it is what unblocks importing at all.
- **Reaper.** `win_jobobj.ReaperJob` becomes a protocol. Windows keeps Job Objects; Unix uses
  `os.setsid` in the child and `os.killpg` to reap.
- **Flags and timer resolution.** No-ops on Unix.
- **Shims.** `create_agent_shims` writes `.sh` with a shebang and `chmod +x` instead of `.cmd`.
  The `MUX_<NAME>_EXE` / `MUX_<NAME>_ARGS` env contract is already portable, and the harness
  registry already turned executable names into per-harness data rather than `.exe` constants.
- **Executable resolution.** PATHEXT and COMSPEC handling in `agent_launcher.py` becomes
  Windows-only; Unix uses plain PATH.
- **Secret store.** DPAPI behind a backend protocol; a `0600` file on Unix, or Keychain/libsecret
  for parity.
- **Desktop shell.** Nothing to do. Already an optional Windows extra; on Unix the daemon runs
  headless behind the browser UI.

## The real blocker is CI, not code

The seams above are bounded work. What decides whether they survive is that **Python has no
compiler to tell you a platform branch rotted.** herdr gets that check on every build; swe-mux
would need `.worktree-verify` running on Linux in CI, tests audited for backslash assumptions, and
a rule that platform-specific code lands with both paths.

Without that, a port works the week it is written and breaks silently a few features later.

## Which target, if ever

The three are different amounts of work and different amounts of architectural fit:

- **Linux dev box** - all seams plus a packaging answer. Most work, least fit, because the frozen
  app and tray are Windows-shaped.
- **Headless Linux server, attach from anywhere** - the best fit by a distance. The daemon is
  already headless-capable, the supervisor already speaks TCP loopback, remote access over
  Tailscale already exists, and the desktop shell is genuinely unnecessary.
- **WSL** - the cheapest middle path, reusing the same PTY and reaper seams. herdr and Orca both
  ship explicit WSL support (Orca has `wsl.ts` and `wsl-hook-relay-*`); swe-mux currently reports
  `agent-bridge-unavailable` there. Running the daemon *inside* WSL would surface most of the same
  bugs at lower cost.

## Not the answer

Rewriting in Rust. What herdr gains is two properties rather than a language: compile-time totality
(already being approximated with `Literal` + `assert_never` + mypy) and a distribution story that
is not a staged multi-minute rebuild. The second is a real, ongoing, measured cost documented in
the root `CLAUDE.md` as two separate silent-failure traps, and it is worth addressing on its own
terms regardless of platform support.
