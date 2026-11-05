"""Consolidated read-only diagnostics report assembled from existing endpoints.

`swemux doctor` used to be a bare alias for ``GET /api/remote/status``. This module
turns it into one structured report over the diagnostics the daemon already
serves (`/api/health`, `/api/remote/status`, `/api/remote/firewall`,
`/api/diagnostics/{prerequisites,status-health,background}`) plus the one class of
fault nothing else exposes: **observation freshness**, where an agent session is
reporting a dead or relocated conversation's status while looking perfectly
healthy from the outside (Phase 5.4, `design/features/status-detection.md`).

The assembly is deliberately pure. `build_doctor_report` takes the already-fetched
diagnostic payloads and returns a structured report, so the daemon handler is a
thin gatherer and the whole shape is unit-testable with stubs. Nothing here reads
a secret, a terminal byte, or message content: every input is a source that is
already sanitized for a first-connect report, and the freshness rows carry only a
session's own id, reason, and age.

Each check carries a ``severity`` that separates an unavailable *optional* feature
(a harness not installed, Tailscale logged out) from a *safety-critical* failure
that compromises terminal ownership, cleanup, or delivery (a lost supervisor, a
dead background loop, a stale observation blocking delivery). A consumer ranks by
severity; the CLI colours and exit-codes by it.
"""

from __future__ import annotations

from typing import Any

from .harness import AGENT_BACKENDS
from .ui_messages import ui_message

# Check status: whether this check passed.
#   ok          - healthy
#   warn        - degraded, worth attention, not blocking
#   fail        - broken; for a critical check this compromises safety
#   unavailable - an optional capability is simply not configured/installed
#   unchecked   - the check did not run, so nothing is known either way
#
# `unchecked` is never produced here: this builder only runs when the daemon
# answered, and then every check has an answer. It belongs to the vocabulary
# rather than to the local report alone because the two reports share a renderer
# and a summary shape, and because the distinction it draws is the one a
# consumer must not collapse - "not measured" is neither healthy nor absent
# (`doctor_local`, which is where such rows come from).
Status = str
# Severity: how much a non-ok result matters.
#   critical - compromises terminal ownership, cleanup, or delivery safety
#   optional - an optional feature is unavailable; nothing is broken
#   info     - purely informational
Severity = str

DOCTOR_REPORT_VERSION = 1

# Freshness reasons that hard-block delivery: the session is reporting a
# conversation it can no longer prove is live, so a queued message could land in
# the wrong place or vanish. These are safety-critical, not cosmetic.
_FRESHNESS_DELIVERY_BLOCKING = {
    "transcript_missing",
    "conversation_owned_elsewhere",
    "explicit_conversation_mismatch",
    "rollover_adoption_failed",
}


def _check(
    *,
    id: str,
    category: str,
    title: str,
    status: Status,
    severity: Severity,
    detail: str,
    remedy: str | None = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "category": category,
        "title": title,
        "status": status,
        "severity": severity,
        "detail": detail,
        "remedy": remedy,
    }


def observation_freshness(sessions: Any, *, now: float) -> list[dict[str, Any]]:
    """Agent sessions whose observation the daemon can no longer trust.

    A stale observation is the fault class that presents as a perfectly healthy
    session: the daemon is silent, the dot is green, and delivery is nonetheless
    blocked because the followed transcript went quiet, moved, or the CLI rolled
    onto a conversation a live sibling already owns. This reads the same
    ``observation_stale_since`` / ``observation_stale_reason`` /
    ``observation_diagnostic`` fields the per-session state-log exposes, filtered
    to agent backends, and returns one content-free row per affected session.
    """
    rows: list[dict[str, Any]] = []
    for session in sessions:
        record = getattr(session, "record", None)
        if record is None or record.backend not in AGENT_BACKENDS:
            continue
        stale_since = getattr(record, "observation_stale_since", None)
        if stale_since is None:
            continue
        reason = getattr(session, "observation_stale_reason", None)
        rows.append(
            {
                "id": record.id,
                "name": record.name,
                "backend": record.backend,
                "reason": reason,
                "since": stale_since,
                "seconds_stale": round(max(0.0, now - stale_since), 1),
                # A path/owner-id diagnostic, exactly what the state-log serves.
                # It names files and sibling ids, never message or terminal bytes.
                "diagnostic": getattr(record, "observation_diagnostic", None),
                "delivery_blocking": reason in _FRESHNESS_DELIVERY_BLOCKING,
            }
        )
    rows.sort(key=lambda row: row["since"])
    return rows


#: How long after a spawn a silent hook channel stops being "not yet" and starts
#: being "never". SessionStart fires as the CLI boots, so the honest bound is the
#: CLI's own startup: seconds on a warm machine, and the clean Windows 11 laptop
#: this check was written for took tens of seconds to do anything at all. Two
#: minutes is generous against that and still catches the fault inside one turn.
HOOK_SILENCE_GRACE_SECONDS = 120.0


