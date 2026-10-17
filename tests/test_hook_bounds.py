"""Hook bodies are bounded at the source, and nothing the daemon reads is lost to it.

Across the 2026-10-16 fleet hook bodies averaged 50 KiB, oversized ones were refused
(1,688 lost events in 2.5 hours) and each refusal was resent twice more
(`DAEMON_LOAD_ISOLATION.md` item 9). Each reader of a full hook body is pinned here
against what it needs: the output's size and digest, the call's target and content
hash, and an approval request's input, which is never cut.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import threading
from pathlib import Path
from typing import Any, cast

import pytest

from swe_mux import hook_client
from swe_mux.event_bus import EventBus
from swe_mux.hook_bounds import (
    BOUNDS_KEY,
    FIELD_CAP,
    HOOK_BODY_BUDGET,
    OUTPUT_HEAD_CHARS,
    bound_hook_payload,
    client_bounds,
    encoded_body,
    hook_output_text,
    text_metrics,
)
from swe_mux.observation import apply_hook_observation
from swe_mux.routes.agent_ingress import hook_event_payload
from swe_mux.tool_evidence import tool_call_evidence
from tests.support.detection_replay import ReplaySession


def _post_tool_use(size: int, *, unicode: bool = False) -> dict[str, Any]:
    filler = ("é✓" * (size // 2)) if unicode else ("x" * size)
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_use_id": "toolu_1",
        "tool_input": {"command": "cat big.log"},
        "tool_response": {"stdout": filler, "stderr": "", "interrupted": False},
    }


def test_a_small_output_travels_whole_beside_its_measurement() -> None:
    payload = _post_tool_use(500)
    bounded = bound_hook_payload("PostToolUse", payload)
    assert bounded["tool_response"] == payload["tool_response"]
    assert client_bounds(bounded)["output"] == text_metrics(
        hook_output_text(payload["tool_response"])
    )
    small = {"hook_event_name": "Stop", "stop_hook_active": False}
    assert bound_hook_payload("Stop", small) is small


def test_a_measured_output_travels_as_its_head_even_when_the_body_fits() -> None:
    """Nothing in the daemon reads an output once it is measured; the 50 KiB average
    body was a tool's output parsed on the loop for nothing."""
    payload = _post_tool_use(50_000)
    bounded = bound_hook_payload("PostToolUse", payload)
    assert bounded["tool_response"] == hook_output_text(payload["tool_response"])[
        :OUTPUT_HEAD_CHARS
    ]
    assert client_bounds(bounded)["output"]["chars"] > 50_000
    assert len(encoded_body("PostToolUse", bounded)) < 4 * OUTPUT_HEAD_CHARS


@pytest.mark.parametrize("unicode", [False, True])
def test_an_oversized_output_is_cut_to_fit_and_measured_whole(unicode: bool) -> None:
    payload = _post_tool_use(400_000, unicode=unicode)
    bounded = bound_hook_payload("PostToolUse", payload)
    assert len(encoded_body("PostToolUse", bounded)) <= HOOK_BODY_BUDGET
    assert len(bounded["tool_response"]) <= FIELD_CAP
    # Exactly what the daemon computed from the whole body before this existed.
    assert client_bounds(bounded)["output"] == text_metrics(
        hook_output_text(payload["tool_response"])
    )
    assert payload["tool_response"]["stdout"], "the caller's payload is never mutated"


def test_a_reported_tool_inputs_evidence_is_computed_before_it_is_cut() -> None:
    content = "line\n" * 100_000
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_use_id": "toolu_2",
        "tool_input": {"file_path": "C:/repo/big.txt", "content": content},
    }
    bounded = bound_hook_payload("PreToolUse", payload)
    assert len(encoded_body("PreToolUse", bounded)) <= HOOK_BODY_BUDGET
    assert len(bounded["tool_input"]["content"]) < len(content)
    assert bounded["tool_input"]["file_path"] == "C:/repo/big.txt"
    assert client_bounds(bounded)["tool_evidence"] == tool_call_evidence(payload["tool_input"])
    assert tool_call_evidence(bounded["tool_input"]) != tool_call_evidence(payload["tool_input"])


def test_an_approval_requests_input_is_never_cut() -> None:
    """A rule matched against the head of a command could approve what its tail does."""
    command = "echo ok && " + ("x" * 300_000) + " && rm -rf /"
    payload = {
        "hook_event_name": "PermissionRequest",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "notes": "n" * 50_000,
    }
    bounded = bound_hook_payload("PermissionRequest", payload)
    assert bounded["tool_input"] == {"command": command}
    assert len(bounded["notes"]) < 50_000, "what can be cut still is"


