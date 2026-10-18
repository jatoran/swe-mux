"""Rolling file logs, crash tracebacks, correlation, and runtime log-level control.

The console redirect held by whoever spawned the daemon (``desktop-daemon.log``
/ ``daemon-relaunch.log``) is only a crash catcher for early startup and native
stderr; structured logging goes to rotating files in the data dir so a noisy
day can never grow an unbounded log:

- ``daemon.log`` — the application log (root logger), 10 MB x 5.
- ``access.log`` — the aiohttp request log, isolated (``propagate=False``) so
  request spam cannot drown signal in ``daemon.log`` or the console.
- ``crash.log`` — faulthandler output on hard native crashes (segfault /
  access violation). External kills (a Job closing, taskkill) cannot be
  observed in-process at all; those are covered by the lifecycle ledger.

The root logger level is the runtime debug toggle: ``log_level`` in config is
the startup default, ``POST /api/debug/log-level`` flips it on a live daemon.

Two things make a line reconstructable afterwards rather than merely readable:

- **The sink serializes ``extra``.** ``LOG_FORMAT`` alone drops every keyword a
  call site passes through ``extra=``, which is where the correlation data the
  codebase already writes was going: ``git_monitor`` recording the root and the
  git exit code, ``observation`` recording the session and the elapsed seconds,
  ``usage`` recording the source count. `StructuredFormatter` appends them as
  ``key=value`` pairs after the message, which is the shape the rest of the
  daemon already logs in by hand (``git_mutation_started operation_id=… cwd=…``)
  — so one convention covers both, and `daemon.log` stays greppable rather than
  becoming a file of JSON objects a human reads through `jq`.
- **Every line carries the request that caused it.** `request_id_var` is a
  contextvar bound by the HTTP correlation middleware; `CorrelationFilter`, on
  each structured handler, copies it onto every record that reaches the sink.
  Because a contextvar is inherited by `asyncio.create_task` and
  `asyncio.to_thread`, the background work a request starts stays correlated
  with it after the response has been written, which is exactly the span an
  incident covers.
"""

from __future__ import annotations

import faulthandler
import json
import logging
import logging.handlers
import queue
import re
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import IO, Protocol, TextIO
from uuid import uuid4

DAEMON_LOG_NAME = "daemon.log"
ACCESS_LOG_NAME = "access.log"
CRASH_LOG_NAME = "crash.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
DAEMON_LOG_BYTES = 10 * 1024 * 1024
DAEMON_LOG_BACKUPS = 5
ACCESS_LOG_BYTES = 10 * 1024 * 1024
#: Sized so the request log outlives the day after an incident. Measured on the
#: operator's host 2026-10-16/17: about 3.3 MB an hour of ordinary fleet traffic
#: (hook POSTs, OTLP posts and the window's polls at ~260 bytes a line) and 6.4 MB an
#: hour inside the 2026-10-16 incident. Three backups held about 22 hours, so the
#: requests behind an evening's stalls were gone by the next afternoon; twenty hold
#: about 64 hours of ordinary traffic and 33 at the incident rate, in at most 210 MB.
ACCESS_LOG_BACKUPS = 20
#: The worker process's own log (`worker.py`): one process at a time writes it, and
#: each start says which pid it is, so a restart is readable as a boundary.
WORKER_LOG_NAME = "worker.log"
WORKER_LOG_BYTES = 10 * 1024 * 1024
WORKER_LOG_BACKUPS = 3

#: A single rendered `extra` value is truncated here. Bounded because one
#: oversized field must not be able to push a whole rotation's worth of real
#: lines out of `daemon.log`; call sites that carry command output already clamp
#: harder than this (`git_monitor` uses 200 bytes).
MAX_EXTRA_VALUE_CHARS = 500

#: Correlation id for the HTTP request (or other bounded operation) in flight.
#: Empty outside one, which is the honest answer for daemon startup, a
#: background loop's own work, and anything an external event triggered.
request_id_var: ContextVar[str] = ContextVar("swe_mux_request_id", default="")

