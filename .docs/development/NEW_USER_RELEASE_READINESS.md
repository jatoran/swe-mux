# New-user and Windows-release readiness

## Purpose

This plan lists the work that prepares swe-mux for a first external tester on a fresh Windows machine, and for a wider Windows release.
It covers remote connection, Windows hardening, agent instrumentation control, first-run onboarding, and diagnostics.
Priorities are `P0` (do before the fresh-machine test), `P1` (before wider release), and `P2` (polish).

The harness enablement work (`design/features/backends.md`, the three-state launcher filter and first-run panel) is already shipped and is the pattern the instrumentation toggles follow.

## Relationship to the roadmap

This is a feeder plan, not a competing sequence.
`ROADMAP.md` lists it under "Plans not sequenced here" and already carries the agnostic principle its items serve: Phase 7 takes every harness list and label from the registry and turns `mux doctor` into a consolidated read-only report, and Phase 11 requires a clean-machine install whose behaviour matches its documented capabilities.
Most items here land in Phase 7 (the diagnostics, the connection-state and firewall checks) or Phase 11 (the first-use download gates and neutral defaults).
This document owns the fresh-machine detail those phases depend on rather than restating it there.

`CROSS_PLATFORM_FINDINGS.md` owns the deeper platform-interface work these items sit on: the XDG data-directory abstraction, the secret-store contract, the unified startup preflight, and the POSIX shim resolver.
Most items here are platform-neutral browser or diagnostic work that the headless-Linux target inherits unchanged: the connection-state detection, the phone DNS checklist, the QR, the onboarding-prerequisites surface, and the first-use download gates.
The one Windows-specific item is the Windows Defender Firewall inspect and repair; on a headless Linux host the equivalent is a reachability probe plus `ufw`/`firewalld` guidance, so it must sit behind a platform boundary.

## Design constraint that bounds this plan

swe-mux and the Orca reference checkout (`.tmp-orca/`, untracked design reference) made opposite remote-access decisions.
Orca has its own auth: a bearer-credential device registry, pairing tokens with rotate and revoke, and a relay for arbitrary networks, and it advertises LAN and overlay interfaces.
swe-mux has no auth: it binds only loopback plus the specific Tailscale IPv4, never LAN and never `0.0.0.0`, and it treats the tailnet ACL as the whole perimeter (`design/features/remote-access.md`).

Therefore this plan borrows Orca's connection UX and diagnostics, which fit swe-mux's model, and rejects Orca's device registry, bearer credentials, and relay, which contradict it.
Adopting the registry or relay would mean building the auth subsystem swe-mux deliberately does not have, and is out of scope unless swe-mux later chooses to support LAN or public access.

## Implementation status

Implemented and verified in the backend and frontend; not yet shipped to the frozen desktop app.

P0:
- Done: real Tailscale connection state (`classify_tailscale_connection` in `tailscale.py`, surfaced through `remote_status`, rendered in the Remote and Voice tabs).
- Done: phone-side DNS checklist in the Remote and Voice tabs.
- Done: Tailscale-aware cause-pointing text (`connection_detail` per state).
- Done: Windows Defender Firewall inspect and repair (`windows_firewall.py`, `GET /api/remote/firewall`, `POST /api/remote/firewall/repair`, Remote-tab panel), platform-gated to a frozen Windows build.
- Done: one-click diagnostics export (`GET /api/diagnostics/export`, `mux doctor --export`, Remote-tab button with clipboard and textarea fallback).

