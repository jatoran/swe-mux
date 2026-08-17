# Cross-platform and shell compatibility: what it would actually take

A findings record, not a plan.
Measured 2026-08-08 against `src/swe_mux/`, the current Windows proving host, and the herdr reference checkout.
Roadmap commitments remain in `ROADMAP.md` Phase 7, Phase 10, and Phase 11.

## Scope: four separate compatibility surfaces

"Cross-platform" is too imprecise for this project.
Compatibility has four independent surfaces:

| Surface | Question | Current state |
| --- | --- | --- |
| Daemon host OS | Where do `muxd`, PTYs, process ownership, persistence, and local integrations run? | Windows only |
| Terminal shell | Which interactive shell or task interpreter runs inside a Windows-owned ConPTY? | PowerShell 7 primary; Windows PowerShell, CMD, WSL, and custom profiles have different limits |
| Agent runtime | Where do Claude, Codex, OMP, hooks, transcripts, credentials, and native IDs live? | Windows-native agents supported; WSL agent bridge unavailable |
| Browser client OS | Which device renders and controls the web UI? | Already broadly portable through localhost or Tailscale, subject to browser security and input differences |

A macOS browser controlling a Windows daemon is not a macOS port.
A Windows daemon launching a WSL shell is not a Linux daemon.
A terminal displaying bash does not prove that agent promotion, transcripts, process ownership, previews, and cleanup work inside that environment.

## Current compatibility boundary

| Environment | Current contract |
| --- | --- |
| Windows + PowerShell 7 | Primary shell path and preferred first-run default |
| Windows + Windows PowerShell 5.1 | Basic interactive shell and mux bootstrap work; task-language and profile behavior differ from PowerShell 7 |
| Windows + CMD | Detected interactive profile; agent awareness depends on PATH and CMD startup behavior |
| Windows + WSL distro shell | Interactive shell with translated Project cwd; native WSL agent bridge unavailable |
| Windows + Git Bash/MSYS/Cygwin/custom shell | Generic executable/argv profile only; agent awareness, startup files, path conversion, task quoting, and signal behavior are not a supported contract |
| Linux/macOS daemon | Not importable or runnable as a complete daemon today |
| Linux/macOS/mobile browser client | Browser/API client can control a supported Windows host; clipboard, microphone, keyboard, and secure-context behavior still require client coverage |

The narrow external proving target should remain Windows with an explicitly declared OS build, architecture, PowerShell version, WebView2/browser version, and agent CLI versions.
PowerShell 7 is the defensible primary shell for that proving target.

## Native code is concentrated; platform behavior is distributed

Roughly 2,000 lines remain concentrated in the original eight visibly Windows-coupled modules:

| Module | Windows-specific responsibility |
| --- | --- |
| `pty_host.py` | `winpty`/ConPTY creation, Windows argv, console-host ownership, `taskkill` teardown |
| `desktop.py` | pywebview, pystray, WebView2, Run-key startup, Windows single-instance behavior |
| `win_jobobj.py` | Job Objects and process-tree reaping |
| `timer_resolution.py` | `timeBeginPeriod` |
| `subprocess_flags.py` | `CREATE_NO_WINDOW`, process groups, Job breakaway |
| `secret_store.py` | Current-user DPAPI |
| `launchers.py` | `.cmd` agent shims, PATHEXT, COMSPEC-compatible launch resolution |
| `profiles.py` | PowerShell bootstrap, CMD/WSL discovery, Windows-to-WSL cwd translation |

That line count is not a port estimate.
A 2026-08-08 textual scan found 34 `sys.platform`/`os.name`-family references across 21 files, already beyond the previous 23-in-16 count.
Raw conditional counts also miss unconditional Windows defaults and behavioral assumptions.

Distributed platform-sensitive behavior includes:

- default shell configuration and profile migration;
- executable lookup, PATHEXT, COMSPEC, batch shims, shebangs, quoting, and environment-key case rules;
- process trees, creation fingerprints, signals, listener ownership, orphan evidence, and daemon-death cleanup;
- case sensitivity, same-file identity, symlinks, UNC paths, WSL paths, worktrees, and containment checks;
- Claude/Codex home directories, transcript discovery, hooks, native IDs, account files, and instruction locations;
- Explorer/macOS/Linux reveal operations and service-start environments;
- DPAPI, Keychain/libsecret, file modes, and config/data-directory conventions;
- WebView2/tray packaging, headless service installation, code signing, upgrades, and rollback;
- browser clipboard, microphone, keyboard, modifier, font, and secure-context behavior.

