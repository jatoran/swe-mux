from __future__ import annotations

import hashlib
import json
import os
import re
import time
import tomllib
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from .harness import AGENT_BACKENDS, agent_harnesses
from .models import ProjectRecord
from .project_files import parse_project_config

PROMPT_SCHEMA_VERSION = 1
PROMPT_BODY_LIMIT = 64 * 1024
PROMPT_FRONTMATTER = "+++\n"
PROMPT_VARIABLE = re.compile(r"{{\s*([A-Za-z][A-Za-z0-9_]{0,63})\s*}}")
PROMPT_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
PromptScope = Literal["global", "project"]


def _revision(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:24]


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _string_array(value: Any, field: str, *, maximum: int, item_limit: int) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be an array of strings")
    normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
    if len(normalized) > maximum or any(len(item) > item_limit for item in normalized):
        raise ValueError(f"{field} exceeds its bounded size")
    return normalized


def _template_values(
    values: dict[str, Any], *, existing: dict[str, Any] | None = None
) -> dict[str, Any]:
    title = str(values.get("title") or "").strip()
    # Normalize newlines and strip trailing whitespace so a round-trip through
    # serialize_template (which appends a single newline) never grows the body.
    # A stray trailing newline would submit the prompt on insertion.
    body = str(values.get("body") or "").replace("\r\n", "\n").replace("\r", "\n").rstrip()
    if not title or len(title) > 120:
        raise ValueError("prompt title must be 1–120 characters")
    if not body.strip() or len(body.encode("utf-8")) > PROMPT_BODY_LIMIT:
        raise ValueError("prompt body must be non-empty and at most 64 KiB")
    if PROMPT_CONTROL.search(body):
        raise ValueError("prompt body contains terminal control characters")
    tags = _string_array(values.get("tags", []), "tags", maximum=20, item_limit=40)
    backends = _string_array(
        values.get("backends", ["shell", *agent_harnesses()]),
        "backends",
        maximum=len(AGENT_BACKENDS) + 1,
        item_limit=10,
    )
    if not backends or not set(backends) <= {"shell", *AGENT_BACKENDS}:
        raise ValueError("backends must contain shell and/or registered agents")
    now = time.time()
    return {
        "schema_version": PROMPT_SCHEMA_VERSION,
        "id": str(existing["id"] if existing else values.get("id") or uuid.uuid4()),
        "title": title,
        "tags": tags,
        "variables": sorted(set(PROMPT_VARIABLE.findall(body))),
        "backends": backends,
        "created_at": float(existing["created_at"] if existing else now),
        "updated_at": now,
        "body": body,
    }


def serialize_template(template: dict[str, Any]) -> bytes:
    lines = [
        "+++",
        f"schema_version = {PROMPT_SCHEMA_VERSION}",
        f"id = {json.dumps(template['id'])}",
        f"title = {json.dumps(template['title'])}",
        f"tags = {json.dumps(template['tags'])}",
        f"variables = {json.dumps(template['variables'])}",
        f"backends = {json.dumps(template['backends'])}",
        f"created_at = {float(template['created_at']):.6f}",
        f"updated_at = {float(template['updated_at']):.6f}",
        "+++",
        str(template["body"]),
    ]
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def parse_template(data: bytes) -> dict[str, Any]:
    text = data.decode("utf-8")
    if not text.startswith(PROMPT_FRONTMATTER):
        raise ValueError("prompt template is missing TOML frontmatter")
    boundary = text.find("\n+++\n", len(PROMPT_FRONTMATTER))
    if boundary < 0:
        raise ValueError("prompt template frontmatter is not closed")
    metadata = tomllib.loads(text[len(PROMPT_FRONTMATTER) : boundary])
    if metadata.get("schema_version") != PROMPT_SCHEMA_VERSION:
        raise ValueError(f"prompt schema_version must be {PROMPT_SCHEMA_VERSION}")
    required = {
        "id",
        "title",
        "tags",
        "variables",
        "backends",
        "created_at",
        "updated_at",
    }
    if set(metadata) != required | {"schema_version"}:
        raise ValueError("prompt template metadata fields are invalid")
    body = text[boundary + 5 :]
    normalized = _template_values({**metadata, "body": body})
    normalized["id"] = str(metadata["id"])
    normalized["created_at"] = float(metadata["created_at"])
    normalized["updated_at"] = float(metadata["updated_at"])
    declared = _string_array(metadata["variables"], "variables", maximum=64, item_limit=64)
    if sorted(declared) != normalized["variables"]:
        raise ValueError("declared variables do not match prompt placeholders")
    return normalized


