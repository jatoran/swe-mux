"""DAEMON_LOAD_ISOLATION item 25 - one writer per PTY, and conditional delivery.

Every PTY write and resize goes through the session's terminal core, so writes
from a browser, the queue, voice and the rest have one order, and a delivery's
write can carry the readiness verdict it was decided on. Once keystrokes arrive in
another process (the terminal gateway), a keystroke can land between the verdict
and the write; these tests stand in for that by landing one there.
"""

from __future__ import annotations

import ast
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux.meta_hooks import MetaHookEngine
from swe_mux.models import MuxEvent, SessionRecord
from swe_mux.operator_input import record_operator_input
from swe_mux.prompt_queue import PromptQueueService, PromptQueueStore, QueueError, delivery_payload
from swe_mux.session import Session
from swe_mux.terminal_core import WriteCondition, WriteRefused

SOURCE = Path(__file__).parents[1] / "src" / "swe_mux"

#: The modules that may call `write` or `resize` on a PTY: the one writer, and the
#: PTY implementations underneath it.
PTY_WRITERS = frozenset(
    {
        "terminal_core/core.py",  # TerminalCore.write, apply_geometry, the repaint pulse
        "pty_host.py",  # the in-process PTY host, including its own graceful close
        "pty_backend_windows.py",
        "pty_backend_posix.py",
        "supervisor.py",  # the process that owns supervised PTYs
        "supervisor_client.py",  # RemotePtyHost, the daemon's proxy for one
    }
)


