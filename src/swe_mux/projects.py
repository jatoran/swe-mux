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


def _stable_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:24]


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
    if remote:
        identity = f"remote:{_normalize_remote(remote)}"
        return ProjectIdentity(
            _stable_id(identity), root.name or "Repository", str(root), "git-remote"
        )
    if common:
        common_path = os.path.normcase(str(Path(common).resolve()))
        return ProjectIdentity(
            _stable_id(f"git:{common_path}"),
            root.name or "Repository",
            str(root),
            "git-common-dir",
        )
    normalized = os.path.normcase(str(resolved))
    return ProjectIdentity(
        _stable_id(f"cwd:{normalized}"), resolved.name or "Ungrouped", str(resolved), "cwd"
    )
