from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux.event_bus import EventBus
from swe_mux.layouts import attach_leaf, layout_terminal_ids
from swe_mux.processes import OwnedProcess, PreviewRegistry, ProcessInspector


class FakeInspector:
    async def snapshot(self, session_id: str) -> dict[str, Any]:
        return {
            "available": True,
            "session_id": session_id,
            "processes": [
                {
                    "pid": 44,
                    "listeners": [
                        {
                            "host": "127.0.0.1",
                            "port": 4321,
                            "loopback": True,
                            "url": "http://127.0.0.1:4321/",
                        }
                    ],
                }
            ],
        }


def fake_sessions() -> Any:
    session = SimpleNamespace(
        record=SimpleNamespace(id="session-a", space_id="default", pid=10)
    )
    return SimpleNamespace(
        sessions={"session-a": session},
        resolve=lambda identity: session if identity == "session-a" else None,
    )


@pytest.mark.asyncio
async def test_preview_registration_requires_loopback_and_session_listener() -> None:
    registry = PreviewRegistry(cast(Any, FakeInspector()), cast(Any, fake_sessions()))
    item = await registry.register("session-a", "http://127.0.0.1:4321/")
    assert item.source == "detected"
    assert item.session_id == "session-a"

    with pytest.raises(ValueError, match="loopback"):
        await registry.register("session-a", "https://example.com/")
    with pytest.raises(ValueError, match="approval"):
        await registry.register("session-a", "http://127.0.0.1:9999/")
    approved = await registry.register(
        "session-a", "http://127.0.0.1:9999/", approved=True
    )
    assert approved.source == "user-approved"


def test_process_action_cannot_target_another_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from swe_mux import processes

    monkeypatch.setattr(processes, "psutil", SimpleNamespace())
    inspector = ProcessInspector(cast(Any, fake_sessions()), EventBus())
    inspector.owned[(55, 1.0)] = OwnedProcess(
        55, 10, "session-b", "server", "server", 1.0, None, 0, 1, [], []
    )
    with pytest.raises(ValueError, match="not owned"):
        inspector._owned_live("session-a", 55)


def test_preview_leaf_attaches_without_losing_terminal() -> None:
    layout = attach_leaf(None, "terminal", "terminal-a")
    layout = attach_leaf(
        layout,
        "preview",
        "preview-a",
        target_id="terminal-a",
        direction="horizontal",
    )
    assert layout_terminal_ids(layout) == ["terminal-a"]
    assert layout["root"]["second"] == {  # type: ignore[index]
        "type": "leaf",
        "kind": "preview",
        "id": "preview-a",
    }


def test_process_reconciliation_records_descendant_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from swe_mux import processes

    monkeypatch.setattr(processes, "psutil", SimpleNamespace())
    inspector = ProcessInspector(cast(Any, fake_sessions()), EventBus())
    child = OwnedProcess(
        55, 10, "session-a", "server", "server", 1.0, None, 0, 1, [], []
    )
    samples = [[child], []]

    def collect(_session: Any) -> list[OwnedProcess]:
        return samples.pop(0)

    monkeypatch.setattr(inspector, "_collect_session", collect)
    inspector._collect_all()
    inspector._collect_all()

    assert inspector.owned[(55, 1.0)].exited_at is not None
