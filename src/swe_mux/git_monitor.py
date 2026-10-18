from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
from typing import Any, TypeVar

from .background_tasks import background
from .bounded_subprocess import run_bounded
from .event_bus import EventBus
from .git_readonly import READ_ONLY_GIT_OPTIONS
from .git_review import resolve_comparison_ref
from .git_watch import CheckoutLayout, CheckoutWatches
from .logsetup import bound_request_id, current_request_id, new_request_id
from .models import GitState
from .session import Session, SessionManager
from .session_patch import session_patch

log = logging.getLogger(__name__)

GIT_MONITOR_LOOP = "git-monitor"
#: The load-shedding name for the monitor's safety sweeps, which wait while the loop
#: drowns; the monitor's own tick, and the reads a change causes, never do.
GIT_SWEEP_DEFERRAL = "git-sweep"
GIT_TIMEOUT_SECONDS = 4.0
GIT_CONCURRENCY = 4
#: Git's ignored-path listing walks the working tree like `git status` does, once
#: per watched checkout and again only when a `.gitignore` moves. It gets longer
#: than an ordinary query because a slow answer here delays nothing.
IGNORED_LISTING_TIMEOUT_SECONDS = 15.0
#: What one Git query may hold in memory. Every caller here parses what it gets
#: back, so a capture that lost its middle is not a smaller answer - it is a wrong
#: one, and is reported as `GIT_OUTPUT_CAPPED` rather than returned.
GIT_OUTPUT_LIMIT_BYTES = 16 * 1024 * 1024
#: The ignored-path listing's own cap, for the same reason its timeout is longer: it
#: is read once per checkout every 15 minutes, not on every poll. Measured
#: 2026-10-13, a checkout whose pipeline writes ignored files beside tracked ones
#: lists 130,892 of them one by one (`--directory` collapses only a directory that
#: is ignored whole), 17.2 MB in 0.68s. Under the shared cap that listing failed on
#: every refresh, which left the checkout with the most noise as the one checkout
#: with no ignore rules at all.
IGNORED_LISTING_LIMIT_BYTES = 64 * 1024 * 1024
#: Beside the reserved 124 for a timeout, and for the same reason: a caller has to
#: be able to tell "Git could not answer" from "Git answered nothing".
GIT_OUTPUT_CAPPED = 125
#: Repository roots retained in the diffstat memo. One entry per checkout the
#: fleet has open, so this is bounded by how many worktrees exist, not by time.
DIFFSTAT_CACHE_LIMIT = 256

T = TypeVar("T")
_FULL_OID = re.compile(r"^[0-9a-fA-F]{40,64}$")


def _normalized_path(value: str) -> str:
    return os.path.normcase(os.path.normpath(value))


async def _git(
    cwd: str,
    *args: str,
    timeout_seconds: float = GIT_TIMEOUT_SECONDS,
    output_limit: int = GIT_OUTPUT_LIMIT_BYTES,
) -> tuple[int, str]:
    """Run one bounded, **read-only** Git query and always reap the subprocess.

    Code 124 is reserved for a timeout so API callers can return a typed diagnostic
    instead of hanging a terminal-facing request indefinitely, and 125 for a capture
    that hit the output cap - every caller here parses what it gets, so a truncated
    answer must read as a failure rather than as a shorter repository.

    `READ_ONLY_GIT_OPTIONS` is what makes this read-only, and it is not a tuning knob.
    `git status` refreshes the index and *writes it back* whenever any tracked file's
    mtime has moved, taking `.git/index.lock` to do so. In a repository where agents
    are editing files that is every single poll, so a monitor that merely wanted to
    read the branch name was writing to the user's repository every 5 seconds and
    contending for the lock with the agents it was watching. Verified 2026-08-05 by
    touching a tracked file and comparing `.git/index` mtime across both forms: plain
    `status` rewrote it, `--no-optional-locks status` did not, with byte-identical
    output. `--no-optional-locks` alone does not cover `git diff`, which this module
    runs for every diffstat; `git_readonly.py` has that measurement and the
    `diff.autoRefreshIndex=false` that closes it.

    The failure mode that makes this more than waste: a write in flight when the
    daemon is killed can strand `index.lock`, which blocks *every* git operation in
    that repository for every agent until someone removes it by hand. One such lock
    was found stranded in this repo, created within seconds of a daemon restart.

    Git documents this flag for exactly this caller: tools that poll a repository for
    display. Latency is unaffected (measured 15.1ms against 14.8ms); the point is that
    a monitor must not mutate what it monitors.
    """
    try:
        outcome = await run_bounded(
            ("git", *READ_ONLY_GIT_OPTIONS, "-C", cwd, *args),
            label="git",
            timeout_seconds=timeout_seconds,
            output_limit=output_limit,
            stderr_limit=GIT_OUTPUT_LIMIT_BYTES,
            # Whatever caused this query: an HTTP request's id when one is in
            # flight, the poll's own minted id inside the monitor loop, and empty
            # only where neither exists — which is the honest answer, not a
            # missing field.
            operation_id=current_request_id() or None,
        )
    except OSError:
        return 1, ""
    if outcome.timed_out:
        return 124, f"git timed out after {timeout_seconds:g}s"
    if outcome.truncated:
        return GIT_OUTPUT_CAPPED, f"git output exceeded {output_limit} bytes"
    code = outcome.exit_code or 0
    output = outcome.stdout if code == 0 else outcome.stderr or outcome.stdout
    return code, output.decode("utf-8", "replace").strip()


#: The read-only runner under its public name, for callers outside this module that
#: need one bounded Git query. Exported rather than reimplemented so no second caller
#: can forget `READ_ONLY_GIT_OPTIONS` and start writing to the repository it is reading.
read_git = _git


@dataclass(slots=True, frozen=True)
class GitEvidence:
    """Deterministic Tier 0 git facts for one repository root.

    `head` is the exact commit the work happened at; `dirty_hash` fingerprints the
    working-tree change set (paths + status codes, order-independent). Together
    they let a provenance consumer say *which* tree a fact was produced against
    — `GitState` alone only reports a dirty file count, which is not an identity.
    """

    head: str | None = None
    dirty_hash: str | None = None

    def as_payload(self) -> dict[str, str | None]:
        return {"head": self.head, "dirty_hash": self.dirty_hash}


@dataclass(slots=True, frozen=True)
class GitReading:
    state: GitState
    evidence: GitEvidence
    #: Where the checkout keeps its tree and its Git directory, resolved. What the
    #: monitor watches for changes; None when Git could not say (not a repository,
    #: or the identity query failed), which leaves the checkout on timed polling.
    layout: CheckoutLayout | None = None


