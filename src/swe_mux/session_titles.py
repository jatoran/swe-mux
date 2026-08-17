"""What a session is called on screen, resolved once for every surface that shows it.

A session carries two names. ``name`` is what it was spawned or renamed with; the
generated title is an observer annotation tagged ``title`` and keyed by *agent run*,
not by session, because a conversation that rolls over keeps its own title.

The rule: the generated title wins only while the session is still ``auto_named``.
A rename is the human overriding the generator, and a title produced afterwards must
not silently take the name back.

Two shapes ask the same question and disagree about types:

- a live :class:`~swe_mux.models.SessionRecord`, where ``auto_named`` is a ``bool``
- a History row, where SQLite hands back ``0``/``1``

Both default to auto-named when the field is missing, which is what a row written
before the column existed means.

The run id is the join key to the annotation store: ``agent_run_id`` when the session
has one, falling back to the session id, which is what the History row id itself is
(``history.py`` writes ``session.agent_run_id or session.id``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

#: Run ids per annotation query, kept under SQLite's default 999-variable ceiling.
_RUN_ID_CHUNK = 400


class _AnnotationStore(Protocol):
    async def annotations(
        self,
        *,
        agent_run_ids: Sequence[str] | None = ...,
        tag: str | None = ...,
        limit: int = ...,
    ) -> list[dict[str, Any]]: ...


def record_run_id(record: Any) -> str:
    """Annotation join key for a live session record."""
    return str(getattr(record, "agent_run_id", "") or record.id)


def row_run_id(row: dict[str, Any]) -> str:
    """Annotation join key for a History/run row."""
    return str(row.get("agent_run_id") or row.get("id") or "")


async def generated_titles(
    store: _AnnotationStore | None, run_ids: set[str]
) -> dict[str, str]:
    """Latest generated title for each requested run, keyed by run id.

    Filtered by run id rather than swept off the newest N annotations: a caller
    decorating a long tail of historical runs (Git provenance covers hundreds of
    commits) would otherwise silently get no title for anything older than the
    window, which reads as "this run was never titled".
    """
    ids = sorted(run_id for run_id in run_ids if run_id)
    if not ids or store is None:
        return {}
    titles: dict[str, str] = {}
    # Chunked because the id set is unbounded in principle (Git provenance can name a
    # session per commit) while a bound `IN (...)` list is not: SQLite's default ceiling
    # is 999 variables, and exceeding it raises rather than degrading.
    for start in range(0, len(ids), _RUN_ID_CHUNK):
        chunk = ids[start : start + _RUN_ID_CHUNK]
        annotations = await store.annotations(
            agent_run_ids=chunk, tag="title", limit=1000
        )
        for annotation in annotations:
            run_id = str(annotation.get("agent_run_id") or "")
            title = str(annotation.get("content") or "").strip()
            # Ordered newest first by the store, so the first hit per run is the latest.
            if run_id and title and run_id not in titles:
                titles[run_id] = title
    return titles


def record_display_name(record: Any, titles: dict[str, str]) -> str:
    """Display name of a live session, given titles keyed by run id."""
    generated = titles.get(record_run_id(record))
    if getattr(record, "auto_named", True) and generated:
        return generated
    return str(record.name)


def row_display_name(row: dict[str, Any], titles: dict[str, str]) -> str:
    """Display name of a History/run row, given titles keyed by run id."""
    generated = titles.get(row_run_id(row))
    if bool(row.get("auto_named", 1)) and generated:
        return generated
    return str(row.get("name") or "")