The right conclusion is: native implementation seams are identifiable, but the acceptance contract is broad.

## Current non-Windows import and startup blockers

`import winpty` in `pty_host.py`, reached through `session.py`, is one hard blocker but not the only one.

- `pyproject.toml` installs `pywinpty` only on Windows, while `pty_host.py` imports it unconditionally.
- `pyproject.toml` installs Pillow only on Windows, while `project_files.py` imports `PIL` unconditionally for Project image handling.
- `server.create_app` constructs `win_jobobj.ReaperJob` unconditionally.
- `Config` defaults to `powershell.exe`, and default `ShellProfile.platforms` is `['windows']`.
- Profile validation rejects a non-Windows profile on Windows but does not select or validate a POSIX default on POSIX.
- Agent shims are always `.cmd` files.
- The supervisor imports the Windows PTY and Job Object implementations even though its IPC transport is portable TCP.
- Persistent secrets fail outside Windows DPAPI.
- A source checkout without a built frontend serves a build-required diagnostic rather than a complete UI; public wheel and artifact assembly are not finished.

Dependency resolution on Linux is therefore not evidence that the package imports, starts, or retains feature parity.
The first port milestone must be package import and non-PTY test collection on every target before a PTY implementation is considered usable.

## Shell compatibility on a Windows host

Shell support is its own product matrix.
It must be tested separately from host-OS portability.

### PowerShell 7

- First-run config prefers `pwsh.exe` when present.
- Interactive sessions load the user's PowerShell 7 `$PROFILE`.
- The mux bootstrap runs after the profile and reasserts `MUX_SHIM_DIR` at the front of PATH.
- Project Action quoting uses PowerShell single-quoted arguments and the call operator for quoted executable paths.
- PowerShell 7 supports `&&` and `||` pipeline-chain operators used by many task commands.
- PowerShell 7 should be the primary supported shell until the real-shell matrix proves more.

### Windows PowerShell 5.1

- First-run config falls back to `powershell.exe` when `pwsh.exe` is absent.
- The mux PATH/OSC bootstrap executed successfully on Windows PowerShell 5.1.19041 during the 2026-08-08 review.
- Windows PowerShell 5.1 rejects `&&` and `||`; a task string valid under PowerShell 7 can fail before executing any command.
- Windows PowerShell and PowerShell 7 load different profile files and differ in default encoding, native-command behavior, modules, and language features.
- Installing PowerShell 7 later upgrades only the untouched auto-managed default, which changes the loaded profile and task interpreter on the next config load.
- Windows PowerShell should remain a separately tested compatibility profile, not be described as equivalent to PowerShell 7.

### PowerShell profile interference

The PATH bootstrap handles a profile that rebuilds PATH, but it cannot neutralize arbitrary profile behavior.

- An alias or function named `claude`, `codex`, or `omp` resolves before the `.cmd` shim and bypasses promotion/hook injection.
- A profile can exit, throw, change encoding, replace `prompt`, modify TERM variables, or load modules that alter key handling and startup latency.
- A configured profile already carrying `-Command` or `-File` cannot receive the mux bootstrap; cwd integration rejects it, but agent-awareness otherwise degrades silently.
- `-NoProfile` avoids user startup behavior but also changes the user's intended shell contract.

A profile diagnostic must launch the exact configured executable/argv and report edition/version, effective PATH, `Get-Command -All` results for each agent, profile errors, cwd telemetry, and whether the mux shim wins resolution.
Static executable existence is insufficient.

### CMD

