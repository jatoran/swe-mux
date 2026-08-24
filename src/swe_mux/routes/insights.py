"""Reads that summarise a run rather than serving its state."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import (
    app_keys as keys,
)
from .. import (
    budget,
)
from ..automation import (
    OBSERVER_SCHEMAS,
    validate_observer_result,
)
from ..automation_store import AutomationStore
from ..config import Config
from ..errors import NotFound
from ..git_monitor import _git
from ..harness import (
    HarnessLevel,
    harnesses_at_least,
    has_observable_transcript,
)
from ..history import HistoryIndex
from ..http_support import json_response
from ..layouts import attach_terminal
from ..openrouter import OpenRouterError
from ..scan_consumers import handoff_progress
from . import sessions
from .support import _project_root_for

log = logging.getLogger(__name__)


async def second_opinion(request: web.Request) -> web.Response:
    source_id = request.match_info["sid"]
    history: HistoryIndex = request.app[keys.HISTORY]
    source = await history.history_entry(source_id)
    if not source:
        live = next(
            (
                item.record
                for item in request.app[keys.SESSIONS].sessions.values()
                if item.record.agent_run_id == source_id or item.record.id == source_id
            ),
            None,
        )
        if live and live.agent_run_id:
            source_id = live.agent_run_id
            source = await history.history_entry(source_id)
    if not source or not has_observable_transcript(source.get("backend")):
        raise NotFound(source_id, kind="observable transcript")
    body = await request.json()
    # "The other agent" stopped being well defined at two harnesses. The request may
    # name any observed harness that is not the one under review; with none named,
    # the default is the first other observed harness in registry order.
    alternatives = tuple(
        name for name in harnesses_at_least(HarnessLevel.observed) if name != source["backend"]
    )
    backend = str(body.get("backend") or (alternatives[0] if alternatives else ""))
    if not has_observable_transcript(backend) or backend == source["backend"]:
        raise ValueError("second opinion backend must be a different observed harness")
    # Phase 7.7: the scan timeline is the behavioral-summary substrate, so prior
    # run summaries come from its spine; fall back to `summary` annotations for a
    # run with no scan records.
    scan_records = await request.app[keys.AUTOMATION_STORE].scan_records(
        agent_run_id=source_id, limit=500
    )
    summaries = [
        text
        for record in scan_records
        if (text := (str(record.get("summary") or "").strip()
                     or str(record.get("intent") or "").strip()))
    ][-12:]
    if not summaries:
        annotations = await request.app[keys.AUTOMATION_STORE].annotations(
            agent_run_id=source_id, limit=50
        )
        summaries = [
            str(item["content"])
            for item in reversed(annotations)
            if item["tag"] in {"turn-summary", "summary"}
        ][-12:]
    worktree_context = await _review_worktree_context(str(source["cwd"]))
    prompt = (
        f"Review the work from a {source['backend']} agent run in {source['cwd']}.\n"
        "Act as an independent reviewer. Inspect the current working tree and identify "
        "incorrect changes, missing tests, regressions, or unsupported completion claims. "
        "Do not assume the prior agent was correct.\n"
    )
    if summaries:
        prompt += "\nPrior run summaries:\n- " + "\n- ".join(summaries)
    if worktree_context:
        prompt += f"\n\nCurrent bounded worktree context:\n```text\n{worktree_context}\n```"
    if body.get("instructions"):
        prompt += f"\n\nUser review instructions:\n{str(body['instructions'])[:4000]}"
    preview = {
        "source_run_id": source_id,
        "source_backend": source["backend"],
        "backend": backend,
        "cwd": source["cwd"],
        "worktree_context": worktree_context,
        "prompt": prompt,
        "relation": "review",
    }
    preview_token = hashlib.sha256(
        json.dumps(preview, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    preview["preview_token"] = preview_token
    if not body.get("confirm"):
        return json_response({"preview": preview, "spawned": False})
    if not secrets.compare_digest(str(body.get("preview_token") or ""), preview_token):
        raise ValueError("review confirmation requires the current preview token")
    target_project = str(body.get("project_id") or source.get("project_id") or "")
    session = await sessions._spawn_from_body(
        request.app,
        {
            "backend": backend,
            "name": body.get("name") or f"{backend} review · {source['name']}",
            "project_id": target_project,
            "argv": [prompt],
        },
    )
    project_record = request.app[keys.PROJECTS].projects[target_project]
    next_layout = attach_terminal(
        project_record.layout,
        session.record.id,
        target_id=body.get("target_session_id"),
        direction=body.get("direction"),
    )
    try:
        await request.app[keys.PROJECTS].update(
            target_project,
            layout=next_layout,
            layout_revision=project_record.layout_revision,
        )
    except Exception:
        await request.app[keys.SESSIONS].stop(session.record.id)
        request.app[keys.SESSIONS].sessions.pop(session.record.id, None)
        raise
    lineage = await request.app[keys.AUTOMATION_STORE].add_lineage(
        source_id,
        session.record.agent_run_id or session.record.id,
        "review",
        {
            "prompt_reviewed": True,
            "preview_token": preview_token,
            "source_backend": source["backend"],
        },
    )
    return json_response(
        {
            "preview": preview,
            "spawned": True,
            "session": session.record.snapshot(),
            "lineage": lineage,
        },
        201,
    )


async def _review_worktree_context(cwd: str) -> str:
    """Return bounded, reviewable Git evidence without persisting a repository diff."""
    (status_code, status), (diff_code, diff) = await asyncio.gather(
        _git(cwd, "status", "--short", "--branch", "--untracked-files=normal"),
        _git(cwd, "diff", "--stat", "--", "."),
    )
    sections: list[str] = []
    if status_code == 0 and status:
        sections.append("STATUS\n" + status[:6000])
    if diff_code == 0 and diff:
        sections.append("DIFF STAT\n" + diff[:4000])
    return "\n\n".join(sections)[:10_000]


async def export_handoff(request: web.Request) -> web.Response:
    run_id = request.match_info["sid"]
    row = await request.app[keys.HISTORY].history_entry(run_id)
    if not row or not has_observable_transcript(row.get("backend")):
        raise NotFound(run_id, kind="observable transcript")
    store = request.app[keys.AUTOMATION_STORE]
    annotations = await store.annotations(agent_run_id=run_id, limit=200)
    # Historical `turn-summary` notes stay readable (the producer is retired, not
    # the records); the scan spine below is the primary source when available.
    summaries = [
        item
        for item in reversed(annotations)
        if item["tag"] in {"turn-summary", "summary", "handoff-suggestion"}
    ]
    # Phase 7.7 timeline-based handoff: when the Project opts into it, the
    # handoff is regenerated phase-structured from the run's scan spine rather
    # than from flat annotations. Falls back to annotation summaries when the
    # consumer is off or the run has no scan records.
    project_root = _project_root_for(request.app, str(row.get("project_id") or ""), row.get("cwd"))
    gate = request.app.get(keys.AUTOMATION_GATE)
    enabled = await gate(project_root) if (gate and project_root) else frozenset()
    scan_progress: list[str] = []
    if "timeline_handoff" in enabled:
        scan_records = await request.app[keys.AUTOMATION_STORE].scan_records(
            agent_run_id=run_id, limit=2000
        )
        scan_progress = handoff_progress(scan_records)
    history_id = str(row["id"])
    native_id = str(row.get("native_id") or "").strip()
    transcript_path = str(row.get("transcript_path") or "").strip()
    escaped_transcript_path = transcript_path.replace("`", "\\`")
    transcript_available = bool(transcript_path and Path(transcript_path).is_file())
    lines = [
        f"# Handoff: {row['name']}",
        "",
        f"- Backend: {row['backend']}",
        f"- Working directory: {row['cwd']}",
        f"- swe-mux history ID: {history_id}",
        f"- Provider session ID: {native_id or 'unavailable'}",
        "",
        "## Native transcript",
        "",
        (
            f"`{escaped_transcript_path}`"
            if escaped_transcript_path
            else "Unavailable in the current swe-mux history index."
        ),
        "",
        (
            "Read this provider-native file directly to review the complete conversation. "
            "The summary in this handoff is not a transcript copy."
            if transcript_available
            else (
                "This recorded provider-native path is not currently available. Use the provider "
                "session ID to locate the conversation."
                if escaped_transcript_path
                else "Use the provider session ID to locate the native conversation when available."
            )
        ),
        "",
        "## Progress",
        "",
    ]
    if scan_progress:
        lines.extend(f"- {item}" for item in scan_progress)
        provenance = (
            "Generated phase-structured from the read-only swe-mux scan timeline for this "
            "run. Review before using it as context."
        )
    else:
        lines.extend(f"- {item['content']}" for item in summaries)
        if not summaries:
            lines.append("- No observer summaries are available yet.")
        provenance = (
            "Generated from read-only swe-mux annotations. Review before using it as context."
        )
    lines.extend(["", "## Provenance", "", provenance])
    return json_response({"run_id": history_id, "markdown": "\n".join(lines) + "\n"})


async def workload_telemetry(request: web.Request) -> web.Response:
    since = float(request.query.get("since", 0))
    result = await request.app[keys.HISTORY].workload_telemetry(since)
    result["observer_spend"] = await request.app[keys.AUTOMATION_STORE].spend()
    provider_costs: list[dict[str, Any]] = []
    usage = request.app.get(keys.USAGE)
    providers = (usage.cache.get("providers") or {}) if usage else {}
    for backend, payload in providers.items():
        for row in payload.get("models") or []:
            provider_costs.append(
                {
                    "backend": backend,
                    "model": row.get("model") or "unknown",
                    "tokens": int(row.get("total_tokens") or 0),
                    "cost_usd": float(row.get("cost_usd") or 0),
                    "cost_is_estimate": bool(row.get("cost_is_estimate", True)),
                    "attribution": "ccusage_provider_model_aggregate",
                }
            )
    result["provider_cost_dimensions"] = provider_costs
    result["cost_note"] = (
        "ccusage costs are backend/model aggregates and are not attributed to individual runs"
    )
    return json_response(result)


async def list_experiences(request: web.Request) -> web.Response:
    return json_response(
        {
            "items": await request.app[keys.AUTOMATION_STORE].experiences(
                query=request.query.get("q", ""),
                project_scope_id=request.query.get("project_scope_id"),
                limit=int(request.query.get("limit", 100)),
            ),
            "advisory_only": True,
        }
    )


async def list_observer_batches(request: web.Request) -> web.Response:
    return json_response(
        {
            "items": await request.app[keys.AUTOMATION_STORE].batches(
                int(request.query.get("limit", 50))
            )
        }
    )


async def create_observer_batch(request: web.Request) -> web.Response:
    body = await request.json()
    kind = str(body.get("kind") or "")
    allowed = {"experience", "procedure", "doc-drift", "convention", "regression"}
    if kind not in allowed:
        raise ValueError(f"kind must be one of {', '.join(sorted(allowed))}")
    run_ids = body.get("run_ids")
    if (
        not isinstance(run_ids, list)
        or not 1 <= len(run_ids) <= 25
        or not all(isinstance(item, str) for item in run_ids)
    ):
        raise ValueError("run_ids must select between 1 and 25 agent runs")
    rows: list[dict[str, Any]] = []
    for identity in run_ids:
        row = await request.app[keys.HISTORY].history_entry(identity)
        if (
            not row
            or not has_observable_transcript(row.get("backend"))
            or not row.get("exited_at")
            or not row.get("transcript_path")
        ):
            raise ValueError(f"batch run is not an ended agent transcript: {identity}")
        rows.append(row)
    estimate = {
        "calls": len(rows),
        "maximum_input_tokens": len(rows) * request.app[keys.CONFIG].automation_max_input_tokens,
        "maximum_output_tokens": len(rows) * request.app[keys.CONFIG].automation_max_output_tokens,
        "repository_mutation": False,
    }
    preview_token = hashlib.sha256(
        json.dumps(
            {"kind": kind, "run_ids": run_ids, "estimate": estimate},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if not body.get("confirm"):
        return json_response(
            {
                "preview": True,
                "preview_token": preview_token,
                "kind": kind,
                "runs": run_ids,
                "estimate": estimate,
            }
        )
    if not secrets.compare_digest(str(body.get("preview_token") or ""), preview_token):
        raise ValueError("batch confirmation requires the current preview token")
    if not request.app[keys.CONFIG].automation_enabled:
        raise ValueError("automation kill switch is off")
    batch_id = await request.app[keys.AUTOMATION_STORE].create_batch(kind, run_ids)
    task = asyncio.create_task(
        _run_observer_batch(request.app, batch_id, kind, rows),
        name=f"observer-batch-{batch_id}",
    )
    request.app[keys.AUTOMATION_TASKS].add(task)
    task.add_done_callback(request.app[keys.AUTOMATION_TASKS].discard)
    return json_response({"id": batch_id, "status": "running", "estimate": estimate}, 202)


async def _run_observer_batch(
    app: web.Application, batch_id: str, kind: str, rows: list[dict[str, Any]]
) -> None:
    store: AutomationStore = app[keys.AUTOMATION_STORE]
    config: Config = app[keys.CONFIG]
    model = config.openrouter_standard_model or config.openrouter_cheap_model
    results: list[dict[str, Any]] = []
    calls = tokens = 0
    cost = 0.0
    error: str | None = None
    if not model:
        await store.finish_batch(
            batch_id,
            status="failed",
            preview=[],
            calls=0,
            tokens=0,
            cost_usd=0,
            error="no OpenRouter standard or cheap model is configured",
        )
        return
    schema_name = "experience_v1" if kind == "experience" else "summary_v1"
    prompts = {
        "experience": (
            "Extract one concrete error and its demonstrated resolution. If no resolution "
            "is demonstrated, state that clearly. Return only the schema."
        ),
        "procedure": "Summarize one repeatable procedure demonstrated by this run.",
        "doc-drift": "Identify a plausible documentation drift candidate; do not edit files.",
        "convention": "Summarize one project convention evidenced by this run.",
        "regression": "Summarize one concrete regression-test candidate from this run.",
    }
    try:
        for row in rows:
            spend = await store.spend()
            rule_id = f"batch.{kind}"
            rule_spend = await store.spend(rule_id=rule_id)
            for verdict in (
                budget.spent_out(
                    config.automation_daily_budget, spend, label="the global daily observer"
                ),
                budget.spent_out(
                    config.automation_rule_daily_budget, rule_spend, label="the batch observer rule"
                ),
            ):
                if verdict.exhausted:
                    raise ValueError(verdict.reason)
            hour_ago = time.time() - 3600
            if await store.observer_call_count(hour_ago) >= config.automation_hourly_call_cap:
                raise ValueError("global hourly observer call cap is exhausted")
            if (
                await store.observer_call_count(hour_ago, rule_id=rule_id)
                >= config.automation_rule_hourly_call_cap
            ):
                raise ValueError("batch observer hourly call cap is exhausted")
            raw_path = row["transcript_path"]
            transcript = await app[keys.AUTOMATION].slices.build(
                Path(str(raw_path)) if raw_path else None,
                str(row["backend"]),
                "last_n_messages",
                max_messages=24,
                max_bytes=min(config.automation_max_input_tokens * 4, 512 * 1024),
                native_id=str(row.get("native_id") or "") or None,
            )
            input_text = transcript.render()
            call_id = await store.observer_started(
                firing_id=batch_id,
                rule_id=rule_id,
                model=model,
                input_hash=transcript.input_hash,
                input_bytes=transcript.bytes,
            )
            try:
                completion = await app[keys.OPENROUTER].complete_json(
                    model=model,
                    messages=[
                        {"role": "system", "content": prompts[kind]},
                        {"role": "user", "content": input_text},
                    ],
                    schema_name=schema_name,
                    schema=OBSERVER_SCHEMAS[schema_name],
                    max_tokens=config.automation_max_output_tokens,
                )
                validate_observer_result(completion.value, schema_name)
                await store.observer_finished(
                    call_id,
                    status="completed",
                    resolved_model=completion.resolved_model,
                    generation_id=completion.generation_id,
                    input_tokens=completion.input_tokens,
                    output_tokens=completion.output_tokens,
                    cost_usd=completion.cost_usd,
                    latency_ms=completion.latency_ms,
                    provider_name=completion.provider_name,
                    finish_reason=completion.finish_reason,
                    response_content_type=completion.response_content_type,
                    response_content_length=completion.response_content_length,
                    http_status=200,
                )
                await store.add_spend(
                    rule_id=rule_id,
                    model=completion.resolved_model,
                    input_tokens=completion.input_tokens,
                    output_tokens=completion.output_tokens,
                    cost_usd=completion.cost_usd,
                    call_id=call_id,
                )
            except Exception as exc:
                if isinstance(exc, OpenRouterError):
                    await store.observer_finished(
                        call_id,
                        status="failed",
                        resolved_model=exc.resolved_model,
                        generation_id=exc.generation_id,
                        input_tokens=exc.input_tokens,
                        output_tokens=exc.output_tokens,
                        cost_usd=exc.cost_usd,
                        latency_ms=exc.latency_ms,
                        provider_name=exc.provider_name,
                        finish_reason=exc.finish_reason,
                        response_content_type=exc.response_content_type,
                        response_content_length=exc.response_content_length,
                        http_status=exc.status,
                        retryable=exc.retryable,
                        error=str(exc)[:1000],
                    )
                else:
                    await store.observer_finished(call_id, status="failed", error=str(exc)[:1000])
                results.append({"run_id": row["id"], "error": str(exc)})
                continue
            calls += 1
            tokens += completion.input_tokens + completion.output_tokens
            cost += completion.cost_usd or 0
            result = {"run_id": row["id"], "result": completion.value}
            results.append(result)
            if kind == "experience":
                await store.add_experience(
                    project_scope_id=row.get("project_scope_id"),
                    backend=str(row["backend"]),
                    error=str(completion.value["error"]),
                    resolution=str(completion.value["resolution"]),
                    source_run_id=str(row["id"]),
                    confidence=float(completion.value["confidence"]),
                )
    except Exception as exc:
        error = str(exc)
    await store.finish_batch(
        batch_id,
        status="failed" if error else "completed",
        preview=results,
        calls=calls,
        tokens=tokens,
        cost_usd=cost,
        error=error,
    )


ROUTES: tuple[web.RouteDef, ...] = (
    web.post("/api/history/{sid}/second-opinion", second_opinion),
    web.get("/api/history/{sid}/handoff", export_handoff),
    web.get("/api/telemetry/workloads", workload_telemetry),
    web.get("/api/experiences", list_experiences),
    web.get("/api/automation/batches", list_observer_batches),
    web.post("/api/automation/batches", create_observer_batch),
)
