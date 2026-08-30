from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from swe_mux import doctor
from swe_mux.harness import AGENT_BACKENDS

_AGENT = next(iter(AGENT_BACKENDS))


def _session(
    *,
    sid: str,
    name: str,
    backend: str,
    stale_since: float | None = None,
    reason: str | None = None,
    diagnostic: str | None = None,
) -> SimpleNamespace:
    record = SimpleNamespace(
        id=sid,
        name=name,
        backend=backend,
        observation_stale_since=stale_since,
        observation_diagnostic=diagnostic,
    )
    return SimpleNamespace(record=record, observation_stale_reason=reason)


def _healthy_sources() -> dict[str, Any]:
    return {
        "health": {
            "ok": True,
            "version": "0.1.0",
            "live_sessions": 2,
            "ui_build_id": "abcdef123456",
            "supervisor_state": "connected",
            "supervisor_unadopted": 0,
        },
        "remote": {
            "tailnet_enabled": True,
            "connection_state": "connected",
            "device_name": "host.ts.net",
            "connection_detail": "Connected to Tailscale as host.ts.net.",
            "serve_configured": True,
            "serve_url": "https://host.ts.net/",
        },
        "firewall": {"supported": True, "inspection_available": True, "needs_repair": False,
                     "detail": "Inbound admitted."},
        "prerequisites": [
            {"id": "git", "label": "Git", "present": True, "path": "/usr/bin/git",
             "purpose": "worktrees", "install_command": "winget install Git.Git"},
        ],
        "status_health": {"alarm": False, "identity_collisions": [],
                          "classifier_blind_sessions": [], "stuck_sessions": []},
        "background": {"degraded": [], "total_faults": 0},
        "harnesses": {"harnesses": [
            {"name": _AGENT, "display_name": "Agent", "installed": True,
             "cli_version": "1.2.3", "version_untested": False, "level": "controlled",
             "resolved_path": "/bin/agent", "capabilities": {}},
        ]},
        "freshness": [],
        "platform": {"system": "win32", "python": "3.12.0", "frozen": True},
        "daemon": {"host": "127.0.0.1", "port": 8765},
        "now": 1000.0,
    }


def test_healthy_report_is_ok_with_no_failures() -> None:
    report = doctor.build_doctor_report(**_healthy_sources())
    assert report["ok"] is True
    assert report["summary"]["fail"] == 0
    # Every check carries a status and a severity.
    for check in report["checks"]:
        assert check["status"] in {"ok", "warn", "fail", "unavailable"}
        assert check["severity"] in {"critical", "optional", "info"}
    caps = report["capabilities"]
    assert caps["swe_mux_version"] == "0.1.0"
    assert caps["remote"]["connection_state"] == "connected"


def test_lost_supervisor_is_a_critical_failure() -> None:
    sources = _healthy_sources()
    sources["health"]["supervisor_state"] = "lost"
    report = doctor.build_doctor_report(**sources)
    supervisor = next(c for c in report["checks"] if c["id"] == "daemon.supervisor")
    assert supervisor["status"] == "fail"
    assert supervisor["severity"] == "critical"
    assert supervisor["remedy"]
    assert report["ok"] is False


def test_firewall_needs_repair_fails_with_remedy() -> None:
    sources = _healthy_sources()
    sources["firewall"] = {
        "supported": True, "inspection_available": True, "needs_repair": True,
        "detail": "Inbound rule missing.",
    }
    report = doctor.build_doctor_report(**sources)
    firewall = next(c for c in report["checks"] if c["id"] == "firewall.inbound")
    assert firewall["status"] == "fail"
    assert "repair" in firewall["remedy"].lower()
    assert report["ok"] is False


def test_unsupported_firewall_is_unavailable_not_a_failure() -> None:
    sources = _healthy_sources()
    sources["firewall"] = {"supported": False, "needs_repair": False}
    report = doctor.build_doctor_report(**sources)
    firewall = next(c for c in report["checks"] if c["id"] == "firewall.inbound")
    assert firewall["status"] == "unavailable"
    assert firewall["severity"] == "info"
    assert report["ok"] is True


