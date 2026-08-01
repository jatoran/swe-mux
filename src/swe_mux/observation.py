from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from .adapters.codex import codex_data_home
from .event_bus import EventBus
from .models import SessionState
from .scrollback import SCREEN_TAIL_BYTES
from .session import (
    STANDING_ACTIVITY_TTL_SLACK_SECONDS,
    Session,
    clear_standing_activity,
    pty_tail_state,
    pty_tail_waiting_on_background,
    set_standing_activity,
    transition_proof,
)
from .text_safety import utf8_safe

log = logging.getLogger(__name__)

OBSERVATION_SCHEMA_VERSION = "2"
PARSER_DEGRADE_MIN_EVENTS = 20
PARSER_DEGRADE_UNKNOWN_RATIO = 0.25

CLAUDE_KNOWN_RECORDS = {
    "ai-title",
    "assistant",
    "attachment",
    "file-history-delta",
    "file-history-snapshot",
    "last-prompt",
    "mode",
    "permission-mode",
    "queue-operation",
    "system",
    "user",
}

CODEX_KNOWN_OUTER_RECORDS = {
    "compacted",
    "inter_agent_communication_metadata",
    "session_meta",
    "turn_context",
    "world_state",
}

CODEX_KNOWN_PAYLOADS = {
    "agent_message",
    "apply_patch_approval_request",
    "context_compacted",
    "custom_tool_call",
    "custom_tool_call_output",
    "error",
    "exec_approval_request",
    "exec_command_begin",
    "exec_command_end",
    "exec_command_output_delta",
    "function_call",
    "function_call_output",
    "mcp_tool_call_end",
    "message",
    "patch_apply_end",
    "rate_limit",
    "rate_limited",
    "reasoning",
    "request_user_input",
    "sub_agent_activity",
    "task_complete",
    "task_started",
    "thread_goal_updated",
    "thread_rolled_back",
    "thread_settings_applied",
    "token_count",
    "turn_aborted",
    "user_message",
    "web_search_end",
}

# Local slash commands (/copy, /model, /resume, ...) are logged as user records
# but never reach the model, so they must not begin or sustain a root turn.
CLAUDE_LOCAL_COMMAND_PREFIXES = ("<command-", "<local-command-")
# Esc/interrupt writes a user record instead of any Stop hook or completion
# record; it is the only terminal evidence an aborted turn ever produces.
CLAUDE_INTERRUPT_PREFIX = "[Request interrupted by user"

# Records appended more than this long before observation attach are historical
# context (resume/promotion catch-up), not live activity.
HISTORICAL_TIMESTAMP_SLACK_SECONDS = 2.0
# An open turn found during catch-up counts as still running only when its last
# record is this recent; anything older is a stale artifact of an ended run.
CATCHUP_OPEN_TURN_WINDOW_SECONDS = 60.0
# A hook-initiated turn whose own submission turns out to be a local command is
# closed only when nothing else has happened in it and it began moments ago.
EMPTY_HOOK_TURN_WINDOW_SECONDS = 3.0
# An approval is raised by an unordered hook (priority 2) while the resumption
# evidence arrives on the ordered transcript (priority 1), so arbitration alone
# would keep a session "awaiting approval" for the whole rest of the turn. A
# transcript record may clear it, but only when provably written *after* the
# block: the transcript is polled while hooks POST immediately, so the record
# that triggered the prompt can be observed just after the prompt itself. The
# slack absorbs sub-second write/notify interleaving; a human approval is
# always slower than that.
AWAITING_RESUME_SLACK_SECONDS = 0.5
# Codex payloads that prove the model/tooling is running again.
CODEX_RESUME_PAYLOADS = frozenset(
    {
        "agent_message",
        "custom_tool_call",
        "custom_tool_call_output",
        "exec_command_begin",
        "exec_command_end",
        "function_call",
        "function_call_output",
        "mcp_tool_call_end",
        "patch_apply_end",
        "reasoning",
        "task_started",
        "user_message",
        "web_search_end",
    }
)

CLAUDE_CONTEXT_WINDOWS = {
    "claude-opus-4-8": 1_000_000,
    "claude-opus-4-7": 1_000_000,
    "claude-opus-4-6": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude-haiku-4-5": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}

# Standing-activity extraction (Claude). Record shapes verified 2026-07-31
# against live transcripts and the CLI's own tool schemas:
# - `ScheduleWakeup` input `{delaySeconds, reason, prompt}` arms a dynamic
#   /loop; `{stop: true}` ends it. The runtime clamps delays to [60, 3600] s.
# - Cron jobs are session-only: in-memory, no on-disk store (`durable` is a
#   documented no-op), gone when the CLI exits, recurring jobs auto-expired
#   after 7 days. `CronCreate` input `{cron, prompt, recurring?}`; `CronDelete`
#   input `{id}`. Transcript-only detection is therefore complete, and the
#   run-scoped annotation clears already match the store's lifetime. CronList
#   results are free text and are not parsed — the list call only refreshes.
# - A background Bash launch carries `run_in_background: true`; its tool_result
#   reads "Command running in background with ID: <task_id>." and completion
#   arrives later as a user record containing `<task-notification>` with the
#   launch's `<tool-use-id>` and a `<status>`. `TaskStop` input `{task_id}`.
LOOP_DELAY_MIN_SECONDS = 60.0
LOOP_DELAY_MAX_SECONDS = 3600.0
CRON_JOB_LIFETIME_SECONDS = 7 * 86400.0
# Background tasks have no evidence-implied duration (a watcher can legitimately
# run for hours), so the TTL is a slow decay against missed completion evidence,
# refreshed by every background-related record and by the CLI's own
# background-wait footer at each turn end. Process-tree fast-clear is Phase C.
BACKGROUND_TASKS_TTL_SECONDS = 1800.0
# Subagent liveness is evidence-recency: any sidechain record or lifecycle hook
# refreshes; this long without any means the fleet is gone.
SUBAGENT_QUIET_SECONDS = 120.0
STANDING_DETAIL_MAX_CHARS = 120
_BACKGROUND_TASK_ID = re.compile(r"running in background with ID:\s*([A-Za-z0-9_-]+)")
_TASK_NOTIFICATION_TOOL_USE = re.compile(r"<tool-use-id>\s*([^<\s]+)\s*</tool-use-id>")
_TASK_NOTIFICATION_TASK = re.compile(r"<task-id>\s*([^<\s]+)\s*</task-id>")


def _publish_update(session: Session) -> None:
    if getattr(session, "observation_replay", False):
        return
    publish = getattr(session, "publish_update", None)
    if callable(publish):
        publish()


class _NullEventBus:
    """Swallows semantic events while historical transcript records are replayed."""

    async def emit(self, event_type: str, **payload: Any) -> None:
        del event_type, payload
        return None


_NULL_EVENTS: Any = _NullEventBus()


def _claude_user_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text") or "")
    return ""


def _is_local_command_text(text: str) -> bool:
    return text.lstrip().startswith(CLAUDE_LOCAL_COMMAND_PREFIXES)


def _is_interrupt_text(text: str) -> bool:
    return text.lstrip().startswith(CLAUDE_INTERRUPT_PREFIX)


def _event_timestamp(event: dict[str, Any]) -> float | None:
    raw = event.get("timestamp") or event.get("ts")
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


_TOOL_TARGET_FIELDS = (
    "file_path",
    "path",
    "notebook_path",
    "filename",
    "command",
    "cmd",
    "pattern",
    "url",
)
_TOOL_CONTENT_FIELDS = ("new_string", "content", "contents", "new_source", "text", "patch")


def tool_call_evidence(tool_input: Any) -> tuple[str | None, str | None]:
    """Extract a normalized target and a parse-time content hash from a tool call.

    Runs at the adapter boundary while the native input is still in hand, so the
    hash is of the exact bytes the agent wrote — race-free, unlike reading the
    file back off disk after the event has queued. Native shapes never leave here.
    """
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except (json.JSONDecodeError, ValueError):
            return None, None
    if not isinstance(tool_input, dict):
        return None, None
    target: str | None = None
    for key in _TOOL_TARGET_FIELDS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            target = value.strip()[:512]
            break
    parts: list[str] = []
    for key in _TOOL_CONTENT_FIELDS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict) and isinstance(edit.get("new_string"), str):
                parts.append(edit["new_string"])
    content_hash = (
        hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest() if parts else None
    )
    return target, content_hash


# Bound for the `detail` string carried on a normalized tool_result event.
TOOL_DETAIL_LIMIT = 4000
# Only the tail of a very large result is scanned for a test summary: every
# supported runner prints its counts last, and an unbounded regex sweep on the
# event path is the one heavy operation this module must not introduce.
_TEST_SCAN_TAIL_BYTES = 256 * 1024
# Cheap pre-filter: skip the (bounded but non-trivial) parser entirely unless the
# output contains a token every supported runner emits.
_TEST_MARKERS = (
    " passed",
    " failed",
    "--- FAIL",
    "--- PASS",
    "test result:",
    "Ran ",
    "Tests:",
    "Test Files",
    "no tests ran",
)
_MAX_FAILING_TESTS = 100
_MAX_FAILING_TEST_CHARS = 200
# Cap on in-flight tool-call correlations kept per session.
_MAX_TRACKED_TOOL_CALLS = 512

_PYTEST_SUMMARY_RE = re.compile(
    r"^=*\s*(?P<body>(?:\d+\s+[a-z]+|no tests ran)(?:[,\s].*)?)\s+in\s+[\d.]+\s*s", re.IGNORECASE
)
_PYTEST_COUNT_RE = re.compile(
    r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed)\b", re.IGNORECASE
)
_PYTEST_FAILING_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)")
_JEST_COUNT_RE = re.compile(r"^\s*Tests:\s+(?P<body>.+)$")
_VITEST_COUNT_RE = re.compile(r"^\s*Tests\s+(?P<body>\d+\s+\w+.*)$")
_JS_COUNT_TOKEN_RE = re.compile(r"(\d+)\s+(passed|failed|skipped|todo|pending|total)\b")
_JEST_FAILING_RE = re.compile(r"^\s*(?:●|✕|×)\s+(.+?)\s*$")
_GO_FAIL_RE = re.compile(r"^\s*--- FAIL:\s+(\S+)")
_GO_PASS_RE = re.compile(r"^\s*--- PASS:\s+(\S+)")
_GO_SKIP_RE = re.compile(r"^\s*--- SKIP:\s+(\S+)")
_CARGO_RESULT_RE = re.compile(
    r"^test result:\s+(?P<verdict>ok|FAILED)\.\s+(?P<body>.+)$", re.IGNORECASE
)
_CARGO_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|ignored|measured|filtered out)\b")
_CARGO_FAILING_RE = re.compile(r"^test\s+(\S+)\s+\.\.\.\s+FAILED\s*$")
_UNITTEST_RAN_RE = re.compile(r"^Ran\s+(\d+)\s+tests?\s+in\s+[\d.]+s")
_UNITTEST_VERDICT_RE = re.compile(r"^(OK|FAILED)(?:\s*\((?P<body>.*)\))?\s*$")
_UNITTEST_COUNT_RE = re.compile(r"(failures|errors|skipped|expected failures)=(\d+)")
_UNITTEST_FAILING_RE = re.compile(r"^(?:FAIL|ERROR):\s+(.+?)\s*$")


