"""Phase 14: which gate a land runs, decided from the change set's paths alone.

The queue's verification step is the expensive one - minutes of pytest, and the same
minutes whether the branch rewrote the scheduler or fixed a typo in a heading. This
repository's own manual triage rule already says so: a branch that touched only
documentation lands immediately. This module is that rule written down deterministically
so the pipeline can apply it without anybody deciding anything.

**It stays on the executing side of the design's line.** The pipeline runs a fixed
vocabulary and never decides anything intelligent, and *matching paths against a closed
allowlist is not a decision* - it is a total function from a change set to one of two
fixed answers, with no model, no heuristic, and no configuration in it. What would cross
the line is a judgement about whether a change "looks risky"; nothing here asks that.

Everything about it fails closed, in one direction: **the full gate is the answer to
every question this module cannot answer with certainty.** An unreadable diff, an empty
one, a status letter it does not recognise, a file mode it does not recognise, a path
whose bytes did not decode - each of them runs the gate exactly as before. The cost of
being wrong is asymmetric and it is not close: a needless three-minute gate costs three
minutes, while a skipped one puts an unverified change on the trunk, where the next
branch's gate fails and blames an agent that did nothing wrong.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from .git_monitor import read_git

log = logging.getLogger(__name__)

Gate = Literal["full", "docs_only"]

#: Markdown, anywhere in the tree. Markdown is not imported, not executed, not
#: typechecked, and not linted by any gate this repository or its peers declare - it is
#: the one extension where "changing it cannot change what a test does" holds wherever
#: the file sits.
MARKDOWN_SUFFIXES = frozenset({".md"})

#: The documentation trees, matched as a **root-anchored prefix** and case-sensitively:
#: `Docs/` is a different directory from `docs/` on the filesystems Git is honest about,
#: and guessing otherwise is exactly the doubt this module resolves against.
DOCUMENTATION_TREES = (".docs/", "docs/")

#: Inside a documentation tree, and only inside one, these count as documentation too.
#: The prefix is doing real work here rather than decorating the rule: a `.png` under
#: `.docs/` is a diagram, while a `.png` under `frontend/` or `tests/` may be a fixture a
#: test compares bytes against, so the same suffix is docs in one place and not in the
#: other. Deliberately no `.py`, `.ts`, `.toml`, `.json`, or `.sh` at any depth - a
#: script that happens to live under `.docs/` is the doubt case, not the easy one.
DOCUMENTATION_TREE_SUFFIXES = frozenset(
    {".md", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
)

#: An ordinary file, and an ordinary executable file. Anything else - `160000` for a
#: submodule gitlink, `120000` for a symlink - is exotic by definition and runs the gate.
ORDINARY_MODES = frozenset({"100644", "100755"})
ABSENT_MODE = "000000"

#: Added, modified, deleted. A rename (`R`) and a copy (`C`) are excluded on purpose even
#: when both of their paths are documentation: a rename is two paths and a mode in one
#: record, it is the shape most likely to be reported differently by a future Git or a
#: differently-configured one, and the cost of running the gate on the rare docs rename
#: is three minutes. `T` (type change), `U` (unmerged) and anything unknown are excluded
#: for the same reason.
ORDINARY_STATUSES = frozenset({"A", "M", "D"})

#: How many paths a classification carries into its audit record. The full set stays in
#: Git; this is the sample a human reads in the event trail.
MAX_RECORDED_PATHS = 40


@dataclass(frozen=True, slots=True)
class ChangeEntry:
    """One record of `git diff --raw`, kept in the form Git stated it.

    Modes are kept as their six-digit strings rather than parsed: the only questions
    asked of them are equality questions, and a parse would invent failure modes that
    string comparison does not have.
    """

    src_mode: str
    dst_mode: str
    status: str
    paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GateChoice:
    """Which gate this change set earns, and the sentence that says why.

    `reason` is written to be read in an audit trail months later by someone asking
    "why did that land in nine seconds", so it states the rule that fired and the
    evidence, never just a verdict.
    """

    gate: Gate
    reason: str
    #: A bounded sample of the paths classified, for the audit record.
    paths: tuple[str, ...] = ()
    #: How many paths the change set held in total, sample or not.
    path_count: int = 0
    #: A bounded sample of what forced the full gate, empty when it did not.
    disqualifying: tuple[str, ...] = ()

    @property
    def skips_verification(self) -> bool:
        return self.gate == "docs_only"

    def public_dict(self) -> dict[str, object]:
        return {
            "gate": self.gate,
            "reason": self.reason,
            "paths": list(self.paths),
            "path_count": self.path_count,
            "disqualifying": list(self.disqualifying),
        }


def _suffix(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def _plausible_path(path: str) -> bool:
    """Whether this path is one the allowlist may be applied to at all.

    A path that arrived through `-z` is never quoted, so anything strange in it came
    from the repository rather than from the format. U+FFFD is the tell that the name
    did not decode as UTF-8, and a name that did not decode cannot be matched against an
    allowlist honestly: a mojibaked name still ending in `.md` would otherwise pass as
    markdown on the strength of bytes nobody read.
    """
    if not path or path != path.strip():
        return False
    if "\ufffd" in path:
        return False
    return all(ord(character) >= 0x20 for character in path)


def is_documentation_path(path: str) -> bool:
    """Whether one repository-relative path is documentation, by the closed allowlist.

    Two rules, and nothing else is documentation:

    1. it ends in `.md` (compared case-insensitively, because `README.MD` is the same
       kind of file everywhere it can exist), anywhere in the tree; or
    2. it lies inside `.docs/` or `docs/` at the repository root **and** ends in one of
       the documentation asset suffixes.
    """
    if not _plausible_path(path):
        return False
    suffix = _suffix(path)
    if suffix in MARKDOWN_SUFFIXES:
        return True
    if any(path.startswith(tree) for tree in DOCUMENTATION_TREES):
        return suffix in DOCUMENTATION_TREE_SUFFIXES
    return False


def parse_raw_change_set(payload: str) -> tuple[ChangeEntry, ...] | None:
    """Parse `git diff --raw -z`, or return `None` when it is not that.

    `None` means "this could not be read", which the caller turns into the full gate.
    It is returned for every departure from the documented form rather than for a
    curated list of them, because the whole value of this parser is that a shape it has
    never seen cannot be silently classified as documentation.

    The `-z` form is one NUL-terminated metadata chunk - `:<srcmode> <dstmode> <srcsha>
    <dstsha> <status>` - followed by one NUL-terminated path, or two for a rename or a
    copy. It is used rather than `--name-status` because it is the only form that
    carries the file modes, and the modes are what separate an ordinary file from a
    submodule gitlink or a symlink.
    """
    if not payload:
        return ()
    fields = payload.split("\0")
    while fields and fields[-1] == "":
        fields.pop()
    entries: list[ChangeEntry] = []
    index = 0
    while index < len(fields):
        meta = fields[index]
        if not meta.startswith(":"):
            return None
        parts = meta[1:].split()
        if len(parts) != 5:
            # A combined diff (`::`, from a merge commit) lands here, as does anything
            # else. Neither is classified; both run the gate.
            return None
        src_mode, dst_mode, _src_sha, _dst_sha, status = parts
        letter = status[:1].upper()
        if not letter.isalpha():
            return None
        wanted = 2 if letter in {"R", "C"} else 1
        if index + wanted >= len(fields):
            return None
        paths = tuple(fields[index + 1 : index + 1 + wanted])
        if not all(paths):
            return None
        entries.append(ChangeEntry(src_mode, dst_mode, letter, paths))
        index += 1 + wanted
    return tuple(entries)


def _disqualify(entry: ChangeEntry) -> str:
    """Why this one record cannot be classified as documentation, or ''.

    Ordered so the most structural objection is stated first: a rename is reported as a
    rename rather than as "two paths that are not documentation", because the first is
    the fact an operator needs and the second is a consequence of it.
    """
    if entry.status not in ORDINARY_STATUSES:
        word = {
            "R": "a rename",
            "C": "a copy",
            "T": "a file-type change",
            "U": "an unmerged path",
        }.get(entry.status, f"a `{entry.status}` change")
        return f"{word}: {' -> '.join(entry.paths)}"
    for mode in (entry.src_mode, entry.dst_mode):
        if mode != ABSENT_MODE and mode not in ORDINARY_MODES:
            kind = {"160000": "a submodule", "120000": "a symlink"}.get(mode, f"mode {mode}")
            return f"{kind}: {entry.paths[-1]}"
    if (
        entry.src_mode != ABSENT_MODE
        and entry.dst_mode != ABSENT_MODE
        and entry.src_mode != entry.dst_mode
    ):
        return f"a mode change {entry.src_mode} -> {entry.dst_mode}: {entry.paths[-1]}"
    for path in entry.paths:
        if not is_documentation_path(path):
            return path
    return ""


def classify_change_set(entries: tuple[ChangeEntry, ...] | None) -> GateChoice:
    """Decide the gate for one change set. Total, deterministic, and fail-closed.

    `None` is an unreadable change set and `()` is an empty one, and both run the full
    gate. The empty case is not an oversight: a change set that classifies as
    documentation must be one whose paths were *read and matched*, and "there were no
    paths" is evidence of nothing. A branch with genuinely nothing to land never reaches
    here - the pipeline settles it as `already_landed` before the gate.
    """
    if entries is None:
        return GateChoice(
            "full",
            "the change set could not be read, so it is not classified as documentation",
        )
    if not entries:
        return GateChoice(
            "full",
            "the change set read as empty, which is not evidence that it is documentation",
        )
    paths: list[str] = []
    disqualifying: list[str] = []
    for entry in entries:
        paths.extend(entry.paths)
        objection = _disqualify(entry)
        if objection:
            disqualifying.append(objection)
    count = len(paths)
    sample = tuple(sorted(paths)[:MAX_RECORDED_PATHS])
    if disqualifying:
        blocked = tuple(sorted(disqualifying)[:MAX_RECORDED_PATHS])
        return GateChoice(
            "full",
            f"{len(disqualifying)} of {count} changed path(s) are not documentation, "
            f"starting with {blocked[0]}",
            sample,
            count,
            blocked,
        )
    return GateChoice(
        "docs_only",
        f"all {count} changed path(s) are documentation under the "
        f"{'/'.join(sorted(MARKDOWN_SUFFIXES | DOCUMENTATION_TREE_SUFFIXES))} allowlist",
        sample,
        count,
    )


async def read_change_set(
    cwd: str, base: str, tip: str
) -> tuple[ChangeEntry, ...] | None:
    """The records a fast-forward from `base` to `tip` would apply, or `None`.

    Read against the trunk's **actual HEAD** rather than against the merge base or the
    comparison ref, because the trunk's HEAD is what `merge --ff-only` moves from and
    therefore what the trunk really gains. After the pipeline's own reconcile the trunk
    is an ancestor of the branch, so this is also exactly "merge base to tip" - the two
    readings coincide, and the one that stays correct when they do not is this one. A
    branch that merged an upstream ref the local trunk has not seen would otherwise have
    those commits classified as somebody else's problem while landing them here.
    """
    if not base or not tip or base == tip:
        return None
    code, output = await read_git(cwd, "diff", "--raw", "-z", "-M", f"{base}..{tip}")
    if code != 0:
        log.warning("land_change_set_unreadable cwd=%s base=%s tip=%s", cwd, base[:12], tip[:12])
        return None
    return parse_raw_change_set(output)