def test_other_long_text_is_cut_only_when_the_body_still_does_not_fit() -> None:
    prompt = "p" * 300_000
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": prompt, "cwd": "C:/repo"}
    bounded = bound_hook_payload("UserPromptSubmit", payload)
    assert len(encoded_body("UserPromptSubmit", bounded)) <= HOOK_BODY_BUDGET
    assert client_bounds(bounded)["fields"]["prompt"] == text_metrics(prompt)
    assert bounded["cwd"] == "C:/repo"
    # A body whose tool cuts already fit leaves every other field whole.
    mixed = {**_post_tool_use(300_000), "message": "m" * 20_000}
    assert bound_hook_payload("PostToolUse", mixed)["message"] == mixed["message"]


def test_a_malformed_note_is_ignored() -> None:
    assert client_bounds({BOUNDS_KEY: "nope"}) == {}
    assert client_bounds({BOUNDS_KEY: {"output": {"chars": -1, "bytes": 2, "sha256": None}}}) == {}
    assert client_bounds({BOUNDS_KEY: {"output": {"chars": 1, "bytes": 1, "sha256": "zz"}}}) == {}
    assert client_bounds({BOUNDS_KEY: {"tool_evidence": ["a", 2, None]}}) == {}


async def test_the_daemon_takes_the_clients_measurements() -> None:
    session = cast(Any, ReplaySession("claude"))
    events = EventBus()
    canonical = events.subscribe(name="canonical-telemetry")
    ordinary = events.subscribe(name="ordinary")
    pre = bound_hook_payload(
        "PreToolUse",
        {
            "tool_name": "Write",
            "tool_use_id": "call-1",
            "tool_input": {"file_path": "C:/repo/a.txt", "content": "c" * 400_000},
        },
    )
    await apply_hook_observation(session, "PreToolUse", pre, events)
    tool_use = next(event for event in _drain(ordinary) if event.type == "tool_use")
    expected = tool_call_evidence(
        {"file_path": "C:/repo/a.txt", "content": "c" * 400_000}
    )
    assert (
        tool_use.payload["target"],
        tool_use.payload["content_hash"],
        tool_use.payload["target_digest"],
    ) == expected

    full = _post_tool_use(400_000)
    post = bound_hook_payload("PostToolUse", {**full, "tool_use_id": "call-1"})
    await apply_hook_observation(session, "PostToolUse", post, events)
    result = next(
        event for event in _drain(canonical) if event.type == "canonical_tool_result"
    )
    metrics = text_metrics(hook_output_text(full["tool_response"]))
    assert (
        result.payload["output_chars"],
        result.payload["output_bytes"],
        result.payload["output_sha256"],
    ) == (metrics["chars"], metrics["bytes"], metrics["sha256"])


def test_the_event_fan_out_never_carries_a_tools_output() -> None:
    """The `events` table persisted every `PostToolUse` output whole: 53 KB a row
    on average in the load harness, and whatever secret a tool printed."""
    payload = bound_hook_payload("PostToolUse", _post_tool_use(10_000))
    fanned = hook_event_payload(payload)
    assert "tool_response" not in fanned
    assert BOUNDS_KEY not in fanned
    assert fanned["tool_name"] == "Bash" and fanned["tool_use_id"] == "toolu_1"


def _drain(queue: asyncio.Queue[Any]) -> list[Any]:
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


class _Answer(http.server.BaseHTTPRequestHandler):
    status = 200
    hits = 0

    def do_POST(self) -> None:  # noqa: N802 - the stdlib's name
        type(self).hits += 1
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.send_response(type(self).status)
        self.end_headers()

    def log_message(self, *_args: Any) -> None:
        return


@pytest.mark.parametrize(
    ("status", "hits", "result"), [(413, 1, None), (404, 1, None), (500, 3, False)]
)
def test_a_refusal_is_final_and_a_failure_is_retried(
    status: int, hits: int, result: bool | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = type("Answer", (_Answer,), {"status": status, "hits": 0})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(hook_client.time, "sleep", lambda _seconds: None)
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/api/hooks/s1"
        assert hook_client._post(url, "secret", b"{}") is result
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert handler.hits == hits


def test_a_refused_durable_event_is_not_spooled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spool = tmp_path / "spool.jsonl"
    monkeypatch.setenv("MUX_HOOK_URL", "http://127.0.0.1:9/api/hooks/s1")
    monkeypatch.setenv("MUX_HOOK_SECRET", "secret")
    monkeypatch.setenv("MUX_HOOK_SPOOL", str(spool))
    monkeypatch.setattr(hook_client, "_read_payload", lambda: json.dumps({"x": 1}))
    for outcome, spooled in ((None, False), (False, True)):
        spool.unlink(missing_ok=True)
        monkeypatch.setattr(hook_client, "_post", lambda *_args, o=outcome: o)
        monkeypatch.setattr(hook_client.sys, "argv", ["hook_client", "Stop"])
        hook_client.main()
        assert spool.exists() is spooled