def test_absent_optional_prerequisite_is_unavailable_optional() -> None:
    sources = _healthy_sources()
    sources["prerequisites"] = [
        {"id": "tailscale", "label": "Tailscale", "present": False, "path": None,
         "purpose": "remote access", "install_command": "winget install tailscale.tailscale"},
    ]
    report = doctor.build_doctor_report(**sources)
    prereq = next(c for c in report["checks"] if c["id"] == "prereq.tailscale")
    assert prereq["status"] == "unavailable"
    assert prereq["severity"] == "optional"
    assert prereq["remedy"] == "winget install tailscale.tailscale"
    assert report["ok"] is True


def test_untested_harness_version_warns_at_info_severity() -> None:
    sources = _healthy_sources()
    sources["harnesses"]["harnesses"][0]["version_untested"] = True
    sources["harnesses"]["harnesses"][0]["tested_cli_version"] = "1.0.0"
    report = doctor.build_doctor_report(**sources)
    harness = next(c for c in report["checks"] if c["id"] == f"harness.{_AGENT}")
    assert harness["status"] == "warn"
    assert harness["severity"] == "info"


def test_identity_collision_is_a_critical_status_failure() -> None:
    sources = _healthy_sources()
    sources["status_health"] = {
        "alarm": True, "alarm_reasons": ["identity_collision"],
        "identity_collisions": [{"kind": "native", "backend": _AGENT, "value": "x",
                                 "sessions": ["a", "b"]}],
        "classifier_blind_sessions": [], "stuck_sessions": [],
    }
    report = doctor.build_doctor_report(**sources)
    collision = next(c for c in report["checks"] if c["id"] == "status.identity_collisions")
    assert collision["status"] == "fail"
    assert report["ok"] is False


def test_degraded_background_loop_fails() -> None:
    sources = _healthy_sources()
    sources["background"] = {"degraded": ["process-inspector"], "total_faults": 3}
    report = doctor.build_doctor_report(**sources)
    loops = next(c for c in report["checks"] if c["id"] == "background.loops")
    assert loops["status"] == "fail"
    assert "process-inspector" in loops["detail"]


def test_observation_freshness_lists_only_stale_agent_sessions() -> None:
    sessions = [
        _session(sid="s1", name="fresh", backend=_AGENT),
        _session(sid="s2", name="shell-stale", backend="shell", stale_since=900.0),
        _session(
            sid="s3", name="stale-agent", backend=_AGENT, stale_since=880.0,
            reason="transcript_stale", diagnostic="transcript X last written 40s ago",
        ),
        _session(
            sid="s4", name="moved-agent", backend=_AGENT, stale_since=800.0,
            reason="conversation_owned_elsewhere", diagnostic="owned by s9",
        ),
    ]
    rows = doctor.observation_freshness(sessions, now=1000.0)
    # Only stale AGENT sessions, oldest first (s4 before s3), shell excluded.
    assert [r["id"] for r in rows] == ["s4", "s3"]
    assert rows[0]["reason"] == "conversation_owned_elsewhere"
    assert rows[0]["delivery_blocking"] is True
    assert rows[1]["delivery_blocking"] is False
    assert rows[1]["seconds_stale"] == 120.0


def test_freshness_check_delivery_blocking_reason_fails_report() -> None:
    sources = _healthy_sources()
    sources["freshness"] = [
        {"id": "s4", "name": "moved", "backend": _AGENT,
         "reason": "transcript_missing", "since": 800.0, "seconds_stale": 200.0,
         "diagnostic": None, "delivery_blocking": True},
    ]
    report = doctor.build_doctor_report(**sources)
    fresh = next(c for c in report["checks"] if c["id"] == "freshness.stale")
    assert fresh["status"] == "fail"
    assert report["ok"] is False


def test_freshness_stale_only_warns_when_delivery_not_blocked() -> None:
    sources = _healthy_sources()
    sources["freshness"] = [
        {"id": "s3", "name": "stale", "backend": _AGENT,
         "reason": "transcript_stale", "since": 880.0, "seconds_stale": 120.0,
         "diagnostic": None, "delivery_blocking": False},
    ]
    report = doctor.build_doctor_report(**sources)
    fresh = next(c for c in report["checks"] if c["id"] == "freshness.stale")
    assert fresh["status"] == "warn"
    assert report["ok"] is True