#: What an inbound `X-Request-ID` may look like before the daemon adopts it
#: instead of minting its own. A client-supplied id ends up in a log file, so it
#: is length-bounded and restricted to characters that cannot forge a field
#: boundary or a new line.
_REQUEST_ID_PATTERN = re.compile(r"\A[A-Za-z0-9._:-]{1,64}\Z")

# Keeps the faulthandler target open for the process lifetime.
_crash_log_file: IO[str] | None = None

# Everything the stdlib puts on a LogRecord itself. Anything else in a record's
# `__dict__` came from a call site's `extra=`, which is precisely what the sink
# is here to keep. Derived from a real record rather than transcribed, so a
# future Python that adds an attribute does not start logging it as a field.
_RESERVED_RECORD_ATTRS = frozenset(
    vars(
        logging.LogRecord(
            name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None
        )
    )
) | {"message", "asctime", "taskName"}


def new_request_id() -> str:
    """A fresh correlation id: short enough to read in a log line, wide enough."""
    return uuid4().hex[:16]


def valid_request_id(value: str) -> bool:
    return bool(_REQUEST_ID_PATTERN.match(value))


def current_request_id() -> str:
    return request_id_var.get()


@contextmanager
def bound_request_id(request_id: str) -> Iterator[str]:
    """Bind a correlation id for the duration of the block.

    Used by the HTTP correlation middleware, and available to any service that
    performs work on behalf of a request it did not itself receive - a queued
    job replaying a caller's operation id, say - so that work's log lines join
    the same trail rather than starting an anonymous one.
    """
    token = request_id_var.set(request_id)
    try:
        yield request_id
    finally:
        request_id_var.reset(token)


