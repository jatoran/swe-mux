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


# --------------------------------------------------------------------------- #
# Launcher names
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("argv0", "expected"),
    [
        (r"C:\Users\x\.venv\Scripts\swemux.exe", "swemux"),
        ("/home/x/.local/bin/swemux", "swemux"),
        # Anything this project does not ship prints the name it documents,
        # rather than whatever the copy happens to be called.
        (r"C:\Users\x\.venv\Scripts\mux.exe", "swemux"),
        ("/home/x/.local/bin/sm", "swemux"),
    ],
)
def test_the_usage_line_names_the_command_that_was_actually_typed(
    argv0: str, expected: str
) -> None:
    """The `.exe` suffix has to come off, and a foreign name has to be ignored.

    Two failures in one place. Argparse's own default `prog` is `argv[0]`'s
    basename, which on the proving platform is `swemux.exe` - a spelling nobody
    types and no document contains. And a renamed or symlinked copy must still
    print the name this project documents, which is why `LAUNCHER_NAMES` is a
    closed set rather than "whatever the file is called": `mux` was a launcher
    until 2026-08-30, and a usage line naming it now would be advice to run
    something that does not exist.
    """
    assert cli.invoked_as(argv0) == expected
    assert cli.build_parser(prog=cli.invoked_as(argv0)).format_usage().startswith(
        f"usage: {expected}"
    )


@pytest.mark.parametrize(
    "argv0",
    ["", "/usr/lib/python3.12/site-packages/swe_mux/cli.py", "/opt/renamed-copy", "/tmp/"],
)
def test_a_name_this_project_does_not_ship_prints_the_documented_one(argv0: str) -> None:
    """`invoked_as` is display text, so an unknown `argv[0]` falls back rather than echoes.

    Reached by `python -m swe_mux.cli`, by an embedder that leaves `argv[0]`
    empty, and by a copy someone renamed. Printing that name back would tell the
    reader to run a command no document describes and no install provides.
    """
    assert cli.invoked_as(argv0) == cli.DEFAULT_PROG
    assert cli.DEFAULT_PROG in cli.LAUNCHER_NAMES


def test_the_daemon_resolves_its_own_pair_by_the_same_rule() -> None:
    """The rule is shared; only the vocabulary differs, and the two must not overlap.

    A daemon launcher that resolved to a client name (or the reverse) would print
    a usage line for the other program's flags.
    """
    from swe_mux.__main__ import DAEMON_LAUNCHER_NAMES, DEFAULT_DAEMON_PROG

    for name in DAEMON_LAUNCHER_NAMES:
        assert (
            cli.invoked_as(
                f"/bin/{name}", names=DAEMON_LAUNCHER_NAMES, default=DEFAULT_DAEMON_PROG
            )
            == name
        )
    assert not (DAEMON_LAUNCHER_NAMES & cli.LAUNCHER_NAMES)
    assert DEFAULT_DAEMON_PROG in DAEMON_LAUNCHER_NAMES


# ------------------------------------------------------------------ agent mode
#
# `swemux agent` is the CLI as a second transport onto the mux MCP surface
# (ROADMAP Phase 23 W2/W3): env-authenticated, one passthrough, zero per-tool
# CLI code. What these pin: the env refusals, the k=v / k:=json argument
# grammar, the unwrapping of a tool result, and the exit-code contract - a
# typed refusal prints the tool's own JSON and exits 1.


def _agent_env(monkeypatch, surfaces: str | None = "mcp,cli") -> None:
    monkeypatch.setenv("MUX_SESSION_ID", "s1")
    monkeypatch.setenv("MUX_MCP_URL", "http://127.0.0.1:8765/mcp")
    monkeypatch.setenv("MUX_MCP_TOKEN", "tok-abc")
    if surfaces is None:
        monkeypatch.delenv("MUX_SURFACES", raising=False)
    else:
        monkeypatch.setenv("MUX_SURFACES", surfaces)


def test_agent_mode_refuses_outside_a_pane(monkeypatch, capsys) -> None:
    for var in ("MUX_SESSION_ID", "MUX_MCP_URL", "MUX_MCP_TOKEN", "MUX_SURFACES"):
        monkeypatch.delenv(var, raising=False)
    assert cli.main(["agent", "tools"]) == cli.EXIT_LOCAL_FAIL
    err = capsys.readouterr().err
    assert "MUX_MCP_TOKEN" in err


def test_agent_mode_refuses_when_the_cli_surface_is_off(monkeypatch, capsys) -> None:
    _agent_env(monkeypatch, surfaces="mcp")
    assert cli.main(["agent", "tools"]) == cli.EXIT_LOCAL_FAIL
    assert "Settings -> Harnesses" in capsys.readouterr().err


