from __future__ import annotations

import os
import re
import socket
from pathlib import Path
from urllib.parse import unquote, urlsplit

OSC7_PATTERN = re.compile(rb"\x1b\]7;([^\x07\x1b]{1,8192})(?:\x07|\x1b\\)")


class Osc7Parser:
    """Incrementally extract OSC 7 file URIs from a PTY byte stream."""

    def __init__(self) -> None:
        self._tail = b""

    def feed(self, chunk: bytes) -> list[str]:
        data = self._tail + chunk
        values = [
            match.group(1).decode("utf-8", "replace")
            for match in OSC7_PATTERN.finditer(data)
        ]
        # Retain only a possible incomplete OSC sequence, with a strict bound.
        marker = b"\x1b]7;"
        start = data.rfind(marker)
        if start >= 0 and not OSC7_PATTERN.search(data[start:]):
            self._tail = data[start:][-8196:]
        else:
            self._tail = next(
                (
                    data[-size:]
                    for size in range(len(marker) - 1, 0, -1)
                    if data.endswith(marker[:size])
                ),
                b"",
            )
        return values


def local_directory_from_osc7(value: str) -> Path | None:
    """Validate an OSC 7 URI as a local, existing directory.

    OSC is controlled by the child process, so this deliberately accepts no
    remote hosts and performs no directory creation or other side effects.
    """

    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.casefold() != "file" or parsed.username or parsed.password or parsed.port:
        return None
    local_hosts = {"", "localhost", socket.gethostname().casefold(), socket.getfqdn().casefold()}
    if (parsed.hostname or "").casefold() not in local_hosts:
        return None
    path_text = unquote(parsed.path)
    if os.name == "nt" and re.match(r"^/[A-Za-z]:/", path_text):
        path_text = path_text[1:]
    try:
        path = Path(path_text).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return path if path.is_dir() else None
