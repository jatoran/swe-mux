"""Who authored the bytes the verification gate is about to run.

`worktree_verify.py` answers *what* will run and whether a human approved those exact
bytes. This module answers the question that makes it safe to skip that approval for
some of them: **where did these bytes come from.**

The distinction the whole module exists to draw is the one that changed when this
repository went public. An agent working in a worktree here already runs arbitrary
commands in that checkout all day, so letting it also edit `.worktree-verify` adds
almost nothing it could not already do - the operator's own agents are not the threat
the byte approval protects against. What *is* new is that the gate's bytes are **branch
content**, and a branch can now arrive from a stranger. Landing a fetched contributor
branch that edited the gate would have the daemon execute their script unattended,
under `base_session_env`, with no permission prompt and nobody watching.

So authority here is decided by provenance rather than by content: bytes this machine
authored, or bytes the trunk already carries, may run without a human reading them
again; bytes some other author put on the branch still present for approval exactly as
before. That keeps the frictionless case frictionless - your agents commit as you, so
they never meet the prompt - without opening the one path that was worth closing.

Nothing here decides anything on its own. It reports a verdict, and
`land_queue._verify` combines it with the Project's `land_verify_grant` before any gate
runs unapproved. Reading is done with the same bounded, read-only `--no-optional-locks`
git seam every other monitor uses, because a provenance check that took `index.lock`
would contend with the agents it is asking about.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger(__name__)

#: A bounded, read-only git reader: `(cwd, *args) -> (exit_code, output)`.
#: Injected so this module can be tested against a real repository with no daemon, and
#: so it cannot reach a git invocation that writes.
GitReader = Callable[..., Awaitable[tuple[int, str]]]

Verdict = Literal[
    "project_config",
    "uncommitted",
    "local_author",
    "foreign_author",
    "unknown",
]

#: The verdicts that permit a gate to run on the Project's standing authority rather
#: than on a human having read these exact bytes. Everything absent from this set -
#: including `unknown` - falls back to asking, because "I could not tell" and "it is
#: fine" are the two answers an authority check must never conflate.
TRUSTED_VERDICTS: frozenset[str] = frozenset(
    {"project_config", "uncommitted", "local_author"}
)

#: How many distinct author addresses a refusal names. The list exists to make a
#: refusal answerable ("who wrote this"), not to enumerate a merge.
MAX_REPORTED_AUTHORS = 8


@dataclass(frozen=True, slots=True)
class VerifyProvenance:
    """Where the bytes that would run came from, and whether that is enough."""

    verdict: Verdict
    #: Whether this verdict alone would permit a run without a fresh human approval.
    #: Still not sufficient: the Project's `land_verify_grant` decides separately.
    trusted: bool
    #: One sentence, written for a handback rather than for a log. A refusal that only
    #: said "unapproved" is what sent agents looking for the defect in their own diff.
    reason: str
    #: The author addresses behind a `local_author` / `foreign_author` verdict, in the
    #: order git reported them. Empty for every other verdict.
    authors: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "trusted": self.trusted,
            "reason": self.reason,
            "authors": list(self.authors),
        }


def _unique(values: list[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for value in values:
        cleaned = value.strip()
        if cleaned:
            seen.setdefault(cleaned, None)
    return tuple(seen)[:MAX_REPORTED_AUTHORS]


async def _configured_identity(git: GitReader, project_root: str) -> str:
    """This machine's committing address for the repository, casefolded.

    Read from the **Project root** rather than from the worktree: linked worktrees share
    one `.git/config`, so there is one answer, and asking the checkout under examination
    would let the thing being judged nominate its own judge if a worktree-local override
    ever existed.

    An unset identity is not an error and is not a hole - it simply means no author is
    trusted, so every branch-authored edit presents for approval.
    """
    code, output = await git(project_root, "config", "--get", "user.email")
    if code != 0:
        return ""
    return output.strip().casefold()


async def read_verify_provenance(
    *,
    git: GitReader,
    worktree_root: str,
    project_root: str,
    source: str,
    script_name: str,
    trunk_ref: str = "",
) -> VerifyProvenance:
    """Where this checkout's resolved verification command came from.

    `source` is `worktree_exec.CommandSource`: a `project_config` command is read from
    `.swe-mux/config.toml`, which is git-ignored per-machine state (`.docs/CLAUDE.md`),
    so no branch can carry one and there is nothing to trace.

    For the script convention the questions are asked in the order that lets the cheap,
    certain one answer first:

    1. Does the working copy differ from `HEAD`? Only something on this machine can
       have done that, whether the file is modified, staged, or untracked.
    2. Otherwise, who authored the commits that put these bytes there? If the branch
       changed the script relative to the trunk, that is the commits in `trunk..HEAD`
       touching it; if it did not, it is whoever last touched it in the repository's
       history.

    **"The trunk already carries it" is deliberately not a trusted answer on its own.**
    It reads as one - those bytes landed once - but only in a repository whose trunk is
    the operator's. A Project can be a clone of somebody else's repository, and there
    the trunk's `.worktree-verify` is a stranger's script that nobody here ever read;
    trusting it by position would execute it unattended on the first land. So the
    fallback asks the same authorship question the branch case does, and a repository
    whose gate this machine never wrote presents it for approval exactly once.

    A git failure at every step answers `unknown`, which is untrusted. That is
    deliberate: this runs to *widen* authority, so it must fail closed.
    """
    if source == "project_config":
        return VerifyProvenance(
            "project_config",
            True,
            "the command comes from this machine's .swe-mux/config.toml, which no branch carries",
        )

    pathspec = f"./{script_name}"
    code, output = await git(
        worktree_root, "status", "--porcelain", "--untracked-files=all", "--", pathspec
    )
    if code == 0 and output.strip():
        return VerifyProvenance(
            "uncommitted",
            True,
            f"{script_name} differs from HEAD in this checkout, so the bytes were written here",
        )
    if trunk_ref and trunk_ref != "HEAD":
        code, output = await git(
            worktree_root, "log", "--format=%ae", f"{trunk_ref}..HEAD", "--", pathspec
        )
        if code == 0:
            authors = _unique(output.splitlines())
            if authors:
                return await _judge_authors(
                    git, project_root, script_name, authors, where="on this branch"
                )

    # The branch did not change it, or there was no range to ask over. Either way the
    # question is the same one, asked of the repository's whole history instead of the
    # branch: who put these bytes here.
    code, output = await git(worktree_root, "log", "-1", "--format=%ae", "--", pathspec)
    if code == 0:
        authors = _unique(output.splitlines())
        if authors:
            return await _judge_authors(
                git, project_root, script_name, authors, where="in this repository"
            )
        return VerifyProvenance(
            "unknown",
            False,
            f"git reported no commit that introduced {script_name}",
        )
    return VerifyProvenance(
        "unknown",
        False,
        f"git could not report where {script_name} came from in this checkout",
    )


async def _judge_authors(
    git: GitReader,
    project_root: str,
    script_name: str,
    authors: tuple[str, ...],
    *,
    where: str,
) -> VerifyProvenance:
    """Whether every address that put these bytes here is this machine's own."""
    identity = await _configured_identity(git, project_root)
    if not identity:
        return VerifyProvenance(
            "foreign_author",
            False,
            (
                f"{script_name} was written {where} and this repository has no "
                "user.email configured, so no author can be recognised as local"
            ),
            authors,
        )
    foreign = tuple(author for author in authors if author.casefold() != identity)
    if foreign:
        return VerifyProvenance(
            "foreign_author",
            False,
            (
                f"{script_name} was written {where} by {', '.join(foreign)}, "
                "who is not this machine's committing identity"
            ),
            foreign,
        )
    return VerifyProvenance(
        "local_author",
        True,
        f"{script_name} was written {where} by this machine's own identity",
        authors,
    )


def verify_bypass_allowed(grant: str, provenance: VerifyProvenance) -> bool:
    """Whether the gate may run on the Project's standing authority.

    Both halves are required and neither is redundant. The grant is the operator saying
    "my agents may change what verification runs here"; the provenance is the daemon
    checking that these particular bytes are the operator's agents' and not a fetched
    branch's. A grant with no provenance check would make landing any contributor branch
    an unattended execution of that branch's script.
    """
    return grant == "granted" and provenance.trusted
