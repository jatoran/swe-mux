"""The scan timeline and everything read from a session's transcript."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import (
    agent_environment,
    git_review,
    mcp_tools,
)
from .. import (
    app_keys as keys,
)
from ..agent_environment import discover_agent_environment
from ..agent_skills import discover_skills
from ..harness import (
    has_observable_transcript,
    is_agent_harness,
)
from ..http_support import json_response
from ..project_context import ProjectContext
from ..scan_consumers import catch_me_up, live_blocker, search_scan_records
from ..scan_timeline import ScanTimelineService
from ..transcript_view import (
    CONVERSATION_DEFAULT_LIMIT,
    CONVERSATION_MAX_LIMIT,
    conversation_is_readable,
    conversation_view_cached,
    final_reply_text,
)
from .agent_ingress import HOOK_WINDOW_SWEEP_AT
from .support import _project_root_for

log = logging.getLogger(__name__)


# A parse this misses is a blank reading column, never a wrong one, so the
# budget is generous: the largest Codex rollout on record (550 MB) parses in
# about a second, and the byte cap in `conversation_view` bounds the rest.
# Shared with the reply copy below, which reads the same view.
CONVERSATION_PARSE_TIMEOUT_SECONDS = 5.0


async def session_last_reply(request: web.Request) -> web.Response:
    """Return normalized assistant text without routing through terminal OSC 52.

    Reads the same reduction the drawer's Transcript tab renders, so what this
    hands the clipboard is the last agent message a reader can see and check,
    down to the tool boundary it starts at.
    """
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    if not has_observable_transcript(session.record.backend):
        return json_response({"error": "last reply is available only for agent sessions"}, 409)
    path = session.transcript_path
    native_id = session.record.native_session_id
    if not conversation_is_readable(path, session.record.backend, native_id):
        return json_response({"error": "the agent transcript is not available yet"}, 409)
    try:
        text = await asyncio.wait_for(
            asyncio.to_thread(final_reply_text, path, session.record.backend, native_id=native_id),
            timeout=CONVERSATION_PARSE_TIMEOUT_SECONDS,
        )
    except (OSError, TimeoutError) as exc:
        return json_response({"error": str(exc) or "the agent transcript could not be read"}, 409)
    if not text:
        return json_response(
            {"error": "no assistant reply text was found in the recent transcript"}, 409
        )
    return json_response({"text": text, "agent_run_id": session.record.agent_run_id})


async def session_scan_timeline(request: web.Request) -> web.Response:
    service: ScanTimelineService = request.app[keys.SCAN_TIMELINE]
    return json_response(await service.snapshot(request.match_info["sid"]))


async def put_session_scan_timeline(request: web.Request) -> web.Response:
    body = await request.json()
    if not isinstance(body.get("enabled"), bool):
        raise ValueError("enabled must be a boolean")
    service: ScanTimelineService = request.app[keys.SCAN_TIMELINE]
    if body["enabled"]:
        session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
        project = request.app[keys.PROJECTS].projects.get(session.record.project_id or "")
        if project is not None:
            await asyncio.to_thread(
                request.app[keys.PROJECT_CONTEXTS].ensure,
                ProjectContext(project_id=project.id, project_root=project.root),
            )
    await service.set_enabled(request.match_info["sid"], bool(body["enabled"]))
    return json_response(await service.snapshot(request.match_info["sid"]))


# `PUT /api/sessions/{sid}/scan-timeline/project` used to live here: a session-scoped way
# to flip its Project's scan-timeline opt-in, written for a Timeline-tab shortcut that was
# taken out again. It had no caller in the browser and no test, and it was a third writer
# of one file - a read-then-write with no caller-supplied revision, so it could silently
# overwrite an open Project editor. `POST /api/grants` does the enable half properly
# (allowlisted, revision-checked, one audit record) and the Projects registry owns the
# disable half, which is where taking a permission away belongs.


async def scan_session_now(request: web.Request) -> web.Response:
    service: ScanTimelineService = request.app[keys.SCAN_TIMELINE]
    record = await service.scan_now(request.match_info["sid"], "manual")
    return json_response({"record": record})


async def backfill_session_scan_timeline(request: web.Request) -> web.Response:
    service: ScanTimelineService = request.app[keys.SCAN_TIMELINE]
    return json_response(await service.start_backfill(request.match_info["sid"]), 202)


async def cancel_session_scan_timeline_backfill(request: web.Request) -> web.Response:
    """Stop a running full-session scan. Records already written stay readable."""
    service: ScanTimelineService = request.app[keys.SCAN_TIMELINE]
    return json_response(await service.cancel_backfill(request.match_info["sid"]))


async def session_scan_timeline_record(request: web.Request) -> web.Response:
    service: ScanTimelineService = request.app[keys.SCAN_TIMELINE]
    return json_response(
        await service.record_detail(
            request.match_info["sid"],
            request.match_info["record_id"],
            rehydrate=request.query.get("rehydrate") == "1",
        )
    )


def _record_project_root(request: web.Request, record: Any) -> str:
    """The checkout root for a live session record."""
    root = getattr(record, "project_root", None) or getattr(record, "spawn_project_root", None)
    if root:
        return str(root)
    return _project_root_for(request.app, str(record.project_id or ""), getattr(record, "cwd", ""))


_CHANGE_MAP_EXCLUDES = (
    "Concurrent other-session edits are excluded by construction: the red seeds are "
    "this session's own file writes, filtered by session/run."
)


_CHANGE_MAP_LOWER_BOUND = (
    "Static reverse-callers are a lower bound; dynamic dispatch (getattr, dict "
    "dispatch, decorators, dependency injection, dynamic import) is not shown."
)


#: The three honest answers to "what changed", in the order a selector offers them.
_CHANGE_MAP_SCOPES = ("session", "branch", "project")


#: Provenance rows read per map. One row is one commit, so this covers a long-lived
#: session's whole history of landed work without an unbounded read.
_CHANGE_MAP_PROVENANCE_LIMIT = 300


def _project_compare_ref(app: web.Application, project_id: str) -> str | None:
    """The Project's comparison-base override, or None to let git_review infer one.

    The same override the Git drawer and the sidebar measure against, so a branch
    delta on the change map cannot disagree with the numbers beside it.
    """
    projects = app.get(keys.PROJECTS)
    if not project_id or projects is None:
        return None
    project = projects.projects.get(project_id)
    value = getattr(project, "git_compare_ref", None) if project else None
    return str(value) if value else None


def _change_map_scope(
    query: Mapping[str, str], *, worktree_name: str | None, comparable: bool
) -> str:
    """Which scope this request gets, honouring the caller and then the checkout.

    An explicit ``scope`` wins, then the legacy ``unify=true`` alias, and only then
    the default — which is ``branch`` in a worktree, because a worktree exists to
    hold a branch and the session's own facts are the *narrower* answer there.
    A ``branch`` request against a checkout with no comparison base falls back
    rather than returning an empty map that blames the session for it.
    """
    requested = str(query.get("scope") or "").strip().lower()
    if requested not in _CHANGE_MAP_SCOPES:
        requested = "project" if str(query.get("unify") or "") in ("1", "true", "yes") else ""
    if not requested:
        requested = "branch" if (worktree_name and comparable) else "session"
    if requested == "branch" and not comparable:
        return "session"
    return requested


def _same_root(left: str, right: str) -> bool:
    """Whether two checkout roots name the same directory, spelling aside."""

    def shape(value: str) -> str:
        return str(Path(value)).replace("\\", "/").rstrip("/").casefold()

    return bool(left) and bool(right) and shape(left) == shape(right)


#: How long a checkout's membership in a Project's repository is trusted. Worktrees
#: are added and removed by hand, never between two turns, so re-running `git
#: worktree list` per change-map fetch would buy nothing.
_WORKTREE_MEMBERSHIP_TTL_SECONDS = 60.0


_worktree_membership: dict[tuple[str, str], tuple[float, str | None]] = {}


async def _validated_worktree(project_root: str, checkout: str) -> str | None:
    """`checkout` as git spells it, if it is a worktree of this Project's repository.

    The authoritative test, not a path-shape guess: a Codex worktree can live
    anywhere, and a directory that merely sits under the Project is not a worktree
    at all. None means "do not treat this as the same repository".
    """
    key = (project_root.casefold(), checkout.casefold())
    now = time.monotonic()
    cached = _worktree_membership.get(key)
    if cached is not None and cached[0] > now:
        return cached[1]
    resolved: str | None
    try:
        repository, _common = await git_review.repository_identity(project_root)
        resolved = await git_review.validate_worktree_root(repository, checkout)
    except (git_review.GitReviewError, OSError, ValueError):
        resolved = None
    if len(_worktree_membership) > 256:
        for stale in [k for k, (expiry, _) in _worktree_membership.items() if expiry <= now]:
            _worktree_membership.pop(stale, None)
    _worktree_membership[key] = (now + _WORKTREE_MEMBERSHIP_TTL_SECONDS, resolved)
    return resolved


async def _change_map_checkout(record: Any, project_root: str) -> tuple[str, str | None]:
    """The checkout a session's writes are relative to, and its worktree name.

    `project_root` is where the *Project* was registered; it is not where the
    agent is working. A session in a linked worktree writes files whose only
    correct path identity is the repository-relative one, and normalizing those
    against the Project root instead yields `.claude/worktrees/<name>/…`, which
    the graph refuses as a hidden directory — so the whole session's work reads as
    unmappable. The git monitor already resolves the live working tree
    (`rev-parse --show-toplevel`) and already knows whether it is a linked
    worktree, so this only has to ask.

    Two roots that differ are *not* automatically the same repository. A nested
    repository inside a Project (a vendored checkout, a sub-project) reports its
    own root with no worktree name, and re-anchoring its paths onto this Project's
    identities would join two unrelated trees. Only a validated linked worktree
    re-anchors.
    """
    git = getattr(record, "git", None)
    checkout = str(getattr(git, "root", "") or "")
    worktree_name = getattr(git, "worktree", None)
    if not checkout or not project_root or _same_root(checkout, project_root):
        return project_root, None
    if not worktree_name:
        return project_root, None
    validated = await _validated_worktree(project_root, checkout)
    if validated is None:
        return project_root, None
    return validated, str(worktree_name)


class _SeedAdmission:
    """Path admission for change-map seeds, with honest exclusion counts.

    Every seed source funnels through here — this run's write facts, the session's
    landed commits, and a branch's change set — so one rule decides what the map
    can draw, and one count reports what it refused.

    The graph only ever indexes files under the Project root and refuses
    generated, vendored, and hidden directories outright (`is_indexable_path`), so
    a path failing either test can never acquire an edge, never show a blast
    radius, and never be opened from the pane. Counts are of distinct files, not
    of facts: a scratchpad script rewritten twenty times is one omission to
    report, not twenty.
    """

    def __init__(self) -> None:
        self.seeds: dict[str, set[str]] = {}
        self.outside_root: set[str] = set()
        self.unindexable: set[str] = set()

    def admit(self, identity: str | None, owner: str | None) -> bool:
        from .. import code_graph as cg

        if identity is None or cg.spec_for_path(identity) is None:
            return False
        if not cg.is_project_relative(identity):
            self.outside_root.add(identity)
            return False
        if not cg.is_indexable_path(identity):
            self.unindexable.add(identity)
            return False
        owners = self.seeds.setdefault(identity, set())
        if owner:
            owners.add(owner)
        return True

    @property
    def excluded(self) -> dict[str, int]:
        return {"outside_root": len(self.outside_root), "unindexable": len(self.unindexable)}


def _seeds_from_facts(
    admission: _SeedAdmission,
    facts: Iterable[dict[str, Any]],
    roots: Sequence[str],
    default_session_id: str,
) -> None:
    """Tier 0 write facts, re-anchored against whichever checkout contains them.

    `roots` is every checkout the facts may be recorded against: the requesting
    session's own, plus (in project scope) the checkout of every other session
    that contributed a write. Re-anchoring against all of them is what keeps a
    sibling worktree's session on the map — its writes are absolute paths under
    *its* checkout, which this session's root cannot strip, so without this they
    would all read as outside-root.

    **Deepest root first, and the best candidate wins.** A worktree usually lives
    *inside* the Project root (`.claude/worktrees/<name>`), so stripping the
    Project root off a worktree write does produce a relative path — the useless
    one, `.claude/worktrees/<name>/src/…`, which the hidden-directory rule then
    refuses. Taking the first merely-relative answer is how a whole worktree
    session's work reads as unmappable even with its own root in the list.
    """
    from .. import code_graph as cg
    from ..deterministic_consumers import normalize_target

    def rank(candidate: str) -> int:
        if not cg.is_project_relative(candidate):
            return 0
        return 2 if cg.is_indexable_path(candidate) else 1

    ordered = sorted(roots, key=len, reverse=True)
    for fact in facts:
        if fact.get("kind") not in ("file_write", "file_write_result"):
            continue
        target = fact.get("target")
        identity: str | None = None
        best = -1
        for root in ordered:
            candidate = normalize_target(target, root)
            if candidate is None:
                continue
            score = rank(candidate)
            if score > best:
                identity, best = candidate, score
            if score == 2:
                break
        admission.admit(identity, str(fact.get("session_id") or default_session_id))


def _seeds_from_provenance(admission: _SeedAdmission, rows: Iterable[dict[str, Any]]) -> None:
    """Files this session has actually landed, from the git provenance ledger.

    Tier 0 facts expire twice over — a six-hour window and a conversation
    rollover — so a session whose work merged hours ago reads as having edited
    nothing at all. Provenance rows do not expire: they name repository-relative
    paths per commit per session, and merging the branch does not disturb them.
    """
    from ..deterministic_consumers import normalize_target

    for row in rows:
        owner = str(row.get("session_id") or "")
        paths = row.get("contributed_paths")
        if not isinstance(paths, list):
            continue
        for path in paths:
            if isinstance(path, str) and path:
                admission.admit(normalize_target(path, None), owner)


def _seeds_from_branch(admission: _SeedAdmission, paths: Iterable[str]) -> None:
    """A checkout's whole change set against its comparison base.

    Deliberately unattributed. A branch delta describes the *checkout*, and two
    sessions sharing one worktree cannot be told apart by anything git can answer
    — claiming a per-session hue for it would be an invention.
    """
    from ..deterministic_consumers import normalize_target

    for path in paths:
        if path:
            admission.admit(normalize_target(path, None), None)


async def session_change_map(request: web.Request) -> web.Response:
    """The per-session code change map (Phase 7.9, Surface 3).

    Red = edited source files (seeds), yellow = their blast radius (reverse
    dependents), blue = immediate imports (context). Server-side and bounded: only
    the changed nodes plus blast radius plus one hop ship, never the whole codebase
    graph.

    Three scopes, because "what changed" has three honest answers and they expire
    at different rates:

    * ``session`` — this session's own work: this run's Tier 0 write facts, plus
      every path it has landed according to the git provenance ledger. The facts
      are precise and short-lived; the ledger is durable and survives the merge.
    * ``branch`` — everything the session's checkout has changed against its
      comparison base. Checkout-scoped, so it carries no per-session attribution,
      and immune to both fact expiries. The default in a worktree, because a
      worktree exists to hold a branch.
    * ``project`` — every session's edits, one hue each. The former ``unify=true``,
      which is still accepted as an alias.
    """
    from .. import code_graph as cg
    from ..deterministic_consumers import RUN_FACT_WINDOW_SECONDS

    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    record = session.record
    run_id = str(record.agent_run_id or "")
    pid = str(record.project_id or "")
    try:
        hops = int(request.query.get("hops", "1"))
    except ValueError:
        hops = 1
    hops = max(1, min(hops, cg.MAX_BLAST_HOPS))
    baseline_head = getattr(getattr(record, "git", None), "head", None)

    # Where the Project was registered, and where this session is *actually*
    # working. They differ for every worktree session, and normalizing writes
    # against the first is what made a worktree's whole map read as unmappable.
    project_root = _project_root_for(request.app, pid, "") or _record_project_root(request, record)
    root, worktree_name = await _change_map_checkout(record, project_root)
    worktree = root if root and project_root and not _same_root(root, project_root) else ""
    base: dict[str, Any] = {
        "session_id": record.id,
        "project_id": pid or None,
        "baseline_head": baseline_head,
        "nodes": [],
        "edges": [],
        "sessions": [],
        "worktree": worktree or None,
        "scope": "session",
        "scopes": list(_CHANGE_MAP_SCOPES),
        "checkout": None,
        "excluded": {"outside_root": 0, "unindexable": 0},
        "excludes_note": _CHANGE_MAP_EXCLUDES,
        "lower_bound_note": _CHANGE_MAP_LOWER_BOUND,
    }

    store = request.app.get(keys.CODE_GRAPH)
    tier0 = request.app.get(keys.TIER0)
    if store is None or tier0 is None:
        return json_response({**base, "available": False, "disabled_reason": "unsupported"})
    if not pid or not root:
        return json_response({**base, "available": False, "disabled_reason": "no_project"})
    # The opt-in is the *Project's*, so it is asked of the Project root even when
    # the session is working in one of its worktrees.
    enabled = await request.app[keys.AUTOMATION_GATE](project_root)
    if "code_graph" not in enabled:
        return json_response({**base, "available": False, "disabled_reason": "automation_disabled"})

    # Offerability is decided from the comparison ref the git monitor already
    # resolved and cached on the record — free — and the branch diff itself only
    # runs when the branch scope is actually the one being served. A detached
    # checkout with no base has no branch to describe.
    git_state = getattr(record, "git", None)
    comparable = bool(getattr(git_state, "compare_ref", None))
    scope = _change_map_scope(request.query, worktree_name=worktree_name, comparable=comparable)
    branch = (
        await git_review.branch_changed_paths(root, _project_compare_ref(request.app, pid))
        if scope == "branch"
        else None
    )
    if scope == "branch" and branch is None:
        # The ref resolved a moment ago and the diff did not: say so rather than
        # drawing an empty branch and letting it read as "nothing changed".
        scope = "session"
        base["scope_fallback"] = "no_comparison_base"
    unify = scope == "project"
    base["scope"] = scope
    base["scopes"] = ["session", *(["branch"] if comparable else []), "project"]
    if worktree_name or branch is not None:
        base["checkout"] = {
            "root": root,
            "worktree": worktree_name,
            "branch": getattr(git_state, "branch", None),
            "ref": branch["ref"] if branch else getattr(git_state, "compare_ref", None),
            "base": branch["base"] if branch else None,
            "truncated": bool(branch["truncated"]) if branch else False,
        }

    admission = _SeedAdmission()
    manager = request.app[keys.SESSIONS]
    if scope == "branch" and branch is not None:
        _seeds_from_branch(admission, branch["paths"])
    else:
        since = time.time() - RUN_FACT_WINDOW_SECONDS
        if unify:
            facts = await tier0.facts_for_project(pid, since=since)
        elif run_id:
            facts = await tier0.facts_for_run(run_id, since=since)
        else:
            facts = []

        # The checkout roots the facts may be recorded against. One run's facts
        # share a cwd, so the session view needs only this session's checkout; the
        # project view spans every session, and a sibling worktree's writes are
        # absolute paths this root cannot strip.
        roots = [root]
        if unify:
            for fact in facts:
                if fact.get("kind") not in ("file_write", "file_write_result"):
                    continue
                owner_id = str(fact.get("session_id") or "")
                other = manager.sessions.get(owner_id) if owner_id else None
                if other is None:
                    continue
                other_root, _name = await _change_map_checkout(other.record, project_root)
                if other_root and not any(_same_root(other_root, known) for known in roots):
                    roots.append(other_root)

        _seeds_from_facts(admission, facts, roots, record.id)
        # Landed work, which the fact window and the run rollover both drop. Without
        # it a session reads as having edited nothing the moment its branch merges.
        history = request.app.get(keys.HISTORY)
        if history is not None:
            _seeds_from_provenance(
                admission,
                await history.git_provenance(
                    project_id=pid,
                    session_id=None if unify else record.id,
                    limit=_CHANGE_MAP_PROVENANCE_LIMIT,
                ),
            )

    seed_sessions = admission.seeds
    excluded = admission.excluded
    seeds = sorted(seed_sessions)
    if not seeds:
        # "Nothing written" and "everything written was unmappable" are different
        # readings, and the second one is the honest answer for a session that only
        # touched scratch files.
        empty_reason = "excluded" if any(excluded.values()) else "no_edits"
        return json_response(
            {
                **base,
                "available": True,
                "disabled_reason": None,
                "empty_reason": empty_reason,
                "excluded": excluded,
            }
        )

    subgraph = await store.subgraph(pid, seeds, hops=hops)

    # Session legend + per-seed session attribution (unify mode colours by session).
    session_ids: list[str] = []
    for owners in seed_sessions.values():
        for owner in owners:
            if owner not in session_ids:
                session_ids.append(owner)
    session_ids.sort()
    hue_by_session = {
        sid: f"hsl({(index * 360) // max(1, len(session_ids)) % 360}, 70%, 55%)"
        for index, sid in enumerate(session_ids)
    }
    sessions_legend = []
    for sid in session_ids:
        other = manager.sessions.get(sid)
        name = str(getattr(other.record, "name", sid)) if other is not None else sid
        sessions_legend.append({"id": sid, "name": name, "hue": hue_by_session[sid]})

    nodes = []
    for node in subgraph.get("nodes", []):
        path = node.get("path")
        entry = dict(node)
        if node.get("role") == "seed":
            seed_owners = sorted(seed_sessions.get(path, set()))
            entry["sessions"] = seed_owners
            if unify and seed_owners:
                entry["hue"] = hue_by_session.get(seed_owners[0])
        nodes.append(entry)

    # Graph identities are casefolded, which makes them useless as filesystem paths.
    # Recover the real casing once per map so a node can be opened in a pane at all
    # (a case-sensitive host) and under the same pane identity the Files browser
    # uses (a case-insensitive one). A node with no `display_path` no longer exists
    # on disk and offers no button rather than a dead link.
    display_paths = await asyncio.to_thread(
        cg.resolve_display_paths, root, [str(entry.get("path") or "") for entry in nodes]
    )
    for entry in nodes:
        shown = display_paths.get(str(entry.get("path") or ""))
        if shown:
            entry["display_path"] = shown

    return json_response(
        {
            **base,
            "available": True,
            "disabled_reason": None,
            "nodes": nodes,
            "edges": subgraph.get("edges", []),
            "sessions": sessions_legend if unify else [],
            "excluded": excluded,
            # When the blast radius overflowed the node cap, say so and by how much,
            # so a truncated view never reads as the whole reach.
            "truncated": bool(subgraph.get("truncated")),
            "totals": subgraph.get("totals"),
        }
    )


async def session_catch_me_up(request: web.Request) -> web.Response:
    """An on-demand rollup of one run's scan spine: phases, claims, current blocker.

    Gated on the Project opting into `catch_me_up`; returns `enabled: false` (never a
    fake empty digest) when it is off. Attributed to the run it came from.
    """
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    record = session.record
    run_id = str(record.agent_run_id or "")
    root = _record_project_root(request, record)
    enabled = await request.app[keys.AUTOMATION_GATE](root) if root else frozenset()
    if "catch_me_up" not in enabled or not run_id:
        return json_response(
            {"enabled": False, "agent_run_id": run_id or None, "digest": None}
        )
    records = await request.app[keys.AUTOMATION_STORE].scan_records(
        agent_run_id=run_id, limit=2000
    )
    return json_response({"enabled": True, "digest": catch_me_up(records, run_id)})


async def fleet_live_blockers(request: web.Request) -> web.Response:
    """A fleet glance of sessions currently waiting on something, without opening any.

    Aggregates the scan spine's `blocked_on` across active sessions whose Project
    opted into `live_blockers`. A session whose latest record is not blocked
    contributes nothing.
    """
    store = request.app[keys.AUTOMATION_STORE]
    gate = request.app[keys.AUTOMATION_GATE]
    blockers: list[dict[str, Any]] = []
    gate_cache: dict[str, frozenset[str]] = {}
    for session in request.app[keys.SESSIONS].sessions.values():
        record = session.record
        if record.state in {"exited", "crashed"}:
            continue
        run_id = str(record.agent_run_id or "")
        if not run_id:
            continue
        root = _record_project_root(request, record)
        if not root:
            continue
        if root not in gate_cache:
            gate_cache[root] = await gate(root)
        if "live_blockers" not in gate_cache[root]:
            continue
        records = await store.scan_records(agent_run_id=run_id, limit=500)
        blocker = live_blocker(records, run_id)
        if blocker is not None:
            blocker["session_id"] = record.id
            blocker["name"] = record.name
            blocker["project_id"] = record.project_id
            blockers.append(blocker)
    blockers.sort(key=lambda item: float(item.get("since") or 0.0))
    return json_response({"blockers": blockers, "generated_at": time.time()})


async def scan_timeline_search(request: web.Request) -> web.Response:
    """Semantic history search over distilled scan `summary`/`intent`/`target` records.

    Scoped to one `run_id` or one `project_id` and gated on that Project opting into
    `semantic_history_search`. Resolves against the behavioral spine, not a raw
    transcript grep, and every result names the `agent_run_id` it came from.
    """
    query = request.query.get("q", "").strip()
    run_id = request.query.get("run_id", "").strip()
    project_id = request.query.get("project_id", "").strip()
    store = request.app[keys.AUTOMATION_STORE]
    if run_id:
        records = await store.scan_records(agent_run_id=run_id, limit=2000)
    elif project_id:
        records = await store.scan_records(project_id=project_id, limit=2000)
    else:
        raise ValueError("scan-timeline search requires a run_id or project_id scope")
    scope_project = project_id or (str(records[0].get("project_id") or "") if records else "")
    root = _project_root_for(request.app, scope_project, "") if scope_project else ""
    enabled = await request.app[keys.AUTOMATION_GATE](root) if root else frozenset()
    if "semantic_history_search" not in enabled:
        return json_response({"enabled": False, "query": query, "results": []})
    if not query:
        return json_response({"enabled": True, "query": query, "results": []})
    limit = max(1, min(int(request.query.get("limit", 50) or 50), 200))
    results = search_scan_records(records, query, limit=limit)
    return json_response({"enabled": True, "query": query, "results": results})


async def session_transcript(request: web.Request) -> web.Response:
    """The focused session's readable conversation, for the drawer's reader tab.

    Deliberately NOT `/api/history/{sid}/transcript`: that endpoint reindexes the
    run's searchable messages and loads its annotations on every call, which is
    right for opening a history entry once and wrong for a surface that refreshes
    whenever a turn ends. This one only reads.

    Every "there is nothing to show" case answers 200 with a `reason` rather than
    an error status. A shell pane and an agent that has not written its first
    record yet are ordinary states of a passive view, not failures, and the tab
    renders a sentence for each.
    """
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    record = session.record
    try:
        limit = int(request.query.get("limit") or CONVERSATION_DEFAULT_LIMIT)
    except ValueError:
        raise web.HTTPBadRequest(text="limit must be an integer") from None
    limit = max(1, min(limit, CONVERSATION_MAX_LIMIT))
    empty: dict[str, Any] = {
        "session_id": record.id,
        "agent_run_id": record.agent_run_id,
        "backend": record.backend,
        # The transcript observer can end up following a conversation that is no
        # longer this PTY's. The reader is the one surface where that is plainly
        # visible, so it reports the doubt instead of presenting a sibling's
        # conversation as this session's.
        "observation_stale_since": record.observation_stale_since,
        "messages": [],
        "trailing_tool_calls": [],
        "hidden": 0,
        "abandoned_messages": 0,
        "truncated": False,
        "reason": None,
    }
    if record.runtime_boundary != "local":
        boundary = record.runtime_boundary
        return json_response(
            {
                **empty,
                "reason": "agent_bridge_unavailable",
                "capability": "agent-bridge-unavailable",
                "boundary": boundary,
                "boundary_reason": (
                    "remote_terminal_boundary"
                    if boundary == "remote"
                    else "terminal_boundary_unknown"
                ),
            }
        )
    if not has_observable_transcript(record.backend):
        return json_response({**empty, "reason": "not_agent"})
    path = session.transcript_path
    # `conversation_is_readable` rather than a file test: a store-backed harness has
    # no path, and testing one answered "no transcript" for every opencode session.
    native_id = record.native_session_id
    if not conversation_is_readable(path, record.backend, native_id):
        return json_response({**empty, "reason": "no_transcript"})
    try:
        view = await asyncio.wait_for(
            asyncio.to_thread(
                conversation_view_cached,
                path,
                record.backend,
                limit=limit,
                native_id=native_id,
            ),
            timeout=CONVERSATION_PARSE_TIMEOUT_SECONDS,
        )
    except (OSError, TimeoutError):
        return json_response({**empty, "reason": "unreadable"})
    return json_response({**empty, **view})


async def session_skills(request: web.Request) -> web.Response:
    """The skills this session's CLI can see, read from the directories it reads.

    Scoped to the session because both inputs are: the backend decides which
    roots exist and how a skill is invoked, and the *live* cwd decides which repo
    skills apply — Codex resolves `.codex/skills` and `.agents/skills` from its
    working directory, so a session sitting in a worktree sees a different set
    than one in the primary checkout of the same Project.
    """
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    if session.record.runtime_boundary != "local":
        boundary = session.record.runtime_boundary
        return json_response(
            {
                "error": "skill inventory is unavailable across a non-local terminal boundary",
                "code": "agent_bridge_unavailable",
                "capability": "agent-bridge-unavailable",
                "reason": (
                    "remote_terminal_boundary"
                    if boundary == "remote"
                    else "terminal_boundary_unknown"
                ),
                "boundary": boundary,
                "authority": session.record.remote_authority,
            },
            409,
        )
    backend = session.record.backend
    if not is_agent_harness(backend):
        return json_response({"error": "skills are available only for agent sessions"}, 409)
    record = session.record
    cwd = Path(
        (record.runtime_cwd if record.runtime_cwd_live else None)
        or record.run_cwd
        or record.spawn_cwd
        or record.cwd
    )
    if not cwd.is_dir():
        cwd = Path(record.spawn_cwd or record.cwd)
    payload = await asyncio.to_thread(
        discover_skills,
        backend,
        cwd,
        refresh=request.query.get("refresh") in {"1", "true"},
    )
    # Conversation rollover does not restart the CLI. Root sessions therefore
    # retain their process start; promoted shell sessions retain the promotion
    # timestamp rather than treating every /clear or /new as a skill reload.
    started = _agent_loaded_at(session)
    skills = [
        {**skill, "added_after_start": skill["mtime"] > started} for skill in payload["skills"]
    ]
    return json_response(
        {
            **payload,
            "skills": skills,
            "agent_loaded_at": started,
            "agent_run_started_at": record.agent_run_started_at or record.created_at,
        }
    )


def _agent_loaded_at(session: Any) -> float:
    """Start of the live CLI process generation, not its current conversation."""
    record = session.record
    if record.agent_loaded_at is not None:
        return float(record.agent_loaded_at)
    if record.spawn_backend == record.backend:
        return float(record.created_at)
    return float(
        getattr(session, "agent_promoted_at", None)
        or record.agent_run_started_at
        or record.created_at
    )


def _agent_environment_cwd(record: Any) -> Path:
    """The directory the CLI actually trusts, with a fallback that always exists.

    Shared by the inventory and the tool fetch so a probe is configured from the
    same project the inventory described: the live cwd decides which project
    configuration layer wins, and answering the two questions from two
    directories would let a fetch dial a server the row never mentioned. The
    rule lives on the record because the drift baseline capture (`session.py`)
    has to take a snapshot from that same directory.
    """
    cwd: Path = record.agent_environment_cwd
    return cwd


async def session_agent_environment(request: web.Request) -> web.Response:
    """Return a bounded passive inventory for the focused agent CLI."""
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    record = session.record
    if record.runtime_boundary != "local":
        boundary = record.runtime_boundary
        return json_response(
            {
                "error": "agent environment is unavailable across a non-local terminal boundary",
                "code": "agent_bridge_unavailable",
                "capability": "agent-bridge-unavailable",
                "reason": (
                    "remote_terminal_boundary"
                    if boundary == "remote"
                    else "terminal_boundary_unknown"
                ),
                "boundary": boundary,
                "authority": record.remote_authority,
            },
            409,
        )
    if not is_agent_harness(record.backend):
        return json_response(
            {"error": "agent environment is available only for agent sessions"}, 409
        )
    cwd = _agent_environment_cwd(record)
    refresh = request.query.get("refresh") in {"1", "true"}
    payload = await asyncio.to_thread(
        discover_agent_environment,
        backend=record.backend,
        cwd=cwd,
        executable=record.exe,
        args=list(record.args),
        model=record.model,
        loaded_at=_agent_loaded_at(session),
        run_started_at=record.agent_run_started_at,
        baseline=dict(record.agent_env_baseline) or None,
        refresh=refresh,
    )
    if refresh:
        log.info(
            "agent environment refreshed session=%s backend=%s sources=%d sections=%d",
            record.id,
            record.backend,
            len(payload["sources"]),
            len(payload["sections"]),
        )
    return json_response(payload)


#: A tool fetch may start a probe process, so it is rate limited per session on
#: top of the cache. Nothing here is expensive to *serve* - the cost is entirely
#: in what a burst of clicks would spawn.
MCP_TOOLS_RATE_LIMIT = 20


MCP_TOOLS_RATE_WINDOW_SECONDS = 60.0


async def session_mcp_tools(request: web.Request) -> web.Response:
    """Fetch one configured MCP server's published tools, on explicit request.

    This is deliberately not part of the inventory GET. Opening the Agent tab
    must stay passive (`features/agent-environment.md`), and folding a probe into
    the payload every tab-open reads would start servers and open connections for
    a user who only wanted to see a model name.
    """
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    record = session.record
    if record.runtime_boundary != "local":
        boundary = record.runtime_boundary
        return json_response(
            {
                "error": "agent environment is unavailable across a non-local terminal boundary",
                "code": "agent_bridge_unavailable",
                "capability": "agent-bridge-unavailable",
                "reason": (
                    "remote_terminal_boundary"
                    if boundary == "remote"
                    else "terminal_boundary_unknown"
                ),
                "boundary": boundary,
                "authority": record.remote_authority,
            },
            409,
        )
    if not is_agent_harness(record.backend):
        return json_response(
            {"error": "agent environment is available only for agent sessions"}, 409
        )
    body = await request.json() if request.can_read_body else {}
    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    server = str(body.get("server") or "").strip()
    if not server:
        return json_response({"error": "a server name is required"}, 400)
    refresh = bool(body.get("refresh"))

    now = time.monotonic()
    windows: dict[str, deque[float]] = request.app[keys.MCP_TOOLS_WINDOWS]
    if len(windows) > HOOK_WINDOW_SWEEP_AT:
        live = request.app[keys.SESSIONS].sessions
        for stale in [sid for sid in windows if sid not in live]:
            windows.pop(stale, None)
    window = windows.setdefault(record.id, deque())
    while window and now - window[0] >= MCP_TOOLS_RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= MCP_TOOLS_RATE_LIMIT:
        raise web.HTTPTooManyRequests(
            text="too many MCP tool fetches for this session", headers={"Retry-After": "5"}
        )
    window.append(now)

    cwd = _agent_environment_cwd(record)
    args = list(record.args)
    try:
        configs = await asyncio.to_thread(
            agent_environment.resolve_mcp_servers,
            backend=record.backend,
            cwd=cwd,
            args=args,
        )
    except ValueError as exc:
        return json_response({"error": str(exc)}, 409)
    entry = configs.get(server.casefold())
    if entry is None:
        return json_response(
            {"error": "no MCP server with that name is configured for this session"}, 404
        )
    payload = await mcp_tools.fetch_server_tools(
        backend=record.backend,
        server=entry.name,
        entry=entry,
        cwd=cwd,
        executable=record.exe,
        args=args,
        version=await asyncio.to_thread(
            agent_environment.probe_cli_version, record.backend, record.exe
        ),
        mux_mcp_url=f"{request.app[keys.SESSIONS].ingress_url}/mcp",
        live_snapshot=request.app[keys.RUNTIME_INVENTORIES].get(record.id),
        session_id=record.id,
        refresh=refresh,
    )
    return json_response(payload)


async def runtime_inventory_ingress(request: web.Request) -> web.Response:
    """Accept a runtime tool inventory published by a session's injected extension.

    Loopback-only and authenticated with the session's own hook secret, like hook
    ingress - but on its own route, because this is not a lifecycle event and
    must never touch status detection, history, or the prompt queue. The body is
    whitelisted and bounded before anything is retained; an extension runs inside
    the user's agent and its payload is untrusted input like any other.
    """
    if request.content_length is not None and request.content_length > 256 * 1024:
        raise web.HTTPRequestEntityTooLarge(max_size=256 * 1024, actual_size=request.content_length)
    peer = request.transport.get_extra_info("peername") if request.transport else None
    host = peer[0] if peer else ""
    if host not in {"127.0.0.1", "::1"}:
        raise web.HTTPForbidden(text="runtime inventory ingress is loopback-only")
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    if session.record.state in {"exited", "crashed"}:
        raise web.HTTPGone(text="session has ended")
    supplied = request.headers.get("X-Mux-Hook-Secret", "")
    if not secrets.compare_digest(supplied, session.hook_secret):
        raise web.HTTPForbidden(text="invalid hook secret")
    raw = await request.read()
    if len(raw) > 256 * 1024:
        raise web.HTTPRequestEntityTooLarge(max_size=256 * 1024, actual_size=len(raw))
    snapshot = mcp_tools.normalize_live_snapshot(json.loads(raw))
    store: mcp_tools.LiveSnapshotStore = request.app[keys.RUNTIME_INVENTORIES]
    store.put(session.record.id, snapshot)
    store.sweep(set(request.app[keys.SESSIONS].sessions))
    log.info(
        "runtime inventory published session=%s tools=%d reason=%s",
        session.record.id,
        len(snapshot["tools"]),
        snapshot["reason"] or "-",
    )
    return json_response({"ok": True, "tools": len(snapshot["tools"])})


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/sessions/{sid}/last-reply", session_last_reply),
    web.get("/api/sessions/{sid}/transcript", session_transcript),
    web.get("/api/sessions/{sid}/scan-timeline", session_scan_timeline),
    web.put("/api/sessions/{sid}/scan-timeline", put_session_scan_timeline),
    web.post("/api/sessions/{sid}/scan-timeline/scan", scan_session_now),
    web.post(
        "/api/sessions/{sid}/scan-timeline/backfill",
        backfill_session_scan_timeline,
    ),
    web.delete(
        "/api/sessions/{sid}/scan-timeline/backfill",
        cancel_session_scan_timeline_backfill,
    ),
    web.get(
        "/api/sessions/{sid}/scan-timeline/{record_id}",
        session_scan_timeline_record,
    ),
    # Phase 7.9 per-session code change map.
    web.get("/api/sessions/{sid}/change-map", session_change_map),
    # Phase 7.7 near-term scan-timeline consumers.
    web.get("/api/sessions/{sid}/catch-me-up", session_catch_me_up),
    web.get("/api/attention/blockers", fleet_live_blockers),
    web.get("/api/history/scan-search", scan_timeline_search),
    web.get("/api/sessions/{sid}/skills", session_skills),
    web.get("/api/sessions/{sid}/agent-environment", session_agent_environment),
    # POST because it is the one Agent Environment call that reaches a
    # server: it may start a short-lived probe process and open a network
    # connection, which is exactly what a GET promises not to do.
    web.post(
        "/api/sessions/{sid}/agent-environment/mcp-tools", session_mcp_tools
    ),
    web.post("/api/sessions/{sid}/runtime-inventory", runtime_inventory_ingress),
)