def bounded_detail(text: str, limit: int = TOOL_DETAIL_LIMIT) -> str:
    """Bound tool output for an event payload without discarding its tail.

    A head-only slice drops exactly the part that carries meaning for build and
    test output — every runner prints its verdict last — so keep both ends with
    an explicit marker for the dropped middle.
    """
    if len(text) <= limit:
        return text
    reserve = 64
    head = max(0, (limit - reserve) // 2)
    tail = max(0, limit - reserve - head)
    dropped = len(text) - head - tail
    marker = f"\n...[{dropped} chars truncated]...\n"
    if not tail:
        return (text[:head] + marker)[:limit]
    return (text[:head] + marker + text[-tail:])[:limit]


def _clean_test_ids(ids: list[str]) -> list[str]:
    seen: list[str] = []
    for raw in ids:
        value = raw.strip()[:_MAX_FAILING_TEST_CHARS]
        if value and value not in seen:
            seen.append(value)
        if len(seen) >= _MAX_FAILING_TESTS:
            break
    return seen


def _test_outcome(
    framework: str,
    counts: dict[str, int],
    failing: list[str],
    *,
    truncated: bool,
) -> dict[str, Any]:
    failed = counts.get("failed", 0) + counts.get("errors", 0)
    outcome: dict[str, Any] = {
        "framework": framework,
        "passed": counts.get("passed", 0),
        "failed": counts.get("failed", 0),
        "errors": counts.get("errors", 0),
        "skipped": counts.get("skipped", 0),
        "failing_tests": _clean_test_ids(failing),
        "ok": failed == 0,
    }
    if truncated:
        outcome["scan_truncated"] = True
    return outcome


def _parse_pytest(lines: list[str]) -> tuple[dict[str, int], list[str]] | None:
    counts: dict[str, int] | None = None
    for line in reversed(lines):
        match = _PYTEST_SUMMARY_RE.match(line.strip())
        if not match:
            continue
        body = match.group("body")
        found = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
        for value, label in _PYTEST_COUNT_RE.findall(body):
            key = label.lower()
            if key in {"error", "errors"}:
                found["errors"] += int(value)
            elif key in {"passed", "xpassed"}:
                found["passed"] += int(value)
            elif key in {"failed", "xfailed"}:
                found["failed"] += int(value)
            elif key == "skipped":
                found["skipped"] += int(value)
        counts = found
        break
    if counts is None:
        return None
    failing = [
        match.group(1) for line in lines if (match := _PYTEST_FAILING_RE.match(line.strip()))
    ]
    return counts, failing


def _parse_js(lines: list[str]) -> tuple[dict[str, int], list[str]] | None:
    counts: dict[str, int] | None = None
    for line in reversed(lines):
        match = _JEST_COUNT_RE.match(line) or _VITEST_COUNT_RE.match(line)
        if not match:
            continue
        tokens = _JS_COUNT_TOKEN_RE.findall(match.group("body"))
        if not tokens:
            continue
        found = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
        for value, label in tokens:
            key = label.lower()
            if key in {"skipped", "todo", "pending"}:
                found["skipped"] += int(value)
            elif key in {"passed", "failed"}:
                found[key] += int(value)
        counts = found
        break
    if counts is None:
        return None
    failing = [match.group(1) for line in lines if (match := _JEST_FAILING_RE.match(line))]
    return counts, failing


def _parse_go(lines: list[str]) -> tuple[dict[str, int], list[str]] | None:
    failing = [match.group(1) for line in lines if (match := _GO_FAIL_RE.match(line))]
    passed = sum(1 for line in lines if _GO_PASS_RE.match(line))
    skipped = sum(1 for line in lines if _GO_SKIP_RE.match(line))
    if not failing and not passed and not skipped:
        return None
    counts = {"passed": passed, "failed": len(failing), "errors": 0, "skipped": skipped}
    return counts, failing


def _parse_cargo(lines: list[str]) -> tuple[dict[str, int], list[str]] | None:
    counts: dict[str, int] | None = None
    for line in reversed(lines):
        match = _CARGO_RESULT_RE.match(line.strip())
        if not match:
            continue
        found = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
        for value, label in _CARGO_COUNT_RE.findall(match.group("body")):
            key = label.lower()
            if key == "ignored":
                found["skipped"] += int(value)
            elif key in {"passed", "failed"}:
                found[key] += int(value)
        counts = found
        break
    if counts is None:
        return None
    failing = [match.group(1) for line in lines if (match := _CARGO_FAILING_RE.match(line.strip()))]
    return counts, failing


def _parse_unittest(lines: list[str]) -> tuple[dict[str, int], list[str]] | None:
    ran: int | None = None
    counts: dict[str, int] | None = None
    for index, line in enumerate(lines):
        match = _UNITTEST_RAN_RE.match(line.strip())
        if not match:
            continue
        ran = int(match.group(1))
        found = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
        for candidate in lines[index + 1 : index + 4]:
            verdict = _UNITTEST_VERDICT_RE.match(candidate.strip())
            if not verdict:
                continue
            for label, value in _UNITTEST_COUNT_RE.findall(verdict.group("body") or ""):
                key = label.lower()
                if key == "failures":
                    found["failed"] += int(value)
                elif key == "errors":
                    found["errors"] += int(value)
                elif key == "skipped":
                    found["skipped"] += int(value)
            break
        counts = found
    if ran is None or counts is None:
        return None
    counts["passed"] = max(
        0, ran - counts["failed"] - counts["errors"] - counts["skipped"]
    )
    failing = [
        match.group(1) for line in lines if (match := _UNITTEST_FAILING_RE.match(line.strip()))
    ]
    return counts, failing


def parse_test_outcome(text: str) -> dict[str, Any] | None:
    """Extract a structured test result (counts + failing ids) from tool output.

    Deterministic Tier 0 capture (CONTROL_PLANE_ROADMAP §5.3): a test fact is
    "pass/fail counts + failing-test ids", not a success boolean. Runs at the
    adapter boundary while the full output is still in hand, because the bounded
    `detail` carried on the event cannot be relied on to contain the summary.
    Returns None when the output is not a recognized test run.
    """
    if not text:
        return None
    truncated = len(text) > _TEST_SCAN_TAIL_BYTES
    window = text[-_TEST_SCAN_TAIL_BYTES:] if truncated else text
    if not any(marker in window for marker in _TEST_MARKERS):
        return None
    lines = window.splitlines()
    for framework, parser in (
        ("pytest", _parse_pytest),
        ("jest", _parse_js),
        ("cargo", _parse_cargo),
        ("go", _parse_go),
        ("unittest", _parse_unittest),
    ):
        parsed = parser(lines)
        if parsed is not None:
            counts, failing = parsed
            return _test_outcome(framework, counts, failing, truncated=truncated)
    return None


def tool_result_evidence(text: str) -> tuple[str | None, dict[str, Any] | None]:
    """Derive a content hash and a structured test outcome from a tool result.

    The hash covers the *full* result text (the exact bytes the agent saw), which
    is what closes the Tier 0 read-side hash gap: a `Read` result hashes the file
    content the agent actually consumed, race-free, without re-reading it off
    disk. Identical repeated command output also hashes identically, which is the
    no-progress signal loop detection queries.
    """
    if not text:
        return None, None
    content_hash = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
    return content_hash, parse_test_outcome(text)


def _tool_names(session: Session) -> dict[str, str]:
    names = getattr(session, "tool_names", None)
    if names is None:
        names = {}
        session.tool_names = names
    return names


def _tool_targets(session: Session) -> dict[str, str]:
    targets = getattr(session, "tool_targets", None)
    if targets is None:
        targets = {}
        session.tool_targets = targets
    return targets


def _remember_tool_call(
    session: Session, call_id: str, name: str, target: str | None
) -> None:
    """Correlate a tool invocation id with its name and target.

    Tool results identify their call only by opaque id, so a result would
    otherwise carry no target at all — collapsing every Tier 0 result fingerprint
    onto one value. Both maps are bounded: a call whose result never arrives (an
    interrupted turn) must not accumulate for the session's lifetime.
    """
    if not call_id:
        return
    names = _tool_names(session)
    targets = _tool_targets(session)
    names[call_id] = name
    if target:
        targets[call_id] = target
    for bucket in (names, targets):
        while len(bucket) > _MAX_TRACKED_TOOL_CALLS:
            bucket.pop(next(iter(bucket)))


def _recall_tool_call(
    session: Session, call_id: str, fallback: str = "tool"
) -> tuple[str, str | None]:
    if not call_id:
        return fallback, None
    return _tool_names(session).pop(call_id, fallback), _tool_targets(session).pop(call_id, None)


class JsonlTailer:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.decoder = IncrementalJsonlDecoder()
        self.prefix: bytes | None = None
        # Content already present at attach is history (resume, promotion after
        # activity), not live agent behavior; events() labels it historical.
        try:
            self.initial_size = path.stat().st_size
        except OSError:
            self.initial_size = 0
        self._caught_up = self.initial_size == 0

    async def events(self, stop: asyncio.Event):  # type: ignore[no-untyped-def]
        """Yield records plus explicit replay-boundary markers.

        ``(None, True)`` starts a new historical snapshot after the provider
        truncates or replaces the file (Claude cancel/revert does this), and
        ``(None, False)`` ends catch-up.  The end marker is also emitted after all
        bytes that existed at initial attach have been decoded.
        """
        while not stop.is_set() and not self.path.exists():
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.2)
            except TimeoutError:
                pass
        while not stop.is_set():
            try:
                size = self.path.stat().st_size
                reset = size < self.offset
                if not reset and self.offset and self.prefix is not None:
                    with self.path.open("rb") as handle:
                        current_prefix = handle.read(min(64, size))
                    compared = min(len(self.prefix), len(current_prefix))
                    reset = current_prefix[:compared] != self.prefix[:compared]
                if reset:
                    self.offset = 0
                    self.decoder.reset()
                    self.prefix = None
                    # Replacement content is a fresh historical snapshot.  Never
                    # compare its byte positions with the original attach size;
                    # doing so suppresses live records until the rewritten file
                    # grows past its former length.
                    self.initial_size = size
                    self._caught_up = False
                    yield None, True
                if size > self.offset:
                    with self.path.open("rb") as handle:
                        if self.prefix is None or len(self.prefix) < 64:
                            self.prefix = handle.read(min(64, size))
                        handle.seek(self.offset)
                        chunk = handle.read(size - self.offset)
                    self.offset = size
                    for position, item in self.decoder.feed_with_positions(chunk):
                        yield item, position <= self.initial_size
                if not self._caught_up and self.offset >= self.initial_size:
                    self._caught_up = True
                    yield None, False
            except FileNotFoundError:
                pass
            await asyncio.sleep(0.25)