P1 and P2:
- Done: QR of the connection URL and the "Connect a phone" modal (`frontend/src/remoteConnection.tsx` `ConnectionQr` via the `qrcode-generator` dependency, `frontend/src/ConnectPhone.tsx`), reachable from Settings -> Remote. The URL uses the `.ts.net` MagicDNS name.
- Done: "Not installed" next step (winget command plus a download link in the connection readout).
- Done: per-harness mux MCP toggle and hook-instrumentation toggle (`harness_mcp_enabled`, `harness_instrument_enabled` in `config.py`; the `instrument` gate threaded through `build_agent_adapter` and every adapter family; Settings -> Harnesses). Both are restart-scoped and named their consequence.
- Done: "What mux injects" disclosure in the first-run panel.
- Done: security posture statement in Settings -> Remote and the Connect-a-phone modal.
- Done: STT off by default with a first-use download note; neutral `en-US` TTS voice default; STT language/model stated as a first-use choice; scan-timeline model made an editable, changeable default.
- Done: model-catalog unknown-model path confirmed to degrade cleanly (family fallback in `claude_models.py`; covered by `tests/test_claude_models.py`).
- Done: OpenRouter key surface listing what the key unlocks (Settings -> Accounts).
- Done: provider-account login guidance in the first-run panel.
- Done: prerequisite checklist for Git, Node, npm, and Tailscale (`prerequisites.py`, `GET /api/diagnostics/prerequisites`, Settings -> Remote).
- Done: first-run chaining copy (harnesses -> project -> account login -> session) in the first-run panel.
- Done: CLI-version-drift signal (best-effort `probe_cli_version`, `version_untested` against a maintainer-armed `TESTED_CLI_VERSIONS`, shown in Settings -> Harnesses).
- Done: confirmed `ProcessPanel.tsx`'s `127.0.0.1:3000` was a seeded example; changed it to a placeholder so it no longer reads as an assumed dev-server port.
- Done (Phase 7): the diagnostics export, prerequisites, connection-state, and firewall pieces are now consumed by the consolidated `mux doctor` report (`GET /api/diagnostics/doctor`, assembled by `doctor.build_doctor_report`), which adds per-check severity/remedy, a machine-readable capability block, and the observation-freshness check. This document owns the fresh-machine detail; the aggregation lives in `ROADMAP.md` Phase 7.
- Done (Phase 11, W10): `mux doctor` answers when the daemon does not.
  The consolidated report above presupposed a running daemon, which made the one diagnostic the project ships useless for the most likely fresh-install failure - a new user whose install is broken ran it and got a connection error.
  An unreachable daemon now produces the local report (`doctor_local.build_local_doctor_report`) over the checks that stop a daemon starting: the Python floor, the package's own import graph, the config file, the frontend bundle in the installed package (the self-reported half of the release-artifact gate a wheel from a clean clone can otherwise fail silently), the data directory's existence and writability, whether `mux.db` opens, whether the configured port is already held, whether this host's PTY backend imports, the frozen app's supervisor bundle, the prerequisite tools, harness detection, the W9 first-use asset inventory below, and the presence of each optional extra with its install command.
  The daemon report is untouched and byte-compatible.
  Three things are load-bearing rather than incidental: `unchecked` is its own status so a skipped check reads as neither healthy nor absent, prerequisite/harness/asset rows come from the daemon report's own builders rather than a second copy, and the exit codes compose the existing two (`1` on a failing check, `3` otherwise) so a degraded report never exits `0`.
  Contract: `design/interfaces.md`, "`mux doctor` without a running daemon".
- Done (Phase 11 W9): the **first-use asset inventory** below is now reported rather than discovered by failing. See "First-use assets" for what a clean machine is told and what each state's command is.

Still open (deliberately not code): ship the frozen `dist/` app for external testers; code-signing and SmartScreen decision; foreign-PATH shim/detection testing across npm, bun, and native installers; CLI-on-PATH packaging verification; arming `TESTED_CLI_VERSIONS` with verified bounds.

## Remote connection

The current status probe reports only `available` (the Tailscale CLI is on PATH), which cannot tell logged-out from connected.
The `.ts.net` device name is computed (`tailscale.py` `tailscale_dns_name`) but is absent from the status payload.
There is no QR, and the phone-side DNS requirement is stated nowhere in the UI.

