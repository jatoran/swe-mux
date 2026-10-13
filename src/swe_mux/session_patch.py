"""Field-level session row patches carried on events.

The browser keeps one row per session, merged from REST snapshots and PTY frames by
(daemon generation, session revision) (`frontend/src/sessionSnapshots.ts`). An event
that changes a few fields of one row carries their complete new values here, with the
revision `Session.publish_update` stamped when it made them, so the browser applies
them to that row in place - ordered against snapshots by that revision - instead of
re-reading the whole fleet to learn them.

Measured 2026-10-12: the desktop re-read five fleet endpoints every ~7 seconds, and
the two most frequent causes were `harness_status` (~5,700 a day) and `git_changed`
(~1,400), each of which changes a handful of fields on one session.

Two rules keep a patch safe to apply:

- **Call this after `publish_update`**, so the revision names a snapshot that already
  contains these values. A snapshot at or past it supersedes the patch; one before it
  cannot roll the patched fields back.
- **Carry every field the change wrote**, serialized exactly as `SessionRecord.snapshot`
  serializes it. A patch that left out a field the change also moved would leave the
  row half-updated with nothing to say so.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, is_dataclass
from typing import Any


def session_patch(session: Any, fields: Iterable[str]) -> dict[str, Any]:
    """`{"patch": {field: value}, "session_revision": int}` for an event payload."""
    record = session.record
    return {
        "patch": {name: _snapshot_value(getattr(record, name)) for name in fields},
        "session_revision": int(getattr(session, "revision", 0) or 0),
    }


def _snapshot_value(value: Any) -> Any:
    """`value` as `dataclasses.asdict` renders it inside a record snapshot."""
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, (list, tuple)):
        return [_snapshot_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _snapshot_value(item) for key, item in value.items()}
    return value
