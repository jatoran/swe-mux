"""Project card: a distilled, cached description of one project.

Control-plane build-order step 4 (`CONTROL_PLANE_ROADMAP.md` §5.4), roadmap
Phase 5.5. The card answers "what is this project" in a few hundred tokens —
what it is, its main subsystems, and a file → area map — so a later cheap-model
call judges "edited `processes.py`" against real architecture instead of
guessing from a filename. Built once per project from the project's own `.docs`
(`00_OVERVIEW.md` and the `.docs/CLAUDE.md` routing table), cached, and reused
by every consumer.

Four properties are load-bearing:

- **The file → area map is deterministic, never model-written.** It is inverted
  from each doc's literal "Key files" section, the same lossless source the
  doc-debt ledger uses. Every compaction eval puts artifact/file tracking last
  among a summarizer's abilities (CP §2); a paraphrased path list is worse than
  no path list, so the model never sees the map as something to rewrite.
- **Invalidation is by source content, not by clock.** The card records a
  fingerprint over the exact bytes it was built from plus the model and prompt
  version. A card whose fingerprint no longer matches its sources is never
  served — a stale architecture summary presented as current is precisely the
  silent-omission failure the design forbids (CP §2, Slipstream/MemCollab).
- **No provider, no card.** Every failure path — no model configured, no key,
  a provider error, an empty response, an exhausted budget — yields *no card*.
  Nothing here ever falls back to a heuristic guess about a project, because a
  usually-wrong card poisons every consumer that prepends it.
- **Per-project opt-in and budget-bounded.** The `project_card` automation must
  be enabled for the project (CP §8), and the one model call it costs is
  metered on the shared ledger under `builtin:project-card` with its own daily
  dollar budget, the same way the read-aloud summarizer meters itself.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .deterministic_consumers import build_doc_ownership, normalize_target

log = logging.getLogger(__name__)

# Metered on the shared automation budget ledger under its own rule id, so the
# card's spend is visible and bounded next to the observers' (see voice.py).
PROJECT_CARD_RULE_ID = "builtin:project-card"

# Bumped whenever the stored card's shape changes. Part of the fingerprint, so
# a version bump invalidates every cached card rather than deserializing an old
# shape into a new reader.
CARD_SCHEMA_VERSION = 1
# Bumped whenever the prompt changes. Also part of the fingerprint: the same
# docs through a different prompt are a different card.
PROMPT_VERSION = 1

DOCS_DIR = ".docs"
ROUTING_TABLE = "CLAUDE.md"
# Ordered candidates for the "what is this project" source. The design names
# `00_OVERVIEW.md` (the documentation standard's entry point); the rest are the
# conventional fallbacks so a project that never adopted that layout still gets
# a card instead of silently getting none.
OVERVIEW_CANDIDATES = (
    ".docs/00_OVERVIEW.md",
    ".docs/OVERVIEW.md",
    ".docs/design/architecture.md",
    "README.md",
)

# Per-source read bound. Overviews are prose and the routing table is a list;
# neither needs more than this, and an unbounded read on a repository we do not
# control is an obvious denial-of-service surface.
MAX_SOURCE_BYTES = 32 * 1024
# Rendered map bound. The full map stays queryable on the card object; only the
# prepended text block is capped, and the cap is stated in the render rather
# than silently truncating (design law 7: no silent caps).
MAX_RENDERED_MAP_FILES = 80
MAX_RENDERED_AREAS = 24
# Areas offered to the model as evidence for naming subsystems.
MAX_PROMPT_AREAS = 40
MAX_PROMPT_FILES_PER_AREA = 6
# A provider failure is not retried for this long. Without it, every consumer
# call on a project with no OpenRouter key would issue a fresh failing request.
RETRY_AFTER_FAILURE_SECONDS = 300.0

PROJECT_CARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "subsystems"],
    "properties": {
        "summary": {"type": "string", "maxLength": 600},
        "subsystems": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "purpose"],
                "properties": {
                    "name": {"type": "string", "maxLength": 60},
                    "purpose": {"type": "string", "maxLength": 200},
                },
            },
        },
    },
}

PROJECT_CARD_PROMPT = (
    "You distil a software project's own documentation into a compact card that a "
    "cheap background model will read before judging what an agent session is doing. "
    "Use ONLY the documentation given to you. Do not infer subsystems from names, do "
    "not guess at technologies that are not mentioned, and do not restate file paths — "
    "a separate deterministic file map covers those.\n\n"
    "Return:\n"
    "- summary: 2-4 sentences on what this project is and what it does.\n"
    "- subsystems: the main subsystems, each with a one-line purpose. Name only "
    "subsystems the documentation actually describes; fewer real ones beat more "
    "invented ones. Return an empty list if the documentation does not describe any."
)


class ProjectCardError(RuntimeError):
    """A card could not be built. Always degrades to no card, never to a guess."""


@dataclass(frozen=True, slots=True)
class Subsystem:
    name: str
    purpose: str

    def snapshot(self) -> dict[str, str]:
        return {"name": self.name, "purpose": self.purpose}


@dataclass(frozen=True, slots=True)
class ProjectCard:
    """One project's distilled description, valid only for `fingerprint`.

    `areas` is the deterministic file → area map, stored area-first
    (`area, files`) because that is how it renders and how a consumer asks
    "what lives in this part of the repo"; `areas_for` answers the inverse.
    """

    project_id: str
    summary: str
    subsystems: tuple[Subsystem, ...]
    areas: tuple[tuple[str, tuple[str, ...]], ...]
    fingerprint: str
    overview_source: str | None
    model: str
    built_at: float

    def areas_for(self, path: str, project_root: str | None = None) -> tuple[str, ...]:
        """Which documented areas claim a source path. Empty when none do."""
        normalized = normalize_target(path, project_root)
        if not normalized:
            return ()
        return tuple(area for area, files in self.areas if normalized in files)

    def render(
        self,
        *,
        max_files: int = MAX_RENDERED_MAP_FILES,
        max_areas: int = MAX_RENDERED_AREAS,
    ) -> str:
        """The text block a consumer prepends to a model call."""
        lines = ["# Project card", "", self.summary]
        if self.subsystems:
            lines.extend(["", "## Subsystems"])
            lines.extend(
                f"- {item.name}: {item.purpose}" for item in self.subsystems
            )
        if self.areas:
            lines.extend(["", "## File → area map"])
            shown_files = 0
            shown_areas = 0
            dropped_files = 0
            dropped_areas = 0
            for area, files in self.areas:
                if shown_areas >= max_areas or shown_files >= max_files:
                    dropped_areas += 1
                    dropped_files += len(files)
                    continue
                budget = max_files - shown_files
                listed = files[:budget]
                dropped_files += len(files) - len(listed)
                shown_files += len(listed)
                shown_areas += 1
                lines.append(f"- {area}: {', '.join(listed)}")
            if dropped_files or dropped_areas:
                # Stated, never silent: a truncated map that looks complete
                # would read as "nothing else is documented here".
                lines.append(
                    f"- (+{dropped_files} more file(s) across {dropped_areas} more area(s) "
                    "not shown)"
                )
        return "\n".join(lines)

    def snapshot(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "summary": self.summary,
            "subsystems": [item.snapshot() for item in self.subsystems],
            "areas": [[area, list(files)] for area, files in self.areas],
            "fingerprint": self.fingerprint,
            "overview_source": self.overview_source,
            "model": self.model,
            "built_at": self.built_at,
        }


def card_from_snapshot(project_id: str, payload: dict[str, Any]) -> ProjectCard | None:
    """Rebuild a card from its stored JSON, or None when the row is unreadable."""
    try:
        areas = tuple(
            (str(area), tuple(str(item) for item in files))
            for area, files in (payload.get("areas") or [])
        )
        subsystems = tuple(
            Subsystem(str(item["name"]), str(item["purpose"]))
            for item in (payload.get("subsystems") or [])
            if isinstance(item, dict) and item.get("name")
        )
        return ProjectCard(
            project_id=project_id,
            summary=str(payload["summary"]),
            subsystems=subsystems,
            areas=areas,
            fingerprint=str(payload["fingerprint"]),
            overview_source=payload.get("overview_source"),
            model=str(payload.get("model") or ""),
            built_at=float(payload.get("built_at") or 0.0),
        )
    except (KeyError, TypeError, ValueError):
        return None


# ------------------------------------------------------------------- sources


@dataclass(frozen=True, slots=True)
class CardSources:
    """Everything the card is derived from, plus its identity.

    `stamp` is the cheap staleness pre-check (file count, newest mtime, total
    size); `fingerprint` is the authoritative identity over actual content. The
    stamp exists so the common case — nothing changed — costs a stat walk
    instead of re-reading and re-hashing the whole docs tree.
    """

    overview_source: str | None
    overview_text: str
    routing_text: str
    areas: tuple[tuple[str, tuple[str, ...]], ...]
    fingerprint: str
    stamp: tuple[int, int, int]

    @property
    def usable(self) -> bool:
        """False when the project documents nothing a card could be built from."""
        return bool(self.overview_text.strip() or self.routing_text.strip())

    def prompt_text(self, *, max_chars: int) -> str:
        sections: list[str] = []
        if self.overview_text.strip():
            label = self.overview_source or "overview"
            sections.append(f"=== {label} ===\n{self.overview_text.strip()}")
        if self.routing_text.strip():
            sections.append(
                f"=== {DOCS_DIR}/{ROUTING_TABLE} (documentation routing table) ===\n"
                f"{self.routing_text.strip()}"
            )
        if self.areas:
            listed = []
            for area, files in self.areas[:MAX_PROMPT_AREAS]:
                sample = ", ".join(files[:MAX_PROMPT_FILES_PER_AREA])
                listed.append(f"{area}: {sample}")
            sections.append("=== documented areas (doc → its key files) ===\n" + "\n".join(listed))
        text = "\n\n".join(sections)
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n...[documentation truncated to fit the input budget]"


def _read_bounded(path: Path, limit: int = MAX_SOURCE_BYTES) -> str:
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit)
    except OSError:
        return ""
    return raw.decode("utf-8", errors="replace")


def _docs_stamp(project_root: Path) -> tuple[int, int, int]:
    """Cheap "did anything change" signal over the documentation tree."""
    count = 0
    newest = 0
    total = 0
    candidates = list((project_root / DOCS_DIR).rglob("*.md"))
    candidates.extend(
        project_root / candidate for candidate in OVERVIEW_CANDIDATES if "/" not in candidate
    )
    for path in candidates:
        try:
            info = path.stat()
        except OSError:
            continue
        count += 1
        newest = max(newest, int(info.st_mtime_ns))
        total += int(info.st_size)
    return (count, newest, total)


def gather_sources(project_root: str, *, model: str) -> CardSources:
    """Read every card source and compute its identity. Blocking; call off-loop."""
    root = Path(project_root)
    overview_source: str | None = None
    overview_text = ""
    for candidate in OVERVIEW_CANDIDATES:
        text = _read_bounded(root / candidate)
        if text.strip():
            overview_source = candidate
            overview_text = text
            break
    routing_text = _read_bounded(root / DOCS_DIR / ROUTING_TABLE)
    # Same inversion the doc-debt ledger uses, including its hub limit: a file
    # claimed by many docs is infrastructure and carries no area signal.
    ownership = build_doc_ownership(root / DOCS_DIR)
    by_area: dict[str, list[str]] = {}
    for file_path, owners in ownership.items():
        for owner in owners:
            by_area.setdefault(owner, []).append(file_path)
    areas = tuple(
        (area, tuple(sorted(files))) for area, files in sorted(by_area.items())
    )
    basis = json.dumps(
        {
            "schema": CARD_SCHEMA_VERSION,
            "prompt": PROMPT_VERSION,
            "model": model,
            "overview_source": overview_source,
            "overview": overview_text,
            "routing": routing_text,
            "areas": [[area, list(files)] for area, files in areas],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(basis.encode("utf-8")).hexdigest()
    return CardSources(
        overview_source=overview_source,
        overview_text=overview_text,
        routing_text=routing_text,
        areas=areas,
        fingerprint=fingerprint,
        stamp=_docs_stamp(root),
    )


# ------------------------------------------------------------------- service


@dataclass(frozen=True, slots=True)
class ProjectCardContext:
    """A session's owning project, resolved only when the gate allows the card."""

    project_id: str
    project_root: str