def project_prompt_scope(project: ProjectRecord | None) -> str:
    if project is None:
        return "global"
    path = Path(project.root) / ".swe-mux" / "config.toml"
    try:
        values = parse_project_config(path.read_bytes()) if path.is_file() else {}
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError):
        values = {}
    return str(values.get("prompt_library_scope") or "both")


class PromptLibrary:
    def __init__(self, data_dir: Path) -> None:
        self.global_dir = data_dir / "prompts"
        self.state_path = data_dir / "prompt-library-state.json"

    def _directory(self, scope: PromptScope, project: ProjectRecord | None) -> Path:
        if scope == "global":
            return self.global_dir
        if project is None:
            raise ValueError("project_id is required for Project prompt templates")
        return Path(project.root) / ".swe-mux" / "prompts"

    def _path(self, scope: PromptScope, template_id: str, project: ProjectRecord | None) -> Path:
        try:
            safe_id = str(uuid.UUID(template_id))
        except ValueError as exc:
            raise ValueError("invalid prompt template id") from exc
        return self._directory(scope, project) / f"{safe_id}.md"

    def _state(self) -> dict[str, dict[str, Any]]:
        try:
            parsed = json.loads(self.state_path.read_text(encoding="utf-8"))
            return parsed if isinstance(parsed, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_state(self, state: dict[str, dict[str, Any]]) -> None:
        bounded = dict(
            sorted(
                state.items(),
                key=lambda item: float(item[1].get("last_used_at") or 0),
                reverse=True,
            )[:1000]
        )
        _atomic_write(
            self.state_path,
            (json.dumps(bounded, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )

    def _read_directory(
        self, scope: PromptScope, project: ProjectRecord | None
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        directory = self._directory(scope, project)
        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        if not directory.is_dir():
            return items, errors
        for path in sorted(directory.glob("*.md"))[:1000]:
            try:
                data = path.read_bytes()
                item = parse_template(data)
                item.update(scope=scope, revision=_revision(data), key=f"{scope}:{item['id']}")
                items.append(item)
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError) as exc:
                errors.append({"path": path.name, "error": str(exc)[:500]})
        return items, errors

    def list(
        self,
        project: ProjectRecord | None = None,
        *,
        other_projects: Sequence[ProjectRecord] = (),
    ) -> dict[str, Any]:
        """List templates visible from `project`.

        `other_projects` widens the read to the Project libraries of Projects the
        caller is *not* focused on. That is a management view only: pinning a
        template to an Action layout resolves it back through the focused-Project
        listing, so a widened list must never be the source a pin is taken from
        (`design/features/prompt-library.md`).
        """
        configured_scope = project_prompt_scope(project)
        scopes: list[PromptScope] = []
        if configured_scope in {"global", "both"}:
            scopes.append("global")
        if project is not None and configured_scope in {"project", "both"}:
            scopes.append("project")
        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for scope in scopes:
            found, diagnostics = self._read_directory(scope, project)
            for item in found:
                item.update(
                    project_id=project.id if scope == "project" and project else None,
                    project_name=project.name if scope == "project" and project else None,
                )
            items.extend(found)
            errors.extend(diagnostics)
        sources: list[dict[str, str]] = []
        if project is not None and "project" in scopes:
            sources.append({"id": project.id, "name": project.name})
        for other in other_projects:
            if project is not None and other.id == project.id:
                continue
            if project_prompt_scope(other) not in {"project", "both"}:
                continue
            sources.append({"id": other.id, "name": other.name})
            found, diagnostics = self._read_directory("project", other)
            for item in found:
                item.update(project_id=other.id, project_name=other.name)
            items.extend(found)
            errors.extend(
                {"path": f"{other.name}/{item['path']}", "error": item["error"]}
                for item in diagnostics
            )
        # A conflict is two templates the *focused* listing would return under one
        # stable ID, because that is the pair a `scope:id` key cannot choose between.
        # Two unfocused Projects reusing an ID (a copied repository) is not ambiguous:
        # neither is reachable from a key, so flagging them would be noise.
        focused_id = project.id if project else None
        id_counts: dict[str, int] = {}
        for item in items:
            if item["project_id"] in (None, focused_id):
                id_counts[item["id"]] = id_counts.get(item["id"], 0) + 1
        state = self._state()
        for item in items:
            usage = state.get(item["key"], {})
            item.update(
                favorite=bool(usage.get("favorite")),
                last_used_at=usage.get("last_used_at"),
                use_count=int(usage.get("use_count") or 0),
                conflict=item["project_id"] in (None, focused_id)
                and id_counts.get(item["id"], 0) > 1,
            )
        items.sort(
            key=lambda item: (
                not item["favorite"],
                -float(item["last_used_at"] or 0),
                item["title"].casefold(),
                item["project_id"] or "",
                item["key"],
            )
        )
        return {
            "schema_version": PROMPT_SCHEMA_VERSION,
            "project_id": project.id if project else None,
            "configured_scope": configured_scope,
            "items": items,
            "projects": sources,
            "diagnostics": errors,
        }

    def create(
        self, scope: PromptScope, values: dict[str, Any], project: ProjectRecord | None = None
    ) -> dict[str, Any]:
        if scope not in {"global", "project"}:
            raise ValueError("prompt scope must be global or project")
        allowed = project_prompt_scope(project)
        if project is not None and allowed != "both" and scope != allowed:
            raise ValueError(f"Project prompt library scope is {allowed}")
        template = _template_values(values)
        path = self._path(scope, template["id"], project)
        if path.exists():
            raise ValueError("prompt template id already exists in this scope")
        data = serialize_template(template)
        _atomic_write(path, data)
        return {
            **template,
            "scope": scope,
            "key": f"{scope}:{template['id']}",
            "revision": _revision(data),
            "project_id": project.id if scope == "project" and project else None,
            "project_name": project.name if scope == "project" and project else None,
        }

    def update(
        self,
        scope: PromptScope,
        template_id: str,
        values: dict[str, Any],
        expected_revision: str,
        project: ProjectRecord | None = None,
    ) -> dict[str, Any]:
        path = self._path(scope, template_id, project)
        if not path.is_file():
            raise ValueError("prompt template does not exist")
        current_data = path.read_bytes()
        if _revision(current_data) != expected_revision:
            raise ValueError("prompt template changed externally; reload before saving")
        current = parse_template(current_data)
        template = _template_values(values, existing=current)
        data = serialize_template(template)
        _atomic_write(path, data)
        return {
            **template,
            "scope": scope,
            "key": f"{scope}:{template_id}",
            "revision": _revision(data),
            "project_id": project.id if scope == "project" and project else None,
            "project_name": project.name if scope == "project" and project else None,
        }

    def delete(
        self,
        scope: PromptScope,
        template_id: str,
        expected_revision: str,
        project: ProjectRecord | None = None,
    ) -> None:
        path = self._path(scope, template_id, project)
        if not path.is_file():
            raise ValueError("prompt template does not exist")
        if _revision(path.read_bytes()) != expected_revision:
            raise ValueError("prompt template changed externally; reload before deleting")
        path.unlink()

    def record_use(self, key: str) -> dict[str, Any]:
        if not re.fullmatch(r"(?:global|project):[0-9a-f-]{36}", key):
            raise ValueError("invalid prompt template key")
        state = self._state()
        item = state.setdefault(key, {})
        item["last_used_at"] = time.time()
        item["use_count"] = int(item.get("use_count") or 0) + 1
        self._write_state(state)
        return item

    def set_favorite(self, key: str, favorite: bool) -> dict[str, Any]:
        if not re.fullmatch(r"(?:global|project):[0-9a-f-]{36}", key):
            raise ValueError("invalid prompt template key")
        state = self._state()
        item = state.setdefault(key, {})
        item["favorite"] = favorite
        self._write_state(state)
        return item
