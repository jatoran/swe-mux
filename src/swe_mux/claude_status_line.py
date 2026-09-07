"""Which status-line command Claude Code would run, so the tee can run it too.

The per-session `--settings` file swe-mux passes outranks the user's project and
user settings (Claude's precedence is managed > command line > local > project
> user), so a `statusLine` written there replaces the one the user configured
rather than adding to it. The tee therefore has to know the user's own command
and delegate to it, and this module is where that command is looked up.

Resolved at spawn, from the same three files Claude reads, in Claude's order:
`.claude/settings.local.json`, then `.claude/settings.json` under the spawn
directory, then the user file under the Claude data home. A command found at a
higher scope wins outright; a scope whose `statusLine` is absent or malformed
does not shadow a lower one. Managed settings are deliberately not read: a
managed `statusLine` outranks the injected one, so the tee never runs there and
there is nothing for it to delegate to.

The tee is injected **only when a delegate exists**. Configuring a status line
where the user had none changes their terminal - Claude hides most of the
footer's keyboard hints once any custom status line is set - and swe-mux must
not make that choice for them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: A settings file is small; anything past this is not one and is not read.
MAX_SETTINGS_BYTES = 1 << 20


@dataclass(frozen=True, slots=True)
class StatusLineDelegate:
    """The user's own status-line configuration, as the tee must reproduce it."""

    command: str
    #: The settings file it came from, for the identity file and the log.
    source: str
    padding: int | None = None
    refresh_interval: int | None = None
    hide_vim_mode_indicator: bool | None = None

    def settings_entry(self, tee_command: str) -> dict[str, Any]:
        """The `statusLine` object to write, with the user's presentation keys kept.

        Replacing the whole object is what Claude's precedence does; carrying
        `padding`, `refreshInterval` and `hideVimModeIndicator` across is what
        keeps the replacement invisible.
        """
        entry: dict[str, Any] = {"type": "command", "command": tee_command}
        if self.padding is not None:
            entry["padding"] = self.padding
        if self.refresh_interval is not None:
            entry["refreshInterval"] = self.refresh_interval
        if self.hide_vim_mode_indicator is not None:
            entry["hideVimModeIndicator"] = self.hide_vim_mode_indicator
        return entry


def _read_settings(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_SETTINGS_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        log.debug("status line: %s unreadable (%s)", path, exc)
        return None
    return data if isinstance(data, dict) else None


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def status_line_from_settings(data: dict[str, Any], source: str) -> StatusLineDelegate | None:
    """The delegate one settings document declares, or None when it declares none."""
    entry = data.get("statusLine")
    if not isinstance(entry, dict):
        return None
    if entry.get("type") != "command":
        return None
    command = entry.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    padding = entry.get("padding")
    hide = entry.get("hideVimModeIndicator")
    real_padding = isinstance(padding, int) and not isinstance(padding, bool) and padding >= 0
    return StatusLineDelegate(
        command=command.strip(),
        source=source,
        padding=padding if real_padding else None,
        refresh_interval=_positive_int(entry.get("refreshInterval")),
        hide_vim_mode_indicator=hide if isinstance(hide, bool) else None,
    )


def resolve_status_line_delegate(cwd: Path, data_home: Path) -> StatusLineDelegate | None:
    """The status-line command Claude would run for a session started in `cwd`."""
    scopes: tuple[tuple[Path, str], ...] = (
        (cwd / ".claude" / "settings.local.json", ".claude/settings.local.json"),
        (cwd / ".claude" / "settings.json", ".claude/settings.json"),
        (data_home / "settings.json", "~/.claude/settings.json"),
    )
    for path, label in scopes:
        data = _read_settings(path)
        if data is None:
            continue
        delegate = status_line_from_settings(data, label)
        if delegate is not None:
            return delegate
    return None
