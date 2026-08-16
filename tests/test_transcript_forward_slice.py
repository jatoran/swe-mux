"""The forward transcript window, against real files in every parsed dialect.

`TranscriptSliceService.build` returns the NEWEST window and trims from the
front. That is right for "summarise what just happened" and silently lossy for
any caller that advances a cursor to the end of what it read: everything
trimmed sits *before* the cursor and is never offered again. The scan timeline
was that caller. These tests pin the replacement's two guarantees - oldest
first, and an honest count of what was left behind - on files each harness
actually writes, because the timestamp shapes differ per dialect and a
millisecond stamp compared against a second-denominated cursor silently
matches everything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swe_mux.automation import TranscriptSliceService, slice_timestamp, tool_input_digest


def write_claude(path: Path, count: int, *, start: int = 0) -> None:
    lines = []
    for index in range(start, start + count):
        lines.append(
            json.dumps(
                {
                    "type": "assistant" if index % 2 else "user",
                    "timestamp": f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}.000Z",
                    "message": {"content": [{"type": "text", "text": f"message {index}"}]},
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ts_of(message: dict) -> float:
    stamp = slice_timestamp(message.get("ts"))
    assert stamp is not None
    return stamp


@pytest.mark.asyncio
async def test_forward_window_walks_oldest_first_and_covers_everything(tmp_path: Path) -> None:
    path = tmp_path / "claude.jsonl"
    write_claude(path, 25)
    service = TranscriptSliceService()

    seen: list[str] = []
    cursor = 0.0
    for _ in range(10):
        window = await service.build_forward(
            path, "claude", since_ts=cursor, max_messages=6, max_bytes=1_000_000
        )
        if not window.messages:
            break
        seen.extend(
            block["text"]
            for message in window.messages
            for block in message["content"]
            if block.get("type") == "text"
        )
        cursor = max(ts_of(message) for message in window.messages)

    assert seen == [f"message {index}" for index in range(25)]


@pytest.mark.asyncio
async def test_forward_window_reports_the_tail_it_did_not_reach(tmp_path: Path) -> None:
    path = tmp_path / "claude.jsonl"
    write_claude(path, 25)
    service = TranscriptSliceService()

    window = await service.build_forward(
        path, "claude", since_ts=0.0, max_messages=6, max_bytes=1_000_000
    )
    assert len(window.messages) == 6
    assert window.remaining == 19
    assert window.truncated is True
    assert window.remaining_from_ts == pytest.approx(ts_of(dict(window.messages[-1])) + 1)

    tail = await service.build_forward(
        path, "claude", since_ts=1e12, max_messages=6, max_bytes=1_000_000
    )
    assert tail.messages == ()
    assert tail.remaining == 0


@pytest.mark.asyncio
async def test_a_byte_bound_trims_the_newest_end_not_the_oldest(tmp_path: Path) -> None:
    """The whole point. Trimming the front is what created the hole."""
    path = tmp_path / "claude.jsonl"
    write_claude(path, 12)
    service = TranscriptSliceService()

    window = await service.build_forward(
        path, "claude", since_ts=0.0, max_messages=12, max_bytes=400
    )
    assert window.bytes <= 400
    assert len(window.messages) < 12
    texts = [
        block["text"]
        for message in window.messages
        for block in message["content"]
        if block.get("type") == "text"
    ]
    assert texts[0] == "message 0", "the oldest unscanned message must survive the bound"
    assert window.remaining == 12 - len(window.messages)


@pytest.mark.asyncio
async def test_the_read_budget_no_longer_hides_the_start_of_a_long_transcript(
    tmp_path: Path,
) -> None:
    """`build` reads only the trailing `max_bytes` of the file before filtering.

    That made the window size and the read budget the same number, so a scan
    after one large tool call saw that call and nothing before it. The forward
    reader takes them separately.
    """
    path = tmp_path / "claude.jsonl"
    write_claude(path, 400)
    service = TranscriptSliceService()

    bounded = await service.build(
        path, "claude", "since_event", max_messages=5, max_bytes=2_000, since_ts=0.0
    )
    forward = await service.build_forward(
        path, "claude", since_ts=0.0, max_messages=5, max_bytes=2_000, read_bytes=None
    )
    first_bounded = [
        block["text"]
        for message in bounded.messages
        for block in message["content"]
        if block.get("type") == "text"
    ][0]
    first_forward = [
        block["text"]
        for message in forward.messages
        for block in message["content"]
        if block.get("type") == "text"
    ][0]
    assert first_bounded.startswith("message 39")
    assert first_forward == "message 0"
    assert forward.remaining == 395


@pytest.mark.asyncio
async def test_millisecond_timestamps_are_not_compared_against_seconds(
    tmp_path: Path,
) -> None:
    """opencode writes epoch milliseconds; a raw compare passes every message."""
    assert slice_timestamp(1_786_848_932_000) == pytest.approx(1_786_848_932.0)
    assert slice_timestamp(1_786_848_932.0) == pytest.approx(1_786_848_932.0)
    assert slice_timestamp("2026-01-01T00:00:00Z") == pytest.approx(1_767_225_600.0)
    assert slice_timestamp("not a timestamp") is None
    assert slice_timestamp(True) is None


def test_tool_input_digest_is_bounded_and_total() -> None:
    assert tool_input_digest({"file_path": "a.py"}, 200) == '{"file_path":"a.py"}'
    assert tool_input_digest("x" * 500, 200) == "x" * 200 + "…"
    assert tool_input_digest(None, 200) == ""
    assert tool_input_digest({"a": 1}, 0) == ""
    assert tool_input_digest({"a": object()}, 200).startswith('{"a":"<object')


def test_render_carries_bounded_tool_arguments(tmp_path: Path) -> None:
    window = TranscriptSliceService.from_prompt("hello")
    assert window.render() == "user: hello"

    from swe_mux.automation import TranscriptSlice

    slice_ = TranscriptSlice(
        "test",
        (
            {
                "role": "assistant",
                "ts": 1.0,
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "src/a.py"}}
                ],
            },
        ),
        1,
        1,
        False,
        "hash",
    )
    assert slice_.render() == "assistant: [tool Read]"
    assert slice_.render(tool_input_chars=200) == (
        'assistant: [tool Read {"file_path":"src/a.py"}]'
    )