def _pty_calls(path: Path) -> list[int]:
    """Lines calling `.write(` or `.resize(` on something named `pty`."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"write", "resize"}
        ):
            continue
        receiver = node.func.value
        if isinstance(receiver, ast.Attribute):
            named = receiver.attr
        else:
            named = getattr(receiver, "id", "")
        if named in {"pty", "_pty"}:
            lines.append(node.lineno)
    return lines


def test_only_the_terminal_core_writes_to_a_pty() -> None:
    offenders = [
        f"{path.relative_to(SOURCE).as_posix()}:{line}"
        for path in sorted(SOURCE.rglob("*.py"))
        if path.relative_to(SOURCE).as_posix() not in PTY_WRITERS
        for line in _pty_calls(path)
    ]
    assert offenders == [], (
        "a PTY written or resized outside the terminal core: write through "
        "`TerminalCore.write`/`operator_input.record_operator_input` instead"
    )
    # The sweep can see what it is looking for: the core's own writes are found.
    assert len(_pty_calls(SOURCE / "terminal_core" / "core.py")) >= 3


class _Pty:
    def __init__(self) -> None:
        self.writes: list[str | bytes] = []

    def write(self, data: str | bytes) -> None:
        self.writes.append(data)

    def resize(self, cols: int, rows: int) -> None:
        pass

    def isalive(self) -> bool:
        return True


def _session(backend: str = "claude") -> tuple[Session, _Pty]:
    pty = _Pty()
    record = SessionRecord(
        "s1", f"{backend}-s1", "default", backend, "s1", ".", backend, [], state="idle"
    )
    record.agent_run_id = "run-s1"
    session = Session(record, cast(Any, pty), cast(Any, SimpleNamespace()), 1024, "secret")
    return session, pty


def test_a_condition_that_still_holds_writes() -> None:
    session, pty = _session()
    core = session.terminal
    core.write("hello", condition=WriteCondition(input_revision=0, operator_quiet_seconds=2.0))
    assert pty.writes == ["hello"]


@pytest.mark.parametrize(
    ("arrange", "reason"),
    [
        (lambda core: setattr(core, "input_revision", 1), "input_revision_moved"),
        (
            lambda core: setattr(core, "last_input_event_ts", time.monotonic()),
            "operator_recently_typed",
        ),
        (
            lambda core: setattr(core, "pending_submit", object()),
            "unsubmitted_delivery_in_composer",
        ),
    ],
)
def test_a_condition_that_no_longer_holds_writes_nothing(arrange: Any, reason: str) -> None:
    session, pty = _session()
    core = session.terminal
    arrange(core)
    with pytest.raises(WriteRefused) as refused:
        core.write(
            "paste",
            condition=WriteCondition(
                input_revision=0, operator_quiet_seconds=2.0, no_pending_submit=True
            ),
        )
    assert refused.value.reason == reason
    assert pty.writes == []


def test_operator_input_is_accounted_and_input_crosses_as_an_age() -> None:
    session, pty = _session()
    assert session.terminal.last_input_age() is None
    record_operator_input(_Events(), session, "x", source="http")
    assert pty.writes == ["x"]
    assert session.input_revision == 1
    age = session.terminal.last_input_age()
    assert age is not None and 0 <= age < 5


async def test_an_automation_write_goes_through_the_core_and_is_not_operator_input(
    tmp_path: Path,
) -> None:
    """`delivery-readiness.md`: an automation's `write_pty` is not operator input.

    It takes the one writer like everything else, and leaves the operator-quiet
    clock and the input revision where they were - counting it would let an
    automation's own write hold back, or wave through, a delivery.
    """
    session, pty = _session()
    sessions = SimpleNamespace(
        sessions={"s1": session}, resolve=lambda target: {"s1": session}[target]
    )
    engine = MetaHookEngine(tmp_path / "hooks.toml", cast(Any, _Events()), cast(Any, sessions))
    event = MuxEvent(ts=time.time(), session_id="s1", source="test", type="turn_ended", payload={})

    await engine._act({"kind": "write_pty", "text": "ran {type}"}, event)

    assert pty.writes == ["ran turn_ended"]
    assert session.input_revision == 0
    assert session.terminal.last_input_age() is None


class _Events:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    def emit_background(self, event_type: str, **payload: Any) -> None:
        self.emitted.append((event_type, payload))

    def subscribe(self, *, name: str = "anonymous") -> Any:
        raise AssertionError("not started")

    def unsubscribe(self, queue: Any) -> None:
        pass


class _RacedReadiness:
    """Judges safe, then lands a keystroke in the target before the write.

    ``races`` is how many verdicts are followed by one. ``after`` is the verdict
    once the races are spent: what readiness would say about the input it now sees.
    """

    def __init__(self, session: Session, races: int, after: str = "safe") -> None:
        self.session = session
        self.races = races
        self.after = after
        self.verdicts = 0

    def screen_state(self, session: Any) -> str:
        return "unknown"

    def evaluate(self, session: Any, **_options: Any) -> dict[str, Any]:
        self.verdicts += 1
        raced = self.verdicts <= self.races
        state = "safe" if raced or self.after == "safe" else "blocked"
        verdict = {
            "delivery_state": state,
            "reasons": (
                ["all_required_evidence_positive"]
                if state == "safe"
                else ["terminal_input_after_completion"]
            ),
            "interject_state": "blocked",
            "interject_reasons": ["not_a_running_turn"],
            "evidence": {"input_revision": self.session.input_revision},
        }
        if raced:
            # A keystroke from another process lands after the verdict: the
            # revision moves, and long enough ago that only the revision says so.
            self.session.terminal.input_revision += 1
            self.session.terminal.last_input_event_ts = time.monotonic() - 10.0
        return verdict


async def _queue(
    tmp_path: Path, session: Session, readiness: Any
) -> tuple[PromptQueueService, _Events]:
    events = _Events()

    def write(target: Any, data: str, *, condition: WriteCondition | None = None) -> None:
        record_operator_input(
            events, target, data, source="queue", input_owner=False, condition=condition
        )

    service = PromptQueueService(
        PromptQueueStore(tmp_path / "queue.db"),
        SimpleNamespace(sessions={session.record.id: session}),
        events,
        readiness,
        write,
        submit_delay=0.0,
        submit_confirm_seconds=0.0,
        submit_retry_delays=(),
    )
    return service, events


async def test_a_keystroke_after_the_verdict_refuses_the_paste_and_the_next_verdict_blocks(
    tmp_path: Path,
) -> None:
    session, pty = _session()
    readiness = _RacedReadiness(session, races=1, after="blocked")
    service, _events = await _queue(tmp_path, session, readiness)
    message = await service.enqueue(target_session_id="s1", body="hello there")

    with pytest.raises(QueueError) as refused:
        await service.send_next(message["id"], revision=1)

    assert refused.value.code == "delivery_not_safe"
    assert readiness.verdicts == 2, "the refusal sent the queue back to judge again"
    assert pty.writes == [], "nothing was pasted over the keystroke"
    view = await service.target_view("s1")
    assert [item["state"] for item in view["messages"]] == ["blocked"]


async def test_a_refused_write_that_is_judged_safe_again_is_delivered_once(
    tmp_path: Path,
) -> None:
    session, pty = _session()
    readiness = _RacedReadiness(session, races=1, after="safe")
    service, _events = await _queue(tmp_path, session, readiness)
    message = await service.enqueue(target_session_id="s1", body="hello there")

    result = await service.send_next(message["id"], revision=1)

    assert result["status"] == "sent"
    assert readiness.verdicts == 2
    assert pty.writes.count(delivery_payload("hello there", "claude")) == 1


async def test_input_that_keeps_racing_the_write_is_refused_after_the_bounded_retries(
    tmp_path: Path,
) -> None:
    session, pty = _session()
    readiness = _RacedReadiness(session, races=10)
    service, _events = await _queue(tmp_path, session, readiness)
    message = await service.enqueue(target_session_id="s1", body="hello there")

    with pytest.raises(QueueError) as refused:
        await service.send_next(message["id"], revision=1)

    assert refused.value.code == "delivery_not_safe"
    assert refused.value.payload["reasons"] == ["input_revision_moved"]
    assert readiness.verdicts == 3
    assert pty.writes == []


async def test_a_human_override_carries_no_condition(tmp_path: Path) -> None:
    session, pty = _session()

    class _Blocked(_RacedReadiness):
        def evaluate(self, session: Any, **options: Any) -> dict[str, Any]:
            verdict = super().evaluate(session, **options)
            verdict["delivery_state"] = "blocked"
            verdict["reasons"] = ["operator_recently_typed"]
            return verdict

    readiness = _Blocked(session, races=1)
    service, _events = await _queue(tmp_path, session, readiness)
    message = await service.enqueue(target_session_id="s1", body="hello there")

    result = await service.send_next(message["id"], revision=1, confirm=True)

    assert result["status"] == "sent" and result["confirmed"] is True
    assert readiness.verdicts == 1
    assert delivery_payload("hello there", "claude") in pty.writes
