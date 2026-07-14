from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import CCUSAGE_PACKAGE, Config
from .event_bus import EventBus

MAX_USAGE_OUTPUT_BYTES = 10 * 1024 * 1024
USAGE_TIMEOUT_SECONDS = 30.0
CACHE_VERSION = 2


class UsageAdapterError(ValueError):
    pass


def prepare_usage_command(
    command: list[str], *, windows: bool | None = None
) -> list[str]:
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
    cost = _number(item, "totalCost", "total_cost", "costUSD", "cost_usd", "cost")
    return {
        key: value,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_tokens": cache_create,
        "cache_read_tokens": cache_read,
        "total_tokens": total,
        "cost_usd": round(cost, 6),
        "cost_is_estimate": True,
    }


def _sum_rows(rows: list[dict[str, Any]], key: str, value: str) -> dict[str, Any]:
    result: dict[str, Any] = {key: value, "cost_is_estimate": True}
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
        if day_cost and day_tokens and not _number(
            metrics, "totalCost", "total_cost", "costUSD", "cost_usd", "cost"
        ):
            row["costUSD"] = day_cost * model_tokens / day_tokens
        result.append(row)
    return result


def normalize_usage(payload: object, provider: str, provenance: dict[str, Any]) -> dict[str, Any]:
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
        "provider": provider,
        "daily": daily,
        "monthly": monthly,
        "sessions": sessions,
        "model_daily": model_daily,
        "models": models,
        "totals": totals,
        "provenance": provenance,
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
        self.states = {"claude": RefreshState(), "codex": RefreshState()}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        if config.ccusage_enabled:
            for state in self.states.values():
                state.status = "stale" if self.cache else "ready"

    def _load_cache(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return payload if payload.get("version") == CACHE_VERSION else {}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def start(self) -> None:
        self._task = asyncio.create_task(self._background(), name="usage-refresh")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.config.ccusage_enabled,
            "package": CCUSAGE_PACKAGE,
            "install_command": f"npm install -g {CCUSAGE_PACKAGE}",
            "refresh_minutes": self.config.ccusage_refresh_minutes,
            "refreshing": self._lock.locked(),
            "states": {
                key: {
                    "status": value.status,
                    "error": value.error,
                    "refreshed_at": value.refreshed_at,
                }
                for key, value in self.states.items()
            },
            "cache": self.cache,
        }

    async def refresh(self, provider: str | None = None) -> dict[str, Any]:
        if not self.config.ccusage_enabled:
            raise UsageAdapterError("usage analytics is disabled")
        if provider and provider not in self.states:
            raise UsageAdapterError("provider must be claude or codex")
        if self._lock.locked():
            raise UsageAdapterError("a usage refresh is already running")
        targets = [provider] if provider else ["claude", "codex"]
        async with self._lock:
            for target in targets:
                await self._refresh_one(target)
        return self.snapshot()

    async def _refresh_one(self, provider: str) -> None:
        state = self.states[provider]
        state.status = "refreshing"
        state.error = None
        command = (
            self.config.ccusage_claude_command
            if provider == "claude"
            else self.config.ccusage_codex_command
        )
        try:
            output = await self._invoke(command)
            payload = json.loads(output)
            normalized = normalize_usage(
                payload,
                provider,
                {
                    "adapter": "ccusage-json-v1",
                    "command": command,
                    "package": (
                        CCUSAGE_PACKAGE
                        if Path(command[0]).stem.casefold() == "ccusage"
                        else next(
                            (part for part in command if "ccusage" in part), command[0]
                        )
                    ),
                },
            )
            now = time.time()
            providers = dict(self.cache.get("providers") or {})
            providers[provider] = normalized
            self.cache = {"version": CACHE_VERSION, "updated_at": now, "providers": providers}
            self._write_cache()
            state.status = "ready"
            state.refreshed_at = now
            await self.events.emit("usage_refreshed", source="ccusage", provider=provider)
        except (OSError, ValueError, TypeError, json.JSONDecodeError, UsageAdapterError) as exc:
            state.status = "stale" if self.cache.get("providers", {}).get(provider) else "error"
            state.error = str(exc)
            await self.events.emit(
                "usage_refresh_failed", source="ccusage", provider=provider, error=str(exc)
            )

    async def _invoke(self, command: list[str]) -> str:
        prepared = prepare_usage_command(command)
        try:
            process = await asyncio.create_subprocess_exec(
                *prepared,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise UsageAdapterError(
                f"ccusage could not start; install {CCUSAGE_PACKAGE} or configure its "
                f"command: {exc}"
            ) from exc
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=USAGE_TIMEOUT_SECONDS
            )
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise UsageAdapterError("ccusage refresh timed out") from None
        if len(stdout) > MAX_USAGE_OUTPUT_BYTES:
            raise UsageAdapterError("ccusage output exceeded 10 MiB")
        if process.returncode:
            detail = stderr.decode("utf-8", "replace").strip()[:2000]
            raise UsageAdapterError(detail or f"ccusage exited with status {process.returncode}")
        return stdout.decode("utf-8")

    def _write_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.cache, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.cache_path)

    def clear(self) -> dict[str, Any]:
        self.cache = {}
        try:
            self.cache_path.unlink()
        except FileNotFoundError:
            pass
        for state in self.states.values():
            state.status = "ready" if self.config.ccusage_enabled else "disabled"
            state.error = None
            state.refreshed_at = None
        return self.snapshot()

    async def _background(self) -> None:
        while True:
            minutes = self.config.ccusage_refresh_minutes
            if not self.config.ccusage_enabled or minutes <= 0:
                await asyncio.sleep(60)
                continue
            await asyncio.sleep(minutes * 60)
            if self.config.ccusage_enabled and not self._lock.locked():
                await self.refresh()
