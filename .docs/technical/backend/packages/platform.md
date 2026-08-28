# Backend: platform seams

Index: `../packages.md`.
Plan: `../../../development/ROADMAP.md` Phase 10.
Findings: `../../../development/CROSS_PLATFORM_FINDINGS.md`.

The rule the seams exist to enforce: `host_platform.py` answers *which host this is* and nothing else, while whether a capability exists is answered by the module that owns it.
A capability can be absent on a supported platform, and conflating the two is how a port starts claiming parity it does not have.

Each entry lists what the module owns, then **Not:** what it deliberately does not.

## `host_platform.py`

The single vocabulary for which host this is: `IS_WINDOWS`/`IS_MACOS`/`IS_LINUX`/`IS_POSIX`, `platform_key`, `platform_label`.

**Not:** every capability question.

## `pty_backend.py`

The `PtyProcess` contract (`spawn`/`read`/`write`/`set_size`/`isalive`/`exit_status`/`interrupt_read`/`force_kill`/`close`), the unified `PtyError`, and the per-host factory.

**Not:** buffering, fanout, or scrollback, which are shared and owned by `pty_host.py`.

## `pty_backend_windows.py`

ConPTY allocation with the bounded pywinpty PyO3-panic retry, Windows argv quoting through `list2cmdline`, the OpenConsole/conhost helper binding and deterministic reaping, and `taskkill /T` teardown.

**Not:** anything shared; imported only on Windows.

## `pty_backend_posix.py`

`pty.fork` with a real controlling terminal, so Ctrl+C, SIGWINCH and `isatty` behave.
Nonblocking master reads with EIO as end-of-output, `TIOCSWINSZ` resize, waitpid exit codes normalized to 128+signal, and process-group kill.

It also owns the *handoff* to `posix_process_group.py`: `spawn` does not return until the child is out of the daemon's process group.
`setsid` runs in the child after the fork, so an unsynchronised `getpgid(child)` in the parent can still answer the daemon's own group - the one value `ProcessGroupReaper.assign` refuses - and the session then runs with no ownership and no guardian.
Measured 2026-08-27 in a Linux container against this exact path: idle, ownership was never refused; with every core pinned it was refused 21, 36 and 33 times out of 40, and zero times out of 40 in three runs with the wait in place.
The wait belongs to the parent because the child may not do anything but `chdir` and `execvpe` (a multi-threaded parent makes any allocation there a potential deadlock).

**Not:** anything shared; imported only on POSIX.

## `process_reaper.py`

The `ProcessReaper` ownership contract (`assign`/`process_ids`/`create_child`/`close`) and the per-host factory, plus `process_in_job` (the Windows answer, `None` on POSIX for "no such concept").

**Not:** the implementations themselves.
It exists so no caller imports `win_jobobj` directly, which is what made the package unimportable off Windows.

## `posix_process_group.py`

POSIX lifetime ownership by process group: refusing to own the daemon's *own* group, live group membership as the analogue of Job-object membership, and SIGTERM then a bounded wait then group SIGKILL.

**Not:** daemon-death cleanup (`posix_guardian.py`), or the Windows Job object (`win_jobobj.py`).
Also not *establishing* the group: `assign` states the precondition and refuses loudly when it does not hold, and `pty_backend_posix.py` is what makes it hold before calling.
Relaxing that refusal into a retry would put the retry in the module that must never guess, so it stays where the fork is.

## `posix_guardian.py`

The POSIX stand-in for `KILL_ON_JOB_CLOSE`: a separate process, started outside the group it watches, that kills the group when the daemon's pipe reaches EOF, and exits harmlessly on an explicit `release` (a deliberate restart).

**Not:** orderly shutdown while the daemon is alive, which is the reaper's job.

## `subprocess_flags.py`

