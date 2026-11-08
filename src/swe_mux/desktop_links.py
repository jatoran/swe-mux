"""Session links: an `http(s)` link to a session that lands in the desktop app when it can.

Agents send links of the form ``https://<host>/?session=<id>``. Telegram buttons accept only
http(s), so the link stays a web URL, and the hand-off to the Windows desktop app happens from
the page that link opens. Three processes take part, and this module is the vocabulary they
share; nothing here imports the shell's toolkit or the daemon's runtime.

- **The desktop shell registers a per-user ``swe-mux://`` link type** on every start
  (`register_link_protocol`), pointing at the executable and config it is running with, so the
  registration always names the copy that would answer it. A second launch carrying a link
  (``--open-link``) hands it to the running window through a mailbox in the data directory
  plus the existing single-instance activation event (`post_link`/`drain_links`); a cold launch
  opens its window directly at the link's page URL (`page_url`).
- **The daemon decides whether a browser tab should try the hand-off at all**
  (`routes/desktop_integration.py`): only on Windows, only for a browser on this same machine,
  and only when a registered handler's executable exists. A tab on any other device, or on a
  machine with no desktop app, is never asked to open a link nothing will answer - which is
  what would otherwise leave Firefox on an error page.
- **The page that took the link reports back** (`LinkHandoffRegistry`). The browser tab polls for
  that report and closes itself only once the desktop window has the session; until then, and
  forever if the report never comes, it is the ordinary web UI on the linked session.

Every value arriving through ``swe-mux://`` is untrusted: any web page may launch the scheme.
So a link is reduced to two identifiers matched against closed patterns, nothing else in it is
read, and the script that carries it into the page JSON-encodes both.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

LINK_SCHEME = "swe-mux"
#: The one action the scheme carries: ``swe-mux://open?session=<id>&handoff=<id>``.
LINK_ACTION = "open"
#: Per-user, so registering needs no elevation and never touches another account.
PROTOCOL_KEY = rf"Software\Classes\{LINK_SCHEME}"
PROTOCOL_COMMAND_KEY = rf"{PROTOCOL_KEY}\shell\open\command"
PROTOCOL_ICON_KEY = rf"{PROTOCOL_KEY}\DefaultIcon"
PROTOCOL_DESCRIPTION = "URL:swe-mux session link"
#: The shell's own flag for a link handed over by Windows.
OPEN_LINK_FLAG = "--open-link"

#: Session ids are UUIDs today; the pattern is a closed superset, never a parser of anything.
SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
#: `secrets.token_urlsafe(24)` is 32 characters; the bounds leave room without opening it up.
HANDOFF_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,64}")

#: Where a second launch leaves a link for the running shell.
LINK_INBOX_DIR = "desktop-links"
#: A link the running shell did not pick up within this long is dropped rather than applied:
#: focus moving minutes after the click, because a wedged window recovered, is a surprise.
LINK_INBOX_MAX_AGE_SECONDS = 120.0
#: A burst of launches cannot grow the inbox without bound.
LINK_INBOX_MAX_ENTRIES = 16

#: The page-global the frontend installs before it renders (`frontend/src/sessionLinks.ts`).
PAGE_HOOK = "__swemuxOpenSessionLink"

#: How long the daemon remembers a hand-off. Long enough for a cold desktop start behind the
#: browser's "Open swe-mux?" prompt; the browser tab stops polling well before this.
HANDOFF_TTL_SECONDS = 600.0
HANDOFF_CAPACITY = 64


@dataclass(frozen=True)
class SessionLink:
    """The whole of what a link may say: which session, and which browser tab asked."""

    session_id: str
    handoff: str | None = None


def valid_session_id(value: object) -> bool:
    return isinstance(value, str) and SESSION_ID_PATTERN.fullmatch(value) is not None


def valid_handoff_id(value: object) -> bool:
    return isinstance(value, str) and HANDOFF_ID_PATTERN.fullmatch(value) is not None


def parse_session_link(raw: str) -> SessionLink | None:
    """A ``swe-mux://open?session=...`` link, or None for anything else.

    Accepts the trailing-slash form some launchers produce (``swe-mux://open/?...``). A link
    whose session is missing or malformed is refused whole, and so is one whose hand-off id is
    present but malformed: half of an untrusted link is not a link.
    """
    if not isinstance(raw, str) or len(raw) > 512:
        return None
    try:
        parts = urlsplit(raw.strip())
    except ValueError:
        return None
    if parts.scheme.casefold() != LINK_SCHEME:
        return None
    action = (parts.netloc or parts.path.strip("/")).casefold()
    if parts.netloc and parts.path.strip("/"):
        return None
    if action != LINK_ACTION:
        return None
    query = parse_qs(parts.query, keep_blank_values=True)
    sessions = query.get("session", [])
    if len(sessions) != 1 or not valid_session_id(sessions[0]):
        return None
    handoffs = query.get("handoff", [])
    if len(handoffs) > 1:
        return None
    handoff = handoffs[0] if handoffs else None
    if handoff is not None and not valid_handoff_id(handoff):
        return None
    return SessionLink(session_id=sessions[0], handoff=handoff)


def session_link_url(link: SessionLink) -> str:
    """The ``swe-mux://`` form of `link`, which `parse_session_link` reads back unchanged."""
    query = {"session": link.session_id}
    if link.handoff:
        query["handoff"] = link.handoff
    return f"{LINK_SCHEME}://{LINK_ACTION}?{urlencode(query)}"


def page_url(origin: str, link: SessionLink) -> str:
    """The web UI's own URL for `link`, which the page reads exactly as it reads a web link."""
    query = {"session": link.session_id}
    if link.handoff:
        query["handoff"] = link.handoff
    return f"{origin.rstrip('/')}/?{urlencode(query)}"


def delivery_script(link: SessionLink, origin: str) -> str:
    """JavaScript that hands `link` to a running page, or navigates there if it cannot.

    The page installs its hook before it renders, so the hook is missing only on a document
    that is not the app (WebView2's error page while the daemon restarts, `about:blank`) or in
    the instant before the bundle runs; navigating to the page URL is correct for all of them,
    because the page reads the link from its own URL on load.
    """
    payload = json.dumps({"session_id": link.session_id, "handoff": link.handoff})
    target = json.dumps(page_url(origin, link))
    return (
        "(function(){"
        f"var link={payload};"
        f"var hook=window[{json.dumps(PAGE_HOOK)}];"
        "if(typeof hook==='function'){hook(link);return;}"
        f"window.location.assign({target});"
        "})()"
    )


# -- the shell's registration -------------------------------------------------


def protocol_command(
    config_path: Path,
    *,
    executable: str | None = None,
    frozen: bool | None = None,
) -> str:
    """What Windows runs for a ``swe-mux://`` link: this shell, this config, the link.

    The same executable choice as the login command (`desktop.startup_command`): the frozen
    app runs itself, a source install runs ``pythonw -m swe_mux.desktop`` so no console flashes.
    ``%1`` is quoted by hand because `list2cmdline` would leave it bare, and Windows substitutes
    the whole link there.
    """
    executable = executable or sys.executable
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if frozen:
        parts = [executable, "--config", str(config_path)]
    else:
        python = Path(executable)
        pythonw = python.with_name("pythonw.exe")
        parts = [str(pythonw if pythonw.exists() else python), "-m", "swe_mux.desktop"]
        parts.extend(["--config", str(config_path)])
    return f'{subprocess.list2cmdline(parts)} {OPEN_LINK_FLAG} "%1"'


def command_executable(command: str) -> str:
    """The program a registered command line names: the quoted first token, or the bare one."""
    text = command.strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        return text[1:end] if end > 0 else ""
    return text.split(" ", 1)[0]


def registered_link_command() -> str | None:
    """The command the current user's ``swe-mux://`` registration runs, or None."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PROTOCOL_COMMAND_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "")
    except OSError:
        return None
    return str(value) if value else None


def link_handler_installed() -> bool:
    """Whether a ``swe-mux://`` link would start a program that exists on this machine.

    A registration whose executable is gone (an uninstalled copy, a deleted checkout) answers
    False, so a browser tab is never sent to a link that would fail to launch.
    """
    command = registered_link_command()
    if not command:
        return False
    executable = command_executable(command)
    return bool(executable) and Path(executable).is_file()


def register_link_protocol(
    config_path: Path,
    *,
    executable: str | None = None,
    frozen: bool | None = None,
) -> str | None:
    """Point the current user's ``swe-mux://`` registration at this shell.

    Returns the command when it wrote one, None when the registration already said exactly
    this - so a start that changes nothing writes nothing and logs nothing new. Raises
    `OSError` when the registry refuses, which the caller records rather than surfaces: the
    link type is a convenience on top of a working app.
    """
    if sys.platform != "win32":
        return None
    import winreg

    command = protocol_command(config_path, executable=executable, frozen=frozen)
    if registered_link_command() == command:
        return None
    program = command_executable(command)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROTOCOL_KEY) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, PROTOCOL_DESCRIPTION)
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROTOCOL_ICON_KEY) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{program}",0')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROTOCOL_COMMAND_KEY) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
    return command


# -- the second launch's mailbox ------------------------------------------------


def post_link(data_dir: Path, link: SessionLink, *, now: float | None = None) -> Path:
    """Leave `link` for the running shell; it is picked up on the activation signal.

    One file per link, written under a temporary name and renamed into place, so the reader
    never sees half of one and two launches a millisecond apart cannot overwrite each other.
    """
    inbox = data_dir / LINK_INBOX_DIR
    inbox.mkdir(parents=True, exist_ok=True)
    stamp = time.time() if now is None else now
    name = f"{int(stamp * 1_000_000):020d}-{secrets.token_hex(4)}"
    body = json.dumps({"session_id": link.session_id, "handoff": link.handoff, "posted_at": stamp})
    temporary = inbox / f"{name}.tmp"
    temporary.write_text(body, encoding="utf-8")
    final = inbox / f"{name}.json"
    os.replace(temporary, final)
    return final


@dataclass(frozen=True)
class DrainedLinks:
    links: list[SessionLink]
    #: Entries discarded unread or unapplied, by reason, for the caller's log line.
    dropped: dict[str, int]


def drain_links(
    data_dir: Path,
    *,
    now: float | None = None,
    max_age_seconds: float = LINK_INBOX_MAX_AGE_SECONDS,
    max_entries: int = LINK_INBOX_MAX_ENTRIES,
) -> DrainedLinks:
    """Take every waiting link, oldest first, removing each file as it is read.

    Stale, malformed and over-capacity entries are removed too and counted, never applied;
    a leftover `.tmp` from a launch that died mid-write is swept once it is stale.
    """
    inbox = data_dir / LINK_INBOX_DIR
    dropped: dict[str, int] = {}
    links: list[SessionLink] = []
    if not inbox.is_dir():
        return DrainedLinks(links, dropped)
    current = time.time() if now is None else now

    def drop(reason: str, path: Path) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1
        try:
            path.unlink()
        except OSError:
            pass

    try:
        entries = sorted(inbox.iterdir(), key=lambda path: path.name)
    except OSError:
        return DrainedLinks(links, dropped)
    for path in entries:
        if path.suffix == ".tmp":
            try:
                stale = current - path.stat().st_mtime > max_age_seconds
            except OSError:
                continue
            if stale:
                drop("abandoned", path)
            continue
        if path.suffix != ".json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            drop("malformed", path)
            continue
        if not isinstance(payload, dict):
            drop("malformed", path)
            continue
        session_id = payload.get("session_id")
        handoff = payload.get("handoff")
        posted_at = payload.get("posted_at")
        if not valid_session_id(session_id) or (
            handoff is not None and not valid_handoff_id(handoff)
        ):
            drop("malformed", path)
            continue
        if not isinstance(posted_at, (int, float)) or current - float(posted_at) > max_age_seconds:
            drop("stale", path)
            continue
        if len(links) >= max_entries:
            drop("overflow", path)
            continue
        try:
            path.unlink()
        except OSError:
            # Unremovable means it would be applied again on the next signal; skip it now
            # rather than deliver the same link twice.
            dropped["unremovable"] = dropped.get("unremovable", 0) + 1
            continue
        assert isinstance(session_id, str)
        links.append(
            SessionLink(
                session_id=session_id, handoff=handoff if isinstance(handoff, str) else None
            )
        )
    return DrainedLinks(links, dropped)


# -- the daemon's record of a browser tab's hand-off ------------------------------


@dataclass
class LinkHandoff:
    handoff_id: str
    session_id: str
    created_at: float
    opened_at: float | None = None
    #: Whether the desktop page found the session; None until it reports.
    found: bool | None = None

    @property
    def state(self) -> str:
        return "opened" if self.opened_at is not None else "pending"

    def as_dict(self) -> dict[str, object]:
        return {
            "handoff_id": self.handoff_id,
            "session_id": self.session_id,
            "state": self.state,
            "found": self.found,
        }


class LinkHandoffRegistry:
    """In-memory and bounded: a hand-off is a few seconds of one browser tab's life.

    Nothing here survives a daemon restart, and nothing should - the tab treats an unknown
    hand-off exactly like one that never opened, and stays as the web UI.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = HANDOFF_TTL_SECONDS,
        capacity: int = HANDOFF_CAPACITY,
        clock: Callable[[], float] = time.monotonic,
        on_expired: Callable[[LinkHandoff], None] | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._capacity = capacity
        self._clock = clock
        self._on_expired = on_expired
        self._lock = threading.Lock()
        self._items: OrderedDict[str, LinkHandoff] = OrderedDict()

    def create(self, session_id: str) -> LinkHandoff:
        if not valid_session_id(session_id):
            raise ValueError("not a session id")
        with self._lock:
            expired = self._prune_locked()
            handoff = LinkHandoff(
                handoff_id=secrets.token_urlsafe(24),
                session_id=session_id,
                created_at=self._clock(),
            )
            self._items[handoff.handoff_id] = handoff
            while len(self._items) > self._capacity:
                _, evicted = self._items.popitem(last=False)
                expired.append(evicted)
        self._report(expired)
        return handoff

    def get(self, handoff_id: str) -> LinkHandoff | None:
        with self._lock:
            expired = self._prune_locked()
            handoff = self._items.get(handoff_id)
        self._report(expired)
        return handoff

    def mark_opened(self, handoff_id: str, *, found: bool) -> LinkHandoff | None:
        with self._lock:
            expired = self._prune_locked()
            handoff = self._items.get(handoff_id)
            if handoff is not None and handoff.opened_at is None:
                handoff.opened_at = self._clock()
                handoff.found = found
        self._report(expired)
        return handoff

    def _prune_locked(self) -> list[LinkHandoff]:
        cutoff = self._clock() - self._ttl
        expired: list[LinkHandoff] = []
        while self._items:
            first = next(iter(self._items.values()))
            if first.created_at >= cutoff:
                break
            self._items.popitem(last=False)
            expired.append(first)
        return expired

    def _report(self, expired: list[LinkHandoff]) -> None:
        if self._on_expired is None:
            return
        for handoff in expired:
            self._on_expired(handoff)