@dataclass(slots=True, frozen=True)
class GitPosition:
    root: str
    head: str


@dataclass(slots=True, frozen=True)
class GitCommitMetadata:
    oid: str
    parents: tuple[str, ...]
    committed_at: float
    subject: str


async def read_git_position(cwd: str) -> GitPosition | None:
    """Read only the worktree root and HEAD used at a command boundary."""
    (root_code, root), (head_code, head) = await asyncio.gather(
        _git(cwd, "rev-parse", "--show-toplevel"),
        _git(cwd, "rev-parse", "HEAD"),
    )
    oid = head.strip()
    if root_code or head_code or not root or not _FULL_OID.fullmatch(oid):
        return None
    return GitPosition(root=root, head=oid.lower())


async def read_commit_metadata(cwd: str, oid: str) -> GitCommitMetadata | None:
    """Read bounded immutable metadata for one exact commit object."""
    if not _FULL_OID.fullmatch(oid):
        return None
    code, value = await _git(cwd, "show", "-s", "--format=%H%x00%P%x00%ct%x00%s", oid)
    if code:
        return None
    parts = value.split("\0", 3)
    if len(parts) != 4 or not _FULL_OID.fullmatch(parts[0]):
        return None
    try:
        committed_at = float(parts[2])
    except ValueError:
        return None
    parents = tuple(parent.lower() for parent in parts[1].split() if _FULL_OID.fullmatch(parent))
    return GitCommitMetadata(
        oid=parts[0].lower(),
        parents=parents,
        committed_at=committed_at,
        subject=parts[3][:512],
    )


@dataclass(slots=True, frozen=True)
class GitCommitChange:
    """One file a commit changed, with the object it left behind.

    `blob` is the *post-image* object id, so it is the bytes the commit actually
    stored; a deletion carries None. Attribution reads this rather than a patch:
    a blob is the whole file, which is what a write fact can be compared against.
    """

    path: str
    status: str
    blob: str | None


#: Commits examined when isolating which one a commit command produced. A command
#: that moved more than this many commits is a rebase or a merge, not one commit,
#: and is classified as such rather than searched.
COMMIT_RANGE_LIMIT = 50
#: Changed files read per commit for contributor attribution.
COMMIT_CHANGE_LIMIT = 400
#: Blob bytes hashed for an exact content match. A blob past this is compared by
#: path alone; hashing an arbitrarily large object on the event path is the one
#: thing this must not do.
BLOB_DIGEST_MAX_BYTES = 2 * 1024 * 1024

_RAW_CHANGE = re.compile(
    r"^:(\d{6}) (\d{6}) ([0-9a-f]{40,64}) ([0-9a-f]{40,64}) ([A-Z])(\d*)$"
)
_EMPTY_OID = re.compile(r"^0{40,64}$")


async def read_is_ancestor(cwd: str, ancestor: str, descendant: str) -> bool | None:
    """Whether `ancestor` is reachable from `descendant`. None when git cannot say.

    This is the one question that separates a reference moving *forward* — a
    commit, a fast-forward, a merge — from one that was rewritten out from under
    its old position by a rebase or a reset. Guessing between those two produced
    the "a merge or a rebase" non-answer; git decides it in a single call.
    """
    if not _FULL_OID.fullmatch(ancestor) or not _FULL_OID.fullmatch(descendant):
        return None
    code, _ = await _git(cwd, "merge-base", "--is-ancestor", ancestor, descendant)
    # 0 and 1 are the answer; anything else (a missing object, a timeout) is git
    # declining to answer, and must not read as "no".
    if code in (0, 1):
        return code == 0
    return None


async def read_commit_range(
    cwd: str,
    base: str | None,
    head: str,
    *,
    limit: int = COMMIT_RANGE_LIMIT,
    first_parent: bool = False,
) -> tuple[GitCommitMetadata, ...]:
    """Commits reachable from `head` but not from `base`, newest first.

    This is what isolates *which* commit a session's command produced when a
    sibling session committed into the same checkout in between: reading HEAD back
    after the command answers "what is on top now", which is a different question.
    An amend is covered by the same range — the replaced commit stops being an
    ancestor, so the rewritten one appears here.

    `first_parent` walks only the reference's *own* line of development, and is
    what tells a merge apart from a bulk arrival. Full ancestry counts the side
    branch a merge absorbed, so `git merge master` creating one commit and a
    two-commit fast-forward both report two — which is precisely the collision
    that stamped the session that ran the merge `ambiguous`.
    """
    if not _FULL_OID.fullmatch(head) or (base is not None and not _FULL_OID.fullmatch(base)):
        return ()
    selector = f"{base}..{head}" if base else head
    code, output = await _git(
        cwd,
        "log",
        f"--max-count={max(1, limit)}",
        *(("--first-parent",) if first_parent else ()),
        "--format=%H%x00%P%x00%ct%x00%s",
        selector,
    )
    if code:
        return ()
    commits: list[GitCommitMetadata] = []
    for line in output.splitlines():
        parts = line.split("\0", 3)
        if len(parts) != 4 or not _FULL_OID.fullmatch(parts[0]):
            continue
        try:
            committed_at = float(parts[2])
        except ValueError:
            continue
        commits.append(
            GitCommitMetadata(
                oid=parts[0].lower(),
                parents=tuple(
                    parent.lower() for parent in parts[1].split() if _FULL_OID.fullmatch(parent)
                ),
                committed_at=committed_at,
                subject=parts[3][:512],
            )
        )
    return tuple(commits)


def parse_raw_changes(
    output: str, *, limit: int = COMMIT_CHANGE_LIMIT
) -> tuple[GitCommitChange, ...]:
    """Parse `diff-tree --raw -z` into post-image file changes.

    NUL-delimited because a path may legally contain anything but NUL, and the
    quoted-path form git falls back to without `-z` would have to be unescaped
    before it could be compared with a recorded write target.
    """
    tokens = output.split("\0")
    changes: list[GitCommitChange] = []
    index = 0
    while index < len(tokens) and len(changes) < limit:
        meta = _RAW_CHANGE.match(tokens[index].strip())
        if meta is None:
            index += 1
            continue
        status = meta.group(5)
        # A rename or copy carries source *and* destination; the destination is
        # the path the commit now holds, which is the one a write can match.
        paths_needed = 2 if status in {"R", "C"} else 1
        paths = tokens[index + 1 : index + 1 + paths_needed]
        index += 1 + paths_needed
        if len(paths) < paths_needed or not paths[-1]:
            continue
        blob = meta.group(4)
        changes.append(
            GitCommitChange(
                path=paths[-1],
                status=status,
                blob=None if _EMPTY_OID.fullmatch(blob) else blob.lower(),
            )
        )
    return tuple(changes)


