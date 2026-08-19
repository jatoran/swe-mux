# Documentation routing

- Changing process/session lifecycle or package boundaries: `design/architecture.md`,
  `design/features/sessions.md`, `design/features/backends.md`,
  `design/features/delivery-readiness.md`
- Changing what survives a crash rather than a restart - the durable session registry, terminal
  checkpoints, cold sessions, or whether an ended pane stays readable:
  `design/features/session-recovery.md`, `design/features/sessions.md`, `design/data-model.md`,
  `design/interfaces.md`, `technical/backend/packages.md`, `technical/backend/sqlite.md`,
  `technical/frontend/packages.md`.
  The rule the split exists to enforce: the PTY supervisor is the *primary* recovery path and stays
  near-frozen, so nothing here may be written from the supervisor process - a change there reaps
  every live session (`development/archive/SESSION_PRESERVING_RELOAD.md` §8).
- Changing the harness registry, descriptors, capability levels, adapter families, or adding a
  harness: `design/features/backends.md`; completed abstraction record:
  `development/archive/HARNESS_ABSTRACTION_AND_OMP.md`; per-surface parity classification and
  the open enforcement gaps: `development/archive/HARNESS_PARITY_AUDIT_2026-08-11.md`;
  per-candidate parity study for CLIs not yet in the registry, and the sequencing that consumes
  it: `development/HARNESS_EXPANSION_CANDIDATES.md`, `development/ROADMAP.md` Phase 12
- Changing Project/Group registration, ownership, ordering, or sidebar visibility:
  `design/features/projects.md`, `design/data-model.md`, `design/interfaces.md`
- Changing Project notes, the global Scratchpad, files, ignores, or watches:
  `design/features/project-resources.md`, `design/data-model.md`, `design/interfaces.md`
- Changing Agent Context discovery, root instruction sync/restore, or its drawer surface:
  `design/features/agent-context.md`, `design/features/ui.md`, `design/interfaces.md`,
  `technical/backend/packages.md`, `technical/frontend/packages.md`
- Changing the session Agent Environment inventory, safety boundaries, or drawer surface:
  `design/features/agent-environment.md`, `design/features/ui.md`, `design/interfaces.md`,
  `technical/backend/packages.md`, `technical/frontend/packages.md`; runtime inventory research
  and planned collection strategy: `development/AGENT_ENVIRONMENT_RUNTIME_INVENTORY.md`
- Changing trusted task imports, the Project Run menu, or task launch:
  `design/features/project-actions.md`, `design/features/projects.md`, `design/interfaces.md`,
  `technical/backend/packages.md`, `technical/frontend/packages.md`
- Changing panes, tabs, splits, drag/drop, or the mobile workspace projection:
  `design/features/workspace-layout.md`, `technical/frontend/workspace-state.md`
- Changing browser chrome, sidebar interaction, settings, focus, or overlays:
  `design/features/ui.md`, `technical/frontend/packages.md`
- Adding a surface that goes inert behind a switch, or changing how one reaches that switch
  (the deep link, the scroll-and-flash arrival, the `data-setting` marks):
  `design/features/setting-links.md`, `design/features/ui.md`,
  `technical/frontend/packages.md`; per-Project opt-ins themselves:
  `design/features/automation-enablement.md`
- Changing what shows or hides the mobile soft keyboard: `design/features/ui.md`,
  `technical/frontend/packages.md`; open ask against the vendored note editor:
  `development/CONTINUITY_TOUCH_KEYBOARD_ASK.md`
- Changing terminal input ownership across devices, the PTY WebSocket frames, or how a
  shared PTY is sized: `design/features/terminal-input.md`, `design/interfaces.md`,
  `design/features/sessions.md`, `design/features/ui.md`,
  `technical/backend/packages.md`, `technical/frontend/packages.md`; investigating delayed
  terminal input: `development/TERMINAL_INPUT_INCIDENT_RUNBOOK.md`
- Changing device presence (what counts as "in use", the heartbeat, the leading device):
  `design/features/device-presence.md`, `design/interfaces.md`,
  `design/features/notifications.md`, `design/features/terminal-input.md`
- Changing the utility drawer (tabs, which of them are drawn, recursive desktop dock/launcher), Action rail placement, or where
  inserted text lands: `design/features/ui.md`, `design/features/workspace-layout.md`,
  `technical/frontend/workspace-state.md`, `technical/frontend/packages.md`;
  completed custom drawer layout implementation record:
  `development/archive/UTILITY_DRAWER_LAYOUT_IMPLEMENTATION.md`