def test_agent_call_speaks_the_mcp_passthrough_and_unwraps_the_result(
    monkeypatch, capsys
) -> None:
    _agent_env(monkeypatch)
    calls: list[tuple[str, str, str, dict]] = []

    def fake_rpc(url: str, token: str, method: str, params: dict) -> dict:
        calls.append((url, token, method, params))
        return {
            "content": [{"type": "text", "text": json.dumps({"sessions": []})}],
            "isError": False,
        }

    monkeypatch.setattr(cli, "_mcp_rpc", fake_rpc)
    code = cli.main(
        [
            "agent",
            "call",
            "list_sessions",
            "project=fleet",
            "limit:=5",
            "include_ended:=true",
        ]
    )
    assert code == cli.EXIT_OK
    url, token, method, params = calls[0]
    assert url == "http://127.0.0.1:8765/mcp"
    assert token == "tok-abc"
    assert method == "tools/call"
    assert params == {
        "name": "list_sessions",
        # k=v is a string, k:=v is JSON-typed - the difference the grammar exists for.
        "arguments": {"project": "fleet", "limit": 5, "include_ended": True},
    }
    assert json.loads(capsys.readouterr().out) == {"sessions": []}


def test_a_typed_refusal_prints_the_tools_own_json_and_exits_one(
    monkeypatch, capsys
) -> None:
    _agent_env(monkeypatch, surfaces=None)  # an unset variable means an older daemon
    refusal = {"error": "origin_budget_exhausted", "message": "spent"}

    def fake_rpc(url: str, token: str, method: str, params: dict) -> dict:
        return {
            "content": [{"type": "text", "text": json.dumps(refusal)}],
            "isError": True,
        }

    monkeypatch.setattr(cli, "_mcp_rpc", fake_rpc)
    assert cli.main(["agent", "call", "notify", "target=x", "body=y"]) == cli.EXIT_LOCAL_FAIL
    printed = json.loads(capsys.readouterr().out)
    assert printed["error"] == "origin_budget_exhausted"
    assert printed["isError"] is True


def test_agent_tools_lists_names_for_humans(monkeypatch, capsys) -> None:
    _agent_env(monkeypatch)

    def fake_rpc(url: str, token: str, method: str, params: dict) -> dict:
        assert method == "tools/list"
        return {
            "tools": [
                {"name": "list_sessions", "description": "List sessions. More prose."}
            ]
        }

    monkeypatch.setattr(cli, "_mcp_rpc", fake_rpc)
    assert cli.main(["agent", "tools"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "list_sessions" in out
    assert "swemux agent call" in out


def test_malformed_tool_arguments_are_refused_locally(monkeypatch, capsys) -> None:
    _agent_env(monkeypatch)
    monkeypatch.setattr(
        cli, "_mcp_rpc", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no rpc"))
    )
    assert cli.main(["agent", "call", "notify", "not-a-pair"]) == cli.EXIT_LOCAL_FAIL
    assert "key=value" in capsys.readouterr().err
    assert cli.main(["agent", "call", "notify", "n:=not-json"]) == cli.EXIT_LOCAL_FAIL


def test_requests_carry_the_pane_identity_headers(monkeypatch) -> None:
    """Phase 23 W1's client half: inside a pane, every operator-route request
    names its calling session, which is what lets the daemon refuse the
    session-acting verbs from an agent pane."""
    _agent_env(monkeypatch)
    seen: dict[str, str] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"[]"

    def fake_urlopen(req, timeout=0):
        seen.update({key: value for key, value in req.header_items()})

        class Body:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        import io

        return io.BytesIO(b"[]")

    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_urlopen)
    cli.request("GET", "/api/sessions", base="http://127.0.0.1:1")
    headers = {key.lower(): value for key, value in seen.items()}
    assert headers.get("x-mux-caller-session") == "s1"
    assert headers.get("x-mux-caller-token") == "tok-abc"


def test_requests_outside_a_pane_carry_no_identity(monkeypatch) -> None:
    for var in ("MUX_SESSION_ID", "MUX_MCP_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    seen: dict[str, str] = {}

    def fake_urlopen(req, timeout=0):
        seen.update({key: value for key, value in req.header_items()})
        import io

        return io.BytesIO(b"[]")

    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_urlopen)
    cli.request("GET", "/api/sessions", base="http://127.0.0.1:1")
    assert not any(key.lower().startswith("x-mux-caller") for key in seen)