class CorrelationFilter(logging.Filter):
    """Stamp the in-flight request id onto every record that passes through.

    Installed on the *handlers*, not on the root logger. `Logger.handle`
    consults only the filters of the logger the call was made on, so a filter on
    root would never see a record from `swe_mux.session` - it would look
    installed and stamp almost nothing. `Handler.handle` consults its own
    filters for every record that reaches it, including every propagated one,
    which is the coverage this needs.

    A filter rather than a formatter concern, so `record.request_id` is a real
    attribute any handler can read. A record created outside a request carries
    no field at all, rather than an empty one that reads as a request whose id
    went missing.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = request_id_var.get()
        if request_id and not hasattr(record, "request_id"):
            record.request_id = request_id
        return True


def _render_value(value: object) -> str:
    """One `extra` value as a single-line, unambiguous `key=value` right-hand side."""
    if isinstance(value, str):
        text = value
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif value is None:
        text = "null"
    elif isinstance(value, (int, float)):
        text = repr(value)
    else:
        try:
            text = json.dumps(value, default=str, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(value)
    if len(text) > MAX_EXTRA_VALUE_CHARS:
        text = text[:MAX_EXTRA_VALUE_CHARS] + "..."
    # Quoting is decided by the rendered text, not by its source type: a value
    # holding a space, a quote, an `=` or a newline would otherwise be read back
    # as two fields or as two lines, and a log line that cannot be parsed back is
    # not structured.
    if text == "" or any(character in text for character in ' "\\=\n\r\t'):
        return json.dumps(text)
    return text


def structured_fields(record: logging.LogRecord) -> dict[str, object]:
    """The call-site `extra` fields on a record, in the order they were passed."""
    return {
        name: value
        for name, value in record.__dict__.items()
        if name not in _RESERVED_RECORD_ATTRS and not name.startswith("_")
    }


def format_fields(fields: dict[str, object]) -> str:
    """Render fields as `key=value` pairs, correlation id first."""
    ordered: list[tuple[str, object]] = []
    if "request_id" in fields:
        ordered.append(("request_id", fields["request_id"]))
    ordered.extend((name, value) for name, value in fields.items() if name != "request_id")
    return " ".join(f"{name}={_render_value(value)}" for name, value in ordered)


class StructuredFormatter(logging.Formatter):
    """`LOG_FORMAT`, then the record's `extra` fields as `key=value` pairs.

    Appended after the message rather than replacing it with JSON: the daemon
    already logs `event_name key=value key=value` messages by hand in dozens of
    places, so this makes the two halves one convention instead of two, and an
    operator keeps grepping `daemon.log` for the same shapes. Exception text
    stays last, where a reader expects a traceback to be.
    """

    def format(self, record: logging.LogRecord) -> str:
        fields = structured_fields(record)
        if not fields:
            return super().format(record)
        # `super().format` appends the traceback, so the fields have to be
        # spliced into the message before it runs - otherwise they would land
        # after the traceback, pages away from the line they describe.
        rendered = format_fields(fields)
        original = record.msg
        original_args = record.args
        try:
            record.msg = f"{record.getMessage()} {rendered}"
            record.args = ()
            return super().format(record)
        finally:
            record.msg = original
            record.args = original_args


#: Records the writer takes per pass; a pass is one write and one flush per file.
LOG_WRITE_BATCH = 1024
#: How long `flush()` waits for the writer to catch up before giving up.
LOG_FLUSH_TIMEOUT_SECONDS = 5.0


class _LogWriter:
    """The one thread that writes every daemon log, in batches.

    A log line written where it is emitted costs that thread its syscalls: the
    rotating handler stats the file twice and writes and flushes once per record,
    and each syscall gives up the GIL and has to win it back. Measured with four
    CPU-bound Python threads beside it, that put a single line at a p50 of 61 ms
    on the emitting thread (p99 296 ms), and with sixteen at 185 ms (p99 1.06 s);
    on the event loop, every access line was one of those. The emitting thread now
    formats the record (CPU only, and where the correlation id is still bound) and
    queues the text; this thread writes whatever has queued with one write and one
    flush per file, so its GIL needs are per batch rather than per line and it
    keeps up under the same contention a per-line writer fell seconds behind in.

    What a hard kill can lose is what is queued and unwritten at that moment,
    which is one pass. `flush()` waits for everything queued before it, which is
    what `logging.shutdown` and an orderly exit call.
    """

    def __init__(self) -> None:
        self._queue: queue.SimpleQueue[tuple[_BatchedWrites, str] | threading.Event] = (
            queue.SimpleQueue()
        )
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()

    def submit(self, target: _BatchedWrites, text: str) -> None:
        if self._thread is None:
            self._start()
        self._queue.put((target, text))

    def flush(self, timeout: float = LOG_FLUSH_TIMEOUT_SECONDS) -> bool:
        """Wait until everything queued before this call is written."""
        if self._thread is None or threading.current_thread() is self._thread:
            return True
        written = threading.Event()
        self._queue.put(written)
        return written.wait(timeout)

    def _start(self) -> None:
        with self._start_lock:
            if self._thread is None:
                thread = threading.Thread(target=self._run, name="log-writer", daemon=True)
                thread.start()
                self._thread = thread

    def _run(self) -> None:
        # unsupervised-loop-ok: the log writer's own OS thread, not an asyncio loop;
        # `_write` contains every failure, so nothing can end it but the process.
        while True:
            batch = [self._queue.get()]
            while len(batch) < LOG_WRITE_BATCH:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            pending: dict[_BatchedWrites, list[str]] = {}
            for item in batch:
                if isinstance(item, threading.Event):
                    # Everything queued before the barrier is written before it opens.
                    self._write(pending)
                    pending = {}
                    item.set()
                    continue
                target, text = item
                pending.setdefault(target, []).append(text)
            self._write(pending)

    @staticmethod
    def _write(pending: dict[_BatchedWrites, list[str]]) -> None:
        for target, texts in pending.items():
            try:
                target.write_batch(texts)
            except Exception as exc:  # noqa: BLE001 - the writer must outlive any one file
                _report_log_failure(f"log write to {target.describe()} failed: {exc!r}")


def _report_log_failure(message: str) -> None:
    stream = sys.__stderr__
    if stream is None:
        return
    try:
        stream.write(f"swe-mux logging: {message}\n")
        stream.flush()
    except (OSError, ValueError):
        pass


_WRITER = _LogWriter()


class _BatchedWrites(Protocol):
    def write_batch(self, texts: list[str]) -> None: ...

    def describe(self) -> str: ...


class BatchedRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """`RotatingFileHandler` whose writes happen on the log writer thread.

    ``emit`` formats and queues; ``write_batch`` writes a pass's lines, rotating
    by size between them. A rotation that loses a race for the file (a predecessor
    daemon still holding it during a session-preserving restart) keeps writing to
    the current file and is retried at the next threshold, rather than dropping
    the lines the way a failed stdlib rollover does.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._stream_lock = threading.Lock()
        self._closed = False

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
        except Exception:  # noqa: BLE001 - the stdlib contract for a failing format
            self.handleError(record)
            return
        _WRITER.submit(self, text)

    def describe(self) -> str:
        return self.baseFilename

    def write_batch(self, texts: list[str]) -> None:
        with self._stream_lock:
            if self._closed:
                return
            if self.stream is None:
                self.stream = self._open()
            position = self.stream.tell() if self.maxBytes > 0 else 0
            # One failed rotation per pass: retrying it for every remaining line
            # would only repeat the same refusal.
            may_rotate = self.maxBytes > 0
            chunk: list[str] = []
            for text in texts:
                line = text + self.terminator
                if may_rotate and position > 0 and position + len(line) >= self.maxBytes:
                    if chunk:
                        self.stream.write("".join(chunk))
                        chunk = []
                    may_rotate = self._rotate()
                    position = self.stream.tell()
                chunk.append(line)
                position += len(line)
            if chunk:
                self.stream.write("".join(chunk))
            self.stream.flush()

    def _rotate(self) -> bool:
        rotated = True
        try:
            self.doRollover()
        except OSError as exc:
            rotated = False
            _report_log_failure(f"rotating {self.baseFilename} failed, still appending: {exc!r}")
        if self.stream is None:
            self.stream = self._open()
        return rotated

    def flush(self) -> None:
        _WRITER.flush()

    def close(self) -> None:
        _WRITER.flush()
        with self._stream_lock:
            self._closed = True
            super().close()


