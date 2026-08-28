from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from swe_mux import cli

# --------------------------------------------------------------------------- #
# URL resolution
# --------------------------------------------------------------------------- #


def test_url_flag_beats_env_and_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUX_URL", "http://env:9000")
    assert cli.resolve_base_url("http://flag:1234/") == "http://flag:1234"


def test_env_beats_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUX_URL", "http://env:9000/")
    assert cli.resolve_base_url(None) == "http://env:9000"


def test_config_supplies_host_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MUX_URL", raising=False)

    class _Config:
        host = "127.0.0.1"
        port = 18765

    monkeypatch.setattr(
        "swe_mux.config.load_config", lambda: _Config(), raising=True
    )
    assert cli.resolve_base_url(None) == "http://127.0.0.1:18765"


def test_config_failure_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MUX_URL", raising=False)

    def _boom() -> Any:
        raise RuntimeError("no config")

    monkeypatch.setattr("swe_mux.config.load_config", _boom, raising=True)
    assert cli.resolve_base_url(None) == cli.DEFAULT_URL


# --------------------------------------------------------------------------- #
# Session id / name resolution
# --------------------------------------------------------------------------- #


def _stub_sessions(monkeypatch: pytest.MonkeyPatch, sessions: list[dict[str, Any]]) -> None:
    def _request(method: str, path: str, body: Any = None, *, base: str) -> Any:
        assert path == "/api/sessions"
        return sessions
    monkeypatch.setattr(cli, "request", _request)


def test_resolve_exact_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_sessions(monkeypatch, [{"id": "abc123", "name": "one", "backend": "shell"}])
    assert cli.resolve_session("abc123", base="x") == "abc123"


def test_resolve_exact_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_sessions(monkeypatch, [{"id": "abc123", "name": "one", "backend": "shell"}])
    assert cli.resolve_session("one", base="x") == "abc123"


def test_resolve_unique_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_sessions(monkeypatch, [{"id": "abc123", "name": "one", "backend": "shell"}])
    assert cli.resolve_session("abc", base="x") == "abc123"