Consoleless flags for daemon-owned Windows background commands, `reap_process_tree` (kill the descendants before reaping the child, because a Windows `cmd.exe` wrapper's real workers survive `process.kill()` and hold the pipes), and `popen_outside_job` breakaway spawn (with a plain-spawn fallback) for children that must outlive any inherited Job object.

**Not:** interactive ConPTY children.

## `bounded_subprocess.py`

The one runner for every daemon-owned *one-shot* subprocess: chunked head-and-tail bounded capture per stream with the truncation reported rather than hidden, and a timeout that returns `exit_code=None` instead of a zero.
It reaps the process tree on the timeout **and** on `CancelledError`, drains the pipes under a grace afterwards so an escaped descendant holding one cannot hang the caller, and rate-limits its timeout and cap warnings per label.

`run_bounded` raises `OSError` from the spawn rather than folding it into an outcome, because "install ccusage" and "could not start codex" are the callers' diagnostics to phrase.
It returns bytes and never decodes: `git cat-file blob` needs the bytes git stores, and `errors="replace"` would digest a repaired string.

Callers: `usage.py`, `provider_accounts.py`, `git_monitor.py`, and `worktree_exec.py` (whose pattern this is).
Migrating the first three closed three real gaps - `usage.py` buffered ccusage to completion through `communicate()` and only then compared it against its own 10 MiB limit, the other two had no cap at all, and none of the three reaped on cancellation despite all of them running inside supervised background loops.

**Not:** interpreting an exit status, decoding, long-lived or interactive stdio children (the Codex quota app-server in `provider_accounts.py` speaks JSON-RPC over a held stdin and stays hand-rolled), or ConPTY sessions.

## `cli_version.py`

One `<cli> --version` probe: `which_real` resolution, one subprocess per resolved executable per TTL, and a `CliVersion` carrying the exit code and the merged banner with no judgement about what it means.

The two former implementations disagreed on timeout (6 s against 2 s), TTL (5 minutes against an hour), and resolution (`which_real` against none), so one binary could be probed twice in a request and answer differently.
What deliberately stays with each caller is the *presentation*: `harness.probe_cli_version` returns the version token, because `version_is_untested` compares it against a tested bound; `agent_environment.probe_cli_version` returns the CLI's own line and requires a zero exit, because it is shown to a person and used as part of an MCP catalog's cache key.

**Not:** deciding whether a version is supported (`harness.version_is_untested`), or running on the event loop - both call sites reach it through `asyncio.to_thread`.

## `path_identity.py`

"Same file?" and "inside?" answered per filesystem: `os.path.samefile` when both paths exist, then NFC plus platform-correct case folding with a read-only per-directory case-sensitivity probe, and component-wise containment so `project-old` is never inside `project`.

**Not:** the code-graph and doc-debt storage key (`deterministic_consumers.normalize_target`), which is deliberately a platform-neutral repo-relative form.

## `secret_backends.py`

Where secrets rest per host: a Windows DPAPI file, macOS Keychain via `security`, Linux libsecret via `secret-tool`, an explicitly opt-in (`MUX_SECRET_STORE=file`) 0600 file that never claims to be encrypted, and a fail-closed `UnavailableBackend`.

**Not:** the environment override or the public status shape (`secret_store.py`).

## `secret_cipher_windows.py`

DPAPI protect and unprotect, isolated so `ctypes.wintypes` is imported nowhere else.

**Not:** anything not Windows.

## `posix_firewall.py`

The POSIX answer to "can a peer reach this daemon": a bounded TCP reachability probe, which host firewall front-end is installed (`ufw`/`firewall-cmd`/`nft`/`iptables`), and the exact command that would open the port - as advice, never executed, because opening a port needs root and is the user's decision.

**Not:** reading firewall rules, since there is no single POSIX firewall to read and a probe is both simpler and better evidence; and any mutation at all.

## `wsl_bridge.py`

The distro-side half of a native WSL agent.

- Discovery that runs *inside* the distribution and refuses `/mnt` interop binaries.
- `wslpath` translation both ways.
- The host address a distro actually reaches the daemon on, parsed from `/proc/net/route` rather than from a shelled-out pipeline.
- Reachability probing that names the firewall.
- Materialization of a dependency-free stdlib bridge script plus per-harness shims under `~/.mux-bridge/`.

Its failure mode is silence by construction - a bridged agent that cannot reach the daemon runs perfectly and simply never reports - so any change must keep the reachability probe and the `reasons` it produces, and must never let "not checked" render as available.

**Not:** the listener itself (`__main__`/`tailscale`), the firewall rule (`windows_firewall`), or the capability label (`profiles.derive_capabilities`, which is pure and takes the answer as an argument).

## Verifying a platform change

Verify on both hosts, not one.
`tools/linux_container_verify.sh` runs the suite on Linux from a Windows host with only Docker.
`.worktree-verify` runs the two `--platform` mypy passes, so each host's implementation is typechecked wherever the gate runs.
