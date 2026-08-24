from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

import swe_mux.session_attachments as attachment_store
from swe_mux import app_keys as keys
from swe_mux.models import SessionRecord
from swe_mux.routes.sessions import upload_session_attachment
from swe_mux.session_attachments import (
    attachment_workspace_root,
    sanitize_attachment_name,
    store_session_attachment,
)


def _record(root: Path) -> SessionRecord:
    return SessionRecord(
        id="session-a",
        name="Agent",
        project_id="project-a",
        backend="codex",
        native_session_id="native-a",
        cwd=str(root),
        spawn_cwd=str(root),
        exe="codex",
        args=[],
        state="idle",
    )


def test_attachment_workspace_prefers_project_and_preserves_worktree_scope(tmp_path: Path) -> None:
    project = tmp_path / "project"
    nested = project / "nested"
    worktree = tmp_path / "worktree"
    nested.mkdir(parents=True)
    worktree.mkdir()

    assert attachment_workspace_root(project, nested) == project.resolve()
    assert attachment_workspace_root(project, worktree) == worktree.resolve()


def test_attachment_name_is_single_component_and_bounded() -> None:
    assert sanitize_attachment_name(r"..\Quarter 1: totals.csv") == "Quarter_1__totals.csv"
    assert sanitize_attachment_name(".") == "attachment"
    assert len(sanitize_attachment_name(f"{'x' * 120}.xlsx")) == 96


def test_store_attachment_is_workspace_local_ignored_and_content_typed(tmp_path: Path) -> None:
    stored = store_session_attachment(
        tmp_path,
        "session-a",
        "sales report.csv",
        "text/csv",
        b"month,total\nJan,42\n",
    )

    assert stored.kind == "file"
    assert stored.name == "sales_report.csv"
    assert stored.path.read_bytes() == b"month,total\nJan,42\n"
    assert stored.relative_path.startswith(".swe-mux/attachments/session-a/")
    assert (tmp_path / ".swe-mux" / "attachments" / ".gitignore").read_text() == "*\n"


def test_store_attachment_detects_images_when_browser_mime_is_empty(tmp_path: Path) -> None:
    stored = store_session_attachment(
        tmp_path,
        "session-a",
        "screen.png",
        "",
        b"\x89PNG\r\n\x1a\npixels",
    )
    assert stored.kind == "image"
    assert stored.media_type == "image/png"

    with pytest.raises(ValueError, match="does not match"):
        store_session_attachment(tmp_path, "session-b", "fake.png", "image/png", b"not png")


def test_store_attachment_enforces_session_count_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(attachment_store, "MAX_ATTACHMENTS_PER_SESSION", 1)
    store_session_attachment(tmp_path, "session-a", "first.txt", "text/plain", b"first")
    with pytest.raises(ValueError, match="32-file attachment limit"):
        store_session_attachment(tmp_path, "session-a", "second.txt", "text/plain", b"second")


async def test_attachment_endpoint_copies_file_into_owning_project(tmp_path: Path) -> None:
    record = _record(tmp_path)
    session = SimpleNamespace(record=record)

    class Events:
        def __init__(self) -> None:
            self.emitted: list[tuple[str, dict[str, Any]]] = []

        async def emit(self, event_type: str, **payload: Any) -> None:
            self.emitted.append((event_type, payload))

    events = Events()
    app = web.Application(client_max_size=26 * 1024 * 1024)
    app[keys.SESSIONS] = SimpleNamespace(
        resolve=lambda _sid: session,
        adapters={"codex": SimpleNamespace(media_reference=lambda path: str(path))},
    )
    app[keys.PROJECTS] = SimpleNamespace(
        projects={"project-a": SimpleNamespace(root=str(tmp_path))}
    )
    app[keys.ATTACHMENT_LOCKS] = {}
    app[keys.EVENTS] = events
    app.router.add_post("/api/sessions/{sid}/attachments", upload_session_attachment)

    form = FormData()
    form.add_field(
        "file",
        b"a,b\n1,2\n",
        filename="table.csv",
        content_type="text/csv",
    )
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/sessions/session-a/attachments",
            data=form,
            headers={"X-Mux-User-Gesture": "terminal-attachment"},
        )
        assert response.status == 201
        payload = await response.json()

    path = Path(payload["path"])
    assert path.read_bytes() == b"a,b\n1,2\n"
    assert path.is_relative_to(tmp_path / ".swe-mux" / "attachments" / "session-a")
    assert payload["kind"] == "file"
    assert payload["reference"] == str(path)
    assert events.emitted == [
        (
            "session_attachment_uploaded",
            {
                "session_id": "session-a",
                "attachment_kind": "file",
                "media_type": "text/csv",
                "bytes": 8,
            },
        )
    ]
