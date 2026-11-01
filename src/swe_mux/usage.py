"""Historical agent usage, collected by the locally installed `ccusage` CLI.

The collector is the expensive part, and the design is shaped by what it cost on
2026-10-31: the scheduled refresh re-read the whole transcript corpus (46 GB on the
operator's host) every three hours, on one thread per logical CPU, at the same
priority as the browser in front of the operator, and froze the PC each time. Three
rules follow, and each is load-bearing:

- **Read only what can have changed.** Past days do not change, so a refresh asks
  ccusage for the days since the last successful refresh (`--since`, with one day of
  overlap) and merges them over the cached history. The whole corpus is read only
  when there is no history to merge into, or when the operator asks for a rebuild.
  How much `--since` saves is ccusage's: from 20.0.26 it skips Claude Code
  transcripts last written before the window, but any transcript written inside it
  is still read whole, so a host whose live sessions have very large transcripts
  still reads gigabytes. Older versions read every Claude transcript regardless, and
  the collector says so.
- **Run it as background work the OS enforces** (`background_containment`): idle
  CPU class, a hard cap on its CPU share, very-low I/O and memory priority, and a
  job that owns the tree. Containment makes the run slow by design, so its bound is
  a no-progress window (`USAGE_STALL_SECONDS`) under a generous absolute ceiling,
  not a wall-clock guess at how long a slow scan should take.
- **Never stack it on a host that is already saturated.** A scheduled refresh waits
  (bounded) while host CPU or memory is above its threshold. An operator's own
  refresh never waits.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import platform
import re
import shutil
import sys
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from .background_containment import BACKGROUND_BATCH, ResourceSample
from .background_tasks import background
from .bounded_subprocess import ProcessOutcome, run_bounded
from .config import CCUSAGE_PACKAGE, Config
from .event_bus import EventBus
from .host_metrics import host_cpu_times, host_memory_percent
from .http_support import log_task_failure

USAGE_REFRESH_LOOP = "usage-refresh"
MAX_USAGE_OUTPUT_BYTES = 10 * 1024 * 1024
#: ccusage's diagnostics are read as a head slice for the operator's error message,
#: so a cap far below the stdout one costs nothing and bounds a chatty failure.
MAX_USAGE_STDERR_BYTES = 64 * 1024
#: The absolute ceiling on one ccusage run. It is a backstop, not the working bound:
#: a contained full rebuild of a large corpus yields to everything else on the host
#: and can legitimately take tens of minutes, and a ceiling sized to the 2026-08
#: measurement (33.9 s cold for 21 GB, uncontained) would kill it. Two hours is far
#: past anything a progressing scan needs; a hung one is caught by the stall window.
USAGE_CEILING_SECONDS = 2 * 60 * 60
#: A contained run that has used no CPU and read nothing for this long is hung, not
#: slow: a starved idle-class thread still runs within seconds of the OS's
#: anti-starvation boost, so five silent minutes is far outside a working scan.
USAGE_STALL_SECONDS = 300.0
#: `ccusage --version` is a banner, not a scan.
USAGE_VERSION_TIMEOUT_SECONDS = 30.0
CACHE_VERSION = 3

#: The first release that skips Claude Code transcripts last written before
#: `--since` (`split_files_before_since`, ccusage v20.0.26). Earlier releases read
#: every transcript and filter afterwards, so a windowed refresh costs a full scan.
CCUSAGE_PRUNING_VERSION = (20, 0, 26)
#: Days re-read before the last refresh's date, for entries a CLI flushed after
#: midnight and for a clock that moved.
WINDOW_OVERLAP_DAYS = 1

#: The schedule is re-read this often, so a change to the cadence or to the switch
#: applies within a minute instead of after the previous cadence's sleep.
SCHEDULE_TICK_SECONDS = 60.0
#: A daemon that has just started is busy rebuilding its own state; a scheduled
#: refresh that has fallen due waits this long after startup.
STARTUP_GRACE_SECONDS = 300.0
#: Host load above which a *scheduled* refresh waits rather than adding to it.
HOST_BUSY_CPU_PERCENT = 85.0
HOST_BUSY_MEMORY_PERCENT = 90.0
#: The CPU reading is a delta over this window, never a single instant.
HOST_SAMPLE_SECONDS = 5.0
HOST_RECHECK_SECONDS = 60.0
#: A busy host delays a scheduled refresh by at most this much; after that it runs
#: anyway, contained, so a host that is never idle still gets its numbers.
HOST_MAX_DEFERRAL_SECONDS = 60 * 60.0

REFRESH_MODES = ("auto", "incremental", "rebuild")
PLAN_INCREMENTAL = "incremental"
PLAN_REBUILD = "rebuild"
PLAN_CONFIGURED = "configured_window"

_DATE_BOUND_FLAGS = ("-s", "--since", "-u", "--until", "--last")
_VERSION_PATTERN = re.compile(r"(\d+)\.(\d+)\.(\d+)")
#: The `cli.js` a shim launches: `node_modules/ccusage/src/cli.js` from a global
#: npm prefix, `../ccusage/src/cli.js` from a project's `node_modules/.bin`.
_SHIM_ENTRY_PATTERN = re.compile(r"""(?P<path>[^"'\s]*ccusage[\\/]src[\\/]cli\.js)""")
_SHIM_BASE_PREFIXES = ("%dp0%", "%~dp0", "${basedir}", "$basedir")
_MAX_SHIM_BYTES = 64 * 1024

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


# --------------------------------------------------------------------------- #
# Launching ccusage                                                           #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class UsageLaunch:
    """How a configured command is actually started.

    `launcher` is ``native`` when the npm shim was bypassed for ccusage's own
    binary, ``shim`` when it runs through the npm shim (a `cmd.exe` wrapper on
    Windows), and ``command`` for anything that is not ccusage itself (`npx ...`).
    """

    program: list[str]
    args: list[str]
    launcher: str
    executable: str
    is_ccusage: bool

    @property
    def argv(self) -> list[str]:
        return [*self.program, *self.args]


def _native_package_name(platform_name: str, machine: str) -> str | None:
    plat = {"win32": "win32", "darwin": "darwin", "linux": "linux"}.get(platform_name)
    if plat is None and platform_name.startswith("linux"):
        plat = "linux"
    arch = {"amd64": "x64", "x86_64": "x64", "x64": "x64", "arm64": "arm64", "aarch64": "arm64"}
    resolved_arch = arch.get(machine.casefold())
    if plat is None or resolved_arch is None:
        return None
    return f"ccusage-{plat}-{resolved_arch}"


def _ccusage_package_dir(shim: Path) -> Path | None:
    """The `ccusage` npm package directory a shim launches, or ``None``.

    A POSIX npm install links the bin straight to `ccusage/src/cli.js`; the
    Windows `.cmd`/`.ps1` shims and the sh shim name it relative to their own
    directory, so the text is read (bounded) for that path.
    """
    try:
        real = shim.resolve()
    except OSError:
        real = shim
    if real.name == "cli.js" and real.parent.name == "src":
        return real.parent.parent
    try:
        if shim.stat().st_size > _MAX_SHIM_BYTES:
            return None
        text = shim.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for match in _SHIM_ENTRY_PATTERN.finditer(text):
        raw = match.group("path")
        for prefix in _SHIM_BASE_PREFIXES:
            if raw.startswith(prefix):
                raw = raw[len(prefix) :]
                break
        raw = raw.lstrip("\\/")
        candidate = Path(raw.replace("\\", "/"))
        entry = candidate if candidate.is_absolute() else shim.parent / candidate
        try:
            package = entry.parent.parent.resolve()
        except OSError:
            continue
        if package.is_dir():
            return package
    return None


def find_ccusage_native(
    shim: Path,
    *,
    platform_name: str | None = None,
    machine: str | None = None,
) -> Path | None:
    """ccusage's platform binary behind an npm shim, resolved the way Node would.

    ccusage 20's `cli.js` only spawns `@ccusage/ccusage-<platform>-<arch>/bin/ccusage`
    with the same arguments. Starting that binary directly drops the `cmd.exe` and
    node hops, so the containment and the accounting land on the process that does
    the reading. Any layout this cannot find falls back to the shim.
    """
    package = _ccusage_package_dir(shim)
    if package is None:
        return None
    name = _native_package_name(
        platform_name or sys.platform, machine if machine is not None else platform.machine()
    )
    if name is None:
        return None
    windows = (platform_name or sys.platform) == "win32"
    binary = "ccusage.exe" if windows else "ccusage"
    for base in [package, *package.parents][:8]:
        if base.name == "node_modules":
            continue
        candidate = base / "node_modules" / "@ccusage" / name / "bin" / binary
        if candidate.is_file():
            return candidate
    return None


def resolve_usage_launch(command: Sequence[str], *, windows: bool | None = None) -> UsageLaunch:
    if not command:
        raise UsageAdapterError("ccusage command is not configured")
    resolved = shutil.which(command[0])
    if resolved is None:
        raise UsageAdapterError(
            f"ccusage executable unavailable: {command[0]}; "
            f"install it with: npm install -g {CCUSAGE_PACKAGE}"
        )
    windows = os.name == "nt" if windows is None else windows
    args = list(command[1:])
    is_ccusage = Path(command[0]).stem.casefold() == "ccusage"
    if is_ccusage:
        native = find_ccusage_native(
            Path(resolved), platform_name="win32" if windows else None
        )
        if native is not None:
            return UsageLaunch([str(native)], args, "native", str(native), True)
    if windows and Path(resolved).suffix.casefold() in {".cmd", ".bat"}:
        program = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", resolved]
    else:
        program = [resolved]
    return UsageLaunch(program, args, "shim" if is_ccusage else "command", resolved, is_ccusage)


def prepare_usage_command(command: list[str], *, windows: bool | None = None) -> list[str]:
    """The argv a configured command runs as (see `resolve_usage_launch`)."""
    return resolve_usage_launch(command, windows=windows).argv


def parse_ccusage_version(text: str) -> str | None:
    match = _VERSION_PATTERN.search(text)
    return match.group(0) if match else None


def _version_tuple(version: str | None) -> tuple[int, int, int] | None:
    if not version:
        return None
    match = _VERSION_PATTERN.search(version)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def ccusage_prunes_by_since(version: str | None) -> bool | None:
    """Whether this version skips old Claude transcripts for `--since`; None if unknown."""
    parsed = _version_tuple(version)
    return None if parsed is None else parsed >= CCUSAGE_PRUNING_VERSION


# --------------------------------------------------------------------------- #
# Planning a refresh                                                          #
# --------------------------------------------------------------------------- #


def command_bounds_dates(command: Sequence[str]) -> bool:
    """Whether an operator-configured command already chooses its own date window."""
    for arg in command[1:]:
        if arg in _DATE_BOUND_FLAGS:
            return True
        if any(arg.startswith(f"{flag}=") for flag in _DATE_BOUND_FLAGS if flag.startswith("--")):
            return True
    return False


def local_timezone_key() -> str:
    """A stable name for the zone ccusage groups dates in (the host's local zone)."""
    names = "/".join(time.tzname)
    configured = os.environ.get("TZ")
    return f"{configured}|{names}" if configured else names


def _local_date(timestamp: float) -> dt.date:
    return dt.datetime.fromtimestamp(timestamp).date()


@dataclass(frozen=True, slots=True)
class RefreshPlan:
    kind: str
    since: dt.date | None
    reason: str

    @property
    def since_iso(self) -> str | None:
        return None if self.since is None else self.since.isoformat()

    def argv(self, command: Sequence[str]) -> list[str]:
        """``command`` with this plan's window, unless it already sets one."""
        if self.since is None or command_bounds_dates(command):
            return list(command)
        return [*command, "--since", self.since.strftime("%Y%m%d")]


def plan_refresh(
    requested: str, cache: dict[str, Any], command: Sequence[str], *, now: float
) -> RefreshPlan:
    """Decide how much history this refresh reads, and say why."""
    if requested not in REFRESH_MODES:
        raise UsageAdapterError(f"unknown refresh mode: {requested}")
    if command_bounds_dates(command):
        return RefreshPlan(PLAN_CONFIGURED, None, "the configured command sets its own date window")
    if requested == "rebuild":
        return RefreshPlan(PLAN_REBUILD, None, "a rebuild was requested")
    sources = cache.get("sources")
    updated_at = cache.get("updated_at")
    if not sources or not isinstance(updated_at, (int, float)) or isinstance(updated_at, bool):
        return RefreshPlan(PLAN_REBUILD, None, "there is no cached history to merge into")
    last = min(_local_date(float(updated_at)), _local_date(now))
    since = last - dt.timedelta(days=WINDOW_OVERLAP_DAYS)
    return RefreshPlan(PLAN_INCREMENTAL, since, f"history before {since.isoformat()} is cached")


# --------------------------------------------------------------------------- #
# Normalizing ccusage output                                                  #
# --------------------------------------------------------------------------- #


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


def _has_daily_list(payload: object) -> bool:
    """Whether a payload states its daily rows, even as an empty list."""
    if not isinstance(payload, dict):
        return False
    if isinstance(payload.get("daily"), list) or isinstance(payload.get("days"), list):
        return True
    data = payload.get("data")
    return _has_daily_list(data)


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
    for field_name in (
        "input_tokens",
        "output_tokens",
        "cache_creation_tokens",
        "cache_read_tokens",
        "total_tokens",
        "cost_usd",
    ):
        result[field_name] = sum(float(row.get(field_name, 0)) for row in rows)
        if field_name != "cost_usd":
            result[field_name] = int(result[field_name])
        else:
            result[field_name] = round(result[field_name], 6)
    return result


def _group_models(model_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in model_rows:
        by_model[str(row["model"])].append(row)
    return [_sum_rows(rows, "model", model) for model, rows in sorted(by_model.items())]


def _group_months(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in daily:
        by_month[str(row["date"])[:7]].append(row)
    return [_sum_rows(rows, "month", month) for month, rows in sorted(by_month.items())]


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
    models = _group_models(model_rows)
    monthly = _group_months(daily)
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
    payload: object, provenance: dict[str, Any], *, allow_empty: bool = False
) -> dict[str, dict[str, Any]]:
    """Split ccusage ``--by-agent`` rows into independently filterable sources.

    ``allow_empty`` is for a windowed refresh: a payload that states an empty daily
    list says "no usage since the window opened", which is an answer, not a fault.
    """
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
        if allow_empty and _has_daily_list(payload) and not _items(payload, "daily", "days"):
            return {}
        raise UsageAdapterError("ccusage JSON contains no per-source --by-agent rows")
    return {
        source: normalize_usage({"daily": days}, source, provenance)
        for source, days in sorted(source_days.items())
    }


def _dated_rows(rows: object) -> list[tuple[str, dict[str, Any]]]:
    if not isinstance(rows, list):
        return []
    return [(str(row.get("date") or ""), row) for row in rows if isinstance(row, dict)]


def _rows_on_or_after(rows: object, since_iso: str) -> list[dict[str, Any]]:
    return [row for date, row in _dated_rows(rows) if date >= since_iso]


def _rows_before(rows: object, since_iso: str) -> list[dict[str, Any]]:
    return [row for date, row in _dated_rows(rows) if date < since_iso]


def merge_usage_window(
    cached: dict[str, Any], fresh: dict[str, dict[str, Any]], since_iso: str
) -> dict[str, dict[str, Any]]:
    """Cached history before ``since_iso``, the fresh window from it on.

    The fresh window is authoritative for every date it covers: a cached row on or
    after ``since_iso`` that the window no longer reports (usage that has gone, or a
    source with nothing in the window) is dropped, not kept beside the new answer.
    Monthly, model and total rows are derived again from the merged days, so a
    total never mixes a cached aggregate with fresh days.
    """
    merged: dict[str, dict[str, Any]] = {}
    for source in sorted(set(cached) | set(fresh)):
        old_raw = cached.get(source)
        old: dict[str, Any] = old_raw if isinstance(old_raw, dict) else {}
        new = fresh.get(source)
        daily = sorted(
            _rows_before(old.get("daily"), since_iso)
            + (_rows_on_or_after(new.get("daily"), since_iso) if new else []),
            key=lambda row: str(row.get("date") or ""),
        )
        model_daily = sorted(
            _rows_before(old.get("model_daily"), since_iso)
            + (_rows_on_or_after(new.get("model_daily"), since_iso) if new else []),
            key=lambda row: (str(row.get("date") or ""), str(row.get("model") or "")),
        )
        sessions_raw = (new or {}).get("sessions") or old.get("sessions") or []
        sessions = [row for row in sessions_raw if isinstance(row, dict)]
        base = new or old
        if old.get("model_daily") or (new and new.get("model_daily")):
            # Derived from dated rows, so derived again - possibly to nothing.
            models = _group_models(model_daily)
        else:
            # A legacy payload with only undated model totals: nothing to window.
            models = [row for row in base.get("models") or [] if isinstance(row, dict)]
        if not daily and not sessions and not models:
            continue
        merged[source] = {
            "source_id": source,
            "source_label": base.get("source_label")
            or CCUSAGE_SOURCE_LABELS.get(source, source.replace("-", " ").title()),
            "collector_id": "ccusage",
            "daily": daily,
            "monthly": _group_months(daily),
            "sessions": sessions,
            "model_daily": model_daily,
            "models": models,
            "totals": _sum_rows(daily or sessions or models, "scope", "all"),
            "provenance": base.get("provenance") or {},
        }
    return merged


# --------------------------------------------------------------------------- #
# Host load                                                                   #
# --------------------------------------------------------------------------- #

HostLoad = tuple[float | None, float | None]


async def sample_host_load(seconds: float = HOST_SAMPLE_SECONDS) -> HostLoad:
    """Host ``(cpu_percent, memory_percent)``; CPU is a delta over ``seconds``."""
    first = host_cpu_times()
    await asyncio.sleep(seconds)
    second = host_cpu_times()
    cpu: float | None = None
    if first is not None and second is not None:
        busy = second[0] - first[0]
        total = second[1] - first[1]
        if total > 0:
            cpu = round(max(0.0, min(100.0, 100.0 * busy / total)), 1)
    return cpu, host_memory_percent()


def host_pressure(load: HostLoad) -> str | None:
    """Why the host is too busy to add a scan to, or ``None`` when it is not."""
    cpu, memory = load
    reasons = []
    if cpu is not None and cpu >= HOST_BUSY_CPU_PERCENT:
        reasons.append(f"CPU {cpu:.0f}%")
    if memory is not None and memory >= HOST_BUSY_MEMORY_PERCENT:
        reasons.append(f"memory {memory:.0f}%")
    return ", ".join(reasons) or None


# --------------------------------------------------------------------------- #
# The manager                                                                 #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class RefreshState:
    status: str = "disabled"
    error: str | None = None
    refreshed_at: float | None = None
    #: ``deferred`` while a scheduled refresh waits out a busy host, ``running``
    #: while ccusage runs, otherwise ``None``.
    phase: str | None = None
    deferred_reason: str | None = None
    #: The run in flight: its plan and the live resource readings.
    run: dict[str, Any] | None = None
    #: The last finished run, successful or not, while this daemon has been up.
    last_run: dict[str, Any] | None = None


@dataclass(slots=True)
class _VersionProbe:
    key: tuple[str, int, int] | None = None
    version: str | None = None


@dataclass(slots=True)
class _RunRecord:
    resources: dict[str, Any] | None = None
    launcher: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class UsageManager:
    def __init__(
        self,
        config: Config,
        events: EventBus,
        *,
        clock: Callable[[], float] = time.time,
        host_load: Callable[[], Awaitable[HostLoad]] | None = None,
    ) -> None:
        self.config = config
        self.events = events
        self.cache_path = config.data_dir / "usage-cache.json"
        self.cache: dict[str, Any] = self._load_cache()
        self.state = RefreshState()
        self._clock = clock
        self._host_load = host_load or sample_host_load
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._manual: asyncio.Task[None] | None = None
        self._version = _VersionProbe()
        self._run = _RunRecord()
        self._last_attempt_at: float | None = None
        self._not_before = 0.0
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

    # -- lifecycle -------------------------------------------------------- #

    def start(self) -> None:
        self._not_before = self._clock() + STARTUP_GRACE_SECONDS
        self._task = background.start(USAGE_REFRESH_LOOP, self._background)

    async def stop(self) -> None:
        await background.stop(USAGE_REFRESH_LOOP)
        self._task = None
        manual, self._manual = self._manual, None
        if manual is not None and not manual.done():
            manual.cancel()
            try:
                await manual
            except asyncio.CancelledError:
                pass

    # -- reading ---------------------------------------------------------- #

    def refreshing(self) -> bool:
        return self._lock.locked() or (self._manual is not None and not self._manual.done())

    def _collection(self) -> dict[str, Any]:
        collection = self.cache.get("collection")
        return collection if isinstance(collection, dict) else {}

    def advisories(self) -> list[str]:
        """What the operator should know about how these numbers were collected."""
        notes: list[str] = []
        version = self._version.version
        if ccusage_prunes_by_since(version) is False:
            minimum = ".".join(str(part) for part in CCUSAGE_PRUNING_VERSION)
            notes.append(
                f"ccusage {version} reads every Claude Code transcript on each refresh, "
                f"however short the window. Update to {minimum} or newer "
                f"(npm install -g {CCUSAGE_PACKAGE}) so a refresh reads only recently "
                "written transcripts."
            )
        history = self._collection().get("history")
        if isinstance(history, dict):
            computed_by = history.get("ccusage_version")
            if computed_by and version and computed_by != version:
                notes.append(
                    f"Older days were computed by ccusage {computed_by}. Rebuild history "
                    f"to recompute them with {version}."
                )
            zone = history.get("timezone")
            if zone and zone != local_timezone_key():
                notes.append(
                    f"Older days were grouped by date in another timezone ({zone}). "
                    "Rebuild history to regroup them."
                )
        last = self.state.last_run or self._collection().get("last_refresh")
        resources = last.get("resources") if isinstance(last, dict) else None
        degraded = resources.get("degraded") if isinstance(resources, dict) else None
        if degraded:
            notes.append(
                "The last refresh ran without full background containment: "
                + ", ".join(str(reason) for reason in degraded)
                + "."
            )
        return notes

    def snapshot(self) -> dict[str, Any]:
        last_run = self.state.last_run or self._collection().get("last_refresh")
        return {
            "enabled": self.config.ccusage_enabled,
            "package": CCUSAGE_PACKAGE,
            "install_command": f"npm install -g {CCUSAGE_PACKAGE}",
            "refresh_minutes": self.config.ccusage_refresh_minutes,
            "refreshing": self.refreshing(),
            "collector": {
                "id": "ccusage",
                "status": self.state.status,
                "error": self.state.error,
                "refreshed_at": self.state.refreshed_at,
                "phase": self.state.phase,
                "deferred_reason": self.state.deferred_reason,
                "run": dict(self.state.run) if self.state.run else None,
                "last_run": last_run,
                "ccusage_version": self._version.version,
                "advisories": self.advisories(),
            },
            "cache": self.cache,
        }

    # -- refreshing ------------------------------------------------------- #

    def _require_enabled(self, mode: str) -> None:
        if not self.config.ccusage_enabled:
            raise UsageAdapterError("usage analytics is disabled")
        if mode not in REFRESH_MODES:
            raise UsageAdapterError(f"unknown refresh mode: {mode}")

    async def refresh(self, mode: str = "auto", *, trigger: str = "manual") -> dict[str, Any]:
        """Run one refresh to completion and return the snapshot it produced."""
        self._require_enabled(mode)
        if self._lock.locked():
            raise UsageAdapterError("a usage refresh is already running")
        async with self._lock:
            await self._refresh(mode, trigger=trigger)
        return self.snapshot()

    def start_refresh(self, mode: str = "auto") -> dict[str, Any]:
        """Start an operator refresh in the background and answer at once.

        A contained scan can take minutes, which is too long to hold an HTTP request
        (or a phone's connection) open for; the caller reads `GET /api/usage` until
        `refreshing` is false. A refresh already in flight is not queued behind.
        """
        self._require_enabled(mode)
        if self.refreshing():
            return {**self.snapshot(), "started": False}
        self._manual = asyncio.create_task(self._run_manual(mode), name="usage-refresh-manual")
        self._manual.add_done_callback(log_task_failure)
        return {**self.snapshot(), "started": True}

    async def _run_manual(self, mode: str) -> None:
        async with self._lock:
            await self._refresh(mode, trigger="manual")

    async def _refresh(self, mode: str, *, trigger: str) -> None:
        self.state.status = "refreshing"
        self.state.error = None
        command = self.config.usage_command
        started = time.monotonic()
        started_at = self._clock()
        self._last_attempt_at = started_at
        refresh_id = uuid4().hex
        self._run = _RunRecord()
        plan: RefreshPlan | None = None
        try:
            plan = plan_refresh(mode, self.cache, command, now=started_at)
            version = await self._ccusage_version(command, operation_id=refresh_id)
            self.state.phase = "running"
            self.state.run = {
                "kind": plan.kind,
                "since": plan.since_iso,
                "reason": plan.reason,
                "trigger": trigger,
                "started_at": started_at,
                "read_bytes": None,
                "cpu_seconds": None,
            }
            logger.info(
                "ccusage refresh started",
                extra={
                    "operation_id": refresh_id,
                    "refresh_kind": plan.kind,
                    "since": plan.since_iso,
                    "reason": plan.reason,
                    "trigger": trigger,
                    "ccusage_version": version,
                    "legacy_override_count": len(self.config.usage_commands),
                },
            )
            windowed = plan.kind == PLAN_INCREMENTAL
            payload = json.loads(await self._invoke(plan.argv(command), operation_id=refresh_id))
            provenance = {
                "adapter": "ccusage-by-agent-json-v1",
                "package": CCUSAGE_PACKAGE,
            }
            fresh = normalize_usage_sources(payload, provenance, allow_empty=windowed)
            replaced: dict[str, dict[str, Any]] = {}
            for source, override in sorted(self.config.usage_commands.items()):
                focused_payload = json.loads(
                    await self._invoke(plan.argv(override), operation_id=refresh_id)
                )
                override_provenance = {
                    "adapter": "ccusage-source-json-v1",
                    "package": CCUSAGE_PACKAGE,
                }
                if windowed and command_bounds_dates(override):
                    # Its own window: it replaces the source rather than merging into it.
                    replaced[source] = normalize_usage(focused_payload, source, override_provenance)
                elif windowed and _has_daily_list(focused_payload) and not _items(
                    focused_payload, "daily", "days"
                ):
                    fresh.pop(source, None)
                else:
                    fresh[source] = normalize_usage(focused_payload, source, override_provenance)
            if windowed and plan.since_iso is not None:
                cached_sources = self.cache.get("sources")
                sources = merge_usage_window(
                    cached_sources if isinstance(cached_sources, dict) else {},
                    fresh,
                    plan.since_iso,
                )
                sources.update(replaced)
            else:
                sources = fresh
            now = self._clock()
            duration_ms = round((time.monotonic() - started) * 1000)
            last_run = self._run_summary(
                plan, trigger, version, started_at, duration_ms, error=None
            )
            self.cache = {
                "version": CACHE_VERSION,
                "updated_at": now,
                "sources": sources,
                "collection": self._next_collection(plan, version, now, last_run),
            }
            self._write_cache()
            self.state.status = "ready"
            self.state.refreshed_at = now
            self.state.last_run = last_run
            logger.info(
                "ccusage refresh completed",
                extra={
                    "usage_source_count": len(sources),
                    "duration_ms": duration_ms,
                    "operation_id": refresh_id,
                    "refresh_kind": plan.kind,
                    "since": plan.since_iso,
                    **_resource_fields(self._run.resources),
                },
            )
            await self.events.emit(
                "usage_refreshed",
                source="ccusage",
                usage_sources=sorted(sources),
                refresh_kind=plan.kind,
                since=plan.since_iso,
                operation_id=refresh_id,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError, UsageAdapterError) as exc:
            self.state.status = "stale" if self.cache.get("sources") else "error"
            self.state.error = str(exc)
            duration_ms = round((time.monotonic() - started) * 1000)
            self.state.last_run = self._run_summary(
                plan, trigger, self._version.version, started_at, duration_ms, error=str(exc)
            )
            logger.warning(
                "ccusage refresh failed: %s",
                exc,
                extra={
                    "duration_ms": duration_ms,
                    "error_type": type(exc).__name__,
                    "operation_id": refresh_id,
                    "refresh_kind": plan.kind if plan else None,
                    **_resource_fields(self._run.resources),
                },
            )
            await self.events.emit(
                "usage_refresh_failed",
                source="ccusage",
                error=str(exc),
                operation_id=refresh_id,
            )
        finally:
            self.state.phase = None
            self.state.run = None

    def _run_summary(
        self,
        plan: RefreshPlan | None,
        trigger: str,
        version: str | None,
        started_at: float,
        duration_ms: int,
        *,
        error: str | None,
    ) -> dict[str, Any]:
        return {
            "kind": plan.kind if plan else None,
            "since": plan.since_iso if plan else None,
            "reason": plan.reason if plan else None,
            "trigger": trigger,
            "at": started_at,
            "duration_ms": duration_ms,
            "ccusage_version": version,
            "timezone": local_timezone_key(),
            "launcher": self._run.launcher,
            "resources": self._run.resources,
            "error": error,
        }

    def _next_collection(
        self, plan: RefreshPlan, version: str | None, now: float, last_run: dict[str, Any]
    ) -> dict[str, Any]:
        """Provenance for the cache: what computed the history, and the last refresh.

        A cache from before provenance existed keeps unknown history provenance until
        its next rebuild - it is not claimed for the version that merely topped it up.
        """
        previous = self._collection().get("history")
        history: dict[str, Any] = (
            dict(previous)
            if isinstance(previous, dict)
            else {"ccusage_version": None, "timezone": None, "rebuilt_at": None}
        )
        if plan.kind != PLAN_INCREMENTAL:
            history = {
                "ccusage_version": version,
                "timezone": local_timezone_key(),
                "rebuilt_at": now,
            }
        return {"history": history, "last_refresh": last_run}

    async def _ccusage_version(
        self, command: Sequence[str], *, operation_id: str | None = None
    ) -> str | None:
        """The installed ccusage's version, re-probed only when its binary changes."""
        try:
            launch = resolve_usage_launch(command)
        except UsageAdapterError:
            return None
        if not launch.is_ccusage:
            return None
        try:
            stat = Path(launch.executable).stat()
            key: tuple[str, int, int] | None = (launch.executable, stat.st_mtime_ns, stat.st_size)
        except OSError:
            key = None
        if key is not None and key == self._version.key:
            return self._version.version
        version: str | None = None
        try:
            outcome = await run_bounded(
                [*launch.program, "--version"],
                label="ccusage-version",
                timeout_seconds=USAGE_VERSION_TIMEOUT_SECONDS,
                output_limit=4096,
                stderr_limit=4096,
                operation_id=operation_id,
            )
            if outcome.exit_code == 0:
                version = parse_ccusage_version(outcome.stdout.decode("utf-8", "replace"))
        except OSError as exc:
            logger.warning("ccusage version probe failed: %s", exc)
        if version != self._version.version:
            logger.info(
                "ccusage version detected",
                extra={
                    "ccusage_version": version,
                    "launcher": launch.launcher,
                    "prunes_by_since": ccusage_prunes_by_since(version),
                    "operation_id": operation_id,
                },
            )
        self._version = _VersionProbe(key=key, version=version)
        return version

    def _note_sample(self, sample: ResourceSample) -> None:
        run = self.state.run
        if run is None:
            return
        run["read_bytes"] = sample.read_bytes
        run["cpu_seconds"] = None if sample.cpu_seconds is None else round(sample.cpu_seconds, 1)

    async def _invoke(self, command: list[str], *, operation_id: str | None = None) -> str:
        """Run ccusage once, contained, and return its stdout or raise a UsageAdapterError.

        The 10 MiB limit used to be checked *after* `communicate()` had already
        buffered the whole answer, so it bounded the error message and not the
        daemon's memory. The bounded runner enforces it while reading, which is what
        makes the number mean what it says.

        `operation_id` is the refresh this run belongs to, so the runner's own
        `bounded_command_timed_out` line joins the adapter's `ccusage refresh
        failed` line and the `usage_refresh_failed` event instead of being a
        third, anonymous account of one failure.
        """
        launch = resolve_usage_launch(command)
        self._run.launcher = launch.launcher
        try:
            outcome = await run_bounded(
                launch.argv,
                label="ccusage",
                timeout_seconds=USAGE_CEILING_SECONDS,
                output_limit=MAX_USAGE_OUTPUT_BYTES,
                stderr_limit=MAX_USAGE_STDERR_BYTES,
                operation_id=operation_id,
                containment=BACKGROUND_BATCH,
                stall_seconds=USAGE_STALL_SECONDS,
                on_sample=self._note_sample,
            )
        except OSError as exc:
            raise UsageAdapterError(
                f"ccusage could not start; install {CCUSAGE_PACKAGE} or configure its "
                f"command: {exc}"
            ) from exc
        if outcome.resources is not None:
            self._run.resources = dict(outcome.resources)
        if outcome.stalled:
            raise UsageAdapterError(
                f"ccusage made no progress for {USAGE_STALL_SECONDS:g}s - no CPU time and "
                f"nothing read - and was stopped ({_spent(outcome)}). It looks hung "
                "rather than slow; run the command in a shell to see where it stops."
            )
        if outcome.timed_out:
            # Name the bound and what was spent against it. "timed out" alone left
            # a reader unable to tell a hung command from one that is simply
            # slower than the daemon allows - which is what it turned out to be.
            raise UsageAdapterError(
                f"ccusage refresh exceeded its {USAGE_CEILING_SECONDS:g}s ceiling "
                f"({_spent(outcome)}). Its cost is the size of this host's transcript "
                "corpus; run the command in a shell to see how long it now takes."
            )
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
        if self.refreshing():
            raise UsageAdapterError("a usage refresh is running; clear the cache after it ends")
        source_count = len(self.cache.get("sources") or {})
        self.cache = {}
        try:
            self.cache_path.unlink()
        except FileNotFoundError:
            pass
        self.state.status = "ready" if self.config.ccusage_enabled else "disabled"
        self.state.error = None
        self.state.refreshed_at = None
        self.state.last_run = None
        logger.info("usage cache cleared", extra={"usage_source_count": source_count})
        return self.snapshot()

    # -- the schedule ----------------------------------------------------- #

    def _scheduled_refresh_due(self, now: float) -> bool:
        minutes = self.config.ccusage_refresh_minutes
        if not self.config.ccusage_enabled or minutes <= 0 or self.refreshing():
            return False
        updated_at = self.cache.get("updated_at")
        anchors = [self._last_attempt_at or 0.0]
        if isinstance(updated_at, (int, float)) and not isinstance(updated_at, bool):
            anchors.append(float(updated_at))
        return now >= max(max(anchors) + minutes * 60, self._not_before)

    async def _wait_for_quiet_host(self) -> None:
        """Hold a scheduled refresh while the host is saturated, within a bound."""
        started = time.monotonic()
        announced = False
        try:
            # unsupervised-loop-ok: one deferral inside a supervised iteration's caller,
            # bounded by HOST_MAX_DEFERRAL_SECONDS
            while True:
                reason = host_pressure(await self._host_load())
                if reason is None:
                    if announced:
                        logger.info(
                            "ccusage refresh resumed: host no longer busy",
                            extra={"deferred_s": round(time.monotonic() - started)},
                        )
                    return
                waited = time.monotonic() - started
                if waited >= HOST_MAX_DEFERRAL_SECONDS:
                    logger.info(
                        "ccusage refresh deferral exhausted; running contained",
                        extra={"deferred_s": round(waited), "host_busy": reason},
                    )
                    return
                if not announced:
                    logger.info(
                        "ccusage refresh deferred: host busy",
                        extra={"host_busy": reason},
                    )
                    announced = True
                self.state.phase = "deferred"
                self.state.deferred_reason = reason
                await asyncio.sleep(HOST_RECHECK_SECONDS)
                if not self.config.ccusage_enabled:
                    return
        finally:
            self.state.phase = None
            self.state.deferred_reason = None

    async def _background(self) -> None:
        while True:
            await background.pace(USAGE_REFRESH_LOOP, SCHEDULE_TICK_SECONDS, deferrable=True)
            if not self._scheduled_refresh_due(self._clock()):
                continue
            # Claimed before the wait, so a refresh deferred by a busy host is not
            # re-planned on every tick of that wait.
            self._last_attempt_at = self._clock()
            await self._wait_for_quiet_host()
            # Settings may have changed while the host was busy.
            if (
                not self.config.ccusage_enabled
                or self.config.ccusage_refresh_minutes <= 0
                or self.refreshing()
            ):
                continue
            with background.iteration(USAGE_REFRESH_LOOP):
                await self.refresh(trigger="scheduled")


def _spent(outcome: ProcessOutcome) -> str:
    parts = [f"ran for {outcome.duration_ms / 1000:.1f}s"]
    resources = outcome.resources or {}
    read = resources.get("read_bytes")
    if isinstance(read, int):
        parts.append(f"read {read / 2**20:.0f} MB")
    cpu = resources.get("cpu_seconds")
    if isinstance(cpu, (int, float)):
        parts.append(f"{cpu:.0f}s CPU")
    return ", ".join(parts)


def _resource_fields(resources: dict[str, Any] | None) -> dict[str, Any]:
    if not resources:
        return {}
    return {
        "containment": resources.get("mechanism"),
        "containment_degraded": ",".join(str(item) for item in resources.get("degraded") or [])
        or None,
        "read_bytes": resources.get("read_bytes"),
        "cpu_seconds": resources.get("cpu_seconds"),
        "peak_memory_bytes": resources.get("peak_memory_bytes"),
    }