def parse_combined_changes(
    output: str, *, limit: int = COMMIT_CHANGE_LIMIT
) -> tuple[GitCommitChange, ...]:
    """Parse `diff-tree -c --raw -z` into the files a merge itself decided.

    The combined raw format is not the ordinary one and cannot be read by the same
    expression: it carries one leading colon *per parent*, then N+1 modes, then
    N+1 object ids, then one status letter per parent. Reading it with the
    single-parent regex silently yields nothing, which would look exactly like the
    "a merge changed no files" answer this exists to replace.

    The last object id is the merge's own post-image, which is the blob a write
    fact can be compared against. Renames are absent by construction — `-c` does
    not detect them — so every record carries exactly one path.
    """
    changes: list[GitCommitChange] = []
    tokens = output.split("\0")
    index = 0
    while index + 1 < len(tokens) and len(changes) < limit:
        record = tokens[index].strip()
        index += 1
        if not record.startswith(":"):
            continue
        parents = len(record) - len(record.lstrip(":"))
        fields = record.split()
        # 1 mode-with-colons + N further modes + N+1 object ids + 1 status run.
        if parents < 2 or len(fields) != 2 * parents + 3:
            continue
        status = fields[-1]
        blob = fields[-2]
        if len(status) != parents or not status.isalpha() or not status.isupper():
            continue
        path = tokens[index]
        index += 1
        if not path:
            continue
        changes.append(
            GitCommitChange(
                path=path,
                status=status,
                blob=None if _EMPTY_OID.fullmatch(blob) else blob.lower(),
            )
        )
    return tuple(changes)


async def read_merge_resolution_changes(
    cwd: str, oid: str, *, limit: int = COMMIT_CHANGE_LIMIT
) -> tuple[GitCommitChange, ...]:
    """Files a merge commit holds that match **none** of its parents.

    This is `git diff-tree -c`, and the choice of `-c` over `-m`/`--first-parent`
    is the whole scoping rule. A first-parent diff of a landing merge is every
    change the trunk brought in, and a `-m` diff is that plus the entire branch —
    so either one would attribute the whole of one session's branch to whoever ran
    the merge. The combined diff lists exactly the paths the merge *resolved*: a
    file taken wholesale from either side never appears in it, and a file whose
    conflict someone settled by hand always does.
    """
    if not _FULL_OID.fullmatch(oid):
        return ()
    code, output = await _git(
        cwd,
        "diff-tree",
        "--no-commit-id",
        "-c",
        "-r",
        "-z",
        "--no-ext-diff",
        "--no-textconv",
        "--raw",
        oid,
    )
    if code:
        return ()
    return parse_combined_changes(output, limit=limit)


async def read_excluded_range(
    cwd: str, include: str, exclude: tuple[str, ...], *, limit: int = COMMIT_RANGE_LIMIT
) -> tuple[str, ...]:
    """Commits reachable from `include` and from none of `exclude`, newest first.

    `git rev-list include ^e1 ^e2`. For a merge commit this answers "what did this
    side have that the other side did not", which is the branch a merge unified
    rather than the history under it.
    """
    if not _FULL_OID.fullmatch(include) or any(
        not _FULL_OID.fullmatch(item) for item in exclude
    ):
        return ()
    code, output = await _git(
        cwd,
        "rev-list",
        f"--max-count={max(1, limit)}",
        include,
        *(f"^{item}" for item in exclude),
    )
    if code:
        return ()
    return tuple(
        line.strip().lower() for line in output.splitlines() if _FULL_OID.fullmatch(line.strip())
    )


async def read_commit_changes(
    cwd: str, oid: str, *, limit: int = COMMIT_CHANGE_LIMIT
) -> tuple[GitCommitChange, ...]:
    """Files one commit changed against its first parent, post-image objects.

    A merge produces no output here by design: `diff-tree` without `-c`/`-m` says
    nothing about a merge. That is right for this question — none of these bytes
    are a merge's own decision — and `read_merge_resolution_changes` asks the
    question a merge *does* have an answer to.
    """
    if not _FULL_OID.fullmatch(oid):
        return ()
    code, output = await _git(
        cwd,
        "diff-tree",
        "--no-commit-id",
        "-r",
        "-z",
        "--root",
        "--find-renames",
        "--no-ext-diff",
        "--no-textconv",
        "--raw",
        oid,
    )
    if code:
        return ()
    return parse_raw_changes(output, limit=limit)


async def _git_bytes(cwd: str, *args: str, timeout_seconds: float = GIT_TIMEOUT_SECONDS) -> bytes:
    """Run one bounded read-only Git query and return stdout undecoded.

    Blob content cannot go through the decoding runner: `errors="replace"` rewrites
    every byte it cannot decode, so the digest of the result would be the digest of
    a repaired string rather than of the file git stores.
    """
    try:
        outcome = await run_bounded(
            ("git", *READ_ONLY_GIT_OPTIONS, "-C", cwd, *args),
            label="git-bytes",
            timeout_seconds=timeout_seconds,
            # The only caller digests a blob, and `read_blob_digest` has already
            # refused anything above this by asking `cat-file -s` first - so a
            # capture that hits the cap is one whose bytes are not the file's, and
            # an empty answer is the only honest one.
            output_limit=BLOB_DIGEST_MAX_BYTES,
            stderr_limit=GIT_OUTPUT_LIMIT_BYTES,
            operation_id=current_request_id() or None,
        )
    except OSError:
        return b""
    if outcome.timed_out or outcome.stdout_truncated or outcome.exit_code != 0:
        return b""
    return outcome.stdout


async def read_blob_digest(cwd: str, blob: str) -> str | None:
    """SHA-256 of one blob's exact bytes, or None when it is absent or too large.

    The same digest a write fact carries for whole-file content
    (`tool_evidence.tool_call_evidence`), which is what makes an equality comparison
    between "what the agent wrote" and "what the commit stored" meaningful. Git's
    own object id is not comparable: it is SHA-1 over a `blob <len>\\0` header.
    """
    if not _FULL_OID.fullmatch(blob):
        return None
    code, size = await _git(cwd, "cat-file", "-s", blob)
    if code:
        return None
    try:
        blob_size = int(size.strip())
    except ValueError:
        return None
    if blob_size > BLOB_DIGEST_MAX_BYTES:
        return None
    if blob_size == 0:
        return hashlib.sha256(b"").hexdigest()
    data = await _git_bytes(cwd, "cat-file", "blob", blob)
    if not data:
        return None
    return hashlib.sha256(data).hexdigest()


