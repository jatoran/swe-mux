from __future__ import annotations

import json
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
