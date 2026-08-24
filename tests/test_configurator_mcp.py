"""The configurator MCP family's gate, and the write it wraps.

Two things are worth pinning here and they are separate claims. The **gate** is
that these tools are invisible and unreachable to every session but a
configurator, checked at both listing and dispatch so neither can be the only
one holding. The **write** is that `configurator_apply_settings` refuses as a
result rather than an exception, because an agent needs to know whether to adapt
the value or stop asking.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux.mcp import CONFIGURATOR_TOOLS, TOOLS, McpService, tools_for
from swe_mux.mcp_contract import (
    CONFIGURATOR_READ_TOOL_NAMES,
    CONFIGURATOR_WRITE_TOOL_NAMES,
)


def session(sid: str, *, configurator: bool = False, project: str = "p1") -> Any:
    return SimpleNamespace(
        record=SimpleNamespace(
            id=sid,
            project_id=project,
            project_label="Work",
            cwd="D:/work",
            run_cwd="",
            configurator=configurator,
        ),
        mcp_token=f"tok-{sid}",
    )


class ConfiguratorStub:
    def __init__(self, apply_result: dict[str, Any] | None = None) -> None:
        self.applied: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.apply_result = apply_result or {"applied": True, "hot_applied": ["theme"]}

    async def capabilities(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"tool": "capabilities", **kwargs})
        return {"install": {"mode": "source", "session": kwargs.get("session") or {}}}

    async def diagnostics(self) -> dict[str, Any]:
        return {"ok": True, "checks": []}

    async def apply_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        self.applied.append(changes)
        return self.apply_result

    async def device_settings(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"tool": "device_settings", **kwargs})
        return {"profiles": ["desktop", "mobile"], "index": {}}

    async def edit_device_settings(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"tool": "edit_device_settings", **kwargs})
        return {"applied": ["removed 4"], "digest": "abc"}

    async def project_settings(self, project: str = "") -> dict[str, Any]:
        self.calls.append({"tool": "project_settings", "project": project})
        return {"project": {"id": project}}

    async def apply_project_settings(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"tool": "apply_project_settings", **kwargs})
        return {"applied": True, "changed": sorted(kwargs.get("changes") or {})}


def service_for(*sessions: Any, configurator: Any = None) -> McpService:
    table = {item.record.id: item for item in sessions}
    manager = SimpleNamespace(sessions=table, resolve=lambda sid: table[sid])
    return McpService(manager, SimpleNamespace(), configurator=configurator)


# ------------------------------------------------------------------- the gate


def test_an_ordinary_session_is_shown_the_unchanged_tool_list() -> None:
    assert tools_for(session("s1")) is TOOLS


def test_a_configurator_session_is_shown_both_families() -> None:
    listed = {tool["name"] for tool in tools_for(session("s1", configurator=True))}
    assert listed == {tool["name"] for tool in TOOLS} | {
        tool["name"] for tool in CONFIGURATOR_TOOLS
    }


def test_a_session_with_no_marker_at_all_is_not_a_configurator() -> None:
    """Records adopted from an older daemon have no such attribute.

    Defaulting a missing marker to False is the only safe direction, and it is
    the case a session-preserving restart actually produces.
    """
    legacy = SimpleNamespace(record=SimpleNamespace(id="s1", project_id="p1"), mcp_token="t")
    assert tools_for(legacy) is TOOLS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", [*CONFIGURATOR_READ_TOOL_NAMES, *CONFIGURATOR_WRITE_TOOL_NAMES]
)
async def test_guessing_a_configurator_tool_name_answers_unknown_tool(name: str) -> None:
    """Refused as unknown, not as forbidden.

    To a session that was never shown the tool that is the literal truth, and
    naming a capability that exists elsewhere would only invite an agent to look
    for a way to reach it.
    """
    service = service_for(session("s1"), configurator=ConfiguratorStub())
    with pytest.raises(ValueError, match="unknown tool"):
        await service.dispatch_tool(session("s1"), name, {"changes": {"theme": "nord"}})


@pytest.mark.asyncio
async def test_listing_and_dispatch_agree_for_a_configurator() -> None:
    caller = session("s1", configurator=True)
    service = service_for(caller, configurator=ConfiguratorStub())
    listing = await service.handle_rpc(caller, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listing is not None
    listed = {tool["name"] for tool in listing["result"]["tools"]}
    for name in (*CONFIGURATOR_READ_TOOL_NAMES, *CONFIGURATOR_WRITE_TOOL_NAMES):
        assert name in listed
    assert await service.dispatch_tool(caller, "configurator_capabilities", {})


@pytest.mark.asyncio
async def test_the_initialize_briefing_mentions_the_extra_tools_only_to_a_configurator() -> None:
    service = service_for(configurator=ConfiguratorStub())
    plain = await service.handle_rpc(
        session("s1"), {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    special = await service.handle_rpc(
        session("s2", configurator=True),
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert plain is not None and special is not None
    assert "configurator_*" not in plain["result"]["instructions"]
    assert "configurator_*" in special["result"]["instructions"]


@pytest.mark.asyncio
async def test_the_reads_are_annotated_read_only_and_the_write_is_not() -> None:
    annotations = {
        str(tool["name"]): tool["annotations"]["readOnlyHint"] for tool in CONFIGURATOR_TOOLS
    }
    for name in CONFIGURATOR_READ_TOOL_NAMES:
        assert annotations[name] is True
    for name in CONFIGURATOR_WRITE_TOOL_NAMES:
        assert annotations[name] is False


# -------------------------------------------------------------------- guides


@pytest.mark.asyncio
async def test_the_guide_tool_answers_the_index_with_no_argument() -> None:
    caller = session("s1", configurator=True)
    service = service_for(caller, configurator=ConfiguratorStub())
    index = await service.dispatch_tool(caller, "configurator_guide", {})
    assert [entry["id"] for entry in index["guides"]]
    body = await service.dispatch_tool(caller, "configurator_guide", {"id": "orientation"})
    assert body["id"] == "orientation"
    assert body["text"].strip()


@pytest.mark.asyncio
async def test_the_guide_tool_serves_without_a_wired_service() -> None:
    """Guides are files in this build, not runtime state.

    A daemon wired without a configurator service still answers them, which
    keeps the one surface that could explain the misconfiguration reachable when
    something else is misconfigured.
    """
    caller = session("s1", configurator=True)
    service = service_for(caller, configurator=None)
    assert await service.dispatch_tool(caller, "configurator_guide", {})


@pytest.mark.asyncio
async def test_an_unknown_guide_is_a_parameter_error_naming_the_real_ones() -> None:
    caller = session("s1", configurator=True)
    service = service_for(caller, configurator=ConfiguratorStub())
    with pytest.raises(ValueError, match="orientation"):
        await service.dispatch_tool(caller, "configurator_guide", {"id": "nope"})


# --------------------------------------------------------------------- writes


@pytest.mark.asyncio
async def test_a_settings_write_reaches_the_service_verbatim() -> None:
    caller = session("s1", configurator=True)
    stub = ConfiguratorStub()
    service = service_for(caller, configurator=stub)
    result = await service.dispatch_tool(
        caller, "configurator_apply_settings", {"changes": {"theme": "nord"}}
    )
    assert stub.applied == [{"theme": "nord"}]
    assert result["applied"] is True
    assert service.writes == 1


@pytest.mark.asyncio
async def test_an_empty_or_malformed_batch_is_refused_before_the_service() -> None:
    caller = session("s1", configurator=True)
    stub = ConfiguratorStub()
    service = service_for(caller, configurator=stub)
    with pytest.raises(ValueError, match="at least one setting"):
        await service.dispatch_tool(caller, "configurator_apply_settings", {"changes": {}})
    with pytest.raises(ValueError, match="must be an object"):
        await service.dispatch_tool(caller, "configurator_apply_settings", {"changes": "theme"})
    assert stub.applied == []
    # A refused shape is not a write, so it must not be counted as one.
    assert service.writes == 0


@pytest.mark.asyncio
async def test_a_daemon_with_no_configurator_service_says_so_rather_than_faking_it() -> None:
    """Unavailable, never an empty inventory.

    An agent told "no settings exist" would confidently advise nonsense; one told
    the surface is not wired stops.
    """
    from swe_mux.prompt_queue import QueueError

    caller = session("s1", configurator=True)
    service = service_for(caller, configurator=None)
    for name in ("configurator_capabilities", "configurator_diagnostics"):
        with pytest.raises(QueueError) as caught:
            await service.dispatch_tool(caller, name, {})
        assert caught.value.code == "unavailable"


@pytest.mark.asyncio
async def test_every_configurator_read_carries_where_the_caller_is_standing() -> None:
    """The fact whose absence produced a confident, wrong answer (2026-08-24).

    A configurator launched into somebody else's Project cannot derive its own
    Project id, so every per-Project override it meets reads as "this one". The
    session block is how it stops guessing, and it has to be attached by the
    dispatcher rather than asked for - an argument the model has to remember is
    an argument it will omit exactly when it matters.
    """
    caller = session("s1", configurator=True, project="p-cmr")
    stub = ConfiguratorStub()
    service = service_for(caller, configurator=stub)
    result = await service.dispatch_tool(caller, "configurator_capabilities", {})
    assert result["install"]["session"]["project_id"] == "p-cmr"
    assert result["install"]["session"]["project_name"] == "Work"

    await service.dispatch_tool(caller, "configurator_device_settings", {"domain": "commandRail"})
    device = next(call for call in stub.calls if call["tool"] == "device_settings")
    assert device["session_project_id"] == "p-cmr"


@pytest.mark.asyncio
async def test_a_project_tool_defaults_to_the_callers_own_project() -> None:
    caller = session("s1", configurator=True, project="p-cmr")
    stub = ConfiguratorStub()
    service = service_for(caller, configurator=stub)
    await service.dispatch_tool(caller, "configurator_project_settings", {})
    assert stub.calls[-1]["project"] == "p-cmr"
    # An explicit ask still wins: reading another Project is legitimate, guessing
    # which one you are in is not.
    await service.dispatch_tool(caller, "configurator_project_settings", {"project": "other"})
    assert stub.calls[-1]["project"] == "other"


@pytest.mark.asyncio
async def test_the_settings_catalog_is_omitted_until_it_is_asked_for() -> None:
    caller = session("s1", configurator=True)
    stub = ConfiguratorStub()
    service = service_for(caller, configurator=stub)
    await service.dispatch_tool(caller, "configurator_capabilities", {})
    assert "settings" not in stub.calls[-1]["sections"]
    await service.dispatch_tool(
        caller, "configurator_capabilities", {"sections": ["settings"], "settings_query": "theme"}
    )
    assert stub.calls[-1]["sections"] == ("settings",)
    assert stub.calls[-1]["settings_query"] == "theme"


@pytest.mark.asyncio
async def test_a_device_settings_edit_passes_operations_and_the_digest_through() -> None:
    caller = session("s1", configurator=True)
    stub = ConfiguratorStub()
    service = service_for(caller, configurator=stub)
    operations = [
        {"op": "remove_values", "path": "/layouts/mobile/strip/[id=row-2]/items",
         "values": ["up", "down"]}
    ]
    await service.dispatch_tool(
        caller,
        "configurator_edit_device_settings",
        {"domain": "commandRail", "operations": operations, "expect_digest": "d1"},
    )
    call = stub.calls[-1]
    assert call["operations"] == operations
    assert call["expect_digest"] == "d1"
    assert service.writes == 1


@pytest.mark.asyncio
async def test_an_edit_with_no_operations_is_refused_before_the_service() -> None:
    caller = session("s1", configurator=True)
    stub = ConfiguratorStub()
    service = service_for(caller, configurator=stub)
    with pytest.raises(ValueError, match="non-empty array"):
        await service.dispatch_tool(
            caller, "configurator_edit_device_settings", {"domain": "commandRail", "operations": []}
        )
    with pytest.raises(ValueError, match="domain is required"):
        await service.dispatch_tool(
            caller, "configurator_edit_device_settings", {"domain": "", "operations": [{}]}
        )
    assert stub.calls == []
    assert service.writes == 0


@pytest.mark.asyncio
async def test_a_refused_write_comes_back_as_a_typed_result_not_a_protocol_error() -> None:
    """The agent has to be able to tell "bad value" from "server broke"."""
    caller = session("s1", configurator=True)
    stub = ConfiguratorStub(
        apply_result={"applied": False, "errors": {"port": "must be between 1 and 65535"}}
    )
    service = service_for(caller, configurator=stub)
    answer = await service.handle_rpc(
        caller,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "configurator_apply_settings",
                "arguments": {"changes": {"port": 0}},
            },
        },
    )
    assert answer is not None
    assert "error" not in answer
    payload = json.loads(answer["result"]["content"][0]["text"])
    assert payload["applied"] is False
    assert payload["errors"]["port"] == "must be between 1 and 65535"