def _dirty_hash(porcelain: str) -> str | None:
    """Order-independent fingerprint of the working-tree change set."""
    lines = sorted(line.strip() for line in porcelain.splitlines() if line.strip())
    if not lines:
        return None
    return hashlib.sha256("\n".join(lines).encode("utf-8", "replace")).hexdigest()[:16]


def _resolve_layout(
    cwd: str, root: str, git_dir: str, common_dir: str
) -> tuple[str | None, CheckoutLayout | None]:
    """(linked worktree name, layout) for a checkout. Filesystem-bound: off the loop.

    The name is the leaf of `root` when it is a linked worktree, else None. A
    linked worktree's `--git-dir` lives under the primary checkout's
    `.git/worktrees/<name>`, so it differs from `--git-common-dir`; the primary
    checkout reports the same path for both. Comparing the two paths is the only
    check that stays correct for bare repositories and `.git`-file submodules,
    where comparing directory *names* does not.

    Both paths are resolved against `cwd` first, and that is the whole
    correctness of this function. `--absolute-git-dir` promises an absolute
    answer for the git dir **only**: `--git-common-dir` still replies relatively
    whenever it can — `.git` from a repository root, `../.git` from a
    subdirectory — and relative to *the directory git ran in*, not to the
    toplevel. Resolved against this process's cwd instead, those never matched
    the git dir, so every primary checkout compared unequal to itself and was
    reported as a linked worktree named after the repository folder.

    The same two resolved paths are what the monitor watches, so they are
    computed once here for both purposes.
    """
    if not git_dir or not common_dir:
        return None, None
    try:
        base = Path(cwd)
        resolved_git = (base / git_dir).resolve()
        resolved_common = (base / common_dir).resolve()
    except OSError:
        return None, None
    worktree = None if resolved_git == resolved_common else (Path(root).name or None)
    layout = CheckoutLayout(
        toplevel=str(Path(root)), git_dir=str(resolved_git), common_dir=str(resolved_common)
    )
    return worktree, layout


def _worktree_name(cwd: str, root: str, git_dir: str, common_dir: str) -> str | None:
    """Leaf name of `root` when it is a linked worktree, else None (see `_resolve_layout`)."""
    return _resolve_layout(cwd, root, git_dir, common_dir)[0]


#: (cwd, root, git dir, common dir) as Git reported them -> `_resolve_layout`'s
#: answer. What `rev-parse` said about a directory does not move while the
#: directory exists, and resolving those paths is filesystem work: two `realpath`
#: calls per poll on the event loop were a recurring loop-stall frame (2026-10-12).
#: A different answer from Git is a different key, so a checkout that is recreated
#: as something else is never served a stale layout.
_layout_memo: OrderedDict[
    tuple[str, str, str, str], tuple[str | None, CheckoutLayout | None]
] = OrderedDict()


async def _checkout_layout(
    cwd: str, root: str, git_dir: str, common_dir: str
) -> tuple[str | None, CheckoutLayout | None]:
    key = (cwd, root, git_dir, common_dir)
    cached = _layout_memo.get(key)
    if cached is not None:
        _layout_memo.move_to_end(key)
        return cached
    value = await asyncio.to_thread(_resolve_layout, *key)
    _layout_memo[key] = value
    _trim(_layout_memo)
    return value


#: root -> (dirty_hash, added, removed). Keyed by the working-tree fingerprint the
#: cheap poll already computes, so `git diff --numstat` runs only when the change
#: set actually moved — not once per session, and not once per five-second poll.
_diffstat_memo: OrderedDict[str, tuple[str | None, int, int]] = OrderedDict()

#: (root, ref) -> ((compare oid, head, dirty_hash), added, removed, files). The
#: branch-scoped diff moves for a strictly larger set of reasons than the
#: HEAD-scoped one: committing changes it while leaving `dirty_hash` alone, and
#: the base itself advances under it. All three therefore key the memo.
_compare_memo: OrderedDict[
    tuple[str, str], tuple[tuple[str, str, str | None], int, int, int]
] = OrderedDict()

#: (root, override) -> (expires_at monotonic, resolved ref). Inference costs
#: several git calls and answers a question that changes when a remote's HEAD is
#: re-pointed or a branch is created — never between two polls seconds apart.
_compare_ref_cache: OrderedDict[tuple[str, str | None], tuple[float, str | None]] = OrderedDict()

#: Seconds a resolved comparison ref is trusted before being inferred again.
COMPARE_REF_TTL_SECONDS = 60.0

#: Seconds a line count Git could not produce is left alone before it is attempted
#: again, whatever else holds. Measured 2026-10-12: a checkout with 1,485 modified
#: files, several of them JSON documents of 100k+ lines, needs 13s for
#: `git diff --numstat HEAD` and as long again for its branch comparison. Both hit
#: `GIT_TIMEOUT_SECONDS` on every read, and nothing remembered that, so every read
#: of that checkout - every 30s detached, every 5s with a pane open - spawned two
#: diffs that each burned 4s of CPU and were then killed, for an answer that never
#: arrived. This bound re-tests a failure that load, rather than size, may have
#: caused; `_Failure` says what else lets one be asked sooner.
DIFFSTAT_FAILURE_RETRY_SECONDS = 600.0

#: Exit codes that say a change set was too big to count, rather than that Git
#: refused: the timeout and the output cap. Such a failure is a property of how
#: much changed, so it outlives churn in *what* changed. Measured 2026-10-13 on the
#: same checkout, with the failure keyed on the exact fingerprint: 158 of 192
#: timed-out diffs ran again inside their 600s, because each file an agent added to
#: a 1,871-entry change set moved the fingerprint without making the count any
#: cheaper.
_SIZE_FAILURES = frozenset({124, GIT_OUTPUT_CAPPED})


@dataclass(slots=True, frozen=True)
class _Failure:
    """A count Git could not produce, and what has to move before it is asked again.

    A refusal (a bad revision, a missing object) stands for exactly the fingerprint
    it failed on, because a different change set is a different question. A size
    failure (`_SIZE_FAILURES`) instead stands until the change set has shrunk to
    `shrink_to` entries: committing or discarding most of it is what can make it
    countable, and adding to it is not. Either is re-tested once `retry_at` passes.
    """

    fingerprint: object
    retry_at: float
    shrink_to: int | None = None

    def stands(self, fingerprint: object, dirty: int, now: float) -> bool:
        if now >= self.retry_at:
            return False
        if self.shrink_to is not None:
            return dirty > self.shrink_to
        return fingerprint == self.fingerprint


def _shrink_floor(code: int, dirty: int) -> int | None:
    """For a diff that ran out of budget, the change-set size that earns another try.

    Half the set it failed on, which bounds what a shrinking set can cost to the
    logarithm of its size. A set that failed empty (a comparison of committed work
    alone) has nowhere to shrink to, so only the bound re-tests it. None for every
    other failure, which follows its fingerprint instead.
    """
    if code not in _SIZE_FAILURES:
        return None
    return dirty // 2 if dirty else -1


