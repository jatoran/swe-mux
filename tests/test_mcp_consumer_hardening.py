"""Roadmap v2 S9: the MCP/automation-consumer hardening batch.

Seven behaviours, each of which used to fail *quietly* - which is why they are
pinned here rather than left to the surfaces' own suites:

- a handler defect impersonating "no such session" (S9.1, audit F24)
- `"test" in path` classifying `latest.py` as a test (S9.2, F26)
- a failed co-change read reported as "nothing co-changes" (S9.3)
- a timed-out transcript parse still burning an executor slot while the agent
  is told to retry, and each retry adding another (S9.4, F24)
- per-key `asyncio.Lock` maps that nothing ever evicted (S9.5, F24)
- `list_sessions` re-serializing the whole page per item popped (S9.6, F24)
- a doc-ownership cache keyed on `max(mtime)`, blind to deletes and renames,
  and bypassed entirely by the MCP caller beside it (S9.7, F22)
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import deterministic_consumers as dc
from swe_mux.code_graph import CodeGraphStore, index_project, parsing_available
from swe_mux.mcp import LIST_MAX_BYTES, PARSE_TIMEOUT_SECONDS, McpService
from swe_mux.project_card import ProjectCardService
from tests.test_mcp import HistoryStub, live_session, manager_for, service_for
from tests.test_mcp_memory import Tier0Stub, _caller, _fact, _gate, _projects

# --------------------------------------------------------------------- S9.2


TEST_PATHS = [
    "tests/test_mcp.py",
    "tests/support/settle.py",
    "src/pkg/test_helper.py",
    "src/pkg/helper_test.py",
    "conftest.py",
    "frontend/src/lib/api.test.ts",
    "frontend/src/lib/api.spec.tsx",
    "frontend/test/renderer/pane-layout.spec.ts",
    "frontend/src/__tests__/App.tsx",
    "internal/server/router_test.go",
    "Tests/Thing.py",
]

NOT_TEST_PATHS = [
    # The whole point of F26: every one of these matched `"test" in path`.
    "src/swe_mux/latest.py",
    "src/swe_mux/contest.py",
    "frontend/src/lib/attestation.ts",
    "frontend/src/components/Protest.tsx",
    "src/attestation/service.py",
    "docs/testing-guide.md",
    "src/swe_mux/mcp.py",
    "frontend/src/lib/api.ts",
    "playwright.renderer.config.ts",
    "",
]


@pytest.mark.parametrize("path", TEST_PATHS)
def test_classifier_accepts_the_test_conventions(path: str) -> None:
    assert dc.is_test_path(path) is True


@pytest.mark.parametrize("path", NOT_TEST_PATHS)
def test_classifier_rejects_names_that_merely_contain_test(path: str) -> None:
    assert dc.is_test_path(path) is False


def test_classifier_accepts_windows_separators() -> None:
    assert dc.is_test_path(r"tests\test_mcp.py") is True
    assert dc.is_test_path(r"src\swe_mux\latest.py") is False


def test_mcp_shares_the_one_classifier() -> None:
    # Both consumers - `blast_radius` covering tests and `test_gap` suppression -
    # read the same predicate, so they cannot drift apart again.
    assert McpService._is_test_path("src/swe_mux/latest.py") is False
    assert McpService._is_test_path("tests/test_mcp.py") is True


# --------------------------------------------------------------------- S9.7


def _write_docs(root: Path, pages: dict[str, str]) -> Path:
    docs = root / ".docs"
    for name, body in pages.items():
        target = docs / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return docs


def _doc(*sources: str) -> str:
    listed = "\n".join(f"- `{source}`" for source in sources)
    return f"# Page\n\n## Key files\n\n{listed}\n"


@pytest.fixture(autouse=True)
def _clear_ownership_cache() -> Any:
    dc._OWNERSHIP_CACHE.clear()
    yield
    dc._OWNERSHIP_CACHE.clear()


def test_ownership_cache_serves_a_repeat_read_without_rebuilding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = _write_docs(tmp_path, {"a.md": _doc("src/swe_mux/mcp.py")})
    builds = 0
    real = dc.build_doc_ownership

    def counted(*args: Any, **kwargs: Any) -> dict[str, tuple[str, ...]]:
        nonlocal builds
        builds += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(dc, "build_doc_ownership", counted)
    first = dc.cached_doc_ownership(docs)
    second = dc.cached_doc_ownership(docs)
    assert first == second == {"src/swe_mux/mcp.py": ("a.md",)}
    assert builds == 1


def test_ownership_fingerprint_sees_a_delete(tmp_path: Path) -> None:
    docs = _write_docs(
        tmp_path,
        {"a.md": _doc("src/swe_mux/mcp.py"), "b.md": _doc("src/swe_mux/session.py")},
    )
    assert set(dc.cached_doc_ownership(docs)) == {
        "src/swe_mux/mcp.py",
        "src/swe_mux/session.py",
    }
    # A delete leaves the newest mtime untouched, so the old max-mtime key served
    # a map that still owned a file no doc mentions.
    (docs / "b.md").unlink()
    assert set(dc.cached_doc_ownership(docs)) == {"src/swe_mux/mcp.py"}


def test_ownership_fingerprint_sees_a_rename(tmp_path: Path) -> None:
    docs = _write_docs(tmp_path, {"a.md": _doc("src/swe_mux/mcp.py")})
    assert dc.cached_doc_ownership(docs) == {"src/swe_mux/mcp.py": ("a.md",)}
    (docs / "a.md").rename(docs / "renamed.md")
    assert dc.cached_doc_ownership(docs) == {"src/swe_mux/mcp.py": ("renamed.md",)}


def test_ownership_fingerprint_sees_a_size_change_at_a_frozen_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows freezes a file's reported mtime while a handle is open, so a doc
    # being written right now can grow without its mtime moving. Size moves.
    docs = _write_docs(tmp_path, {"a.md": _doc("src/swe_mux/mcp.py")})
    assert dc.cached_doc_ownership(docs) == {"src/swe_mux/mcp.py": ("a.md",)}
    frozen = (docs / "a.md").stat().st_mtime_ns
    (docs / "a.md").write_text(
        _doc("src/swe_mux/mcp.py", "src/swe_mux/session.py"), encoding="utf-8"
    )
    real_stat = Path.stat

    def frozen_stat(self: Path, **kwargs: Any) -> Any:
        info = real_stat(self, **kwargs)
        if self.name == "a.md":
            return SimpleNamespace(st_mtime_ns=frozen, st_size=info.st_size)
        return info

    monkeypatch.setattr(Path, "stat", frozen_stat)
    assert set(dc.cached_doc_ownership(docs)) == {
        "src/swe_mux/mcp.py",
        "src/swe_mux/session.py",
    }


def test_ownership_cache_is_bounded(tmp_path: Path) -> None:
    for index in range(dc._OWNERSHIP_CACHE_MAX_ROOTS + 5):
        root = tmp_path / f"p{index}"
        docs = _write_docs(root, {"a.md": _doc("src/swe_mux/mcp.py")})
        dc.cached_doc_ownership(docs)
    assert len(dc._OWNERSHIP_CACHE) <= dc._OWNERSHIP_CACHE_MAX_ROOTS


@pytest.mark.asyncio
async def test_mcp_owning_docs_shares_the_cached_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = _write_docs(tmp_path, {"a.md": _doc("src/swe_mux/mcp.py")})
    assert docs.is_dir()
    builds = 0
    real = dc.build_doc_ownership

    def counted(*args: Any, **kwargs: Any) -> dict[str, tuple[str, ...]]:
        nonlocal builds
        builds += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(dc, "build_doc_ownership", counted)
    service = service_for(live_session("s1", token="tok"))
    first = await service._owning_docs(str(tmp_path), "src/swe_mux/mcp.py")
    second = await service._owning_docs(str(tmp_path), "src/swe_mux/mcp.py")
    assert first == second == ["a.md"]
    # It used to reparse the whole docs tree on every call.
    assert builds == 1

    consumer_view = await asyncio.to_thread(dc.cached_doc_ownership, docs)
    assert consumer_view == {"src/swe_mux/mcp.py": ("a.md",)}
    assert builds == 1


# --------------------------------------------------------------------- S9.3


class _ProvenanceHistory(HistoryStub):
    """A history stub whose `git_provenance` behaves as the caller asks."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, *, boom: bool = False):
        super().__init__()
        self.provenance_rows = rows or []
        self.boom = boom

    async def git_provenance(self, **_kwargs: Any) -> list[dict[str, Any]]:
        if self.boom:
            raise RuntimeError("provenance table is locked")
        return list(self.provenance_rows)


