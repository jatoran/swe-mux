"""Phase 7.9: the code-structure graph mux MCP reads.

These pin the contract, not the wording: every structural read is gated on the
per-Project `code_graph` opt-in and answers `unsupported`/`disabled` explicitly
rather than a fake empty; results are import-aware (never a same-name false
caller); and every static result is labelled a lower bound. The surface is
read-only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from swe_mux.code_graph import CodeGraphStore, index_project, parsing_available
from swe_mux.mcp import McpService
from swe_mux.prompt_queue import QueueError
from tests.test_mcp import HistoryStub, manager_for
from tests.test_mcp_memory import Tier0Stub, _caller, _fact, _gate, _projects

pytestmark = pytest.mark.skipif(
    not parsing_available(), reason="tree-sitter grammars not available on this host"
)


async def _seed_store(tmp_path: Path) -> CodeGraphStore:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "helper.py").write_text("def make_thing(x):\n    return x + 1\n")
    (tmp_path / "app.py").write_text(
        "from pkg.helper import make_thing\ndef run(n):\n    return make_thing(n)\n"
    )
    store = CodeGraphStore(tmp_path / "graph.db")
    await index_project(store, "p1", str(tmp_path))
    return store


def _graph_service(
    tmp_path: Path,
    store: Any,
    *,
    tier0: Any = None,
    gate: Any = None,
) -> McpService:
    caller = _caller()
    return McpService(
        manager_for(caller),
        HistoryStub(),
        projects=_projects(root=str(tmp_path)),
        tier0=tier0 or Tier0Stub(),
        automation_gate=gate or _gate(frozenset({"code_graph"})),
        code_graph=store,
    )


CODE_GRAPH_TOOLS = (
    ("blast_radius", {"file": "pkg/helper.py"}),
    ("find_definition", {"name": "make_thing"}),
    ("find_callers", {"file": "pkg/helper.py"}),
    ("find_references", {"file": "pkg/helper.py"}),
    ("code_context", {"files": ["app.py"]}),
    ("test_gap", {}),
)


@pytest.mark.parametrize("tool,args", CODE_GRAPH_TOOLS)
async def test_unsupported_without_graph(tool: str, args: dict[str, Any], tmp_path: Path) -> None:
    service = _graph_service(tmp_path, store=None)
    with pytest.raises(QueueError) as excinfo:
        await getattr(service, tool)(_caller(), args)
    assert excinfo.value.code == "unsupported"


@pytest.mark.parametrize("tool,args", CODE_GRAPH_TOOLS)
async def test_disabled_when_not_opted_in(tool: str, args: dict[str, Any], tmp_path: Path) -> None:
    store = await _seed_store(tmp_path)
    service = _graph_service(tmp_path, store, gate=_gate(frozenset()))
    with pytest.raises(QueueError) as excinfo:
        await getattr(service, tool)(_caller(), args)
    assert excinfo.value.code == "disabled"
    assert excinfo.value.payload.get("automation") == "code_graph"
    store.close()


async def test_blast_radius_reports_callers_and_lower_bound(tmp_path: Path) -> None:
    store = await _seed_store(tmp_path)
    service = _graph_service(tmp_path, store)
    result = await service.blast_radius(_caller(), {"file": "pkg/helper.py"})
    assert "lower bound" in result["note"].lower()
    entries = result["blast_radius"]
    assert entries and entries[0]["file"] == "pkg/helper.py"
    caller_paths = {c["path"] for c in entries[0]["callers"]}
    assert "app.py" in caller_paths
    store.close()


async def test_find_definition(tmp_path: Path) -> None:
    store = await _seed_store(tmp_path)
    service = _graph_service(tmp_path, store)
    result = await service.find_definition(_caller(), {"name": "make_thing"})
    assert result["definitions"]
    assert result["definitions"][0]["path"] == "pkg/helper.py"
    store.close()


async def test_find_callers_import_aware(tmp_path: Path) -> None:
    store = await _seed_store(tmp_path)
    service = _graph_service(tmp_path, store)
    result = await service.find_callers(
        _caller(), {"file": "pkg/helper.py", "symbol": "make_thing"}
    )
    callers = {(c["src_path"], c["src_symbol"]) for c in result["callers"]}
    assert ("app.py", "run") in callers
    store.close()


async def test_find_definition_requires_name(tmp_path: Path) -> None:
    store = await _seed_store(tmp_path)
    service = _graph_service(tmp_path, store)
    with pytest.raises(ValueError):
        await service.find_definition(_caller(), {})
    store.close()


async def test_code_context_packs_symbols(tmp_path: Path) -> None:
    store = await _seed_store(tmp_path)
    service = _graph_service(tmp_path, store)
    result = await service.code_context(_caller(), {"files": ["app.py"]})
    ctx = result["context"]
    assert ctx and ctx[0]["file"] == "app.py"
    assert any(s["name"] == "run" for s in ctx[0]["symbols"])
    assert "pkg/helper.py" in ctx[0]["imports"]
    store.close()


async def test_test_gap_flags_untested_change(tmp_path: Path) -> None:
    store = await _seed_store(tmp_path)
    # A recent write to helper.py, whose blast radius (app.py) has no test.
    facts = {
        "p1": [
            _fact(
                "f1",
                kind="file_write_result",
                target="pkg/helper.py",
                session_id="s1",
                run="r1",
            )
        ]
    }
    service = _graph_service(tmp_path, store, tier0=Tier0Stub(project_facts=facts))
    result = await service.test_gap(_caller(), {})
    gaps = {row["file"] for row in result["untested_changes"]}
    assert "pkg/helper.py" in gaps
    assert "lower bound" in result["note"].lower()
    store.close()