class IncrementalJsonlDecoder:
    """Decode append-only JSONL chunks while retaining only an incomplete final line."""

    def __init__(self) -> None:
        self.partial = b""
        self.position = 0

    def reset(self) -> None:
        self.partial = b""
        self.position = 0

    def feed_with_positions(self, chunk: bytes) -> list[tuple[int, dict[str, Any]]]:
        """Return (byte offset after the record's line, record) for each complete line."""
        lines = (self.partial + chunk).split(b"\n")
        consumed = self.position
        self.partial = lines.pop()
        result: list[tuple[int, dict[str, Any]]] = []
        for line in lines:
            consumed += len(line) + 1
            if not line.strip():
                continue
            try:
                item = json.loads(line.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                log.debug("skipping invalid transcript JSONL record")
                continue
            if isinstance(item, dict):
                result.append((consumed, item))
        self.position = consumed
        return result

    def feed(self, chunk: bytes) -> list[dict[str, Any]]:
        return [item for _position, item in self.feed_with_positions(chunk)]


async def observe_transcript(
    session: Session, path: Path, events: EventBus, stop: asyncio.Event
) -> None:
    session.record.parser_status = "watching"
    session.record.parser_schema_version = OBSERVATION_SCHEMA_VERSION
    session.record.parser_diagnostic = f"tailing {path.name}"
    _publish_update(session)
    tailer = JsonlTailer(path)
    attach_ts = time.time()
    replay_pending = tailer.initial_size > 0
    if replay_pending:
        session.observation_replay = True
    historical_seen = 0
    last_historical_ts: float | None = None
    try:
        async for event, byte_historical in tailer.events(stop):
            if event is None:
                if byte_historical:
                    replay_pending = True
                    session.observation_replay = True
                    historical_seen = 0
                    last_historical_ts = None
                    continue
                if replay_pending:
                    replay_pending = False
                    await _finish_transcript_catchup(
                        session, events, attach_ts, last_historical_ts, historical_seen
                    )
                continue
            ts = _event_timestamp(event)
            historical = byte_historical or (
                ts is not None and ts < attach_ts - HISTORICAL_TIMESTAMP_SLACK_SECONDS
            )
            if historical:
                # Harvest telemetry (tokens/context/model/tool correlation) from
                # history without emitting events or driving state; the replay
                # flag suppresses transitions and update fanout even when an
                # old-stamped record straggles in after catch-up finished.
                historical_seen += 1
                if ts is not None:
                    last_historical_ts = ts
                session.observation_replay = True
                try:
                    if session.record.backend == "claude":
                        await _claude(session, event, _NULL_EVENTS)
                    elif session.record.backend == "codex":
                        await _codex(session, event, _NULL_EVENTS)
                finally:
                    session.observation_replay = replay_pending
                continue
            if replay_pending:
                # A live record can share the first read with the historical tail;
                # resolve catch-up before it may drive state.
                replay_pending = False
                await _finish_transcript_catchup(
                    session, events, attach_ts, last_historical_ts, historical_seen
                )
            recognized, signature = classify_transcript_event(session.record.backend, event)
            if session.record.backend == "claude":
                await _claude(session, event, events)
            elif session.record.backend == "codex":
                await _codex(session, event, events)
            await _record_parser_observation(session, events, recognized, signature)
    finally:
        session.observation_replay = False


async def _finish_transcript_catchup(
    session: Session,
    events: EventBus,
    attach_ts: float,
    last_historical_ts: float | None,
    historical_seen: int,
) -> None:
    """Resolve the live state once pre-existing transcript content is consumed.

    A turn left open by history counts as running only when its newest record is
    recent (promotion moments after the user submitted); an old open turn is a
    stale artifact of an ended run and the session is simply ready for input.
    """
    session.observation_replay = False
    state = _observation_state(session)
    open_turn = bool(state.get("root_turn_active"))
    recent = (
        last_historical_ts is not None
        and attach_ts - last_historical_ts < CATCHUP_OPEN_TURN_WINDOW_SECONDS
    )
    state["root_turn_active"] = False
    state["root_completion_seen"] = False
    if not historical_seen:
        # An empty replacement is still authoritative cancel/revert evidence.
        if session.record.state in {"working", "awaiting"}:
            await _transition(
                session,
                events,
                "idle",
                source="transcript",
                force=True,
                evidence="catchup:empty_replacement",
            )
        return
    if hasattr(session, "state_source_priority"):
        # A complete provider snapshot is a new observation boundary.  It may
        # reconcile a turn whose higher-priority completion hook never fired.
        session.state_source_priority = -1
    if open_turn and recent:
        await _begin_root_turn(
            session, events, source="transcript", evidence="catchup:open_turn_recent"
        )
    else:
        await _transition(
            session, events, "idle", source="transcript", evidence="catchup:settled"
        )
        if session.record.state == "idle":
            # Delivery readiness keeps its lifecycle memory in the daemon process,
            # so a restart left an already-idle session with no record that its
            # last root turn had finished, and every queued message to it then
            # needed the operator's override. Settling *is* that evidence, read
            # from the transcript.
            #
            # It is left on the session rather than only announced, because
            # adoption runs long before the fleet subscribes to the bus: the one
            # session that most needs this is the one whose observer caught up
            # during startup, and its event went out to nobody. A fact parked
            # where the reader looks cannot be missed by being early. `records`
            # is how many of this session's own transcript records were read to
            # reach the conclusion; non-zero is itself proof the transcript was
            # found, owned, parsed, and understood, which is what readiness asks
            # of `parser_or_hook_supported` and which an idle session cannot
            # otherwise demonstrate until its next turn. The revision and screen
            # are captured here, not when the reader gets around to it, so the
            # composer-collision guard still measures from this instant.
            state["catchup_settled"] = {
                "records": historical_seen,
                "input_revision": int(getattr(session, "input_revision", 0)),
                "screen": getattr(getattr(session, "screen", None), "mode", None),
            }
            # Announced as well, for the audit trail and the live path. Never as a
            # synthetic `turn_ended`, which would fire read-aloud, notifications,
            # and turn observers for a turn that ended before the restart.
            await events.emit(
                "root_turn_settled",
                session_id=session.record.id,
                source="transcript",
                scope="root",
                evidence="catchup:settled",
                records=historical_seen,
            )
    _publish_update(session)


def classify_transcript_event(backend: str, event: dict[str, Any]) -> tuple[bool, str]:
    outer = str(event.get("type") or "<missing>")
    if backend == "claude":
        return outer in CLAUDE_KNOWN_RECORDS, f"claude:{outer}"
    if backend == "codex":
        payload = event.get("payload")
        payload_type = str(payload.get("type") or "") if isinstance(payload, dict) else ""
        signature = f"codex:{outer}:{payload_type or '<none>'}"
        return (
            outer in CODEX_KNOWN_OUTER_RECORDS or payload_type in CODEX_KNOWN_PAYLOADS,
            signature,
        )
    return False, f"{backend}:{outer}"


# Trailing records the provider appends after a turn (naming, mode markers,
# prompt echoes) are not turn activity and must be skipped when judging the tail.
_CLAUDE_TAIL_IGNORED = {
    "ai-title",
    "attachment",
    "file-history-delta",
    "file-history-snapshot",
    "last-prompt",
    "mode",
    "permission-mode",
    "queue-operation",
}
# Tail bytes read to judge turn state without loading a multi-megabyte transcript.
TRANSCRIPT_TAIL_BYTES = 131_072


def transcript_tail_turn_state(backend: str, path: Path) -> str:
    """Classify the transcript tail as ``ended``, ``open`` or ``unknown``.

    Used by the quiescence watchdog to decide whether a stuck "working" session
    has actually finished. ``open`` means a tool or turn is still in flight, so a
    long-running tool call is never mistaken for a hang. Only ``ended`` is proof
    the turn is over; ``unknown`` means the tail carries no decisive signal.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > TRANSCRIPT_TAIL_BYTES:
                handle.seek(size - TRANSCRIPT_TAIL_BYTES)
                handle.readline()  # discard the partial line at the seek point
            raw = handle.read()
    except OSError:
        return "unknown"
    records: list[dict[str, Any]] = []
    for line in raw.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return tail_turn_state(backend, records)


def tail_turn_state(backend: str, records: list[dict[str, Any]]) -> str:
    """Classify already-parsed tail records; shared with the replay harness."""
    if backend == "claude":
        return _claude_tail_state(records)
    if backend == "codex":
        return _codex_tail_state(records)
    return "unknown"


def _claude_tail_state(records: list[dict[str, Any]]) -> str:
    for event in reversed(records):
        event_type = event.get("type")
        if event_type == "system":
            subtype = str(event.get("subtype") or "")
            if subtype in {"turn_duration", "stop_hook_summary"}:
                return "ended"
            continue
        if event_type == "assistant":
            message = event.get("message") or {}
            content = message.get("content")
            blocks = content if isinstance(content, list) else []
            has_tool_use = any(
                isinstance(block, dict) and block.get("type") == "tool_use" for block in blocks
            )
            has_text = (
                isinstance(content, str)
                and bool(content)
                or any(isinstance(block, dict) and block.get("type") == "text" for block in blocks)
            )
            if message.get("stop_reason") == "end_turn":
                return "ended"
            if has_tool_use:
                return "open"
            if message.get("stop_reason") == "tool_use":
                return "open"
            return "ended" if has_text else "unknown"
        if event_type == "user":
            content = event.get("message", {}).get("content") if event.get("message") else None
            text = _claude_user_text(content)
            if event.get("isMeta") is True or _is_local_command_text(text):
                continue
            if _is_interrupt_text(text):
                return "ended"
            # A plain prompt or a tool result both mean the model still owes a
            # response — the turn is open.
            return "open"
        # Metadata / bookkeeping records carry no turn signal; keep scanning.
        if event_type in _CLAUDE_TAIL_IGNORED:
            continue
    return "unknown"


def _codex_tail_state(records: list[dict[str, Any]]) -> str:
    for event in reversed(records):
        payload = event.get("payload") or {}
        payload_type = payload.get("type") if isinstance(payload, dict) else None
        if payload_type in {"task_complete", "turn_aborted", "thread_rolled_back"}:
            return "ended"
        if payload_type in {
            "task_started",
            "user_message",
            "function_call",
            "custom_tool_call",
            "exec_command_begin",
            "function_call_output",
            "custom_tool_call_output",
            "exec_command_end",
            "patch_apply_end",
            "mcp_tool_call_end",
            "web_search_end",
            "exec_approval_request",
            "apply_patch_approval_request",
            "request_user_input",
        }:
            return "open"
    return "unknown"


async def _record_parser_observation(
    session: Session,
    events: EventBus,
    recognized: bool,
    signature: str,
) -> None:
    record = session.record
    previous_status = record.parser_status
    if record.observation_stale_since is not None:
        # A record read from the followed transcript is proof it is the live
        # conversation after all: whatever made it look abandoned has resolved.
        record.observation_stale_since = None
    if recognized:
        record.parser_events_seen += 1
    else:
        record.parser_unknown_events += 1
        signatures = record.parser_unknown_signatures
        signatures[signature] = signatures.get(signature, 0) + 1
        if len(signatures) > 20:
            least = min(signatures, key=lambda item: (signatures[item], item))
            signatures.pop(least, None)
    total = record.parser_events_seen + record.parser_unknown_events
    unknown_ratio = record.parser_unknown_events / total if total else 0.0
    if total >= PARSER_DEGRADE_MIN_EVENTS and unknown_ratio >= PARSER_DEGRADE_UNKNOWN_RATIO:
        record.parser_status = "degraded"
        record.parser_diagnostic = (
            f"schema v{OBSERVATION_SCHEMA_VERSION}: {record.parser_unknown_events}/{total} "
            f"unrecognized transcript records ({unknown_ratio:.0%})"
        )
    elif record.parser_events_seen:
        record.parser_status = "ready"
        record.parser_diagnostic = (
            f"schema v{OBSERVATION_SCHEMA_VERSION}: {record.parser_events_seen}/{total} "
            f"recognized ({unknown_ratio:.0%} unknown)"
        )
    if record.parser_status != previous_status or not recognized:
        _publish_update(session)
    if record.parser_status == "degraded" and previous_status != "degraded":
        await events.emit(
            "capability_degraded",
            session_id=record.id,
            source="transcript",
            capability="semantic_transcript",
            minimum="semantic",
            reason=record.parser_diagnostic,
            schema_version=OBSERVATION_SCHEMA_VERSION,
            unknown_ratio=unknown_ratio,
            unknown_signatures=dict(record.parser_unknown_signatures),
        )


def _observation_state(session: Session) -> dict[str, Any]:
    state = getattr(session, "observation_state", None)
    if state is None:
        state = {
            "root_turn_active": False,
            "root_completion_seen": False,
            "codex_scope": "root",
            "closed_by_transcript": False,
        }
        session.observation_state = state
    return state


def provisional_observation(session: Session) -> bool:
    """True while the followed transcript was chosen by elimination, not identity.

    A backend that mints its own conversation id cannot be exact-matched to a file
    until it names that id over the authenticated hook ingress, which Codex only
    does when its first turn *ends*. Following the sole unclaimed candidate in the
    meantime is what lets a fresh pane report its first turn at all
    (`_may_adopt_sole_candidate`), but the file is a well-reasoned guess rather
    than a proven fact, so it is trusted for exactly one thing: state.

    Everything this gates is *attribution* — a durable claim that some work was
    this session's. Getting that wrong renders a stranger's conversation under
    this pane's identity, which no amount of later correction undoes:

    - `native_session_id` from the file's own header. Rebinding to it would make
      the guess self-confirming and would defeat the hook check entirely.
    - Token counts, context window, and model. These are displayed on the pane and
      copied into the history row at turn end.
    - Compaction evidence, which is durable per-session operational telemetry.
    - The history row itself (written in `SessionManager._observe`).

    State is deliberately not on that list. A wrong guess there costs a pane that
    reads "working" while some other codex runs — visible, self-correcting on the
    next hook, and strictly more conservative for delivery than the "ready · turn
    complete" it replaces.
    """
    return bool(getattr(session, "transcript_provisional", False))


def _transcript_authoritative(session: Session) -> bool:
    """True once the ordered transcript observer has proven itself healthy.

    Hooks are an unordered, retried side channel — each event is a separate POST
    with its own backoff, so a late ``PreToolUse``/``PostToolUse`` can land after
    the transcript's end-of-turn and strand a finished session as "working".
    Once the parser is ``ready`` the transcript is the authoritative, in-order
    record of turn boundaries and tool activity, so hooks must not drive that
    state; while it is still warming up (``watching``, no recognized records yet)
    or has ``degraded``, hooks remain the fallback that keeps state moving.

    A *stale* transcript revokes that authority outright. The parser is healthy and
    the file is well-formed; it is simply no longer the conversation this PTY is
    running (an unfollowable `/clear` or `/new`). Continuing to treat it as
    authoritative is what froze such sessions: the transcript can no longer report
    a turn boundary, and hooks were being dropped as redundant to it.
    """
    if getattr(session.record, "observation_stale_since", None):
        return False
    return getattr(session.record, "parser_status", "") == "ready"


def session_pty_state(session: Session) -> str:
    """What this session's CLI is showing, or "unknown" without a scrollback."""
    scrollback = getattr(session, "scrollback", None)
    if scrollback is None:
        return "unknown"
    try:
        return pty_tail_state(
            scrollback.tail_bytes(SCREEN_TAIL_BYTES).decode("utf-8", "replace")
        )
    except (OSError, ValueError):
        return "unknown"


async def _resume_from_awaiting(
    session: Session, events: EventBus, event: dict[str, Any], *, evidence: str
) -> None:
    """Clear an already-answered `awaiting` when the transcript proves work resumed.

    Ordered, in-band evidence written after the block was raised means the user
    answered (or the CLI moved on): the tool ran, the model spoke again, or a new
    prompt was submitted. The PTY is used only as a veto — if this session's own
    screen still shows a permission dialog, a parallel tool's record must not
    hide a prompt the user has yet to answer.
    """
    if session.record.state != "awaiting":
        return
    state = _observation_state(session)
    awaiting_since = state.get("awaiting_since")
    if not isinstance(awaiting_since, (int, float)):
        return
    ts = _event_timestamp(event)
    if ts is None or ts <= awaiting_since + AWAITING_RESUME_SLACK_SECONDS:
        return
    if session_pty_state(session) == "approval":
        return
    await _transition(
        session,
        events,
        "working",
        source="transcript",
        force=True,
        evidence=f"resumed_after_awaiting:{evidence}",
    )


async def _begin_root_turn(
    session: Session, events: EventBus, *, source: str, evidence: str | None = None
) -> None:
    state = _observation_state(session)
    if source == "transcript":
        # Ordered, in-band evidence of new work supersedes the prior close.
        state["closed_by_transcript"] = False
    elif state.get("closed_by_transcript") and _transcript_authoritative(session):
        # The transcript already closed this turn; a later, unordered hook must
        # not reopen "working" on a session that is really waiting for input.
        # Only fresh transcript activity (above) starts the next turn. Each
        # refusal is a hook/transcript race, counted as a regression signal.
        note = getattr(session, "note_reopen_blocked", None)
        if callable(note):
            note(source)
        return
    if (
        not state["root_turn_active"]
        and session.record.state not in {"working", "awaiting"}
        and hasattr(session, "state_source_priority")
    ):
        # Priority arbitrates conflicting evidence within one turn, not forever.
        # A new root turn lets the best currently available tier take ownership
        # when a hook or transcript source has gone missing.
        session.state_source_priority = -1
    await _transition(session, events, "working", source=source, evidence=evidence)
    if state["root_turn_active"]:
        return
    state["root_turn_active"] = True
    state["root_completion_seen"] = False
    state["turn_started_at"] = time.time()
    state["turn_saw_activity"] = False
    await events.emit("turn_started", session_id=session.record.id, source=source, scope="root")


def _background_wait_reason(session: Session) -> str | None:
    """`waiting_on_background` when the CLI says it will resume itself.

    Read from this session's own screen, which is the one source that cannot be
    mis-attributed. `delivery_state` is deliberately untouched: the composer does
    accept input and a write is safe — the sub-reason exists so "ready" does not
    read as "finished, nothing more is coming".
    """
    scrollback = getattr(session, "scrollback", None)
    if scrollback is None:
        return None
    try:
        tail = scrollback.tail_bytes(SCREEN_TAIL_BYTES).decode("utf-8", "replace")
    except Exception:
        return None
    return "waiting_on_background" if pty_tail_waiting_on_background(tail) else None


async def _finish_root_turn(
    session: Session,
    events: EventBus,
    *,
    source: str,
    outcome: str = "completed",
    force: bool = False,
    evidence: str | None = None,
    inferred: bool | None = None,
    **payload: Any,
) -> None:
    state = _observation_state(session)
    if source == "transcript":
        # Latch the transcript's authoritative turn boundary so a late, unordered
        # hook cannot reopen "working" afterward (set before the early-return so a
        # trailing turn_duration after a hook Stop still arms the latch).
        state["closed_by_transcript"] = True
    if not state["root_turn_active"] and state.get("root_completion_seen"):
        return
    # A provider-native completion belongs to the active root turn and is a
    # stronger boundary than the source that observed its start.  This lets the
    # transcript close a hook-started turn when Stop was missed.
    force = force or (
        source == "transcript"
        and (bool(state["root_turn_active"]) or session.record.state in {"working", "awaiting"})
    )
    state["root_turn_active"] = False
    state["root_completion_seen"] = True
    if transition_proof(source, inferred) == "inferred":
        # Recovery inferences stay visible in the event stream, not only the ledger.
        payload.setdefault("inferred", True)
    if outcome == "completed":
        pty_wait = _background_wait_reason(session)
        if pty_wait and not getattr(session, "observation_replay", False):
            # The CLI's own footer corroborates running background work. It
            # knows *that* tasks run, not how many, so it refreshes recency
            # without clobbering a transcript-derived count.
            now = _session_now(session)
            if set_standing_activity(
                session,
                "background_tasks",
                source="pty",
                evidence="pty:background_wait_marker",
                expires_at=now + BACKGROUND_TASKS_TTL_SECONDS,
                now=now,
            ):
                _publish_update(session)
        # UI-compat: `idle_reason` is derived from the annotation axis (kept one
        # release); the annotation is the source of truth either way.
        idle_reason = (
            "waiting_on_background"
            if pty_wait
            or any(a.kind == "background_tasks" for a in session.record.standing_activity)
            else None
        )
        await _transition(
            session, events, "idle", source=source, force=force,
            evidence=evidence, inferred=inferred, idle_reason=idle_reason,
        )
        await events.emit(
            "turn_ended",
            session_id=session.record.id,
            source=source,
            scope="root",
            outcome=outcome,
            # Consumers that decide whether to interrupt the user (sounds, push,
            # attention) need this on the event, not only on the record: the turn
            # ended, but the agent is going to resume itself.
            idle_reason=idle_reason,
            **payload,
        )
    else:
        await _transition(
            session, events, "idle", outcome, source=source, force=force,
            evidence=evidence, inferred=inferred,
        )
        await events.emit(
            "turn_aborted",
            session_id=session.record.id,
            source=source,
            scope="root",
            outcome=outcome,
            **payload,
        )


async def _complete_empty_hook_turn(session: Session, events: EventBus) -> None:
    """Close a just-started turn whose submission turned out to be a local command.

    UserPromptSubmit can fire before the daemon can know the prompt is a local
    command. Such a turn never reaches the model, so no Stop/completion evidence
    will ever arrive; without this it stays "working" forever. A genuinely active
    turn is protected by the recency and no-activity guards.
    """
    state = _observation_state(session)
    if not state.get("root_turn_active") or state.get("turn_saw_activity"):
        return
    started = float(state.get("turn_started_at") or 0.0)
    if time.time() - started > EMPTY_HOOK_TURN_WINDOW_SECONDS:
        return
    await _finish_root_turn(
        session, events, source="transcript", force=True, evidence="local_command_record"
    )


def hook_event_scope(event_type: str, payload: dict[str, Any]) -> str:
    if event_type in {"SubagentStart", "SubagentStop"}:
        return "subagent"
    if payload.get("isSidechain") is True or payload.get("is_sidechain") is True:
        return "subagent"
    if payload.get("agent_id") and event_type not in {"SessionStart", "SessionEnd"}:
        return "subagent"
    return "root"


_HOOK_NATIVE_ID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def conversation_unbound(session: Session) -> bool:
    """True while this session's conversation id is still a placeholder.

    Not a shape test. A backend that mints its own conversation id (Codex) carries
    the *mux session id* until it learns the real one, and mux session ids are UUIDs
    too — so asking whether the id merely looks like a UUID reports every fresh Codex
    session as already bound and refuses the only evidence that could bind it.
    """
    record = session.record
    native = record.native_session_id or ""
    if not native:
        return True
    # Defaults to the conservative side: a backend that has not declared itself is
    # treated as having been given its conversation id, so a bound-looking id is
    # left alone rather than re-bound from a hook.
    adapter = getattr(session, "adapter", None)
    if getattr(adapter, "assigns_conversation_id", True):
        return not _HOOK_NATIVE_ID.fullmatch(native)
    return native == record.id


MAX_REMEMBERED_PROMPT_CHARS = 4000


def _remember_user_prompt(session: Session, payload: dict[str, Any]) -> None:
    """Keep this pane's first and latest root requests for the titler.

    Bounded rather than stored whole: a long prompt is no more informative for a
    2-3 word tab label, and the observer input budget would reject it anyway. Only
    root prompts count — a subagent's instructions are not what the tab is about.

    The first prompt is pinned and never overwritten for the life of the run. It is
    what the session is about; every later prompt is a step within that, so titling
    from one produces a name that describes the last few minutes rather than the
    work. A rollover retires the pin along with the conversation.
    """
    if hook_event_scope("UserPromptSubmit", payload) != "root":
        return
    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        return
    # Scrubbed before it is stored, not before it is used: this value is pinned to
    # a checkpoint that outlives the daemon, so anything unencodable saved here is
    # a fault that keeps being replayed after the source is fixed.
    text = utf8_safe(prompt.strip())
    if text:
        session.last_user_prompt = text[:MAX_REMEMBERED_PROMPT_CHARS]
        if session.first_user_prompt is None:
            session.first_user_prompt = session.last_user_prompt


async def _bind_native_id_from_hook(
    session: Session, payload: dict[str, Any], events: EventBus
) -> None:
    """Adopt the conversation id the CLI reports for itself, when we have none.

    `claude --continue` / `claude -r <term>` let the CLI choose the conversation,
    so the launcher shim cannot inject or read a `--session-id` and promotes with
    an empty native id. The root `SessionStart` hook arrives over this session's
    own loopback ingress authenticated with this session's own secret, which makes
    it the strongest available proof of which conversation this PTY is running —
    stronger than the sole-unclaimed-candidate heuristic it replaces here.

    This is also the *only* way a Codex session can be bound. Codex mints its own
    thread id, so nothing on the filesystem separates its rollout from one written
    by a `codex` started outside mux in the same cwd (measured: `originator` betrays
    only the headless `codex exec`; an interactive outsider is identical). Codex
    reports `thread-id` on its `agent-turn-complete` notify, over this same
    authenticated ingress, and an outsider has no secret with which to reach it.

    Deliberately one-way: it only fills an *unknown* id and never overwrites one
    the daemon already established, so a hook cannot rekey a bound session.
    """
    if session.record.backend not in {"claude", "codex"}:
        return
    if not conversation_unbound(session):
        return
    native_id = str(
        payload.get("session_id")
        or payload.get("sessionId")
        or payload.get("thread-id")
        or payload.get("thread_id")
        or ""
    )
    if not _HOOK_NATIVE_ID.fullmatch(native_id):
        return
    if native_id == session.record.id:
        # The placeholder echoed back is not evidence of anything.
        return
    session.record.native_session_id = native_id
    if not session.agent_lifecycle_id:
        session.agent_lifecycle_id = native_id
    await events.emit(
        "agent_native_id_bound",
        session_id=session.record.id,
        source="hook",
        scope="root",
        backend=session.record.backend,
        native_session_id=native_id,
    )
    # This is the moment a provisional follow can be judged: the conversation now
    # has a proven id, so the guessed file is either confirmed (promote it to a
    # real binding and write the history row) or refuted (drop it and re-bind by
    # exact match). Routed through a sink because resolution belongs to the
    # SessionManager, which owns observers and history.
    resolve = getattr(session, "provisional_binding_sink", None)
    if callable(resolve):
        resolve()
    session.publish_update()


class RolloverDecision(NamedTuple):
    """What a root SessionStart means for this session's conversation identity.

    ``roll_to`` names the successor conversation to adopt. ``refused`` carries a
    candidate that provably is not this PTY replacing its own conversation —
    logged rather than rolled, so the identity a nested child would steal stays
    put while the refusal remains observable in the ledger.
    """

    roll_to: str | None = None
    refused: str | None = None
    refusal_reason: str | None = None


def _same_directory(left: str, right: str) -> bool:
    try:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
            os.path.abspath(right)
        )
    except (OSError, ValueError):
        return False