class ProjectCardService:
    """Builds, caches, and serves per-project cards (CP §5.4).

    Lazy by construction: nothing is built until a consumer asks for a card, so
    an enabled project that no consumer reads costs nothing. The one model call
    per fingerprint is what "built once" means.
    """

    def __init__(
        self,
        store: Any,
        config: Any,
        provider: Any,
        *,
        resolve_session: Callable[[str], Awaitable[ProjectCardContext | None]],
        resolve_project: Callable[[str], Awaitable[bool]],
    ) -> None:
        self.store = store
        self.config = config
        self.provider = provider
        self._resolve_session = resolve_session
        self._resolve_project = resolve_project
        self._locks: dict[str, asyncio.Lock] = {}
        # project_id -> (card, stamp). The stamp lets a repeat lookup skip the
        # docs re-read entirely while still being invalidated by a real edit.
        self._memo: dict[str, tuple[ProjectCard, tuple[int, int, int]]] = {}
        self._failures: dict[str, tuple[float, str]] = {}
        self.builds = 0
        self.skipped = 0
        self.last_error: str | None = None

    # -- public API for consumers ------------------------------------------

    async def card_for_session(self, session_id: str) -> ProjectCard | None:
        """The card for the project owning `session_id`, or None."""
        context = await self._resolve_session(session_id)
        if context is None:
            return None
        return await self._card(context.project_id, context.project_root)

    async def card_for_project(self, project_id: str, project_root: str) -> ProjectCard | None:
        """The card for one project, gated on that project's opt-in."""
        if not await self._resolve_project(project_root):
            return None
        return await self._card(project_id, project_root)

    async def prompt_prefix(self, session_id: str) -> str:
        """Rendered card for prepending, or an empty string when there is none.

        Consumers prepend unconditionally; "no card" must therefore cost them
        nothing rather than force a branch — and an empty prefix is the correct
        degradation, since the alternative is inventing architecture.
        """
        card = await self.card_for_session(session_id)
        return card.render() if card else ""

    def status(self) -> dict[str, Any]:
        return {
            "cached": len(self._memo),
            "builds": self.builds,
            "skipped": self.skipped,
            "last_error": self.last_error,
        }

    # -- internals ----------------------------------------------------------

    def _model(self) -> str:
        return str(
            getattr(self.config, "project_card_model", "")
            or getattr(self.config, "openrouter_cheap_model", "")
        )

    async def _card(self, project_id: str, project_root: str) -> ProjectCard | None:
        lock = self._locks.setdefault(project_id, asyncio.Lock())
        async with lock:
            try:
                return await self._resolve_card(project_id, project_root)
            except ProjectCardError as exc:
                self._note_failure(project_id, str(exc))
                return None
            except Exception as exc:  # noqa: BLE001 - a card is never load-bearing
                self._note_failure(project_id, f"{type(exc).__name__}: {exc}")
                log.exception("project card build failed for %s", project_id)
                return None

    def _note_failure(self, project_id: str, reason: str) -> None:
        self.skipped += 1
        self.last_error = reason[:400]
        self._failures[project_id] = (time.monotonic(), reason[:400])

    async def _resolve_card(self, project_id: str, project_root: str) -> ProjectCard | None:
        model = self._model()
        if not model:
            # No provider means no card. Deliberately not a heuristic fallback.
            raise ProjectCardError("no OpenRouter model is configured for the project card")
        if not getattr(self.config, "automation_enabled", True):
            raise ProjectCardError("the automation kill switch is off")

        memo = self._memo.get(project_id)
        stamp = await asyncio.to_thread(_docs_stamp, Path(project_root))
        if memo is not None and memo[1] == stamp:
            return memo[0]

        sources = await asyncio.to_thread(gather_sources, project_root, model=model)
        if not sources.usable:
            raise ProjectCardError("the project documents nothing a card can be built from")
        if memo is not None and memo[0].fingerprint == sources.fingerprint:
            # Touched but unchanged: refresh the cheap stamp, spend nothing.
            self._memo[project_id] = (memo[0], sources.stamp)
            return memo[0]

        stored = await self.store.project_card(project_id)
        if stored and str(stored.get("fingerprint")) == sources.fingerprint:
            payload = _load_json(stored.get("card_json"))
            card = card_from_snapshot(project_id, payload) if payload else None
            if card is not None:
                self._memo[project_id] = (card, sources.stamp)
                return card

        failure = self._failures.get(project_id)
        if failure and time.monotonic() - failure[0] < RETRY_AFTER_FAILURE_SECONDS:
            # A failing provider must not be re-asked on every consumer call.
            return None

        card = await self._build(project_id, project_root, sources, model)
        self._memo[project_id] = (card, sources.stamp)
        self._failures.pop(project_id, None)
        self.builds += 1
        return card

    async def _build(
        self, project_id: str, project_root: str, sources: CardSources, model: str
    ) -> ProjectCard:
        budget = float(getattr(self.config, "project_card_daily_budget_usd", 0.0))
        spend = await self.store.spend(rule_id=PROJECT_CARD_RULE_ID)
        if float(spend["cost_usd"]) >= budget:
            raise ProjectCardError("the daily project-card budget is exhausted")
        max_input_tokens = int(getattr(self.config, "project_card_max_input_tokens", 6000))
        max_output_tokens = int(getattr(self.config, "project_card_max_output_tokens", 600))
        prompt_text = sources.prompt_text(max_chars=max_input_tokens * 4)
        call_id = await self.store.observer_started(
            firing_id=f"project-card:{project_id}",
            rule_id=PROJECT_CARD_RULE_ID,
            model=model,
            input_hash=sources.fingerprint,
            input_bytes=len(prompt_text.encode("utf-8")),
        )
        try:
            completion = await self.provider.complete_json(
                model=model,
                messages=[
                    {"role": "system", "content": PROJECT_CARD_PROMPT},
                    {"role": "user", "content": prompt_text},
                ],
                schema_name="project_card_v1",
                schema=PROJECT_CARD_SCHEMA,
                max_tokens=max_output_tokens,
            )
        except asyncio.CancelledError:
            await self.store.observer_finished(call_id, status="cancelled", error="cancelled")
            raise
        except Exception as exc:
            await self.store.observer_finished(call_id, status="failed", error=str(exc)[:1000])
            raise ProjectCardError(f"the project-card model call failed: {exc}") from exc
        await self.store.observer_finished(
            call_id,
            status="completed",
            resolved_model=completion.resolved_model,
            generation_id=completion.generation_id,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            cost_usd=completion.cost_usd,
            latency_ms=completion.latency_ms,
        )
        await self.store.add_spend(
            rule_id=PROJECT_CARD_RULE_ID,
            model=completion.resolved_model,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            cost_usd=completion.cost_usd or 0,
            call_id=call_id,
        )
        summary = str(completion.value.get("summary") or "").strip()
        if not summary:
            raise ProjectCardError("the project-card model returned an empty summary")
        subsystems = tuple(
            Subsystem(str(item["name"]).strip()[:60], str(item.get("purpose") or "").strip()[:200])
            for item in (completion.value.get("subsystems") or [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        )
        card = ProjectCard(
            project_id=project_id,
            summary=summary[:600],
            subsystems=subsystems,
            areas=sources.areas,
            fingerprint=sources.fingerprint,
            overview_source=sources.overview_source,
            model=completion.resolved_model,
            built_at=time.time(),
        )
        await self.store.save_project_card(
            project_id=project_id,
            project_root=project_root,
            fingerprint=sources.fingerprint,
            card=card.snapshot(),
            schema_version=CARD_SCHEMA_VERSION,
            requested_model=model,
            resolved_model=completion.resolved_model,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            cost_usd=completion.cost_usd,
        )
        return card


def _load_json(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None