- The detected interactive CMD profile currently uses `/Q`, not `/D /Q`.
- Without `/D`, CMD executes `Command Processor\AutoRun` registry commands before the prompt.
- AutoRun can replace PATH, install DOSKEY macros, change cwd, or run arbitrary startup logic before agent shims resolve.
- Project Action process shims already use `%COMSPEC% /d /s /c`, so one-shot process actions and interactive CMD currently have different startup isolation.
- A safe built-in agent-aware CMD profile should disable AutoRun or reassert the shim contract after AutoRun; a separate custom profile can preserve deliberate AutoRun behavior.
- CMD quoting, `%VAR%` expansion, delayed expansion, code pages, Ctrl+C, and Unicode paths require real integration tests.

### WSL

Two modes must remain distinct:

1. A Windows daemon launches `wsl.exe` as a terminal profile.
2. The daemon itself runs inside WSL as a Linux process.

Mode 1 exists today.
It translates the canonical Windows Project root and starts the selected distribution, but the profile is labelled `agent-bridge-unavailable`.
Windows `.cmd` shims cannot provide a native Linux agent bridge.
WSL Project Actions explicitly use `wsl.exe -- sh -lc`, which may not match the user's interactive default shell or startup files.

Mode 2 requires most of the Linux port: POSIX PTY, process guardian/reaper, POSIX launchers, Linux paths and homes, secrets, service lifecycle, packaging, and Linux CI.
It is not merely a cheaper version of Mode 1.

### Git Bash, MSYS2, Cygwin, and other custom shells

These shells can be configured as raw executable/argv profiles, but there is no declared agent-aware contract.

- Startup-file behavior differs by login/interactive flags.
- Windows paths may need MSYS conversion while native Windows executables need unconverted arguments.
- `.cmd` shim discovery and execution cannot be assumed.
- POSIX `-c` and `shlex` quoting do not prove that the selected Windows-hosted shell accepts the same path and signal semantics.
- OSC 7, bracketed paste, Ctrl+C, Unicode, resize, exit status, and descendant cleanup need shell-specific tests.

Generic custom-shell support should be labelled best-effort until a shell is named in the compatibility matrix.

### Shell profile model gaps

- `cwd_strategy='home'` is accepted by config and exposed in Settings, but `resolve_profile` implements only the WSL branch; `home` currently behaves like native Project-root startup.
- `platforms` is stored but not used to select a POSIX first-run default.
- `capabilities=['agent-aware']` is declarative and can overstate runtime behavior when startup scripts or command precedence bypass shims.
- Settings has no Test profile action and does not show the effective executable, argv, version, command precedence, or compatibility warnings before saving.

These are Windows trial-readiness issues even if native Linux/macOS work never starts.

## Platform interfaces required for a native port

### PTY and lifecycle ownership

- Split `PtyHost` into a shared protocol, shared buffering/fanout logic, Windows ConPTY implementation, and POSIX implementation.
- Keep the poll ladder, bounded coalescing, scrollback, backpressure, resize contract, and exit-status contract shared where semantics truly match.
- Retain Windows Job Objects.
- On POSIX, use a per-session guardian/process-group owner with graceful signal, bounded wait, group kill, daemon-loss behavior, and supervisor reattachment semantics.
- Do not reduce cleanup to `os.setsid` plus `os.killpg` without proving daemon crash, detached descendants, PID reuse, and session-preserving restart.

### Process inspection and previews

- Normalize descendant identity, creation fingerprints, CPU/memory, listeners, signals, and termination behind a platform boundary.
- Preserve the rule that suspected or inaccessible processes are not killed automatically.
- Verify listener ownership and loopback proxying across Windows, Linux network namespaces, WSL, macOS, containers, and service contexts.

### Shells, launchers, hooks, and agents

- Generate `.cmd` launchers on Windows and executable shebang launchers on POSIX.
- Preserve structured argv/env and the `MUX_<NAME>_EXE`/`MUX_<NAME>_ARGS` contract.
- Keep PATHEXT, COMSPEC, batch-file, and Windows Node-shim handling inside the Windows implementation.
- Resolve POSIX commands through PATH without importing Windows suffix assumptions.
- Extend the shim-aware resolver to POSIX. Harness-installation detection (`harness.detect_installation` via `shim_paths.which_real`) strips mux's own launchers before deciding a CLI is present, but `is_mux_shim`/`path_without_shim_dirs` recognize only `.cmd`/`.bat` shims. With POSIX shebang launchers, `which_real` would resolve to the mux shim and detection would report every harness installed, the same self-invocation trap the shim guard exists to prevent.
- Adapt hook commands to each provider's actual shell on each host; do not infer hook-shell portability from the interactive terminal shell.
- Prove promotion/demotion, hook secrets, native IDs, transcript ownership, resume, rollover, and account switching independently for every host/agent-runtime pair.

