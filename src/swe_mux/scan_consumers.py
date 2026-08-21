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

#: Bounds on the catch-me-up digest. A digest is read to answer "is this run
#: healthy" and its cost has to stay proportional to that question, not to the
#: run's length: measured on the live store, an unbounded digest of a 230-record
#: run rendered 17 KB (~4.3k tokens), because `progress` emitted one bullet per
#: phase segment and neither summaries nor claims were length-capped. Every bound
#: reports what it dropped rather than truncating silently.
DIGEST_MAX_SEGMENTS = 8
DIGEST_MAX_SUMMARIES = 2
DIGEST_MAX_CLAIMS = 8
DIGEST_MAX_PHASES = 40
DIGEST_MAX_LINE_CHARS = 240

#: Bounds on the compact record projection. `target` is the single largest field
#: in a stored record (211 KB of 1.2 MB across the live store's 379 records), so
#: collapsing it to a count plus a few representative paths is worth more than
#: dropping every hash and id combined.
PROJECTION_MAX_TARGETS = 3
PROJECTION_MAX_CHARS = 600

#: How a repair string names the field it repaired. `_validate_semantics` in
#: `scan_timeline.py` is the only writer of these strings, and the two must move
#: together. Classification exists because the raw list cries wolf: 99 of the
#: live store's 114 repairs are `behavior repeated a label`, which says nothing
#: about whether `work_phase` was a model assertion or an enum fallback - and
#: telling those apart is the whole reason a reader is shown repairs at all.
_REPAIR_FIELD_LISTS = ("dropped unknown fields:", "filled missing fields:")
#: The record fields a repair string can name as its first word. Matching against
#: this set rather than against a sentence shape is deliberate: the strings are
#: prose ("behavior repeated a label", "summary was truncated to 600
#: characters"), and a grammatical rule that happened to fit today's wording
#: would silently start returning "other" the next time one is reworded.
_REPAIRABLE_FIELDS = frozenset(
    {
        "behavior",
        "work_phase",
        "blocked_on",
        "approach_status",
        "intent",
        "claim",
        "user_ask",
        "summary",
        "dead_end",
        "confidence",
    }
)


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


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def repaired_fields(repairs: Any) -> list[str]:
    """Which record fields a repair list touched, sorted and deduplicated.

    Reads the strings `_validate_semantics` writes. A repair either opens with
    the field it concerns (``"summary was truncated to 600 characters"``,
    ``"behavior repeated a label"``) or is one of the two comma-list forms named
    in `_REPAIR_FIELD_LISTS`. Anything else is reported under ``"other"`` rather
    than dropped, because a repair a reader cannot attribute is still a reason
    to trust the record less.
    """
    if not isinstance(repairs, list):
        return []
    fields: set[str] = set()
    for entry in repairs:
        text = str(entry).strip()
        if not text:
            continue
        matched = False
        for prefix in _REPAIR_FIELD_LISTS:
            if text.startswith(prefix):
                for name in text[len(prefix) :].split(","):
                    if name.strip():
                        fields.add(name.strip())
                matched = True
                break
        if matched:
            continue
        head = text.split(" ", 1)[0]
        fields.add(head if head in _REPAIRABLE_FIELDS else "other")
    return sorted(fields)


def project_record(
    record: dict[str, Any],
    *,
    max_targets: int = PROJECTION_MAX_TARGETS,
    max_chars: int = PROJECTION_MAX_CHARS,
) -> dict[str, Any]:
    """One scan record reduced to what a reading agent can act on.

    Drops `evidence_refs`, `tier0_fact_ids`, `prompt_hash`, `prompt_version` and
    `observer_model` (42% of stored record bytes, and none of it actionable) and
    collapses `target` to a count plus a few representative paths (another 17%).

    Three things survive that look like metadata and are not:

    - `repaired_fields`, because `_ENUM_FALLBACKS` substitutes `unknown`/`none`
      for an out-of-range enum and a stored fallback is otherwise
      indistinguishable from a model assertion.
    - `messages_seen` and `truncated`, because they say how thin the window
      behind a judgement was. A `work_phase` decided from one `tool_result` and
      one decided from forty messages are not the same claim.
    - `approach_status` only when the record carries it. A record scanned from a
      window too narrow to support a run-level judgement omits the key, and
      rendering an absent field as `unknown` would put the model's silence and
      the model's uncertainty in the same box.
    """
    coverage = record.get("coverage")
    coverage = coverage if isinstance(coverage, dict) else {}
    targets = sorted(target_set(record))
    projected: dict[str, Any] = {
        "id": str(record.get("id") or ""),
        "agent_run_id": str(record.get("agent_run_id") or ""),
        "t0": float(record.get("t0") or 0.0),
        "t1": float(record.get("t1") or 0.0),
        "trigger": str(record.get("trigger") or ""),
        "work_phase": str(record.get("work_phase") or _UNKNOWN_PHASE),
        "blocked_on": str(record.get("blocked_on") or "none"),
        "behavior": [str(item) for item in (record.get("behavior") or [])],
        "intent": _clip(str(record.get("intent") or ""), max_chars),
        "summary": _clip(str(record.get("summary") or ""), max_chars),
        "confidence": float(record.get("confidence") or 0.0),
        "target_count": len(targets),
        "targets": targets[: max(0, max_targets)],
        "messages_seen": int(coverage.get("messages_seen") or 0),
    }
    if coverage.get("truncated"):
        projected["window_truncated"] = True
    if "approach_status" in record and record.get("approach_status") is not None:
        projected["approach_status"] = str(record["approach_status"])
    dead_end = str(record.get("dead_end") or "").strip()
    if dead_end:
        projected["dead_end"] = _clip(dead_end, max_chars)
    repaired = repaired_fields(record.get("repairs"))
    if repaired:
        projected["repaired_fields"] = repaired
    return projected


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