def conversation_rollover_decision(
    session: Session, event_type: str, payload: dict[str, Any]
) -> RolloverDecision:
    """The new conversation id when this hook proves the CLI replaced its own.

    `/clear` mints a fresh session id and starts a fresh transcript file, then
    fires `SessionStart` — over this session's loopback ingress, with this
    session's own secret — reporting the id it is now writing. That is the
    strongest identity evidence available and, unlike the filesystem watcher, it
    is unaffected by sibling sessions sharing the cwd.

    The ingress and the secret authenticate the *session*, not the process:
    a nested `claude` launched by this session's own tool call inherits the hook
    wiring and reports itself over the same channel. Two facts separate it from
    the CLI replacing its own conversation, and both refuse the roll:

    - ``source == "startup"`` is a fresh process announcing itself. An in-place
      replacement (`/clear`, in-CLI `/resume`) reports ``clear``/``resume``;
      `compact` keeps the id and never reaches the comparison. A bound session's
      own CLI cannot fire a root ``startup`` — its process-level restarts go
      through daemon lifecycle (demote/promote), never this hook.
    - A cwd that is not this session's. Replacing a conversation cannot move the
      CLI's working directory; a child probing from a scratch dir cannot fake it.

    Measured live 2026-07-31: a session whose task spawned probe children rolled
    its identity 14 times onto their conversations and spent most of its life
    showing their unanswerable "awaiting approval".

    Distinct from `_bind_native_id_from_hook`, which fills an *unknown* id and is
    still forbidden from rekeying a bound session. Replacing a bound conversation
    is a lifecycle transition (a new agent run), not a rebind.
    """
    nothing = RolloverDecision()
    if event_type != "SessionStart" or hook_event_scope(event_type, payload) != "root":
        return nothing
    if session.record.backend not in {"claude", "codex"}:
        return nothing
    current = session.record.native_session_id or ""
    if not _HOOK_NATIVE_ID.fullmatch(current):
        # Nothing bound yet: that is the bind path's job, not a rollover.
        return nothing
    native_id = str(payload.get("session_id") or payload.get("sessionId") or "")
    if not _HOOK_NATIVE_ID.fullmatch(native_id) or native_id == current:
        return nothing
    if session.agent_lifecycle_id == native_id:
        return nothing
    source = str(payload.get("source") or "")
    if source == "startup":
        return RolloverDecision(refused=native_id, refusal_reason="foreign_process_startup")
    hook_cwd = str(payload.get("cwd") or "")
    session_cwd = session.record.run_cwd or session.record.cwd
    if hook_cwd and session_cwd and not _same_directory(hook_cwd, session_cwd):
        return RolloverDecision(refused=native_id, refusal_reason="cwd_mismatch")
    return RolloverDecision(roll_to=native_id)


