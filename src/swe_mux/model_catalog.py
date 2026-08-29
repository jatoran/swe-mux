"""What models a harness's CLI actually has, asked of the CLI itself.

This is the *discovery* half of choosing a model, and it is deliberately not the
gate. The gate is `harness.ModelSelection`, which checks a name's vocabulary and
nothing else, because a check that consulted a list would refuse every model
released after the list was written - the failure `claude_models.py` had to grow
a family fallback to escape.

What a list is good for is the question a vocabulary cannot answer: *which* model
should this be. An agent asked to open a cheap session has no way to know what
this machine is authenticated for, and the alternative to asking is guessing a
name and finding out from a dead pane. So the catalogue informs and never
refuses: a model absent from it still spawns.

Three properties keep it honest.

**The command is the harness's own** (`HarnessDescriptor.model_selection.catalog`),
resolved through `which_real` like every other CLI probe - the daemon prepends
`~/.mux/bin` to PATH and writes a shim for every harness, so a plain `which`
finds the shim, and probing the shim invokes the agent launcher.

**The parser is declared, not sniffed.** A parser that guesses at a layout it
does not recognize returns *fewer* models, and a short list is indistinguishable
from a small account. Each format is a measured statement about one CLI's output,
and a layout change shows up as an empty catalogue with the command named, which
is a diagnosis rather than a silent shrug.

**Nothing here interprets an exit status into a fatal error.** `omp models`
prints its table and warns about unauthenticated providers; a nonzero exit with
parseable models on stdout is still a useful answer. The caller is told the exit
code and the parsed count and decides.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass

from .bounded_subprocess import run_bounded
from .harness import (
    ModelCatalog,
    agent_harnesses,
    descriptor,
    model_catalog,
    normalize_model_name,
    resolve_launch_model,
)

log = logging.getLogger(__name__)

#: Long enough for a CLI that refreshes a remote catalogue on a cold cache, short
#: enough that a hung probe cannot hold a request. Measured 2026-08-29 on a warm
#: cache: opencode 0.6s, pi 1.1s, omp 1.3s.
PROBE_TIMEOUT_SECONDS = 20.0
#: A catalogue changes when a provider is added or a vendor ships, neither of
#: which happens inside a work session. Long enough that repeated asks are free,
#: short enough that authenticating a new provider shows up without a restart.
CACHE_TTL_SECONDS = 900.0
#: omp lists ~500 models through OpenRouter, each row a few hundred bytes of JSON.
#: 4 MiB holds that with room to spare and still caps a CLI that decides to stream.
OUTPUT_LIMIT_BYTES = 4 * 1024 * 1024
#: Every id a caller could act on, and a ceiling on what one bad parse can cost.
MAX_MODELS = 2000

# Terminal decoration a CLI prints when it does not believe it is being piped.
# Only the two sequence families that appear in these listings; anything else is
# left in place, where a caller can see it, rather than silently scrubbed.
_ESCAPES = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# A model id as any of the three formats spell one: no whitespace and no
# decoration. Slash depth is deliberately not constrained - omp routes a provider
# through a second one (`openrouter/~anthropic/claude-opus-latest`), and a
# depth rule written from opencode's two-part ids would have silently dropped 462
# of omp's 470 entries. Whitespace is the whole discriminator here, and it is
# enough: a table border, a header word, or a footnote never survives it.
_MODEL_ID = re.compile(r"[A-Za-z0-9~][A-Za-z0-9._\-\[\]:/~]*")


@dataclass(frozen=True, slots=True)
class CatalogResult:
    """One harness's answer, with everything a caller needs to judge it."""

    harness: str
    #: The command that produced this, as a string, so a thin answer can be
    #: reproduced by hand rather than argued about.
    command: str
    models: tuple[str, ...] = ()
    #: What the CLI exited with. `None` means it never finished (timeout), which
    #: must never read as a zero.
    exit_code: int | None = None
    #: Why there is nothing here, in the CLI's own words where there are any.
    #: `None` is not "no models" - an empty catalogue with no error is a CLI that
    #: ran, said nothing parseable, and is worth looking at.
    error: str | None = None
    fetched_at: float = 0.0

    @property
    def available(self) -> bool:
        return bool(self.models)


@dataclass(slots=True)
class _CacheEntry:
    at: float
    result: CatalogResult


_lock = asyncio.Lock()
_cache: dict[str, _CacheEntry] = {}


def clear_cache() -> None:
    """Drop every cached catalogue. For tests, and for a deliberate re-probe."""
    _cache.clear()


def _clean(text: str) -> str:
    return _ESCAPES.sub("", text).strip()


def _parse_qualified_lines(text: str) -> list[str]:
    """opencode: one `provider/model` per line, and nothing else on the line."""
    found: list[str] = []
    for raw in text.splitlines():
        line = _clean(raw)
        if "/" in line and _MODEL_ID.fullmatch(line):
            found.append(line)
    return found


def _parse_selector_json(text: str) -> list[str]:
    """omp: `{"models":[{"selector":"provider/id", …}]}`.

    `selector` and not `provider` + `id` composed here: the CLI publishes the
    exact string its own `--model` takes, and rebuilding it would be this module
    guessing at a join it was handed.
    """
    start = text.find("{")
    if start < 0:
        return []
    try:
        document = json.loads(text[start:])
    except (ValueError, RecursionError):
        return []
    entries = document.get("models") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        return []
    found: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        selector = str(entry.get("selector") or "").strip()
        if selector and _MODEL_ID.fullmatch(selector):
            found.append(selector)
    return found


