from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from .profiles import resolve_profile

ACTION_FILES = (Path(".vscode/tasks.json"), Path("package.json"), Path(".swe-mux/actions.toml"))
MAX_ACTIONS = 128
MAX_STEPS = 32
_VARIABLE = re.compile(r"\$\{([^}]+)\}")


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

    def preview(self) -> str:
        return " ".join((self.command, *self.args)).strip()


@dataclass(frozen=True, slots=True)
class ProjectAction:
    id: str
    label: str
    source: str
    batches: tuple[tuple[ActionStep, ...], ...]

    @property
    def steps(self) -> tuple[ActionStep, ...]:
        return tuple(step for batch in self.batches for step in batch)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "source": self.source,
            "steps": [
                {"name": step.name, "kind": step.kind, "command": step.preview()}
                for step in self.steps
            ],
        }


@dataclass(frozen=True, slots=True)
class ActionCatalog:
    root: str
    fingerprint: str
    trusted: bool
    sources: tuple[str, ...]
    actions: tuple[ProjectAction, ...]
    diagnostics: tuple[str, ...]

    def snapshot(self) -> dict[str, Any]:
        return {
            "project_root": self.root,
            "fingerprint": self.fingerprint,
            "trusted": self.trusted,
            "sources": list(self.sources),
            "actions": [action.snapshot() for action in self.actions],
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


def _resolve_value(value: str, root: Path) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == "workspaceFolder":
            return str(root)
        if name == "workspaceFolderBasename":
            return root.name
        if name == "pathSeparator":
            return os.sep
        if name.startswith("env:"):
            return os.environ.get(name[4:], "")
        raise ValueError(f"unsupported VS Code variable: ${{{name}}}")

    return _VARIABLE.sub(replace, value)


def _cwd(value: str | None, root: Path) -> str:
    target = root if not value else Path(_resolve_value(value, root))
    if not target.is_absolute():
        target = root / target
    target = target.resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("action cwd must stay inside the Project root") from exc
    return str(target)


def _strings(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be an array of strings")
    return tuple(value)


def _environment(value: Any, root: Path) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 64:
        raise ValueError("action env must be an object with at most 64 entries")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, (str, int, float, bool)):
            raise ValueError("action env values must be strings or scalar values")
        result[key] = _resolve_value(str(item), root)
    return result


def _vscode_step(raw: dict[str, Any], root: Path) -> ActionStep | None:
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
        command=_resolve_value(str(raw["command"]), root),
        args=tuple(_resolve_value(item, root) for item in _strings(raw.get("args"), "args")),
        cwd=_cwd(str(options["cwd"]) if options.get("cwd") is not None else None, root),
        env=_environment(options.get("env"), root),
        shell_executable=(
            _resolve_value(str(shell["executable"]), root) if shell.get("executable") else None
        ),
        shell_args=tuple(
            _resolve_value(item, root) for item in _strings(shell.get("args"), "shell.args")
        ),
    )


def _vscode_actions(path: Path, root: Path) -> tuple[list[ProjectAction], list[str]]:
    if not path.is_file():
        return [], []
    document = loads_jsonc(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("tasks", []), list):
        raise ValueError(".vscode/tasks.json must contain a tasks array")
    raw_tasks = [item for item in document["tasks"] if isinstance(item, dict)]
    by_label = {str(item.get("label") or ""): item for item in raw_tasks if item.get("label")}
    diagnostics: list[str] = []

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
        step = _vscode_step(raw, root)
        if step:
            batches.append((step,))
        return tuple(batches)

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
        actions.append(ProjectAction(f"vscode:{label}", label, "vscode", batches))
    return actions, diagnostics


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
    actions: list[ProjectAction] = []
    for name, command in scripts.items():
        if not isinstance(name, str) or not isinstance(command, str):
            continue
        step = ActionStep(
            name=name, kind="process", command=manager, args=("run", name), cwd=str(root)
        )
        actions.append(ProjectAction(f"package:{name}", name, "package", ((step,),)))
    return actions


def _native_step(raw: dict[str, Any], label: str, root: Path) -> ActionStep:
    command = raw.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError(f"native action {label} step requires command")
    kind = str(raw.get("type") or "shell")
    if kind not in {"shell", "process"}:
        raise ValueError(f"native action {label} type must be shell or process")
    return ActionStep(
        name=str(raw.get("name") or label),
        kind=kind,
        command=_resolve_value(command, root),
        args=tuple(_resolve_value(item, root) for item in _strings(raw.get("args"), "args")),
        cwd=_cwd(str(raw["cwd"]) if raw.get("cwd") is not None else None, root),
        env=_environment(raw.get("env"), root),
    )


