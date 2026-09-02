"""Identity of the production frontend currently served by the daemon."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

UI_BUILD_META_NAME = "ui-build"
UI_BUILD_ID_LENGTH = 64
#: How long a stat of the served index is trusted before the loop asks a thread
#: to repeat it. The health endpoint is polled every few seconds by the tray, the
#: stall banner and every open tab, and on 2026-09-02 the watchdog caught that
#: stat holding the loop for up to 15 s under a saturated disk, thirteen times in
#: one morning. The tree it describes changes on a redeploy or an overlay install,
#: both of which restart the daemon, so a few seconds of staleness costs nothing.
UI_BUILD_FRESH_SECONDS = 5.0


class _BuildMetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.build_id: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "meta" or self.build_id is not None:
            return
        values = {name.casefold(): value for name, value in attrs}
        if values.get("name") == UI_BUILD_META_NAME:
            self.build_id = normalize_ui_build_id(values.get("content"))


def normalize_ui_build_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().casefold()
    if len(candidate) != UI_BUILD_ID_LENGTH:
        return None
    return candidate if all(character in "0123456789abcdef" for character in candidate) else None


def parse_ui_build_id(html: str) -> str | None:
    parser = _BuildMetaParser()
    parser.feed(html)
    return parser.build_id


@dataclass(frozen=True)
class _CacheEntry:
    modified_ns: int
    size: int
    build_id: str | None


_cache: dict[Path, _CacheEntry] = {}
#: When each path was last stat'ed, so `ui_build_id_cached` can skip the syscall.
_checked_at: dict[Path, float] = {}
_cache_lock = threading.Lock()


def read_ui_build_id(frontend_dir: Path) -> str | None:
    """Read the served index identity, rechecking only when its stat changes.

    Synchronous and always stats. On the event loop use `ui_build_id_cached`.
    """

    path = frontend_dir / "index.html"
    try:
        stat = path.stat()
    except OSError:
        with _cache_lock:
            _cache.pop(path, None)
            _checked_at[path] = time.monotonic()
        return None
    with _cache_lock:
        cached = _cache.get(path)
        _checked_at[path] = time.monotonic()
        if cached and cached.modified_ns == stat.st_mtime_ns and cached.size == stat.st_size:
            return cached.build_id
    try:
        build_id = parse_ui_build_id(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        build_id = None
    with _cache_lock:
        _cache[path] = _CacheEntry(stat.st_mtime_ns, stat.st_size, build_id)
    return build_id


async def ui_build_id_cached(frontend_dir: Path) -> str | None:
    """The served index identity for a request handler: never a stat on the loop.

    Answers from the last reading while it is younger than `UI_BUILD_FRESH_SECONDS`,
    and otherwise repeats the stat in a thread. A missing index is remembered for
    the same window, so a tree with no frontend is not stat'ed on every poll either.
    """
    path = frontend_dir / "index.html"
    with _cache_lock:
        checked = _checked_at.get(path)
        cached = _cache.get(path)
    if checked is not None and time.monotonic() - checked < UI_BUILD_FRESH_SECONDS:
        return cached.build_id if cached is not None else None
    return await asyncio.to_thread(read_ui_build_id, frontend_dir)


def forget_ui_build_id(frontend_dir: Path) -> None:
    """Drop what is known about a tree, so the next ask stats it again."""
    path = frontend_dir / "index.html"
    with _cache_lock:
        _cache.pop(path, None)
        _checked_at.pop(path, None)
