"""The agent-facing half of Project Actions: discovery, authoring, and running.

Before this, an agent could not see that a Project had actions, could not learn how
to write one, and could not read the result of one that ran. Running a task and
being unable to read its exit code is writing to `/dev/null`, so the result path is
tested here alongside the two new tools.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux.mcp import MAX_SESSION_OUTPUT_BYTES, McpService
from swe_mux.project_actions import ProjectActionService
from swe_mux.prompt_queue import QueueError

pytestmark = pytest.mark.asyncio


def record(sid: str, **kw: Any) -> Any:
    values: dict[str, Any] = {
        "id": sid,
        "name": kw.get("name", sid),
        "auto_named": True,
        "backend": kw.get("backend", "claude"),
        "state": kw.get("state", "working"),
        "state_detail": None,
        "awaiting_reason": None,
        "idle_reason": None,
        "model": None,
        "cwd": "D:/work",
        "project_id": "p1",
        "project_scope_id": "scope-1",
        "project_label": "Work",
        "agent_run_id": sid,
        "agent_run_seq": 0,
        "native_session_id": sid,
        "created_at": 1.0,
        "last_activity_ts": 2.0,
        "tokens_in": 0,
        "tokens_out": 0,
        "context_pct": 0.0,
        "completion_mode": kw.get("completion_mode", "interactive"),
        "exit_code": kw.get("exit_code"),
    }
    return SimpleNamespace(**values)


class ScrollbackStub:
    def __init__(self, text: str) -> None:
        self.data = text.encode("utf-8")

    def tail_bytes(self, limit: int) -> bytes:
        return self.data[-limit:]


def session(sid: str, *, output: str = "", **kw: Any) -> Any:
    return SimpleNamespace(
        record=record(sid, **kw),
        mcp_token=kw.get("token", ""),
        transcript_path=None,
        scrollback=ScrollbackStub(output),
    )


class HistoryStub:
    async def history_page(self, **_kw: Any) -> dict[str, Any]:
        return {"items": [], "next_cursor": None}

    async def history_entry(self, _sid: str) -> None:
        return None

    async def agent_runs_for_session(self, _sid: str) -> list[dict[str, Any]]:
        return []


class AutomationStoreStub:
    async def annotations(self, **_kw: Any) -> list[dict[str, Any]]:
        return []

    async def checkpoint(self, _key: str) -> None:
        return None


def manager_for(*sessions: Any) -> Any:
    table = {item.record.id: item for item in sessions}

    def resolve(identity: str) -> Any:
        if identity in table:
            return table[identity]
        raise KeyError(identity)

    return SimpleNamespace(sessions=table, resolve=resolve)


def project_with_actions(tmp_path: Path, toml: str) -> Any:
    root = tmp_path / "project"
    (root / ".swe-mux").mkdir(parents=True)
    (root / ".swe-mux" / "actions.toml").write_text(toml, encoding="utf-8")
    return SimpleNamespace(id="p1", name="Work", root=str(root))


VERIFY = """version = 1
[[actions]]
id = "verify"
label = "Verify"
description = "Run the test suite."
command = "pytest"
"""


def service_for(
    caller: Any,
    project: Any,
    actions: ProjectActionService,
    runner: Any = None,
) -> McpService:
    return McpService(
        manager_for(caller),
        HistoryStub(),
        automation_store=AutomationStoreStub(),
        projects=SimpleNamespace(projects={project.id: project}),
        project_actions=actions,
        action_runner=runner,
    )


# --- discovery ----------------------------------------------------------------


async def test_project_actions_lists_what_a_project_declares(tmp_path: Path) -> None:
    project = project_with_actions(tmp_path, VERIFY)
    caller = session("s1", token="tok")
    service = service_for(caller, project, ProjectActionService(tmp_path / "data"))

    result = await service.project_actions(caller, {})

    assert [item["id"] for item in result["actions"]] == ["native:verify"]
    assert result["actions"][0]["description"] == "Run the test suite."
    assert result["actions"][0]["source_path"] == ".swe-mux/actions.toml"
    # Untrusted until a human approves, and the result says so rather than leaving
    # the caller to infer it from a later refusal.
    assert result["actions"][0]["trusted"] is False
    assert "run_action" in result["note"]
    assert "schema" not in result


async def test_the_authoring_reference_travels_with_the_listing(tmp_path: Path) -> None:
    """One tool, not two.

    The agent that lists actions is the agent that wants to write one, and a
    separate documentation tool is not called. Folding it in makes the capability
    self-advertising: the description of the tool it already uses names the option.
    """
    project = project_with_actions(tmp_path, VERIFY)
    caller = session("s1", token="tok")
    service = service_for(caller, project, ProjectActionService(tmp_path / "data"))

    result = await service.project_actions(caller, {"include_schema": True})

    assert "Authoring `.swe-mux/actions.toml`" in result["schema"]
    assert "${input:" in result["schema"]


async def test_a_daemon_without_the_service_says_so_rather_than_answering_empty(
    tmp_path: Path,
) -> None:
    """An empty result would read as "this Project declares nothing"."""
    project = project_with_actions(tmp_path, VERIFY)
    caller = session("s1", token="tok")
    service = McpService(
        manager_for(caller),
        HistoryStub(),
        automation_store=AutomationStoreStub(),
        projects=SimpleNamespace(projects={project.id: project}),
    )

    with pytest.raises(QueueError) as error:
        await service.project_actions(caller, {})
    assert error.value.code == "unavailable"


# --- running ------------------------------------------------------------------


async def test_run_action_refuses_an_unapproved_action_and_names_the_file(
    tmp_path: Path,
) -> None:
    """The refusal is typed and actionable, not a protocol fault.

    An agent that cannot tell "refused" from "broken" either retries forever or
    stops calling, and both are worse than being told what a human must do.
    """
    project = project_with_actions(tmp_path, VERIFY)
    caller = session("s1", token="tok")
    actions = ProjectActionService(tmp_path / "data")

    async def runner(owner: Any, action_id: str, inputs: dict[str, str]) -> Any:
        actions.action(owner.root, action_id)  # raises PermissionError
        raise AssertionError("unreachable")

    service = service_for(caller, project, actions, runner)

    with pytest.raises(QueueError) as error:
        await service.run_action(caller, {"action_id": "native:verify"})

    assert error.value.code == "trust_required"
    assert ".swe-mux/actions.toml" in str(error.value)


async def test_run_action_starts_an_approved_action_and_points_at_the_result(
    tmp_path: Path,
) -> None:
    project = project_with_actions(tmp_path, VERIFY)
    caller = session("s1", token="tok")
    actions = ProjectActionService(tmp_path / "data")
    actions.trust(project.root, actions.catalog(project.root).fingerprint)
    started: list[tuple[str, dict[str, str]]] = []

    async def runner(owner: Any, action_id: str, inputs: dict[str, str]) -> Any:
        actions.action(owner.root, action_id)
        started.append((action_id, inputs))
        return (
            {
                "sessions": [{"id": "task-1", "name": "Verify", "state": "running"}],
                "errors": [],
                "inputs": inputs,
            },
            201,
        )

    service = service_for(caller, project, actions, runner)

    result = await service.run_action(caller, {"action_id": "native:verify"})

    assert started == [("native:verify", {})]
    assert result["sessions"] == [
        {"session_id": "task-1", "name": "Verify", "state": "running"}
    ]
    assert "get_session" in result["note"] and "exit_code" in result["note"]


async def test_run_action_refuses_an_action_no_project_in_scope_declares(
    tmp_path: Path,
) -> None:
    project = project_with_actions(tmp_path, VERIFY)
    caller = session("s1", token="tok")

    async def runner(*_args: Any, **_kw: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError("an unknown action must never reach the runner")

    service = service_for(caller, project, ProjectActionService(tmp_path / "data"), runner)

    with pytest.raises(QueueError) as error:
        await service.run_action(caller, {"action_id": "native:nope"})
    assert error.value.code == "unknown_action"


async def test_run_action_rejects_a_non_string_input_map(tmp_path: Path) -> None:
    project = project_with_actions(tmp_path, VERIFY)
    caller = session("s1", token="tok")

    async def runner(*_args: Any, **_kw: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError("a malformed input map must never reach the runner")

    service = service_for(caller, project, ProjectActionService(tmp_path / "data"), runner)

    with pytest.raises(ValueError, match="map of input id"):
        await service.run_action(
            caller, {"action_id": "native:verify", "inputs": {"env": 3}}
        )


# --- the result path ----------------------------------------------------------


async def test_a_finished_task_reports_its_exit_code(tmp_path: Path) -> None:
    """Without this a caller saw the session leave `running` and learned nothing.

    `completion_mode` is what makes `exit_code: null` readable: on a one-shot task
    it means "still running", and on an interactive pane it means "there is no
    such thing as a result here".
    """
    project = project_with_actions(tmp_path, VERIFY)
    caller = session("s1", token="tok")
    task = session("task-1", backend="shell", completion_mode="one_shot", exit_code=1)
    service = McpService(
        manager_for(caller, task),
        HistoryStub(),
        automation_store=AutomationStoreStub(),
        projects=SimpleNamespace(projects={project.id: project}),
        project_actions=ProjectActionService(tmp_path / "data"),
    )

    result = await service.get_session(caller, {"session_id": "task-1"})

    assert result["completion_mode"] == "one_shot"
    assert result["exit_code"] == 1


async def test_terminal_output_is_returned_only_when_asked_and_is_bounded(
    tmp_path: Path,
) -> None:
    project = project_with_actions(tmp_path, VERIFY)
    caller = session("s1", token="tok")
    task = session(
        "task-1",
        backend="shell",
        completion_mode="one_shot",
        exit_code=1,
        output="line one\nassert failed\n",
    )
    service = McpService(
        manager_for(caller, task),
        HistoryStub(),
        automation_store=AutomationStoreStub(),
        projects=SimpleNamespace(projects={project.id: project}),
        project_actions=ProjectActionService(tmp_path / "data"),
    )

    quiet = await service.get_session(caller, {"session_id": "task-1"})
    loud = await service.get_session(
        caller, {"session_id": "task-1", "output_bytes": MAX_SESSION_OUTPUT_BYTES}
    )
    clipped = await service.get_session(caller, {"session_id": "task-1", "output_bytes": 6})

    assert "output" not in quiet
    assert "assert failed" in loud["output"]
    assert loud["output_truncated"] is False
    assert clipped["output_truncated"] is True


async def test_credential_shaped_output_is_redacted(tmp_path: Path) -> None:
    """A task that echoes a token is exactly what this gate exists for."""
    project = project_with_actions(tmp_path, VERIFY)
    caller = session("s1", token="tok")
    task = session(
        "task-1",
        backend="shell",
        completion_mode="one_shot",
        output="deploying\nexport TOKEN=sk-live-abcdefghijklmnopqrstuvwxyz0123456789\n",
    )
    service = McpService(
        manager_for(caller, task),
        HistoryStub(),
        automation_store=AutomationStoreStub(),
        projects=SimpleNamespace(projects={project.id: project}),
        project_actions=ProjectActionService(tmp_path / "data"),
    )

    result = await service.get_session(caller, {"session_id": "task-1", "output_bytes": 4096})

    assert "sk-live-abcdefghijklmnopqrstuvwxyz0123456789" not in result["output"]
    assert "deploying" in result["output"]


async def test_a_removed_session_says_output_is_gone_rather_than_omitting_it(
    tmp_path: Path,
) -> None:
    """The scrollback ring lives with the Session object, not in history.

    Silently dropping the field leaves the caller unable to tell "the task printed
    nothing" from "I did not read it", which is the reading that makes an agent
    stop asking.
    """
    project = project_with_actions(tmp_path, VERIFY)
    caller = session("s1", token="tok")

    ended_row = {
        "id": "task-1",
        "name": "Verify",
        "backend": "shell",
        "project_id": "p1",
        "project_scope_id": "scope-1",
        "cwd": "D:/work",
        "spawned_at": 1.0,
        "exited_at": 2.0,
        "final_state": "exited",
    }

    class HistoryWithRow(HistoryStub):
        async def history_entry(self, sid: str) -> dict[str, Any] | None:
            return ended_row if sid == "task-1" else None

        async def history_page(self, **_kw: Any) -> dict[str, Any]:
            return {"items": [ended_row], "next_cursor": None}

    service = McpService(
        manager_for(caller),
        HistoryWithRow(),
        automation_store=AutomationStoreStub(),
        projects=SimpleNamespace(projects={project.id: project}),
        project_actions=ProjectActionService(tmp_path / "data"),
    )

    result = await service.get_session(caller, {"session_id": "task-1", "output_bytes": 4096})

    assert result["output_available"] is False
    assert "no longer live" in result["output_note"]


async def test_an_agent_session_is_sent_to_read_transcript_instead(tmp_path: Path) -> None:
    """Its PTY bytes are a differential frame stream, not a transcript."""
    project = project_with_actions(tmp_path, VERIFY)
    caller = session("s1", token="tok", output="\x1b[2J\x1b[Hframe")
    service = McpService(
        manager_for(caller),
        HistoryStub(),
        automation_store=AutomationStoreStub(),
        projects=SimpleNamespace(projects={project.id: project}),
        project_actions=ProjectActionService(tmp_path / "data"),
    )

    result = await service.get_session(caller, {"output_bytes": 4096})

    assert result["output_available"] is False
    assert "read_transcript" in result["output_note"]


async def test_the_listing_reports_per_file_approval(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / ".swe-mux").mkdir(parents=True)
    (root / ".swe-mux" / "actions.toml").write_text(VERIFY, encoding="utf-8")
    (root / "package.json").write_text(
        json.dumps({"scripts": {"dev": "vite"}}), encoding="utf-8"
    )
    project = SimpleNamespace(id="p1", name="Work", root=str(root))
    caller = session("s1", token="tok")
    actions = ProjectActionService(tmp_path / "data")
    native = next(
        item for item in actions.catalog(str(root)).files if item.path.endswith("actions.toml")
    )
    actions.trust(str(root), native.fingerprint, source=native.path)
    service = service_for(caller, project, actions)

    result = await service.project_actions(caller, {})

    by_id = {item["id"]: item for item in result["actions"]}
    assert by_id["native:verify"]["trusted"] is True
    assert by_id["package:dev"]["trusted"] is False