def foreign_conversation_hook_id(session: Session, payload: dict[str, Any]) -> str | None:
    """The foreign conversation a hook speaks for, or None when it is this session's.

    A hook authenticated with this session's secret still names the conversation
    it describes (`session_id` in every Claude hook payload). Once this session
    is bound, an id that is neither the bound conversation nor the session's own
    spawn conversation belongs to another process sharing the wiring — a nested
    child CLI — and must not drive this session's state. The spawn conversation
    (`record.id`, minted via ``--session-id``) is deliberately never foreign:
    it speaking while the session is bound elsewhere is identity-corruption
    evidence, which the heal path acts on rather than discarding.
    """
    if session.record.backend != "claude":
        return None
    if conversation_unbound(session):
        return None
    native_id = str(payload.get("session_id") or payload.get("sessionId") or "")
    if not _HOOK_NATIVE_ID.fullmatch(native_id):
        return None
    if native_id == (session.record.native_session_id or ""):
        return None
    if native_id == session.record.id:
        return None
    return native_id


# --- standing-activity extraction ------------------------------------------
#
# Annotation management for the standing-engagement axis (see session.py and
# status-detection.md § Standing-activity annotations). Everything here is
# gated on live observation: historical catch-up must not re-arm last week's
# loop, and an annotation set that was live before a daemon restart survives
# via the record snapshot instead.


def _session_now(session: Session) -> float:
    """Wall clock, honoring the replay harness's virtual clock when present."""
    clock = getattr(session, "clock", None)
    if clock is not None:
        wall = getattr(clock, "wall", None)
        if callable(wall):
            return float(wall())
    return time.time()


def _standing_now(session: Session, event: dict[str, Any]) -> float:
    ts = _event_timestamp(event)
    return ts if ts is not None else _session_now(session)


def _standing_activity_count(session: Session, kind: str) -> int:
    for activity in session.record.standing_activity:
        if activity.kind == kind:
            return int(activity.count)
    return 0


def _standing_detail(value: Any) -> str | None:
    text = str(value or "").strip()
    return text[:STANDING_DETAIL_MAX_CHARS] or None


def _refresh_subagents(
    session: Session,
    *,
    source: str,
    evidence: str,
    now: float,
    count: int | None = None,
    create: bool = True,
) -> bool:
    """Refresh (or, when ``create``, open) the subagents annotation.

    ``create=False`` is the refresh-only tier: once lifecycle hooks own the
    count, transcript records for the same subagent arrive *later* on their
    slower channel, and letting a trailing Task tool_result re-open an
    annotation the hooks already cleared flaps it (observed live 2026-07-31:
    cleared by SubagentStop, re-added by the trailing completion record).
    """
    if not create and _standing_activity_count(session, "subagents") == 0:
        return False
    return set_standing_activity(
        session,
        "subagents",
        source=source,
        evidence=evidence,
        expires_at=now + SUBAGENT_QUIET_SECONDS,
        count=count,
        now=now,
    )


def _drop_subagent(session: Session, *, source: str, evidence: str, now: float) -> bool:
    count = _standing_activity_count(session, "subagents") - 1
    if count <= 0:
        return clear_standing_activity(session, "subagents", evidence=evidence, now=now)
    return _refresh_subagents(session, source=source, evidence=evidence, now=now, count=count)


