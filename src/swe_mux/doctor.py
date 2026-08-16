"""Consolidated read-only diagnostics report assembled from existing endpoints.

`mux doctor` used to be a bare alias for ``GET /api/remote/status``. This module
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

# Check status: whether this check passed.
#   ok          - healthy
#   warn        - degraded, worth attention, not blocking
#   fail        - broken; for a critical check this compromises safety
#   unavailable - an optional capability is simply not configured/installed
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


def _daemon_checks(health: dict[str, Any], daemon: dict[str, Any]) -> list[dict[str, Any]]:
    checks = [
        _check(
            id="daemon.reachable",
            category="daemon",
            title="Daemon reachable",
            status="ok" if health.get("ok") else "warn",
            severity="critical",
            detail=(
                f"swe-mux {health.get('version', '?')} on "
                f"{daemon.get('host')}:{daemon.get('port')}, "
                f"{health.get('live_sessions', 0)} live session(s)."
            ),
        )
    ]
    state = health.get("supervisor_state")
    if state == "connected":
        checks.append(
            _check(
                id="daemon.supervisor",
                category="daemon",
                title="PTY supervisor",
                status="ok",
                severity="critical",
                detail="Supervisor connected; sessions survive a daemon restart.",
            )
        )
    elif state == "lost":
        checks.append(
            _check(
                id="daemon.supervisor",
                category="daemon",
                title="PTY supervisor",
                status="fail",
                severity="critical",
                detail="Supervisor is alive but unreachable from this daemon; "
                "live sessions are running and cannot be controlled from here.",
                remedy="Restart the daemon (mux reload-daemon) to re-attach; if it "
                "stays lost, the supervisor process may need a manual restart.",
            )
        )
    else:  # absent, or an older snapshot with no field
        checks.append(
            _check(
                id="daemon.supervisor",
                category="daemon",
                title="PTY supervisor",
                status="warn",
                severity="critical",
                detail="No PTY supervisor attached; sessions will not survive a "
                "daemon restart or app rebuild.",
                remedy="Enable the session-preserving supervisor so reloads keep "
                "sessions alive.",
            )
        )
    unadopted = int(health.get("supervisor_unadopted", 0) or 0)
    if unadopted:
        checks.append(
            _check(
                id="daemon.supervisor_unadopted",
                category="daemon",
                title="Unadopted supervised sessions",
                status="warn",
                severity="critical",
                detail=f"{unadopted} supervised session(s) are running with no UI "
                "handle; they cannot be shown or killed from here.",
                remedy="Restart the daemon to re-adopt, or kill the orphaned "
                "sessions from the supervisor.",
            )
        )
    build_id = health.get("ui_build_id")
    checks.append(
        _check(
            id="daemon.frontend",
            category="daemon",
            title="Frontend build",
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
                        title=f"{label} CLI",
                        status="warn",
                        severity="info",
                        detail=f"{label} {version} is newer than the version mux was "
                        f"tested against ({entry.get('tested_cli_version')}); "
                        "behaviour is best-effort.",
                        remedy="Confirm the harness still detects and drives "
                        "correctly, or pin a tested CLI version.",
                    )
                )
            else:
                checks.append(
                    _check(
                        id=f"harness.{name}",
                        category="harness",
                        title=f"{label} CLI",
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
                    title=f"{label} CLI",
                    status="unavailable",
                    severity="optional",
                    detail=f"{label} is not installed; sessions on this harness "
                    "cannot be spawned.",
                    remedy=f"Install the {label} CLI to use this harness.",
                )
            )
        # installed is None means detection was not supplied; skip silently.
    if entries and not installed_any and all(e.get("installed") is False for e in entries):
        checks.append(
            _check(
                id="harness.none",
                category="harness",
                title="Agent harnesses",
                status="warn",
                severity="optional",
                detail="No agent harness is installed; only shell sessions can be "
                "spawned.",
                remedy="Install at least one agent CLI (for example the Claude or "
                "Codex CLI).",
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
                title="Tailnet listener",
                status="unavailable",
                severity="optional",
                detail="The tailnet listener is disabled; swe-mux is reachable over "
                "loopback only.",
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
                title="Tailscale connection",
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
                title="Tailscale connection",
                status="unavailable",
                severity="optional",
                detail=detail or "Tailscale is not installed; remote and mobile "
                "access are unavailable.",
                remedy=str(remote.get("connection_command") or "Install Tailscale."),
            )
        )
    else:  # logged_out, stopped, connecting, needs_machine_auth, unknown
        checks.append(
            _check(
                id="remote.connection",
                category="remote",
                title="Tailscale connection",
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
            title="Tailscale Serve (mobile voice)",
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
                title="Windows Defender Firewall",
                status="unavailable",
                severity="info",
                detail="Firewall inspection is not applicable on this platform; "
                "inbound is governed by the host firewall.",
            )
        ]
    if not firewall.get("inspection_available"):
        return [
            _check(
                id="firewall.inbound",
                category="firewall",
                title="Windows Defender Firewall",
                status="warn",
                severity="optional",
                detail=str(firewall.get("detail") or "Firewall could not be inspected."),
                remedy="Run the firewall repair from Settings -> Remote if a phone "
                "cannot connect.",
            )
        ]
    if firewall.get("needs_repair"):
        return [
            _check(
                id="firewall.inbound",
                category="firewall",
                title="Windows Defender Firewall",
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
            title="Windows Defender Firewall",
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
                title="Session identity collisions",
                status="fail",
                severity="critical",
                detail=f"{len(collisions)} identity collision(s): two sessions share "
                "one conversation, so one session's status and tokens render under "
                "another's identity.",
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
                title="Screen classifier blind",
                status="warn",
                severity="critical",
                detail=f"{len(blind)} session(s) the screen classifier cannot read; "
                "status falls back to weaker evidence.",
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
                title="Stuck sessions",
                status="warn",
                severity="critical",
                detail=f"{len(stuck)} session(s) sat in an active state past the stuck "
                "bound with no fresh evidence.",
                remedy="Inspect the session's state-log for the last layer reading.",
            )
        )
    if status_health.get("alarm") and not (collisions or blind or stuck):
        checks.append(
            _check(
                id="status.alarm",
                category="status",
                title="Fleet status-health alarm",
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
                title="Fleet status health",
                status="ok",
                severity="critical",
                detail="No status-health alarm; sessions reach terminal status by "
                "proven evidence.",
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
                title="Background loops",
                status="fail",
                severity="critical",
                detail=f"Degraded loop(s): {', '.join(map(str, degraded))}. A dead "
                "loop silently stops a feature (status, cleanup, delivery) for the "
                "rest of the process life.",
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
                title="Background loops",
                status="warn",
                severity="critical",
                detail=f"All loops running, but {faults} recovered fault(s) recorded "
                "this daemon boot.",
                remedy="Inspect daemon.log if faults recur.",
            )
        ]
    return [
        _check(
            id="background.loops",
            category="background",
            title="Background loops",
            status="ok",
            severity="critical",
            detail="All supervised background loops are running with no faults.",
        )
    ]


def _freshness_checks(freshness: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not freshness:
        return [
            _check(
                id="freshness.ok",
                category="freshness",
                title="Observation freshness",
                status="ok",
                severity="critical",
                detail="Every agent session's observation is fresh; none is reporting "
                "a stale, missing, or relocated conversation.",
            )
        ]
    blocking = [row for row in freshness if row.get("delivery_blocking")]
    status: Status = "fail" if blocking else "warn"
    names = ", ".join(f"{row['name']} ({row['reason']})" for row in freshness[:5])
    return [
        _check(
            id="freshness.stale",
            category="freshness",
            title="Observation freshness",
            status=status,
            severity="critical",
            detail=f"{len(freshness)} agent session(s) with an untrusted observation: "
            f"{names}. These look healthy but the daemon cannot prove the followed "
            "conversation is live" + (" and delivery is blocked." if blocking else "."),
            remedy="Inspect GET /api/sessions/{id}/state-log; the session may need a "
            "fresh turn or a re-attach to re-bind its transcript.",
        )
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
    checks += _freshness_checks(freshness)

    summary = {"ok": 0, "warn": 0, "fail": 0, "unavailable": 0}
    for check in checks:
        summary[check["status"]] = summary.get(check["status"], 0) + 1

    return {
        "version": DOCTOR_REPORT_VERSION,
        "generated_at": now,
        "ok": summary["fail"] == 0,
        "summary": summary,
        "capabilities": _capabilities(health, platform, daemon, remote, firewall, harnesses),
        "checks": checks,
        "observation_freshness": freshness,
    }


def _capabilities(
    health: dict[str, Any],
    platform: dict[str, Any],
    daemon: dict[str, Any],
    remote: dict[str, Any],
    firewall: dict[str, Any],
    harnesses: dict[str, Any],
) -> dict[str, Any]:
    """Machine-readable capability/version block, free of secrets and bytes."""
    return {
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
