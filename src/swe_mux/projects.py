from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProjectIdentity:
    id: str
    label: str
    root: str
    source: str
    repo_group_id: str | None = None
    repo_group_label: str | None = None


def _stable_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:24]


def project_scope_id(root: str | Path) -> str:
    return _stable_id(f"scope:{os.path.normcase(str(Path(root).resolve()))}")


def _normalize_remote(value: str) -> str:
    remote = value.strip().removesuffix(".git").replace("\\", "/")
    if remote.startswith("git@") and ":" in remote:
        host, path = remote.split(":", 1)
        remote = f"ssh://{host}/{path}"
    return remote.casefold()


async def _git(cwd: Path, *args: str) -> str | None:
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(cwd),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=3)
        if process.returncode == 0:
            return stdout.decode("utf-8", "replace").strip()
    except (FileNotFoundError, TimeoutError, OSError):
        pass
    return None


async def resolve_project(cwd: str | Path) -> ProjectIdentity:
    resolved = Path(cwd).resolve()
    worktree = await _git(resolved, "rev-parse", "--show-toplevel")
    root = Path(worktree).resolve() if worktree else resolved
    common = await _git(resolved, "rev-parse", "--path-format=absolute", "--git-common-dir")
    remote = await _git(resolved, "remote", "get-url", "origin")
    scope_id = project_scope_id(root)
    if remote:
        normalized_remote = _normalize_remote(remote)
        return ProjectIdentity(
            scope_id, root.name or "Repository", str(root), "git-worktree",
            _stable_id(f"remote:{normalized_remote}"), normalized_remote,
        )
    if common:
        common_path = os.path.normcase(str(Path(common).resolve()))
        return ProjectIdentity(
            scope_id, root.name or "Repository", str(root), "git-worktree",
            _stable_id(f"git:{common_path}"), Path(common_path).parent.name or root.name,
        )
    return ProjectIdentity(scope_id, resolved.name or "Ungrouped", str(resolved), "cwd")
