from __future__ import annotations

import asyncio
import hashlib
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path

from .bounded_subprocess import run_bounded

#: Each query prints one path or one remote URL; 1 MiB leaves room for any of them.
_GIT_OUTPUT_LIMIT = 1024 * 1024


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
#
# It is bounded because live cwd telemetry resolves every directory a session ever
# `cd`s into: on a weeks-long daemon an unbounded dict grows one entry per distinct
# path forever, which is the "explicit bounds" invariant broken quietly.
_RESOLVE_CACHE_SECONDS = 30.0
_RESOLVE_CACHE_MAX = 1024
_resolve_cache: OrderedDict[str, tuple[float, ProjectIdentity]] = OrderedDict()


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
    """One read-only Git query's stdout, or None when it failed, timed out or is absent."""
    try:
        outcome = await run_bounded(
            ["git", "-C", str(cwd), *args],
            label="git-project-identity",
            timeout_seconds=3,
            output_limit=_GIT_OUTPUT_LIMIT,
            stderr_limit=1024,
        )
    except OSError:
        return None
    if outcome.timed_out or outcome.exit_code != 0:
        return None
    return outcome.stdout.decode("utf-8", "replace").strip()


async def resolve_project(cwd: str | Path) -> ProjectIdentity:
    resolved = Path(cwd).resolve()
    cache_key = os.path.normcase(str(resolved))
    cached = _resolve_cache.get(cache_key)
    now = time.monotonic()
    if cached and now-cached[0] < _RESOLVE_CACHE_SECONDS:
        _resolve_cache.move_to_end(cache_key)
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
    _resolve_cache.move_to_end(cache_key)
    while len(_resolve_cache) > _RESOLVE_CACHE_MAX:
        _resolve_cache.popitem(last=False)
    return identity


def rebase_identity(project: ProjectIdentity, canonical_root: str | Path) -> ProjectIdentity:
    """Return ``project`` with an explicitly registered Project root made authoritative.

    Git discovery answers "which worktree contains this path", which is the wrong
    question once a route already knows which explicit Project owns the request: a
    Project registered *inside* a larger worktree resolves to the enclosing
    toplevel, so every path derived from ``identity.root`` (notes, config,
    observations, prompts) silently lands in the outer Project. Repository-group
    metadata still describes the real worktree; only the root/scope identity is
    re-anchored.
    """
    root = Path(canonical_root).resolve()
    if os.path.normcase(str(root)) == os.path.normcase(str(Path(project.root))):
        return project
    return replace(
        project,
        id=project_scope_id(root),
        label=root.name or project.label,
        root=str(root),
    )