def _graph_service(tmp_path: Path, store: Any, *, history: Any = None, tier0: Any = None):
    return McpService(
        manager_for(_caller()),
        history or HistoryStub(),
        projects=_projects(root=str(tmp_path)),
        tier0=tier0 or Tier0Stub(),
        automation_gate=_gate(frozenset({"code_graph"})),
        code_graph=store,
    )


async def _seed_graph(tmp_path: Path) -> CodeGraphStore:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "helper.py").write_text("def make_thing(x):\n    return x + 1\n")
    # A caller whose name merely contains "test", and a real test beside it.
    (tmp_path / "attestation.py").write_text(
        "from pkg.helper import make_thing\ndef verify(n):\n    return make_thing(n)\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_helper.py").write_text(
        "from pkg.helper import make_thing\ndef test_it():\n    assert make_thing(1) == 2\n"
    )
    store = CodeGraphStore(tmp_path / "graph.db")
    await index_project(store, "p1", str(tmp_path))
    return store


pytestmark_graph = pytest.mark.skipif(
    not parsing_available(), reason="tree-sitter grammars not available on this host"
)


@pytestmark_graph
@pytest.mark.asyncio
async def test_covering_tests_exclude_names_that_merely_contain_test(
    tmp_path: Path,
) -> None:
    store = await _seed_graph(tmp_path)
    try:
        service = _graph_service(tmp_path, store)
        result = await service.blast_radius(_caller(), {"file": "pkg/helper.py"})
        entry = result["blast_radius"][0]
        caller_paths = {row["path"] for row in entry["callers"]}
        assert "attestation.py" in caller_paths
        assert entry["covering_tests"] == ["tests/test_helper.py"]
        assert entry["has_no_covering_test"] is False
    finally:
        store.close()