| Item | Priority | Intent | Key files |
|---|---|---|---|
| Real connection state | P0 | Read `BackendState` and `Self.DNSName` from `tailscale status --json`; report not-installed / logged-out / connected-as-`<device>.ts.net` with the exact next command per state | `src/swe_mux/tailscale.py` (`_probe_tailscale_status`), `src/swe_mux/server.py` (`remote_status`) |
| Phone-side DNS checklist | P0 | State it in the Remote and Voice tabs: enable "Use Tailscale DNS" on the phone; on Android set Private DNS to off or automatic | `frontend/src/Settings.tsx` (Remote and Voice tabs) |
| Tailscale-aware error text | P0 | Replace generic diagnostics with cause-pointing text ("check Tailscale on the phone"); mirror Orca `unreachableHostDetail` | `src/swe_mux/tailscale.py`, `frontend/src/Settings.tsx` |
| QR of the connection URL | P1 | Render `https://<device>.ts.net/` when Serve is up, else `http://<device>.ts.net:<port>/`; MagicDNS resolves the name to the `100.x` IP | `frontend/src/Settings.tsx`, status payload from `tailscale.py` |
| "Connect a phone" surface | P1 | One modal with the QR, hostname, DNS checklist, and live status, reachable from first-run and Settings | `frontend/src/` new component, `frontend/src/App.tsx` |
| "Not installed" next step | P2 | Link plus `winget install tailscale.tailscale` when the CLI is absent | `frontend/src/Settings.tsx` |

Rejected from Orca: the device registry, pairing tokens, rotate and revoke, and the relay broker.

## Windows release hardening

swe-mux binds a real host socket on the `100.x` address, so Windows Defender Firewall governs inbound to `swe-mux.exe` on the Private profile.
A blocking or absent inbound rule silently stops the first phone connect while the desktop keeps working over loopback, and nothing reports why.

| Item | Priority | Intent | Reference |
|---|---|---|---|
| Windows Defender Firewall inspect and repair | P0 | Detect a blocking or missing inbound rule for `swe-mux.exe` on the Private profile and offer a one-click elevated PowerShell repair | `.tmp-orca/src/main/runtime/windows-mobile-firewall.ts` (inspect and repair template) |
| Ship the frozen `dist/` app | P0 | A fresh clone serves no UI until `npm run build` runs once (gitignored static), so external testers get the packaged app | `CLAUDE.md` build notes, `packaging/` |
| Code signing and SmartScreen | P1 | Decide the signing story; an unsigned executable warning loses testers | `packaging/` |
| Shim and detection on a foreign PATH | P1 | Test each harness installed via npm, bun, and native installer so `which_real` and the shim-recursion guard are exercised | `src/swe_mux/shim_paths.py`, `src/swe_mux/harness.py` (`detect_installation`) |

## Agent instrumentation control

These follow the shipped three-state enablement pattern and are separate-criticality toggles.
The mux MCP toggle has low blast radius; the hook instrumentation toggle is load-bearing and must name its consequence.

| Item | Priority | Intent | Key files |
|---|---|---|---|
| mux MCP toggle | P1 | Per-harness, default on; disabling removes only the agent's fleet visibility and messaging | `src/swe_mux/config.py`, `src/swe_mux/mcp.py`, `frontend/src/Settings.tsx` (Agents) |
| Hook instrumentation toggle | P1 | Per-harness "Instrument / launch clean", default instrument; state that clean launch drops the harness to unobserved (no status, history, or queue) | `src/swe_mux/config.py`, `src/swe_mux/adapters/`, `frontend/src/Settings.tsx` (Agents) |
| "What mux injects" disclosure | P1 | One transparency line in first-run plus a per-session view; verify the per-session and cleanup property holds for every harness before stating it | `frontend/src/HarnessSetup.tsx`, `design/features/agent-environment.md` |

## First-run and onboarding

| Item | Priority | Intent | Key files |
|---|---|---|---|
| Chain the first-run path | P1 | Harnesses (shipped) to project to account to first session, each empty state pointing at the next step | `frontend/src/HarnessSetup.tsx`, `frontend/src/App.tsx` |
| Security posture statement | P1 | One prominent line: any tailnet device reaches this daemon with no login; do not enable the tailnet listener on a shared tailnet | `frontend/src/Settings.tsx` (Remote), `design/features/remote-access.md` |

## Defaults and first-use costs

The shippable code is free of hardcoded user identity, absolute personal paths, and a hardcoded daemon host.
`data_dir` is home-relative (`~/.mux`), the frontend talks to the daemon over relative URLs, `pwsh` is auto-detected, and OpenRouter models default blank with the key read from the secret store or `OPENROUTER_API_KEY`.
So the "bespoke to one workflow" surface is config defaults and first-use downloads, not values to un-hardcode.

