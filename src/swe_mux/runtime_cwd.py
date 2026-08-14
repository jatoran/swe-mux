from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

OSC7_PATTERN = re.compile(rb"\x1b\]7;([^\x07\x1b]{1,8192})(?:\x07|\x1b\\)")
OSC_SIGNAL_PATTERN = re.compile(
    rb"\x1b\](0|2|4|9;4);([^\x07\x1b]{0,8192})(?:\x07|\x1b\\)"
)
# FinalTerm/VS Code shell integration: A prompt start, B command start,
# C output start, D command finished (with an optional exit status).
OSC133_PATTERN = re.compile(rb"\x1b\]133;([A-D])(?:;([^\x07\x1b]{0,64}))?(?:\x07|\x1b\\)")


class Osc7Parser:
    """Incrementally extract OSC 7 file URIs from a PTY byte stream."""

    def __init__(self) -> None:
        self._tail = b""

    def feed(self, chunk: bytes) -> list[str]:
        data = self._tail + chunk
        values = [
            match.group(1).decode("utf-8", "replace") for match in OSC7_PATTERN.finditer(data)
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


class OscSignalParser:
    """Incrementally extract bounded title and progress OSC values."""

    def __init__(self) -> None:
        self._tail = b""
        self.title: str | None = None
        self.progress: str | None = None

    def feed(self, chunk: bytes) -> list[tuple[str, str]]:
        data = self._tail + chunk
        values: list[tuple[str, str]] = []
        for match in OSC_SIGNAL_PATTERN.finditer(data):
            code = match.group(1).decode("ascii")
            value = match.group(2).decode("utf-8", "replace")
            channel = "title" if code in {"0", "2"} else "progress"
            values.append((channel, value))
            if channel == "title":
                self.title = value
            else:
                self.progress = value
        marker = b"\x1b]"
        start = data.rfind(marker)
        if start >= 0 and not OSC_SIGNAL_PATTERN.search(data[start:]):
            self._tail = data[start:][-8198:]
        else:
            self._tail = b"\x1b" if data.endswith(b"\x1b") else b""
        return values


class Osc133Parser:
    """Incrementally extract OSC 133 shell-integration markers from a PTY stream.

    The marker that matters to attention ranking is ``D``: the human's own shell
    reporting that their command finished, which is empirically the strongest
    moment to hand them something to look at. The parser is deliberately
    transport-only — it classifies nothing and reports every marker it sees, so a
    shell that emits only a subset degrades to fewer breakpoints rather than to a
    wrong one.
    """

    def __init__(self) -> None:
        self._tail = b""
        self.last_exit_status: str | None = None

    def feed(self, chunk: bytes) -> list[tuple[str, str | None]]:
        data = self._tail + chunk
        markers: list[tuple[str, str | None]] = []
        for match in OSC133_PATTERN.finditer(data):
            marker = match.group(1).decode("ascii")
            raw = match.group(2)
            value = raw.decode("utf-8", "replace") if raw is not None else None
            if marker == "D":
                self.last_exit_status = value
            markers.append((marker, value))
        prefix = b"\x1b]133;"
        start = data.rfind(prefix[:2])
        if start >= 0 and not OSC133_PATTERN.search(data[start:]):
            self._tail = data[start:][-80:]
        else:
            self._tail = b"\x1b" if data.endswith(b"\x1b") else b""
        return markers


@dataclass(frozen=True, slots=True)
class Osc7Location:
    """Security-classified OSC 7 location.

    A remote authority is useful boundary evidence even though its path is never
    accepted as a local filesystem path. Invalid values stay distinct so malformed
    child-controlled bytes cannot assert either side of the boundary.
    """

    kind: Literal["local", "remote", "invalid"]
    path: Path | None = None
    authority: str | None = None


def classify_osc7_location(value: str) -> Osc7Location:
    try:
        parsed = urlsplit(value)
        username = parsed.username
        password = parsed.password
        port = parsed.port
        hostname = parsed.hostname
    except ValueError:
        return Osc7Location("invalid")
    if parsed.scheme.casefold() != "file" or username or password or port:
        return Osc7Location("invalid")
    authority = (hostname or "").casefold()
    local_hosts = {"", "localhost", socket.gethostname().casefold(), socket.getfqdn().casefold()}
    if authority not in local_hosts:
        return Osc7Location("remote", authority=authority)
    path_text = unquote(parsed.path)
    if os.name == "nt" and re.match(r"^/[A-Za-z]:/", path_text):
        path_text = path_text[1:]
    try:
        path = Path(path_text).resolve(strict=True)
    except (OSError, RuntimeError):
        return Osc7Location("invalid")
    return Osc7Location("local", path=path) if path.is_dir() else Osc7Location("invalid")


def local_directory_from_osc7(value: str) -> Path | None:
    """Validate an OSC 7 URI as a local, existing directory.

    OSC is controlled by the child process, so this deliberately accepts no
    remote hosts and performs no directory creation or other side effects.
    """

    location = classify_osc7_location(value)
    return location.path if location.kind == "local" else None