def hook_ingress_silence(
    sessions: Any, *, now: float, instrumented: Any = None
) -> list[dict[str, Any]]:
    """Instrumented agent sessions this daemon spawned that never reported a hook.

    The fault this exists for is total and silent. swe-mux writes Claude Code's
    hook `command` as a path string, Claude Code decides for itself which shell
    runs it, and when the two disagree every one of the eleven events dies inside
    the CLI: status detection, history, the prompt queue and approvals all stop,
    the session still runs, and nothing in mux says a word. It shipped that way on
    every Windows machine without Git Bash and was found by a tester reading the
    CLI's stderr, which is not a diagnostic channel this project can rely on.

    Deliberately narrow, because a false positive here would fire on every healthy
    session after every daemon reload. Three conditions must all hold: the session
    is an *agent* on a harness this install instruments (a "launch clean" session
    is supposed to be silent); **this daemon spawned it**, so a working channel was
    obliged to speak (`hook_channel_watch_since`, never set on an adopted or
    restored session, whose in-memory counter the restart merely reset); and the
    grace period has passed. ``instrumented`` is a predicate over the backend name,
    defaulting to "all of them" so the pure builder needs no config.
    """
    rows: list[dict[str, Any]] = []
    for session in sessions:
        record = getattr(session, "record", None)
        if record is None or record.backend not in AGENT_BACKENDS:
            continue
        if instrumented is not None and not instrumented(record.backend):
            continue
        since = getattr(session, "hook_channel_watch_since", None)
        if since is None:
            continue
        if getattr(session, "last_hook_ts", 0.0):
            continue
        silent_for = now - since
        if silent_for < HOOK_SILENCE_GRACE_SECONDS:
            continue
        rows.append(
            {
                "id": record.id,
                "name": record.name,
                "backend": record.backend,
                "since": since,
                "seconds_silent": round(silent_for, 1),
            }
        )
    rows.sort(key=lambda row: row["since"])
    return rows


