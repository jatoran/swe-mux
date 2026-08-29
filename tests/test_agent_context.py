from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux.agent_context import AgentContextConflict, AgentContextService
from swe_mux.event_bus import EventBus
from swe_mux.harness import HARNESSES, descriptor, instruction_harnesses
from swe_mux.routes import agent_context as agent_context_routes
from swe_mux.routes.agent_context import (
    get_agent_context,
    get_agent_context_source,
    link_agent_context,
    preview_agent_context_link,
    preview_agent_context_sync,
    restore_agent_context,
    reveal_agent_context_source,
    sync_agent_context,
    unlink_agent_context,
)
from swe_mux.server import error_middleware


def revision(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_inventory_is_project_scoped_typed_and_tracks_run_start(tmp_path: Path) -> None:
    root = tmp_path / "project"
    home = tmp_path / "home"
    memory = home / "memories"
    root.mkdir()
    memory.mkdir(parents=True)
    (root / "CLAUDE.md").write_text("# Shared\n", encoding="utf-8", newline="\n")
    (root / "AGENTS.md").write_bytes(b"# Shared\r\n")
    (memory / "MEMORY.md").write_text("# Learned\n", encoding="utf-8")
    (memory / "testing.md").write_bytes(b"Prefer focused tests.\n")
    (home / ".claude").mkdir()
    (home / ".claude" / "CLAUDE.md").write_text("# Global Claude\n", encoding="utf-8")
    (home / ".claude" / "settings.json").write_text(
        '{"autoMemoryDirectory": "~/memories"}', encoding="utf-8"
    )
    (home / ".codex").mkdir()
    (home / ".codex" / "AGENTS.md").write_text("# Global Codex\n", encoding="utf-8")
    (home / ".codex" / "config.toml").write_text("[features]\nmemories = true\n", encoding="utf-8")

    service = AgentContextService(tmp_path / "backups", home=home)
    service.capture_project(root)
    inventory = service.inventory("project-one", "Project One", root)

    assert inventory["project"] == {"id": "project-one", "name": "Project One"}
    assert inventory["instructions"]["comparison"] == "in_sync"
    assert [item["label"] for item in inventory["instructions"]["items"]] == [
        "CLAUDE.md",
        "AGENTS.md",
    ]
    assert not any(item["changed_since_start"] for item in inventory["instructions"]["items"])
    assert all(item["revealable"] for item in inventory["instructions"]["items"])
    # Derived from the registry, not pinned to a pair: every harness declaring an
    # instruction file owes a global entry, and a new one joins this list by
    # declaring `global_instruction_parts` rather than by editing this test.
    assert [item["label"] for item in inventory["global_instructions"]["items"]] == [
        "~/" + "/".join(descriptor(name).global_instruction_parts or ())
        for name in instruction_harnesses()
    ]
    assert [item["label"] for item in inventory["global_instructions"]["items"]][:2] == [
        "~/.claude/CLAUDE.md",
        "~/.codex/AGENTS.md",
    ]
    assert all(item["scope"] == "global" for item in inventory["global_instructions"]["items"])
    # A project-root instruction file is shared, so its readers are named in full:
    # AGENTS.md is read by every harness that is not Claude.
    project_readers = {
        item["label"]: item["readers"] for item in inventory["instructions"]["items"]
    }
    assert project_readers["CLAUDE.md"] == ["claude"]
    assert project_readers["AGENTS.md"] == [
        name for name in instruction_harnesses() if name != "claude"
    ]
    # Only the two globals this fixture created exist; the rest are reported as a
    # stated absence rather than omitted, which is what makes a missing harness
    # instruction file visible instead of invisible.
    global_items = {item["label"]: item for item in inventory["global_instructions"]["items"]}
    assert global_items["~/.claude/CLAUDE.md"]["revealable"] is True
    assert global_items["~/.codex/AGENTS.md"]["revealable"] is True
    assert all(
        item["status"] == "missing" and item["revealable"] is False
        for label, item in global_items.items()
        if label not in {"~/.claude/CLAUDE.md", "~/.codex/AGENTS.md"}
    )
    providers = {provider["id"]: provider for provider in inventory["providers"]}
    assert list(providers) == list(HARNESSES)
    claude = providers["claude"]
    codex = providers["codex"]
    assert claude["status"] == "available"
    assert [item["label"] for item in claude["items"]] == ["MEMORY.md", "testing.md"]
    assert claude["item_count"] == 2
    assert codex["status"] == "unsupported"
    assert all(
        providers[name]["status"] == "unsupported"
        for name in HARNESSES
        if name not in {"claude", "codex"}
    )
    assert {option["direction"] for option in inventory["sync_options"]} == {
        "instruction:claude->instruction:codex",
        "instruction:codex->instruction:claude",
    }

    source = service.read_source(root, claude["items"][1]["id"])
    assert source["source"]["kind"] == "memory"
    assert source["text"] == "Prefer focused tests.\n"
    global_source = service.read_source(root, "instruction:global:codex")
    assert global_source["source"]["scope"] == "global"
    assert global_source["source"]["label"] == "~/.codex/AGENTS.md"
    assert global_source["text"].replace("\r\n", "\n") == "# Global Codex\n"
    assert (
        service.source_path(root, "instruction:global:codex")
        == (home / ".codex" / "AGENTS.md").resolve()
    )

    (root / "AGENTS.md").write_text("changed\n", encoding="utf-8")
    changed = service.inventory("project-one", "Project One", root)
    assert changed["instructions"]["comparison"] == "different"
    assert changed["instructions"]["items"][1]["changed_since_start"] is True


def test_the_inventory_is_memoized_on_the_files_it_reads(tmp_path: Path) -> None:
    """Every open re-read and re-normalized every instruction file, with nothing retained.

    The cache is keyed on a stat signature over exactly those files, so it invalidates
    when they move and never when they do not - and `refresh` bypasses it outright,
    which is what makes "rescan" mean rescan rather than "ask again politely".
    """
    root = tmp_path / "project"
    home = tmp_path / "home"
    root.mkdir()
    (home / ".claude").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("# One\n", encoding="utf-8", newline="\n")
    (root / "AGENTS.md").write_text("# One\n", encoding="utf-8", newline="\n")

    reads: list[str] = []
    service = AgentContextService(tmp_path / "backups", home=home)
    original = service._instruction_item

    def counted(source_root: Path, source_id: str) -> dict[str, object]:
        reads.append(source_id)
        return original(source_root, source_id)

    service._instruction_item = counted  # type: ignore[method-assign]

    first = service.inventory("p", "Project", root)
    taken = len(reads)
    assert taken > 0
    second = service.inventory("p", "Project", root)
    assert second is first
    assert len(reads) == taken

    # `refresh` reads again even though nothing moved.
    service.inventory("p", "Project", root, refresh=True)
    assert len(reads) == taken * 2

    # And an edit invalidates on its own. Different size, so the signature moves whatever
    # the filesystem's mtime resolution happens to be.
    (root / "AGENTS.md").write_text("# One, revised\n", encoding="utf-8", newline="\n")
    changed = service.inventory("p", "Project", root)
    assert changed is not first
    assert changed["instructions"]["comparison"] == "different"


def test_repo_derived_claude_memory_uses_primary_checkout(tmp_path: Path) -> None:
    root = tmp_path / "worktree"
    primary = tmp_path / "primary checkout"
    home = tmp_path / "home"
    root.mkdir()
    primary.mkdir()
    key = str(primary.resolve())
    key = "".join(
        character if character.isalnum() or character in "_-" else "-" for character in key
    )
    memory = home / ".claude" / "projects" / key / "memory"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text("shared across worktrees\n", encoding="utf-8")

    service = AgentContextService(
        tmp_path / "backups",
        home=home,
        repository_root=lambda _root: primary,
    )
    inventory = service.inventory("project", "Project", root)

    assert inventory["providers"][0]["status"] == "available"
    assert inventory["providers"][0]["items"][0]["label"] == "MEMORY.md"


def test_sync_is_preview_guarded_atomic_and_reversible(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source_data = b"# Claude\nnew instruction\n"
    target_data = b"# Agents\r\nold instruction\r\n"
    (root / "CLAUDE.md").write_bytes(source_data)
    (root / "AGENTS.md").write_bytes(target_data)
    service = AgentContextService(tmp_path / "backups", home=tmp_path / "home")

    preview = service.preview_sync(root, "claude_to_agents")
    assert preview["source"]["revision"] == revision(source_data)
    assert preview["target"]["revision"] == revision(target_data)
    assert preview["in_sync"] is False
    assert "-# Agents" in preview["diff"]
    assert "+# Claude" in preview["diff"]

    result = service.sync(
        "project",
        root,
        "claude_to_agents",
        preview["source"]["revision"],
        preview["target"]["revision"],
    )
    synced = (root / "AGENTS.md").read_bytes()
    assert synced == b"# Claude\r\nnew instruction\r\n"
    assert result["backup"]["existed"] is True

    restored = service.restore("project", root, result["backup"]["id"], revision(synced))
    assert restored["target"] == "AGENTS.md"
    assert (root / "AGENTS.md").read_bytes() == target_data
    assert len(service.inventory("project", "Project", root)["backups"]) == 2


def test_sync_refuses_stale_preview_and_restore_can_remove_created_target(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "CLAUDE.md").write_text("source\n", encoding="utf-8")
    service = AgentContextService(tmp_path / "backups", home=tmp_path / "home")

    preview = service.preview_sync(root, "claude_to_agents")
    created = service.sync(
        "project",
        root,
        "claude_to_agents",
        preview["source"]["revision"],
        preview["target"]["revision"],
    )
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == "source\n"
    service.restore(
        "project",
        root,
        created["backup"]["id"],
        created["revision"],
    )
    assert not (root / "AGENTS.md").exists()

    (root / "AGENTS.md").write_text("first\n", encoding="utf-8")
    stale = service.preview_sync(root, "claude_to_agents")
    (root / "AGENTS.md").write_text("second\n", encoding="utf-8")
    with pytest.raises(AgentContextConflict, match="changed since"):
        service.sync(
            "project",
            root,
            "claude_to_agents",
            stale["source"]["revision"],
            stale["target"]["revision"],
        )
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == "second\n"


def test_link_is_guarded_visible_unlinkable_and_reversible(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    claude_data = b"# Claude\nclaude-only\n"
    agents_data = b"# Shared\nall agents\n"
    (root / "CLAUDE.md").write_bytes(claude_data)
    (root / "AGENTS.md").write_bytes(agents_data)
    service = AgentContextService(tmp_path / "backups", home=tmp_path / "home")

    stale = service.preview_link(root, "agents_to_claude")
    (root / "CLAUDE.md").write_bytes(b"changed after preview\n")
    with pytest.raises(AgentContextConflict, match="changed since"):
        service.link(
            "project",
            root,
            "agents_to_claude",
            stale["source"]["revision"],
            stale["target"]["revision"],
        )
    assert (root / "CLAUDE.md").read_bytes() == b"changed after preview\n"

    (root / "CLAUDE.md").write_bytes(claude_data)
    preview = service.preview_link(root, "agents_to_claude")
    assert preview["source"]["label"] == "AGENTS.md"
    assert preview["target"]["label"] == "CLAUDE.md"
    assert preview["already_linked"] is False
    try:
        linked = service.link(
            "project",
            root,
            "agents_to_claude",
            preview["source"]["revision"],
            preview["target"]["revision"],
        )
    except ValueError as exc:
        if "Could not create a symbolic link" in str(exc):
            pytest.skip(str(exc))
        raise

    alias = root / "CLAUDE.md"
    canonical = root / "AGENTS.md"
    assert alias.is_symlink()
    assert os.readlink(alias) == "AGENTS.md"
    assert linked["backup"]["entry_kind"] == "regular"
    assert linked["backup"]["revision"] == revision(claude_data)

    inventory = service.inventory("project", "Project", root)
    assert inventory["instructions"]["comparison"] == "linked"
    claude = inventory["instructions"]["items"][0]
    assert claude["status"] == "available"
    assert claude["link_target"] == "AGENTS.md"
    assert claude["link_target_id"] == "instruction:codex"
    assert service.read_source(root, "instruction:claude")["text"] == agents_data.decode()
    assert service.source_path(root, "instruction:claude") == canonical.resolve()

    unlinked = service.unlink("project", root, "instruction:claude", linked["revision"])
    assert not alias.is_symlink()
    assert alias.read_bytes() == agents_data
    assert unlinked["backup"]["entry_kind"] == "symlink"

    relinked = service.restore("project", root, unlinked["backup"]["id"], unlinked["revision"])
    assert alias.is_symlink()
    assert os.readlink(alias) == "AGENTS.md"
    restored = service.restore("project", root, linked["backup"]["id"], relinked["revision"])
    assert not alias.is_symlink()
    assert alias.read_bytes() == claude_data
    assert restored["revision"] == revision(claude_data)


def test_unknown_instruction_link_remains_unsupported(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("canonical\n", encoding="utf-8")
    try:
        os.symlink(outside, root / "AGENTS.md", target_is_directory=False)
    except OSError as exc:
        pytest.skip(f"host cannot create test symlink: {exc}")
    service = AgentContextService(tmp_path / "backups", home=tmp_path / "home")

    inventory = service.inventory("project", "Project", root)
    agents = inventory["instructions"]["items"][1]
    assert agents["status"] == "unsupported"
    assert "outside the declared" in agents["detail"]
    with pytest.raises(ValueError, match="outside the declared"):
        service.read_source(root, "instruction:codex")
    with pytest.raises(ValueError, match="outside the declared"):
        service.preview_link(root, "claude_to_agents")


def test_link_capability_failure_keeps_files_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "CLAUDE.md").write_text("canonical\n", encoding="utf-8")
    original = b"independent\n"
    (root / "AGENTS.md").write_bytes(original)
    service = AgentContextService(tmp_path / "backups", home=tmp_path / "home")
    preview = service.preview_link(root, "claude_to_agents")

    def refused(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("symbolic links unavailable")

    monkeypatch.setattr(os, "symlink", refused)
    with pytest.raises(ValueError, match="Could not create a symbolic link"):
        service.link(
            "project",
            root,
            "claude_to_agents",
            preview["source"]["revision"],
            preview["target"]["revision"],
        )
    assert not (root / "AGENTS.md").is_symlink()
    assert (root / "AGENTS.md").read_bytes() == original
    assert service.inventory("project", "Project", root)["backups"] == []


def test_sources_are_allowlisted_and_bounded(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "CLAUDE.md").write_bytes(b"x" * (512 * 1024 + 1))
    service = AgentContextService(tmp_path / "backups", home=tmp_path / "home")

    inventory = service.inventory("project", "Project", root)
    assert inventory["instructions"]["items"][0]["status"] == "too_large"
    assert inventory["instructions"]["items"][0]["revealable"] is True
    assert service.source_path(root, "instruction:claude") == (root / "CLAUDE.md").resolve()
    with pytest.raises(ValueError, match="larger than"):
        service.read_source(root, "instruction:claude")
    with pytest.raises(ValueError, match="unknown"):
        service.read_source(root, "memory:claude:Li4vZXNjYXBlLm1k")
    with pytest.raises(ValueError, match="unknown"):
        service.read_source(root, "file:anywhere")
    with pytest.raises(ValueError, match="unknown"):
        service.read_source(root, "instruction:global:../../escape")
    with pytest.raises(ValueError, match="unknown"):
        service.source_path(root, "file:anywhere")


async def test_agent_context_http_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "project"
    home = tmp_path / "home"
    root.mkdir()
    (root / "CLAUDE.md").write_text("shared\n", encoding="utf-8")
    (home / ".claude").mkdir(parents=True)
    global_claude = home / ".claude" / "CLAUDE.md"
    global_claude.write_text("global\n", encoding="utf-8")
    project = SimpleNamespace(id="project-one", root=str(root), name="Project One")
    service = AgentContextService(tmp_path / "backups", home=home)
    revealed: list[Path] = []
    monkeypatch.setattr(
        agent_context_routes, "open_in_file_manager", lambda path: revealed.append(Path(path))
    )
    app = web.Application(middlewares=[error_middleware])
    app[keys.PROJECTS] = SimpleNamespace(projects={project.id: project})
    app[keys.AGENT_CONTEXT] = service
    app[keys.EVENTS] = EventBus()
    app.router.add_get("/projects/{project_id}/agent-context", get_agent_context)
    app.router.add_get(
        "/projects/{project_id}/agent-context/sources/{source_id}", get_agent_context_source
    )
    app.router.add_post(
        "/projects/{project_id}/agent-context/sources/{source_id}/reveal",
        reveal_agent_context_source,
    )
    app.router.add_post(
        "/projects/{project_id}/agent-context/sync/preview", preview_agent_context_sync
    )
    app.router.add_post("/projects/{project_id}/agent-context/sync", sync_agent_context)
    app.router.add_post(
        "/projects/{project_id}/agent-context/link/preview",
        preview_agent_context_link,
    )
    app.router.add_post("/projects/{project_id}/agent-context/link", link_agent_context)
    app.router.add_post("/projects/{project_id}/agent-context/unlink", unlink_agent_context)
    app.router.add_post("/projects/{project_id}/agent-context/restore", restore_agent_context)

    async with TestClient(TestServer(app)) as client:
        inventory_response = await client.get("/projects/project-one/agent-context")
        inventory_payload = await inventory_response.json()
        read_response = await client.get(
            "/projects/project-one/agent-context/sources/instruction:claude"
        )
        read_payload = await read_response.json()
        reveal_response = await client.post(
            "/projects/project-one/agent-context/sources/instruction:claude/reveal"
        )
        global_reveal_response = await client.post(
            "/projects/project-one/agent-context/sources/instruction:global:claude/reveal"
        )
        preview_response = await client.post(
            "/projects/project-one/agent-context/sync/preview",
            json={"direction": "claude_to_agents"},
        )
        preview = await preview_response.json()
        link_preview_response = await client.post(
            "/projects/project-one/agent-context/link/preview",
            json={"direction": "claude_to_agents"},
        )
        link_preview = await link_preview_response.json()
        sync_response = await client.post(
            "/projects/project-one/agent-context/sync",
            json={
                "direction": "claude_to_agents",
                "source_revision": preview["source"]["revision"],
                "target_revision": preview["target"]["revision"],
            },
        )
        synced = await sync_response.json()
        conflict_response = await client.post(
            "/projects/project-one/agent-context/sync",
            json={
                "direction": "claude_to_agents",
                "source_revision": preview["source"]["revision"],
                "target_revision": "missing",
            },
        )
        conflict_payload = await conflict_response.json()
        restore_response = await client.post(
            "/projects/project-one/agent-context/restore",
            json={"backup_id": synced["backup"]["id"], "target_revision": synced["revision"]},
        )

    assert inventory_response.status == 200
    assert inventory_payload["instructions"]["comparison"] == "missing"
    # One global file exists in this fixture; every other declaring harness reports a
    # stated absence. The count follows the registry rather than a pinned pair.
    assert [item["status"] for item in inventory_payload["global_instructions"]["items"]] == [
        "available",
        *["missing"] * (len(instruction_harnesses()) - 1),
    ]
    assert read_response.status == 200
    assert read_payload["text"].replace("\r\n", "\n") == "shared\n"
    assert reveal_response.status == 200
    assert global_reveal_response.status == 200
    assert revealed == [(root / "CLAUDE.md").resolve(), global_claude.resolve()]
    assert preview_response.status == 200
    assert link_preview_response.status == 200
    assert link_preview["already_linked"] is False
    assert sync_response.status == 200
    assert conflict_response.status == 409
    assert conflict_payload["code"] == "revision_conflict"
    assert restore_response.status == 200
    assert not (root / "AGENTS.md").exists()