#: root -> the HEAD diffstat that failed.
_diffstat_failures: OrderedDict[str, _Failure] = OrderedDict()

#: (root, ref) -> the branch-scoped comparison's diff that failed.
_compare_failures: OrderedDict[tuple[str, str], _Failure] = OrderedDict()

#: (root, ref) -> a merge base Git could not find: unrelated histories, a shallow
#: clone, a timeout. It depends on the two commits alone, so it is keyed on them,
#: and nothing in the working tree can ask it again.
_merge_base_failures: OrderedDict[tuple[str, str], _Failure] = OrderedDict()


def reset_diffstat_cache() -> None:
    """Drop every memoized diffstat, comparison ref, and layout. For tests and restart."""
    _diffstat_memo.clear()
    _compare_memo.clear()
    _compare_ref_cache.clear()
    _layout_memo.clear()
    _diffstat_failures.clear()
    _compare_failures.clear()
    _merge_base_failures.clear()


def _trim(cache: OrderedDict[Any, Any]) -> None:
    while len(cache) > DIFFSTAT_CACHE_LIMIT:
        cache.popitem(last=False)


def _failure_standing[K](
    failures: OrderedDict[K, _Failure], key: K, fingerprint: object, dirty: int
) -> bool:
    """Whether an earlier failure for `key` still answers this read without Git."""
    entry = failures.get(key)
    if entry is None:
        return False
    if not entry.stands(fingerprint, dirty, monotonic()):
        del failures[key]
        return False
    failures.move_to_end(key)
    return True


def _record_failure[K](
    failures: OrderedDict[K, _Failure], key: K, fingerprint: object, shrink_to: int | None
) -> None:
    failures[key] = _Failure(fingerprint, monotonic() + DIFFSTAT_FAILURE_RETRY_SECONDS, shrink_to)
    failures.move_to_end(key)
    _trim(failures)


def _memoized_diffstat(root: str, dirty_hash: str | None) -> tuple[int, int] | None:
    cached = _diffstat_memo.get(root)
    if cached is None or cached[0] != dirty_hash:
        return None
    _diffstat_memo.move_to_end(root)
    return cached[1], cached[2]


def _memoize_diffstat(root: str, dirty_hash: str | None, added: int, removed: int) -> None:
    _diffstat_memo[root] = (dirty_hash, added, removed)
    _diffstat_memo.move_to_end(root)
    _trim(_diffstat_memo)


def parse_numstat_summary(output: str) -> tuple[int, int, int]:
    """Added lines, removed lines, and changed files from `git diff --numstat`.

    Binary files report `-` for both counts. They contribute no lines rather than
    aborting the sum — a repository with one PNG in it still has a line count —
    but they are real changed files and do count toward the file total.
    """
    added = removed = files = 0
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        if parts[0].isdigit():
            added += int(parts[0])
        if parts[1].isdigit():
            removed += int(parts[1])
    return added, removed, files


def parse_numstat(output: str) -> tuple[int, int]:
    added, removed, _ = parse_numstat_summary(output)
    return added, removed


async def _read_diffstat(
    cwd: str, root: str, dirty_hash: str | None, dirty: int, has_head: bool
) -> tuple[int | None, int | None]:
    """Lines added/removed vs HEAD, memoized on the working-tree fingerprint.

    A clean tree is answered without touching git at all: no change set means no
    changed lines, and that is the common case across an idle fleet. A failure is
    memoized too (`_Failure`), because a change set too large to count inside the
    timeout stays too large on the next read. A clean tree is the one reading that
    proves a size failure obsolete, so it forgets one outright.
    """
    if not has_head:
        return None, None
    if dirty_hash is None:
        _diffstat_failures.pop(root, None)
        _memoize_diffstat(root, None, 0, 0)
        return 0, 0
    cached = _memoized_diffstat(root, dirty_hash)
    if cached is not None:
        return cached
    if _failure_standing(_diffstat_failures, root, dirty_hash, dirty):
        return None, None
    code, output = await _git(cwd, "diff", "--numstat", "HEAD")
    if code:
        shrink_to = _shrink_floor(code, dirty)
        _record_failure(_diffstat_failures, root, dirty_hash, shrink_to)
        log.info(
            "git_diffstat_unavailable root=%s code=%d dirty=%d retry_s=%.0f shrink_to=%s "
            "diagnostic=%s",
            root,
            code,
            dirty,
            DIFFSTAT_FAILURE_RETRY_SECONDS,
            shrink_to,
            output[:200],
        )
        return None, None
    added, removed = parse_numstat(output)
    _memoize_diffstat(root, dirty_hash, added, removed)
    return added, removed


async def _comparison_ref(cwd: str, root: str, override: str | None) -> str | None:
    """The checkout's comparison base, inferred at most once per TTL per root.

    Resolution is several git calls and its answer changes when a remote HEAD is
    re-pointed or a branch appears — never between two polls five seconds apart —
    so caching it is what makes a branch-scoped diff affordable on the monitor's
    cadence. The inference itself is `git_review`'s, not a second copy: one
    implementation is why the sidebar and the Git drawer cannot disagree about
    which base a number is measured from.
    """
    key = (root, override)
    now = monotonic()
    cached = _compare_ref_cache.get(key)
    if cached is not None and cached[0] > now:
        _compare_ref_cache.move_to_end(key)
        return cached[1]
    resolved = await resolve_comparison_ref(cwd, override)
    ref = resolved["ref"]
    _compare_ref_cache[key] = (now + COMPARE_REF_TTL_SECONDS, ref)
    _compare_ref_cache.move_to_end(key)
    _trim(_compare_ref_cache)
    if ref is None:
        log.debug(
            "git comparison ref unavailable",
            extra={"root": root, "source": resolved["source"], "diagnostic": resolved["reason"]},
        )
    return ref


def _forget_comparison_ref(root: str, override: str | None) -> None:
    """Drop a cached ref that stopped resolving, so the next poll re-infers it."""
    _compare_ref_cache.pop((root, override), None)