def _background_open(session: Session) -> dict[str, str | None]:
    """Open background launches this run: tool_use id -> task id (once known)."""
    state = _observation_state(session)
    tasks = state.setdefault("background_open", {})
    return tasks  # type: ignore[no-any-return]


def _sync_background_annotation(session: Session, *, evidence: str, now: float) -> bool:
    open_tasks = _background_open(session)
    if not open_tasks:
        # No opens tracked this run. An annotation may still exist (adopted from
        # a pre-restart snapshot, or PTY-corroborated); a close without a match
        # decrements it below in _close_background_task instead.
        return False
    return set_standing_activity(
        session,
        "background_tasks",
        source="transcript",
        evidence=evidence,
        expires_at=now + BACKGROUND_TASKS_TTL_SECONDS,
        count=len(open_tasks),
        now=now,
    )


def _close_background_task(
    session: Session,
    *,
    evidence: str,
    now: float,
    tool_use_id: str | None = None,
    task_id: str | None = None,
) -> bool:
    open_tasks = _background_open(session)
    matched = None
    if tool_use_id and tool_use_id in open_tasks:
        matched = tool_use_id
    elif task_id:
        matched = next((key for key, value in open_tasks.items() if value == task_id), None)
    if matched is not None:
        open_tasks.pop(matched, None)
        if open_tasks:
            return _sync_background_annotation(session, evidence=evidence, now=now)
        return clear_standing_activity(session, "background_tasks", evidence=evidence, now=now)
    # A completion for a launch this run never tracked (state lost across a
    # daemon restart): the annotation itself is the only count there is.
    count = _standing_activity_count(session, "background_tasks")
    if count <= 0:
        return False
    if count == 1:
        return clear_standing_activity(session, "background_tasks", evidence=evidence, now=now)
    return set_standing_activity(
        session,
        "background_tasks",
        source="transcript",
        evidence=evidence,
        expires_at=now + BACKGROUND_TASKS_TTL_SECONDS,
        count=count - 1,
        now=now,
    )


def _extract_standing_tool_use(
    session: Session, event: dict[str, Any], name: str, block: dict[str, Any]
) -> None:
    """Annotation lifecycle from one assistant tool_use record (live only)."""
    if getattr(session, "observation_replay", False):
        return
    raw_input = block.get("input")
    tool_input: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}
    now = _standing_now(session, event)
    state = _observation_state(session)
    changed = False
    if name == "ScheduleWakeup":
        if tool_input.get("stop"):
            changed = clear_standing_activity(
                session, "loop", evidence="transcript:ScheduleWakeup:stop", now=now
            )
        else:
            try:
                delay = float(tool_input.get("delaySeconds") or 0.0)
            except (TypeError, ValueError):
                delay = 0.0
            delay = min(max(delay, LOOP_DELAY_MIN_SECONDS), LOOP_DELAY_MAX_SECONDS)
            changed = set_standing_activity(
                session,
                "loop",
                source="transcript",
                evidence="transcript:ScheduleWakeup",
                expires_at=now + delay + STANDING_ACTIVITY_TTL_SLACK_SECONDS,
                count=1,
                detail=_standing_detail(tool_input.get("reason")),
                now=now,
            )
    elif name == "CronCreate":
        detail = _standing_detail(tool_input.get("cron"))
        if detail and tool_input.get("recurring") is False:
            detail = f"{detail} (once)"
        changed = set_standing_activity(
            session,
            "cron",
            source="transcript",
            evidence="transcript:CronCreate",
            expires_at=now + CRON_JOB_LIFETIME_SECONDS + STANDING_ACTIVITY_TTL_SLACK_SECONDS,
            count=_standing_activity_count(session, "cron") + 1,
            detail=detail,
            now=now,
        )
    elif name == "CronDelete":
        count = _standing_activity_count(session, "cron") - 1
        if count <= 0:
            changed = clear_standing_activity(
                session, "cron", evidence="transcript:CronDelete", now=now
            )
        else:
            changed = set_standing_activity(
                session,
                "cron",
                source="transcript",
                evidence="transcript:CronDelete",
                expires_at=now + CRON_JOB_LIFETIME_SECONDS + STANDING_ACTIVITY_TTL_SLACK_SECONDS,
                count=count,
                now=now,
            )
    elif name == "CronList":
        if _standing_activity_count(session, "cron"):
            set_standing_activity(
                session,
                "cron",
                source="transcript",
                evidence="transcript:CronList",
                expires_at=now + CRON_JOB_LIFETIME_SECONDS + STANDING_ACTIVITY_TTL_SLACK_SECONDS,
                now=now,
            )
    elif name == "Bash" and tool_input.get("run_in_background"):
        tool_use_id = str(block.get("id") or "")
        if tool_use_id:
            _background_open(session).setdefault(tool_use_id, None)
            changed = _sync_background_annotation(
                session, evidence="transcript:Bash:run_in_background", now=now
            )
    elif name == "TaskStop":
        task_id = str(tool_input.get("task_id") or "")
        if task_id:
            changed = _close_background_task(
                session, evidence="transcript:TaskStop", now=now, task_id=task_id
            )
    elif name in {"Task", "Agent"}:
        # Fallback tier: hooks own the count once a SubagentStart has arrived
        # this run; transcript launches then only refresh recency.
        hooks_seen = bool(state.get("subagent_hooks_seen"))
        launch_count: int | None = (
            None if hooks_seen else _standing_activity_count(session, "subagents") + 1
        )
        changed = _refresh_subagents(
            session,
            source="transcript",
            evidence="transcript:Task",
            now=now,
            count=launch_count,
            create=not hooks_seen,
        )
    if changed:
        _publish_update(session)


def _extract_standing_tool_result(
    session: Session, event: dict[str, Any], tool: str, tool_use_id: str, detail: str
) -> None:
    """Annotation lifecycle from one tool_result record (live only)."""
    if getattr(session, "observation_replay", False):
        return
    now = _standing_now(session, event)
    changed = False
    if tool == "Bash":
        open_tasks = _background_open(session)
        if tool_use_id in open_tasks and open_tasks[tool_use_id] is None:
            match = _BACKGROUND_TASK_ID.search(detail)
            if match:
                open_tasks[tool_use_id] = match.group(1)
    elif tool in {"Task", "Agent"}:
        state = _observation_state(session)
        if state.get("subagent_hooks_seen"):
            changed = _refresh_subagents(
                session,
                source="transcript",
                evidence="transcript:Task:completed",
                now=now,
                create=False,
            )
        else:
            changed = _drop_subagent(
                session, source="transcript", evidence="transcript:Task:completed", now=now
            )
    if changed:
        _publish_update(session)


def _extract_standing_task_notifications(
    session: Session, event: dict[str, Any], text: str
) -> None:
    """Close background launches named by <task-notification> user records."""
    if getattr(session, "observation_replay", False) or "<task-notification>" not in text:
        return
    now = _standing_now(session, event)
    changed = False
    tool_use_ids = _TASK_NOTIFICATION_TOOL_USE.findall(text)
    task_ids = _TASK_NOTIFICATION_TASK.findall(text)
    for tool_use_id in tool_use_ids:
        changed = (
            _close_background_task(
                session, evidence="transcript:task_notification", now=now,
                tool_use_id=tool_use_id,
            )
            or changed
        )
    if not tool_use_ids:
        for task_id in task_ids:
            changed = (
                _close_background_task(
                    session, evidence="transcript:task_notification", now=now, task_id=task_id
                )
                or changed
            )
    if changed:
        _publish_update(session)


def _apply_subagent_hook(session: Session, event_type: str) -> None:
    """SubagentStart/SubagentStop lifecycle hooks own the subagent count."""
    if getattr(session, "observation_replay", False):
        return
    now = _session_now(session)
    state = _observation_state(session)
    changed = False
    if event_type == "SubagentStart":
        state["subagent_hooks_seen"] = True
        changed = _refresh_subagents(
            session,
            source="hook",
            evidence="hook:SubagentStart",
            now=now,
            count=_standing_activity_count(session, "subagents") + 1,
        )
    elif event_type == "SubagentStop":
        changed = _drop_subagent(
            session, source="hook", evidence="hook:SubagentStop", now=now
        )
    if changed:
        _publish_update(session)


