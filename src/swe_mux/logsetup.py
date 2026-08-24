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
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import IO
from uuid import uuid4

DAEMON_LOG_NAME = "daemon.log"
ACCESS_LOG_NAME = "access.log"
CRASH_LOG_NAME = "crash.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
DAEMON_LOG_BYTES = 10 * 1024 * 1024
DAEMON_LOG_BACKUPS = 5
ACCESS_LOG_BYTES = 10 * 1024 * 1024
ACCESS_LOG_BACKUPS = 3

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


def rotating_handler(
    path: Path, *, max_bytes: int, backups: int, structured: bool = True
) -> logging.Handler:
    """A size-rotated file handler that degrades gracefully.

    ``delay=True`` defers opening until the first emit, and a rotation rename
    that loses a race (a predecessor daemon briefly holding the old file during
    a session-preserving restart) is swallowed by ``Handler.handleError`` and
    retried on the next emit rather than breaking logging.

    ``structured=False`` is for ``access.log``: aiohttp's `AccessLogger` passes
    its whole atom table through ``extra=``, so serializing extras there would
    repeat every access line's contents twice over. The request id reaches that
    file through the access format string instead.
    """
    handler = logging.handlers.RotatingFileHandler(
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


def setup_daemon_logging(data_dir: Path, level: str = "INFO") -> None:
    """Configure the daemon process's logging: console + rotating files."""
    data_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(normalize_level(level))
    console = logging.StreamHandler()
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
