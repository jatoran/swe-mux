"""The daemon's log files are written by one thread, in batches, never by the emitter.

A per-line write cost the emitting thread - the event loop, for every access line -
p50 61 ms per line beside four CPU-bound threads and 185 ms beside sixteen, because
each of its syscalls gave up the GIL (`DAEMON_LOAD_ISOLATION.md` item 6).
"""

from __future__ import annotations

import io
import logging
import threading
from pathlib import Path
from typing import Any

import pytest

from swe_mux import logsetup
from swe_mux.logsetup import BatchedRotatingFileHandler, BatchedStreamHandler, rotating_handler


def _logger(name: str, handler: logging.Handler) -> logging.Logger:
    logger = logging.getLogger(f"test_log_writer.{name}")
    logger.propagate = False
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    return logger


def test_emitting_never_writes_and_flush_waits_for_everything_before_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = rotating_handler(tmp_path / "a.log", max_bytes=1 << 20, backups=2)
    assert isinstance(handler, BatchedRotatingFileHandler)
    emitters: set[int] = set()
    writers: set[int] = set()
    real_submit = logsetup._WRITER.submit
    real_write = handler.write_batch

    def submit(target: Any, text: str) -> None:
        emitters.add(threading.get_ident())
        real_submit(target, text)

    def write(texts: list[str]) -> None:
        writers.add(threading.get_ident())
        real_write(texts)

    monkeypatch.setattr(logsetup._WRITER, "submit", submit)
    monkeypatch.setattr(handler, "write_batch", write)
    logger = _logger("order", handler)
    try:
        for index in range(500):
            logger.info("line %d", index)
        handler.flush()
        lines = (tmp_path / "a.log").read_text(encoding="utf-8").splitlines()
    finally:
        handler.close()
    assert [line.rsplit(" ", 1)[-1] for line in lines] == [str(i) for i in range(500)]
    assert emitters == {threading.get_ident()}
    assert writers and threading.get_ident() not in writers, "the emitter never writes"


def test_files_rotate_by_size_between_lines(tmp_path: Path) -> None:
    handler = rotating_handler(tmp_path / "r.log", max_bytes=2000, backups=3, structured=False)
    logger = _logger("rotate", handler)
    try:
        for index in range(200):
            logger.info("%04d %s", index, "x" * 40)
        handler.flush()
    finally:
        handler.close()
    files = sorted(tmp_path.glob("r.log*"))
    assert len(files) == 4, "the live file and three backups"
    for path in files:
        assert path.stat().st_size < 2000 + 200, path
        for line in path.read_text(encoding="utf-8").splitlines():
            assert line.endswith("x" * 40), "rotation never splits a line"
    newest = (tmp_path / "r.log").read_text(encoding="utf-8").splitlines()
    assert "0199" in newest[-1]


def test_a_rotation_that_loses_the_race_keeps_appending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    handler = rotating_handler(tmp_path / "busy.log", max_bytes=500, backups=2, structured=False)
    assert isinstance(handler, BatchedRotatingFileHandler)
    attempts: list[int] = []

    def refused() -> None:
        attempts.append(1)
        if handler.stream is not None:
            handler.stream.close()
            handler.stream = None  # type: ignore[assignment]
        raise PermissionError(32, "the file is held by another process")

    monkeypatch.setattr(handler, "doRollover", refused)
    logger = _logger("refused", handler)
    try:
        for index in range(100):
            logger.info("%03d %s", index, "y" * 30)
        handler.flush()
    finally:
        handler.close()
    lines = (tmp_path / "busy.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 100, "no line is lost to a refused rotation"
    assert attempts, "the rotation was attempted"
    assert len(attempts) < 100, "and not once per line"


def test_the_console_is_written_off_the_emitter_too() -> None:
    stream = io.StringIO()
    handler = BatchedStreamHandler(stream)
    logger = _logger("console", handler)
    logger.info("hello")
    logger.info("world")
    handler.flush()
    assert stream.getvalue().splitlines() == ["hello", "world"]


def test_closing_writes_what_was_queued(tmp_path: Path) -> None:
    handler = rotating_handler(tmp_path / "c.log", max_bytes=1 << 20, backups=1)
    logger = _logger("close", handler)
    for index in range(50):
        logger.info("queued %d", index)
    handler.close()
    assert len((tmp_path / "c.log").read_text(encoding="utf-8").splitlines()) == 50
    logger.info("after close")  # dropped, never raised
    logsetup._WRITER.flush()