class BatchedStreamHandler(logging.StreamHandler[TextIO]):
    """`StreamHandler` (the console) whose writes happen on the log writer thread."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
        except Exception:  # noqa: BLE001 - the stdlib contract for a failing format
            self.handleError(record)
            return
        _WRITER.submit(self, text)

    def describe(self) -> str:
        return getattr(self.stream, "name", "console")

    def write_batch(self, texts: list[str]) -> None:
        stream = self.stream
        if stream is None:
            return
        stream.write("".join(text + self.terminator for text in texts))
        stream.flush()

    def flush(self) -> None:
        _WRITER.flush()


def rotating_handler(
    path: Path, *, max_bytes: int, backups: int, structured: bool = True
) -> logging.Handler:
    """A size-rotated file handler that degrades gracefully and never blocks its caller.

    Writes happen on the log writer thread (`_LogWriter`), so logging costs the
    emitting thread - the event loop, for every access line - a format and a queue
    put, never a syscall. ``delay=True`` defers opening until the first write, and
    a rotation rename that loses a race keeps appending to the current file.

    ``structured=False`` is for ``access.log``: aiohttp's `AccessLogger` passes
    its whole atom table through ``extra=``, so serializing extras there would
    repeat every access line's contents twice over. The request id reaches that
    file through the access format string instead.
    """
    handler = BatchedRotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8", delay=True
    )
    formatter = StructuredFormatter(LOG_FORMAT) if structured else logging.Formatter(LOG_FORMAT)
    handler.setFormatter(formatter)
    if structured:
        handler.addFilter(CorrelationFilter())
    return handler


def normalize_level(level: str) -> str:
    candidate = str(level).strip().upper()
    if candidate not in LOG_LEVELS:
        raise ValueError(f"log level must be one of {', '.join(LOG_LEVELS)}")
    return candidate


#: Third-party loggers that narrate routine work at INFO, and the level each is held at.
#: `watchfiles` writes one "N changes detected" line per change batch from every
#: watch: the Project file watches, and since 2026-10-12 one recursive watch per
#: followed checkout. The first three minutes after that deploy wrote ~170 of them,
#: which at a working fleet's rate rotates `daemon.log` for nothing - the owners'
#: own counters (`git_monitor.watch` on `/api/diagnostics/background`, the Project
#: watch diagnostics) carry every fact those lines did. Pinned per logger rather than
#: via the root level, so a root at DEBUG for a diagnosis does not bring them back.
QUIET_LIBRARY_LOGGERS: dict[str, int] = {"watchfiles.main": logging.WARNING}


def quiet_library_loggers() -> None:
    for name, level in QUIET_LIBRARY_LOGGERS.items():
        logging.getLogger(name).setLevel(level)


def setup_daemon_logging(data_dir: Path, level: str = "INFO") -> None:
    """Configure the daemon process's logging: console + rotating files."""
    data_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(normalize_level(level))
    quiet_library_loggers()
    console = BatchedStreamHandler()
    console.setFormatter(StructuredFormatter(LOG_FORMAT))
    console.addFilter(CorrelationFilter())
    root.addHandler(console)
    root.addHandler(
        rotating_handler(
            data_dir / DAEMON_LOG_NAME,
            max_bytes=DAEMON_LOG_BYTES,
            backups=DAEMON_LOG_BACKUPS,
        )
    )
    access = logging.getLogger("aiohttp.access")
    access.propagate = False
    access.setLevel(logging.INFO)
    access.addHandler(
        rotating_handler(
            data_dir / ACCESS_LOG_NAME,
            max_bytes=ACCESS_LOG_BYTES,
            backups=ACCESS_LOG_BACKUPS,
            structured=False,
        )
    )