def test_resolve_ambiguous_name_exits_distinct_code(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_sessions(
        monkeypatch,
        [
            {"id": "aaa1", "name": "dup", "backend": "shell"},
            {"id": "bbb2", "name": "dup", "backend": "shell"},
        ],
    )
    with pytest.raises(cli.CliError) as exc:
        cli.resolve_session("dup", base="x")
    assert exc.value.code == cli.EXIT_AMBIGUOUS


def test_resolve_not_found_exits_distinct_code(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_sessions(monkeypatch, [{"id": "abc123", "name": "one", "backend": "shell"}])
    with pytest.raises(cli.CliError) as exc:
        cli.resolve_session("nope", base="x")
    assert exc.value.code == cli.EXIT_NOT_FOUND


# --------------------------------------------------------------------------- #
# Table rendering
# --------------------------------------------------------------------------- #


def test_render_table_aligns_and_marks_none() -> None:
    text = cli.render_table(
        [{"a": "x", "b": None}, {"a": "yy", "b": True}],
        [("A", "a"), ("B", "b")],
    )
    lines = text.splitlines()
    assert lines[0].split() == ["A", "B"]
    assert "-" == text[text.index("\n") + 1]
    assert "yes" in text
    assert "-" in text  # None rendered as dash


def test_render_table_empty_is_none() -> None:
    assert cli.render_table([], [("A", "a")]) == "(none)"


# --------------------------------------------------------------------------- #
# Command dispatch
# --------------------------------------------------------------------------- #


def _capture_requests(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, Any]]:
    captured: list[tuple[str, str, Any]] = []

    def _request(method: str, path: str, body: Any = None, *, base: str) -> Any:
        captured.append((method, path, body))
        if path == "/api/sessions" and method == "GET":
            return [{"id": "sid1", "name": "target", "backend": "shell", "state": "idle",
                     "project_id": "p1"}]
        if path == "/api/diagnostics/doctor":
            return {"ok": False, "summary": {"ok": 1, "warn": 0, "fail": 1, "unavailable": 0},
                    "checks": [{"title": "t", "detail": "d", "status": "fail", "remedy": "fix"}]}
        return {"ok": True}

    monkeypatch.setattr(cli, "request", _request)
    return captured


def test_ls_filters_build_query(monkeypatch: pytest.MonkeyPatch,
                                capsys: pytest.CaptureFixture[str]) -> None:
    captured = _capture_requests(monkeypatch)
    assert cli.main(["ls", "--project", "p1", "--state", "working", "--json"]) == cli.EXIT_OK
    method, path, _ = captured[0]
    assert method == "GET"
    assert path.startswith("/api/sessions?")
    assert "project=p1" in path and "state=working" in path


def test_kill_resolves_name_then_deletes(monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    captured = _capture_requests(monkeypatch)
    assert cli.main(["kill", "target"]) == cli.EXIT_OK
    # First a GET to resolve, then a DELETE to the resolved stable id.
    assert captured[-1] == ("DELETE", "/api/sessions/sid1", None)


def test_doctor_returns_fail_exit_code(monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    _capture_requests(monkeypatch)
    code = cli.main(["doctor"])
    assert code == cli.EXIT_DOCTOR_FAIL
    out = capsys.readouterr().out
    assert "PROBLEMS FOUND" in out


def test_doctor_json_prints_raw(monkeypatch: pytest.MonkeyPatch,
                                capsys: pytest.CaptureFixture[str]) -> None:
    _capture_requests(monkeypatch)
    cli.main(["doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_doctor_export_hits_export_endpoint(monkeypatch: pytest.MonkeyPatch,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    captured = _capture_requests(monkeypatch)
    cli.main(["doctor", "--export"])
    assert ("GET", "/api/diagnostics/export", None) in captured


def test_connection_error_exit_code(monkeypatch: pytest.MonkeyPatch,
                                    capsys: pytest.CaptureFixture[str]) -> None:
    def _boom(method: str, path: str, body: Any = None, *, base: str) -> Any:
        raise cli.CliError("cannot reach", cli.EXIT_CONNECTION)

    monkeypatch.setattr(cli, "request", _boom)
    assert cli.main(["ls"]) == cli.EXIT_CONNECTION
    assert "cannot reach" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# doctor without a daemon
# --------------------------------------------------------------------------- #


def _unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(method: str, path: str, body: Any = None, *, base: str) -> Any:
        raise cli.CliError(
            f"cannot reach the mux daemon at {base}: refused.",
            cli.EXIT_CONNECTION,
            reason="refused",
        )

    monkeypatch.setattr(cli, "request", _boom)


def _local_report(monkeypatch: pytest.MonkeyPatch, *, failing: bool) -> None:
    """Stub the local report so these tests exercise the CLI, not the probes."""
    status = "fail" if failing else "ok"
    report = {
        "version": 1,
        "mode": "local",
        "complete": False,
        "ok": not failing,
        "summary": {"ok": 0 if failing else 1, "warn": 0, "fail": int(failing),
                    "unavailable": 0, "unchecked": 2},
        "daemon": {"reachable": False, "url": "http://127.0.0.1:8765", "detail": "refused"},
        "checks": [
            {"id": "install.pty", "category": "install", "title": "Pseudoterminal backend",
             "status": status, "severity": "critical", "detail": "d", "remedy": "reinstall"},
            {"id": "daemon.unchecked", "category": "daemon", "title": "Daemon health",
             "status": "unchecked", "severity": "info", "detail": "Needs a running daemon.",
             "remedy": None},
            {"id": "status.unchecked", "category": "status", "title": "Fleet status health",
             "status": "unchecked", "severity": "info", "detail": "Needs a running daemon.",
             "remedy": None},
        ],
    }
    monkeypatch.setattr(cli, "local_doctor_report", lambda *, base, detail: report)


def test_doctor_falls_back_to_the_local_report_when_no_daemon_answers(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _unreachable(monkeypatch)
    _local_report(monkeypatch, failing=False)
    code = cli.main(["doctor"])
    out = capsys.readouterr().out
    # A degraded report must not exit as though everything passed; 3 is what
    # "daemon unreachable" already meant, which is exactly what happened.
    assert code == cli.EXIT_CONNECTION
    assert "LOCAL report" in out
    assert "2 unchecked" in out
    assert "healthy" not in out


def test_a_failing_local_check_exits_with_the_doctor_fail_code(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _unreachable(monkeypatch)
    _local_report(monkeypatch, failing=True)
    assert cli.main(["doctor"]) == cli.EXIT_DOCTOR_FAIL
    assert "PROBLEMS FOUND" in capsys.readouterr().out


def test_the_local_report_marks_unchecked_rows_distinctly_from_unavailable(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _unreachable(monkeypatch)
    _local_report(monkeypatch, failing=False)
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "[????] Daemon health" in out
    assert "[n/a ]" not in out
    assert "[OK  ] Daemon health" not in out


def test_doctor_json_prints_the_local_report(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _unreachable(monkeypatch)
    _local_report(monkeypatch, failing=False)
    cli.main(["doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "local"
    assert payload["complete"] is False


def test_a_daemon_http_error_does_not_fall_back_to_the_local_report(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """An HTTP error means a daemon answered: a daemon fault, not an install one."""
    def _boom(method: str, path: str, body: Any = None, *, base: str) -> Any:
        raise cli.CliError("daemon returned HTTP 500: boom", cli.EXIT_HTTP)

    monkeypatch.setattr(cli, "request", _boom)
    assert cli.main(["doctor"]) == cli.EXIT_HTTP
    assert "LOCAL report" not in capsys.readouterr().out


def test_doctor_export_still_fails_without_a_daemon_and_points_at_the_local_report(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _unreachable(monkeypatch)
    assert cli.main(["doctor", "--export"]) == cli.EXIT_CONNECTION
    assert "no local form" in capsys.readouterr().err


def test_the_daemon_report_renders_exactly_as_it_did(
        capsys: pytest.CaptureFixture[str]) -> None:
    """Byte-compatibility guard for the path a running daemon takes.

    The local mode's preamble, `????` mark, and `unchecked` tally are each
    conditioned on a field the daemon payload does not carry, so this pins that
    they stay invisible on that path rather than trusting that they do.
    """
    result = {
        "ok": True,
        "summary": {"ok": 2, "warn": 1, "fail": 0, "unavailable": 1},
        "checks": [
            {"title": "Daemon reachable", "detail": "up", "status": "ok"},
            {"title": "Frontend build", "detail": "served", "status": "ok"},
            {"title": "Tailscale connection", "detail": "off", "status": "warn",
             "remedy": "tailscale up"},
            {"title": "Serve", "detail": "absent", "status": "unavailable",
             "remedy": "ignored"},
        ],
    }
    assert cli._render_doctor(result) == (
        "[OK  ] Daemon reachable: up\n"
        "[OK  ] Frontend build: served\n"
        "[WARN] Tailscale connection: off\n"
        "         -> tailscale up\n"
        "[n/a ] Serve: absent\n"
        "\n"
        "swe-mux doctor: 2 ok, 1 warn, 0 fail, 1 unavailable - healthy"
    )


# --------------------------------------------------------------------------- #
# install-shortcut
# --------------------------------------------------------------------------- #


def _no_daemon_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if anything reaches for the daemon.

    `install-shortcut` exists for the person whose install produced no way to
    start one, so a request here would make the command useless exactly where it
    is the answer.
    """

    def _forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("install-shortcut must not talk to a daemon")

    monkeypatch.setattr(cli, "request", _forbidden)


def _report(**overrides: Any) -> Any:
    from swe_mux import shortcuts

    fields: dict[str, Any] = {"action": "install", "supported": True}
    fields.update(overrides)
    return shortcuts.ShortcutReport(**fields)


def _outcome(slot: str, action: str, detail: str = "") -> Any:
    from swe_mux import shortcuts

    return shortcuts.ShortcutOutcome(
        slot=slot, path=Path(f"{slot}.lnk"), action=action, detail=detail
    )


def test_install_shortcut_asks_for_a_start_menu_and_a_desktop_entry_by_default(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _no_daemon_allowed(monkeypatch)
    seen: dict[str, Any] = {}

    def _apply(*, config: Any, slots: Any, remove: bool) -> Any:
        seen["slots"] = list(slots)
        seen["remove"] = remove
        return _report(outcomes=(_outcome("desktop", "created"),))

    monkeypatch.setattr("swe_mux.shortcuts.apply_shortcuts", _apply)
    assert cli.main(["install-shortcut"]) == cli.EXIT_OK
    assert seen == {"slots": ["start-menu", "desktop"], "remove": False}
    assert "created" in capsys.readouterr().out


def test_install_shortcut_flags_reach_the_plan(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _no_daemon_allowed(monkeypatch)
    seen: dict[str, Any] = {}

    def _apply(*, config: Any, slots: Any, remove: bool) -> Any:
        seen["slots"] = list(slots)
        seen["remove"] = remove
        return _report(outcomes=(_outcome("startup", "created"),))

    monkeypatch.setattr("swe_mux.shortcuts.apply_shortcuts", _apply)
    cli.main(["install-shortcut", "--startup", "--no-desktop"])
    assert seen == {"slots": ["start-menu", "startup"], "remove": False}
    cli.main(["install-shortcut", "--remove"])
    assert seen["remove"] is True
    capsys.readouterr()


def test_install_shortcut_with_nothing_left_to_create_is_refused(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _no_daemon_allowed(monkeypatch)
    code = cli.main(["install-shortcut", "--no-desktop", "--no-start-menu"])
    assert code == cli.EXIT_LOCAL_FAIL
    assert "nothing to do" in capsys.readouterr().err


def test_an_unsupported_host_is_reported_and_still_exits_zero(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """POSIX asked for something this host does not have; nothing went wrong.

    Exiting non-zero here would make the command red on every POSIX machine that
    merely asked, which is a different fact from a shortcut that did not land.
    """
    _no_daemon_allowed(monkeypatch)
    monkeypatch.setattr(
        "swe_mux.shortcuts.apply_shortcuts",
        lambda **_: _report(supported=False, reason="Shell shortcuts are Windows-only."),
    )
    assert cli.main(["install-shortcut"]) == cli.EXIT_OK
    assert "Windows-only" in capsys.readouterr().out


def test_a_shortcut_that_was_attempted_and_failed_exits_non_zero(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _no_daemon_allowed(monkeypatch)
    monkeypatch.setattr(
        "swe_mux.shortcuts.apply_shortcuts",
        lambda **_: _report(outcomes=(_outcome("desktop", "failed", "denied"),)),
    )
    assert cli.main(["install-shortcut"]) == cli.EXIT_LOCAL_FAIL
    assert "denied" in capsys.readouterr().out


def test_a_config_that_will_not_load_says_so_instead_of_tracebacking(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _no_daemon_allowed(monkeypatch)

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError("port must be an integer")

    monkeypatch.setattr("swe_mux.config.load_config", _boom)
    assert cli.main(["install-shortcut"]) == cli.EXIT_LOCAL_FAIL
    err = capsys.readouterr().err
    assert "port must be an integer" in err
    assert "mux doctor" in err


def test_install_shortcut_json_carries_every_path_it_wrote(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _no_daemon_allowed(monkeypatch)
    monkeypatch.setattr(
        "swe_mux.shortcuts.apply_shortcuts",
        lambda **_: _report(
            target=Path("swe-mux.exe"),
            outcomes=(_outcome("start-menu", "created"), _outcome("desktop", "unchanged")),
        ),
    )
    cli.main(["install-shortcut", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert [row["path"] for row in payload["shortcuts"]] == ["start-menu.lnk", "desktop.lnk"]
    assert [row["action"] for row in payload["shortcuts"]] == ["created", "unchanged"]
