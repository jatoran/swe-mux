"""User-owned Project context for semantic timeline scans."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .project_files import revision

log = logging.getLogger(__name__)

PROJECT_CONTEXT_PATH = ".swe-mux/project-context.md"
MAX_PROJECT_CONTEXT_BYTES = 16 * 1024


@dataclass(frozen=True, slots=True)
class ProjectContext:
    project_id: str
    project_root: str


def generation_prompt() -> str:
    return (
        "Analyze this repository and write `.swe-mux/project-context.md` as concise Markdown "
        "context for swe-mux timeline scans. Include only verified repository facts: purpose, "
        "architecture, major subsystems, important paths, terminology, workflows, constraints, "
        f"and validation conventions. Keep the file under {MAX_PROJECT_CONTEXT_BYTES} UTF-8 "
        "bytes. Use repository evidence only. Do not modify any other file."
    )


class ProjectContextService:
    """Read and edit one fixed Markdown context file inside each Project."""

    def __init__(self, *, resolve_session: Any) -> None:
        self.resolve_session = resolve_session
        self.reads = 0
        self.writes = 0
        self.creates = 0
        self.last_error: str | None = None

    @staticmethod
    def _paths(root: str | Path) -> tuple[Path, Path]:
        project_root = Path(root).resolve()
        control = project_root / ".swe-mux"
        target = control / "project-context.md"
        if control.is_symlink() or (control.exists() and not control.is_dir()):
            raise ValueError("Project control directory is unsafe")
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError("Project context path is unsafe")
        return control, target

    @staticmethod
    def _read_bytes(target: Path) -> bytes | None:
        if not target.exists():
            return None
        with target.open("rb") as handle:
            data = handle.read(MAX_PROJECT_CONTEXT_BYTES + 1)
        if len(data) > MAX_PROJECT_CONTEXT_BYTES:
            raise ValueError(
                f"Project context exceeds the {MAX_PROJECT_CONTEXT_BYTES}-byte limit"
            )
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Project context must be UTF-8 Markdown") from exc
        return data

    def read(self, context: ProjectContext) -> dict[str, Any]:
        _control, target = self._paths(context.project_root)
        data = self._read_bytes(target)
        self.reads += 1
        return {
            "project_id": context.project_id,
            "path": PROJECT_CONTEXT_PATH,
            "exists": data is not None,
            "revision": revision(data),
            "markdown": data.decode("utf-8") if data is not None else "",
            "max_bytes": MAX_PROJECT_CONTEXT_BYTES,
            "generation_prompt": generation_prompt(),
        }

    def ensure(self, context: ProjectContext) -> dict[str, Any]:
        control, target = self._paths(context.project_root)
        control.mkdir(parents=True, exist_ok=True)
        if control.is_symlink() or not control.is_dir():
            raise ValueError("Project control directory is unsafe")
        try:
            with target.open("x", encoding="utf-8", newline="\n"):
                pass
            self.creates += 1
            log.info(
                "Project context initialized project_id=%s path=%s",
                context.project_id,
                target,
            )
        except FileExistsError:
            pass
        try:
            return self.read(context)
        except (OSError, ValueError) as exc:
            # An existing malformed user file must not block timeline enablement.
            # Reads and scans still report or degrade around the malformed content.
            self.last_error = f"{type(exc).__name__}: {exc}"[:400]
            log.warning(
                "Existing project context is unavailable project_id=%s path=%s error=%s",
                context.project_id,
                target,
                exc,
            )
            return {
                "project_id": context.project_id,
                "path": PROJECT_CONTEXT_PATH,
                "exists": True,
                "revision": "unavailable",
                "markdown": "",
                "max_bytes": MAX_PROJECT_CONTEXT_BYTES,
                "generation_prompt": generation_prompt(),
                "error": str(exc),
            }

    def write(
        self,
        context: ProjectContext,
        markdown: str,
        expected_revision: str,
    ) -> dict[str, Any]:
        if not isinstance(markdown, str):
            raise ValueError("markdown must be a string")
        data = markdown.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        if len(data) > MAX_PROJECT_CONTEXT_BYTES:
            raise ValueError(
                f"Project context exceeds the {MAX_PROJECT_CONTEXT_BYTES}-byte limit"
            )
        control, target = self._paths(context.project_root)
        current = self._read_bytes(target)
        if revision(current) != expected_revision:
            raise ValueError("Project context changed externally; reload before saving")
        control.mkdir(parents=True, exist_ok=True)
        if control.is_symlink() or not control.is_dir():
            raise ValueError("Project control directory is unsafe")
        temporary = target.with_name(f".{target.name}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.replace(control / f".{target.name}.failed")
        self.writes += 1
        log.info(
            "Project context saved project_id=%s path=%s bytes=%d",
            context.project_id,
            target,
            len(data),
        )
        return self.read(context)

    async def prompt_prefix(self, session_id: str) -> str:
        context = await self.resolve_session(session_id)
        if context is None:
            return ""
        try:
            payload = await asyncio.to_thread(self.read, context)
            return str(payload["markdown"])
        except (OSError, ValueError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"[:400]
            log.warning(
                "Project context unavailable session_id=%s project_id=%s error=%s",
                session_id,
                context.project_id,
                exc,
            )
            return ""

    def status(self) -> dict[str, Any]:
        return {
            "path": PROJECT_CONTEXT_PATH,
            "max_bytes": MAX_PROJECT_CONTEXT_BYTES,
            "reads": self.reads,
            "writes": self.writes,
            "creates": self.creates,
            "last_error": self.last_error,
        }
