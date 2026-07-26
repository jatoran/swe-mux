"""Native clipboard history: a bounded ring of the texts copied inside swe-mux.

Every copy the browser makes goes through one chokepoint (`Clipboard.writeText`
plus the native `copy`/`cut` events), so the frontend can report each one here
without any call site knowing about it. The ring lives server-side rather than in
each device's localStorage for the same reason settings do: the phone and the
desktop drive one install, and a history the phone cannot read is useless exactly
where the platform clipboard is most hostile.

Two deliberate safety properties, because a clipboard ring accumulates
credentials by nature:

* **Memory-only by default.** The ring is authoritative in memory; SQLite is a
  mirror written only when ``clipboard_history_persist`` is on. With persistence
  off nothing reaches disk, and a daemon restart starts clean. Turning
  persistence off deletes the rows that were already written.
* **Secret-shaped text is dropped, not stored.** ``looks_like_secret`` rejects
  the common provider key/token/JWT/private-key shapes before an entry exists.
  It is a heuristic and is documented as one; it is not a substitute for not
  copying secrets.

Entries longer than ``entry_max_chars`` are skipped outright rather than stored
truncated: a history entry that pastes a silently partial payload into an agent
prompt is worse than a missing one.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import sqlite3
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from .sqlite_store import database_operation_lock, run_sqlite_operation

log = logging.getLogger(__name__)

T = TypeVar("T")

CLIPBOARD_SCHEMA_VERSION = 1
#: Single-line label shown in the picker; the full text is fetched per entry.
PREVIEW_CHARS = 180
_MAX_SOURCE_CHARS = 32
_MAX_DEVICE_CHARS = 16

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS clipboard_entries (
  id TEXT PRIMARY KEY,
  content_hash TEXT NOT NULL,
  text TEXT NOT NULL,
  char_count INTEGER NOT NULL,
  line_count INTEGER NOT NULL,
  source TEXT NOT NULL DEFAULT '',
  session_id TEXT,
  project_id TEXT,
  device TEXT NOT NULL DEFAULT '',
  pinned INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_clipboard_hash ON clipboard_entries(content_hash);
CREATE INDEX IF NOT EXISTS idx_clipboard_recent ON clipboard_entries(updated_at DESC);
"""

# Credential shapes worth refusing outright. Deliberately specific: a heuristic
# that also swallows ordinary copies (git SHAs, hashes, base64 blobs) would make
# the feature feel broken, so the generic high-entropy rule below is narrow.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-(?:ant-|proj-|live-|test-)?[A-Za-z0-9_-]{16,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{16,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bASIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"\bnpm_[A-Za-z0-9]{30,}"),
    re.compile(r"\bpypi-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}"),
    # JWT: header.payload.signature
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    # Assignment forms: PASSWORD=…, "api_key": "…", apiKey => "…". The optional
    # quote before the separator is what makes the JSON/TOML shape match.
    re.compile(
        r"(?i)\b(?:api[_-]?key|secret(?:[_-]?key)?|access[_-]?token|auth[_-]?token"
        r"|refresh[_-]?token|client[_-]?secret|password|passwd|pwd)\b"
        r"[\"']?\s*(?:[:=]|=>)\s*[\"']?\S{8,}"
    ),
    re.compile(r"(?i)\bauthorization\s*:\s*(?:bearer|basic)\s+\S{8,}"),
)

# A lone opaque token: no whitespace, long, and mixed case *and* digits. Hex
# SHAs, uppercase hashes, and file paths all fail at least one of those.
_OPAQUE_TOKEN = re.compile(r"\A[A-Za-z0-9+/=_.~-]{40,256}\Z")