async def _read_comparison(
    cwd: str,
    root: str,
    ref: str | None,
    ref_oid: str | None,
    head: str | None,
    dirty_hash: str | None,
    dirty: int,
) -> tuple[int | None, int | None, int | None]:
    """Working tree versus its merge base with `ref`, memoized on what moves it.

    The merge base, not the ref itself: diffing a branch straight against a base
    that has advanced reports the *inbound* commits as this branch's deletions,
    which reads as work destroyed rather than work not yet merged.

    Every failure answers `None` rather than 0. A zero here would claim a branch
    identical to its base, which is the one thing a reader would act on. And every
    failure is memoized, for the reason `_read_diffstat` gives: a branch that
    diverged too far to count in time has not converged by the next read. A merge
    base that could not be found is keyed on the two commits it was asked about,
    since nothing else can change the answer.
    """
    if ref is None or ref_oid is None or head is None:
        return None, None, None
    key = (root, ref)
    commits = (ref_oid, head)
    fingerprint = (ref_oid, head, dirty_hash)
    cached = _compare_memo.get(key)
    if cached is not None and cached[0] == fingerprint:
        _compare_memo.move_to_end(key)
        return cached[1], cached[2], cached[3]
    if _failure_standing(_merge_base_failures, key, commits, dirty) or _failure_standing(
        _compare_failures, key, fingerprint, dirty
    ):
        return None, None, None
    base_code, base = await _git(cwd, "merge-base", ref_oid, head)
    base = base.strip()
    if base_code or not base:
        _record_failure(_merge_base_failures, key, commits, None)
        log.info(
            "git_merge_base_unavailable root=%s ref=%s code=%d retry_s=%.0f diagnostic=%s",
            root,
            ref,
            base_code,
            DIFFSTAT_FAILURE_RETRY_SECONDS,
            base[:200],
        )
        return None, None, None
    code, output = await _git(cwd, "diff", "--numstat", base)
    if code:
        shrink_to = _shrink_floor(code, dirty)
        _record_failure(_compare_failures, key, fingerprint, shrink_to)
        log.info(
            "git_comparison_diffstat_unavailable root=%s ref=%s code=%d dirty=%d retry_s=%.0f "
            "shrink_to=%s diagnostic=%s",
            root,
            ref,
            code,
            dirty,
            DIFFSTAT_FAILURE_RETRY_SECONDS,
            shrink_to,
            output[:200],
        )
        return None, None, None
    added, removed, files = parse_numstat_summary(output)
    _compare_memo[key] = (fingerprint, added, removed, files)
    _compare_memo.move_to_end(key)
    _trim(_compare_memo)
    return added, removed, files


async def _unavailable() -> tuple[int, str]:
    """Stand-in for a git call that is not worth making. Keeps the gather uniform."""
    return 1, ""


async def read_git_reading(cwd: str, compare_override: str | None = None) -> GitReading:
    code, root = await _git(cwd, "rev-parse", "--show-toplevel")
    if code or not root:
        return GitReading(GitState(), GitEvidence())
    # Resolved before the gather so the ref's own oid can be read inside it: on
    # the cached path that costs no subprocess and adds no latency to the poll.
    compare_ref = await _comparison_ref(cwd, root, compare_override)
    (
        (_, branch),
        (_, porcelain),
        (upstream_code, counts),
        (head_code, head),
        (dir_code, dirs),
        (compare_code, compare_oid),
    ) = await asyncio.gather(
        _git(cwd, "branch", "--show-current"),
        _git(cwd, "status", "--porcelain"),
        _git(cwd, "rev-list", "--left-right", "--count", "HEAD...@{upstream}"),
        _git(cwd, "rev-parse", "HEAD"),
        _git(cwd, "rev-parse", "--absolute-git-dir", "--git-common-dir"),
        _git(cwd, "rev-parse", "--verify", f"{compare_ref}^{{commit}}")
        if compare_ref
        else _unavailable(),
    )
    if not branch:
        _, branch = await _git(cwd, "rev-parse", "--short", "HEAD")
    ahead = behind = 0
    if not upstream_code and counts:
        parts = counts.replace("\t", " ").split()
        if len(parts) == 2:
            ahead, behind = int(parts[0]), int(parts[1])
    # An unborn branch (no commits yet) reports a non-zero code and no oid.
    commit = head.strip() if not head_code and head.strip() else None
    dir_lines = dirs.splitlines() if not dir_code else []
    worktree, layout = (
        await _checkout_layout(cwd, root, dir_lines[0], dir_lines[1])
        if len(dir_lines) >= 2
        else (None, None)
    )
    dirty_hash = _dirty_hash(porcelain)
    dirty = len(porcelain.splitlines())
    added, removed = await _read_diffstat(cwd, root, dirty_hash, dirty, commit is not None)
    resolved_oid = compare_oid.strip() if compare_ref and not compare_code else ""
    if compare_ref and not resolved_oid:
        # The ref was cached from an earlier poll and has since been deleted or
        # rewritten. Re-infer next tick rather than reporting nothing until TTL.
        _forget_comparison_ref(root, compare_override)
    compare_added, compare_removed, compare_files = await _read_comparison(
        cwd, root, compare_ref, resolved_oid or None, commit, dirty_hash, dirty
    )
    return GitReading(
        GitState(
            branch,
            dirty,
            ahead,
            behind,
            worktree=worktree,
            added=added,
            removed=removed,
            root=root,
            compare_ref=compare_ref,
            compare_added=compare_added,
            compare_removed=compare_removed,
            compare_files=compare_files,
            head=commit,
        ),
        GitEvidence(head=commit, dirty_hash=dirty_hash),
        layout,
    )


async def read_git_state(cwd: str, compare_override: str | None = None) -> GitState:
    return (await read_git_reading(cwd, compare_override)).state


async def _read_unique[K, T](
    keys: Iterable[K], read_one: Callable[[K], Awaitable[T]]
) -> dict[K, T]:
    """Poll unique targets concurrently while keeping subprocess pressure bounded."""
    semaphore = asyncio.Semaphore(GIT_CONCURRENCY)

    async def read(key: K) -> tuple[K, T]:
        async with semaphore:
            return key, await read_one(key)

    return dict(await asyncio.gather(*(read(key) for key in dict.fromkeys(keys))))


async def read_unique_git_states(cwds: Iterable[str]) -> dict[str, GitState]:
    return await _read_unique(cwds, lambda cwd: read_git_state(cwd))


#: A checkout as the monitor addresses it: where to run git, and which comparison
#: base that cwd's Project has configured. Both belong to the key because two
#: Projects can legitimately point at one checkout with different bases, and
#: collapsing them onto the cwd alone would serve one Project the other's number.
GitTarget = tuple[str, str | None]


async def read_unique_git_readings(targets: Iterable[GitTarget]) -> dict[GitTarget, GitReading]:
    return await _read_unique(targets, lambda target: read_git_reading(*target))


