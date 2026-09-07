"""Harness-reported session status: each channel's report folded into one record.

Three channels feed `SessionRecord.harness_status`, and each carries a different
subset of the same facts:

- **Claude's status line** (`hook_client Status`): the JSON the CLI hands its
  status-line command on every assistant message - effort, output style, fast
  mode, thinking, the CLI's own context and cost figures, and the account's
  rate-limit windows. The only channel that carries Claude's effort at all.
- **Lifecycle hooks** (every harness): `permission_mode` on every hook payload,
  in the one vocabulary Claude and Codex share.
- **The Codex rollout**: `effort`/`reasoning_effort` and `service_tier` in
  `turn_context` and `thread_settings_applied`, and `rate_limits` on every
  persisted `token_count`.

Every function here is pure over the record: it parses one payload, writes the
fields it can vouch for, leaves the rest untouched, and returns the names of the
fields it changed so the caller can decide whether to publish. Nothing here
guesses - a field the payload does not carry is not written, because the row
renders nothing for an absent field and a guessed value would render as fact.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .models import (
    RATE_LIMIT_FIVE_HOUR,
    RATE_LIMIT_SEVEN_DAY,
    RATE_LIMIT_SPEND,
    HarnessStatus,
    RateLimitWindow,
    SessionRecord,
)

log = logging.getLogger(__name__)

SOURCE_CLAUDE_STATUSLINE = "claude-statusline"
SOURCE_HOOK = "hook"
SOURCE_CODEX_ROLLOUT = "codex-rollout"

#: The hook event the Claude status-line tee posts. Not a lifecycle hook: it
#: never moves state, and the ingress keeps it off the generic event fan-out.
STATUS_EVENT = "Status"

#: Tokens Codex treats as always present in the context (system prompt, fixed
#: tool instructions); subtracted from both numerator and denominator so the
#: reading matches the CLI's own footer. `BASELINE_TOKENS` in
#: `codex-rs/protocol/src/protocol.rs`, read 2026-09-07 at rust-v0.153.4.
CODEX_BASELINE_TOKENS = 12_000

#: Window lengths Codex reports in `window_minutes`, mapped onto the shared keys.
_CODEX_WINDOW_KEYS: dict[int, str] = {
    300: RATE_LIMIT_FIVE_HOUR,
    1_440: "daily",
    10_080: RATE_LIMIT_SEVEN_DAY,
    43_200: "monthly",
    525_600: "annual",
}

#: Effort levels and permission modes are short identifiers; anything else is a
#: payload this parser does not understand and must not render as a setting.
_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")


def _token(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        if _TOKEN.match(text):
            return text
    return None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _set(status: HarnessStatus, name: str, value: object, source: str, changed: list[str]) -> None:
    """Write one field with its provenance, recording whether it moved."""
    if getattr(status, name) != value:
        setattr(status, name, value)
        changed.append(name)
    status.sources[name] = source


def _stamp(record: SessionRecord, now: float) -> None:
    record.harness_status.updated_at = now


def _log_setting_change(record: SessionRecord, name: str, before: object, source: str) -> None:
    after = getattr(record.harness_status, name)
    if before != after:
        log.info(
            "session %s %s %s -> %s (%s)",
            record.id,
            name,
            before if before is not None else "unset",
            after,
            source,
        )


def codex_context_fraction(last_usage: dict[str, Any], window: int) -> float:
    """The share of the context window used, as Codex's own footer computes it.

    Mirrors `TokenUsage::percent_of_context_window_remaining`: both the usage and
    the window lose the 12k baseline first, the usage is the *last* response's
    `total_tokens` (input, cached and output together), and the result is
    clamped to the unit interval. Returns 0 for a window the baseline swallows,
    which is what the CLI shows too.
    """
    if window <= CODEX_BASELINE_TOKENS:
        return 0.0
    total = _number(last_usage.get("total_tokens"))
    if total is None:
        total = (_number(last_usage.get("input_tokens")) or 0.0) + (
            _number(last_usage.get("output_tokens")) or 0.0
        )
    effective = float(window - CODEX_BASELINE_TOKENS)
    used = max(total - CODEX_BASELINE_TOKENS, 0.0)
    return min(1.0, max(0.0, used / effective))


def apply_hook_permission_mode(
    record: SessionRecord, payload: dict[str, Any], *, now: float
) -> list[str]:
    """Take the permission mode a root-scoped hook payload reports."""
    mode = _token(payload.get("permission_mode"))
    if mode is None:
        return []
    changed: list[str] = []
    before = record.harness_status.permission_mode
    _set(record.harness_status, "permission_mode", mode, SOURCE_HOOK, changed)
    _stamp(record, now)
    _log_setting_change(record, "permission_mode", before, SOURCE_HOOK)
    return changed


def apply_codex_settings(record: SessionRecord, source: dict[str, Any], *, now: float) -> list[str]:
    """Take effort and service tier from a `turn_context` or `thread_settings` block.

    `turn_context` spells the effort `effort` and `thread_settings_applied`
    spells it `reasoning_effort`; the tier appears only in the latter. Read
    from whichever the block carries, because the opening turn and a
    mid-session `/model` switch arrive on different records.
    """
    changed: list[str] = []
    status = record.harness_status
    effort = _token(source.get("effort")) or _token(source.get("reasoning_effort"))
    if effort is not None:
        before = status.effort
        _set(status, "effort", effort, SOURCE_CODEX_ROLLOUT, changed)
        _log_setting_change(record, "effort", before, SOURCE_CODEX_ROLLOUT)
    tier = _token(source.get("service_tier"))
    if tier is not None:
        _set(status, "fast_mode", tier.lower() == "fast", SOURCE_CODEX_ROLLOUT, changed)
    if changed or effort is not None or tier is not None:
        _stamp(record, now)
    return changed


def _codex_window(raw: object) -> tuple[str | None, RateLimitWindow | None]:
    if not isinstance(raw, dict):
        return None, None
    used = _number(raw.get("used_percent"))
    if used is None:
        return None, None
    minutes = _positive_int(raw.get("window_minutes"))
    resets = _number(raw.get("resets_at"))
    key = _CODEX_WINDOW_KEYS.get(minutes) if minutes is not None else None
    return key, RateLimitWindow(used_pct=used, resets_at=resets, window_minutes=minutes)


def apply_codex_rate_limits(
    record: SessionRecord, limits: dict[str, Any], *, now: float
) -> list[str]:
    """Take the `primary`/`secondary` windows a persisted `token_count` carries.

    Each is keyed by its length: the 300-minute window is the five-hour limit
    and the 10080-minute one the weekly limit, whichever slot Codex put it in.
    A window of a length this table does not know keeps its slot name, so it is
    still reported rather than dropped.
    """
    changed: list[str] = []
    status = record.harness_status
    for slot in ("primary", "secondary"):
        key, window = _codex_window(limits.get(slot))
        if window is None:
            continue
        key = key or slot
        if status.rate_limits.get(key) != window:
            status.rate_limits[key] = window
            changed.append(f"rate_limits.{key}")
    if changed:
        status.sources["rate_limits"] = SOURCE_CODEX_ROLLOUT
        _stamp(record, now)
        log.debug(
            "session %s rate limits updated (%s): %s", record.id, SOURCE_CODEX_ROLLOUT, changed
        )
    return changed


def _claude_window(raw: object) -> RateLimitWindow | None:
    if not isinstance(raw, dict):
        return None
    used = _number(raw.get("used_percentage"))
    if used is None:
        return None
    return RateLimitWindow(used_pct=used, resets_at=_number(raw.get("resets_at")))


def apply_claude_status_snapshot(
    record: SessionRecord, payload: dict[str, Any], *, now: float
) -> list[str]:
    """Fold one status-line JSON snapshot into the record.

    Beyond `harness_status` this writes the three measurements the CLI knows
    better than the transcript does: its own context window size and used
    percentage, and its own cost figure. The transcript path keeps publishing
    tokens, but reads the window from here once it is known, so the two agree.
    """
    changed: list[str] = []
    status = record.harness_status
    source = SOURCE_CLAUDE_STATUSLINE

    effort_block = payload.get("effort")
    effort = _token(effort_block.get("level")) if isinstance(effort_block, dict) else None
    if effort is not None:
        before = status.effort
        _set(status, "effort", effort, source, changed)
        _log_setting_change(record, "effort", before, source)

    style_block = payload.get("output_style")
    style = style_block.get("name") if isinstance(style_block, dict) else None
    if isinstance(style, str) and style.strip():
        _set(status, "output_style", style.strip()[:80], source, changed)

    fast = payload.get("fast_mode")
    if isinstance(fast, bool):
        _set(status, "fast_mode", fast, source, changed)

    thinking_block = payload.get("thinking")
    thinking = thinking_block.get("enabled") if isinstance(thinking_block, dict) else None
    if isinstance(thinking, bool):
        _set(status, "thinking", thinking, source, changed)

    limits = payload.get("rate_limits")
    if isinstance(limits, dict):
        for name, key in (
            ("five_hour", RATE_LIMIT_FIVE_HOUR),
            ("seven_day", RATE_LIMIT_SEVEN_DAY),
            ("spend_limit", RATE_LIMIT_SPEND),
        ):
            window = _claude_window(limits.get(name))
            if window is not None and status.rate_limits.get(key) != window:
                status.rate_limits[key] = window
                changed.append(f"rate_limits.{key}")
        if any(item.startswith("rate_limits.") for item in changed):
            status.sources["rate_limits"] = source

    context = payload.get("context_window")
    if isinstance(context, dict):
        size = _positive_int(context.get("context_window_size"))
        if size is not None:
            _set(status, "context_window_size", size, source, changed)
            if record.context_window != size:
                record.context_window = size
                changed.append("context_window")
        used = _number(context.get("used_percentage"))
        if used is not None:
            fraction = min(1.0, max(0.0, used / 100.0))
            if record.context_pct != fraction:
                record.context_pct = fraction
                changed.append("context_pct")
            record.context_peak_pct = max(record.context_peak_pct, fraction)

    cost = payload.get("cost")
    total = _number(cost.get("total_cost_usd")) if isinstance(cost, dict) else None
    if total is not None and total >= 0 and record.cost_usd != total:
        record.cost_usd = total
        changed.append("cost_usd")

    _stamp(record, now)
    if changed:
        log.debug("session %s status line snapshot applied: %s", record.id, changed)
    return changed


def status_summary(record: SessionRecord) -> dict[str, Any]:
    """Compact event body: the settings, and each limit's percentage, no prose."""
    status = record.harness_status
    return {
        "effort": status.effort,
        "permission_mode": status.permission_mode,
        "output_style": status.output_style,
        "fast_mode": status.fast_mode,
        "thinking": status.thinking,
        "rate_limits": {key: window.used_pct for key, window in status.rate_limits.items()},
        "context_pct": record.context_pct,
        "cost_usd": record.cost_usd,
    }