| Item | Priority | Intent | Key files |
|---|---|---|---|
| STT default and download gate | P1 | Done. `stt_enabled` defaults false *and* the download is now an explicit act with reported states rather than a first-capture side effect - see "First-use assets" | `src/swe_mux/config.py` (`stt_enabled`, `stt_whisper_model`), `src/swe_mux/voice_models.py`, `src/swe_mux/voice.py` |
| Neutral TTS voice default | P1 | Done, and re-verified 2026-08-27: `tts_edge_voice` is `en-US-JennyNeural`. The Australian default this row was written against is long gone | `src/swe_mux/config.py` (`tts_edge_voice`) |
| Stated STT language and model | P2 | Done, and re-verified 2026-08-27: both are editable inputs in Settings -> Voice under copy that calls them a first-use choice. They remain English-first by default, which is stated rather than hidden | `src/swe_mux/config.py`, `frontend/src/Settings.tsx` (Voice) |
| Scan-timeline model default | P2 | `deepseek/deepseek-v4-flash` is an opinionated default that needs a key; document it as a changeable default | `src/swe_mux/config.py` (`scan_timeline_model`) |
| Model catalog upkeep | P2 | `claude_models.py` hardcodes model ids and context windows; a model newer than the catalog shows no context percentage; confirm the unknown-model path degrades cleanly and track new releases | `src/swe_mux/claude_models.py` |

## First-use assets

This document owns the inventory of things a clean machine does **not** have and swe-mux does not bundle.
Phase 11 W9 closed it, and the shape of the fix is worth stating once because every future optional asset should follow it.

The rule: **an absent capability must say which kind of absent it is, and nothing is fetched without an explicit act.**
That is the same discipline `design/features/agent-environment.md` states for an empty MCP catalog, applied to installation state.
One "unavailable" is what made a fresh install fail oddly - an operator who had already installed the Playwright extra was told to install it again, and the first press of Talk pulled 1.6 GB with no one asking.

| Asset | States a clean machine can be in | Command for each | Reported by |
|---|---|---|---|
| Preview capture (Playwright package) | `extra_missing` | `uv sync --extra preview-capture && uv run playwright install chromium`, or "no command helps" on the packaged app | `POST /previews/{id}/capture`, `optional_asset:preview_capture` in `mux doctor` |
| Preview capture (Chromium binary) | `browser_missing` | `uv run playwright install chromium` - and *only* that half | same |
| On-device speech libraries | `not_downloaded` / `downloading` / `error`, plus `unsupported` for a platform the pinned closure has no wheels for | Settings -> Voice -> Download speech libraries (or the `voice-local` extra, which is the only remedy when `unsupported`) | `GET /api/voice`, `GET /api/voice/models/runtime`, `optional_asset:voice_runtime` |
| Whisper dictation weights | `not_downloaded` / `downloading` / `error`, plus `backend_installed: false` when the speech libraries are absent | Settings -> Voice -> Download (or `uv sync --extra voice-local` for the extra) | `GET /api/voice`, `GET /api/voice/models/whisper`, `optional_asset:voice_whisper:<model>` |
| Kokoro read-aloud weights | `not_downloaded` / `downloading` / `error` | Settings -> Voice -> Download Kokoro voices | `GET /api/voice`, `optional_asset:voice_kokoro` |
| Silero VAD runtime + model | **none** - it ships in the frontend bundle | n/a | n/a |

Four things that were already true before W9 and needed confirming rather than fixing:

- `tts_enabled` and `stt_enabled` both default `False`, so an untouched install downloads nothing at all. `tests/test_first_use_assets.py` pins that so it cannot be flipped quietly.
  Since 2026-08-29 that default carries more weight than it did: it is also the argument for the desktop bundle not *containing* the speech libraries, because an untouched install downloading 277 MB it will never load is the same defect as a surprise fetch, read the other way round.