class GitMonitor:
    """Keeps every session's `GitState` converging on what git actually says.

    **A checkout is read when it changes, not on a timer.** Every followed checkout
    has an OS watch over its working tree and its Git directory (`git_watch.py`), and
    a change marks it due. Attached sessions are re-read at most once per `cadence`
    however fast the changes come; detached ones at most every
    `DETACHED_CHANGE_SECONDS`. Until 2026-10 every attached checkout was re-read every
    five seconds whether or not anything had moved - ~7 `git` processes per checkout
    per tick, measured 2026-10-12 at a third of the loop's life and a 9.9s median
    iteration, almost all of it answering "nothing changed".

    Every session is still swept, far more slowly, and the sweep is not a nicety:
    `GitState` is a *cache of a derived observation* living on a record that outlives
    the daemon that wrote it, so a session whose pane is closed used to freeze
    whatever the last poll computed, for as long as the session lived. A value derived
    by code that has since been fixed then survives the fix - which is exactly how a
    wrong `worktree` name kept rendering after the bug that produced it was gone. The
    sweep is also what bounds a missed OS notification to staleness rather than to a
    wrong answer. The first tick after a daemon start reads everything, so state
    adopted from a previous daemon is re-derived by *this* daemon's code.

    A checkout with no live watch - not a repository, a watch the OS refused, a
    directory on a filesystem that cannot be watched - falls back to the timed
    polling it always had, so losing a watch costs cost, never correctness.

    Reads are deduplicated by checkout target (`read_unique_git_readings`): a fleet of
    thirty sessions in one checkout is one git read, not thirty. That deduplication is
    also why every session sharing a checkout reports the same numbers, and it is not
    an artefact worth removing: `git status` answers for the whole repository however
    it is invoked, so there is no per-session measurement to take. Clients
    disambiguate with `GitState.root`.
    """

    #: How often the loop decides what is due. Deciding reads no files and spawns
    #: nothing, so this is cheap; it bounds how long a noticed change waits.
    TICK_SECONDS = 1.0
    #: Safety sweep for a watched checkout with an attached pane.
    ATTACHED_SWEEP_SECONDS = 60.0
    #: Safety sweep for a watched checkout nobody has open.
    DETACHED_SWEEP_SECONDS = 300.0
    #: Fastest a detached checkout that keeps changing is re-read.
    DETACHED_CHANGE_SECONDS = 30.0
    #: Without a live watch, a detached checkout keeps the pre-watch sweep.
    UNWATCHED_DETACHED_SECONDS = 60.0
    #: A directory Git does not recognise is asked again this often while attached,
    #: so a `git init` in a shell pane appears without waiting for the sweep.
    NON_REPOSITORY_SECONDS = 30.0
    #: A read batch slower than this is logged at WARNING with what it covered.
    SLOW_READ_SECONDS = 5.0

    def __init__(
        self,
        sessions: SessionManager,
        events: EventBus,
        cadence: float = 5.0,
        compare_override: Callable[[str], str | None] | None = None,
        *,
        clock: Callable[[], float] = monotonic,
        watch: bool = True,
    ) -> None:
        self.sessions = sessions
        self.events = events
        self.cadence = cadence
        #: project id -> configured comparison ref, or None for automatic
        #: inference. Injected rather than imported so the monitor keeps knowing
        #: nothing about the Project registry; absent, every checkout infers.
        self.compare_override = compare_override
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._targets: dict[GitTarget, _TargetState] = {}
        self._wake = asyncio.Event()
        self._watches = (
            CheckoutWatches(self._on_checkout_changed, _read_ignored_listing) if watch else None
        )
        self._reads: dict[str, int] = {}
        self._changes = 0
        self._slowest_read_seconds = 0.0
        self._last_read_seconds: float | None = None

    def start(self) -> None:
        log.info(
            "git monitor started: change-driven, attached reads at most every %.1fs, "
            "sweeps every %.0fs attached / %.0fs detached",
            self.cadence,
            self.ATTACHED_SWEEP_SECONDS,
            self.DETACHED_SWEEP_SECONDS,
        )
        self._task = background.start(GIT_MONITOR_LOOP, self._run)

    async def stop(self) -> None:
        await background.stop(GIT_MONITOR_LOOP)
        self._task = None
        if self._watches is not None:
            await self._watches.stop()

    async def _run(self) -> None:
        while True:
            self._wake.clear()
            # One correlation id per tick, minted here because no request causes
            # this work. Without it a `bounded_command_timed_out` from a Git query
            # this loop started carried no identifier at all — not a request id,
            # because there is no request, and not an operation id, because none
            # was passed — leaving nothing to join it to the poll it came from.
            # Bound rather than threaded: the contextvar reaches every `_git` call
            # this iteration makes, including the ones inside its per-session
            # tasks, and stamps the loop's own log lines with the same id.
            with background.iteration(GIT_MONITOR_LOOP), bound_request_id(new_request_id()):
                await self._poll()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.TICK_SECONDS)
            except TimeoutError:
                pass

    def status(self) -> dict[str, Any]:
        """Counters for `/api/diagnostics/background`.

        `reads` is keyed by why each read happened. A healthy idle fleet reads
        almost nothing but `sweep`; `poll` climbing means checkouts have fallen back
        to timed polling, which `watch.unwatched_roots` and `watch.last_failure`
        explain.
        """
        return {
            "cadence_seconds": self.cadence,
            "targets": len(self._targets),
            "dirty_targets": sum(1 for state in self._targets.values() if state.dirty),
            "reads": dict(self._reads),
            "changes_received": self._changes,
            "last_read_seconds": self._last_read_seconds,
            "slowest_read_seconds": round(self._slowest_read_seconds, 3),
            "watch": self._watches.status() if self._watches is not None else None,
        }

    def _on_checkout_changed(self, root_key: str, rules_changed: bool) -> None:
        del rules_changed  # The watch refreshes its own ignore rules.
        self._changes += 1
        touched = False
        for state in self._targets.values():
            if state.root_key == root_key:
                state.dirty = True
                touched = True
        if touched:
            self._wake.set()

    def _live_targets(self) -> dict[GitTarget, tuple[list[Session], bool]]:
        """Every target a live local session reads, with whether any pane shows it."""
        groups: dict[GitTarget, tuple[list[Session], bool]] = {}
        for session in self.sessions.sessions.values():
            # An ended session's reading is frozen at its death, deliberately.
            # Every field here describes the *checkout*, so following it onward
            # would attribute whatever somebody changed afterwards to a session
            # that could not have made it — and a retained ended pane keeps its
            # subscribers, so without this a dead row would poll Git forever.
            if getattr(session.record, "state", None) in {"exited", "crashed"}:
                continue
            if getattr(session.record, "runtime_boundary", "local") != "local":
                continue
            target = (session.record.git_cwd, self._override_for(session))
            members, attached = groups.get(target, ([], False))
            members.append(session)
            groups[target] = (members, attached or session.attended)
        return groups

    def _due_cause(self, state: _TargetState, attached: bool, now: float) -> str | None:
        """Why `state` should be read now, or None. The rate limits live here."""
        return self._due(state, attached, now)[0]

    def _due(self, state: _TargetState, attached: bool, now: float) -> tuple[str | None, float]:
        """`_due_cause`, with the interval that made the read due."""
        if state.last_read is None:
            return "initial", 0.0
        age = now - state.last_read
        if state.dirty:
            limit = self.cadence if attached else self.DETACHED_CHANGE_SECONDS
            return ("change" if age >= limit else None), limit
        if not state.repository:
            limit = self.NON_REPOSITORY_SECONDS if attached else self.UNWATCHED_DETACHED_SECONDS
            return ("sweep" if age >= limit else None), limit
        if (
            self._watches is not None
            and state.root_key is not None
            and self._watches.watched(state.root_key)
        ):
            limit = self.ATTACHED_SWEEP_SECONDS if attached else self.DETACHED_SWEEP_SECONDS
            return ("sweep" if age >= limit else None), limit
        limit = self.cadence if attached else self.UNWATCHED_DETACHED_SECONDS
        return ("poll" if age >= limit else None), limit

    def _override_for(self, session: Session) -> str | None:
        if self.compare_override is None:
            return None
        try:
            return self.compare_override(session.record.project_id)
        except Exception:
            log.exception("comparison ref override lookup failed")
            return None

    async def _poll(self, now: float | None = None) -> None:
        now = self._clock() if now is None else now
        live = self._live_targets()
        for target in tuple(self._targets):
            if target not in live:
                del self._targets[target]
        due: dict[GitTarget, str] = {}
        for target, (_members, attached) in live.items():
            state = self._targets.setdefault(target, _TargetState())
            cause, limit = self._due(state, attached, now)
            if cause is None:
                continue
            # A sweep is the safety net under a live watch or the slow re-check of a
            # non-repository, so it can wait while the daemon sheds load, bounded.
            # A change the watch reported, and polling where there is no watch, are
            # how Git status stays current, and are never deferred.
            if (
                cause == "sweep"
                and state.last_read is not None
                and background.defer(
                    GIT_SWEEP_DEFERRAL, overdue=now - state.last_read - limit, cadence=limit
                )
            ):
                continue
            due[target] = cause
            # Cleared before the read rather than after: a change that lands while
            # git is running must survive to cause the next read.
            state.dirty = False
            state.last_read = now
        if due:
            await self._read(due, live)
        self._sync_watches(set(due))

    async def _read(
        self,
        due: dict[GitTarget, str],
        live: dict[GitTarget, tuple[list[Session], bool]],
    ) -> None:
        started = monotonic()
        readings = await read_unique_git_readings(due)
        elapsed = monotonic() - started
        self._last_read_seconds = round(elapsed, 3)
        self._slowest_read_seconds = max(self._slowest_read_seconds, elapsed)
        for cause in due.values():
            self._reads[cause] = self._reads.get(cause, 0) + 1
        if elapsed >= self.SLOW_READ_SECONDS:
            log.warning(
                "git_read_slow targets=%d elapsed_s=%.2f causes=%s",
                len(due),
                elapsed,
                ",".join(sorted(set(due.values()))),
            )
        for target in due:
            reading = readings[target]
            target_state = self._targets.get(target)
            if target_state is not None:
                target_state.repository = bool(reading.state.root)
                target_state.layout = reading.layout
                target_state.root_key = reading.layout.key if reading.layout else None
            await self._apply(target[0], reading, live[target][0])

    async def _apply(self, cwd: str, reading: GitReading, sessions: list[Session]) -> None:
        state = reading.state
        for session in sessions:
            if session.record.git == state:
                continue
            previous = session.record.git
            previous_head = (
                previous.head
                if previous.root
                and state.root
                and _normalized_path(previous.root) == _normalized_path(state.root)
                else None
            )
            session.record.git = state
            session.publish_update()
            await self.events.emit(
                "git_changed",
                session_id=session.record.id,
                source="daemon",
                # Which Project's repository moved. The Git tab refetched a whole
                # worktree overview on every one of these regardless of whose
                # repository it was about - at fifty checkouts, several hundred
                # `git` subprocesses for a Project nobody was looking at.
                project_id=session.record.project_id,
                git=asdict(state),
                # The complete new value of every row field this changed, ordered
                # by the revision `publish_update` just stamped. The browser patches
                # the session row from it instead of re-reading the whole fleet.
                **session_patch(session, ("git",)),
                # Tier 0 provenance reads these: which commit, which
                # working-tree change set. The UI ignores them.
                content_hash=reading.evidence.head,
                target=cwd,
                previous_head=previous_head,
                **reading.evidence.as_payload(),
            )

    def _sync_watches(self, just_read: set[GitTarget]) -> None:
        """Point the OS watches at exactly the checkouts live targets resolved to.

        A watch that starts for a checkout this tick did not just read may have
        missed a change in the gap before it, so that checkout is read once more.
        """
        if self._watches is None:
            return
        layouts = {
            state.root_key: state.layout
            for state in self._targets.values()
            if state.root_key is not None and state.layout is not None
        }
        fresh = self._watches.sync(layouts)
        if not fresh:
            return
        fresh_read = {
            self._targets[target].root_key for target in just_read if target in self._targets
        }
        for state in self._targets.values():
            if state.root_key in fresh and state.root_key not in fresh_read:
                state.dirty = True


@dataclass(slots=True)
class _TargetState:
    """What the monitor knows about one target between reads."""

    #: Monotonic time of the last read, None before the first.
    last_read: float | None = None
    #: A watched change arrived since the last read began.
    dirty: bool = False
    #: Whether Git recognised the directory at the last read.
    repository: bool = True
    layout: CheckoutLayout | None = None
    #: `layout.key`: which watched checkout a change notification names.
    root_key: str | None = None


async def _read_ignored_listing(toplevel: str) -> str | None:
    """Git's own list of what is ignored in a checkout, or None when it cannot say.

    `--directory` collapses an ignored directory to one entry, which is what makes
    this cheap enough to ask again whenever a `.gitignore` moves: Git does not walk
    into what it ignores.
    """
    code, output = await _git(
        toplevel,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "--directory",
        "--no-empty-directory",
        timeout_seconds=IGNORED_LISTING_TIMEOUT_SECONDS,
        output_limit=IGNORED_LISTING_LIMIT_BYTES,
    )
    if code:
        log.info(
            "git_ignore_rules_unavailable root=%s code=%d diagnostic=%s",
            toplevel,
            code,
            output[:200],
        )
        return None
    return output