def test_report_carries_no_secret_or_message_content_keys() -> None:
    # The report is a fixed structure; assert the freshness rows never leak more
    # than id/name/backend/reason/age/diagnostic. A regression that widened the
    # row would fail here.
    sources = _healthy_sources()
    sources["freshness"] = [
        {"id": "s3", "name": "n", "backend": _AGENT, "reason": "transcript_stale",
         "since": 1.0, "seconds_stale": 2.0, "diagnostic": "path", "delivery_blocking": False},
    ]
    report = doctor.build_doctor_report(**sources)
    allowed = {"id", "name", "backend", "reason", "since", "seconds_stale",
               "diagnostic", "delivery_blocking"}
    for row in report["observation_freshness"]:
        assert set(row).issubset(allowed)


def _hook_session(
    *,
    sid: str,
    name: str,
    backend: str,
    watch_since: float | None,
    last_hook_ts: float = 0.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        record=SimpleNamespace(id=sid, name=name, backend=backend),
        hook_channel_watch_since=watch_since,
        last_hook_ts=last_hook_ts,
    )


def test_hook_ingress_silence_reports_a_spawned_session_that_never_spoke() -> None:
    rows = doctor.hook_ingress_silence(
        [_hook_session(sid="s1", name="one", backend=_AGENT, watch_since=100.0)],
        now=100.0 + doctor.HOOK_SILENCE_GRACE_SECONDS + 1,
    )
    assert [row["id"] for row in rows] == ["s1"]
    assert rows[0]["seconds_silent"] > doctor.HOOK_SILENCE_GRACE_SECONDS


def test_hook_ingress_silence_ignores_a_session_still_inside_its_grace() -> None:
    assert (
        doctor.hook_ingress_silence(
            [_hook_session(sid="s1", name="one", backend=_AGENT, watch_since=100.0)],
            now=100.0 + doctor.HOOK_SILENCE_GRACE_SECONDS - 1,
        )
        == []
    )


def test_hook_ingress_silence_ignores_an_adopted_session() -> None:
    """The exact false positive that would fire on every reload.

    A daemon restart resets `last_hook_ts` for every surviving session while no
    hook is owed - an idle agent has no reason to speak for hours. Only a session
    this daemon *spawned* was promised a SessionStart, which is what
    `hook_channel_watch_since` records and why it is `None` here.
    """
    assert (
        doctor.hook_ingress_silence(
            [_hook_session(sid="s1", name="one", backend=_AGENT, watch_since=None)],
            now=1e9,
        )
        == []
    )


def test_hook_ingress_silence_ignores_a_witnessed_session_and_a_shell() -> None:
    rows = doctor.hook_ingress_silence(
        [
            _hook_session(
                sid="s1", name="one", backend=_AGENT, watch_since=1.0, last_hook_ts=2.0
            ),
            _hook_session(sid="s2", name="two", backend="shell", watch_since=1.0),
        ],
        now=1e9,
    )
    assert rows == []


def test_hook_ingress_silence_ignores_an_uninstrumented_harness() -> None:
    """Launch clean is supposed to be silent; reporting it would be a lie."""
    rows = doctor.hook_ingress_silence(
        [_hook_session(sid="s1", name="one", backend=_AGENT, watch_since=1.0)],
        now=1e9,
        instrumented=lambda backend: False,
    )
    assert rows == []


def test_silent_hook_channel_is_a_critical_report_failure() -> None:
    sources = _healthy_sources()
    sources["hook_silence"] = [
        {"id": "s1", "name": "one", "backend": _AGENT, "since": 1.0, "seconds_silent": 300.0}
    ]
    report = doctor.build_doctor_report(**sources)
    check = next(c for c in report["checks"] if c["id"] == "hook_ingress.silent")
    assert check["status"] == "fail"
    assert check["severity"] == "critical"
    assert check["remedy"]
    assert report["ok"] is False


def test_healthy_report_states_the_hook_channel_is_working() -> None:
    report = doctor.build_doctor_report(**_healthy_sources())
    check = next(c for c in report["checks"] if c["id"] == "hook_ingress.ok")
    assert check["status"] == "ok"
    assert report["hook_ingress_silence"] == []