def setup_worker_logging(data_dir: Path, level: str = "INFO") -> None:
    """Configure the worker process's logging: its own rotating `worker.log`.

    No console handler: the daemon points the worker's stderr at
    `worker-console.log`, which is for what the interpreter says before this runs
    and for native crashes, not for every line.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(normalize_level(level))
    quiet_library_loggers()
    root.addHandler(
        rotating_handler(
            data_dir / WORKER_LOG_NAME,
            max_bytes=WORKER_LOG_BYTES,
            backups=WORKER_LOG_BACKUPS,
        )
    )


def flush_logs() -> None:
    """Write every queued line now, for a process about to exit."""
    _WRITER.flush()


def set_log_level(level: str) -> str:
    """Apply a new root-logger level (runtime toggle); returns the normalized name."""
    name = normalize_level(level)
    logging.getLogger().setLevel(name)
    return name


def current_log_level() -> str:
    return logging.getLevelName(logging.getLogger().getEffectiveLevel())


def enable_crash_tracebacks(data_dir: Path) -> None:
    """Dump Python tracebacks into crash.log when the process dies hard.

    Catches segfaults/access violations in native extensions (pywinpty,
    sqlite3) that otherwise leave zero trace. Multiple processes appending to
    the same file is fine: faulthandler only writes when a crash happens.
    """
    global _crash_log_file
    if _crash_log_file is not None:
        return
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        _crash_log_file = (data_dir / CRASH_LOG_NAME).open("a", encoding="utf-8")
        faulthandler.enable(file=_crash_log_file)
    except OSError:
        _crash_log_file = None