async def apply_hook_observation(
    session: Session,
    event_type: str,
    payload: dict[str, Any],
    events: EventBus,
) -> None:
    scope = hook_event_scope(event_type, payload)
    # A foreign conversation's hook must not move this session's state — its
    # PermissionRequest raises an "awaiting approval" for a dialog that is not on
    # this screen and that nothing here can ever answer. Checked after the caller
    # ran the rollover decision, so a `/clear` successor has already been adopted
    # and reads as this session's own by the time it gets here.
    foreign_id = foreign_conversation_hook_id(session, payload)
    if foreign_id is not None:
        counters = getattr(session, "status_health_counters", None)
        if isinstance(counters, dict):
            counters["foreign_hook_ignored"] = counters.get("foreign_hook_ignored", 0) + 1
        transitions = getattr(session, "state_transitions", None)
        if transitions is not None:
            transitions.append(
                {
                    "ts": time.time(),
                    "kind": "foreign_conversation_hook_ignored",
                    "event": event_type,
                    "native_session_id": foreign_id,
                }
            )
        await events.emit(
            "foreign_conversation_hook_ignored",
            session_id=session.record.id,
            source="hook",
            scope=scope,
            kind=event_type,
            native_session_id=foreign_id,
        )
        return
    if scope == "subagent":
        # Lifecycle hooks manage the `subagents` annotation before the scope
        # early-return; the foreign-conversation filter above has already run,
        # so a nested child's fleet can never count here.
        _apply_subagent_hook(session, event_type)
        await events.emit(
            "subagent_activity",
            session_id=session.record.id,
            source="hook",
            scope="subagent",
            kind=event_type,
        )
        return

    # Tool-activity and turn-start hooks only drive state as a fallback: when the
    # transcript observer is authoritative it already records the same boundaries
    # in order, and a late/reordered hook would only race it (reopening a finished
    # turn as "working"). Turn-end, approval, and notification hooks below stay
    # live because they carry signals the transcript lacks or delivers later.
    if event_type == "SessionStart":
        await _bind_native_id_from_hook(session, payload, events)
        # The CLI announcing its own start is the only positive evidence a session
        # that has never run a turn can offer, and delivery readiness needs it:
        # everything else it reads is about *completing* a turn. Recorded as a
        # plain fact with no timestamp, because the settle it gates is measured
        # against the tracker's own clock (`delivery_readiness.py`).
        _observation_state(session)["session_start_seen"] = True
        await _transition(session, events, "idle", source="hook", evidence="hook:SessionStart")
    elif event_type in {"UserPromptSubmit", "turn_started", "task_started"}:
        # Capture the request itself before the authority check below, which returns
        # early whenever the transcript is driving state — i.e. for every healthy
        # session. This is the only place the user's prompt is available without
        # reading the transcript, and titling from it is what gives a pane a name
        # before its first turn finishes.
        _remember_user_prompt(session, payload)
        if _transcript_authoritative(session):
            return
        await _begin_root_turn(session, events, source="hook", evidence=f"hook:{event_type}")
    elif event_type == "PreToolUse":
        if _transcript_authoritative(session):
            return
        await _begin_root_turn(session, events, source="hook", evidence="hook:PreToolUse")
        _observation_state(session)["turn_saw_activity"] = True
        tool = str(payload.get("tool_name") or payload.get("name") or "tool")
        await _transition(
            session, events, "working", tool, source="hook", evidence="hook:PreToolUse"
        )
        await events.emit(
            "tool_use",
            session_id=session.record.id,
            source="hook",
            scope="root",
            tool=tool,
        )
    elif event_type in {"PostToolUse", "PostToolUseFailure"}:
        if _transcript_authoritative(session):
            return
        await _transition(
            session, events, "working", source="hook", evidence=f"hook:{event_type}"
        )
    elif event_type in {"PermissionRequest", "approval_needed", "approval-requested"}:
        tool = str(payload.get("tool_name") or payload.get("message") or "approval")
        await _transition(
            session,
            events,
            "awaiting",
            tool,
            source="hook",
            evidence=f"hook:{event_type}",
            awaiting_reason="approval",
        )
        await events.emit(
            "approval_needed",
            session_id=session.record.id,
            source="hook",
            scope="root",
            kind="approval",
            detail=tool,
        )
    elif event_type == "Notification":
        notification = str(payload.get("notification_type") or "")
        if notification == "idle_prompt":
            # "Claude is waiting for your input" fires once the turn is over and the
            # agent is back at the prompt. That is "ready", not a blocking approval:
            # bucketing it as awaiting shows a phantom approval on a finished agent.
            # Treat it as a turn boundary (also recovers a missed Stop), but never
            # clobber a genuine approval the user has not yet acted on — unless
            # this session's own screen proves the dialog is gone and the CLI is
            # back at its input prompt, which is exactly what the hook claims.
            if session.record.state != "awaiting" or session_pty_state(session) == "idle":
                await _finish_root_turn(
                    session, events, source="hook", evidence="hook:Notification:idle_prompt"
                )
        elif notification in {"permission_prompt", "elicitation_dialog"}:
            kind = "approval" if notification == "permission_prompt" else "input"
            reason = "approval" if notification == "permission_prompt" else "elicitation"
            detail = str(payload.get("message") or notification)
            await _transition(
                session,
                events,
                "awaiting",
                detail,
                source="hook",
                evidence=f"hook:Notification:{notification}",
                awaiting_reason=reason,
            )
            await events.emit(
                "approval_needed",
                session_id=session.record.id,
                source="hook",
                scope="root",
                kind=kind,
                detail=detail,
            )
        elif notification in {"rate_limit", "rate_limited"}:
            await _transition(
                session,
                events,
                "awaiting",
                "rate_limit",
                source="hook",
                evidence=f"hook:Notification:{notification}",
                awaiting_reason="rate_limit",
            )
            await events.emit(
                "rate_limited",
                session_id=session.record.id,
                source="hook",
                scope="root",
            )
    elif event_type in {"Stop", "turn_ended", "agent-turn-complete", "task_complete"}:
        # Codex has no SessionStart, so its turn-end notify is the only authenticated
        # place it ever names its own thread. Bind before closing the turn so the
        # transcript can be exact-matched and catch-up replays the turn that just ran.
        await _bind_native_id_from_hook(session, payload, events)
        await _finish_root_turn(session, events, source="hook", evidence=f"hook:{event_type}")
    elif event_type == "SessionEnd":
        await _finish_root_turn(
            session,
            events,
            source="hook",
            outcome=str(payload.get("reason") or "session_ended"),
            force=True,
            evidence="hook:SessionEnd",
        )


async def _transition(
    session: Session,
    events: EventBus,
    state: SessionState,
    detail: str | None = None,
    *,
    source: str = "transcript",
    force: bool = False,
    evidence: str | None = None,
    inferred: bool | None = None,
    awaiting_reason: str | None = None,
    idle_reason: str | None = None,
) -> bool:
    if getattr(session, "observation_replay", False):
        return False
    # force: interrupt/abort evidence exists only in the transcript; hooks never
    # deliver it, so it may reclaim authority from an earlier hook state. The
    # shared contract resets arbitration before applying.
    previous = session.record.state
    transition = getattr(session, "transition", None)
    if callable(transition):
        accepted = transition(
            state,
            detail,
            source=source,
            evidence=evidence,
            inferred=inferred,
            awaiting_reason=awaiting_reason,
            idle_reason=idle_reason,
            force=force,
        )
        if not accepted:
            return False
    else:
        if force and hasattr(session, "state_source_priority"):
            session.state_source_priority = -1
        session.record.state = state
        session.record.state_detail = detail
        if hasattr(session.record, "awaiting_reason"):
            session.record.awaiting_reason = awaiting_reason if state == "awaiting" else None
        _publish_update(session)
    if previous != state:
        await events.emit(
            "state_changed",
            session_id=session.record.id,
            source=source,
            previous=previous,
            state=state,
            detail=detail,
            awaiting_reason=awaiting_reason if state == "awaiting" else None,
            proof=transition_proof(source, inferred),
        )
    return True