@pytestmark_graph
@pytest.mark.asyncio
async def test_test_gap_no_longer_suppresses_a_file_named_latest(
    tmp_path: Path,
) -> None:
    store = await _seed_graph(tmp_path)
    try:
        facts = {
            "p1": [
                _fact(
                    "f1",
                    kind="file_write_result",
                    target="pkg/latest.py",
                    session_id="s1",
                    run="r1",
                )
            ]
        }
        service = _graph_service(tmp_path, store, tier0=Tier0Stub(project_facts=facts))
        result = await service.test_gap(_caller(), {})
        # `"test" in "pkg/latest.py"` used to drop this before it was ever
        # checked - a suppressed finding, which is the unsafe direction.
        assert {row["file"] for row in result["untested_changes"]} == {"pkg/latest.py"}
    finally:
        store.close()


@pytestmark_graph
@pytest.mark.asyncio
async def test_blast_radius_reports_a_failed_co_change_read(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = await _seed_graph(tmp_path)
    try:
        service = _graph_service(
            tmp_path, store, history=_ProvenanceHistory(boom=True)
        )
        with caplog.at_level(logging.WARNING, logger="swe_mux.mcp"):
            result = await service.blast_radius(_caller(), {"file": "pkg/helper.py"})
        entry = result["blast_radius"][0]
        assert entry["co_change_available"] is False
        assert entry["co_change_unavailable_reason"] == "provenance_read_failed"
        assert entry["co_changed_files"] == []
        assert "unknown, not empty" in result["note"]
        assert any(
            "co-change read failed" in record.getMessage() for record in caplog.records
        )
    finally:
        store.close()


@pytestmark_graph
@pytest.mark.asyncio
async def test_blast_radius_reports_an_absent_provenance_reader(tmp_path: Path) -> None:
    store = await _seed_graph(tmp_path)
    try:
        service = _graph_service(tmp_path, store, history=HistoryStub())
        result = await service.blast_radius(_caller(), {"file": "pkg/helper.py"})
        entry = result["blast_radius"][0]
        assert entry["co_change_available"] is False
        assert entry["co_change_unavailable_reason"] == "provenance_reader_unavailable"
    finally:
        store.close()


@pytestmark_graph
@pytest.mark.asyncio
async def test_blast_radius_calls_an_empty_co_change_net_empty(tmp_path: Path) -> None:
    store = await _seed_graph(tmp_path)
    try:
        service = _graph_service(tmp_path, store, history=_ProvenanceHistory([]))
        result = await service.blast_radius(_caller(), {"file": "pkg/helper.py"})
        entry = result["blast_radius"][0]
        # A read that happened and found nothing is a *different answer* from a
        # read that did not happen, and only this one is safe to act on.
        assert entry["co_change_available"] is True
        assert "co_change_unavailable_reason" not in entry
        assert "unknown, not empty" not in result["note"]
    finally:
        store.close()


@pytestmark_graph
@pytest.mark.asyncio
async def test_blast_radius_reports_a_bare_file_when_co_change_is_unknown(
    tmp_path: Path,
) -> None:
    store = await _seed_graph(tmp_path)
    try:
        service = _graph_service(
            tmp_path, store, history=_ProvenanceHistory(boom=True)
        )
        # Nothing calls it, no doc owns it: under a working provenance read this
        # entry is skipped, but "we learned nothing" must still be said.
        result = await service.blast_radius(_caller(), {"file": "attestation.py"})
        entry = result["blast_radius"][0]
        assert entry["file"] == "attestation.py"
        assert entry["callers"] == []
        assert entry["co_change_available"] is False
    finally:
        store.close()


# --------------------------------------------------------------------- S9.1


@pytest.mark.asyncio
async def test_a_handler_defect_is_an_internal_error_not_a_miss(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caller = live_session("s1", token="tok")
    service = service_for(caller)

    async def broken(_caller_: Any, _args: dict[str, Any]) -> dict[str, Any]:
        return {"sessions": {}["missing"]}

    service.list_sessions = broken  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR, logger="swe_mux.mcp"):
        response = await service.handle_rpc(
            caller,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "list_sessions", "arguments": {}},
            },
        )
    assert response is not None
    # It used to answer "no such session", which an agent acts on as fact.
    assert "result" not in response
    assert response["error"]["code"] == -32603
    assert "list_sessions" in response["error"]["message"]
    assert any(record.exc_info for record in caplog.records)