def looks_like_secret(text: str) -> bool:
    """True when the copied text has the shape of a credential.

    Heuristic by design (see module docstring): it exists so an ordinary
    ``export TOKEN=…`` copy does not silently join a persisted ring, not as a
    guarantee that no secret can ever be recorded.
    """

    if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
        return True
    candidate = text.strip()
    if not _OPAQUE_TOKEN.fullmatch(candidate):
        return False
    return (
        any(character.islower() for character in candidate)
        and any(character.isupper() for character in candidate)
        and any(character.isdigit() for character in candidate)
    )


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:32]


def clipboard_preview(text: str, *, limit: int = PREVIEW_CHARS) -> str:
    """One-line, whitespace-collapsed label for the picker list."""

    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(1, limit - 1)].rstrip() + "…"


def _clean(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return "".join(character for character in text if character.isprintable())[:limit]


@dataclass(slots=True)
class ClipboardEntry:
    id: str
    content_hash: str
    text: str
    char_count: int
    line_count: int
    source: str
    session_id: str | None
    project_id: str | None
    device: str
    pinned: bool
    created_at: float
    updated_at: float

    def snapshot(self, *, include_text: bool = False) -> dict[str, Any]:
        """Wire form. The list view never carries full text — only the id fetch does."""

        payload: dict[str, Any] = {
            "id": self.id,
            "preview": clipboard_preview(self.text),
            "char_count": self.char_count,
            "line_count": self.line_count,
            "source": self.source,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "device": self.device,
            "pinned": self.pinned,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_text:
            payload["text"] = self.text
        return payload


class ClipboardStore:
    """Bounded newest-first ring of copied texts, mirrored to SQLite on request."""

    def __init__(
        self,
        path: Path,
        *,
        enabled: bool = True,
        persist: bool = False,
        limit: int = 200,
        entry_max_chars: int = 100_000,
        retention_hours: int = 24,
        redact_secrets: bool = True,
        clock: Callable[[], float] = time.time,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.enabled = enabled
        self.persist = persist
        self.limit = max(1, limit)
        self.entry_max_chars = max(1, entry_max_chars)
        self.retention_hours = max(0, retention_hours)
        self.redact_secrets = redact_secrets
        self._clock = clock
        self._entries: list[ClipboardEntry] = []
        self._closed = False
        self._resync: asyncio.Task[None] | None = None
        self._operation_lock = database_operation_lock(path)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mux-clipboard-db")
        self._executor.submit(self._connect).result()

    # ---------------------------------------------------------------- plumbing

    def _connect(self) -> None:
        with self._operation_lock:
            self._db = sqlite3.connect(self.path, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute("PRAGMA busy_timeout=5000")
            self._db.executescript(SCHEMA)
            self._db.commit()

    async def _run(self, fn: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, run_sqlite_operation, self._db, self._operation_lock, fn
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.submit(self._db.close).result()
        self._executor.shutdown(wait=True)

    async def stop(self) -> None:
        if self._resync and not self._resync.done():
            self._resync.cancel()
            await asyncio.gather(self._resync, return_exceptions=True)
        self._resync = None

    # -------------------------------------------------------------- lifecycle

    async def load(self) -> int:
        """Adopt the persisted ring at startup (no-op when not persisting).

        With persistence off this also clears any rows an earlier persisted run
        left behind, so flipping the setting off is what it says it is.
        """

        if not self.persist:
            await self._purge_rows()
            return 0
        rows = await self._run(
            lambda: self._db.execute(
                "SELECT * FROM clipboard_entries ORDER BY updated_at DESC LIMIT ?",
                (self.limit * 2,),
            ).fetchall()
        )
        self._entries = [_entry_from_row(row) for row in rows]
        await self.prune()
        return len(self._entries)

    def apply_config(self, config: Any) -> None:
        """Adopt changed ``clipboard_history_*`` settings without a restart."""

        self.limit = max(1, int(getattr(config, "clipboard_history_limit", self.limit)))
        self.entry_max_chars = max(
            1, int(getattr(config, "clipboard_history_entry_max_chars", self.entry_max_chars))
        )
        self.retention_hours = max(
            0, int(getattr(config, "clipboard_history_retention_hours", self.retention_hours))
        )
        self.redact_secrets = bool(
            getattr(config, "clipboard_history_redact_secrets", self.redact_secrets)
        )
        was_enabled = self.enabled
        was_persist = self.persist
        self.enabled = bool(getattr(config, "clipboard_history_enabled", self.enabled))
        self.persist = bool(getattr(config, "clipboard_history_persist", self.persist))
        # Disabling means "stop keeping my clipboard", so the ring goes too — and
        # with it any rows a persisted run had already written, otherwise turning
        # history off would leave the durable copy of it sitting on disk.
        if was_enabled and not self.enabled:
            self._entries = []
        clear_rows = (was_persist and not self.persist) or (was_enabled and not self.enabled)
        self._schedule(self._resync_persistence(clear_rows=clear_rows))

    def _schedule(self, coroutine: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coroutine.close()
            return
        if self._resync and not self._resync.done():
            self._resync.cancel()
        self._resync = loop.create_task(coroutine, name="clipboard-resync")

    async def _resync_persistence(self, *, clear_rows: bool) -> None:
        try:
            if clear_rows or not self.persist:
                await self._purge_rows()
            elif self.persist:
                await self._flush_rows()
            await self.prune()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a settings change must not break the daemon
            log.exception("clipboard persistence resync failed")

    # ----------------------------------------------------------------- writes

    async def capture(
        self,
        text: Any,
        *,
        source: str = "",
        session_id: str | None = None,
        project_id: str | None = None,
        device: str = "",
    ) -> tuple[ClipboardEntry | None, str]:
        """Record one copy. Returns ``(entry, reason)``; entry is None when skipped.

        Reasons: ``stored``, ``promoted`` (an existing identical entry moved to the
        front), ``disabled``, ``empty``, ``too_large``, ``secret``.
        """

        if not self.enabled:
            return None, "disabled"
        if not isinstance(text, str) or not text.strip():
            return None, "empty"
        if len(text) > self.entry_max_chars:
            return None, "too_large"
        if self.redact_secrets and looks_like_secret(text):
            return None, "secret"
        now = self._clock()
        digest = content_hash(text)
        existing = next((item for item in self._entries if item.content_hash == digest), None)
        if existing is not None:
            existing.updated_at = now
            existing.source = _clean(source, _MAX_SOURCE_CHARS) or existing.source
            if session_id:
                existing.session_id = str(session_id)
            if project_id:
                existing.project_id = str(project_id)
            if device:
                existing.device = _clean(device, _MAX_DEVICE_CHARS)
            self._sort()
            await self._persist_entry(existing)
            return existing, "promoted"
        entry = ClipboardEntry(
            id=uuid.uuid4().hex,
            content_hash=digest,
            text=text,
            char_count=len(text),
            line_count=text.count("\n") + 1,
            source=_clean(source, _MAX_SOURCE_CHARS),
            session_id=str(session_id) if session_id else None,
            project_id=str(project_id) if project_id else None,
            device=_clean(device, _MAX_DEVICE_CHARS),
            pinned=False,
            created_at=now,
            updated_at=now,
        )
        self._entries.insert(0, entry)
        self._sort()
        await self._persist_entry(entry)
        await self.prune()
        return entry, "stored"

    async def set_pinned(self, entry_id: str, pinned: bool) -> ClipboardEntry:
        entry = self.entry(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        entry.pinned = bool(pinned)
        await self._persist_entry(entry)
        return entry

    async def delete(self, entry_id: str) -> bool:
        entry = self.entry(entry_id)
        if entry is None:
            return False
        self._entries = [item for item in self._entries if item.id != entry_id]
        await self._delete_rows([entry_id])
        return True

    async def clear(self, *, include_pinned: bool = False) -> int:
        doomed = [item for item in self._entries if include_pinned or not item.pinned]
        if not doomed:
            return 0
        keep = {item.id for item in self._entries} - {item.id for item in doomed}
        self._entries = [item for item in self._entries if item.id in keep]
        await self._delete_rows([item.id for item in doomed])
        return len(doomed)

    async def prune(self) -> int:
        """Drop expired and over-limit entries. Pinned entries are exempt from both."""

        doomed: list[str] = []
        if self.retention_hours:
            cutoff = self._clock() - self.retention_hours * 3600
            doomed.extend(
                item.id for item in self._entries if not item.pinned and item.updated_at < cutoff
            )
        if doomed:
            dropped = set(doomed)
            self._entries = [item for item in self._entries if item.id not in dropped]
        unpinned = [item for item in self._entries if not item.pinned]
        overflow = len(unpinned) - self.limit
        if overflow > 0:
            evicted = {item.id for item in unpinned[-overflow:]}
            self._entries = [item for item in self._entries if item.id not in evicted]
            doomed.extend(evicted)
        if doomed:
            await self._delete_rows(doomed)
        return len(doomed)

    # ------------------------------------------------------------------ reads

    def entries(self) -> list[ClipboardEntry]:
        return list(self._entries)

    def entry(self, entry_id: str) -> ClipboardEntry | None:
        return next((item for item in self._entries if item.id == entry_id), None)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "persist": self.persist,
            "limit": self.limit,
            "entry_max_chars": self.entry_max_chars,
            "retention_hours": self.retention_hours,
            "redact_secrets": self.redact_secrets,
            "count": len(self._entries),
        }

    # ------------------------------------------------------------- internals

    def _sort(self) -> None:
        # Pinned first, then most recent. Eviction reads the unpinned tail.
        self._entries.sort(key=lambda item: (not item.pinned, -item.updated_at))

    async def _persist_entry(self, entry: ClipboardEntry) -> None:
        if not self.persist or self._closed:
            return

        def op() -> None:
            self._db.execute(
                "INSERT INTO clipboard_entries(id,content_hash,text,char_count,line_count,"
                "source,session_id,project_id,device,pinned,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(content_hash) DO UPDATE SET "
                "id=excluded.id,source=excluded.source,session_id=excluded.session_id,"
                "project_id=excluded.project_id,device=excluded.device,pinned=excluded.pinned,"
                "updated_at=excluded.updated_at",
                (
                    entry.id,
                    entry.content_hash,
                    entry.text,
                    entry.char_count,
                    entry.line_count,
                    entry.source,
                    entry.session_id,
                    entry.project_id,
                    entry.device,
                    int(entry.pinned),
                    entry.created_at,
                    entry.updated_at,
                ),
            )
            self._db.commit()

        await self._run(op)

    async def _delete_rows(self, entry_ids: list[str]) -> None:
        if not entry_ids or self._closed:
            return

        def op() -> None:
            self._db.executemany(
                "DELETE FROM clipboard_entries WHERE id=?", [(item,) for item in entry_ids]
            )
            self._db.commit()

        await self._run(op)

    async def _purge_rows(self) -> None:
        if self._closed:
            return

        def op() -> None:
            self._db.execute("DELETE FROM clipboard_entries")
            self._db.commit()

        await self._run(op)

    async def _flush_rows(self) -> None:
        """Write the live ring out after persistence is switched on."""

        for entry in list(self._entries):
            await self._persist_entry(entry)


def _entry_from_row(row: sqlite3.Row) -> ClipboardEntry:
    return ClipboardEntry(
        id=str(row["id"]),
        content_hash=str(row["content_hash"]),
        text=str(row["text"]),
        char_count=int(row["char_count"]),
        line_count=int(row["line_count"]),
        source=str(row["source"] or ""),
        session_id=str(row["session_id"]) if row["session_id"] else None,
        project_id=str(row["project_id"]) if row["project_id"] else None,
        device=str(row["device"] or ""),
        pinned=bool(row["pinned"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )
