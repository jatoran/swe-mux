"""In-process bridge from a real agent transcript to real Tier 0 facts.

The live automations canary needs facts a real CLI produced, not fixtures, but it
must run with no daemon and no port so it stays parallel-safe in any worktree. This
helper closes that gap: it replays a real transcript through the *production*
observer dispatch (`_dispatch_transcript_event`, the same call the live daemon makes)
and captures the `tool_use`/`tool_result` events the observer emits into a real
`Tier0Store`, exactly as the daemon's event-bus consumer does. The facts that land
in the store are therefore the facts the daemon would have captured from the same
run, which is what lets the automations tier assert the deterministic detectors and
the mux memory tools over real cross-session lineage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from swe_mux.tier0_store import Tier0Store
from tests.support.detection_replay import DetectionReplay

# The event kinds Tier 0 capture consumes (mirrors tier0_store._CAPTURED_EVENTS).
# A transcript replay only ever produces the two tool kinds; git and compaction
# come from other monitors, so they never appear here and that is fine.
_CAPTURED = {"tool_use", "tool_result", "git_changed", "context_compacted"}


def transcript_records(path: Path) -> list[dict[str, Any]]:
    """Every JSON object line of a transcript, ignoring anything unparseable."""
    result: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            result.append(item)
    return result


async def capture_facts_from_transcript(
    backend: str,
    transcript: Path,
    store: Tier0Store,
    *,
    session_id: str,
    agent_run_id: str,
    project_id: str,
) -> int:
    """Replay one real transcript into `store` and return the fact count.

    Each record goes through the production observer, whose emitted `tool_use`/
    `tool_result` events carry the normalized target and content hash. Those events
    are handed to `Tier0Store.record_from_event` — the same method the daemon's bus
    consumer calls — so the recorded facts are byte-for-byte what production would
    capture. `session_id`/`agent_run_id`/`project_id` stamp the facts, and two runs
    into one store under distinct session ids produce the cross-session lineage
    provenance reads.
    """
    replay = DetectionReplay(backend)
    replay.session.record.id = session_id
    replay.session.record.agent_run_id = agent_run_id
    replay.session.record.project_id = project_id
    queue = replay.events.subscribe(name="tier0-capture")
    captured = 0
    try:
        for record in transcript_records(transcript):
            await replay.transcript_record(record)
            # Drain DetectionReplay's own subscriber so its queue never backs up;
            # our capture queue is independent and drained separately below.
            await replay.drain()
            while not queue.empty():
                event = queue.get_nowait()
                if event.type not in _CAPTURED:
                    continue
                fact_id = await store.record_from_event(
                    event, agent_run_id=agent_run_id, project_id=project_id
                )
                if fact_id is not None:
                    captured += 1
    finally:
        replay.events.unsubscribe(queue)
    return captured
