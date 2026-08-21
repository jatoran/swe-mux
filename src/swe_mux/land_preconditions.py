"""Phase 14: what must be true of a repository before the pipeline touches it.

The hazard this module exists for is the one the phase originally left unnamed: the
worktree the pipeline reconciles is a checkout a live agent session owns and may be
mid-turn writing into. Merging under a running agent is how a land loses someone's
work, and no amount of care in the git vocabulary prevents it.

So preconditions are evaluated **before every mutation**, not once at enqueue, and
they fail closed: an unreadable repository blocks exactly like a dirty one, because
"the check could not be made" and "the check passed" are the two answers that must
never be conflated.

They divide into two kinds, and the difference is the whole UX of the queue:

- **Transient** (`hold`): a busy tree, a working session, a locked index. The request
  waits in a state a human can read and retries on the next sweep, because an agent
  that asks to land and keeps working is the common case, not an error.
- **Permanent** (`refuse`): a trunk that is a worktree, a missing branch, a checkout
  that is not registered for this repository. Waiting cannot fix any of them.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .git_monitor import read_git
from .path_identity import same_path

log = logging.getLogger(__name__)

Disposition = Literal["ready", "hold", "refuse", "already_landed"]

#: Long enough that an ordinary agent turn finishes inside it, short enough that a
#: request against an abandoned worktree does not sit in the queue forever.
DEFAULT_HOLD_TIMEOUT_SECONDS = 30 * 60.0


@dataclass(frozen=True, slots=True)
class PreconditionResult:
    disposition: Disposition
    reason: str = ""
    detail: dict[str, object] | None = None

    @property
    def ready(self) -> bool:
        return self.disposition == "ready"


@dataclass(frozen=True, slots=True)
class RepositoryFacts:
    """One consistent reading of both checkouts, taken together."""

    worktree_root: str = ""
    worktree_branch: str = ""
    worktree_head: str = ""
    worktree_dirty: tuple[str, ...] = ()
    worktree_registered: bool = False
    trunk_root: str = ""
    trunk_branch: str = ""
    trunk_head: str = ""
    trunk_is_main_tree: bool = False
    trunk_dirty: tuple[str, ...] = ()
    #: Paths the trunk would gain by fast-forwarding to this branch. The dirty check
    #: is scoped to these rather than to the whole checkout, because those are the
    #: only files a fast-forward can overwrite.
    incoming_paths: tuple[str, ...] = ()
    #: Whether the branch tip is already reachable from the trunk, so there is
    #: nothing left to land.
    already_landed: bool = False
    readable: bool = True
    error: str = ""


def _tracked_changes(porcelain: str) -> tuple[str, ...]:
    """Paths with staged, unstaged, or conflicted content.

    Untracked files are deliberately excluded. They cannot make a merge unsafe on
    their own, and Git refuses by itself when one would be overwritten - which is a
    better-worded refusal than any pre-check here could produce. Counting them would
    block landing on a stray build artefact.
    """
    changed: list[str] = []
    for line in porcelain.splitlines():
        if len(line) < 4 or line.startswith("??"):
            continue
        changed.append(line[3:].strip())
    return tuple(changed)


async def _rev_parse(cwd: str, *args: str) -> str:
    code, output = await read_git(cwd, "rev-parse", *args)
    return output.strip() if code == 0 else ""


async def _is_main_tree(cwd: str) -> bool:
    """True when this checkout is the repository's main tree, not a linked worktree.

    The `--absolute-git-dir` versus `--git-common-dir` comparison is the only test
    that stays correct for bare repositories and `.git`-file submodules, and both
    sides must be resolved against the directory Git ran in before they are compared
    (`git.md`). A name comparison reports every primary checkout as a worktree of
    itself.
    """
    git_dir, common_dir = await asyncio.gather(
        _rev_parse(cwd, "--absolute-git-dir"),
        _rev_parse(cwd, "--git-common-dir"),
    )
    if not git_dir or not common_dir:
        return False
    base = Path(cwd)
    return same_path(str((base / git_dir).resolve()), str((base / common_dir).resolve()))


async def _registered_worktree(trunk_root: str, worktree_root: str) -> bool:
    """Whether Git itself lists this path as a worktree of this repository.

    Git is the authority here, exactly as it is for `resolve_listed_cwd` in the spawn
    path. A directory that merely looks like a checkout is not one of ours.
    """
    code, output = await read_git(trunk_root, "worktree", "list", "--porcelain")
    if code != 0:
        return False
    for line in output.splitlines():
        if line.startswith("worktree "):
            if same_path(line[len("worktree ") :].strip(), worktree_root):
                return True
    return False


async def _incoming_paths(trunk_root: str, trunk_head: str, branch_head: str) -> tuple[str, ...]:
    """The files a fast-forward to `branch_head` would change in the trunk.

    This is the exact set a fast-forward can overwrite, and therefore the only set a
    dirty trunk can conflict with. Reading it is what keeps the pre-check honest: a
    checkout with unrelated local edits is not a reason to refuse a land, and this
    repository's own daemon writes `.swe-mux/config.toml` as ordinary operation, so a
    whole-checkout dirty test deadlocks every land on the machine running it.
    """
    if not trunk_head or not branch_head or trunk_head == branch_head:
        return ()
    code, output = await read_git(
        trunk_root, "diff", "--name-only", f"{trunk_head}..{branch_head}"
    )
    if code != 0:
        # Unknown means unknown. An empty set here would read as "nothing can
        # conflict" and skip the check entirely, so the caller is told to hold.
        return ("\0unreadable",)
    return tuple(line.strip() for line in output.splitlines() if line.strip())


async def _is_ancestor(cwd: str, ancestor: str, descendant: str) -> bool:
    if not ancestor or not descendant:
        return False
    code, _ = await read_git(cwd, "merge-base", "--is-ancestor", ancestor, descendant)
    return code == 0


async def read_repository_facts(worktree_root: str, trunk_root: str) -> RepositoryFacts:
    """Read both checkouts in one pass, and report unreadability as unreadability."""
    try:
        (
            (wt_top_code, wt_top),
            (wt_branch_code, wt_branch),
            (wt_status_code, wt_status),
            (tr_top_code, tr_top),
            (tr_branch_code, tr_branch),
            (tr_status_code, tr_status),
        ) = await asyncio.gather(
            read_git(worktree_root, "rev-parse", "--show-toplevel"),
            read_git(worktree_root, "rev-parse", "--abbrev-ref", "HEAD"),
            read_git(worktree_root, "status", "--porcelain"),
            read_git(trunk_root, "rev-parse", "--show-toplevel"),
            read_git(trunk_root, "rev-parse", "--abbrev-ref", "HEAD"),
            read_git(trunk_root, "status", "--porcelain"),
        )
    except OSError as exc:  # pragma: no cover - defensive
        return RepositoryFacts(readable=False, error=str(exc))
    if wt_top_code != 0 or tr_top_code != 0:
        return RepositoryFacts(
            readable=False,
            error=(wt_top if wt_top_code != 0 else tr_top) or "git could not read the checkout",
        )
    worktree_head, trunk_head, main_tree, registered = await asyncio.gather(
        _rev_parse(worktree_root, "HEAD"),
        _rev_parse(trunk_root, "HEAD"),
        _is_main_tree(trunk_root),
        _registered_worktree(trunk_root, worktree_root),
    )
    incoming, already_landed = await asyncio.gather(
        _incoming_paths(trunk_root, trunk_head, worktree_head),
        _is_ancestor(trunk_root, worktree_head, trunk_head),
    )
    return RepositoryFacts(
        worktree_root=wt_top,
        worktree_branch=wt_branch if wt_branch_code == 0 else "",
        worktree_head=worktree_head,
        worktree_dirty=_tracked_changes(wt_status) if wt_status_code == 0 else (),
        worktree_registered=registered,
        trunk_root=tr_top,
        trunk_branch=tr_branch if tr_branch_code == 0 else "",
        trunk_head=trunk_head,
        trunk_is_main_tree=main_tree,
        trunk_dirty=_tracked_changes(tr_status) if tr_status_code == 0 else (),
        incoming_paths=incoming,
        already_landed=already_landed,
        readable=wt_status_code == 0 and tr_status_code == 0,
        error="" if wt_status_code == 0 and tr_status_code == 0 else "git status failed",
    )


def evaluate_preconditions(
    facts: RepositoryFacts,
    *,
    branch: str,
    busy_sessions: tuple[str, ...] = (),
    lands: bool = True,
) -> PreconditionResult:
    """Decide whether the pipeline may touch these two checkouts right now.

    ``lands`` is whether this request will end by moving the trunk. Every check here
    guards the *worktree* except the last, which guards the trunk's own uncommitted
    work; a verify-only request never writes the trunk, so holding it on that check
    would make it wait forever for a hazard it cannot cause. Nothing else is relaxed -
    the trunk still has to be identifiable and the worktree still has to be quiet and
    clean, because the reconcile writes into it either way.
    """
    if not facts.readable:
        # Unreadable is a hold, not a refusal: a transient lock or a slow filesystem
        # is the common cause, and refusing would turn a hiccup into a handback.
        return PreconditionResult(
            "hold", f"the repository could not be read ({facts.error or 'unknown error'})"
        )
    if not facts.trunk_is_main_tree:
        return PreconditionResult(
            "refuse",
            "the land target is a linked worktree, not the Project's primary checkout",
            {"trunk_root": facts.trunk_root},
        )
    if same_path(facts.trunk_root, facts.worktree_root):
        return PreconditionResult(
            "refuse", "the branch worktree and the land target are the same checkout"
        )
    if not facts.worktree_registered:
        return PreconditionResult(
            "refuse",
            "the checkout is not a registered worktree of this repository",
            {"worktree_root": facts.worktree_root},
        )
    if facts.worktree_branch != branch:
        return PreconditionResult(
            "refuse",
            f"the worktree is on {facts.worktree_branch or 'a detached HEAD'}, not {branch}",
            {"branch": facts.worktree_branch},
        )
    # Checked before the busy and dirty holds, because none of them are reasons to
    # wait for something that has already happened. The branch tip is reachable from
    # the trunk, so a fast-forward would move nothing and the whole pipeline - three
    # minutes of it, almost all verification - would prove nothing.
    if facts.already_landed:
        return PreconditionResult(
            "already_landed",
            "this branch is already on the trunk; there is nothing to land",
            {"trunk_head": facts.trunk_head, "branch_head": facts.worktree_head},
        )
    if busy_sessions:
        return PreconditionResult(
            "hold",
            f"{len(busy_sessions)} session(s) in the worktree are still working",
            {"sessions": list(busy_sessions)},
        )
    if facts.worktree_dirty:
        return PreconditionResult(
            "hold",
            f"the worktree has {len(facts.worktree_dirty)} uncommitted change(s)",
            {"paths": list(facts.worktree_dirty[:20])},
        )
    if not lands:
        # A verify-only request stops at the gate, so the trunk's own uncommitted work
        # is not a hazard it can reach and not a reason to make it wait.
        return PreconditionResult("ready")
    # **Only the paths a fast-forward would actually touch.** A dirty file the merge
    # will not write cannot make the merge unsafe, and `--ff-only` refuses precisely
    # on the ones that would be - so a whole-checkout test is not a stricter safety
    # net, it is a different and wrong question. It also deadlocks by construction on
    # any machine whose daemon writes into its own primary checkout: enabling this
    # feature writes `.swe-mux/config.toml`, which would then hold every land forever.
    if "\0unreadable" in facts.incoming_paths:
        return PreconditionResult(
            "hold", "the incoming change set could not be read from the trunk"
        )
    blocked = sorted(set(facts.trunk_dirty) & set(facts.incoming_paths))
    if blocked:
        # A hold rather than a refusal because the fix is usually the operator
        # committing their own edits, and the request should still land after they do
        # rather than needing to be made again.
        return PreconditionResult(
            "hold",
            f"the primary checkout has local changes to {len(blocked)} file(s) this land "
            "would overwrite",
            {"paths": blocked[:20]},
        )
    return PreconditionResult("ready")