- Changing agent-skill discovery (which CLI directories are scanned, the metadata read from
  them, or how the Actions tab lists them): `design/features/ui.md`, `design/interfaces.md`,
  `technical/backend/packages.md`, `technical/frontend/packages.md`
- Changing clipboard capture, the clipboard-history ring/panel, or where inserted text lands:
  `design/features/ui.md`, `design/interfaces.md`, `design/data-model.md`,
  `technical/frontend/packages.md`, `technical/backend/packages.md`,
  `technical/backend/sqlite.md`, `design/features/remote-access.md`
- Changing Windows desktop packaging, WebView, tray, login startup, or daemon shutdown:
  `design/features/desktop-shell.md`, `design/architecture.md`, `design/interfaces.md`,
  `design/features/remote-access.md`, `technical/backend/packages.md`
- Changing reusable prompt templates: `design/features/prompt-library.md`,
  `design/interfaces.md`, `design/data-model.md`
- Changing the prompt queue (message model, states, head-of-line, send-next, stranding,
  Queue tab, send-to-agent senders, new-session seed staging):
  `design/features/prompt-queue.md`, `design/features/delivery-readiness.md`,
  `design/interfaces.md`, `design/data-model.md`, `technical/backend/packages.md`,
  `technical/frontend/packages.md`
- Changing gated auto-delivery (the per-conversation default/override, stability window, quiet hours,
  emergency pause, item scheduling/expiry, or the promotion criteria):
  `design/features/auto-delivery.md`, `design/features/prompt-queue.md`,
  `design/features/delivery-readiness.md`, `design/interfaces.md`, `design/data-model.md`
- Changing agent-to-agent messages, the fleet queue (the app-wide authorship view, served
  by the older-named `/api/queue/mailbox`), sender provenance, or drafted spawn requests:
  `design/features/agent-messaging.md`, `design/features/mux-mcp.md`,
  `design/features/observations.md`, `design/features/prompt-queue.md`,
  `design/features/ui.md`, `design/interfaces.md`, `design/data-model.md`
- Changing root-session or quota-reset sounds: `design/features/notifications.md`,
  `design/features/voice.md`
- Changing web push, which device a notification reaches, or the notification preference
  shape: `design/features/notifications.md`, `design/features/device-presence.md`,
  `design/interfaces.md`, `technical/backend/packages.md`,
  `technical/frontend/packages.md`
- Changing managed-harness account capture, switching, or quota polling:
  `design/features/provider-accounts.md`, `design/features/backends.md`
- Changing history, transcripts, or cross-vendor review: `design/features/history.md`,
  `design/interfaces.md`
- Changing Git status, comparison, diff review, first-time repository initialization, or worktree tooling:
  `design/features/git.md`, `design/features/project-resources.md`, `design/interfaces.md`,
  `technical/backend/packages.md`, `technical/frontend/packages.md`,
  `technical/frontend/workspace-state.md`
- Changing attention ranking, the interrupt budget, the four delivery channels, breakpoint
  detection, the absence digest, mined demotion rules, or model narration:
  `design/features/attention-ranking.md`, `design/features/deterministic-consumers.md`,
  `design/features/fleet-intelligence.md`, `design/features/automation-enablement.md`,
  `design/interfaces.md`, `design/data-model.md`, `technical/backend/packages.md`,
  `technical/backend/sqlite.md`, `technical/frontend/packages.md`
- Changing automation, observers, attention, or legacy hooks:
  `design/features/automation.md`, `design/features/fleet-intelligence.md`,
  `design/features/meta-hooks.md`, `design/features/delivery-readiness.md`
- Changing session status detection, the transition ledger, the state watchdog,
  awaiting sub-reasons, the detection golden corpus, or status-health diagnostics:
  `design/features/status-detection.md`, `design/features/delivery-readiness.md`
- Changing control-plane approvals (the per-conversation mode, the allow rules, the
  never-auto-approved floor, the decision hook, or the approval strip):
  `design/features/approvals.md`, `design/features/status-detection.md`,
  `design/features/backends.md`, `design/interfaces.md`, `design/data-model.md`,
  `technical/backend/packages.md`, `technical/frontend/packages.md`.
  The rule the design exists to enforce: a decision is made from the harness's structured
  permission request, never from the PTY screen, and the floor in `approvals.py` is checked
  before the mode so no configuration can reach past it. `deny` is deliberately not a
  decision mux ever makes.
