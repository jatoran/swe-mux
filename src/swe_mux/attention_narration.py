"""Model narration: a cheap "why" layered over a deterministic finding.

Roadmap Phase 6.5, control-plane build-order step 6 (`CONTROL_PLANE_ROADMAP.md`
§14). Narration is presentation over evidence and never a substitute for it:
every ranked item is complete and actionable before this layer runs, and stays
complete when it fails.

Four boundaries hold whatever the model returns:

- **Stateless and read-only.** One call sees one normalized slice of one
  incident. There is no conversation, no memory between calls, and no path from
  here to a session, a file, or a queue.
- **A slice never spans two agent runs.** The slice is built from an incident's
  own evidence, which the ranking layer already anchors to a single run. A "why"
  assembled across a `/clear` is a fabricated cause, not a summary.
- **Failure degrades to the detector's own words, never to silence and never to
  a guess.** Every failure path records a typed status the surface can show, and
  the deterministic summary is what the user keeps reading.
- **Budgeted on the shared ledger** under ``builtin:attention-narration``, so its
  spend sits beside the observers' rather than in a private counter.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

log = logging.getLogger(__name__)

NARRATION_RULE_ID = "builtin:attention-narration"

# A provider failure is not retried for this long: without it, every ranked item
# on a daemon with no OpenRouter key issues a fresh failing request.
RETRY_AFTER_FAILURE_SECONDS = 300.0
MAX_EVIDENCE_ENTRIES = 12
MAX_SLICE_CHARS = 4000

NARRATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["why"],
    "properties": {"why": {"type": "string", "maxLength": 400}},
}

NARRATION_PROMPT = (
    "You explain, in one or two sentences, why a software agent session was "
    "flagged for a human's attention. You are given a deterministic finding and "
    "the exact facts behind it.\n\n"
    "Rules:\n"
    "- Use ONLY the given facts. Never infer a cause the facts do not show.\n"
    "- If the facts do not explain the cause, say what they do show and stop.\n"
    "- Do not restate the finding verbatim, do not give instructions, and do not "
    "speculate about intent.\n"
    "- No preamble. Return the explanation as `why`."
)


def build_slice(item: dict[str, Any]) -> str:
    """Normalize one incident into the only input a narration call ever sees."""
    evidence = item.get("evidence")
    entries = evidence[:MAX_EVIDENCE_ENTRIES] if isinstance(evidence, list) else []
    payload = {
        "finding": item.get("title"),
        "class": item.get("incident_class"),
        "detectors": item.get("kinds"),
        "summary": item.get("summary"),
        "agent_run_id": item.get("agent_run_id"),
        "evidence": entries,
    }
    return json.dumps(payload, separators=(",", ":"), default=str)[:MAX_SLICE_CHARS]


class AttentionNarrator:
    """Optional cheap-model narration for ranked attention items."""

    def __init__(self, store: Any, config: Any, provider: Any) -> None:
        self.store = store
        self.config = config
        self.provider = provider
        self._failed_at: float | None = None
        self.narrations = 0
        self.failures = 0

    def _model(self) -> str:
        configured = str(getattr(self.config, "attention_narration_model", "") or "")
        return configured or str(getattr(self.config, "openrouter_cheap_model", "") or "")

    async def narrate(self, item: dict[str, Any]) -> tuple[str | None, str]:
        """Return the narration and a typed status. Never raises to the caller."""
        if not bool(getattr(self.config, "attention_narration_enabled", False)):
            return None, "disabled"
        model = self._model()
        if not model:
            return None, "no_model"
        if self._failed_at and time.monotonic() - self._failed_at < RETRY_AFTER_FAILURE_SECONDS:
            return None, "failed"
        budget = float(getattr(self.config, "attention_narration_daily_budget_usd", 0.10))
        spend = await self.store.spend(rule_id=NARRATION_RULE_ID)
        if float(spend["cost_usd"]) >= budget:
            log.info("attention narration skipped for %s: daily budget spent", item["id"])
            return None, "budget"
        prompt = build_slice(item)
        call_id = await self.store.observer_started(
            firing_id=f"attention-narration:{item['id']}",
            rule_id=NARRATION_RULE_ID,
            model=model,
            input_hash=str(item["incident_key"]),
            input_bytes=len(prompt.encode("utf-8")),
        )
        try:
            completion = await self.provider.complete_json(
                model=model,
                messages=[
                    {"role": "system", "content": NARRATION_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                schema_name="attention_narration_v1",
                schema=NARRATION_SCHEMA,
                max_tokens=int(
                    getattr(self.config, "attention_narration_max_output_tokens", 200)
                ),
            )
        except asyncio.CancelledError:
            await self.store.observer_finished(call_id, status="cancelled", error="cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - narration must never break ranking
            self._failed_at = time.monotonic()
            self.failures += 1
            await self.store.observer_finished(call_id, status="failed", error=str(exc)[:1000])
            log.warning("attention narration failed for %s: %s", item["id"], exc)
            return None, "failed"
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
            rule_id=NARRATION_RULE_ID,
            model=completion.resolved_model,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            cost_usd=completion.cost_usd or 0,
            call_id=call_id,
        )
        self._failed_at = None
        why = str(completion.value.get("why") or "").strip()
        if not why:
            return None, "empty"
        self.narrations += 1
        return why[:400], "ok"

    def status(self) -> dict[str, Any]:
        return {
            "enabled": bool(getattr(self.config, "attention_narration_enabled", False)),
            "model": self._model(),
            "narrations": self.narrations,
            "failures": self.failures,
        }
