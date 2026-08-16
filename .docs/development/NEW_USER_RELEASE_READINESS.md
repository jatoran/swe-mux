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
- Done: per-harness mux MCP toggle and hook-instrumentation toggle (`harness_mcp_enabled`, `harness_instrument_enabled` in `config.py`; the `instrument` gate threaded through `build_agent_adapter` and every adapter family; Settings -> Agents). Both are restart-scoped and named their consequence.
- Done: "What mux injects" disclosure in the first-run panel.
- Done: security posture statement in Settings -> Remote and the Connect-a-phone modal.
- Done: STT off by default with a first-use download note; neutral `en-US` TTS voice default; STT language/model stated as a first-use choice; scan-timeline model made an editable, changeable default.
- Done: model-catalog unknown-model path confirmed to degrade cleanly (family fallback in `claude_models.py`; covered by `tests/test_claude_models.py`).
- Done: OpenRouter key surface listing what the key unlocks (Settings -> Automation).
- Done: provider-account login guidance in the first-run panel.
- Done: prerequisite checklist for Git, Node, npm, and Tailscale (`prerequisites.py`, `GET /api/diagnostics/prerequisites`, Settings -> Remote).
- Done: first-run chaining copy (harnesses -> project -> account login -> session) in the first-run panel.
- Done: CLI-version-drift signal (best-effort `probe_cli_version`, `version_untested` against a maintainer-armed `TESTED_CLI_VERSIONS`, shown in Settings -> Agents).
- Done: confirmed `ProcessPanel.tsx`'s `127.0.0.1:3000` was a seeded example; changed it to a placeholder so it no longer reads as an assumed dev-server port.
- Done (Phase 7): the diagnostics export, prerequisites, connection-state, and firewall pieces are now consumed by the consolidated `mux doctor` report (`GET /api/diagnostics/doctor`, assembled by `doctor.build_doctor_report`), which adds per-check severity/remedy, a machine-readable capability block, and the observation-freshness check. This document owns the fresh-machine detail; the aggregation lives in `ROADMAP.md` Phase 7.

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
| STT default and download gate | P1 | `stt_enabled` is true by default, so the first Talk downloads the Whisper `turbo` model, the Silero VAD WASM runtime, and its ONNX assets with no warning; default STT off, or gate the first capture behind a clear "this downloads a speech model" confirmation | `src/swe_mux/config.py` (`stt_enabled`, `stt_whisper_model`), `src/swe_mux/voice.py` |
| Neutral TTS voice default | P1 | `tts_edge_voice` defaults to the Australian `en-AU-NatashaNeural` for every user; use `en-US` or a locale-derived voice (TTS is off by default, so impact is low) | `src/swe_mux/config.py` (`tts_edge_voice`) |
| Stated STT language and model | P2 | `en-US` and `turbo` are English-first and large; surface them as an explicit first-use choice rather than a silent assumption | `src/swe_mux/config.py`, `frontend/src/Settings.tsx` (Voice) |
| Scan-timeline model default | P2 | `deepseek/deepseek-v4-flash` is an opinionated default that needs a key; document it as a changeable default | `src/swe_mux/config.py` (`scan_timeline_model`) |
| Model catalog upkeep | P2 | `claude_models.py` hardcodes model ids and context windows; a model newer than the catalog shows no context percentage; confirm the unknown-model path degrades cleanly and track new releases | `src/swe_mux/claude_models.py` |

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