### Paths, data, and secrets

- Replace unconditional case-folding with platform-aware same-file and containment rules.
- Test spaces, Unicode normalization, symlinks, junctions, UNC, case-sensitive Windows directories, WSL paths, Git worktrees, and non-repository Projects.
- Define data/config locations for Windows, XDG Linux, and macOS instead of carrying `~/.mux` everywhere by accident.
- Adapt Claude/Codex/OMP homes, transcript layouts, settings, skills, hooks, and account files per platform.
- Put DPAPI, Keychain, libsecret, and a permission-checked file fallback behind one typed secret-store contract.
- Define migration and backup behavior when a user moves data between hosts; encrypted Windows secrets cannot be copied as usable credentials.

### Desktop, browser, and remote access

- The Windows WebView2/tray shell can remain Windows-only.
- Linux/macOS can initially run a headless daemon plus ordinary browser UI.
- Browser clients still require OS/browser coverage for modifiers, IME, fonts, clipboard, file pickers, notifications, microphone, audio autoplay, touch, and viewport behavior.
- Direct Tailscale HTTP is encrypted transport but not a browser secure context; clipboard and microphone behavior differs from private HTTPS through Tailscale Serve.
- swe-mux has no application login; a tailnet peer admitted by policy has terminal and code-execution authority.
- External proving should default to local-only unless the tester has deliberately reviewed Tailscale policy and the remote-control boundary.
- The connect-onboarding surface planned in `NEW_USER_RELEASE_READINESS.md` (Tailscale connection state, the phone-side DNS checklist, and the QR of the connection URL) is browser-side and reads `tailscale status --json`, so it is platform-neutral and the headless-Linux plus browser target inherits it unchanged.
- The Windows Defender Firewall inbound-rule check and repair for the tailnet socket is Windows-specific and must sit behind a platform boundary. A headless Linux host usually leaves the Tailscale interface unfiltered, so the POSIX-appropriate equivalent is a reachability probe plus `ufw`/`firewalld` guidance rather than an elevated rule edit.
- This boundary is now implemented in `src/swe_mux/windows_firewall.py`, gated by `firewall_supported` (Windows plus a frozen build).
  **The POSIX equivalent shipped with Phase 10** as `src/swe_mux/posix_firewall.py`, behind the same gate: a reachability probe plus the exact command *this host's* firewall tool needs (`ufw`, `firewall-cmd`, `nft`, `iptables`), with `repair_supported: false` throughout because opening a port needs root and is the user's decision.
  It probes rather than reading rules on purpose - there is no single POSIX firewall to inspect, so reading whichever one happens to be installed would give a confident answer that is wrong on the next machine, while a probe answers the question the user actually has and stays correct when the blocker is upstream of the host entirely (a cloud security group, a container network).
- **A second Windows firewall rule turned out to be required, for WSL.**
  Measured 2026-08-17: a daemon listener on the WSL virtual adapter is reachable from Windows and *times out* from inside the distribution, which is the signature of a DROP rather than a missing listener.
  Without an inbound rule scoped to the WSL subnet, a bridged agent runs perfectly and its hooks never arrive - the silent-uninstrumented state the bridge exists to end.
  `windows_firewall.build_wsl_repair_script` adds it, scoped to the WSL subnet and the swe-mux executable rather than to `Any`; see `ROADMAP.md` Phase 10.

## Packaging and external-trial readiness

Platform support is not real until a clean machine can install, diagnose, upgrade, and remove the product without a source checkout.

Current gaps:

