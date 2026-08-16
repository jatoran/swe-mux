"""Pure derivations over the scan-timeline behavioral spine (Phase 7.7).

The scan timeline is the single behavioral-summary substrate. These are cheap,
model-free derivations over the per-record spine (`work_phase`, `intent`,
`claim`, `user_ask`, `blocked_on`, `summary`, `novelty`, `target`) that several
near-term consumers share: the timeline-based handoff, the catch-me-up digest,
and the live-blockers glance. Everything here is a pure function over a list of
scan-record dicts as returned by ``AutomationStore.scan_records`` (row columns
merged with the decoded ``record_json``), so it is directly unit-testable and
never issues its own reads.

Two disciplines are baked in, matching the roadmap: **empty beats
plausible-but-wrong** (a derivation returns nothing rather than a low-confidence
guess), and **every derived result names the ``agent_run_id`` it came from**, so
a sibling run's work is never blended into the present.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# `none` is the schema's "not blocked" value; an empty string is an unfilled
# field. Neither is a live blocker.
_NOT_BLOCKED = {"", "none"}
_UNKNOWN_PHASE = "unknown"


def first_nonempty(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return ""


def target_set(record: dict[str, Any]) -> set[str]:
    raw = record.get("target")
    if not isinstance(raw, list):
        return set()
    return {str(item) for item in raw if str(item).strip()}


def phase_segments(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse consecutive same-`work_phase` records into ordered segments.

    Records must be oldest-first (the order ``scan_records`` returns). Each
    segment carries its phase, its time span, the distinct summaries/intents
    seen in it, the last claim asserted, and any blocker labels observed.
    """
    segments: list[dict[str, Any]] = []
    for record in records:
        phase = str(record.get("work_phase") or _UNKNOWN_PHASE)
        if segments and segments[-1]["work_phase"] == phase:
            segment = segments[-1]
        else:
            segment = {
                "work_phase": phase,
                "t0": float(record.get("t0") or 0.0),
                "t1": float(record.get("t1") or 0.0),
                "summaries": [],
                "claim": "",
                "blockers": [],
            }
            segments.append(segment)
        segment["t1"] = float(record.get("t1") or segment["t1"])
        line = first_nonempty(record, "summary", "intent")
        if line and line not in segment["summaries"]:
            segment["summaries"].append(line)
        claim = str(record.get("claim") or "").strip()
        if claim:
            segment["claim"] = claim
        blocked = str(record.get("blocked_on") or "").strip()
        if blocked and blocked not in _NOT_BLOCKED and blocked not in segment["blockers"]:
            segment["blockers"].append(blocked)
    return segments


def handoff_progress(records: list[dict[str, Any]]) -> list[str]:
    """Phase-structured progress bullets for a run handoff.

    "Was in X, hit blocker Y, next step Z" - one bullet per phase segment, with
    the blocker labels and the run's last-asserted claim folded in. Empty when
    there is no scan spine to structure.
    """
    lines: list[str] = []
    for segment in phase_segments(records):
        summaries = segment["summaries"][:3]
        body = "; ".join(summaries) if summaries else "no distilled summary"
        bullet = f"**{segment['work_phase']}**: {body}"
        if segment["blockers"]:
            bullet += f" (blocked on {', '.join(segment['blockers'])})"
        lines.append(bullet)
    return lines


def catch_me_up(records: list[dict[str, Any]], agent_run_id: str) -> dict[str, Any]:
    """An on-demand rollup of one run's scan spine.

    Names the phases the run went through, the distinct claims it asserted, and
    the blocker it is currently on, always attributed to ``agent_run_id``.
    """
    segments = phase_segments(records)
    phases = [segment["work_phase"] for segment in segments]
    claims: list[str] = []
    for record in records:
        claim = str(record.get("claim") or "").strip()
        if claim and claim not in claims:
            claims.append(claim)
    blocker = live_blocker(records, agent_run_id)
    return {
        "agent_run_id": agent_run_id,
        "record_count": len(records),
        "phases": phases,
        "claims": claims[:12],
        "current_blocker": blocker,
        "progress": handoff_progress(records),
    }


def live_blocker(
    records: list[dict[str, Any]], agent_run_id: str
) -> dict[str, Any] | None:
    """The run's *current* blocker, or None when it is not blocked.

    The blocker is real only if the run's most-recent record carries a
    non-`none` ``blocked_on``. The ``since`` timestamp is the start of the
    unbroken streak on that same blocker label, so a momentary earlier block
    does not backdate a fresh one.
    """
    if not records:
        return None
    latest = records[-1]
    label = str(latest.get("blocked_on") or "").strip()
    if label in _NOT_BLOCKED:
        return None
    since = float(latest.get("t0") or latest.get("t1") or 0.0)
    for record in reversed(records[:-1]):
        if str(record.get("blocked_on") or "").strip() == label:
            since = float(record.get("t0") or record.get("t1") or since)
        else:
            break
    return {
        "agent_run_id": agent_run_id,
        "blocked_on": label,
        "since": since,
        "summary": first_nonempty(latest, "summary", "intent", "user_ask"),
        "work_phase": str(latest.get("work_phase") or _UNKNOWN_PHASE),
    }


def _query_terms(query: str) -> list[str]:
    return [term for term in query.casefold().split() if len(term) >= 2]


def search_match(record: dict[str, Any], query: str) -> dict[str, Any] | None:
    """Match a scan record against a search query over its distilled fields.

    Searches the behavioral spine - ``summary``/``intent``/``user_ask``/
    ``claim`` and the ``target`` paths - rather than raw transcript. Returns a
    result with the matched snippet and the ``agent_run_id`` it belongs to, or
    None when no term hits. All terms must appear (AND) so a two-word query
    narrows rather than widens.
    """
    terms = _query_terms(query)
    if not terms:
        return None
    haystack_fields = [
        first_nonempty(record, "summary"),
        first_nonempty(record, "intent"),
        first_nonempty(record, "user_ask"),
        first_nonempty(record, "claim"),
    ]
    targets = sorted(target_set(record))
    haystack = " ".join([*haystack_fields, *targets]).casefold()
    if not all(term in haystack for term in terms):
        return None
    snippet = first_nonempty(record, "summary", "intent", "user_ask", "claim")
    return {
        "record_id": str(record.get("id") or ""),
        "agent_run_id": str(record.get("agent_run_id") or ""),
        "session_id": str(record.get("session_id") or ""),
        "project_id": str(record.get("project_id") or ""),
        "t0": float(record.get("t0") or 0.0),
        "t1": float(record.get("t1") or 0.0),
        "work_phase": str(record.get("work_phase") or _UNKNOWN_PHASE),
        "snippet": snippet[:600],
        "targets": targets[:20],
    }


def search_scan_records(
    records: Iterable[dict[str, Any]], query: str, *, limit: int = 50
) -> list[dict[str, Any]]:
    """Rank scan records by query match, newest-first, bounded by ``limit``."""
    matches = [match for record in records if (match := search_match(record, query))]
    matches.sort(key=lambda item: item["t1"], reverse=True)
    return matches[: max(1, limit)]