def _native_actions(path: Path, root: Path) -> list[ProjectAction]:
    if not path.is_file():
        return []
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    if int(document.get("version", 0)) != 1 or not isinstance(document.get("actions", []), list):
        raise ValueError(".swe-mux/actions.toml requires version = 1 and [[actions]]")
    actions: list[ProjectAction] = []
    for raw in document["actions"]:
        if not isinstance(raw, dict):
            raise ValueError("native actions must be tables")
        action_id = str(raw.get("id") or "").strip()
        label = str(raw.get("label") or action_id).strip()
        if not action_id or not label or not re.fullmatch(r"[A-Za-z0-9_.-]+", action_id):
            raise ValueError("native action id must use letters, digits, dot, dash, or underscore")
        raw_steps = raw.get("steps")
        steps: tuple[ActionStep, ...]
        if raw_steps is None:
            steps = (_native_step(raw, label, root),)
        elif isinstance(raw_steps, list) and 0 < len(raw_steps) <= MAX_STEPS:
            steps = tuple(
                _native_step(step, label, root)
                for step in raw_steps
                if isinstance(step, dict)
            )
            if len(steps) != len(raw_steps):
                raise ValueError(f"native action {label} contains an invalid step")
        else:
            raise ValueError(f"native action {label} steps must contain 1-{MAX_STEPS} tables")
        batches = tuple((step,) for step in steps) if raw.get("sequential") else (steps,)
        actions.append(ProjectAction(f"native:{action_id}", label, "native", batches))
    return actions


class ProjectActionService:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "project-action-trust.json"

    def _trusted(self) -> dict[str, str]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return (
                {str(key): str(item) for key, item in value.items()}
                if isinstance(value, dict)
                else {}
            )
        except (OSError, ValueError, TypeError):
            return {}

    def _fingerprint(self, root: Path) -> tuple[str, tuple[str, ...]]:
        digest = hashlib.sha256()
        sources: list[str] = []
        for relative in ACTION_FILES:
            path = root / relative
            digest.update(relative.as_posix().encode())
            if path.is_file():
                data = path.read_bytes()
                digest.update(b"\0present\0")
                digest.update(data)
                sources.append(relative.as_posix())
            else:
                digest.update(b"\0missing\0")
        return digest.hexdigest(), tuple(sources)

    def catalog(self, project_root: str) -> ActionCatalog:
        root = Path(project_root).resolve()
        fingerprint, sources = self._fingerprint(root)
        actions: list[ProjectAction] = []
        diagnostics: list[str] = []
        readers: tuple[
            tuple[str, Callable[[], tuple[list[ProjectAction], list[str]]]], ...
        ] = (
            ("VS Code tasks", lambda: _vscode_actions(root / ACTION_FILES[0], root)),
            ("package scripts", lambda: (_package_actions(root / ACTION_FILES[1], root), [])),
            ("native Project Actions", lambda: (_native_actions(root / ACTION_FILES[2], root), [])),
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
        return ActionCatalog(
            str(root), fingerprint, self._trusted().get(str(root)) == fingerprint,
            sources, tuple(unique), tuple(dict.fromkeys(diagnostics)),
        )

    def trust(self, project_root: str, fingerprint: str) -> ActionCatalog:
        catalog = self.catalog(project_root)
        if fingerprint != catalog.fingerprint:
            raise ValueError("Project task files changed; review them again before trusting")
        values = self._trusted()
        values[catalog.root] = fingerprint
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)
        return self.catalog(project_root)

    def action(self, project_root: str, action_id: str) -> tuple[ActionCatalog, ProjectAction]:
        catalog = self.catalog(project_root)
        if not catalog.trusted:
            raise PermissionError("Project task files are not trusted or changed since approval")
        action = next((item for item in catalog.actions if item.id == action_id), None)
        if action is None:
            raise KeyError(action_id)
        return catalog, action


def runner_spawn_body(
    step: ActionStep,
    *,
    project_id: str,
    config: Config,
    profile_id: str,
) -> dict[str, Any]:
    command = step.command
    args = list(step.args)
    if step.kind == "shell":
        if step.shell_executable:
            executable = step.shell_executable
            shell_args = list(step.shell_args)
            if not shell_args:
                shell_args = _shell_command_args(executable, command)
            else:
                shell_args.append(command)
        else:
            profile = resolve_profile(config, profile_id, Path(step.cwd), interactive=False)
            executable = profile.executable
            shell_args = [*profile.argv, *_shell_command_args(executable, command)]
        command = executable
        args = shell_args
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {"cwd": step.cwd, "env": step.env, "command": command, "args": args},
            separators=(",", ":"),
        ).encode()
    ).decode()
    runner_executable, runner_prefix = action_runner_invocation()
    return {
        "project_id": project_id,
        "backend": "shell",
        "name": step.name,
        "executable": runner_executable,
        "argv": [*runner_prefix, payload],
        "completion_mode": "one_shot",
    }


def action_runner_invocation(
    *, executable: str | None = None, frozen: bool | None = None
) -> tuple[str, tuple[str, ...]]:
    executable = executable or sys.executable
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if frozen:
        return str(Path(executable).with_name("swe-mux-action.exe")), ()
    return executable, ("-m", "swe_mux.action_runner")


def _shell_command_args(executable: str, command: str) -> list[str]:
    name = Path(executable).name.casefold()
    if name in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        return ["-Command", command]
    if name in {"cmd", "cmd.exe"}:
        return ["/d", "/s", "/c", command]
    if name in {"wsl", "wsl.exe"}:
        return ["--", "sh", "-lc", command]
    return ["-c", command]
