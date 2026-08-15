"""Identity of the production frontend currently served by the daemon."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

UI_BUILD_META_NAME = "ui-build"
UI_BUILD_ID_LENGTH = 64


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
_cache_lock = threading.Lock()


def read_ui_build_id(frontend_dir: Path) -> str | None:
    """Read the served index identity, rechecking only when its stat changes."""

    path = frontend_dir / "index.html"
    try:
        stat = path.stat()
    except OSError:
        with _cache_lock:
            _cache.pop(path, None)
        return None
    with _cache_lock:
        cached = _cache.get(path)
        if cached and cached.modified_ns == stat.st_mtime_ns and cached.size == stat.st_size:
            return cached.build_id
    try:
        build_id = parse_ui_build_id(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        build_id = None
    with _cache_lock:
        _cache[path] = _CacheEntry(stat.st_mtime_ns, stat.st_size, build_id)
    return build_id
