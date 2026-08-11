from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple, assert_never

from .adapters.codex import codex_data_home
from .event_bus import EventBus
from .harness import Backend, descriptor, native_id_matches, reports_lifecycle_hooks
from .models import SessionState
from .scrollback import SCREEN_TAIL_BYTES
from .session import (
    STANDING_ACTIVITY_TTL_SLACK_SECONDS,
    Session,
    clear_standing_activity,
    pty_tail_state,
    pty_tail_waiting_on_background,
    session_cli_state_status,
    set_standing_activity,
    standing_activity_kinds,
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
    "item_completed",
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

OMP_KNOWN_RECORDS = {
    "title",
    "session",
    "message",
    "thinking_level_change",
    "model_change",
    "service_tier_change",
    "compaction",
    "branch_summary",
    "reset_boundary",
    "custom",
    "custom_message",
    "label",
    "title_change",
    "ttsr_injection",
    "credential_pin",
    "session_init",
    "mode_change",
}

# Upstream pi's entry vocabulary, measured against pi 0.74.2's bundled
# `docs/session-format.md` and a real session file. It is omp's set minus the
# records oh-my-pi added after the fork, plus `session_info` (pi's display-name
# entry, which omp spells `title_change`). Kept as its own set rather than
# aliased to OMP_KNOWN_RECORDS so that an omp-only record showing up in a pi
# transcript is reported as unknown-record drift instead of silently accepted.
PI_KNOWN_RECORDS = {
    "session",
    "message",
    "thinking_level_change",
    "model_change",
    "compaction",
    "branch_summary",
    "custom",
    "custom_message",
    "label",
    "session_info",
}

TRANSCRIPT_CLASSIFIER_BACKENDS = frozenset({"claude", "codex", "omp", "pi"})

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

# Re-exported for callers that imported it from here; `claude_models` owns it.
from .claude_models import CLAUDE_CONTEXT_WINDOWS, claude_context_window  # noqa: E402,F401

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
#   reads "Command running in background with ID: <task_id>." A *foreground*
#   Bash that outruns its timeout is moved to the background by the CLI with no
#   `run_in_background` in its input at all - the promotion exists only in the
#   result text ("was moved to the background (ID: <task_id>)"), so the result is
#   the authoritative open for both shapes and the input is only a hint.
# - Completion arrives as `<task-notification>` naming the launch's
#   `<tool-use-id>`, `<task-id>` and a `<status>`. It rides up to three carriers
#   for one completion (verified live 2026-08-06): a `queue-operation` record
#   (`operation: "enqueue"` when the task finishes, `"remove"` when it is handed
#   to the model) with the body in its top-level `content`; an `attachment`
#   record (`attachment.commandMode == "task-notification"`) with the body in
#   `attachment.prompt`; and - only if the CLI gets to deliver it into a turn -
#   a plain user record. A session that finishes its turn before the shell exits
#   never gets the user record at all, which is why the queued carriers are read:
#   reading only the user record left the annotation open for its full 30-minute
#   TTL on every background shell that outlived its turn. Duplicate carriers are
#   the normal case, so closes are idempotent per task rather than decrementing.
# - `TaskStop` input `{task_id}`.
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
# After a SubagentStop, subagent-scoped tool hooks may only refresh (never
# re-create) the annotation for this long: hooks are unordered and retried, so
# the stopped agent's last PostToolUse can land after its stop, and re-opening
# on that straggler would flap a correctly cleared annotation for a full TTL.
SUBAGENT_REOPEN_GRACE_SECONDS = 10.0
STANDING_DETAIL_MAX_CHARS = 120
# Both launch shapes bind a task id from the *result* text: an explicit
# `run_in_background` launch, and a foreground command the CLI moved to the
# background when it outran its timeout (which carries no input flag at all).
_BACKGROUND_TASK_ID = re.compile(
    r"(?:running in background with ID:|moved to the background \(ID:)\s*([A-Za-z0-9_-]+)"
)
_TASK_NOTIFICATION_TOOL_USE = re.compile(r"<tool-use-id>\s*([^<\s]+)\s*</tool-use-id>")
_TASK_NOTIFICATION_TASK = re.compile(r"<task-id>\s*([^<\s]+)\s*</task-id>")
_TASK_NOTIFICATION_MARKER = "<task-notification>"


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
    def __init__(self, path: Path, on_growth: Callable[[], None] | None = None) -> None:
        self.path = path
        self.offset = 0
        self.decoder = IncrementalJsonlDecoder()
        self.prefix: bytes | None = None
        # Called when bytes appear that were not in the file at attach. This is the
        # daemon's only first-hand evidence that the transcript it follows is still
        # being written: `stat().st_mtime` cannot be used for that on Windows, where
        # a live file's last-write time can stay frozen at its creation for hours
        # (see `Session.transcript_growth_ts`). `st_size` remains accurate, and this
        # loop is already polling it.
        self.on_growth = on_growth
        # Content already present at attach is history (resume, promotion after
        # activity), not live agent behavior; events() labels it historical.
        try:
            self.initial_size = path.stat().st_size
        except OSError:
            self.initial_size = 0
        self._caught_up = self.initial_size == 0

    def _note_growth(self) -> None:
        if self.on_growth is not None:
            self.on_growth()

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
                    # Truncated or rewritten under us: whoever owns this file is
                    # demonstrably still writing it.
                    self._note_growth()
                    yield None, True
                if size > self.offset:
                    with self.path.open("rb") as handle:
                        if self.prefix is None or len(self.prefix) < 64:
                            self.prefix = handle.read(min(64, size))
                        handle.seek(self.offset)
                        chunk = handle.read(size - self.offset)
                    self.offset = size
                    # Only bytes past the attach snapshot are evidence of a *live*
                    # writer; replaying what was already there proves nothing.
                    if size > self.initial_size:
                        self._note_growth()
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


async def _dispatch_transcript_event(
    backend: Backend,
    session: Session,
    event: dict[str, Any],
    events: EventBus,
) -> None:
    if backend == "claude":
        await _claude(session, event, events)
    elif backend == "codex":
        await _codex(session, event, events)
    elif backend == "omp" or backend == "pi":
        # pi and oh-my-pi write the same session records — the same
        # `{"type":"session"}` header, the same `id`/`parentId` entry tree, the
        # same `usage`/`cost` shape on an assistant message. pi's set is omp's
        # minus the extras omp added (reset_boundary, credential_pin,
        # title_change, mode_change, ttsr_injection, service_tier_change), so
        # the omp reader is not "close enough" here, it is the same reader over
        # a subset. The omp-only branches are simply unreachable for pi, and
        # `_adapter_context_window` already resolves the window off the session's own
        # adapter rather than assuming omp's models.db.
        await _omp(session, event, events)
    elif backend == "opencode":
        # opencode keeps no transcript file, so no transcript event can arrive
        # for it. Its state comes from the plugin's hooks.
        return
    elif backend == "shell":
        return
    else:
        assert_never(backend)


async def observe_transcript(
    session: Session, path: Path, events: EventBus, stop: asyncio.Event
) -> None:
    session.record.parser_status = "watching"
    session.record.parser_schema_version = OBSERVATION_SCHEMA_VERSION
    session.record.parser_diagnostic = f"tailing {path.name}"
    _publish_update(session)

    def note_growth() -> None:
        # Dated here rather than from the file's own timestamp; see
        # `Session.transcript_growth_ts`. Bound to `path` by the caller resetting
        # the stamp whenever it re-aims the observer at a different file.
        session.transcript_growth_ts = time.time()

    tailer = JsonlTailer(path, on_growth=note_growth)
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
            observed_at = time.time()
            if (
                ts is not None
                and math.isfinite(ts)
                and 0.0 < ts <= observed_at + HISTORICAL_TIMESTAMP_SLACK_SECONDS
            ):
                session.transcript_record_ts = max(
                    float(getattr(session, "transcript_record_ts", 0.0)), ts
                )
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
                    await _dispatch_transcript_event(
                        session.record.backend, session, event, _NULL_EVENTS
                    )
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
            await _dispatch_transcript_event(session.record.backend, session, event, events)
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
            # of `observation_supported` and which an idle session cannot
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


def classify_transcript_event(backend: Backend, event: dict[str, Any]) -> tuple[bool, str]:
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
    if backend == "omp":
        return outer in OMP_KNOWN_RECORDS, f"omp:{outer}"
    if backend == "pi":
        # Signed with pi's own prefix so unknown-record drift is attributed to
        # the harness that produced it rather than pooled with omp's.
        return outer in PI_KNOWN_RECORDS, f"pi:{outer}"
    if backend == "opencode":
        return False, f"opencode:{outer}"
    if backend == "shell":
        return False, f"shell:{outer}"
    assert_never(backend)


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


def transcript_tail_turn_state(backend: Backend, path: Path) -> str:
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


def tail_turn_state(backend: Backend, records: list[dict[str, Any]]) -> str:
    """Classify already-parsed tail records; shared with the replay harness."""
    if backend == "claude":
        return _claude_tail_state(records)
    if backend == "codex":
        return _codex_tail_state(records)
    if backend == "omp" or backend == "pi":
        return _omp_tail_state(records)
    if backend == "opencode":
        return "unknown"
    if backend == "shell":
        return "unknown"
    assert_never(backend)


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


def _omp_chain(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return OMP's active root-to-leaf branch from append-ordered records."""
    entries = {
        str(event["id"]): event
        for event in records
        if isinstance(event.get("id"), str) and event.get("id")
    }
    leaf = next(
        (
            str(event["id"])
            for event in reversed(records)
            if isinstance(event.get("id"), str) and event.get("id")
        ),
        None,
    )
    chain: list[dict[str, Any]] = []
    visited: set[str] = set()
    while leaf and leaf not in visited:
        visited.add(leaf)
        event = entries.get(leaf)
        if event is None:
            break
        chain.append(event)
        parent = event.get("parentId")
        leaf = parent if isinstance(parent, str) and parent else None
    chain.reverse()
    return chain


def _omp_message_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [
        block
        for block in content
        if isinstance(block, dict) and block.get("type") == "toolCall"
    ]


def _omp_chain_turn_state(records: list[dict[str, Any]]) -> str:
    pending: set[str] = set()
    verdict = "unknown"
    for event in records:
        event_type = event.get("type")
        if event_type == "reset_boundary":
            pending.clear()
            verdict = "ended"
            continue
        if event_type == "custom":
            if event.get("customType") != "session_exit":
                continue
            data = event.get("data")
            if not isinstance(data, dict):
                continue
            pending_calls = data.get("pendingToolCalls")
            if data.get("kind") != "normal" or (
                isinstance(pending_calls, list) and bool(pending_calls)
            ):
                pending.clear()
                verdict = "ended"
            continue
        if event_type != "message":
            continue
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role in {"user", "developer"}:
            verdict = "open"
        elif role == "toolResult":
            call_id = message.get("toolCallId")
            if isinstance(call_id, str):
                pending.discard(call_id)
            verdict = "open"
        elif role == "assistant":
            calls = _omp_message_tool_calls(message)
            pending.update(str(call.get("id")) for call in calls if call.get("id"))
            stop_reason = str(message.get("stopReason") or "")
            if calls or stop_reason in {"toolUse", "tool_use"}:
                verdict = "open"
            elif stop_reason in {"stop", "end_turn", "aborted", "error", "length"}:
                verdict = "ended"
    return "open" if pending else verdict


def _omp_tail_state(records: list[dict[str, Any]]) -> str:
    return _omp_chain_turn_state(_omp_chain(records))


async def _record_parser_observation(
    session: Session,
    events: EventBus,
    recognized: bool,
    signature: str,
) -> None:
    record = session.record
    previous_status = record.parser_status
    stale_reason = getattr(session, "observation_stale_reason", None)
    if record.observation_stale_since is not None and stale_reason in {
        None,
        "transcript_stale",
        "transcript_missing",
    }:
        # A record read from the followed transcript is proof it is the live
        # conversation after an inferred liveness failure. Explicit conversation
        # mismatches are not retractable through the file the CLI abandoned.
        record.observation_stale_since = None
        record.observation_diagnostic = None
        session.observation_stale_reason = None
        ledger = getattr(session, "state_transitions", None)
        if ledger is not None:
            ledger.append(
                {
                    "ts": time.time(),
                    "kind": "observation_liveness_restored",
                    "reason": "transcript_record_read",
                }
            )
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


def _first_compaction_evidence(
    session: Session, backend: str, compaction_id: object
) -> bool:
    """Accept one event when hooks and transcripts name the same compaction.

    OMP emits ``session_compact`` before appending the matching transcript
    entry.  Both are explicit evidence, but they describe one boundary.  Keep
    the native identity in the run-scoped observation state so whichever source
    arrives first wins without making source precedence part of telemetry.
    """
    identity = str(compaction_id or "").strip()
    if not identity:
        return True
    state = _observation_state(session)
    seen = state.setdefault("compaction_evidence_ids", {})
    if not isinstance(seen, dict):
        seen = {}
        state["compaction_evidence_ids"] = seen
    key = f"{backend}:{identity}"
    if key in seen:
        return False
    seen[key] = None
    while len(seen) > 256:
        seen.pop(next(iter(seen)))
    return True


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
    """True while the ordered transcript has reported since the latest turn hook.

    Parser status measures schema confidence only. It says nothing about whether
    the followed file is alive, which is the fact needed for source arbitration.
    The tailer's growth stamp is first-hand evidence that ordered records are still
    arriving. A transcript owns boundaries after it has grown at or after the most
    recent root hook whose event must also produce transcript records. Until then,
    hooks remain the conservative fallback.

    This comparison also absorbs every former staleness exception. A missing or
    abandoned file cannot advance its growth stamp; a refused or unadoptable
    rollover advances the hook stamp while the retired file stays still. The
    diagnostics flag remains for delivery and incident reporting, but it does not
    participate in precedence.
    """
    growth = float(getattr(session, "transcript_growth_ts", 0.0))
    latest_turn_hook = float(getattr(session, "last_turn_hook_ts", 0.0))
    return growth > 0.0 and growth >= latest_turn_hook


def _measurements_publishable(session: Session) -> bool:
    """Whether transcript measurements are attributable and schema-confident."""
    return (
        not provisional_observation(session)
        and getattr(session.record, "parser_status", "") != "degraded"
    )


def session_pty_state(session: Session) -> str:
    """What this session's CLI is showing, or "unknown" without a scrollback."""
    scrollback = getattr(session, "scrollback", None)
    if scrollback is None:
        return "unknown"
    try:
        return pty_tail_state(
            scrollback.tail_bytes(SCREEN_TAIL_BYTES).decode("utf-8", "replace"),
            backend=session.record.backend,
            cli_state_status=session_cli_state_status(session),
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
    # Ordered proof of progress retires an approval that has not yet surfaced,
    # whatever the displayed state is: a delegated approval is answered while the
    # session still reads `working`, so waiting for `awaiting` would never see it.
    note_activity_evidence(session, f"activity:{evidence}")
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
    # Same clock the turn is *closed* against, so the two ends of one measurement
    # cannot come from different time sources under the replay harness.
    state["turn_started_at"] = _session_now(session)
    # Mirrored onto the record so a client can age the *turn* rather than the
    # state, which restarts on every tool call and approval inside that turn.
    session.record.turn_started_at = state["turn_started_at"]
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
    return (
        "waiting_on_background"
        if pty_tail_waiting_on_background(tail, backend=session.record.backend)
        else None
    )


#: Turns longer than this are treated as an unclosed boundary rather than a real
#: measurement. A `turn_started` whose completion was missed stays open until some
#: later evidence closes it, which would otherwise report "this turn took 9 hours"
#: on a session that was simply idle overnight.
MAX_TURN_DURATION_SECONDS = 6 * 60 * 60


def _record_turn_duration(
    session: Session, state: dict[str, Any], payload: dict[str, Any]
) -> None:
    """Stamp the completed turn's wall-clock duration on the record and event.

    The record field is what a sidebar reads for a ready session ("the last turn
    took 72s"); the event field is what consumers that never hold a record read.
    A turn with no recorded start, or one long enough to be a missed boundary,
    leaves the previous measurement alone rather than replacing it with a lie.
    """
    started = float(state.get("turn_started_at") or 0.0)
    state["turn_started_at"] = 0.0
    session.record.turn_started_at = None
    # A harness that reports its own turn duration is more authoritative than the
    # daemon's wall clock, which also counts the lag before the boundary was seen.
    reported = payload.get("duration_ms")
    if isinstance(reported, int | float) and not isinstance(reported, bool) and reported > 0:
        session.record.last_turn_ms = float(reported)
        return
    if started <= 0.0:
        return
    elapsed = max(0.0, _session_now(session) - started)
    if elapsed > MAX_TURN_DURATION_SECONDS:
        log.debug(
            "discarding implausible turn duration",
            extra={"session": session.record.id, "seconds": round(elapsed, 1)},
        )
        return
    session.record.last_turn_ms = round(elapsed * 1000.0, 1)
    payload["duration_ms"] = session.record.last_turn_ms


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
    _record_turn_duration(session, state, payload)
    if transition_proof(source, inferred) == "inferred":
        # Recovery inferences stay visible in the event stream, not only the ledger.
        payload.setdefault("inferred", True)
    if outcome == "completed":
        pty_wait = _background_wait_reason(session)
        if (
            pty_wait
            and not getattr(session, "observation_replay", False)
            # Refresh only - this tier may never *open* the annotation. The
            # screen is a 32 KiB append-only window of redraw traffic, so the
            # footer drawn while a task genuinely ran is still matchable minutes
            # after it finished; creating on it resurrected annotations the
            # transcript had just positively closed (measured live 2026-08-06:
            # `transcript:task_notification` removed it, `pty:background_wait_marker`
            # re-added it 29 s later with a fresh 30-minute TTL, and nothing but
            # that TTL could clear it again). Corroboration was always the stated
            # role; `set_standing_activity` creating when absent is what quietly
            # made it a source. Same rule as the subagent tier's `create=False`.
            and _standing_activity_count(session, "background_tasks") > 0
        ):
            # It knows *that* tasks run, not how many, so it refreshes recency
            # without clobbering a transcript-derived count or detail.
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
    if _session_now(session) - started > EMPTY_HOOK_TURN_WINDOW_SECONDS:
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
        return not native_id_matches(record.backend, native)
    return native == record.id


MAX_REMEMBERED_PROMPT_CHARS = 4000
APPROVAL_STABILIZATION_SECONDS = 5.0
# Codex can hand each approval to an automated reviewer instead of the user (the
# CLI's "Automatic approval": `approvals_reviewer: auto_review`). It still fires
# `permission_request`, and there is no resolution hook and no rollout record of
# the decision - measured across every August 2026 rollout, approvals appear in
# the transcript exactly zero times. The only evidence that the reviewer said yes
# is the tool *finishing*, which for anything slower than the stabilization window
# lands after the approval has already become sidebar attention and a push
# notification for a question nobody was ever asked.
#
# So a delegated approval is held until the CLI actually draws the dialog, which
# is what an escalation to the human looks like and what an auto-approval never
# does. The ceiling is the backstop for a screen the classifier cannot read: a
# late approval is a nuisance, a hidden one strands the session.
APPROVAL_AUTO_REVIEW_CEILING_SECONDS = 60.0
APPROVAL_SCREEN_POLL_SECONDS = 1.0
# Values of Codex's `approvals_reviewer` that mean "something other than the user
# answers this". The sibling `approval_policy` is checked too because `never` and
# `on_request_auto_review` say the same thing on CLIs that do not write the
# reviewer field, and both are written kebab-cased in the rollout.
CODEX_AUTOMATIC_APPROVAL_REVIEWERS = frozenset({"auto_review"})
CODEX_AUTOMATIC_APPROVAL_POLICIES = frozenset({"never", "on_request_auto_review"})


def _persist_approval_candidate(
    session: Session, candidate: dict[str, Any] | None
) -> None:
    """Mirror a pending approval timer into supervisor-owned session metadata."""
    session.approval_candidate = candidate
    meta_sink = getattr(session, "meta_sink", None)
    if meta_sink is not None:
        try:
            meta_sink()
        except Exception:
            log.debug("approval candidate meta sink failed", exc_info=True)


def cancel_pending_approval(session: Session, reason: str) -> bool:
    """Cancel an approval candidate before it becomes user-visible attention."""
    state = _observation_state(session)
    pending = state.pop("pending_approval", None)
    candidate = getattr(session, "approval_candidate", None)
    if not isinstance(pending, dict) and not isinstance(candidate, dict):
        return False
    _persist_approval_candidate(session, None)
    source_record = pending if isinstance(pending, dict) else candidate
    assert isinstance(source_record, dict)
    task = source_record.get("task")
    if isinstance(task, asyncio.Task) and task is not asyncio.current_task() and not task.done():
        task.cancel()
    elapsed_seconds = 0.0
    if isinstance(pending, dict):
        elapsed_seconds = max(
            0.0, time.monotonic() - float(pending.get("started") or 0.0)
        )
    else:
        elapsed_seconds = max(
            0.0, time.time() - float(source_record.get("started_at") or 0.0)
        )
    transitions = getattr(session, "state_transitions", None)
    if transitions is not None:
        transitions.append(
            {
                "ts": time.time(),
                "kind": "approval_stabilization_cancelled",
                "reason": reason,
                "source": source_record.get("source"),
                "tool_use_id": source_record.get("tool_use_id"),
                "elapsed_seconds": round(elapsed_seconds, 3),
            }
        )
    return True


def _note_approval_delegation(session: Session, payload: dict[str, Any]) -> None:
    """Record whether this Codex thread answers its own approval requests.

    Read per thread rather than out of `config.toml`, because the CLI's own picker
    changes it live and the file would then describe a session that no longer
    matches it. `turn_context` restates it at the head of every turn and
    `thread_settings_applied` on every change, so both the opening setting and a
    mid-session switch are seen, and a catch-up replay re-derives it for a session
    the daemon adopted rather than started.
    """
    settings = payload.get("thread_settings")
    source = settings if isinstance(settings, dict) else payload
    reviewer = str(source.get("approvals_reviewer") or "").strip().lower().replace("-", "_")
    policy = str(source.get("approval_policy") or "").strip().lower().replace("-", "_")
    if not reviewer and not policy:
        return
    _observation_state(session)["approval_delegated"] = (
        reviewer in CODEX_AUTOMATIC_APPROVAL_REVIEWERS
        or policy in CODEX_AUTOMATIC_APPROVAL_POLICIES
    )


def note_activity_evidence(
    session: Session,
    reason: str,
    *,
    tool_use_id: str | None = None,
) -> bool:
    """Retire an unstabilized approval when the agent is provably still moving.

    Cancellation used to be a side effect of `_transition`, which meant every
    piece of evidence that proves an approval was answered *without* changing the
    displayed state left the timer armed: a `PostToolUse` hook on a session the
    transcript is driving returns before it transitions, and a resume record
    arriving while the session still reads `working` returns before it too. Both
    are exactly what an auto-approved tool produces.

    The PTY veto is the same one `_resume_from_awaiting` applies, and for the same
    reason: a parallel tool's record must not retire a prompt the user can see.
    """
    pending = _observation_state(session).get("pending_approval")
    if not isinstance(pending, dict):
        return False
    approval_tool_use_id = str(pending.get("tool_use_id") or "")
    matching_tool = bool(
        approval_tool_use_id and tool_use_id == approval_tool_use_id
    )
    if approval_tool_use_id and tool_use_id and not matching_tool:
        return False
    if not matching_tool and session_pty_state(session) == "approval":
        return False
    return cancel_pending_approval(session, reason)


async def _await_approval_escalation(
    session: Session, state: dict[str, Any], pending: dict[str, Any]
) -> str:
    """Watch a delegated approval until the CLI shows it, or the ceiling passes.

    Returns the reason the wait ended, which is ledgered on the commit so
    `/api/sessions/{sid}/state-log` can say whether an approval was surfaced
    because the screen proved it or because the classifier went quiet.
    """
    ceiling_seconds = float(
        getattr(
            session,
            "approval_escalation_ceiling_seconds",
            APPROVAL_AUTO_REVIEW_CEILING_SECONDS,
        )
    )
    poll_seconds = max(
        0.0,
        float(getattr(session, "approval_screen_poll_seconds", APPROVAL_SCREEN_POLL_SECONDS)),
    )
    deadline = float(pending["started"]) + ceiling_seconds
    # unsupervised-loop-ok: one escalation watch for one approval, bounded by
    # `APPROVAL_AUTO_REVIEW_CEILING_SECONDS` and cancelled with its settle task.
    while True:
        if state.get("pending_approval") is not pending:
            return "cancelled"
        if session_pty_state(session) == "approval":
            return "screen"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "ceiling"
        await asyncio.sleep(min(poll_seconds, remaining))


async def _request_stabilized_approval(
    session: Session,
    events: EventBus,
    *,
    detail: str,
    source: str,
    evidence: str,
    tool_use_id: str | None = None,
) -> None:
    """Block delivery immediately, but expose approval only after a stable 5 s wait."""
    if getattr(session, "observation_replay", False):
        return
    state = _observation_state(session)
    if session.record.state == "awaiting" and session.record.awaiting_reason == "approval":
        if isinstance(getattr(session, "approval_candidate", None), dict):
            _persist_approval_candidate(session, None)
        transitions = getattr(session, "state_transitions", None)
        if transitions is not None:
            transitions.append(
                {
                    "ts": time.time(),
                    "kind": "approval_stabilization_already_visible",
                    "source": source,
                    "evidence": evidence,
                }
            )
        return
    if isinstance(state.get("pending_approval"), dict):
        transitions = getattr(session, "state_transitions", None)
        if transitions is not None:
            transitions.append(
                {
                    "ts": time.time(),
                    "kind": "approval_stabilization_coalesced",
                    "source": source,
                    "evidence": evidence,
                }
            )
        return

    candidate = getattr(session, "approval_candidate", None)
    restored = isinstance(candidate, dict)
    now_wall = time.time()
    if restored:
        assert isinstance(candidate, dict)
        raw_started_at = candidate.get("started_at")
        try:
            started_at = (
                float(raw_started_at)
                if isinstance(raw_started_at, (int, float, str))
                else now_wall
            )
        except (TypeError, ValueError):
            started_at = now_wall
        source = str(candidate.get("source") or source)
        evidence = str(candidate.get("evidence") or evidence)
        detail = str(candidate.get("detail") or detail)
    else:
        started_at = now_wall
        candidate = {
            "started_at": started_at,
            "source": source,
            "evidence": evidence,
            "detail": detail,
            "tool_use_id": tool_use_id,
        }
        _persist_approval_candidate(session, candidate)
    elapsed_before_start = max(0.0, now_wall - started_at)
    pending: dict[str, Any] = {
        "started": time.monotonic() - elapsed_before_start,
        "source": source,
        "evidence": evidence,
        "detail": detail,
        "tool_use_id": tool_use_id,
    }
    state["pending_approval"] = pending
    stabilization_seconds = float(
        getattr(session, "approval_stabilization_seconds", APPROVAL_STABILIZATION_SECONDS)
    )
    delay_seconds = max(0.0, stabilization_seconds - elapsed_before_start)
    delegated = bool(state.get("approval_delegated"))
    transitions = getattr(session, "state_transitions", None)
    if transitions is not None:
        transitions.append(
            {
                "ts": time.time(),
                "kind": "approval_stabilization_started",
                "source": source,
                "evidence": evidence,
                "delay_seconds": delay_seconds,
                "restored": restored,
                "delegated": delegated,
                "tool_use_id": tool_use_id,
            }
        )
    # Delivery readiness consumes this internal event. Sounds, UI attention,
    # automation triage, and web push consume only the stabilized event below.
    await events.emit(
        "approval_detected",
        session_id=session.record.id,
        source=source,
        scope="root",
        kind="approval",
        detail=detail,
    )

    async def settle() -> None:
        await asyncio.sleep(max(0.0, delay_seconds))
        if state.get("pending_approval") is not pending:
            return
        gate = "stabilized"
        if delegated:
            gate = await _await_approval_escalation(session, state, pending)
            if gate == "cancelled":
                return
        state.pop("pending_approval", None)
        _persist_approval_candidate(session, None)
        if session.record.state in {"exited", "crashed"}:
            return
        if tool_use_id:
            state["active_approval_tool_use_id"] = tool_use_id
        else:
            state.pop("active_approval_tool_use_id", None)
        if transitions is not None:
            transitions.append(
                {
                    "ts": time.time(),
                    "kind": "approval_stabilization_committed",
                    "source": source,
                    "evidence": evidence,
                    "gate": gate,
                    "tool_use_id": tool_use_id,
                    "elapsed_seconds": round(
                        max(0.0, time.monotonic() - float(pending["started"])), 3
                    ),
                }
            )
        accepted = await _transition(
            session,
            events,
            "awaiting",
            detail,
            source=source,
            evidence=evidence,
            awaiting_reason="approval",
        )
        if not accepted and not (
            session.record.state == "awaiting"
            and session.record.awaiting_reason == "approval"
        ):
            if transitions is not None:
                transitions.append(
                    {
                        "ts": time.time(),
                        "kind": "approval_stabilization_transition_refused",
                        "source": source,
                        "evidence": evidence,
                        "state": session.record.state,
                    }
                )
            return
        await events.emit(
            "approval_needed",
            session_id=session.record.id,
            source=source,
            scope="root",
            kind="approval",
            detail=detail,
            stabilized=True,
        )

    # The inline fast path is only safe while `settle()` cannot block: a delegated
    # approval waits on the screen for up to the escalation ceiling, and awaiting
    # that here would stall the caller - the observation loop, or the adoption
    # path `restore_pending_approval` runs on during daemon startup.
    if delay_seconds <= 0 and not delegated:
        await settle()
        return

    task = asyncio.create_task(settle(), name=f"approval-settle-{session.record.id}")
    pending["task"] = task

    def finished(done: asyncio.Task[None]) -> None:
        if done.cancelled():
            return
        try:
            done.result()
        except Exception:
            log.exception("approval stabilization failed for session %s", session.record.id)

    task.add_done_callback(finished)


async def restore_pending_approval(session: Session, events: EventBus) -> bool:
    """Resume a supervisor-mirrored stabilization timer after daemon adoption."""
    candidate = getattr(session, "approval_candidate", None)
    if not isinstance(candidate, dict):
        return False
    await _request_stabilized_approval(
        session,
        events,
        detail=str(candidate.get("detail") or "Approval needed"),
        source=str(candidate.get("source") or "hook"),
        evidence=str(candidate.get("evidence") or "approval:restored"),
        tool_use_id=str(candidate.get("tool_use_id") or "") or None,
    )
    return True


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
        if getattr(session, "first_user_prompt", None) is None:
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

    Codex also mints its own thread id, so nothing on the filesystem separates its
    rollout from one written by a `codex` started outside mux in the same cwd
    (measured: `originator` betrays only the headless `codex exec`; an interactive
    outsider is identical). Its root `SessionStart` hook is the primary binding
    signal. The older `agent-turn-complete` notify remains an authenticated repair
    path when lifecycle hooks are disabled, untrusted, or unavailable.

    Deliberately one-way: it only fills an *unknown* id and never overwrites one
    the daemon already established, so a hook cannot rekey a bound session.
    """
    if not reports_lifecycle_hooks(session.record.backend):
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
    if not native_id_matches(session.record.backend, native_id):
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
    if not reports_lifecycle_hooks(session.record.backend):
        return nothing
    current = session.record.native_session_id or ""
    if not native_id_matches(session.record.backend, current):
        # Nothing bound yet: that is the bind path's job, not a rollover.
        return nothing
    native_id = str(payload.get("session_id") or payload.get("sessionId") or "")
    if not native_id_matches(session.record.backend, native_id) or native_id == current:
        return nothing
    if session.agent_lifecycle_id == native_id:
        return nothing
    source = str(payload.get("source") or "")
    if source == "startup":
        return RolloverDecision(refused=native_id, refusal_reason="foreign_process_startup")
    hook_cwd = str(payload.get("cwd") or "")
    # Both directories this session is known to stand in. The spawn/run cwd is where
    # it was launched; the live cwd is where its CLI says it is now, and for Claude
    # those differ for the whole life of a session that entered a native worktree.
    # Comparing against the spawn cwd alone made a `/clear` inside a worktree read as
    # a foreign process: the roll was refused, the session stayed keyed to the
    # conversation the user had just wiped, and every later hook was then filtered as
    # foreign - a permanent detachment from a routine command.
    known_cwds = [session.record.run_cwd or session.record.cwd]
    if session.record.runtime_cwd_live and session.record.runtime_cwd:
        known_cwds.append(session.record.runtime_cwd)
    present = [cwd for cwd in known_cwds if cwd]
    if hook_cwd and present and not any(_same_directory(hook_cwd, cwd) for cwd in present):
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
    if not reports_lifecycle_hooks(session.record.backend):
        return None
    if conversation_unbound(session):
        return None
    native_id = str(
        payload.get("session_id")
        or payload.get("sessionId")
        or payload.get("thread-id")
        or payload.get("thread_id")
        or ""
    )
    if not native_id_matches(session.record.backend, native_id):
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


def _background_labels(session: Session) -> dict[str, str]:
    """Human label per open launch (tool_use id -> description or command).

    Feeds the annotation's `detail`, which is the only thing that tells the user
    *what* the daemon believes is still running. A count alone is unfalsifiable
    from the outside: "1 background task" on a session with nothing running is
    indistinguishable from a correct reading, so the failure the annotation
    sources are prone to is also the one the UI cannot show.
    """
    state = _observation_state(session)
    labels = state.setdefault("background_labels", {})
    return labels  # type: ignore[no-any-return]


def _background_closed(session: Session) -> set[str]:
    """Identifiers already closed this run (both tool_use ids and task ids).

    One completion is announced up to three times (a `queue-operation` enqueue,
    its `attachment` mirror, and the `remove` when it reaches the model), so
    closes have to be idempotent per task. Without this the unmatched-close path
    - which decrements the annotation's own count when no open was tracked -
    would subtract two or three times for one finished shell and zero out a count
    that other, genuinely-running tasks own.
    """
    state = _observation_state(session)
    closed = state.setdefault("background_closed", set())
    return closed  # type: ignore[no-any-return]


def _background_detail(session: Session) -> str | None:
    """`detail` for the annotation: what the newest open launch is, plus a count.

    Never None while anything is open, because `set_standing_activity` reads None
    as "keep the existing value" - a stale detail outliving the task it named
    would be worse than none at all.
    """
    open_tasks = _background_open(session)
    if not open_tasks:
        return None
    newest_key = next(reversed(open_tasks))
    labels = _background_labels(session)
    newest = labels.get(newest_key) or open_tasks.get(newest_key) or "background command"
    extra = len(open_tasks) - 1
    return _standing_detail(f"{newest} (+{extra} more)" if extra > 0 else newest)


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
        detail=_background_detail(session),
        now=now,
    )


def _open_background_task(
    session: Session,
    *,
    tool_use_id: str,
    evidence: str,
    now: float,
    label: str | None = None,
    task_id: str | None = None,
) -> bool:
    """Track one background launch, whichever shape announced it.

    Re-opening an id this run already closed is refused: the launch record and
    its completion can be read in either order across a daemon restart (the
    transcript is re-read from a byte offset, hooks are not), and a resurrected
    open would hold the annotation for a full TTL against a shell that is gone.
    """
    if not tool_use_id or tool_use_id in _background_closed(session):
        return False
    open_tasks = _background_open(session)
    open_tasks.setdefault(tool_use_id, None)
    if task_id:
        open_tasks[tool_use_id] = task_id
    if label:
        # `setdefault`, so the launch's own description (read at tool_use time,
        # written for a human) outranks the raw command the result path recovers.
        _background_labels(session).setdefault(tool_use_id, label)
    return _sync_background_annotation(session, evidence=evidence, now=now)


def _close_background_task(
    session: Session,
    *,
    evidence: str,
    now: float,
    tool_use_id: str | None = None,
    task_id: str | None = None,
) -> bool:
    closed = _background_closed(session)
    identifiers = {value for value in (tool_use_id, task_id) if value}
    if not identifiers or identifiers & closed:
        # Already accounted for. One finished shell is announced up to three
        # times (see the record-shape notes), and each extra announcement would
        # otherwise decrement the count a second and third time.
        return False
    closed |= identifiers
    open_tasks = _background_open(session)
    matched = None
    if tool_use_id and tool_use_id in open_tasks:
        matched = tool_use_id
    elif task_id:
        matched = next((key for key, value in open_tasks.items() if value == task_id), None)
    if matched is not None:
        open_tasks.pop(matched, None)
        _background_labels(session).pop(matched, None)
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
        changed = _open_background_task(
            session,
            tool_use_id=str(block.get("id") or ""),
            evidence="transcript:Bash:run_in_background",
            now=now,
            label=_standing_detail(tool_input.get("description") or tool_input.get("command")),
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
    session: Session,
    event: dict[str, Any],
    tool: str,
    tool_use_id: str,
    detail: str,
    target: str | None = None,
) -> None:
    """Annotation lifecycle from one tool_result record (live only)."""
    if getattr(session, "observation_replay", False):
        return
    now = _standing_now(session, event)
    changed = False
    if tool == "Bash":
        # The result is authoritative for both launch shapes: an explicit
        # `run_in_background` launch (already open, this binds its task id) and a
        # foreground command the CLI moved to the background on timeout, whose
        # input carried no flag at all and which nothing else would ever open.
        match = _BACKGROUND_TASK_ID.search(detail)
        if match:
            changed = _open_background_task(
                session,
                tool_use_id=tool_use_id,
                evidence="transcript:Bash:background_result",
                now=now,
                label=_standing_detail(target),
                task_id=match.group(1),
            )
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


def _claude_task_notification_text(event: dict[str, Any]) -> str:
    """The `<task-notification>` body carried by a non-message record, if any.

    A background shell that finishes while its session is between turns has no
    turn to be announced into, so the CLI queues the notification instead: it
    lands as a `queue-operation` record (body in `content`) and its `attachment`
    mirror (body in `attachment.prompt`), and only becomes a user message if the
    CLI later gets to hand it to the model. Reading only the message form is why
    a completed shell could hold the annotation for its full 30-minute TTL - the
    proof of completion was in the transcript the whole time, in a record type
    the standing-activity extractors never looked at.
    """
    event_type = event.get("type")
    if event_type == "queue-operation":
        content = event.get("content")
        return content if isinstance(content, str) else ""
    if event_type == "attachment":
        attachment = event.get("attachment")
        if isinstance(attachment, dict):
            prompt = attachment.get("prompt")
            return prompt if isinstance(prompt, str) else ""
    return ""


def _extract_standing_task_notifications(
    session: Session, event: dict[str, Any], text: str
) -> None:
    """Close background launches named by a `<task-notification>` body.

    Idempotent per task, because one completion arrives on up to three carriers.
    """
    if getattr(session, "observation_replay", False) or _TASK_NOTIFICATION_MARKER not in text:
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
    """Subagent-scoped hooks: lifecycle owns the count, activity refreshes it.

    ``SubagentStart``/``SubagentStop`` own the count. Every *other*
    subagent-scoped hook (its ``PreToolUse``/``PostToolUse`` stream) is
    liveness: it proves a subagent is still running right now.

    That refresh is not a nicety, it is what keeps the annotation alive at all.
    A background subagent writes **nothing** into the root transcript — verified
    live 2026-08-02 on a session whose agents ran 16 minutes with zero
    `isSidechain` records — so the transcript tier has no evidence to offer and
    the `SUBAGENT_QUIET_SECONDS` TTL expired the annotation ~2 minutes in while
    the agents kept working. The session then rendered a bare "ready · turn
    complete": the root turn really had ended, but nothing said an agent was
    still running. The tool hooks were arriving the whole time and were simply
    dropped here.

    Creating at count 1 when the launch was missed mirrors the transcript-side
    rule for sidechain records, and is what heals a count that a lone
    ``SubagentStop`` (starts under-counted because its ``SubagentStart``
    predated the annotation, or was lost) had already zeroed — but only after
    ``SUBAGENT_REOPEN_GRACE_SECONDS`` past the last stop. Hooks are unordered
    and retried, so a straggler ``PostToolUse`` from the subagent that just
    stopped can land seconds after its ``SubagentStop``; re-opening on it would
    flap a correctly cleared annotation for a full TTL, the exact failure the
    trailing-transcript rule pins. A genuinely live agent keeps streaming tool
    hooks, so it re-creates the annotation one grace window later at most.
    """
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
        state["subagent_stop_ts"] = now
        changed = _drop_subagent(
            session, source="hook", evidence="hook:SubagentStop", now=now
        )
    else:
        stop_ts = state.get("subagent_stop_ts")
        recent_stop = (
            isinstance(stop_ts, (int, float))
            and now - stop_ts < SUBAGENT_REOPEN_GRACE_SECONDS
        )
        changed = _refresh_subagents(
            session,
            source="hook",
            evidence=f"hook:subagent:{event_type}",
            now=now,
            create=not recent_stop,
        )
    if changed:
        _publish_update(session)


async def _refresh_database_measurements(session: Session) -> None:
    """Publish tokens, cost, and model for a harness that keeps them in a store.

    The counterpart to the transcript measurement path, for a harness that writes
    no transcript. Runs off the event loop because it opens SQLite, and publishes
    nothing when the row is missing: absent measurements must read as absent, not
    as zero, since a published zero is indistinguishable from a genuinely empty
    conversation.

    Context percentage is deliberately not derived. opencode's session row has no
    context window and mux has no catalogue for its providers, so the window is
    unknown — and the codebase already records that a zero window renders as 0%
    used, which looks like a fresh conversation rather than a missing reading.
    """
    record = session.record
    if descriptor(record.backend).measurement_source != "database":
        return
    if not _measurements_publishable(session):
        return
    native_id = record.native_session_id or ""
    reader = getattr(getattr(session, "adapter", None), "session_measurements", None)
    if not native_id or not callable(reader):
        return
    try:
        figures = await asyncio.to_thread(reader, native_id)
    except (OSError, ValueError):
        return
    if not figures:
        return
    record.tokens_in = int(figures.get("tokens_in") or 0)
    record.tokens_out = int(figures.get("tokens_out") or 0)
    record.tokens_cache_read = int(figures.get("tokens_cache_read") or 0)
    record.tokens_cache_write = int(figures.get("tokens_cache_write") or 0)
    record.cost_usd = float(figures.get("cost_usd") or 0.0)
    model = figures.get("model")
    if isinstance(model, str) and model:
        record.model = model
    provider = figures.get("provider")
    if isinstance(provider, str) and provider:
        record.provider = provider
    # Context is published only when the harness's own catalogue knows the
    # window. A zero window renders as 0% used, which reads as a fresh
    # conversation rather than a missing measurement.
    window = _adapter_context_window(session, provider or "", model or "")
    if window > 0:
        record.context_window = window
        record.context_pct = min(1.0, record.tokens_in / window)
        record.context_peak_pct = max(record.context_peak_pct, record.context_pct)
    record.measurement_source = f"{record.backend}-database"
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
        if _observation_state(session).get("root_turn_active") or session.record.state in {
            "working",
            "awaiting",
        }:
            # SessionStart is process/conversation lifecycle evidence, not a turn
            # boundary. Codex can emit it while compacting an active turn. The
            # previous unconditional idle transition produced a false ready beep
            # until the next tool record restored working.
            transitions = getattr(session, "state_transitions", None)
            if transitions is not None:
                transitions.append(
                    {
                        "ts": time.time(),
                        "kind": "session_start_state_ignored",
                        "state": session.record.state,
                        "reason": "active_root_turn",
                    }
                )
        else:
            await _transition(
                session, events, "idle", source="hook", evidence="hook:SessionStart"
            )
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
        # Before the authority check, not after. A tool that ran to completion is
        # proof its approval was answered, and that is true of a session whose
        # displayed state the transcript owns as much as any other - returning
        # first left the timer to expire on a question nobody was asked.
        # `PreToolUse` is deliberately not evidence here: Codex fires it *before*
        # the permission decision, so it proves an attempt, not an answer.
        tool_use_id = str(payload.get("tool_use_id") or "") or None
        note_activity_evidence(
            session,
            f"activity:hook:{event_type}",
            tool_use_id=tool_use_id,
        )
        # A harness with no transcript source has hooks as its only evidence, so this
        # is the only place its tool completions can be reported. Harnesses that do
        # read a transcript emit `tool_result` from the record instead, which carries
        # the result payload this hook does not; emitting here as well would double
        # count them.
        if "transcript" not in descriptor(session.record.backend).state_sources:
            await events.emit(
                "tool_result",
                session_id=session.record.id,
                source="hook",
                scope="root",
                tool=str(payload.get("tool_name") or payload.get("name") or "tool"),
                call_id=tool_use_id,
                success=event_type != "PostToolUseFailure",
            )
        if _transcript_authoritative(session):
            return
        state = _observation_state(session)
        if session.record.state == "awaiting" and session.record.awaiting_reason == "approval":
            active_tool_use_id = str(state.get("active_approval_tool_use_id") or "")
            if active_tool_use_id and tool_use_id and tool_use_id != active_tool_use_id:
                return
            matching_tool = bool(active_tool_use_id and tool_use_id == active_tool_use_id)
            if not matching_tool and session_pty_state(session) == "approval":
                return
        await _transition(
            session, events, "working", source="hook", evidence=f"hook:{event_type}"
        )
    elif event_type in {"PermissionRequest", "approval_needed", "approval-requested"}:
        tool = str(payload.get("tool_name") or payload.get("message") or "approval")
        if payload.get("omp_event") == "tool_approval_requested":
            cancel_pending_approval(session, "omp_exact_approval")
            tool_use_id = str(payload.get("tool_use_id") or "")
            if tool_use_id:
                _observation_state(session)["active_approval_tool_use_id"] = tool_use_id
            else:
                _observation_state(session).pop("active_approval_tool_use_id", None)
            await _transition(
                session,
                events,
                "awaiting",
                tool,
                source="hook",
                evidence="hook:omp:tool_approval_requested",
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
        else:
            await _request_stabilized_approval(
                session,
                events,
                detail=tool,
                source="hook",
                evidence=f"hook:{event_type}",
                tool_use_id=str(payload.get("tool_use_id") or "") or None,
            )
    elif event_type == "approval_resolved":
        cancel_pending_approval(session, "hook:approval_resolved")
        _observation_state(session).pop("active_approval_tool_use_id", None)
        if session.record.state == "awaiting" and session.record.awaiting_reason == "approval":
            await _transition(
                session,
                events,
                "working",
                source="hook",
                force=True,
                evidence="hook:approval_resolved",
            )
        await events.emit(
            "approval_resolved",
            session_id=session.record.id,
            source="hook",
            scope="root",
            approved=payload.get("approved") is True,
            tool=payload.get("tool_name"),
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
            if reason == "approval":
                await _request_stabilized_approval(
                    session,
                    events,
                    detail=detail,
                    source="hook",
                    evidence=f"hook:Notification:{notification}",
                )
            else:
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
    elif event_type == "turn_ended" and payload.get("root_completion") is False:
        await events.emit(
            "provider_turn_ended",
            session_id=session.record.id,
            source="hook",
            scope="root",
            turn_index=payload.get("turn_index"),
        )
    elif event_type == "task_complete" and payload.get("will_continue") is True:
        await _transition(
            session, events, "working", source="hook", evidence="hook:task_continuing"
        )
    elif event_type in {"Stop", "turn_ended", "agent-turn-complete", "task_complete"}:
        # SessionStart normally binds Codex before the first turn. Its completion
        # notify remains a compatibility/repair path when lifecycle hooks are
        # disabled, untrusted, or unavailable. Bind before closing the turn so the
        # transcript can be exact-matched and catch-up replays the turn that just ran.
        await _bind_native_id_from_hook(session, payload, events)
        await _finish_root_turn(session, events, source="hook", evidence=f"hook:{event_type}")
        # A harness whose measurements live in its own store has no transcript
        # record to carry them, so the turn boundary is where they are read.
        await _refresh_database_measurements(session)
    elif (
        event_type == "context_compacted"
        and not provisional_observation(session)
        and _first_compaction_evidence(
            session, session.record.backend, payload.get("compaction_id")
        )
    ):
        await events.emit(
            "context_compacted",
            session_id=session.record.id,
            source="hook",
            scope="root",
            backend=session.record.backend,
            capability="explicit_native",
            confidence="high",
            compaction_id=payload.get("compaction_id"),
            tokens_before=payload.get("tokens_before"),
            parser_version=OBSERVATION_SCHEMA_VERSION,
        )
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
    # Working transitions are too coarse to retire a pending approval. The
    # evidence-producing path above owns that cancellation because it can compare
    # tool identities and consult the effective screen classifier first.
    keeps_approval_candidate = (
        (state == "awaiting" and awaiting_reason == "approval")
        or state == "working"
    )
    if not keeps_approval_candidate:
        cancel_pending_approval(session, f"state_evidence:{state}:{source}")
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
            # The idle axis and the standing axis have to ride the event, not only
            # the record: consumers that decide whether to interrupt the human
            # (push, sounds) see events, and an `idle` with subagents still running
            # is not the moment to tell them the agent wants their input. This
            # mirrors what `turn_ended` has always carried; the two must agree,
            # because `state_changed` is the one the mobile default subscribes to.
            idle_reason=idle_reason if state == "idle" else None,
            standing=standing_activity_kinds(session),
            proof=transition_proof(source, inferred),
        )
    if not (state == "awaiting" and awaiting_reason == "approval"):
        _observation_state(session).pop("active_approval_tool_use_id", None)
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
    if event_type in {"queue-operation", "attachment"}:
        # Deliberately not turn activity - `_CLAUDE_TAIL_IGNORED` skips both when
        # judging whether a turn ended, and that stays true. They are read here
        # for one thing: they are the carriers a background-task completion uses
        # when there is no live turn to announce it into.
        _extract_standing_task_notifications(
            session, event, _claude_task_notification_text(event)
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
            _remember_user_prompt(session, {"prompt": text})
            # A fresh prompt while blocked means the user answered and moved on.
            await _resume_from_awaiting(session, events, event, evidence="user_prompt_record")
            await _begin_root_turn(
                session, events, source="transcript", evidence="user_prompt_record"
            )
            await events.emit(
                "transcript_message",
                session_id=session.record.id,
                source="transcript",
                scope="root",
                role="user",
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
                _extract_standing_tool_result(
                    session, event, tool, tool_use_id, detail, target
                )
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
        if usage and _measurements_publishable(session):
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
            window = claude_context_window(model)
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
    if outer_type == "turn_context":
        # Where the current CLI records the model. `session_meta` carried it once
        # and no longer does, and `token_count`'s `info.model` is absent too, so
        # every Codex session reported no model at all while Claude reported one.
        # Read per turn rather than once: `/model` mid-conversation is a real
        # thing, and the next turn's context is what says so.
        if not provisional_observation(session):
            model = str(payload.get("model") or "")
            if model and model != session.record.model:
                session.record.model = model
                _publish_update(session)
    if outer_type == "turn_context" or payload_type == "thread_settings_applied":
        _note_approval_delegation(session, payload)
    if payload_type in CODEX_RESUME_PAYLOADS:
        # Tooling or the model is running again, so any approval was answered.
        await _resume_from_awaiting(session, events, event, evidence=str(payload_type))
    if payload_type == "user_message":
        _remember_user_prompt(session, {"prompt": str(payload.get("message") or "")})
        await _begin_root_turn(
            session, events, source="transcript", evidence=str(payload_type)
        )
        await events.emit(
            "transcript_message",
            session_id=session.record.id,
            source="transcript",
            scope="root",
            role="user",
        )
    elif payload_type == "task_started":
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
        if reason == "approval":
            await _request_stabilized_approval(
                session,
                events,
                detail=detail,
                source="transcript",
                evidence=str(payload_type),
            )
        else:
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
        # This is the fallback tier until lifecycle hooks arrive. Once they do,
        # SubagentStart/SubagentStop own the count and transcript records only
        # refresh recency, so a trailing record cannot reopen a stopped agent.
        # Count stays 1 in fallback mode because the records carry no fleet size.
        if not getattr(session, "observation_replay", False) and _refresh_subagents(
            session,
            source="transcript",
            evidence="transcript:sub_agent_activity",
            now=_standing_now(session, event),
            create=not _observation_state(session).get("subagent_hooks_seen"),
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
        if not _measurements_publishable(session):
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


def _omp_index_entry(
    session: Session, event: dict[str, Any]
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Index one append and return (active chain, branch changed, duplicate)."""
    state = _observation_state(session)
    entry_id = event.get("id")
    if not isinstance(entry_id, str) or not entry_id:
        return [], False, False
    entries = state.setdefault("omp_entries", {})
    if entry_id in entries:
        active_ids = state.get("omp_active_ids") or []
        return [entries[item] for item in active_ids if item in entries], False, True
    old_leaf = state.get("omp_leaf_id")
    entries[entry_id] = event
    parent = event.get("parentId")
    parent_id = parent if isinstance(parent, str) and parent else None
    branch_changed = old_leaf is not None and parent_id != old_leaf
    if old_leaf is None or parent_id == old_leaf:
        active_ids = [*state.get("omp_active_ids", []), entry_id]
    else:
        active_ids = []
        cursor: str | None = entry_id
        visited: set[str] = set()
        while cursor and cursor not in visited:
            visited.add(cursor)
            item = entries.get(cursor)
            if not isinstance(item, dict):
                break
            active_ids.append(cursor)
            item_parent = item.get("parentId")
            cursor = item_parent if isinstance(item_parent, str) and item_parent else None
        active_ids.reverse()
    state["omp_leaf_id"] = entry_id
    state["omp_active_ids"] = active_ids
    return [entries[item] for item in active_ids if item in entries], branch_changed, False


def _omp_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _adapter_context_window(session: Session, provider: str, model: str) -> int:
    resolver = getattr(getattr(session, "adapter", None), "model_context_window", None)
    if callable(resolver):
        try:
            value = resolver(provider, model)
            if isinstance(value, int) and value > 0:
                return value
        except (OSError, ValueError):
            pass
    if provider == "anthropic" and model == "claude-opus-4-8":
        return 1_000_000
    return 0


def _omp_update_measurements(session: Session, active_chain: list[dict[str, Any]]) -> None:
    if not _measurements_publishable(session):
        return
    state = _observation_state(session)
    entries = state.get("omp_entries")
    if not isinstance(entries, dict):
        return
    tokens_in = 0
    tokens_out = 0
    cache_read = 0
    cache_write = 0
    cost_usd = 0.0
    for event in entries.values():
        if not isinstance(event, dict) or event.get("type") != "message":
            continue
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        tokens_in += _omp_int(usage.get("input"))
        tokens_out += _omp_int(usage.get("output"))
        cache_read += _omp_int(usage.get("cacheRead"))
        cache_write += _omp_int(usage.get("cacheWrite"))
        cost = usage.get("cost")
        if isinstance(cost, dict):
            try:
                cost_usd += max(0.0, float(cost.get("total") or 0.0))
            except (TypeError, ValueError):
                pass

    last_model: str | None = None
    last_provider = ""
    last_used = 0
    peak = 0.0
    last_window = 0
    provider_account_hashes: dict[str, str] = {}
    for event in active_chain:
        if event.get("type") == "credential_pin":
            pin_provider = str(event.get("provider") or "").strip()
            account_hash = str(event.get("hash") or "").strip().lower()
            if pin_provider and re.fullmatch(r"[0-9a-f]{64}", account_hash):
                provider_account_hashes[pin_provider] = account_hash
            continue
        if event.get("type") != "message":
            continue
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        model = str(message.get("model") or "")
        provider = str(message.get("provider") or "")
        usage = message.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        snapshot = message.get("contextSnapshot")
        used = (
            _omp_int(snapshot.get("promptTokens"))
            if isinstance(snapshot, dict)
            else _omp_int(usage.get("input"))
            + _omp_int(usage.get("cacheRead"))
            + _omp_int(usage.get("cacheWrite"))
        )
        window = _adapter_context_window(session, provider, model)
        if window:
            peak = max(peak, min(1.0, used / window))
        last_model = model or last_model
        last_provider = provider
        last_used = used
        last_window = window

    record = session.record
    record.tokens_in = tokens_in
    record.tokens_out = tokens_out
    record.tokens_cache_read = cache_read
    record.tokens_cache_write = cache_write
    record.cost_usd = cost_usd
    record.context_window = last_window
    record.context_pct = min(1.0, last_used / last_window) if last_window else 0.0
    record.context_peak_pct = peak
    record.provider = last_provider or record.provider
    record.provider_account_hashes = provider_account_hashes
    record.model = last_model or record.model
    # Named for the harness that produced the numbers, not for the reader that
    # parsed them. pi and omp share this reader, so a hardcoded "omp-transcript"
    # labelled every pi session's measurements as omp's — and that label is
    # persisted into the history row, so it was cross-attribution in stored data,
    # not just a cosmetic string.
    record.measurement_source = f"{record.backend}-transcript"
    state["omp_last_provider"] = last_provider
    _publish_update(session)


def _omp_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _omp_note_task_start(session: Session, event: dict[str, Any]) -> None:
    if getattr(session, "observation_replay", False):
        return
    changed = _refresh_subagents(
        session,
        source="transcript",
        evidence="transcript:omp:task",
        now=_standing_now(session, event),
        count=_standing_activity_count(session, "subagents") + 1,
    )
    if changed:
        _publish_update(session)


def _omp_note_task_end(session: Session, event: dict[str, Any]) -> None:
    if getattr(session, "observation_replay", False):
        return
    if _drop_subagent(
        session,
        source="transcript",
        evidence="transcript:omp:task_result",
        now=_standing_now(session, event),
    ):
        _publish_update(session)


def _omp_note_hub_call(
    session: Session,
    event: dict[str, Any],
    call_id: str,
    arguments: dict[str, Any],
) -> None:
    if getattr(session, "observation_replay", False):
        return
    op = str(arguments.get("op") or "")
    name = str(arguments.get("name") or "") or None
    now = _standing_now(session, event)
    changed = False
    if op in {"start", "restart"}:
        changed = _open_background_task(
            session,
            tool_use_id=call_id,
            evidence=f"transcript:omp:hub:{op}",
            now=now,
            label=_standing_detail(name or arguments.get("command")),
            task_id=name,
        )
    elif op == "stop" and name:
        changed = _close_background_task(
            session,
            evidence="transcript:omp:hub:stop",
            now=now,
            task_id=name,
        )
    if changed:
        _publish_update(session)


async def _omp_tool_call(
    session: Session,
    event: dict[str, Any],
    block: dict[str, Any],
    events: EventBus,
) -> None:
    name = str(block.get("name") or "tool")
    call_id = str(block.get("id") or block.get("toolCallId") or "")
    arguments = block.get("arguments")
    arguments = arguments if isinstance(arguments, dict) else {}
    target, content_hash = tool_call_evidence(arguments)
    _remember_tool_call(session, call_id, name, target)
    emitted = _observation_state(session).setdefault("omp_emitted_tool_calls", set())
    if call_id and call_id in emitted:
        return
    if call_id:
        emitted.add(call_id)
    await _transition(session, events, "working", name, evidence="omp:tool_call")
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
    if name == "task":
        _omp_note_task_start(session, event)
        await events.emit(
            "subagent_activity",
            session_id=session.record.id,
            source="transcript",
            scope="subagent",
            kind="started",
        )
    elif name == "hub":
        _omp_note_hub_call(session, event, call_id, arguments)


async def _omp_tool_result(
    session: Session,
    event: dict[str, Any],
    message: dict[str, Any],
    events: EventBus,
) -> None:
    call_id = str(message.get("toolCallId") or "")
    fallback = str(message.get("toolName") or "tool")
    tool, target = _recall_tool_call(session, call_id, fallback)
    detail = _omp_content_text(message.get("content"))
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
        success=not bool(message.get("isError")),
        exit_code=None,
        parser_version=OBSERVATION_SCHEMA_VERSION,
        detail=bounded_detail(detail),
    )
    if tool == "task":
        _omp_note_task_end(session, event)
        await events.emit(
            "subagent_activity",
            session_id=session.record.id,
            source="transcript",
            scope="subagent",
            kind="completed",
        )


async def _omp_reconcile_branch_state(
    session: Session, active_chain: list[dict[str, Any]], events: EventBus
) -> None:
    state = _observation_state(session)
    verdict = _omp_chain_turn_state(active_chain)
    if verdict == "open":
        state["root_turn_active"] = True
        state["root_completion_seen"] = False
        await _transition(
            session, events, "working", source="transcript", force=True,
            evidence="omp:active_branch_open",
        )
    elif verdict == "ended":
        state["root_turn_active"] = False
        state["root_completion_seen"] = True
        await _transition(
            session, events, "idle", source="transcript", force=True,
            evidence="omp:active_branch_ended",
        )


async def _omp(session: Session, event: dict[str, Any], events: EventBus) -> None:
    event_type = str(event.get("type") or "")
    if event_type == "session":
        version = event.get("version")
        if isinstance(version, int):
            _observation_state(session)["omp_session_version"] = version
        native_id = event.get("id")
        if isinstance(native_id, str) and native_id and not provisional_observation(session):
            session.record.native_session_id = native_id
        return
    if event_type == "title":
        return

    active_chain, branch_changed, duplicate = _omp_index_entry(session, event)
    if duplicate:
        return
    if active_chain:
        _omp_update_measurements(session, active_chain)
    if branch_changed:
        await _omp_reconcile_branch_state(session, active_chain, events)

    if event_type == "message":
        message = event.get("message")
        if not isinstance(message, dict):
            return
        role = str(message.get("role") or "")
        if role in {"user", "developer"}:
            text = _omp_content_text(message.get("content"))
            _remember_user_prompt(session, {"prompt": text})
            await _resume_from_awaiting(session, events, event, evidence="omp:user_message")
            await _begin_root_turn(
                session, events, source="transcript", evidence="omp:user_message"
            )
            await events.emit(
                "transcript_message",
                session_id=session.record.id,
                source="transcript",
                scope="root",
                role="user",
            )
            return
        if role == "toolResult":
            await _resume_from_awaiting(session, events, event, evidence="omp:tool_result")
            await _begin_root_turn(
                session, events, source="transcript", evidence="omp:tool_result"
            )
            await _omp_tool_result(session, event, message, events)
            return
        if role != "assistant":
            return
        await _resume_from_awaiting(session, events, event, evidence="omp:assistant")
        calls = _omp_message_tool_calls(message)
        stop_reason = str(message.get("stopReason") or "")
        state = _observation_state(session)
        trailing_completion = (
            not calls
            and stop_reason in {"stop", "end_turn"}
            and not state.get("root_turn_active")
            and state.get("root_completion_seen")
        )
        if not trailing_completion:
            await _begin_root_turn(
                session, events, source="transcript", evidence="omp:assistant"
            )
        state["turn_saw_activity"] = True
        for block in calls:
            await _omp_tool_call(session, event, block, events)
        if not calls and stop_reason in {"stop", "end_turn"}:
            await _finish_root_turn(
                session, events, source="transcript", evidence=f"omp:stopReason={stop_reason}"
            )
        elif not calls and stop_reason in {"aborted", "error", "length"}:
            await _finish_root_turn(
                session,
                events,
                source="transcript",
                outcome=stop_reason,
                force=True,
                evidence=f"omp:stopReason={stop_reason}",
            )
        return

    if event_type == "reset_boundary":
        for kind in ("subagents", "background_tasks"):
            clear_standing_activity(
                session, kind, evidence="transcript:omp:reset_boundary",
                now=_standing_now(session, event),
            )
        _tool_names(session).clear()
        _tool_targets(session).clear()
        await _finish_root_turn(
            session,
            events,
            source="transcript",
            outcome="cleared",
            force=True,
            evidence="omp:reset_boundary",
        )
        return

    if (
        event_type == "compaction"
        and not provisional_observation(session)
        and _first_compaction_evidence(session, "omp", event.get("id"))
    ):
        await events.emit(
            "context_compacted",
            session_id=session.record.id,
            source="transcript",
            scope="root",
            backend="omp",
            capability="explicit_native",
            confidence="high",
            compaction_id=event.get("id"),
            tokens_before=event.get("tokensBefore"),
            parser_version=OBSERVATION_SCHEMA_VERSION,
        )
        return

    if event_type != "custom":
        return
    custom_type = str(event.get("customType") or "")
    data = event.get("data")
    data = data if isinstance(data, dict) else {}
    if custom_type == "tool_execution_start":
        call_id = str(data.get("toolCallId") or "")
        name = str(data.get("toolName") or "tool")
        arguments = data.get("args")
        arguments = arguments if isinstance(arguments, dict) else {}
        await _begin_root_turn(
            session, events, source="transcript", evidence="omp:tool_execution_start"
        )
        await _omp_tool_call(
            session,
            event,
            {"type": "toolCall", "id": call_id, "name": name, "arguments": arguments},
            events,
        )
    elif custom_type == "session_exit":
        pending = data.get("pendingToolCalls")
        pending_calls = pending if isinstance(pending, list) else []
        abnormal = str(data.get("kind") or "normal") != "normal"
        if pending_calls or abnormal:
            clear_standing_activity(
                session,
                "subagents",
                evidence="transcript:omp:session_exit",
                now=_standing_now(session, event),
            )
            for item in pending_calls:
                if not isinstance(item, dict):
                    continue
                call_id = str(item.get("toolCallId") or "")
                tool, target = _recall_tool_call(
                    session, call_id, str(item.get("toolName") or "tool")
                )
                await events.emit(
                    "tool_result",
                    session_id=session.record.id,
                    source="transcript",
                    scope="root",
                    tool=tool,
                    call_id=call_id or None,
                    target=target,
                    success=False,
                    exit_code=None,
                    parser_version=OBSERVATION_SCHEMA_VERSION,
                    detail="interrupted before tool completion",
                )
            await _finish_root_turn(
                session,
                events,
                source="transcript",
                outcome="interrupted",
                force=True,
                evidence="omp:session_exit",
                reason=data.get("reason"),
                exit_kind=data.get("kind"),
            )


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