- README installation is a developer flow requiring uv, Python, Node/npm, and a frontend build.
- `requires-python = '>=3.12'` has no tested upper bound despite native dependencies.
- No Node version is declared; frontend tests currently rely on modern Node behavior.
- No CI workflow builds or installs artifacts on a clean matrix.
- Project license, package classifiers, project URLs, changelog, release policy, security contact, and support policy are incomplete.
- PyInstaller builds local unsigned architecture-specific folders; there is no code-signing or published update channel.
- WebView2 Runtime, provider CLIs, PATH/PATHEXT/COMSPEC, port availability, writable data paths, Tailscale, browser capability, and shell profiles are not covered by one startup preflight.
- `mux doctor` currently reports remote-access status rather than the consolidated platform/profile/ownership diagnostic described in Roadmap Phase 7.
- Rotating daemon/access/crash/lifecycle logs exist, but there is no one-click sanitized install-wide support bundle for startup and compatibility failures.
- Some features download assets silently on first use, so a clean install does not match its documented capabilities until the network round trip completes. STT is enabled by default and the first Talk pulls the Whisper model and the Silero VAD runtime; preview capture assumes a local Chromium. These downloads are platform-neutral and should be gated or documented rather than silent (`NEW_USER_RELEASE_READINESS.md`).

A Windows alpha does not require Linux/macOS support.
It does require an explicit Windows support matrix, a self-contained signed artifact, clean-machine smoke tests, actionable diagnostics, and a bounded support bundle.

## Verification matrix

Unit tests that mock `os.name`, `shutil.which`, or argv construction do not prove terminal compatibility.
Each supported combination needs real PTY and process tests.

### Windows proving matrix

- declared minimum Windows build and current Windows build;
- x64 first; ARM64 only after native dependencies and PyInstaller output are proven;
- PowerShell 7 supported/LTS and current versions;
- Windows PowerShell 5.1 as a separate compatibility profile if retained;
- safe CMD plus deliberate-AutoRun CMD;
- paths with spaces, non-ASCII, long paths, UNC, junctions, and case-sensitive directories;
- standard user, no elevation, restrictive execution policy, customized profiles, aliases, PATH, PATHEXT, and COMSPEC;
- WebView2 present/missing, browser-only fallback, no Tailscale, Tailscale direct HTTP, and Tailscale Serve HTTPS;
- direct and nested Claude/Codex/OMP launch, resume, hooks, transcripts, Ctrl+C, resize, paste, reconnect, daemon restart, and descendant cleanup;
- source install, frozen artifact, upgrade, rollback, uninstall, and preserved data.

### Driving a Linux daemon by hand from a Windows host

`tools/wsl_dev_setup.sh` brings a native Linux checkout inside WSL to `origin/master`, installs both dependency sets, builds the frontend, and starts a daemon you open in a Windows browser.
It is the interactive counterpart to `linux_acceptance.sh` and `linux_agent_acceptance.sh`, which prove contracts headlessly and exit.

There is no Linux desktop app to launch.
`pystray` and `pywebview` are declared `sys_platform == "win32"`, so on Linux swe-mux is a headless daemon plus a browser, by design rather than by omission.

Four traps the script encodes, each of which silently produces a wrong result rather than an error:

- **A non-interactive shell gets the wrong Node.** `nvm` is a shell function sourced from a profile, so `wsl.exe -- bash script.sh` never sees it and falls back to the distro Node. On a host whose distro Node is 18, `frontend/scripts/compress-static.mjs` calls `import.meta.dirname` (Node 20.11+), gets `undefined`, and throws in `postbuild` - *after* a successful `vite build`, so it reads as a bundling failure rather than a wrong interpreter. The script sources `nvm` and requires Node 20+ in preflight.
- **A daemon started without `--local-only` steals the phone's address.** The startup mobile-voice setup retargets the single Tailscale Serve 443 route at whatever port it is run on, so a throwaway Linux daemon takes over the address every phone uses and leaves it answering nothing when it exits, while the real daemon keeps working on loopback and never notices.
- **A checkout used to stage work by hand cannot fast-forward.** Files copied in to test a feature before it was committed are untracked files the merge must create, and modified files the merge must overwrite. Both refuse. Neither is real work, but proving that requires comparing against every commit in the incoming range, not just its tip - a file copied from partway through the range matches no tip.
- **The Windows working tree's CRLF travels into the Linux clone.** It presents as hundreds of modified files with no content change, which blocks the merge and buries any genuine change in the noise.

