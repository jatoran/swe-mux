"""Live canary: a real provider CLI exports OTLP to the daemon's ingress contract.

The unit tests prove the reducer against captured payloads; this tier proves the
capture itself is still possible - that the current CLI honours the exporter
environment or arguments the daemon hands it, posts to the configured path with the
configured header, and still names its events the way the reducer expects. It
consumes provider quota and is gated the same way as the other live canaries.

Run by hand:
    SWEMUX_RUN_LIVE_PHASE2_TESTS=1 uv run pytest tests/test_live_otlp.py -m live_telemetry
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from swe_mux.harness import HARNESSES
from swe_mux.telemetry_otlp import (
    CONTENT_ATTRIBUTES,
    IDENTITY_ATTRIBUTES,
    OtlpReduction,
    otlp_log_reduction,
    provider_otel_args,
    provider_otel_env,
)
from tests.test_live_agent_conformance import RUN_PHASE2, _executable, _probe_command

#: The harnesses with a verified native OTLP contract. Derived from the exporter
#: helpers rather than listed, so adding a provider there adds it here.
OTLP_HARNESSES = tuple(
    name
    for name in HARNESSES
    if provider_otel_env(name, enabled=True, ingress_url="http://h", session_id="s", secret="x")
    or provider_otel_args(name, enabled=True, ingress_url="http://h", session_id="s", secret="x")
)

PROMPT = "Read the file hello.txt in this directory and reply with its single word."


def test_every_otlp_harness_is_covered_by_this_canary() -> None:
    assert set(OTLP_HARNESSES) == {"claude", "codex"}


def _command(backend: str, ingress: str) -> list[str]:
    command = _probe_command(backend, PROMPT, None, "read_tool")
    if backend == "claude":
        # `--allowedTools` is variadic, so a trailing prompt would be read as a
        # tool name; the prompt goes first.
        command = [command[0], "--print", PROMPT, *command[2:-1]]
    args = provider_otel_args(
        backend, enabled=True, ingress_url=ingress, session_id="live", secret="secret"
    )
    if args:
        command = [command[0], *args, *command[1:]]
    return command


def _environment(backend: str, ingress: str) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OTEL_", "MUX_")) and key != "CLAUDE_CODE_ENABLE_TELEMETRY"
    }
    env.update(
        provider_otel_env(
            backend, enabled=True, ingress_url=ingress, session_id="live", secret="secret"
        )
    )
    return env


async def _receive(backend: str, workspace: Path) -> tuple[list[str], list[OtlpReduction]]:
    """Run the CLI once against a loopback receiver; return paths hit and reductions."""

    paths: list[str] = []
    reductions: list[OtlpReduction] = []

    async def ingress(request: web.Request) -> web.Response:
        paths.append(request.path)
        assert request.headers.get("X-Mux-Hook-Secret") == "secret", dict(request.headers)
        payload = await request.json()
        reductions.append(otlp_log_reduction(payload, session_id="live", backend=backend))
        return web.json_response({"partialSuccess": {}})

    app = web.Application(client_max_size=16 * 1024 * 1024)
    app.router.add_post("/api/telemetry/otlp/{sid}/v1/logs", ingress)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    server = site._server
    assert server is not None
    port = server.sockets[0].getsockname()[1]
    ingress_url = f"http://127.0.0.1:{port}"
    (workspace / "hello.txt").write_text("pineapple\n", encoding="utf-8")
    command = _command(backend, ingress_url)
    if os.name == "nt" and Path(command[0]).suffix.casefold() in {".cmd", ".bat"}:
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", *command]
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            command,
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=240,
            check=False,
            env=_environment(backend, ingress_url),
        )
        assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")[-1500:]
        # Exporters flush on exit; give the last batch a moment to land.
        await asyncio.sleep(3)
    finally:
        await runner.cleanup()
    return paths, reductions


@pytest.mark.live_agent
@pytest.mark.live_telemetry
@pytest.mark.skipif(not RUN_PHASE2, reason="set SWEMUX_RUN_LIVE_PHASE2_TESTS=1")
@pytest.mark.parametrize("backend", OTLP_HARNESSES)
async def test_authenticated_provider_exports_otlp_to_the_ingress_contract(
    backend: str, tmp_path: Path
) -> None:
    _executable(backend)  # raises where the CLI is not installed
    paths, reductions = await _receive(backend, tmp_path)

    assert paths, f"{backend} posted nothing to the configured OTLP endpoint"
    assert set(paths) == {"/api/telemetry/otlp/live/v1/logs"}
    events: list[Any] = [event for reduction in reductions for event in reduction.events]
    types = {event.type for event in events}
    assert "canonical_tool_result" in types, types
    assert "canonical_model_request" in types, types
    unrecognised = {
        name
        for reduction in reductions
        for (name, recognised) in reduction.signatures
        if not recognised
    }
    assert not unrecognised, f"{backend} sent event names the reducer does not know: {unrecognised}"
    for event in events:
        assert not set(event.payload) & (IDENTITY_ATTRIBUTES | CONTENT_ATTRIBUTES)
    results = [event for event in events if event.type == "canonical_tool_result"]
    assert any(event.payload.get("call_id") for event in results)
    assert any(event.payload.get("duration_ms") for event in results)
    assert any(event.payload.get("harness_version") for event in events)
