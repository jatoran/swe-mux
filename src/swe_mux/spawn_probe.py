"""Proof that a pane survived its own spawn, and the CLI's own words when it did not.

Resume and Branch both hand the operator a pane that continues an existing
conversation. A CLI that refuses the conversation it was given does not fail the
spawn: it starts, prints one line, and exits about a second later — *after* the
HTTP response that announced success. What reaches the operator is a grey pane
with no message, no event and no log line, and the only way back to the cause is
an archaeology pass over access logs and transcript birth times.

Handing back a pane that spawned dead is the defect, not a degraded success. Every
flow that opens a conversation on the operator's behalf therefore proves the pane
is still up before returning it, discards one that is not, and reports the
harness's own dying output as the reason.

Two properties keep the check honest:

- **It is a spawn check, not a health check.** The window is short and the proof
  deliberately weak. A pane that dies later is ordinary session lifecycle and
  belongs to the watchdog, not here.
- **It never guesses why.** The text is whatever the harness printed, cleaned of
  terminal control bytes and nothing else. That is what makes it survive a CLI
  changing its refusals, and what makes it work for a harness this module has
  never heard of.

Retrying is the caller's decision, because only the caller knows whether the
failure it is racing is transient. A refusal that will repeat forever must be
reported on the first attempt rather than retried into a slow identical failure.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# Enough of the pane's end to hold a refusal that a CLI wrapped over several
# lines, and small enough that cleaning it is free.
PANE_TAIL_BYTES = 8192
# What survives into a log line or an HTTP error: the last few lines of real text.
PANE_TEXT_LINES = 4
PANE_TEXT_CHARS = 400

# OSC (terminated by BEL or ST), then CSI, then the two-byte escapes. Ordered so
# the longest form wins; `\x1b]` would otherwise be eaten by the two-byte rule.
_ESCAPES = re.compile(
    rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC ... BEL | ST
    rb"|\x1b\[[0-?]*[ -/]*[@-~]"  # CSI ... final
    rb"|\x1b[@-Z\\-_]"  # ESC <single>
)
_CONTROLS = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def pane_text(data: bytes) -> str:
    """The last lines a pane printed, as readable text.

    Wrapped lines are rejoined with a single space. A terminal hard-wraps at its
    right margin rather than at a word boundary, so a long refusal can come back
    with one space inside one word; that is accepted deliberately, because the
    alternative is guessing the pane's width and re-flowing text that may not have
    been wrapped at all.
    """
    if not data:
        return ""
    stripped = _CONTROLS.sub(b"", _ESCAPES.sub(b"", data.replace(b"\r\n", b"\n")))
    text = stripped.decode("utf-8", "replace").replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n")]
    kept = [line for line in lines if line][-PANE_TEXT_LINES:]
    joined = " ".join(kept)
    return joined[:PANE_TEXT_CHARS].strip()


@dataclass(frozen=True, slots=True)
class SpawnFailure:
    """A pane that did not survive its own spawn window."""

    state: str
    exit_code: int | None
    text: str

    def describe(self) -> str:
        code = "" if self.exit_code is None else f" (exit code {self.exit_code})"
        return f"{self.state}{code}: {self.text}" if self.text else f"{self.state}{code}"


def _pane_tail(session: Any) -> str:
    buffer = getattr(session, "scrollback", None)
    if buffer is None:
        return ""
    try:
        return pane_text(buffer.tail_bytes(PANE_TAIL_BYTES))
    except Exception:  # noqa: BLE001 - a diagnostic must never mask the failure it explains
        log.warning("pane tail unreadable for %s", getattr(session.record, "id", "?"))
        return ""


async def settle_pane(
    session: Any, seconds: float, *, alive: Callable[[Any], bool] | None = None
) -> SpawnFailure | None:
    """How the pane died inside its settle window, or None if it is still up.

    A pane that failed to take its conversation does not merely fail to appear —
    it reaches ``idle`` first and dies a beat later, so "it left the starting
    state" is not evidence of health and the whole window has to be waited out.

    ``alive`` is the one exception: a caller that can *prove* the pane took what it
    was given ends the window early. Without such a proof the window is a fixed
    cost on every success, which is the wrong side to be slow on. The proof has to
    be positive evidence from outside mux — "it has not died yet" is what the
    window already measures.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while loop.time() < deadline:
        await asyncio.sleep(0.2)
        state = str(getattr(session.record, "state", "") or "")
        if state in {"exited", "crashed"}:
            return SpawnFailure(
                state=state,
                exit_code=getattr(session.record, "exit_code", None),
                text=_pane_tail(session),
            )
        if alive is not None and alive(session):
            return None
    return None


async def discard_pane(manager: Any, session: Any) -> None:
    """Take a pane that did not come up back out of the world."""
    with suppress(Exception):
        await manager.stop(session.record.id)
    with suppress(Exception):
        manager.sessions.pop(session.record.id, None)


async def spawn_settled(
    manager: Any,
    *,
    flow: str,
    settle_seconds: float,
    attempts: int = 1,
    retry_backoff_seconds: float = 0.0,
    alive: Callable[[Any], bool] | None = None,
    **spawn_kwargs: Any,
) -> tuple[Any | None, int, SpawnFailure | None]:
    """Spawn a pane and prove it survived, retrying up to ``attempts`` times.

    Verification rather than prediction: however confident the caller is that the
    conversation is free, the only fact that settles it is a resumed process still
    running a moment later.

    Returns ``(session, attempts_used, failure)`` — ``session`` is None only when
    every attempt died, and ``failure`` then carries the last pane's own words.
    """
    failure: SpawnFailure | None = None
    for attempt in range(1, attempts + 1):
        session = await manager.spawn(**spawn_kwargs)
        failure = await settle_pane(session, settle_seconds, alive=alive)
        if failure is None:
            if attempt > 1:
                log.info("%s pane came up on attempt %d/%d", flow, attempt, attempts)
            return session, attempt, None
        log.warning(
            "%s pane %s died on attempt %d/%d: %s",
            flow,
            session.record.id,
            attempt,
            attempts,
            failure.describe(),
        )
        await discard_pane(manager, session)
        if attempt < attempts and retry_backoff_seconds > 0:
            await asyncio.sleep(retry_backoff_seconds)
    return None, attempts, failure
