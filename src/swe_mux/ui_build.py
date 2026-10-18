"""Identity of the production frontend currently served by the daemon."""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from .executor_accounting import AccountedThreadPoolExecutor

UI_BUILD_META_NAME = "ui-build"
UI_BUILD_ID_LENGTH = 64
#: How long a stat of the served index is trusted before the loop asks a thread
#: to repeat it. The health endpoint is polled every few seconds by the tray, the
#: stall banner and every open tab, and on 2026-09-02 the watchdog caught that
#: stat holding the loop for up to 15 s under a saturated disk, thirteen times in
#: one morning. The tree it describes changes on a redeploy or an overlay install,
#: both of which restart the daemon, so a few seconds of staleness costs nothing.
UI_BUILD_FRESH_SECONDS = 5.0
#: The longest a request handler waits for a refresh before answering with what it
#: already knows. `/api/health` is the tray recovery's probe, and on 2026-10-18 it
#: queued this read behind thirty-two graveyard purges holding every worker of the
#: shared executor: the probe timed out, and a busy daemon was killed as a hung one.
#: The refresh now has a thread of its own and is never waited on for longer than
#: this, so the answer is at worst a few seconds stale, never late.
UI_BUILD_WAIT_SECONDS = 0.25


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


#: One thread for every refresh, owned here rather than borrowed from the default
#: executor, so no other work can stand between a health probe and its answer.
_refresh_pool = AccountedThreadPoolExecutor(max_workers=1, thread_name_prefix="mux-ui-build")
#: The refresh in flight per index path, so concurrent asks share one stat.
_refreshing: dict[Path, Future[str | None]] = {}


def _refresh(frontend_dir: Path) -> Future[str | None]:
    path = frontend_dir / "index.html"
    with _cache_lock:
        pending = _refreshing.get(path)
        if pending is not None and not pending.done():
            return pending
        future = _refresh_pool.submit(read_ui_build_id, frontend_dir)
        _refreshing[path] = future
    return future


async def ui_build_id_cached(frontend_dir: Path) -> str | None:
    """The served index identity for a request handler: never a stat on the loop,
    and never a wait on anybody else's work.

    Answers from the last reading while it is younger than `UI_BUILD_FRESH_SECONDS`.
    Otherwise it starts one refresh on this module's own thread and waits for it at
    most `UI_BUILD_WAIT_SECONDS`; a refresh slower than that is left to finish in the
    background and the last reading answers meanwhile (None before the first one,
    which every reader treats as "no newer build known"). A missing index is
    remembered for the same window, so a tree with no frontend is not stat'ed on
    every poll either.
    """
    path = frontend_dir / "index.html"
    with _cache_lock:
        checked = _checked_at.get(path)
        cached = _cache.get(path)
    last = cached.build_id if cached is not None else None
    if checked is not None and time.monotonic() - checked < UI_BUILD_FRESH_SECONDS:
        return last
    refresh = asyncio.wrap_future(_refresh(frontend_dir))
    try:
        return await asyncio.wait_for(asyncio.shield(refresh), UI_BUILD_WAIT_SECONDS)
    except TimeoutError:
        return last


def forget_ui_build_id(frontend_dir: Path) -> None:
    """Drop what is known about a tree, so the next ask stats it again."""
    path = frontend_dir / "index.html"
    with _cache_lock:
        _cache.pop(path, None)
        _checked_at.pop(path, None)