async def _claude(session: Session, event: dict[str, Any], events: EventBus) -> None:
    event_type = event.get("type")
    message = event.get("message") or {}
    if event.get("isSidechain") is True:
        # A sidechain record is proof a subagent is running right now: refresh
        # the annotation's recency, creating it at count 1 only while no
        # lifecycle hook owns the count (hooks clear faster than the transcript
        # delivers, so a trailing sidechain record must not re-open the set).
        if not getattr(session, "observation_replay", False) and _refresh_subagents(
            session,
            source="transcript",
            evidence="transcript:sidechain",
            now=_standing_now(session, event),
            create=not _observation_state(session).get("subagent_hooks_seen"),
        ):
            _publish_update(session)
        block_types = [
            str(block.get("type") or "unknown")
            for block in message.get("content") or []
            if isinstance(block, dict)
        ]
        await events.emit(
            "subagent_activity",
            session_id=session.record.id,
            source="transcript",
            scope="subagent",
            kind=str(event_type or "activity"),
            block_types=sorted(set(block_types)),
        )
        return
    if event_type == "user":
        content = message.get("content")
        text = _claude_user_text(content)
        if event.get("isMeta") is True or _is_local_command_text(text):
            await _complete_empty_hook_turn(session, events)
            return
        if _is_interrupt_text(text):
            await _finish_root_turn(
                session,
                events,
                source="transcript",
                outcome="interrupted",
                force=True,
                evidence="interrupt_marker",
            )
            return
        _extract_standing_task_notifications(session, event, text)
        has_tool_result = isinstance(content, list) and any(
            block.get("type") == "tool_result" for block in content if isinstance(block, dict)
        )
        if isinstance(content, str) or (
            isinstance(content, list)
            and any(
                block.get("type") in {"text", "image"}
                for block in content
                if isinstance(block, dict)
            )
            and not has_tool_result
        ):
            # A fresh prompt while blocked means the user answered and moved on.
            await _resume_from_awaiting(session, events, event, evidence="user_prompt_record")
            await _begin_root_turn(
                session, events, source="transcript", evidence="user_prompt_record"
            )
        elif has_tool_result:
            # The tool actually ran: an approval (or denial) resolved it.
            await _resume_from_awaiting(session, events, event, evidence="tool_result_record")
            session.record.state_detail = None
            await _begin_root_turn(
                session, events, source="transcript", evidence="tool_result_record"
            )
            _observation_state(session)["turn_saw_activity"] = True
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tool_use_id = str(block.get("tool_use_id") or "")
                tool, target = _recall_tool_call(session, tool_use_id)
                result_content = block.get("content")
                if isinstance(result_content, list):
                    detail = " ".join(
                        str(item.get("text") or "")
                        for item in result_content
                        if isinstance(item, dict) and item.get("type") == "text"
                    )
                else:
                    detail = str(result_content or "")
                content_hash, test_outcome = tool_result_evidence(detail)
                await events.emit(
                    "tool_result",
                    session_id=session.record.id,
                    source="transcript",
                    scope="root",
                    tool=tool,
                    call_id=tool_use_id or None,
                    target=target,
                    content_hash=content_hash,
                    test_outcome=test_outcome,
                    success=not bool(block.get("is_error")),
                    exit_code=None,
                    parser_version=OBSERVATION_SCHEMA_VERSION,
                    detail=bounded_detail(detail),
                )
                if tool in {"Agent", "Task"}:
                    await events.emit(
                        "subagent_activity",
                        session_id=session.record.id,
                        source="transcript",
                        scope="subagent",
                        kind="completed",
                    )
                _extract_standing_tool_result(session, event, tool, tool_use_id, detail)
    elif event_type == "assistant":
        # The model produced output again, so nothing is blocking it.
        await _resume_from_awaiting(session, events, event, evidence="assistant_record")
        state = _observation_state(session)
        # A trailing completion record for a turn something else (hook Stop,
        # EventBus-deduped boundary) already closed must not blink the status
        # back to "working" just to close it again: an end_turn assistant record
        # never starts a turn (submission always precedes it), so when the
        # completion was already seen it only confirms the existing boundary.
        trailing_completion = (
            message.get("stop_reason") == "end_turn"
            and not state.get("root_turn_active")
            and state.get("root_completion_seen")
        )
        if not trailing_completion:
            await _begin_root_turn(
                session, events, source="transcript", evidence="assistant_record"
            )
        _observation_state(session)["turn_saw_activity"] = True
        has_text = False
        content = message.get("content") or []
        if isinstance(content, str):
            has_text = bool(content)
            content = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                has_text = True
            elif isinstance(block, dict) and block.get("type") == "tool_use":
                name = str(block.get("name") or "tool")
                tool_use_id = str(block.get("id") or "")
                target, content_hash = tool_call_evidence(block.get("input"))
                _remember_tool_call(session, tool_use_id, name, target)
                await _transition(
                    session, events, "working", name, evidence="tool_use_record"
                )
                await events.emit(
                    "tool_use",
                    session_id=session.record.id,
                    source="transcript",
                    scope="root",
                    tool=name,
                    call_id=tool_use_id or None,
                    target=target,
                    content_hash=content_hash,
                    parser_version=OBSERVATION_SCHEMA_VERSION,
                )
                if name.lower() == "skill" and isinstance(block.get("input"), dict):
                    skill = block["input"].get("skill") or block["input"].get("name")
                    if isinstance(skill, str) and skill.strip():
                        await events.emit(
                            "skill_invoked",
                            session_id=session.record.id,
                            source="transcript",
                            scope="root",
                            backend="claude",
                            tool=name,
                            call_id=tool_use_id or None,
                            skill=skill.strip()[:200],
                            parser_version=OBSERVATION_SCHEMA_VERSION,
                        )
                if name in {"Agent", "Task"}:
                    await events.emit(
                        "subagent_activity",
                        session_id=session.record.id,
                        source="transcript",
                        scope="subagent",
                        kind="started",
                    )
                _extract_standing_tool_use(session, event, name, block)
        usage = message.get("usage") or {}
        if usage:
            session.record.tokens_in = sum(
                int(usage.get(key, 0))
                for key in (
                    "input_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                )
            )
            session.record.tokens_out = int(usage.get("output_tokens", 0))
            model = str(message.get("model") or "")
            window = CLAUDE_CONTEXT_WINDOWS.get(model, 0)
            session.record.context_window = window
            session.record.context_pct = min(1, session.record.tokens_in / window) if window else 0
            session.record.context_peak_pct = max(
                session.record.context_peak_pct, session.record.context_pct
            )
            session.record.model = model or session.record.model
            session.record.measurement_source = "claude-transcript"
            _publish_update(session)
        # Interactive Claude normally appends a turn_duration system record, but
        # print/non-interactive mode can finish at the final assistant message.
        # A text response with end_turn is authoritative; tool-use messages are
        # deliberately left working until their result or a later completion.
        if message.get("stop_reason") == "end_turn" and has_text:
            await _finish_root_turn(
                session, events, source="transcript", evidence="stop_reason=end_turn"
            )
    elif event_type == "system":
        subtype = str(event.get("subtype") or "")
        if subtype == "turn_duration":
            await _finish_root_turn(
                session,
                events,
                source="transcript",
                evidence="system:turn_duration",
                duration_ms=event.get("durationMs"),
            )
        elif subtype in {"compact_boundary", "context_compacted", "compaction"}:
            await events.emit(
                "context_compacted",
                session_id=session.record.id,
                source="transcript",
                scope="root",
                backend="claude",
                capability="explicit_native",
                confidence="high",
                parser_version=OBSERVATION_SCHEMA_VERSION,
            )


async def _codex(session: Session, event: dict[str, Any], events: EventBus) -> None:
    payload = event.get("payload") or {}
    outer_type, payload_type = event.get("type"), payload.get("type")
    state = _observation_state(session)
    if outer_type == "session_meta":
        if payload.get("parent_thread_id"):
            state["codex_scope"] = "subagent"
            await events.emit(
                "subagent_activity",
                session_id=session.record.id,
                source="transcript",
                scope="subagent",
                kind="transcript_attached",
            )
            return
        state["codex_scope"] = "root"
        # A provisional follow must not read identity out of the file it guessed:
        # that would make the guess confirm itself and bypass the hook check that
        # is the only real evidence of which conversation this PTY is running.
        if not provisional_observation(session):
            native_id = payload.get("id") or payload.get("session_id")
            if native_id:
                session.record.native_session_id = str(native_id)
            session.record.model = str(payload.get("model") or "") or session.record.model
        await _transition(session, events, "idle", evidence="session_meta")
    if state.get("codex_scope") == "subagent":
        await events.emit(
            "subagent_activity",
            session_id=session.record.id,
            source="transcript",
            scope="subagent",
            kind=str(payload_type or outer_type or "activity"),
        )
        return
    if payload_type in CODEX_RESUME_PAYLOADS:
        # Tooling or the model is running again, so any approval was answered.
        await _resume_from_awaiting(session, events, event, evidence=str(payload_type))
    if payload_type in {"task_started", "user_message"}:
        await _begin_root_turn(
            session, events, source="transcript", evidence=str(payload_type)
        )
    elif payload_type == "task_complete":
        await _finish_root_turn(
            session,
            events,
            source="transcript",
            evidence="task_complete",
            turn_id=payload.get("turn_id"),
            duration_ms=payload.get("duration_ms"),
        )
    elif payload_type == "turn_aborted":
        await _finish_root_turn(
            session,
            events,
            source="transcript",
            outcome=str(payload.get("reason") or "aborted"),
            evidence="turn_aborted",
            turn_id=payload.get("turn_id"),
            duration_ms=payload.get("duration_ms"),
        )
    elif payload_type == "thread_rolled_back":
        await _finish_root_turn(
            session,
            events,
            source="transcript",
            outcome="rolled_back",
            force=True,
            evidence="thread_rolled_back",
        )
    elif payload_type in {"function_call", "custom_tool_call"}:
        name = str(payload.get("name") or "tool")
        call_id = str(payload.get("call_id") or payload.get("id") or "")
        target, content_hash = tool_call_evidence(
            payload.get("arguments") or payload.get("input")
        )
        _remember_tool_call(session, call_id, name, target)
        await _begin_root_turn(
            session, events, source="transcript", evidence=str(payload_type)
        )
        await _transition(
            session, events, "working", name, evidence=str(payload_type)
        )
        await events.emit(
            "tool_use",
            session_id=session.record.id,
            source="transcript",
            scope="root",
            tool=name,
            call_id=call_id or None,
            target=target,
            content_hash=content_hash,
            parser_version=OBSERVATION_SCHEMA_VERSION,
        )
        explicit_skill = payload.get("skill") or payload.get("skill_name")
        if name.lower() == "skill" and isinstance(explicit_skill, str) and explicit_skill.strip():
            await events.emit(
                "skill_invoked",
                session_id=session.record.id,
                source="transcript",
                scope="root",
                backend="codex",
                tool=name,
                call_id=call_id or None,
                skill=explicit_skill.strip()[:200],
                parser_version=OBSERVATION_SCHEMA_VERSION,
            )
    elif payload_type in {
        "function_call_output",
        "custom_tool_call_output",
        "exec_command_end",
    }:
        call_id = str(payload.get("call_id") or payload.get("id") or "")
        tool, target = _recall_tool_call(session, call_id, str(payload.get("name") or "tool"))
        exit_code_value = payload.get("exit_code")
        try:
            exit_code = int(exit_code_value) if exit_code_value is not None else None
        except (TypeError, ValueError):
            exit_code = None
        success = not bool(payload.get("is_error")) and exit_code in {None, 0}
        detail = str(
            payload.get("output")
            or payload.get("content")
            or payload.get("result")
            or payload.get("message")
            or ""
        )
        content_hash, test_outcome = tool_result_evidence(detail)
        await events.emit(
            "tool_result",
            session_id=session.record.id,
            source="transcript",
            scope="root",
            tool=tool,
            call_id=call_id or None,
            target=target,
            content_hash=content_hash,
            test_outcome=test_outcome,
            success=success,
            exit_code=exit_code,
            duration_ms=payload.get("duration_ms"),
            parser_version=OBSERVATION_SCHEMA_VERSION,
            detail=bounded_detail(detail),
        )
    elif payload_type in {"patch_apply_end", "mcp_tool_call_end", "web_search_end"}:
        tool = {
            "patch_apply_end": "apply_patch",
            "mcp_tool_call_end": "mcp_tool",
            "web_search_end": "web_search",
        }[str(payload_type)]
        success_value = payload.get("success")
        status = str(payload.get("status") or "")
        success = (
            bool(success_value) if success_value is not None else status not in {"failed", "error"}
        )
        call_id = str(payload.get("call_id") or payload.get("id") or "")
        _, target = _recall_tool_call(session, call_id, tool)
        await events.emit(
            "tool_result",
            session_id=session.record.id,
            source="transcript",
            scope="root",
            tool=tool,
            call_id=call_id or None,
            target=target,
            success=success,
            exit_code=None,
            duration_ms=payload.get("duration_ms"),
            parser_version=OBSERVATION_SCHEMA_VERSION,
            detail=status[:4000],
        )
    elif payload_type in {
        "exec_approval_request",
        "apply_patch_approval_request",
        "request_user_input",
    }:
        detail = "input" if payload_type == "request_user_input" else "approval"
        reason = "question" if payload_type == "request_user_input" else "approval"
        await _transition(
            session,
            events,
            "awaiting",
            detail,
            evidence=str(payload_type),
            awaiting_reason=reason,
        )
        await events.emit(
            "approval_needed",
            session_id=session.record.id,
            source="transcript",
            scope="root",
            kind=detail,
        )
    elif payload_type in {"rate_limit", "rate_limited"}:
        await _transition(
            session,
            events,
            "awaiting",
            "rate_limit",
            evidence=str(payload_type),
            awaiting_reason="rate_limit",
        )
        await events.emit(
            "rate_limited",
            session_id=session.record.id,
            source="transcript",
            scope="root",
        )
    elif payload_type == "sub_agent_activity":
        # Codex has no subagent lifecycle hooks, so recency is the only truth:
        # any sub-agent record refreshes (or opens) the annotation and the TTL
        # is the only clear. Count stays 1 — the records carry no fleet size.
        if not getattr(session, "observation_replay", False) and _refresh_subagents(
            session,
            source="transcript",
            evidence="transcript:sub_agent_activity",
            now=_standing_now(session, event),
        ):
            _publish_update(session)
        await events.emit(
            "subagent_activity",
            session_id=session.record.id,
            source="transcript",
            scope="subagent",
            kind=str(payload.get("kind") or "activity"),
            depth=len(payload.get("agent_path") or []),
        )
    elif payload_type == "context_compacted" or outer_type == "compacted":
        # Durable per-session operational telemetry: attributing a stranger's
        # compaction is a claim about this pane that nothing later removes.
        if not provisional_observation(session):
            await events.emit(
                "context_compacted",
                session_id=session.record.id,
                source="transcript",
                scope="root",
                backend="codex",
                capability="explicit_native",
                confidence="high",
                parser_version=OBSERVATION_SCHEMA_VERSION,
            )
    elif payload_type == "token_count":
        # Shown on the pane and copied into the history row at turn end, so this
        # is attribution rather than state and waits for the conversation to be
        # proven. Nothing is lost by waiting: Codex reports cumulative totals, so
        # the first count read after the binding carries the whole turn.
        if provisional_observation(session):
            return
        info = payload.get("info") or payload
        total = info.get("total_token_usage") or {}
        current = info.get("last_token_usage") or total
        session.record.tokens_in = int(total.get("input_tokens", session.record.tokens_in))
        session.record.tokens_out = int(total.get("output_tokens", session.record.tokens_out))
        window = int(info.get("model_context_window") or 0)
        session.record.context_window = window
        current_input = int(current.get("input_tokens") or 0)
        session.record.context_pct = min(1, current_input / window) if window else 0
        session.record.context_peak_pct = max(
            session.record.context_peak_pct, session.record.context_pct
        )
        session.record.model = str(info.get("model") or "") or session.record.model
        session.record.measurement_source = "codex-transcript"
        _publish_update(session)


async def find_codex_transcript(cwd: str, created_at: float, stop: asyncio.Event) -> Path | None:
    root = codex_data_home() / "sessions"
    while not stop.is_set():
        candidates = (
            sorted(
                root.glob("**/rollout-*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True
            )
            if root.exists()
            else []
        )
        for path in candidates[:20]:
            if path.stat().st_mtime + 2 < created_at:
                continue
            try:
                first = path.open("r", encoding="utf-8", errors="replace").readline()
                event = json.loads(first)
                payload = event.get("payload") or {}
                if (
                    not payload.get("parent_thread_id")
                    and str(Path(payload.get("cwd", "")).resolve()).lower()
                    == str(Path(cwd).resolve()).lower()
                ):
                    return path
            except (OSError, json.JSONDecodeError):
                continue
        await asyncio.sleep(0.5)
    return None