- `tts_edge_voice` already defaults to the neutral `en-US-JennyNeural`, not the Australian voice this document's earlier draft recorded.
- `stt_language` and `stt_whisper_model` are already drawn in Settings -> Voice as explicit first-use choices with copy saying so. They are English-first (`en-US`, `turbo` for dictation, `small.en` for routing) and stated rather than hidden; W9 did not change them, because changing a default is not the same as reporting one.
- The Silero VAD assets do **not** download. Vite emits the ~11 MB WASM runtime and ~2.3 MB ONNX model into the frontend bundle and this daemon serves them same-origin; the "lazy" load on first Talk is a lazy import. Both this document and the Settings copy previously said otherwise, and both are corrected.

What W9 actually added: the three-state preview-capture report with a per-half remedy and a launch-time reclassification when the filesystem probe was wrong; `WhisperModelStore` (`not_downloaded → downloading → ready → error`, the same mechanism the Kokoro weights already used) with its own routes and Settings panel; a transcription refusal in place of the implicit fetch, including the skip that stops an absent *routing* model being downloaded to discover it was never needed; and an `optional_assets` block plus `optional_asset:*` checks in `mux doctor`, at severity `optional` so a clean install never exit-codes non-zero for owning none of them.

Still open and deliberately not code: preview capture does not work in the frozen desktop app at all, because `preview-capture` is outside `DISTRIBUTED_EXTRAS` (`packaging/license_audit.py`).
Bundling a ~150 MB Chromium for an optional feature and auto-running `playwright install` on first press were both rejected - the second is the exact silent-fetch failure this section exists to remove.
The packaged app now says so instead of failing quietly.

**The mechanism that would close it now exists**, and is recorded here rather than built, because it is a product decision rather than an engineering one.
`swe_mux.voice_runtime` acquires a pinned, hash-verified wheel closure on an explicit press and puts it on `sys.path`, and Playwright is exactly the shape it handles (2026-08-29, ROADMAP Phase 21 Workstream D).
What it does *not* handle is the second half: `playwright install chromium` downloads a browser through Playwright's own installer, which is a separate mechanism with a separate cache and a separate trust story, and it is ~150 MB against the wheel's ~40.
So the honest version of "apply the same treatment to Playwright" is two presses with two stated sizes, not one - and it is worth having only if somebody wants preview capture in the packaged app, which nobody has asked for.
What Phase 21 did close is the other direction: Playwright can no longer ride into the bundle behind the lazy `import playwright` in `preview_capture.py`, because `verify_bundle_contents` asserts the bundle's package set in both directions.

## The first-use download question

Recorded 2026-08-29, with the options and a recommendation, because the desktop app now acquires the on-device speech closure at first use and a question that size should not be answered by accident.

**The question:** an install that has never enabled voice has no speech engine on disk.
Should it fetch one for the user, and if so, when?

Three options were considered.

- **Explicit press only** - what is implemented.
  Nothing is fetched until somebody presses Download in Settings -> Voice, the size is stated before the press, and every surface that needs the closure says which kind of absent it is and what the press would do.
  Costs the user one press and about ten seconds the first time they turn on read aloud or dictation.
- **Fetch during first-run setup**, alongside the other onboarding steps.
  Removes the later press, and re-introduces exactly what W9 removed: a fresh install that downloads a few hundred megabytes of machinery for a feature that ships switched off and that most users never enable.
  This is the version of the defect that motivated the whole first-use asset contract, moved from first Talk to first launch.
- **Fetch on the first press of the feature itself** - the first Talk, or the first read-aloud.
  Removes the press but restores the silent fetch inside a code path the user thinks is doing something else, which is the specific failure `WhisperModelStore` exists to stop.

**Recommendation and what is implemented: explicit press only.**
The rule this section already states settles it - "an absent capability must say which kind of absent it is, and nothing is fetched without an explicit act" - and the speech libraries are simply a fourth asset under that rule rather than a new kind of thing.

**The number of presses was left at three and that was wrong; it is one now.**
The first draft of this section recorded the three-press sequence as an open UI question, on the reasoning that the three stores fail independently and a merged progress bar would have to lie about which one failed.
The reasoning was right and the conclusion did not follow.
It cost a real operator a failure the same day: he pressed two of the three, reasonably concluded he was done, and met a 500 at the first spoken sentence.