def _hook_ingress_checks(silent: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not silent:
        return [
            _check(
                id="hook_ingress.ok",
                category="hook_ingress",
                title=ui_message("doctor.635fae028b"),
                status="ok",
                severity="critical",
                detail=ui_message("doctor.c630752e62"),
            )
        ]
    names = ", ".join(f"{row['name']} ({row['backend']})" for row in silent[:5])
    return [
        _check(
            id="hook_ingress.silent",
            category="hook_ingress",
            title=ui_message("doctor.635fae028b"),
            status="fail",
            severity="critical",
            detail=(ui_message("doctor.be7c6ecbba", value1=len(silent), value2=names)),
            remedy=(
                "Check the agent CLI's own stderr for a failure running the hook "
                "command. On Windows the usual cause is the interpreter path being "
                "unrunnable in the shell the CLI chose; `swemux doctor --export` "
                "carries the generated command."
            ),
        )
    ]


def _daemon_checks(health: dict[str, Any], daemon: dict[str, Any]) -> list[dict[str, Any]]:
    checks = [
        _check(
            id="daemon.reachable",
            category="daemon",
            title=ui_message("doctor.148d9a5fa1"),
            status="ok" if health.get("ok") else "warn",
            severity="critical",
            detail=(
                ui_message(
                    "doctor.f173c5412b",
                    value1=health.get("version", "?"),
                    value2=daemon.get("host"),
                    value3=daemon.get("port"),
                    value4=health.get("live_sessions", 0),
                )
            ),
        )
    ]
    state = health.get("supervisor_state")
    if state == "connected":
        checks.append(
            _check(
                id="daemon.supervisor",
                category="daemon",
                title=ui_message("doctor.6b0f16ab88"),
                status="ok",
                severity="critical",
                detail=ui_message("doctor.6d256fed3c"),
            )
        )
    elif state == "lost":
        checks.append(
            _check(
                id="daemon.supervisor",
                category="daemon",
                title=ui_message("doctor.6b0f16ab88"),
                status="fail",
                severity="critical",
                detail=ui_message("doctor.e7489ec61b"),
                remedy="The daemon re-attaches by itself; if it stays lost, restart "
                "the daemon (swemux reload-daemon), and if that does not re-attach, "
                "the supervisor process may need a manual restart.",
            )
        )
    else:  # absent, or an older snapshot with no field
        checks.append(
            _check(
                id="daemon.supervisor",
                category="daemon",
                title=ui_message("doctor.6b0f16ab88"),
                status="warn",
                severity="critical",
                detail=ui_message("doctor.11744d9ef9"),
                remedy="Read supervisor-console.log in the data directory for why "
                "the spawn failed, then restart the daemon. If pty_supervisor_enabled "
                "was deliberately turned off, turn it back on to keep sessions alive "
                "across reloads.",
            )
        )
    running_revision = health.get("supervisor_revision")
    shipped_revision = health.get("supervisor_revision_shipped")
    if (
        state == "connected"
        and isinstance(running_revision, int)
        and isinstance(shipped_revision, int)
        and running_revision < shipped_revision
    ):
        checks.append(
            _check(
                id="daemon.supervisor_revision",
                category="daemon",
                title=ui_message("doctor.6231de554c"),
                status="warn",
                severity="optional",
                detail=ui_message(
                    "doctor.962abe2661", value1=running_revision, value2=shipped_revision
                ),
                remedy="Replace it at a quiet moment: an installed copy offers it again "
                "with every update until it is taken (tick the supervisor box, or "
                "`swemux update --install <version> --with-supervisor-update`), and a "
                "source checkout follows the supervisor update steps in CLAUDE.md.",
            )
        )
    transport_check = _terminal_transport_check(health.get("terminal_transport"))
    if transport_check is not None:
        checks.append(transport_check)
    unadopted = int(health.get("supervisor_unadopted", 0) or 0)
    if unadopted:
        checks.append(
            _check(
                id="daemon.supervisor_unadopted",
                category="daemon",
                title=ui_message("doctor.e34baa1b58"),
                status="warn",
                severity="critical",
                detail=ui_message("doctor.f84255a4d1", value1=unadopted),
                remedy="Restart the daemon to re-adopt, or kill the orphaned "
                "sessions from the supervisor.",
            )
        )
    build_id = health.get("ui_build_id")
    checks.append(
        _check(
            id="daemon.frontend",
            category="daemon",
            title=ui_message("doctor.55c5f0ef1a"),
            status="ok" if build_id else "warn",
            severity="optional",
            detail=(
                f"UI build {str(build_id)[:12]} served."
                if build_id
                else "No production frontend identity is served; run the frontend "
                "build so the browser UI is available."
            ),
            remedy=None if build_id else "cd frontend && npm run build",
        )
    )
    return checks


def _terminal_transport_check(transport: Any) -> dict[str, Any] | None:
    """Which process carries the terminals (`gateway_host.py`), and whether that is wanted.

    Off is not a fault: the daemon serving terminals itself is how every install
    worked before the gateway, and it stays the fallback. Enabled and not running is
    worth a warning, because terminals then pause whenever the daemon does - the
    exact thing the gateway is for - while everything else looks fine.
    """
    if not isinstance(transport, dict):
        return None  # a daemon from before the gateway, which reports nothing
    state = str(transport.get("state") or "")
    if transport.get("transport") == "gateway":
        return _check(
            id="daemon.terminal_transport",
            category="daemon",
            title=ui_message("doctor.25e571822b"),
            status="ok",
            severity="optional",
            detail=ui_message("doctor.12a3edcb32", value1=transport.get("port")),
        )
    if state == "disabled":
        return _check(
            id="daemon.terminal_transport",
            category="daemon",
            title=ui_message("doctor.25e571822b"),
            status="ok",
            severity="info",
            detail=ui_message("doctor.c2d7b5ed3d"),
        )
    reason = str(transport.get("reason") or "")
    return _check(
        id="daemon.terminal_transport",
        category="daemon",
        title=ui_message("doctor.25e571822b"),
        status="warn",
        severity="optional",
        detail=ui_message(
            "doctor.c26e069749", value1=state or "unknown", value2=": " + reason if reason else ""
        ),
        remedy="Read terminal-gateway.log and terminal-gateway-console.log in the data "
        "directory; the daemon retries by itself with backoff, and "
        "/api/diagnostics/background carries the `terminal_gateway` block.",
    )


def _harness_checks(harnesses: dict[str, Any]) -> list[dict[str, Any]]:
    entries = harnesses.get("harnesses") or []
    checks: list[dict[str, Any]] = []
    installed_any = False
    for entry in entries:
        name = entry.get("name")
        label = entry.get("display_name") or name
        installed = entry.get("installed")
        if installed:
            installed_any = True
            version = entry.get("cli_version")
            untested = entry.get("version_untested")
            if untested:
                checks.append(
                    _check(
                        id=f"harness.{name}",
                        category="harness",
                        title=ui_message("doctor.e1a3551b59", value1=label),
                        status="warn",
                        severity="info",
                        detail=ui_message(
                            "doctor.30ed499648",
                            value1=label,
                            value2=version,
                            value3=entry.get("tested_cli_version"),
                        ),
                        remedy="Confirm the harness still detects and drives "
                        "correctly, or pin a tested CLI version.",
                    )
                )
            else:
                checks.append(
                    _check(
                        id=f"harness.{name}",
                        category="harness",
                        title=ui_message("doctor.e1a3551b59", value1=label),
                        status="ok",
                        severity="info",
                        detail=(
                            f"{label} {version} detected at {entry.get('resolved_path')}."
                            if version
                            else f"{label} detected at {entry.get('resolved_path')}."
                        ),
                    )
                )
        elif installed is False:
            checks.append(
                _check(
                    id=f"harness.{name}",
                    category="harness",
                    title=ui_message("doctor.e1a3551b59", value1=label),
                    status="unavailable",
                    severity="optional",
                    detail=ui_message("doctor.a4b61f61b2", value1=label),
                    remedy=f"Install the {label} CLI to use this harness.",
                )
            )
        # installed is None means detection was not supplied; skip silently.
    if entries and not installed_any and all(e.get("installed") is False for e in entries):
        checks.append(
            _check(
                id="harness.none",
                category="harness",
                title=ui_message("doctor.bff64c564d"),
                status="warn",
                severity="optional",
                detail=ui_message("doctor.aa90ade19d"),
                remedy="Install at least one agent CLI (for example the Claude or Codex CLI).",
            )
        )
    return checks


def _remote_checks(remote: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if not remote.get("tailnet_enabled", True):
        checks.append(
            _check(
                id="remote.tailnet",
                category="remote",
                title=ui_message("doctor.668c7def66"),
                status="unavailable",
                severity="optional",
                detail=ui_message("doctor.20b379e9fa"),
                remedy="Enable the Tailscale listener in Settings to reach this "
                "daemon from other tailnet devices.",
            )
        )
        return checks
    state = remote.get("connection_state")
    detail = str(remote.get("connection_detail") or "")
    if state == "connected":
        checks.append(
            _check(
                id="remote.connection",
                category="remote",
                title=ui_message("doctor.403f22f8e9"),
                status="ok",
                severity="optional",
                detail=detail or f"Connected as {remote.get('device_name')}.",
            )
        )
    elif state == "not_installed":
        checks.append(
            _check(
                id="remote.connection",
                category="remote",
                title=ui_message("doctor.403f22f8e9"),
                status="unavailable",
                severity="optional",
                detail=detail
                or "Tailscale is not installed; remote and mobile access are unavailable.",
                remedy=str(remote.get("connection_command") or "Install Tailscale."),
            )
        )
    else:  # logged_out, stopped, connecting, needs_machine_auth, unknown
        checks.append(
            _check(
                id="remote.connection",
                category="remote",
                title=ui_message("doctor.403f22f8e9"),
                status="warn",
                severity="optional",
                detail=detail or f"Tailscale is not connected (state: {state}).",
                remedy=str(remote.get("connection_command") or "tailscale up"),
            )
        )
    # Mobile voice needs Serve on 443 for a secure context. Informational: the
    # phone-side DNS requirement and Serve state are surfaced, not enforced.
    serve = remote.get("serve_configured") or remote.get("mobile_voice_configured")
    checks.append(
        _check(
            id="remote.serve",
            category="remote",
            title=ui_message("doctor.1938498449"),
            status="ok" if serve else "unavailable",
            severity="optional",
            detail=(
                "Serve proxies to swe-mux ("
                f"{remote.get('mobile_voice_url') or remote.get('serve_url')})."
                if serve
                else "Tailscale Serve is not configured; mobile microphone (a secure "
                "context) is unavailable until it is."
            ),
            remedy=None if serve else str(remote.get("setup_command") or ""),
        )
    )
    return checks


def _firewall_checks(firewall: dict[str, Any]) -> list[dict[str, Any]]:
    if not firewall.get("supported"):
        # Off Windows / off a frozen build: the host firewall governs inbound and
        # the reachability guidance covers it. Not a failure.
        return [
            _check(
                id="firewall.inbound",
                category="firewall",
                title=ui_message("doctor.335ef137f5"),
                status="unavailable",
                severity="info",
                detail=ui_message("doctor.69374b4e53"),
            )
        ]
    if not firewall.get("inspection_available"):
        return [
            _check(
                id="firewall.inbound",
                category="firewall",
                title=ui_message("doctor.335ef137f5"),
                status="warn",
                severity="optional",
                detail=str(firewall.get("detail") or "Firewall could not be inspected."),
                remedy="Run the firewall repair from Settings -> Remote if a phone cannot connect.",
            )
        ]
    if firewall.get("needs_repair"):
        return [
            _check(
                id="firewall.inbound",
                category="firewall",
                title=ui_message("doctor.335ef137f5"),
                status="fail",
                severity="critical",
                detail=str(firewall.get("detail"))
                or "The inbound rule for the direct tailnet socket is missing or "
                "blocking; a phone cannot connect over the 100.x address.",
                remedy="Run the one-click firewall repair from Settings -> Remote "
                "(adds an inbound Allow rule).",
            )
        ]
    return [
        _check(
            id="firewall.inbound",
            category="firewall",
            title=ui_message("doctor.335ef137f5"),
            status="ok",
            severity="optional",
            detail=str(firewall.get("detail") or "Inbound phone connections are admitted."),
        )
    ]


def _prerequisite_checks(prerequisites: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for item in prerequisites:
        present = item.get("present")
        label = str(item.get("label") or item.get("id"))
        checks.append(
            _check(
                id=f"prereq.{item.get('id')}",
                category="prerequisites",
                title=label,
                status="ok" if present else "unavailable",
                severity="optional",
                detail=(
                    f"{label} found at {item.get('path')}."
                    if present
                    else f"{label} is not installed. {item.get('purpose', '')}".strip()
                ),
                remedy=None if present else str(item.get("install_command") or ""),
            )
        )
    return checks


def _status_checks(status_health: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    collisions = status_health.get("identity_collisions") or []
    if collisions:
        checks.append(
            _check(
                id="status.identity_collisions",
                category="status",
                title=ui_message("doctor.569523ec87"),
                status="fail",
                severity="critical",
                detail=ui_message("doctor.ed22b64d05", value1=len(collisions)),
                remedy="Inspect the colliding sessions and end the duplicate; see "
                "STATUS_INCIDENT_RUNBOOK.md.",
            )
        )
    blind = status_health.get("classifier_blind_sessions") or []
    if blind:
        checks.append(
            _check(
                id="status.classifier_blind",
                category="status",
                title=ui_message("doctor.44569229d9"),
                status="warn",
                severity="critical",
                detail=ui_message("doctor.93ce401878", value1=len(blind)),
                remedy="Inspect the session's state-log; a persistent blind classifier "
                "hides approvals and completions.",
            )
        )
    stuck = status_health.get("stuck_sessions") or []
    if stuck:
        checks.append(
            _check(
                id="status.stuck",
                category="status",
                title=ui_message("doctor.049878ec2e"),
                status="warn",
                severity="critical",
                detail=ui_message("doctor.5ee2689069", value1=len(stuck)),
                remedy="Inspect the session's state-log for the last layer reading.",
            )
        )
    if status_health.get("alarm") and not (collisions or blind or stuck):
        checks.append(
            _check(
                id="status.alarm",
                category="status",
                title=ui_message("doctor.70e3fe24e3"),
                status="fail",
                severity="critical",
                detail="Status-health alarm raised: "
                + ", ".join(status_health.get("alarm_reasons") or ["unknown"]),
                remedy="See STATUS_INCIDENT_RUNBOOK.md.",
            )
        )
    if not checks:
        checks.append(
            _check(
                id="status.ok",
                category="status",
                title=ui_message("doctor.cdd078f783"),
                status="ok",
                severity="critical",
                detail=ui_message("doctor.db46953fc8"),
            )
        )
    return checks


def _background_checks(background: dict[str, Any]) -> list[dict[str, Any]]:
    degraded = background.get("degraded") or []
    if degraded:
        return [
            _check(
                id="background.loops",
                category="background",
                title=ui_message("doctor.218ba0b7d8"),
                status="fail",
                severity="critical",
                detail=ui_message("doctor.0b9152bb69", value1=", ".join(map(str, degraded))),
                remedy="Restart the daemon; if a loop stays degraded, inspect "
                "daemon.log for its fault.",
            )
        ]
    faults = int(background.get("total_faults", 0) or 0)
    if faults:
        return [
            _check(
                id="background.loops",
                category="background",
                title=ui_message("doctor.218ba0b7d8"),
                status="warn",
                severity="critical",
                detail=ui_message("doctor.2255928e03", value1=faults),
                remedy="Inspect daemon.log if faults recur.",
            )
        ]
    return [
        _check(
            id="background.loops",
            category="background",
            title=ui_message("doctor.218ba0b7d8"),
            status="ok",
            severity="critical",
            detail=ui_message("doctor.ce9affbc0d"),
        )
    ]


def _stall_profiler_checks(profiler: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Whether a stall's explanation can name the thread holding the GIL.

    Per-thread CPU attribution is built in; the external `py-spy` dump is optional
    and is the only thing that sees a GIL holder inside a native call. Absent, a
    stall is still classified, so this is advice rather than a fault.
    """
    if profiler is None:
        return []
    external = profiler.get("external_dump") or {}
    thread_cpu = profiler.get("thread_cpu")
    if thread_cpu:
        cpu_text = "Per-thread CPU attribution is on."
    elif thread_cpu is None:
        cpu_text = "Per-thread CPU attribution has not started sampling yet."
    else:
        cpu_text = "Per-thread CPU attribution is unavailable on this host."
    env = str(external.get("env") or "MUX_STALL_PROFILER")
    if external.get("available"):
        return [
            _check(
                id="background.stall_profiler",
                category="background",
                title=ui_message("doctor.38c33f5e84"),
                status="ok",
                severity="info",
                detail=ui_message(
                    "doctor.c1394ea7cb",
                    value1=cpu_text,
                    value2=external.get("executable"),
                    value3=external.get("source"),
                ),
            )
        ]
    source = str(external.get("source") or "")
    reason = {
        "off": f"disabled by {env}=off",
        "env_not_found": f"{env} names an executable that does not exist",
        "not_found": "py-spy is not on PATH",
    }.get(source, "not configured")
    return [
        _check(
            id="background.stall_profiler",
            category="background",
            title=ui_message("doctor.38c33f5e84"),
            status="unavailable",
            severity="info",
            detail=ui_message("doctor.57408264a7", value1=cpu_text, value2=reason),
            remedy=f"`uv tool install py-spy` (or set {env} to its path), then restart the daemon.",
        )
    ]


def _freshness_checks(freshness: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not freshness:
        return [
            _check(
                id="freshness.ok",
                category="freshness",
                title=ui_message("doctor.523e3730b0"),
                status="ok",
                severity="critical",
                detail=ui_message("doctor.484d9d40ed"),
            )
        ]
    blocking = [row for row in freshness if row.get("delivery_blocking")]
    status: Status = "fail" if blocking else "warn"
    names = ", ".join(f"{row['name']} ({row['reason']})" for row in freshness[:5])
    return [
        _check(
            id="freshness.stale",
            category="freshness",
            title=ui_message("doctor.523e3730b0"),
            status=status,
            severity="critical",
            detail=f"{len(freshness)} agent session(s) with an untrusted observation: "
            f"{names}. These look healthy but the daemon cannot prove the followed "
            "conversation is live" + (" and delivery is blocked." if blocking else "."),
            remedy="Inspect GET /api/sessions/{id}/state-log; the session may need a "
            "fresh turn or a re-attach to re-bind its transcript.",
        )
    ]


def _wsl_bridge_checks(bridges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per WSL distribution the bridge was asked about.

    Reported at all because the bridge's failure mode is silent by construction:
    an agent inside a distribution starts perfectly, runs perfectly, and simply
    never reports - no hooks, no transcript link, no status. There is nothing for
    a user to notice, so the only place the truth can appear is a diagnostic that
    goes looking.

    Severity is `optional` throughout: a host that has not opted into the bridge is
    not broken, and neither is one whose distributions have no agent installed. What
    the rows separate is *why* it is not working, because the three answers have
    three different next actions - turn it on, install the bridge, or fix
    reachability - and collapsing them into one "unavailable" tells a reader
    nothing they can act on.
    """
    checks: list[dict[str, Any]] = []
    for bridge in bridges:
        distro = str(bridge.get("distro") or "?")
        reasons = [str(item) for item in (bridge.get("reasons") or [])]
        if bridge.get("enabled") is False:
            # The feature is off, which is the default and not a fault. Reported as
            # `info` rather than `unavailable` so it reads as an offer rather than a
            # problem - and reported at all because a user with WSL and an agent in
            # it has no other way to learn the bridge exists.
            checks.append(
                _check(
                    id=f"wsl_bridge:{distro}",
                    category="wsl",
                    title=ui_message("doctor.eb9282a2f9", value1=distro),
                    status="ok",
                    severity="info",
                    detail="; ".join(reasons)
                    or f"the WSL agent bridge is available for {distro} and is switched off",
                    remedy="enable the WSL agent bridge in Settings",
                )
            )
            continue
        if bridge.get("available") and bridge.get("installed"):
            checks.append(
                _check(
                    id=f"wsl_bridge:{distro}",
                    category="wsl",
                    title=ui_message("doctor.eb9282a2f9", value1=distro),
                    status="ok",
                    severity="optional",
                    detail=(
                        f"{distro} can host a bridged agent: "
                        + ", ".join(
                            str(item.get("name")) for item in (bridge.get("harnesses") or [])
                        )
                    ),
                )
            )
            continue
        remedy = None
        if bridge.get("available") and not bridge.get("installed"):
            remedy = f"install the distro-side bridge into {distro}"
        elif reasons:
            remedy = reasons[0]
        checks.append(
            _check(
                id=f"wsl_bridge:{distro}",
                category="wsl",
                title=ui_message("doctor.eb9282a2f9", value1=distro),
                status="unavailable",
                severity="optional",
                detail="; ".join(reasons) or f"the bridge is not usable for {distro}",
                remedy=remedy,
            )
        )
    return checks


def optional_asset_rows(
    *, capture: dict[str, Any], voice: dict[str, Any], voice_local_install: str
) -> list[dict[str, Any]]:
    """Normalize the first-use assets into one row shape, keeping their own states.

    Pure, like the rest of this module: the caller has already probed. The point
    of the shared shape is that a consumer can render "which kind of absent" for
    every optional asset without knowing what a Playwright browsers root or a
    Hugging Face cache is, while `state` stays each subsystem's own vocabulary
    rather than being flattened into a boolean.

    Voice rows say when a model is unused *as well as* absent: with `tts_enabled`
    and `stt_enabled` both shipping false, an untouched install has downloaded
    nothing, and reporting that as a missing capability would invent a problem.

    Since 2026-08-29 there are four voice rows rather than three, and the new one
    is first because it is a prerequisite of the other three. The desktop bundle
    stopped carrying the speech *libraries* (ROADMAP Phase 21 Workstream D), so
    "the Kokoro weights are missing" and "there is no engine to load them into"
    are now different states with different remedies, and a report that showed
    only the first would send a reader to press a button that cannot help.

    `voice_local_install` is passed in rather than computed here, and that is what
    keeps this function pure while still being right. The command that installs an
    extra depends on how swe-mux was installed - `uv sync --extra voice-local` is
    a source-checkout command that a PyPI user cannot run at all - so the answer
    comes from `install_location.extra_install_command`, which reads the
    filesystem. Both callers already know their own installation.
    """
    rows: list[dict[str, Any]] = [
        {
            "id": "preview_capture",
            "label": ui_message("doctor.4592a077ff"),
            "state": capture.get("state"),
            "detail": capture.get("detail"),
            "remedy": capture.get("remedy"),
        }
    ]
    runtime = dict(voice.get("runtime") or {})
    if runtime:
        # First in the voice group, because it gates the other three: without the
        # speech libraries there is nothing for any of those weights to load into.
        # A reader who sees three "not downloaded" model rows and no explanation
        # would go and press three buttons that cannot help.
        used = bool(voice.get("tts_enabled")) or bool(voice.get("stt_enabled"))
        megabytes = float(runtime.get("total_bytes") or 0) / 1024 / 1024
        rows.append(
            {
                "id": "voice_runtime",
                "label": ui_message("doctor.4c16c922bf"),
                "state": "unsupported" if not runtime.get("supported") else runtime.get("status"),
                "detail": (
                    f"the on-device speech libraries are present ({runtime.get('source')})"
                    if runtime.get("status") == "ready"
                    else str(runtime.get("error") or "")
                    if not runtime.get("supported")
                    else _asset_detail(
                        str(runtime.get("status") or ""),
                        f"the on-device speech libraries ({megabytes:.0f} MB)",
                        used=used,
                        unused_note="read aloud and hands-free conversation are both off",
                        error=runtime.get("error"),
                    )
                ),
                "remedy": None
                if runtime.get("status") == "ready"
                else voice_local_install
                if not runtime.get("supported")
                else "Settings -> Voice -> Download speech libraries",
            }
        )
    kokoro = dict(voice.get("kokoro") or {})
    if kokoro:
        used = bool(voice.get("tts_enabled")) and voice.get("tts_engine") == "kokoro"
        rows.append(
            {
                "id": "voice_kokoro",
                "label": ui_message("doctor.149d4323ea"),
                "state": kokoro.get("status"),
                "detail": _asset_detail(
                    str(kokoro.get("status") or ""),
                    "the Kokoro voice model",
                    used=used,
                    unused_note="read aloud is off or set to another engine",
                    error=kokoro.get("error"),
                ),
                "remedy": None
                if kokoro.get("status") == "ready"
                else "Settings -> Voice -> Download Kokoro voices",
            }
        )
    g2p = dict(kokoro.get("g2p") or {})
    if g2p:
        # Its own row rather than a clause on the Kokoro one, because its two
        # ready states have different causes and only one of them is about this
        # data directory: `installed` means the environment resolves the
        # distribution (a source checkout, the desktop bundle), `downloaded`
        # means this daemon fetched it. A reader chasing a broken voice needs to
        # know which, and a merged row could not say.
        used = bool(voice.get("tts_enabled")) and voice.get("tts_engine") == "kokoro"
        source = str(g2p.get("source") or "")
        rows.append(
            {
                "id": "voice_g2p",
                "label": ui_message("doctor.d9a50250a9"),
                "state": g2p.get("status"),
                "detail": (
                    f"the spaCy English model is present ({source})"
                    if g2p.get("status") == "ready"
                    else _asset_detail(
                        str(g2p.get("status") or ""),
                        "the spaCy English model the Kokoro G2P needs",
                        used=used,
                        unused_note="read aloud is off or set to another engine",
                        error=g2p.get("error"),
                    )
                ),
                "remedy": None
                if g2p.get("status") == "ready"
                else "Settings -> Voice -> Download Kokoro voices (it comes with them)",
            }
        )
    stt_used = bool(voice.get("stt_enabled")) and voice.get("stt_engine") != "sapi"
    for model in voice.get("whisper") or []:
        name = str(model.get("model") or "")
        size = f" ({model['size_hint']})" if model.get("size_hint") else ""
        rows.append(
            {
                "id": f"voice_whisper:{name}",
                "label": ui_message("doctor.f44c73cb76", value1=name),
                "state": "extra_missing"
                if not model.get("backend_installed")
                else model.get("status"),
                "detail": (
                    "faster-whisper is not installed, so no local dictation model can be used"
                    if not model.get("backend_installed")
                    else _asset_detail(
                        str(model.get("status") or ""),
                        f"the '{name}' speech model{size}",
                        used=stt_used,
                        unused_note="hands-free conversation is off or set to the OS engine",
                        error=model.get("error"),
                    )
                ),
                "remedy": None
                if model.get("status") == "ready" and model.get("backend_installed")
                else voice_local_install
                if not model.get("backend_installed")
                else "Settings -> Voice -> Download speech model",
            }
        )
    return rows


def _asset_detail(
    state: str, subject: str, *, used: bool, unused_note: str, error: Any = None
) -> str:
    if state == "ready":
        return f"{subject} is downloaded"
    if state == "downloading":
        return f"{subject} is downloading now"
    if state == "error":
        return f"{subject} failed to download: {error}"
    suffix = "" if used else f" ({unused_note}, so nothing has fetched it)"
    return f"{subject} has never been downloaded{suffix}"


def _optional_asset_checks(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """First-use assets that are installed or downloaded on demand, never bundled.

    Every one of these is absent on a clean machine and each has a *different*
    kind of absence with a different command behind it: an optional Python extra
    that was never installed, a browser binary the extra does not carry, a model
    file that has never been downloaded. Collapsing them into one "unavailable"
    is what made a fresh install fail oddly instead of saying what to do, so the
    state is reported verbatim (`state`) beside the human sentence, and every
    non-ready row carries its own remedy rather than a shared install hint.

    None of these rows is a fault: severity is `optional` throughout, including
    `error`, because a failed download of a feature that is off by default breaks
    nothing that was working. `downloading` is `warn` rather than `ok` only so it
    is visibly not finished.
    """
    checks: list[dict[str, Any]] = []
    for asset in assets:
        state = str(asset.get("state") or "unknown")
        status = (
            "ok"
            if state == "ready"
            else "warn"
            if state in {"downloading", "error"}
            else "unavailable"
        )
        checks.append(
            _check(
                id=f"optional_asset:{asset.get('id')}",
                category="optional-assets",
                title=str(asset.get("label") or asset.get("id") or "optional asset"),
                status=status,
                severity="optional",
                detail=str(asset.get("detail") or state),
                remedy=str(asset.get("remedy")) if asset.get("remedy") else None,
            )
        )
    return checks


def _surface_checks(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per incoherent agent-surface combination (`agent_surfaces.py`).

    Advisory (`warn`), never `fail`: every combination is legal and an operator
    mid-reconfiguration passes through the incoherent ones - but a skill
    teaching commands that will fail, and a mute CLI capability nothing ever
    advertises, are both traps a report should name before an agent falls in.
    """
    return [
        _check(
            id=f"agent_surfaces.{warning.get('backend')}",
            category="agent-surfaces",
            title=ui_message("doctor.0d2100c4ed", value1=warning.get("backend")),
            status="warn",
            severity="optional",
            detail=str(warning.get("message") or warning.get("code") or ""),
            remedy="Settings -> Harnesses -> Fleet access",
        )
        for warning in warnings
    ]


def build_doctor_report(
    *,
    health: dict[str, Any],
    remote: dict[str, Any],
    firewall: dict[str, Any],
    prerequisites: list[dict[str, Any]],
    status_health: dict[str, Any],
    background: dict[str, Any],
    harnesses: dict[str, Any],
    freshness: list[dict[str, Any]],
    platform: dict[str, Any],
    daemon: dict[str, Any],
    now: float,
    wsl_bridges: list[dict[str, Any]] | None = None,
    optional_assets: list[dict[str, Any]] | None = None,
    hook_silence: list[dict[str, Any]] | None = None,
    surface_warnings: list[dict[str, Any]] | None = None,
    stall_profiler: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the consolidated doctor report from already-fetched diagnostics.

    Pure: every input is a payload the daemon already produced, and the output is
    a machine-readable report with a flat ``checks`` list, a ``capabilities``
    block (versions and feature availability), and a ``summary`` count. Nothing
    here fetches or mutates. ``ok`` is false when any check failed.
    """
    checks: list[dict[str, Any]] = []
    checks += _daemon_checks(health, daemon)
    checks += _harness_checks(harnesses)
    checks += _remote_checks(remote)
    checks += _firewall_checks(firewall)
    checks += _prerequisite_checks(prerequisites)
    checks += _status_checks(status_health)
    checks += _background_checks(background)
    checks += _stall_profiler_checks(stall_profiler)
    checks += _freshness_checks(freshness)
    checks += _wsl_bridge_checks(wsl_bridges or [])
    checks += _optional_asset_checks(optional_assets or [])
    checks += _hook_ingress_checks(hook_silence or [])
    checks += _surface_checks(surface_warnings or [])

    summary = {"ok": 0, "warn": 0, "fail": 0, "unavailable": 0}
    for check in checks:
        summary[check["status"]] = summary.get(check["status"], 0) + 1

    return {
        "version": DOCTOR_REPORT_VERSION,
        "generated_at": now,
        "ok": summary["fail"] == 0,
        "summary": summary,
        "capabilities": _capabilities(
            health, platform, daemon, remote, firewall, harnesses, optional_assets or []
        ),
        "checks": checks,
        "observation_freshness": freshness,
        "hook_ingress_silence": hook_silence or [],
    }


def _capabilities(
    health: dict[str, Any],
    platform: dict[str, Any],
    daemon: dict[str, Any],
    remote: dict[str, Any],
    firewall: dict[str, Any],
    harnesses: dict[str, Any],
    optional_assets: list[dict[str, Any]],
) -> dict[str, Any]:
    """Machine-readable capability/version block, free of secrets and bytes."""
    return {
        # Each optional asset keeps its own `state` string here rather than a
        # boolean: "not installed", "installed but no browser", and "never
        # downloaded" are different facts, and a consumer that only sees false
        # cannot tell an operator which command to run.
        "optional_assets": [
            {
                "id": asset.get("id"),
                "label": asset.get("label"),
                "state": asset.get("state"),
                "remedy": asset.get("remedy"),
            }
            for asset in optional_assets
        ],
        "swe_mux_version": health.get("version"),
        "ui_build_id": health.get("ui_build_id"),
        "supervisor_state": health.get("supervisor_state"),
        "platform": platform,
        "daemon": {
            "host": daemon.get("host"),
            "port": daemon.get("port"),
            "live_sessions": health.get("live_sessions"),
        },
        "harnesses": [
            {
                "name": entry.get("name"),
                "display_name": entry.get("display_name"),
                "installed": entry.get("installed"),
                "cli_version": entry.get("cli_version"),
                "version_untested": entry.get("version_untested"),
                "level": entry.get("level"),
                "capabilities": entry.get("capabilities"),
            }
            for entry in (harnesses.get("harnesses") or [])
        ],
        "remote": {
            "connection_state": remote.get("connection_state"),
            "device_name": remote.get("device_name"),
            "tailnet_enabled": remote.get("tailnet_enabled"),
            "serve_configured": bool(
                remote.get("serve_configured") or remote.get("mobile_voice_configured")
            ),
        },
        "firewall": {
            "supported": firewall.get("supported"),
            "needs_repair": firewall.get("needs_repair"),
        },
    }