def _parse_provider_columns(text: str) -> list[str]:
    """pi: a whitespace-aligned table whose first two columns are the id.

    The header row is what makes this parseable rather than guessed: a listing
    that no longer starts `provider`/`model` returns nothing, and nothing with the
    command named is a better answer than two columns of a table that moved.
    """
    lines = [_clean(line) for line in text.splitlines()]
    rows = [line for line in lines if line]
    if not rows:
        return []
    header = rows[0].split()
    if header[:2] != ["provider", "model"]:
        return []
    found: list[str] = []
    for row in rows[1:]:
        columns = row.split()
        if len(columns) < 2:
            continue
        candidate = f"{columns[0]}/{columns[1]}"
        if _MODEL_ID.fullmatch(candidate):
            found.append(candidate)
    return found


_PARSERS = {
    "qualified_lines": _parse_qualified_lines,
    "selector_json": _parse_selector_json,
    "provider_columns": _parse_provider_columns,
}


def parse_models(catalog: ModelCatalog, text: str) -> tuple[str, ...]:
    """The model ids in `text`, deduplicated, in the order the CLI listed them."""
    seen: dict[str, None] = {}
    for model in _PARSERS[catalog.format](text):
        if model not in seen:
            seen[model] = None
        if len(seen) >= MAX_MODELS:
            break
    return tuple(seen)


async def catalog_for(harness: str, *, refresh: bool = False) -> CatalogResult:
    """`harness`'s own model listing, at most once per TTL. Never raises.

    Serialized on one lock rather than per harness: these are cold-start probes a
    caller makes at most a handful of times, and two of the three CLIs spend their
    first second reading a shared on-disk catalogue.
    """
    from .shim_paths import which_real

    name = str(harness or "").strip()
    if name not in agent_harnesses():
        return CatalogResult(
            harness=name, command="", error=f"{name!r} is not a registered agent harness"
        )
    catalog = model_catalog(name)
    if catalog is None:
        return CatalogResult(
            harness=name,
            command="",
            error=(
                f"{descriptor(name).display_name} has no command that lists models, "
                f"so mux cannot enumerate them; the names it accepts are in the "
                f"refusal a wrong one produces"
            ),
        )
    command = " ".join((descriptor(name).executable, *catalog.argv))
    async with _lock:
        now = time.monotonic()
        cached = _cache.get(name)
        if not refresh and cached is not None and now - cached.at < CACHE_TTL_SECONDS:
            return cached.result
        result = await _probe(name, catalog, command, which_real(descriptor(name).executable))
        _cache[name] = _CacheEntry(now, result)
        return result


async def _probe(
    harness: str, catalog: ModelCatalog, command: str, executable: str | None
) -> CatalogResult:
    if not executable:
        return CatalogResult(
            harness=harness,
            command=command,
            error=f"{descriptor(harness).executable} is not installed on this host",
        )
    try:
        outcome = await run_bounded(
            [executable, *catalog.argv],
            label=f"model_catalog:{harness}",
            timeout_seconds=PROBE_TIMEOUT_SECONDS,
            output_limit=OUTPUT_LIMIT_BYTES,
            merge_stderr=True,
        )
    except OSError as exc:
        log.warning(
            "model_catalog_probe_failed harness=%s error_type=%s", harness, type(exc).__name__
        )
        return CatalogResult(
            harness=harness, command=command, error=f"could not run `{command}`: {exc}"
        )
    text = outcome.stdout.decode("utf-8", "replace")
    models = parse_models(catalog, text)
    error: str | None = None
    if not models:
        # A CLI that ran and produced nothing this parser recognizes is a
        # diagnosis, not an absence, and the command is named so it can be run.
        tail = " ".join(_clean(text).split())[-300:]
        error = (
            f"`{command}` exited {outcome.exit_code} and listed no models mux "
            f"could read{f': {tail}' if tail else ''}"
        )
        log.info("model_catalog_empty harness=%s exit_code=%s", harness, outcome.exit_code)
    return CatalogResult(
        harness=harness,
        command=command,
        models=models,
        exit_code=outcome.exit_code,
        error=error,
        fetched_at=time.time(),
    )


def matches(models: tuple[str, ...], query: str) -> tuple[str, ...]:
    """`models` narrowed to those containing `query`, case-insensitively.

    Filtering here rather than through each CLI's own search flag: three CLIs
    spell that three ways, one of them not at all, and a single substring rule
    over an already-cached list is both cheaper and the same answer.
    """
    needle = str(query or "").strip().lower()
    return models if not needle else tuple(m for m in models if needle in m.lower())


def suggest(harness: str, wanted: str, models: tuple[str, ...], limit: int = 8) -> tuple[str, ...]:
    """Catalogue entries that look like what a refused `wanted` was reaching for.

    Substring both ways, so `sonnet` finds `anthropic/claude-sonnet-4-5` and
    `anthropic/claude-sonnet-4-5-2` finds the id it was a typo of. Only entries
    the harness would actually accept are offered - suggesting a name that would
    itself be refused would send a caller round the same loop twice.
    """
    needle = normalize_model_name(wanted)
    if not needle or not models:
        return ()
    stem = needle.rsplit("/", 1)[-1]
    hits = [
        model
        for model in models
        if (stem in model.lower() or model.lower() in needle)
        and resolve_launch_model(harness, model) is not None
    ]
    return tuple(hits[:limit])