Independent failure is an argument for three *lines*, not three controls.
`POST /api/voice/models/kokoro/download` now starts all three stores and the panel draws a line each, so a failure names its own store; one button retries exactly what failed, because every store's `start_download` short-circuits when it is already `ready`.
Dictation is one press too, and chains rather than parallelising: `WhisperModelStore._download` calls `backend_installed()`, so weights started beside the closure would fail immediately and read as a broken weights download.

The general rule, worth stating because it will recur with the next first-use asset: **a capability gets one press, and its prerequisites are sub-steps rather than errands.**
Making the user the integrator of N stores is the same defect as a silent fetch, arrived at from the opposite direction.

## Onboarding prerequisites

Several features assume external state a new user has not set up yet.
Each fails gracefully today, but the absence is invisible, so the disabled capability reads as broken rather than unconfigured.

| Item | Priority | Intent | Key files |
|---|---|---|---|
| OpenRouter key surface | P1 | Automation, scan timeline, TTS summary, attention narration, titler, summarizer, and the project card all need the key; add one place that lists what the key unlocks, with status and set or clear | `src/swe_mux/secret_store.py`, `src/swe_mux/server.py` (OpenRouter key routes), `frontend/src/Settings.tsx` |
| Provider account login guidance | P1 | mux reads Claude and Codex auth files, so the account switcher is empty until the user logs in through each CLI; first-run should point at that step | `src/swe_mux/provider_accounts.py`, `frontend/src/HarnessSetup.tsx` |
| Prerequisite checklist | P1 | Detect and state the presence of Git, Node and npm, and Tailscale, each with a next step; Git backs worktrees and status, Node backs ccusage, Tailscale backs remote and mobile | new detection, `frontend/src/Settings.tsx` or first-run |
| CLI on PATH verification | P1 | Confirm the packaged install puts `mux` and `muxd` on PATH; reload, doctor, and spawn assume they are reachable | `packaging/` |

Verify: `frontend/src/ProcessPanel.tsx` references `http://127.0.0.1:3000`; confirm it is a preview or example placeholder and not an assumed dev-server port.

## Diagnostics

| Item | Priority | Intent | Key files |
|---|---|---|---|
| One-click diagnostics export | P0 | Bundle `daemon.log`, `redeploy.log`, the status diagnostic bundle, network diagnostics, and sanitized config into one copyable blob; mirror Orca `buildConnectionDiagnosticsReport` | `src/swe_mux/server.py`, existing `mux doctor` and `/api/diagnostics/network` |
| CLI-version drift signal | P1 | Confirm graceful degradation on a newer CLI and surface "this CLI is newer than mux tested" | `src/swe_mux/harness.py`, `design/features/backends.md` |

## Sequencing

1. Diagnostics export and the P0 Tailscale-state items (connection state, DNS checklist, error text).
   These make the fresh-machine test produce signal rather than a silent failure.
2. Windows Defender Firewall inspect and repair.
   This is the most likely first-connect failure on a fresh Windows machine.
3. QR and the "Connect a phone" surface.
4. The STT default or download gate, and the onboarding-prerequisites surface (OpenRouter key, provider login, Git, Node, Tailscale).
   These stop unconfigured features from reading as broken on a fresh machine.
5. MCP and hook toggles, first-run chaining, the security statement, and the remaining defaults and catalog upkeep.

## Reference material

- swe-mux remote access: `design/features/remote-access.md`, `src/swe_mux/tailscale.py`, `src/swe_mux/__main__.py` (`_auto_enable_mobile_voice`), `src/swe_mux/server.py` (`remote_status`, `enable_mobile_voice`).
- swe-mux harness detection and enablement: `design/features/backends.md`, `src/swe_mux/harness.py`, `src/swe_mux/config.py`.
- Orca connection model (reference only, `.tmp-orca/`, untracked): `src/main/ipc/mobile.ts` (QR pairing and device registry), `mobile/src/transport/mobile-direct-endpoint-probe.ts` (multi-path fallback), `mobile/src/diagnostics/host-reachability.ts` (reachability probe and Tailscale-aware errors), `mobile/src/diagnostics/connection-diagnostics-report.ts` (shareable report), `src/main/runtime/windows-mobile-firewall.ts` (firewall inspect and repair).