The script's rule is that nothing moves unless it can be proved disposable: identical to `HEAD` modulo CR, or identical to some commit in the incoming range.
Anything else stops the run untouched unless `--stash-unmatched` is given, and everything it does move is copied under `.trash/` and stashed rather than deleted.

### Native-platform CI

- Run import and non-PTY tests first on Windows, Linux, and macOS.
- Run the complete applicable verification suite on every supported host.
- Add PTY/process lifecycle integration tests per host; do not skip the platform-specific core and call the matrix green.
- Build and install the artifact produced by the same revision under test.
- Require platform-specific changes to land with implementation, tests, diagnostics, and compatibility-doc updates for every supported path.
- Keep explicit unsupported/degraded capability results; never silently advertise feature parity.

Without continuous execution, a port works the week it is written and breaks silently a few features later.
CI preserves the property, but CI is not a substitute for implementing the property.

## Target order

1. **Windows external proving.** Finish diagnostics, shell contracts, clean-machine tests, artifact assembly, signing, and support workflow.
2. **Headless Linux server.** Best native fit because the daemon/browser split and TCP supervisor IPC already align with a headless host.
3. **Windows-hosted WSL agent bridge.** Valuable for Windows users, but requires a real distro-side launcher/hook/transcript/path bridge.
4. **Linux developer workstation.** Adds desktop-adjacent integration, local browser behavior, file reveal, service environment, and more shell combinations.
5. **macOS.** Same PTY/process-group work plus Keychain, launch/service behavior, path normalization, and macOS-specific packaging/signing/notarization.

Running the daemon inside WSL can be a useful Linux test environment, but it is not a substitute for native Linux CI or a Windows-hosted WSL agent bridge.

## What herdr pays for the property

The herdr comparison remains useful only as evidence that platform support multiplies acceptance work:

- 539 `#[cfg(windows/unix/target_os)]` sites across 71 of 235 source files in the measured checkout;
- separate Windows, Linux, and macOS PTY/platform implementations;
- substantially larger Windows-specific code than either Unix target;
- Windows still labelled beta in the measured README.

The lesson is not that swe-mux needs the same line count.
The lesson is that PTY, process, path, shell, packaging, and test behavior must be implemented and continuously exercised per target.

## Not the answer

Rewriting in Rust does not supply platform behavior.
It could improve compile-time totality and distribution, but PTYs, process trees, paths, shells, hooks, transcripts, secrets, clients, packages, and CI would still need per-platform contracts.

The frozen-app rebuild and public distribution story are real independent costs and should be improved on their own terms.
They do not justify a rewrite and they do not disappear when native platform code is rewritten.

## Related documentation

- Windows proving, diagnostics, native platform work, and public release: `ROADMAP.md` Phase 7, Phase 10, Phase 11
- Fresh-machine onboarding, remote-connection flow, and first-use costs: `NEW_USER_RELEASE_READINESS.md`
- Runtime/process boundaries: `../design/architecture.md`
- Shell profile contract: `../design/features/launch-profiles.md`
- Agent launch, shims, hooks, and transcripts: `../design/features/backends.md`
- Project Action shell/process semantics: `../design/features/project-actions.md`
- Windows desktop packaging: `../design/features/desktop-shell.md`
- Browser and Tailscale boundary: `../design/features/remote-access.md`
- Backend package ownership: `../technical/backend/packages.md`

## Key files

- `pyproject.toml`
- `src/swe_mux/config.py`
- `src/swe_mux/profiles.py`
- `src/swe_mux/project_actions.py`
- `src/swe_mux/pty_host.py`
- `src/swe_mux/win_jobobj.py`
- `src/swe_mux/supervisor.py`
- `src/swe_mux/supervisor_client.py`
- `src/swe_mux/launchers.py`
- `src/swe_mux/agent_launcher.py`
- `src/swe_mux/project_files.py`
- `src/swe_mux/processes.py`
- `src/swe_mux/runtime_cwd.py`
- `src/swe_mux/secret_store.py`
- `src/swe_mux/file_manager.py`
- `src/swe_mux/desktop.py`
- `packaging/build_desktop.py`