- Changing the durable status timeline (its table, sink, layer readings, or the
  state-log/diagnostic-bundle endpoints) or the incident investigation procedure:
  `design/features/status-detection.md`, `design/interfaces.md`, `design/data-model.md`,
  `technical/backend/sqlite.md`, `technical/backend/packages.md`,
  `development/STATUS_INCIDENT_RUNBOOK.md`
- Changing background-loop supervision, per-loop cost accounting, event-loop lag sampling,
  or the performance investigation procedure: `development/PERFORMANCE_RUNBOOK.md`,
  `technical/backend/packages.md`, `design/interfaces.md`
- Changing HTTP/WebSocket traffic accounting, response compression, static precompression,
  or browser polling cadence: `design/features/remote-access.md`, `design/interfaces.md`,
  `design/features/processes-and-previews.md`, `development/PERFORMANCE_RUNBOOK.md`,
  `technical/backend/packages.md`, `technical/frontend/packages.md`
- Changing scheduled runs (the triggers and their wall-clock/DST arithmetic, the missed-window
  policy, the fire guards, the Schedule drawer tab, or where a definition is stored):
  `design/features/scheduled-runs.md`, `design/features/automation-enablement.md`,
  `design/features/prompt-queue.md`, `design/features/ui.md`, `design/interfaces.md`,
  `design/data-model.md`, `technical/backend/packages.md`, `technical/backend/sqlite.md`,
  `technical/frontend/packages.md`.
  The rule the design turns on: a schedule is a *user-authored deferred spawn*, so it goes
  through the ordinary spawn path and the ordinary prompt queue and never grows a second
  authority; and the definitions stay machine-local, because a schedule committed to a
  repository would arm itself in every clone and worktree.
- Changing per-project automation opt-in, the enablement dependency graph, or its toggle
  surface: `design/features/automation-enablement.md`,
  `design/features/project-resources.md`, `design/data-model.md`, `design/interfaces.md`
- Changing the model-free control-plane detectors (loop/stall, declared-vs-verified,
  doc debt, provenance) or the annotation anchor/evidence schema:
  `design/features/deterministic-consumers.md`, `design/features/tier0-facts.md`,
  `design/data-model.md`, `technical/backend/packages.md`
- Changing Tier 0 deterministic fact capture or its source pointers/fingerprints:
  `design/features/tier0-facts.md`, `design/data-model.md`,
  `technical/backend/packages.md`, `technical/backend/sqlite.md`
- Changing the code-structure graph (tree-sitter engine, import resolution, the
  graph tables, blast-radius/navigation/context/test-gap MCP tools, the
  blast-radius/dead-code/god-node/import-cycle annotations, the doc-debt reach
  refinement, or the per-session change map): `design/features/code-graph.md`,
  `design/features/mux-mcp.md`, `design/features/deterministic-consumers.md`,
  `design/features/automation-enablement.md`, `design/features/desktop-shell.md`
  (grammar bundling), `design/interfaces.md`
- Changing the user-owned Project context card (its fixed file, editor, bounds, revision contract, setup prompt, or scan prefix): `design/features/project-card.md`, `design/features/automation-enablement.md`, `design/data-model.md`, `design/interfaces.md`, `technical/backend/packages.md`, `technical/frontend/packages.md`
- Changing the scan timeline, per-run scan grant, rollover boundary, scan budgets, source
  rehydration, or dead-end extraction: `design/features/scan-timeline.md`,
  `design/features/automation-enablement.md`, `design/features/automation.md`,
  `design/data-model.md`, `design/interfaces.md`, `technical/backend/packages.md`,
  `technical/backend/sqlite.md`, `technical/frontend/packages.md`
- Changing the agent MCP surface (endpoint, tools, per-session tokens, CLI registration):
  `design/features/mux-mcp.md`, `design/interfaces.md`, `technical/backend/packages.md`
- Changing the observation inbox: `design/features/observations.md`, `design/interfaces.md`,
  `design/data-model.md`
- Changing preview screenshot capture or the region selector:
  `design/features/processes-and-previews.md`, `design/interfaces.md`
- Changing processes, listeners, Preview ownership/proxying, or Preview tab lifetime:
  `design/features/processes-and-previews.md`, `design/features/remote-access.md`,
  `technical/backend/packages.md`, `technical/frontend/workspace-state.md`
- Changing headless-browser ghost-window detection, the sweep predicate, or its remediation:
  `design/features/ghost-windows.md`, `technical/backend/packages.md`
- Changing durable process/quota/reset/compaction/tool evidence or retention:
  `design/features/operational-telemetry.md`, `design/data-model.md`, `design/interfaces.md`
