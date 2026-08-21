"""Per-server MCP tool catalogs: evidence tiers, caching, and what never leaks."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from swe_mux import mcp_tools
from swe_mux.agent_environment import McpServerConfig, resolve_mcp_servers
from swe_mux.agent_environment import clear_cache as clear_environment_cache
from swe_mux.harness import agent_harnesses
from swe_mux.mcp_contract import READ_TOOL_NAMES, WRITE_TOOL_NAMES

TINY_SERVER = """
from mcp.server.mcpserver import MCPServer

server = MCPServer("tiny-probe")


@server.tool()
def echo(text: str) -> str:
    \"\"\"Echo the text back.\"\"\"
    return text


@server.tool()
def add(left: int, right: int) -> int:
    \"\"\"Add two integers.\"\"\"
    return left + right


if __name__ == "__main__":
    server.run()
"""


@pytest.fixture(autouse=True)
def _isolated_caches():
    mcp_tools.clear_cache()
    clear_environment_cache()
    yield
    mcp_tools.clear_cache()
    clear_environment_cache()


def _entry(name: str, config: dict[str, Any]) -> McpServerConfig:
    return McpServerConfig(
        name=name,
        scope="project",
        origin="test",
        source_label="test",
        enabled=True,
        config=config,
    )


# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------


def test_every_agent_harness_declares_a_tier() -> None:
    """A harness added to the registry must decide what it can prove about MCP.

    The alternative failure is silent and bad: a new harness would report an
    empty catalog, which reads as "this server publishes no tools".
    """
    for backend in agent_harnesses():
        assert mcp_tools.evidence_tier(backend) in {
            "swe_mux_owned",
            "live_process",
            "parallel_probe",
            "not_supported",
        }
    assert mcp_tools.evidence_tier("claude") == "parallel_probe"
    assert mcp_tools.evidence_tier("codex") == "parallel_probe"
    assert mcp_tools.evidence_tier("omp") == "live_process"
    assert mcp_tools.evidence_tier("opencode") == "not_supported"
    assert mcp_tools.evidence_tier("pi") == "not_supported"


def test_passive_harnesses_say_why_rather_than_returning_nothing() -> None:
    for backend in ("opencode", "pi"):
        catalog = mcp_tools.unsupported_catalog(backend, "whatever")
        assert catalog.status == "unsupported"
        assert catalog.evidence == "not_supported"
        assert catalog.tools == ()
        assert catalog.diagnostic


# ---------------------------------------------------------------------------
# Tier 1: swe-mux's own server
# ---------------------------------------------------------------------------


def test_mux_catalog_is_read_from_the_implementation_not_a_second_list() -> None:
    catalog = mcp_tools.mux_owned_catalog()
    names = {tool.name for tool in catalog.tools}
    assert names == set(READ_TOOL_NAMES) | set(WRITE_TOOL_NAMES)
    assert catalog.evidence == "swe_mux_owned"
    assert catalog.status == "ok"
    assert not catalog.diagnostic
    by_name = {tool.name: tool for tool in catalog.tools}
    # The read/write split travels with the catalog, so the drawer can show which
    # of the agent's own tools act rather than only read.
    assert by_name["list_sessions"].read_only is True
    assert by_name["notify"].read_only is False
    assert all(tool.description for tool in catalog.tools)


def test_mux_server_is_recognized_by_endpoint_not_by_the_name_mux() -> None:
    ours = "http://127.0.0.1:8765/mcp"
    assert mcp_tools.is_mux_server({"url": "http://127.0.0.1:8765/mcp?token=x"}, ours)
    # A user is free to name their own server `mux`; publishing swe-mux's
    # catalog for it would be a confident lie.
    assert not mcp_tools.is_mux_server({"url": "https://example.test/mcp"}, ours)
    assert not mcp_tools.is_mux_server({"command": "mux"}, ours)


@pytest.mark.asyncio
async def test_the_injected_mux_server_short_circuits_to_the_owned_catalog(
    tmp_path: Path,
) -> None:
    payload = await mcp_tools.fetch_server_tools(
        backend="claude",
        server="mux",
        entry=_entry(
            "mux",
            {
                "type": "http",
                "url": "http://127.0.0.1:8765/mcp",
                "headers": {"Authorization": "Bearer super-secret"},
            },
        ),
        cwd=tmp_path,
        executable="claude",
        args=[],
        mux_mcp_url="http://127.0.0.1:8765/mcp",
        session_id="s1",
    )
    # Its own bearer header would otherwise make it "auth required"; mux does not
    # need to dial itself to know what it serves.
    assert payload["evidence"] == "swe_mux_owned"
    assert payload["status"] == "ok"
    assert {tool["name"] for tool in payload["tools"]} >= set(READ_TOOL_NAMES)
    assert "super-secret" not in json.dumps(payload)


# ---------------------------------------------------------------------------
# Tier 2: the live OMP process
# ---------------------------------------------------------------------------


def test_omp_tool_names_are_attributed_with_omps_own_sanitizer() -> None:
    # `mcp__<sanitized server>_<tool>`; the sanitizer lowercases and collapses
    # everything that is not a letter or underscore, so raw-name prefix matching
    # would miss every server with a digit, dash, or capital in its name.
    assert mcp_tools.sanitize_omp_name_part("My-Server 2", "server") == "my_server"
    assert mcp_tools.sanitize_omp_name_part("---", "server") == "server"


def test_omp_live_catalog_selects_only_the_named_servers_tools() -> None:
    snapshot = {
        "tools": [
            {"name": "mcp__mux_list_sessions", "description": "List sessions"},
            {"name": "mcp__mux_notify", "description": "Notify"},
            {"name": "mcp__other_thing", "description": "Elsewhere"},
        ],
        "observed_at": 1_700.0,
    }
    catalog = mcp_tools.omp_live_catalog("mux", snapshot)
    assert catalog.evidence == "live_process"
    assert catalog.status == "ok"
    assert [tool.name for tool in catalog.tools] == [
        "mcp__mux_list_sessions",
        "mcp__mux_notify",
    ]
    assert catalog.observed_at == 1_700.0


def test_omp_without_a_published_snapshot_reports_not_reported_not_empty() -> None:
    catalog = mcp_tools.omp_live_catalog("mux", None)
    assert catalog.status == "unavailable"
    assert catalog.tools == ()
    assert "has not reported" in catalog.diagnostic


def test_a_live_snapshot_is_whitelisted_and_bounded() -> None:
    snapshot = mcp_tools.normalize_live_snapshot(
        {
            "tools": [
                {
                    "name": "mcp__mux_list_sessions",
                    "description": "d" * 5_000,
                    "inputSchema": {"secret": "hidden"},
                },
                {"name": "read", "description": "a built-in"},
                "not-a-dict",
            ],
            "reason": "session_start",
            "extra": {"token": "hidden"},
        }
    )
    assert [tool["name"] for tool in snapshot["tools"]] == ["mcp__mux_list_sessions"]
    assert len(snapshot["tools"][0]["description"]) == mcp_tools.MAX_DESCRIPTION_CHARS
    assert "hidden" not in json.dumps(snapshot)
    assert snapshot["reason"] == "session_start"


def test_a_malformed_live_snapshot_is_refused() -> None:
    with pytest.raises(ValueError):
        mcp_tools.normalize_live_snapshot({"tools": "everything"})
    with pytest.raises(ValueError):
        mcp_tools.normalize_live_snapshot([])


def test_the_snapshot_store_is_bounded_and_sweeps_dead_sessions() -> None:
    store = mcp_tools.LiveSnapshotStore(limit=2)
    store.put("a", {"tools": []})
    store.put("b", {"tools": []})
    store.put("c", {"tools": []})
    assert set(store.snapshots) == {"b", "c"}
    store.sweep({"c"})
    assert set(store.snapshots) == {"c"}
    assert store.get("b") is None


# ---------------------------------------------------------------------------
# Tier 3a: the Codex sidecar
# ---------------------------------------------------------------------------


def test_codex_probe_argv_forwards_only_configuration_shaping_arguments() -> None:
    argv = mcp_tools.codex_probe_argv(
        "codex.cmd",
        ["--full-auto", "-c", "mcp_servers.x.command=y", "--profile", "work", "resume", "--last"],
    )
    assert argv == [
        "codex.cmd",
        "app-server",
        "-c",
        "mcp_servers.x.command=y",
        "--profile",
        "work",
    ]


def test_codex_entry_reports_a_login_requirement_rather_than_an_empty_list() -> None:
    catalog = mcp_tools._codex_entry_catalog(
        "connector",
        {
            "name": "connector",
            "authStatus": "notLoggedIn",
            "tools": {},
            "serverInfo": {"name": "connector", "version": "1.2.3"},
        },
    )
    assert catalog.status == "auth_required"
    assert catalog.evidence == "parallel_probe"
    assert catalog.server_version == "1.2.3"


def test_codex_entry_reduces_tools_to_name_and_description() -> None:
    catalog = mcp_tools._codex_entry_catalog(
        "node_repl",
        {
            "name": "node_repl",
            "authStatus": "unsupported",
            "tools": {
                "js": {"name": "js", "description": "Run JS", "inputSchema": {"x": 1}},
                "js_reset": {"name": "js_reset", "description": "Reset"},
            },
        },
    )
    assert [tool.name for tool in catalog.tools] == ["js", "js_reset"]
    payload = catalog.public(fingerprint="f", cached=False)
    assert payload["tools"] == [
        {"name": "js", "description": "Run JS"},
        {"name": "js_reset", "description": "Reset"},
    ]


class _FakeProcess:
    def __init__(self, reader: asyncio.StreamReader) -> None:
        self.stdout = reader


@pytest.mark.asyncio
async def test_the_response_reader_skips_notifications_and_matches_on_the_id() -> None:
    reader = asyncio.StreamReader()
    # The app server interleaves unsolicited notifications with responses; a
    # reader that took the first line would answer with a remote-control status.
    reader.feed_data(b'{"method":"remoteControl/status/changed","params":{}}\n')
    reader.feed_data(b'{"id":1,"result":{"codexHome":"x"}}\n')
    reader.feed_data(b'{"id":2,"result":{"data":[]}}\n')
    reader.feed_eof()
    process = _FakeProcess(reader)
    answer = await mcp_tools._codex_read_response(process, 2)  # type: ignore[arg-type]
    assert answer == {"id": 2, "result": {"data": []}}


@pytest.mark.asyncio
async def test_an_oversized_inventory_frame_is_a_bounded_failure_not_a_crash() -> None:
    """asyncio raises rather than truncating once a line exceeds its buffer.

    Measured on the development host: `mcpServerStatus/list` with
    `toolsAndAuthOnly` still carries every tool's full schema and overran the
    64 KiB default, which surfaced as a bare `ValueError` out of `readline`.
    """
    reader = asyncio.StreamReader(limit=64)
    reader.feed_data(b'{"id":2,"result":' + b"x" * 4096 + b"}\n")
    reader.feed_eof()
    with pytest.raises(mcp_tools._CodexFrameTooLarge):
        await mcp_tools._codex_read_response(_FakeProcess(reader), 2)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_codex_probe_that_cannot_start_reports_a_failure_not_an_exception(
    tmp_path: Path,
) -> None:
    catalog = await mcp_tools.codex_probe(
        "anything",
        executable=str(tmp_path / "does-not-exist"),
        args=[],
        cwd=tmp_path,
    )
    assert catalog.status == "error"
    assert catalog.evidence == "parallel_probe"
    assert catalog.diagnostic


# ---------------------------------------------------------------------------
# Tier 3b: dialling a Claude-configured server
# ---------------------------------------------------------------------------


def test_an_http_server_that_wants_credentials_is_never_dialled() -> None:
    for config in (
        {"type": "http", "url": "https://x.test/mcp", "headers": {"Authorization": "Bearer t"}},
        {"type": "http", "url": "https://x.test/mcp", "headers": {"X-Api-Key": "t"}},
        {"type": "http", "url": "https://x.test/mcp", "oauth": True},
    ):
        assert mcp_tools.http_auth_required(config)
        assert "credentials" in mcp_tools.claude_skip_reason(config)
    assert not mcp_tools.claude_skip_reason({"type": "http", "url": "https://x.test/mcp"})
    assert not mcp_tools.claude_skip_reason({"command": "python", "args": ["s.py"]})


@pytest.mark.asyncio
async def test_an_auth_required_server_renders_as_auth_required_not_as_no_tools(
    tmp_path: Path,
) -> None:
    catalog = await mcp_tools.claude_probe(
        "guarded",
        {"type": "http", "url": "https://x.test/mcp", "headers": {"Authorization": "Bearer t"}},
        cwd=tmp_path,
    )
    assert catalog.status == "auth_required"
    assert catalog.tools == ()
    assert "not probed" in catalog.diagnostic


@pytest.mark.asyncio
async def test_claude_dials_a_stdio_server_and_lists_its_tools(tmp_path: Path) -> None:
    script = tmp_path / "tiny_server.py"
    script.write_text(TINY_SERVER, encoding="utf-8")
    catalog = await mcp_tools.claude_probe(
        "tiny",
        {"command": sys.executable, "args": [str(script)]},
        cwd=tmp_path,
    )
    assert catalog.status == "ok", catalog.diagnostic
    assert catalog.evidence == "parallel_probe"
    assert [tool.name for tool in catalog.tools] == ["add", "echo"]
    # Probe evidence, never presented as the running TUI's own state.
    assert "not the state of the CLI running in this terminal" in catalog.note


@pytest.mark.asyncio
async def test_an_unreachable_stdio_server_reports_a_bounded_failure(tmp_path: Path) -> None:
    catalog = await mcp_tools.claude_probe(
        "broken",
        {"command": str(tmp_path / "nope"), "args": []},
        cwd=tmp_path,
    )
    assert catalog.status == "error"
    assert catalog.diagnostic


# ---------------------------------------------------------------------------
# Cache identity and coalescing
# ---------------------------------------------------------------------------


def test_the_fingerprint_covers_everything_that_changes_what_is_published() -> None:
    base = dict(
        backend="claude",
        server="s",
        config={"command": "python", "args": ["a.py"], "env": {"TOKEN": "one"}},
        executable="claude",
        version="1.0.0",
        cwd="/repo",
    )
    original = mcp_tools.fingerprint(**base)  # type: ignore[arg-type]
    for change in (
        {"config": {"command": "python", "args": ["b.py"], "env": {"TOKEN": "one"}}},
        {"config": {"command": "python", "args": ["a.py"], "env": {"TOKEN": "two"}}},
        {"version": "1.1.0"},
        {"cwd": "/other"},
        {"executable": "claude-next"},
        {"session_id": "s1"},
    ):
        assert mcp_tools.fingerprint(**{**base, **change}) != original  # type: ignore[arg-type]
    # One-way: a credential can decide cache identity without being retained.
    assert "one" not in original
    assert len(original) == 32


@pytest.mark.asyncio
async def test_a_second_fetch_is_served_from_the_cache_and_refresh_bypasses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def fake_probe(server: str, config: dict[str, Any], **kwargs: Any):
        calls.append(server)
        return mcp_tools.McpToolCatalog(
            server=server,
            backend="claude",
            evidence="parallel_probe",
            status="ok",
            tools=(mcp_tools.McpTool(name="one", description=""),),
        )

    monkeypatch.setattr(mcp_tools, "claude_probe", fake_probe)
    kwargs: dict[str, Any] = dict(
        backend="claude",
        server="s",
        entry=_entry("s", {"command": "python"}),
        cwd=tmp_path,
        executable="claude",
        args=[],
        session_id="s1",
    )
    first = await mcp_tools.fetch_server_tools(**kwargs)
    second = await mcp_tools.fetch_server_tools(**kwargs)
    assert calls == ["s"]
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["fingerprint"] == first["fingerprint"]

    await mcp_tools.fetch_server_tools(**kwargs, refresh=True)
    assert calls == ["s", "s"]


@pytest.mark.asyncio
async def test_two_sessions_with_one_profile_share_a_single_probe(tmp_path: Path, monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def slow_probe(server: str, config: dict[str, Any], **kwargs: Any):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return mcp_tools.McpToolCatalog(
            server=server, backend="claude", evidence="parallel_probe", status="ok"
        )

    monkeypatch.setattr(mcp_tools, "claude_probe", slow_probe)

    def call(session_id: str):
        return mcp_tools.fetch_server_tools(
            backend="claude",
            server="s",
            entry=_entry("s", {"command": "python"}),
            cwd=tmp_path,
            executable="claude",
            args=[],
            session_id=session_id,
        )

    first = asyncio.create_task(call("s1"))
    await started.wait()
    second = asyncio.create_task(call("s2"))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second)
    assert calls == 1


@pytest.mark.asyncio
async def test_a_private_cache_scope_is_enforced_rather_than_merely_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def private_probe(server: str, config: dict[str, Any], **kwargs: Any):
        calls.append(server)
        return mcp_tools.McpToolCatalog(
            server=server,
            backend="claude",
            evidence="parallel_probe",
            status="ok",
            cache_scope="private",
        )

    monkeypatch.setattr(mcp_tools, "claude_probe", private_probe)

    def call(session_id: str):
        return mcp_tools.fetch_server_tools(
            backend="claude",
            server="s",
            entry=_entry("s", {"command": "python"}),
            cwd=tmp_path,
            executable="claude",
            args=[],
            session_id=session_id,
        )

    await call("s1")
    again = await call("s1")
    other = await call("s2")
    assert again["cached"] is True
    # The server asked for its reading not to be shared; a second session probes
    # again rather than reading the first one's answer.
    assert other["cached"] is False
    assert calls == ["s", "s"]


@pytest.mark.asyncio
async def test_omp_readings_are_never_shared_between_sessions(tmp_path: Path) -> None:
    reported = await mcp_tools.fetch_server_tools(
        backend="omp",
        server="mux",
        entry=_entry("mux", {"url": "https://x.test/mcp"}),
        cwd=tmp_path,
        executable="omp",
        args=[],
        session_id="s1",
        live_snapshot={"tools": [{"name": "mcp__mux_notify", "description": ""}]},
    )
    silent = await mcp_tools.fetch_server_tools(
        backend="omp",
        server="mux",
        entry=_entry("mux", {"url": "https://x.test/mcp"}),
        cwd=tmp_path,
        executable="omp",
        args=[],
        session_id="s2",
        live_snapshot=None,
    )
    assert reported["status"] == "ok"
    # One process's live reading must never be handed to another session that
    # merely shares its configuration.
    assert silent["status"] == "unavailable"


@pytest.mark.asyncio
async def test_a_probe_that_raises_becomes_a_diagnostic_not_a_broken_drawer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exploding(server: str, config: dict[str, Any], **kwargs: Any):
        raise RuntimeError("boom")

    monkeypatch.setattr(mcp_tools, "claude_probe", exploding)
    payload = await mcp_tools.fetch_server_tools(
        backend="claude",
        server="s",
        entry=_entry("s", {"command": "python"}),
        cwd=tmp_path,
        executable="claude",
        args=[],
        session_id="s1",
    )
    assert payload["status"] == "error"
    assert "boom" in payload["diagnostic"]


# ---------------------------------------------------------------------------
# Resolving which configuration a fetch would dial
# ---------------------------------------------------------------------------


def test_the_resolver_returns_the_winning_layers_raw_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "claude-home"
    cwd = tmp_path / "repo"
    home.mkdir(parents=True)
    cwd.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    (home / "settings.json").write_text(
        json.dumps({"mcpServers": {"shared": {"command": "global-server"}}}),
        encoding="utf-8",
    )
    (cwd / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shared": {"command": "project-server", "env": {"TOKEN": "hidden"}}
                }
            }
        ),
        encoding="utf-8",
    )

    configs = resolve_mcp_servers(backend="claude", cwd=cwd, args=[], loaded_at=2_000.0)

    # The later layer wins, matching the `shadowed` state the row shows - the two
    # answers come from one walk precisely so they cannot disagree.
    assert configs["shared"].config["command"] == "project-server"
    assert configs["shared"].config["env"] == {"TOKEN": "hidden"}


def test_the_resolver_refuses_a_shell_session(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        resolve_mcp_servers(backend="shell", cwd=tmp_path, args=[], loaded_at=1.0)