def handoff_progress(
    records: list[dict[str, Any]],
    *,
    max_segments: int | None = None,
    max_summaries: int = 3,
    max_chars: int | None = None,
) -> list[str]:
    """Phase-structured progress bullets for a run handoff.

    "Was in X, hit blocker Y, next step Z" - one bullet per phase segment, with
    the blocker labels and the run's last-asserted claim folded in. Empty when
    there is no scan spine to structure.

    Unbounded by default, because a handoff export is read once by a human who
    wants the whole run. ``max_segments`` keeps the **most recent** segments,
    which is what a reader deciding "is this run healthy now" needs; the caller
    is responsible for saying how many were dropped.
    """
    segments = phase_segments(records)
    if max_segments is not None and max_segments >= 0:
        segments = segments[len(segments) - max_segments :] if max_segments else []
    lines: list[str] = []
    for segment in segments:
        summaries = segment["summaries"][:max_summaries]
        if max_chars is not None:
            summaries = [_clip(item, max_chars) for item in summaries]
        body = "; ".join(summaries) if summaries else "no distilled summary"
        bullet = f"**{segment['work_phase']}**: {body}"
        if segment["blockers"]:
            bullet += f" (blocked on {', '.join(segment['blockers'])})"
        lines.append(bullet)
    return lines


def catch_me_up(
    records: list[dict[str, Any]],
    agent_run_id: str,
    *,
    max_segments: int = DIGEST_MAX_SEGMENTS,
    max_summaries: int = DIGEST_MAX_SUMMARIES,
    max_claims: int = DIGEST_MAX_CLAIMS,
    max_phases: int = DIGEST_MAX_PHASES,
    max_chars: int = DIGEST_MAX_LINE_CHARS,
) -> dict[str, Any]:
    """An on-demand rollup of one run's scan spine.

    Names the phases the run went through, the distinct claims it asserted, and
    the blocker it is currently on, always attributed to ``agent_run_id``.

    Bounded so the digest stays proportional to the question rather than to the
    run: `progress` keeps the most recent segments, `claims` the most recent
    claims, and every line is length-capped. Whatever a bound dropped is counted
    in the result, because a digest that quietly omits the first half of a run
    reads exactly like a run that had no first half.
    """
    segments = phase_segments(records)
    phases = [segment["work_phase"] for segment in segments]
    claims: list[str] = []
    for record in records:
        claim = str(record.get("claim") or "").strip()
        if claim and claim not in claims:
            claims.append(claim)
    blocker = live_blocker(records, agent_run_id)
    digest: dict[str, Any] = {
        "agent_run_id": agent_run_id,
        "record_count": len(records),
        "phases": phases[len(phases) - max_phases :] if max_phases > 0 else [],
        "claims": [
            _clip(item, max_chars)
            for item in (claims[len(claims) - max_claims :] if max_claims > 0 else [])
        ],
        "current_blocker": blocker,
        "progress": handoff_progress(
            records,
            max_segments=max_segments,
            max_summaries=max_summaries,
            max_chars=max_chars,
        ),
    }
    omitted = {
        "phase_segments": len(segments),
        "phase_segments_omitted": max(0, len(segments) - max_segments),
        "claims_omitted": max(0, len(claims) - max_claims),
    }
    digest.update(omitted)
    return digest


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