- Changing launch profiles (shell or agent), profile resolution, or reserved harness argv: `design/features/launch-profiles.md`
- Evaluating or changing host-OS support, Windows shell compatibility, WSL behavior,
  platform packaging, or the external compatibility matrix:
  `development/CROSS_PLATFORM_FINDINGS.md`, `design/features/launch-profiles.md`,
  `design/features/backends.md`, `design/features/project-actions.md`,
  `development/ROADMAP.md`
- Changing anything behind a platform seam - pseudoterminal allocation, process-tree
  ownership, path identity, where secrets rest, or where the data directory lives:
  `technical/backend/packages.md` (the per-module map), `development/ROADMAP.md` Phase 10.
  The rule the seams exist to enforce: `host_platform.py` answers *which host this is* and
  nothing else, while whether a capability exists is answered by the module that owns it -
  a capability can be absent on a supported platform, and conflating the two is how a port
  starts claiming parity it does not have.
  Verify a change on both hosts, not one: `tools/linux_container_verify.sh` runs the suite
  on Linux from a Windows host with only Docker, and `.worktree-verify` runs the two
  `--platform` mypy passes so each host's implementation is typechecked wherever the gate runs.
- Changing the WSL agent bridge (distro-side discovery, path translation, the bridge script,
  the WSL listener, or its firewall rule): `src/swe_mux/wsl_bridge.py`,
  `design/features/backends.md`, `development/ROADMAP.md` Phase 10.
  Its failure mode is silence by construction - a bridged agent that cannot reach the daemon
  runs perfectly and simply never reports - so any change must keep the reachability probe
  and the `reasons` it produces, and must never let "not checked" render as available.
- Changing usage analytics: `design/features/usage.md`
- Changing read aloud or hands-free conversation: `design/features/voice.md`;
  completed voice-interaction phases and their decisions:
  `development/archive/VOICE_INTERACTION_ROADMAP.md`
- Changing the Mux assistant (the chat surface, the tool bridge, the trust policy, the
  voice fallback tiers, or its dialogs/actions): `design/features/assistant.md`,
  `design/features/voice.md`, `design/interfaces.md`, `technical/backend/packages.md`,
  `technical/frontend/packages.md`; the plan: `development/ROADMAP.md` Phase 10.6.
  The rule the design enforces: the model proposes names, deterministic code resolves and
  executes through existing paths, and the consequential-action confirmation floor is not
  configurable.
- Changing listeners, Tailscale, browser security, or remote operation:
  `design/features/remote-access.md`
- Planning remaining work: `development/ROADMAP.md`; control-plane plan + completion
  checklist (start at §9): `development/CONTROL_PLANE_ROADMAP.md`
- Changing backend package ownership or shared SQLite behavior:
  `technical/backend/packages.md`, `technical/backend/sqlite.md`

# Audio (voice) system — quick reference

Full detail: `design/features/voice.md`. Two independent halves in one `VoiceService`
(`src/swe_mux/voice.py`):

- **Read aloud (TTS):** `turn_ended` (auto, 1s debounce) or manual → last-turn slice →
  `summary` (OpenRouter cheap model, budgeted under `builtin:voice-summary`) or `verbatim`
  (`speechify`, no LLM) → OS-voice (SAPI) or local Kokoro-82M (onnxruntime; pinned
  hash-verified download, espeak-free misaki G2P) WAV clips in `<data_dir>/voice/` +
  `voice_clips` SQLite.
  Words the Kokoro repair ladder can only spell out letter by letter are fixable with a
  `tts_lexicon` respelling (Settings → Voice; hot-applied), and each spell-out is recorded
  durably (`spelled_words.json`, surfaced by `GET /api/voice`) with a one-tap respell there.
  A respelling must itself be pronounceable (the editor checks each row via
  `POST /api/voice/lexicon/check` and auditions via `GET /api/voice/lexicon/preview`);
  exact sounds use misaki's `[word](/phonemes/)` form, atomic in the ladder.
  Automatic, manual, and application speech keeps ordinary replies in one coherent clip and returns a complete opening sentence for longer streams before tracked background tasks synthesize the remaining sentence-sized clips.
  Browser playback uses one singleton audio element; confirmed speech hard-stops and suppresses the whole current stream.
  Failures are typed `VoiceError` and never touch the PTY/history/transcripts.
