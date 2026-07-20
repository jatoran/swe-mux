from __future__ import annotations

import asyncio
import hashlib
import os
import time
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


# Project identity is stable during a daemon run in normal use, while resolving it
# requires launching Git. A short cache keeps repeated terminal launches in the same
# project off that process boundary without hiding repo changes for long.
_RESOLVE_CACHE_SECONDS = 30.0
_resolve_cache: dict[str, tuple[float, ProjectIdentity]] = {}


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
    process: asyncio.subprocess.Process | None = None
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
    except TimeoutError:
        if process is not None:
            try:
                process.kill()
                await process.wait()
            except (ProcessLookupError, OSError):
                pass
    except (FileNotFoundError, OSError):
        pass
    return None


async def resolve_project(cwd: str | Path) -> ProjectIdentity:
    resolved = Path(cwd).resolve()
    cache_key = os.path.normcase(str(resolved))
    cached = _resolve_cache.get(cache_key)
    now = time.monotonic()
    if cached and now-cached[0] < _RESOLVE_CACHE_SECONDS:
        return cached[1]
    # These probes are independent. Running them concurrently bounds a pathological
    # Git/network/config stall to one timeout window instead of three serial windows.
    worktree, common, remote = await asyncio.gather(
        _git(resolved, "rev-parse", "--show-toplevel"),
        _git(resolved, "rev-parse", "--path-format=absolute", "--git-common-dir"),
        _git(resolved, "remote", "get-url", "origin"),
    )
    root = Path(worktree).resolve() if worktree else resolved
    scope_id = project_scope_id(root)
    if remote:
        normalized_remote = _normalize_remote(remote)
        identity = ProjectIdentity(
            scope_id,
            root.name or "Repository",
            str(root),
            "git-worktree",
            _stable_id(f"remote:{normalized_remote}"),
            normalized_remote,
        )
    elif common:
        common_path = os.path.normcase(str(Path(common).resolve()))
        identity = ProjectIdentity(
            scope_id,
            root.name or "Repository",
            str(root),
            "git-worktree",
            _stable_id(f"git:{common_path}"),
            Path(common_path).parent.name or root.name,
        )
    else:
        identity = ProjectIdentity(scope_id, resolved.name or "Ungrouped", str(resolved), "cwd")
    _resolve_cache[cache_key] = (now, identity)
    return identity
