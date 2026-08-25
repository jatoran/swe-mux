from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from .background_tasks import background
from .bounded_subprocess import run_bounded
from .config import CCUSAGE_PACKAGE, Config
from .event_bus import EventBus

USAGE_REFRESH_LOOP = "usage-refresh"
MAX_USAGE_OUTPUT_BYTES = 10 * 1024 * 1024
#: ccusage's diagnostics are read as a head slice for the operator's error message,
#: so a cap far below the stdout one costs nothing and bounds a chatty failure.
MAX_USAGE_STDERR_BYTES = 64 * 1024
#: What one `ccusage daily --json --by-agent` may take. This is a whole-corpus
#: read, so it scales with how much the agents on this host have ever written
#: rather than with anything the daemon controls, and 30s stopped being enough
#: for it in 2026-08 — every scheduled refresh timed out from 2026-08-21 on.
#:
#: Measured 2026-08-24 on the primary host (36,529 Claude transcripts, ~21 GB of
#: transcript corpus across `~/.claude/projects` and `~/.codex/sessions`), running
#: the daemon's exact command from a shell: 33.9s cold, 10.3s with the OS file
#: cache warm, 5.8s warm with `--offline` (so ~4.4s of it is the pricing fetch).
#: Exit code 0 and an empty stderr in every run — the command was never hung, it
#: was working, and the bound was simply under the cost.
#:
#: 120s is four times the measured cold cost, which is the room a corpus that only
#: grows needs. It is not felt on the scheduled path (`ccusage_refresh_minutes`
#: defaults to 180) and bounds the operator-initiated `POST /api/usage/refresh`,
#: which awaits this inline. `--offline` is deliberately NOT added to the default
#: command: it would buy back 4s by changing what the dollar figures are computed
#: from, and a cost basis is not a thing to trade for latency.
USAGE_TIMEOUT_SECONDS = 120.0
CACHE_VERSION = 3

logger = logging.getLogger(__name__)

CCUSAGE_SOURCE_LABELS = {
    "claude": "Claude Code",
    "codex": "Codex",
    "opencode": "OpenCode",
    "amp": "Amp",
    "droid": "Droid",
    "codebuff": "CodeBuff",
    "hermes": "Hermes",
    "pi": "Pi",
    "goose": "Goose",
    "openclaw": "OpenClaw",
    "kilo": "Kilo Code",
    "kimi": "Kimi",
    "qwen": "Qwen Code",
    "copilot": "GitHub Copilot",
    "gemini": "Gemini CLI",
}


class UsageAdapterError(ValueError):
    pass


def prepare_usage_command(command: list[str], *, windows: bool | None = None) -> list[str]:
    if not command:
        raise UsageAdapterError("ccusage command is not configured")
    resolved = shutil.which(command[0])
    if resolved is None:
        raise UsageAdapterError(
            f"ccusage executable unavailable: {command[0]}; "
            f"install it with: npm install -g {CCUSAGE_PACKAGE}"
        )
    windows = os.name == "nt" if windows is None else windows
    if windows and Path(resolved).suffix.casefold() in {".cmd", ".bat"}:
        return [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/c",
            resolved,
            *command[1:],
        ]
    return [resolved, *command[1:]]


def _number(item: dict[str, Any], *names: str) -> float:
    for name in names:
        value = item.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return 0.0


def _has_number(item: dict[str, Any], *names: str) -> bool:
    return any(
        isinstance(item.get(name), (int, float)) and not isinstance(item.get(name), bool)
        for name in names
    )


def _items(payload: dict[str, Any], *names: str) -> list[dict[str, Any]]:
    for name in names:
        value = payload.get(name)
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return value
    data = payload.get("data")
    if isinstance(data, dict):
        return _items(data, *names)
    return []


def _normalize_row(item: dict[str, Any], *, key: str, value: str) -> dict[str, Any]:
    input_tokens = int(_number(item, "inputTokens", "input_tokens"))
    output_tokens = int(_number(item, "outputTokens", "output_tokens"))
    cache_create = int(
        _number(item, "cacheCreationTokens", "cache_creation_tokens", "cachedInputTokens")
    )
    cache_read = int(_number(item, "cacheReadTokens", "cache_read_tokens"))
    total = int(
        _number(item, "totalTokens", "total_tokens")
        or input_tokens + output_tokens + cache_create + cache_read
    )
    cost_fields = ("totalCost", "total_cost", "costUSD", "cost_usd", "cost")
    cost = _number(item, *cost_fields)
    cost_method = item.get("__cost_method")
    if cost_method not in {"source_estimate", "proportional", "unavailable"}:
        cost_method = "source_estimate" if _has_number(item, *cost_fields) else "unavailable"
    return {
        key: value,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_tokens": cache_create,
        "cache_read_tokens": cache_read,
        "total_tokens": total,
        "cost_usd": round(cost, 6),
        "cost_is_estimate": True,
        "cost_method": cost_method,
    }


