"""Honest progress for a verification gate that is running.

A land spends most of its wall clock inside one opaque subprocess, and `verifying`
alone says nothing about whether that is thirty seconds or four minutes in. The
question a reader actually has - "is this moving, and how much is left" - needs a unit,
and the only unit this module will invent is one the gate itself declared.

**Nothing here is estimated.** Three signals are read, and each is reported only when it
was really observed:

- **Steps.** The convention this repository's own `.worktree-verify` already follows -
  `printf '\\n=== %s ===\\n'` - is a step boundary a script *chose* to announce. Each
  marker starts a step; the step's name and its elapsed time are facts.
- **A step count**, and only from a previous *passing* run of byte-identical bytes
  (`land_store.verify_plan`). A failing run stops early under `set -e`, so its step list
  is a truncated one and would predict a shorter gate forever; that is why only a pass
  records a plan. A run that overruns its plan stops predicting rather than reporting
  "step 8 of 7".
- **Output lines.** The fallback for a gate that declares no steps at all. A line count
  is not progress toward an end, and is reported as what it is: evidence the process is
  still producing output.

No percentage is derived from any of it, here or downstream. A percent implies a
denominator, and for a gate whose steps take 175s and 3s in the same run there is no
honest one.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

#: Exactly three equals signs on each side, which is what the `step()` helper prints.
#: Deliberately not "a line of equals signs": pytest rules its own sections with long
#: runs of them (`===== short test summary info =====`), and a looser pattern would
#: report a failing suite's own section headers as verification steps.
_STEP_MARKER = re.compile(r"^={3}\s+(.+?)\s+={3}$")

#: Steps retained by name. A script that emits a marker in a loop still counts, but it
#: cannot grow this structure without bound.
MAX_TRACKED_STEPS = 64
MAX_STEP_NAME = 80
#: A "line" longer than this is not a step marker and is not worth buffering to find out.
MAX_LINE_BYTES = 64 * 1024


@dataclass(slots=True)
class VerifyStep:
    """One announced step of a gate, and what is known about it."""

    name: str
    index: int
    started_at: float
    ended_at: float | None = None
    lines: int = 0

    @property
    def duration_ms(self) -> float | None:
        if self.ended_at is None:
            return None
        return max(0.0, (self.ended_at - self.started_at) * 1000)


@dataclass(slots=True)
class VerifyProgress:
    """Live reading of a gate's output stream, fed the bytes as they arrive.

    Fed from `worktree_exec.bounded_output`, which is already reading the merged
    stdout/stderr pipe to EOF. Observing that stream costs one split per chunk and
    changes nothing about it: the bytes handed back, and the exit status reported beside
    them, are exactly what they were.
    """

    #: The step names a byte-identical run last completed, when one is on record.
    expected_steps: tuple[str, ...] = ()
    attempt: int = 1
    attempts: int = 1
    clock: Callable[[], float] = time.time
    steps: list[VerifyStep] = field(default_factory=list)
    lines: int = 0
    started_at: float = 0.0
    finished_at: float | None = None
    #: How many markers were seen, including any past `MAX_TRACKED_STEPS`.
    marker_count: int = 0
    _pending: bytearray = field(default_factory=bytearray)

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = self.clock()

    # -- the stream -----------------------------------------------------------

    def feed(self, chunk: bytes) -> None:
        """Consume one chunk of the gate's merged output.

        Never raises: this runs inside the pipe reader, and a progress reading that
        threw would take the gate's own output down with it.
        """
        try:
            self._consume(chunk)
        except Exception:  # noqa: BLE001 - progress is never worth a failed gate
            self._pending.clear()

    def _consume(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._pending.extend(chunk)
        # unsupervised-loop-ok: finite synchronous split of one already-received chunk;
        # every iteration removes bytes from the buffer and it ends when no newline is left.
        while True:
            cut = self._pending.find(b"\n")
            if cut < 0:
                if len(self._pending) > MAX_LINE_BYTES:
                    # Far too long to be a marker. Drop it rather than hold it: the
                    # buffer exists to reassemble a split line, not to accumulate one.
                    self._pending.clear()
                return
            line = bytes(self._pending[:cut])
            del self._pending[: cut + 1]
            self._line(line)

    def _line(self, raw: bytes) -> None:
        self.lines += 1
        if self.steps:
            self.steps[-1].lines += 1
        if len(raw) > 4096:
            return
        text = raw.decode("utf-8", "replace").strip().strip("\r")
        match = _STEP_MARKER.match(text)
        if match is None:
            return
        name = match.group(1).strip()
        # A captured name carrying its own equals signs is a longer rule, not a step.
        if not name or "=" in name:
            return
        self._start_step(name[:MAX_STEP_NAME])

    def _start_step(self, name: str) -> None:
        now = self.clock()
        if self.steps:
            self.steps[-1].ended_at = now
        self.marker_count += 1
        if len(self.steps) < MAX_TRACKED_STEPS:
            self.steps.append(VerifyStep(name, self.marker_count, now))

    def finish(self, *, now: float | None = None) -> None:
        """Close the run: flush a trailing partial line and end the open step."""
        if self._pending:
            self._line(bytes(self._pending))
            self._pending.clear()
        moment = self.clock() if now is None else now
        self.finished_at = moment
        if self.steps and self.steps[-1].ended_at is None:
            self.steps[-1].ended_at = moment

    # -- reads ----------------------------------------------------------------

    def observed_steps(self) -> tuple[str, ...]:
        """The step names this run announced, in order.

        Recorded as a plan only when the run *passed*, because a gate that stopped on a
        failure announced a prefix of its steps rather than all of them.
        """
        return tuple(step.name for step in self.steps)

    def snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        moment = self.finished_at if self.finished_at is not None else (
            self.clock() if now is None else now
        )
        current = self.steps[-1] if self.steps else None
        expected = list(self.expected_steps)
        beyond = bool(expected) and self.marker_count > len(expected)
        return {
            # 0 means "this gate announced no steps", which is a different statement
            # from "it is on its first step" and must not render as one.
            "step_index": self.marker_count,
            "step_name": current.name if current else "",
            # Absent whenever it would be a guess: no plan on record, or a run that has
            # already passed the plan it was measured against.
            "expected_step_count": None if (beyond or not expected) else len(expected),
            "expected_steps": [] if beyond else expected,
            "beyond_plan": beyond,
            "completed_steps": [
                {"name": step.name, "duration_ms": round(step.duration_ms or 0.0, 1)}
                for step in self.steps
                if step.ended_at is not None
            ],
            "lines": self.lines,
            "started_at": self.started_at,
            "elapsed_ms": round(max(0.0, (moment - self.started_at) * 1000), 1),
            "step_started_at": current.started_at if current else None,
            "step_elapsed_ms": (
                round(max(0.0, (moment - current.started_at) * 1000), 1) if current else None
            ),
            "attempt": self.attempt,
            "attempts": self.attempts,
            "finished": self.finished_at is not None,
        }


def sanitize_plan(steps: Iterable[Any]) -> list[str]:
    """A stored plan, read back defensively.

    The plan comes out of SQLite as JSON that a previous version of this code wrote, so
    it is validated on the way in rather than trusted: a malformed row must degrade to
    "no plan" - which renders as `step 3` with no total - and never to a wrong total.
    """
    kept: list[str] = []
    for item in steps:
        if not isinstance(item, str):
            return []
        name = item.strip()
        if not name:
            return []
        kept.append(name[:MAX_STEP_NAME])
        if len(kept) >= MAX_TRACKED_STEPS:
            break
    return kept


def plan_matches(observed: Sequence[str], plan: Sequence[str]) -> bool:
    """Whether an observed run walked the plan it was measured against.

    Used only to decide whether a *passing* run is worth re-recording; a mismatch
    replaces the plan rather than being reconciled with it.
    """
    return list(observed) == list(plan)
