from __future__ import annotations

from typing import Any, cast

from swe_mux.models import SessionRecord
from swe_mux.session import ScrollbackBuffer, Session


def _fake_session(max_bytes: int = 32) -> Any:
    fake = cast(Any, Session.__new__(Session))
    fake.scrollback = ScrollbackBuffer(max_bytes)
    fake.subscribers = set()
    fake.record = cast(
        Any, type("Record", (), {"snapshot": lambda self: {"state": "running"}})()
    )
    fake.revision = 0
    return fake


def test_scrollback_retains_exact_tail_across_chunk_boundaries() -> None:
    buffer = ScrollbackBuffer(5)
    buffer.append(b"abc")
    buffer.append(b"defg")
    assert buffer.bytes() == b"cdefg"


def test_scrollback_retains_tail_of_oversized_chunk() -> None:
    buffer = ScrollbackBuffer(4)
    buffer.append(b"old")
    buffer.append(b"012345")
    assert buffer.bytes() == b"2345"


def test_scrollback_zero_capacity_retains_nothing() -> None:
    buffer = ScrollbackBuffer(0)
    buffer.append(b"ignored")
    assert buffer.bytes() == b""


def test_replay_subscription_boundary_is_atomic() -> None:
    # The method is intentionally synchronous: no fanout task can run between its
    # scrollback snapshot and subscriber registration on the event loop.
    fake = _fake_session(10)
    fake.scrollback.append(b"past")

    snapshot, revision, replay, queue = Session.replay_and_subscribe(fake)
    assert snapshot == {"state": "running"}
    assert revision == 0
    assert replay == b"past"
    assert queue in fake.subscribers


def test_subscriber_overflow_coalesces_into_one_resync() -> None:
    fake = _fake_session(6)
    subscriber = Session.subscribe(fake, maxsize=2)
    for chunk in (b"12", b"34", b"56"):
        fake.scrollback.append(chunk)
        Session.publish_output(fake, chunk)

    assert subscriber.queue.qsize() == 1
    assert subscriber.queue.get_nowait() == {"type": "resync"}
    assert subscriber.dropped_bytes == 6
    assert subscriber.dropped_chunks == 3

    fake.scrollback.append(b"78")
    Session.publish_output(fake, b"78")
    result = Session.take_resync(fake, subscriber)
    dropped_bytes, dropped_chunks, replay, snapshot, revision, exit_frame = result
    assert (dropped_bytes, dropped_chunks) == (8, 4)
    assert replay == b"345678"
    assert snapshot == {"state": "running"}
    assert revision == 0
    assert exit_frame is None


def test_exit_survives_subscriber_overflow() -> None:
    fake = _fake_session()
    subscriber = Session.subscribe(fake, maxsize=1)
    fake.scrollback.append(b"one")
    Session.publish_output(fake, b"one")
    fake.scrollback.append(b"two")
    Session.publish_output(fake, b"two")
    Session.publish_exit(fake, "process_exit")

    assert subscriber.queue.get_nowait() == {"type": "resync"}
    *_, exit_frame = Session.take_resync(fake, subscriber)
    assert exit_frame is not None
    assert exit_frame["type"] == "exit"
    assert exit_frame["reason"] == "process_exit"


def test_hook_state_blocks_lower_priority_transcript_regression() -> None:
    fake = _fake_session()
    fake.record = SessionRecord(
        "mux", "agent", "default", "claude", "native", ".", "claude.exe", []
    )
    fake.state_source_priority = -1

    assert Session.transition(fake, "awaiting", "permission", source="hook")
    assert not Session.transition(fake, "working", None, source="transcript")
    assert fake.record.state == "awaiting"
    assert fake.record.state_detail == "permission"
    assert Session.transition(fake, "idle", None, source="hook")
    assert fake.record.state == "idle"