def _sum_rows(rows: list[dict[str, Any]], key: str, value: str) -> dict[str, Any]:
    methods = {str(row.get("cost_method") or "unavailable") for row in rows}
    cost_method = next(iter(methods)) if len(methods) == 1 else "mixed"
    result: dict[str, Any] = {
        key: value,
        "cost_is_estimate": True,
        "cost_method": cost_method,
    }
    for field in (
        "input_tokens",
        "output_tokens",
        "cache_creation_tokens",
        "cache_read_tokens",
        "total_tokens",
        "cost_usd",
    ):
        result[field] = sum(float(row.get(field, 0)) for row in rows)
        if field != "cost_usd":
            result[field] = int(result[field])
        else:
            result[field] = round(result[field], 6)
    return result


def _daily_model_items(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Accept both legacy modelBreakdowns arrays and ccusage v20 model maps."""
    legacy = item.get("modelBreakdowns") or item.get("model_breakdowns")
    if isinstance(legacy, list):
        return [model for model in legacy if isinstance(model, dict)]
    source = item.get("models")
    if isinstance(source, list):
        return [model for model in source if isinstance(model, dict)]
    if not isinstance(source, dict):
        return []
    day_tokens = _number(item, "totalTokens", "total_tokens")
    day_cost = _number(item, "totalCost", "total_cost", "costUSD", "cost_usd", "cost")
    result: list[dict[str, Any]] = []
    for name, metrics in source.items():
        if not isinstance(metrics, dict):
            continue
        row = {**metrics, "modelName": str(name)}
        model_tokens = _number(metrics, "totalTokens", "total_tokens")
        if (
            day_cost
            and day_tokens
            and not _has_number(
                metrics, "totalCost", "total_cost", "costUSD", "cost_usd", "cost"
            )
        ):
            row["costUSD"] = day_cost * model_tokens / day_tokens
            row["__cost_method"] = "proportional"
        result.append(row)
    return result


def normalize_usage(payload: object, source: str, provenance: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise UsageAdapterError("ccusage output must be a JSON object")
    daily = [
        _normalize_row(item, key="date", value=str(item.get("date") or ""))
        for item in _items(payload, "daily", "days")
        if item.get("date")
    ]
    sessions = [
        _normalize_row(
            item,
            key="session_id",
            value=str(item.get("sessionId") or item.get("session_id") or ""),
        )
        for item in _items(payload, "sessions", "session")
        if item.get("sessionId") or item.get("session_id")
    ]
    model_source = _items(payload, "models", "modelBreakdowns", "model_breakdowns")
    model_daily: list[dict[str, Any]] = []
    if not model_source:
        for day in _items(payload, "daily", "days"):
            for item in _daily_model_items(day):
                row = _normalize_row(
                    item,
                    key="model",
                    value=str(item.get("modelName") or item.get("model") or "unknown"),
                )
                row["date"] = str(day.get("date") or "")
                model_daily.append(row)
    model_rows = model_daily or [
        _normalize_row(
            item,
            key="model",
            value=str(item.get("modelName") or item.get("model") or "unknown"),
        )
        for item in model_source
    ]
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in model_rows:
        by_model[str(row["model"])].append(row)
    models = [_sum_rows(rows, "model", model) for model, rows in sorted(by_model.items())]
    by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in daily:
        by_month[str(row["date"])[:7]].append(row)
    monthly = [_sum_rows(rows, "month", month) for month, rows in sorted(by_month.items())]
    source_rows = daily or sessions or models
    if not source_rows and not isinstance(payload.get("totals"), dict):
        raise UsageAdapterError("ccusage JSON contains no supported aggregate rows")
    totals_source = payload.get("totals")
    totals = (
        _normalize_row(totals_source, key="scope", value="all")
        if isinstance(totals_source, dict)
        else _sum_rows(source_rows, "scope", "all")
    )
    return {
        "source_id": source,
        "source_label": CCUSAGE_SOURCE_LABELS.get(source, source.replace("-", " ").title()),
        "collector_id": "ccusage",
        "daily": daily,
        "monthly": monthly,
        "sessions": sessions,
        "model_daily": model_daily,
        "models": models,
        "totals": totals,
        "provenance": provenance,
    }


def normalize_usage_sources(
    payload: object, provenance: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Split ccusage ``--by-agent`` rows into independently filterable sources."""
    if not isinstance(payload, dict):
        raise UsageAdapterError("ccusage output must be a JSON object")
    source_days: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for day in _items(payload, "daily", "days"):
        date = str(day.get("date") or day.get("period") or "")
        agents = day.get("agents")
        if isinstance(agents, dict):
            agent_rows = [
                {**metrics, "agent": source}
                for source, metrics in agents.items()
                if isinstance(metrics, dict)
            ]
        elif isinstance(agents, list):
            agent_rows = [item for item in agents if isinstance(item, dict)]
        else:
            agent_rows = []
        for item in agent_rows:
            source = str(item.get("agent") or item.get("source") or "").strip().casefold()
            if not source or not date:
                continue
            source_days[source].append({**item, "date": date})
    if not source_days:
        raise UsageAdapterError("ccusage JSON contains no per-source --by-agent rows")
    return {
        source: normalize_usage({"daily": days}, source, provenance)
        for source, days in sorted(source_days.items())
    }


@dataclass(slots=True)
class RefreshState:
    status: str = "disabled"
    error: str | None = None
    refreshed_at: float | None = None


class UsageManager:
    def __init__(self, config: Config, events: EventBus) -> None:
        self.config = config
        self.events = events
        self.cache_path = config.data_dir / "usage-cache.json"
        self.cache: dict[str, Any] = self._load_cache()
        self.state = RefreshState()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        if config.ccusage_enabled:
            self.state.status = "stale" if self.cache else "ready"
            self.state.refreshed_at = self.cache.get("updated_at")

    def _load_cache(self) -> dict[str, Any]:
        try:
            raw: object = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return {}
            payload: dict[str, Any] = raw
            if payload.get("version") == CACHE_VERSION:
                return payload
            if payload.get("version") == 2 and isinstance(payload.get("providers"), dict):
                sources = {}
                for source, item in payload["providers"].items():
                    if not isinstance(item, dict):
                        continue
                    migrated = dict(item)
                    migrated.pop("provider", None)
                    migrated.update(
                        source_id=source,
                        source_label=CCUSAGE_SOURCE_LABELS.get(source, source.title()),
                        collector_id="ccusage",
                    )
                    sources[source] = migrated
                migrated_cache = {
                    "version": CACHE_VERSION,
                    "updated_at": payload.get("updated_at"),
                    "sources": sources,
                }
                logger.info(
                    "usage cache migrated",
                    extra={
                        "from_version": 2,
                        "to_version": CACHE_VERSION,
                        "source_count": len(sources),
                    },
                )
                return migrated_cache
            return {}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def start(self) -> None:
        self._task = background.start(USAGE_REFRESH_LOOP, self._background)

    async def stop(self) -> None:
        await background.stop(USAGE_REFRESH_LOOP)
        self._task = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.config.ccusage_enabled,
            "package": CCUSAGE_PACKAGE,
            "install_command": f"npm install -g {CCUSAGE_PACKAGE}",
            "refresh_minutes": self.config.ccusage_refresh_minutes,
            "refreshing": self._lock.locked(),
            "collector": {
                "id": "ccusage",
                "status": self.state.status,
                "error": self.state.error,
                "refreshed_at": self.state.refreshed_at,
            },
            "cache": self.cache,
        }

    async def refresh(self) -> dict[str, Any]:
        if not self.config.ccusage_enabled:
            raise UsageAdapterError("usage analytics is disabled")
        if self._lock.locked():
            raise UsageAdapterError("a usage refresh is already running")
        async with self._lock:
            await self._refresh()
        return self.snapshot()

    async def _refresh(self) -> None:
        self.state.status = "refreshing"
        self.state.error = None
        command = self.config.usage_command
        started = time.monotonic()
        refresh_id = uuid4().hex
        logger.info(
            "ccusage refresh started",
            extra={
                "operation_id": refresh_id,
                "legacy_override_count": len(self.config.usage_commands),
            },
        )
        try:
            output = await self._invoke(command, operation_id=refresh_id)
            payload = json.loads(output)
            provenance = {
                "adapter": "ccusage-by-agent-json-v1",
                "package": CCUSAGE_PACKAGE,
            }
            sources = normalize_usage_sources(payload, provenance)
            for source, override in sorted(self.config.usage_commands.items()):
                focused_payload = json.loads(
                    await self._invoke(override, operation_id=refresh_id)
                )
                sources[source] = normalize_usage(
                    focused_payload,
                    source,
                    {"adapter": "ccusage-source-json-v1", "package": CCUSAGE_PACKAGE},
                )
            now = time.time()
            self.cache = {"version": CACHE_VERSION, "updated_at": now, "sources": sources}
            self._write_cache()
            self.state.status = "ready"
            self.state.refreshed_at = now
            logger.info(
                "ccusage refresh completed",
                extra={
                    "usage_source_count": len(sources),
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "operation_id": refresh_id,
                },
            )
            await self.events.emit(
                "usage_refreshed",
                source="ccusage",
                usage_sources=sorted(sources),
                operation_id=refresh_id,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError, UsageAdapterError) as exc:
            self.state.status = "stale" if self.cache.get("sources") else "error"
            self.state.error = str(exc)
            logger.warning(
                "ccusage refresh failed: %s",
                exc,
                extra={
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "error_type": type(exc).__name__,
                    "operation_id": refresh_id,
                },
            )
            await self.events.emit(
                "usage_refresh_failed",
                source="ccusage",
                error=str(exc),
                operation_id=refresh_id,
            )

    async def _invoke(self, command: list[str], *, operation_id: str | None = None) -> str:
        """Run ccusage once and return its stdout, or raise a UsageAdapterError.

        The 10 MiB limit used to be checked *after* `communicate()` had already
        buffered the whole answer, so it bounded the error message and not the
        daemon's memory. The bounded runner enforces it while reading, which is what
        makes the number mean what it says.

        `operation_id` is the refresh this run belongs to, so the runner's own
        `bounded_command_timed_out` line joins the adapter's `ccusage refresh
        failed` line and the `usage_refresh_failed` event instead of being a
        third, anonymous account of one failure.
        """
        prepared = prepare_usage_command(command)
        try:
            outcome = await run_bounded(
                prepared,
                label="ccusage",
                timeout_seconds=USAGE_TIMEOUT_SECONDS,
                output_limit=MAX_USAGE_OUTPUT_BYTES,
                stderr_limit=MAX_USAGE_STDERR_BYTES,
                operation_id=operation_id,
            )
        except OSError as exc:
            raise UsageAdapterError(
                f"ccusage could not start; install {CCUSAGE_PACKAGE} or configure its "
                f"command: {exc}"
            ) from exc
        if outcome.timed_out:
            # Name the bound and what was spent against it. "timed out" alone left
            # a reader unable to tell a hung command from one that is simply
            # slower than the daemon allows - which is what it turned out to be.
            raise UsageAdapterError(
                f"ccusage refresh timed out after {USAGE_TIMEOUT_SECONDS:g}s "
                f"(ran for {outcome.duration_ms / 1000:.1f}s). Its cost is the size of "
                "this host's transcript corpus; run the command in a shell to see how "
                "long it now takes."
            ) from None
        if outcome.stdout_truncated:
            raise UsageAdapterError("ccusage output exceeded 10 MiB")
        if outcome.exit_code:
            detail = outcome.stderr.decode("utf-8", "replace").strip()[:2000]
            raise UsageAdapterError(detail or f"ccusage exited with status {outcome.exit_code}")
        return outcome.stdout.decode("utf-8")

    def _write_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.cache, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.cache_path)

    def clear(self) -> dict[str, Any]:
        source_count = len(self.cache.get("sources") or {})
        self.cache = {}
        try:
            self.cache_path.unlink()
        except FileNotFoundError:
            pass
        self.state.status = "ready" if self.config.ccusage_enabled else "disabled"
        self.state.error = None
        self.state.refreshed_at = None
        logger.info("usage cache cleared", extra={"usage_source_count": source_count})
        return self.snapshot()

    async def _background(self) -> None:
        while True:
            minutes = self.config.ccusage_refresh_minutes
            if not self.config.ccusage_enabled or minutes <= 0:
                await asyncio.sleep(60)
                continue
            await asyncio.sleep(minutes * 60)
            with background.iteration(USAGE_REFRESH_LOOP):
                if self.config.ccusage_enabled and not self._lock.locked():
                    await self.refresh()
