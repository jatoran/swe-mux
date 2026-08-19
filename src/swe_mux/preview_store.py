"""Approved preview registrations, across daemon restarts.

Only the ones a human approved. A *detected* preview is rediscovered from the
live listener set within a poll of the daemon coming back, and now returns under
the same id (`preview_id`), so persisting it would be redundant state that could
only go stale. A **user-approved** preview is the opposite case: it exists
precisely because mux could not attribute the listener to a session - the server
is in WSL, in Docker, or in a process tree mux does not own - so nothing will
ever rediscover it. Before this, those vanished on every daemon restart and had
to be re-added by hand, which is the one preview the user had already told us
they could not express any other way.

A JSON file rather than a table in the shared database: the set is small, it is
written only when a human adds or removes one, and it carries no history worth
querying. `SettingsStore` and `PlatformSecretStore` take the same shape for the
same reason.

Nothing here is authoritative at runtime - `PreviewRegistry.items` is. This is a
mirror, so a corrupt or unreadable file costs the approved previews and never the
daemon's ability to start.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PREVIEW_STORE_SCHEMA_VERSION = 1
#: A guard against an unbounded file, not a product limit. Approved previews are
#: hand-created, so a count anywhere near this means something is looping.
MAX_STORED_PREVIEWS = 200
#: Fields restored onto a PreviewRegistration. Deliberately explicit: a field
#: added to the dataclass later must be considered here rather than silently
#: restored from whatever an older file happened to contain.
STORED_FIELDS = (
    "id",
    "session_id",
    "project_id",
    "url",
    "host",
    "port",
    "source",
    "created_at",
    "project_scope_id",
    "repo_group_id",
    "viewport",
    "listed",
)


class PreviewStore:
    """The approved-preview mirror. Read once at startup, rewritten on change."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "previews.json"

    def load(self) -> list[dict[str, Any]]:
        """Stored approved previews, or [] for anything unreadable.

        Never raises. A daemon that cannot start because a mirror file is
        malformed would be a far worse failure than losing the mirror.
        """
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("preview store unreadable (%s); starting with none", exc)
            return []
        if not isinstance(parsed, dict):
            return []
        items = parsed.get("items")
        if not isinstance(items, list):
            return []
        restored: list[dict[str, Any]] = []
        for entry in items[:MAX_STORED_PREVIEWS]:
            if not isinstance(entry, dict):
                continue
            record = {key: entry.get(key) for key in STORED_FIELDS if key in entry}
            # An entry that cannot route is not worth restoring: it would occupy a
            # sidebar row pointing at nothing the proxy can reach.
            if not record.get("id") or not record.get("url") or not record.get("host"):
                continue
            port = record.get("port")
            if not isinstance(port, int | str):
                continue
            try:
                record["port"] = int(port)
            except ValueError:
                continue
            restored.append(record)
        return restored

    def save(self, records: list[dict[str, Any]]) -> None:
        """Rewrite the mirror. Best-effort: a failure here never breaks a request."""
        payload = {
            "schema_version": PREVIEW_STORE_SCHEMA_VERSION,
            "items": [
                {key: record.get(key) for key in STORED_FIELDS}
                for record in records[:MAX_STORED_PREVIEWS]
            ],
        }
        temporary = self.path.with_suffix(".json.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError as exc:
            log.warning("could not persist approved previews (%s)", exc)