@pytest.mark.asyncio
async def test_a_scope_miss_still_answers_not_found() -> None:
    caller = live_session("s1", token="tok")
    service = service_for(caller)
    response = await service.handle_rpc(
        caller,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_session", "arguments": {"session_id": "nope"}},
        },
    )
    assert response is not None
    assert response["result"]["isError"] is True
    assert "no such session" in response["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_an_ambiguous_identity_is_an_invalid_argument() -> None:
    # Two Projects, one session name: answering "not found" would be a lie the
    # caller cannot act on, so the candidates are named at -32602.
    first = live_session("s1", token="tok", project_id="p1", scope_id="scope-1")
    second = live_session("s2", project_id="p2", scope_id="scope-2")
    third = live_session("s3", project_id="p3", scope_id="scope-3")
    second.record.name = "backend"
    third.record.name = "backend"
    service = service_for(first, second, third)
    response = await service.handle_rpc(
        first,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "get_session",
                "arguments": {"session_id": "backend", "project": "fleet"},
            },
        },
    )
    assert response is not None
    assert response["error"]["code"] == -32602
    assert "matches 2 sessions" in response["error"]["message"]


# --------------------------------------------------------------------- S9.4


class _SlowParser:
    """A `transcript_message_page` stand-in that blocks until released."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.signatures: list[tuple[Any, ...]] = []

    def __call__(self, path: Any, backend: str, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        self.signatures.append((str(path), backend, kwargs.get("direction")))
        self.started.set()
        self.release.wait(timeout=10)
        return {"messages": [], "next_anchor": None, "abandoned_messages": 0}


@pytest.fixture
def slow_parser(monkeypatch: pytest.MonkeyPatch) -> Any:
    parser = _SlowParser()
    monkeypatch.setattr("swe_mux.mcp.transcript_message_page", parser)
    yield parser
    parser.release.set()


@pytest.mark.asyncio
async def test_a_timed_out_parse_is_not_restarted_by_the_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slow_parser: Any
) -> None:
    monkeypatch.setattr("swe_mux.mcp.PARSE_TIMEOUT_SECONDS", 0.05)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n",
        encoding="utf-8",
    )
    caller = live_session("s1", token="tok", transcript=transcript)
    service = service_for(caller)

    for _attempt in range(3):
        with pytest.raises(RuntimeError, match="transient"):
            await service.read_transcript(caller, {"session_id": "s1"})
    # One parse, three retries. Each retry used to add another worker thread to
    # the shared executor while telling the agent to try again.
    assert slow_parser.calls == 1
    assert service.status()["transcript_parses"]["in_flight"] == 1
    assert service.status()["transcript_parses"]["timeouts"] >= 3

    slow_parser.release.set()
    for _ in range(200):
        if service.status()["transcript_parses"]["in_flight"] == 0:
            break
        await asyncio.sleep(0.01)
    assert service.status()["transcript_parses"]["in_flight"] == 0

    # Once the flight retires, the next read parses for real.
    result = await service.read_transcript(caller, {"session_id": "s1"})
    assert result["message_count"] == 0
    assert slow_parser.calls == 2


@pytest.mark.asyncio
async def test_concurrent_identical_reads_share_one_parse(
    tmp_path: Path, slow_parser: Any
) -> None:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n",
        encoding="utf-8",
    )
    caller = live_session("s1", token="tok", transcript=transcript)
    service = service_for(caller)

    first = asyncio.ensure_future(service.read_transcript(caller, {"session_id": "s1"}))
    await asyncio.to_thread(slow_parser.started.wait, 5)
    second = asyncio.ensure_future(
        service.read_transcript(caller, {"session_id": "s1"})
    )
    await asyncio.sleep(0)
    slow_parser.release.set()
    results = await asyncio.gather(first, second)
    assert slow_parser.calls == 1
    assert [item["message_count"] for item in results] == [0, 0]


@pytest.mark.asyncio
async def test_a_second_page_of_a_parsing_transcript_is_refused_not_stacked(
    tmp_path: Path, slow_parser: Any
) -> None:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n",
        encoding="utf-8",
    )
    caller = live_session("s1", token="tok", transcript=transcript)
    service = service_for(caller)

    running = asyncio.ensure_future(
        service.read_transcript(caller, {"session_id": "s1", "from": "tail"})
    )
    await asyncio.to_thread(slow_parser.started.wait, 5)
    with pytest.raises(RuntimeError, match="transient"):
        await service.read_transcript(caller, {"session_id": "s1", "from": "head"})
    assert slow_parser.calls == 1
    assert service.status()["transcript_parses"]["refusals"] == 1
    slow_parser.release.set()
    await running


@pytest.mark.asyncio
async def test_the_deadline_belongs_to_the_flight_not_the_waiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slow_parser: Any
) -> None:
    monkeypatch.setattr("swe_mux.mcp.PARSE_TIMEOUT_SECONDS", 0.3)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n",
        encoding="utf-8",
    )
    caller = live_session("s1", token="tok", transcript=transcript)
    service = service_for(caller)

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="transient"):
        await service.read_transcript(caller, {"session_id": "s1"})
    with pytest.raises(RuntimeError, match="transient"):
        await service.read_transcript(caller, {"session_id": "s1"})
    # Two waits, one deadline: the retry inherits what is left of the flight's
    # window rather than buying a fresh one, so the total is bounded by it.
    assert time.monotonic() - started < 2 * 0.3


def test_parse_timeout_default_is_unchanged() -> None:
    assert PARSE_TIMEOUT_SECONDS == 2.0


# --------------------------------------------------------------------- S9.6


@pytest.mark.asyncio
async def test_list_sessions_size_accounting_is_exact() -> None:
    caller = live_session("s1", token="tok")
    others = [live_session(f"s{index:03d}") for index in range(2, 30)]
    service = service_for(caller, *others)
    result = await service.list_sessions(caller, {"limit": 25})
    # The fit is computed from per-item sizes plus the envelope; if that
    # arithmetic ever drifts from what `json.dumps` actually produces, the page
    # bound silently stops being a bound.
    assert len(json.dumps(result, ensure_ascii=False).encode("utf-8")) <= LIST_MAX_BYTES
    assert result["count"] == 25


@pytest.mark.asyncio
async def test_list_sessions_trims_until_the_page_fits() -> None:
    caller = live_session("s1", token="tok")
    fat = []
    for index in range(2, 26):
        session = live_session(f"s{index:03d}")
        session.record.name = "n" * 4_000
        session.record.auto_named = False
        fat.append(session)
    service = service_for(caller, *fat)
    result = await service.list_sessions(caller, {"limit": 25})
    encoded = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    assert encoded <= LIST_MAX_BYTES
    assert 0 < result["count"] < 25
    assert result["has_more"] is True
    assert result["next_cursor"]


@pytest.mark.asyncio
async def test_list_sessions_measures_each_item_once() -> None:
    caller = live_session("s1", token="tok")
    fat = []
    for index in range(2, 26):
        session = live_session(f"s{index:03d}")
        session.record.name = "n" * 4_000
        session.record.auto_named = False
        fat.append(session)
    service = service_for(caller, *fat)

    real_dumps = json.dumps
    payload_dumps = 0

    def counting_dumps(obj: Any, **kwargs: Any) -> str:
        nonlocal payload_dumps
        if isinstance(obj, dict) and "session_id" in obj:
            payload_dumps += 1
        return real_dumps(obj, **kwargs)

    import swe_mux.mcp as mcp_module

    original = mcp_module.json.dumps
    mcp_module.json.dumps = counting_dumps  # type: ignore[assignment]
    try:
        await service.list_sessions(caller, {"limit": 25})
    finally:
        mcp_module.json.dumps = original  # type: ignore[assignment]

    # 25 items, measured once each. The re-serialize-the-world loop measured
    # every surviving item again on every pop.
    assert payload_dumps == 25


# --------------------------------------------------------------------- S9.5


class _Bus:
    def __init__(self) -> None:
        self.queues: list[asyncio.Queue[Any]] = []

    def subscribe(self, *, name: str = "") -> asyncio.Queue[Any]:
        queue: asyncio.Queue[Any] = asyncio.Queue()
        self.queues.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Any]) -> None:
        if queue in self.queues:
            self.queues.remove(queue)

    async def emit(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _scan_service(sessions: dict[str, Any]) -> Any:
    from swe_mux.scan_timeline import ScanTimelineService

    async def resolve(_session_id: str) -> None:
        return None

    return ScanTimelineService(
        store=SimpleNamespace(),
        tier0=SimpleNamespace(),
        sessions=SimpleNamespace(sessions=sessions),
        events=_Bus(),
        config=SimpleNamespace(scan_timeline_enabled=False),
        provider=SimpleNamespace(),
        project_contexts=SimpleNamespace(),
        resolve_context=resolve,
    )


@pytest.mark.asyncio
async def test_scan_timeline_evicts_the_lock_of_an_ended_session() -> None:
    sessions: dict[str, Any] = {"s1": SimpleNamespace(), "s2": SimpleNamespace()}
    service = _scan_service(sessions)
    for sid in ("s1", "s2"):
        service._locks[sid] = asyncio.Lock()
        service._catchup_depth[sid] = 2

    # The sweep is liveness-gated: a session the manager still holds keeps its
    # lock, because a scan can still be scheduled for it.
    service._evict_dead_sessions()
    assert set(service._locks) == {"s1", "s2"}

    # `_forget_session` is what the final scan calls for its own session, so it
    # does not re-ask whether the session is live - the exit event already said.
    service._forget_session("s1")
    assert set(service._locks) == {"s2"}
    assert set(service._catchup_depth) == {"s2"}

    # Anything a chained catch-up or a race left behind is picked up by the
    # next exit's sweep.
    sessions.pop("s2")
    service._evict_dead_sessions()
    assert service._locks == {}
    assert service._catchup_depth == {}


@pytest.mark.asyncio
async def test_scan_timeline_never_drops_a_held_lock() -> None:
    service = _scan_service({})
    lock = asyncio.Lock()
    service._locks["s1"] = lock
    async with lock:
        service._evict_dead_sessions()
        # Dropping it would let the next caller build a second lock while this
        # one is still held, which is the whole point of having it.
        assert service._locks["s1"] is lock
    service._evict_dead_sessions()
    assert service._locks == {}


@pytest.mark.asyncio
async def test_scan_timeline_keeps_a_lock_a_pending_catchup_will_take() -> None:
    service = _scan_service({})

    async def pending() -> None:
        await asyncio.sleep(10)

    task = asyncio.ensure_future(pending())
    service._locks["s1"] = asyncio.Lock()
    service._debounce["s1"] = task
    try:
        service._evict_dead_sessions()
        assert "s1" in service._locks
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_assistant_retires_a_dialog_lock_when_its_turn_ends() -> None:
    from swe_mux.assistant import AssistantService

    service = object.__new__(AssistantService)
    service._dialog_locks = {}
    service._turn_tasks = {}
    service._queued = {}
    service._interrupts = set()
    service._queue_starters = set()

    done: asyncio.Task[None] = asyncio.ensure_future(asyncio.sleep(0))
    await done
    service._dialog_locks["d1"] = asyncio.Lock()
    service._interrupts.add("d1")
    service._turn_finished("d1", done)
    assert service._dialog_locks == {}
    assert service._interrupts == set()


@pytest.mark.asyncio
async def test_assistant_keeps_a_dialog_lock_while_a_turn_waits() -> None:
    from swe_mux.assistant import AssistantService

    service = object.__new__(AssistantService)
    service._dialog_locks = {"d1": asyncio.Lock()}
    service._turn_tasks = {}
    service._queued = {"d1": SimpleNamespace(turn_id="t2", text="x", client_context={})}
    service._interrupts = set()
    service._retire_dialog_lock("d1")
    assert "d1" in service._dialog_locks


@pytest.mark.asyncio
async def test_project_card_forgets_a_removed_project() -> None:
    async def resolve_session(_sid: str) -> None:
        return None

    async def resolve_project(_root: str) -> bool:
        return True

    service = ProjectCardService(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        resolve_session=resolve_session,
        resolve_project=resolve_project,
    )
    lock = asyncio.Lock()
    service._locks["p1"] = lock
    service._failures["p1"] = (0.0, "boom")
    async with lock:
        service.forget_project("p1")
        assert "p1" in service._locks
    service.forget_project("p1")
    assert service._locks == {}
    assert service._failures == {}