- **Conversation (STT):** browser capture through an `AudioWorklet` → 512-sample 16 kHz frames →
  **Silero VAD** (`sileroVad.ts`, lazy ~11 MB WASM runtime + ~2.3 MB ONNX model assets; energy detector as fallback) → the
  frame-counted endpoint gate (`speechGate.ts`: 352 ms tail, speculative decode at 160 ms) →
  `voice/transcribe` → faster-whisper, **two decode profiles** (`command` = `small.en` greedy,
  `dictation` = `turbo` with beam 5 above 3 s), decoded from memory with no disk write →
  wake-word + command-phrase **suffix**, which a speculative transcript can use to short-circuit the remaining tail → one workspace-level draft targeting the focused Agent or text surface.
  App-owned Talk state renders in the focused pane's floating voice overlay, with a fixed top fallback when no terminal pane is visible.
  It names the target, supports an exact-target pin, keeps the draft across focus changes, and retains a device-local user/Mux history.
  Dynamic navigation, direct spawn, typed fleet/help/reply queries, and guarded approvals resolve through voice aliases on the ordinary command registry.
  With a running session focused, that registry also exposes terminal copy/paste, explicitly voiced safe rail keys, and configured agent skill/slash rail items through `railVoice.ts`.
  It excludes attachments, keyboard mode, clear-input, arbitrary prompt/text macros, and destructive rail actions.
  Those commands execute through the mounted terminal owner and report its acknowledgement instead of assuming a PTY action succeeded.
  Spoken navigation uses live hierarchical indexes without requiring a prior list: `Project N` follows visible sidebar order, bare `Session N` follows the selected Project's sidebar session order, `next/previous session` traverses that same Project-scoped order without wrapping, and `Project N Session N` resolves both coordinates atomically.
  Spoken lists retain those canonical addresses, speak five explicitly separated items at a time, and keep five-minute device-local paging context for next/repeat/detail follow-ups.
  Playback leaves capture open; possible speech immediately ducks app audio, waits three frames for echo to drain, then three accepted frames on the quiet mic stop the whole stream before transcription and continue as ordinary dictation or wake-word command input. Rejected echo restores playback. Bare `Mux, stop` stops playback without releasing Talk.
  Agent Send and Append use the mounted terminal's acknowledged xterm path, so Send appends to an existing composer, waits 180 ms for bracketed-paste commit, and then uses the same carriage return as the visible mobile control.
  Note, Scratchpad, Markdown, and Queue-composer Send and Append only insert at the caret without staging or delivering a queue item.
  Voice Comms pins one Agent, prepends a short-response protocol once per run, and temporarily enables automatic verbatim playback until restored.
  Capture always decodes through session-free `/api/voice/transcribe`.
  Wake words and the phrase→action map are user-configurable (`voice_wake_words` /
  `voice_commands` in config, editable in Settings → Voice; `buildVoiceMatcher` compiles them).
  Fixed action set: `send`/`append`/`cancel`/`undo`/`mute`/`read`/`summary`/`verbatim`/`interrupt`/
  `help`/`standby`/`resume`/`comms_on`/`comms_off`/`stop`. `standby` keeps the mic on but ignores everything except a
  `resume`/`stop` command; `stop` releases the mic. Hold `Ctrl+Alt+Space` for push-to-talk with
  no endpointing. `GET/POST/DELETE /api/voice/stt-latency` is the end-of-speech-to-action stage
  breakdown (also in `daemon.log`), read in Settings → Voice beside the wake-word tester.
  `POST /api/voice/barge-in-diagnostic` validates and logs confirmed/rejected browser sidechain probes.
  `voiceQueries.ts` adds the non-configurable deterministic query grammar for help, scoped fleet lists/status, numbered navigation, and one-shot summary/verbatim reply reading.
  Routing is three tiers: this deterministic grammar, a conservative fuzzy pass (`voiceFuzzy.ts`), and — only on no-match, only when enabled — the Mux assistant (`design/features/assistant.md`), whose replies render in the voice panel's `chat` mode and speak through the application-speech path.

**Mobile mic needs HTTPS (secure context).** swe-mux runs **Tailscale Serve on 443**
(`https://<device>.ts.net/`) proxying to the daemon's loopback port — auto-started at boot
(`_auto_enable_mobile_voice`), idempotent. Serve is on 443 **not** the swe-mux port because the
daemon binds its port on the tailnet IP directly for the plain-HTTP fallback (same-port Serve
would collide). The phone must resolve the `.ts.net` name over MagicDNS ("Use Tailscale DNS" on;
Android Private DNS off/automatic); the cert is hostname-bound so the raw 100.x IP can't do
HTTPS. If mobile voice breaks, first confirm a daemon is listening on the real config port and
`tailscale serve status` proxies to it. Tailscale/Serve code: `tailscale.py`, `__main__.py`.
