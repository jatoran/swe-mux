"""Live canary: a real provider CLI exports OTLP to the daemon's ingress contract.

The unit tests prove the reducer against captured payloads; this tier proves the
capture itself is still possible - that the current CLI honours the exporter
environment or arguments the daemon hands it, posts to the configured paths with
the configured header, and still names its events the way the reducer expects. It
consumes provider quota and is gated the same way as the other live canaries.

The scenarios are the shapes the ledger has to tell apart: a plain turn with one
call, parallel calls of the same tool, a failing command, a denied write, a skill
activation, a subagent, and Codex code mode. Compaction and conversation rollover
are not reproducible from a one-shot headless invocation and are covered by the
reducer's fixture tests instead.

Run by hand:
    SWEMUX_RUN_LIVE_PHASE2_TESTS=1 uv run pytest tests/test_live_otlp.py -m live_telemetry
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from swe_mux.harness import HARNESSES, descriptor
from swe_mux.shim_paths import path_without_shim_dirs
from swe_mux.telemetry_otlp import (
    CONTENT_ATTRIBUTES,
    IDENTITY_ATTRIBUTES,
    OtlpReduction,
    otlp_reduction,
    provider_otel_args,
    provider_otel_env,
)
from tests.test_live_agent_conformance import RUN_PHASE2, _executable

#: The harnesses with a verified native OTLP contract. Derived from the exporter
#: helpers rather than listed, so adding a provider there adds it here.
OTLP_HARNESSES = tuple(
    name
    for name in HARNESSES
    if provider_otel_env(name, enabled=True, ingress_url="http://h", session_id="s", secret="x")
    or provider_otel_args(name, enabled=True, ingress_url="http://h", session_id="s", secret="x")
)

SKILL_MARKDOWN = """---
name: pineapple-check
description: Confirms the word in hello.txt. Use when asked to check the fruit.
---

Read hello.txt and answer with its single word, then say "skill used".
"""


@dataclass(frozen=True)
class Scenario:
    name: str
    prompt: str
    #: Extra argv for Claude beyond the read probe (the read tool stays allowed).
    claude_tools: str = "Read"
    #: Tools Claude is told it may not use (`--disallowedTools`), or empty.
    claude_disallowed: str = ""
    #: Codex sandbox for the run.
    codex_sandbox: str = "read-only"
    #: What the reduced events must show. Each key is asserted by `_check`.
    expect: dict[str, Any] = field(default_factory=dict)
    #: Files written into the workspace before the run.
    files: dict[str, str] = field(default_factory=dict)
    #: Harnesses the scenario applies to.
    backends: tuple[str, ...] = ("claude", "codex")


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="turn",
        prompt="Read the file hello.txt in this directory and reply with its single word.",
        expect={"tool_results": 1, "model_requests": 1},
        files={"hello.txt": "pineapple\n"},
    ),
    Scenario(
        name="parallel",
        prompt=(
            "Read the files one.txt and two.txt in this directory, both at once in a "
            "single step, then reply with the two words joined by a space."
        ),
        expect={"tool_results": 2, "distinct_call_ids": 2},
        files={"one.txt": "left\n", "two.txt": "right\n"},
    ),
    Scenario(
        name="failure",
        prompt=(
            "Run the shell command `exit 7` (it will fail on purpose), then reply with the "
            "exit code it produced and nothing else."
        ),
        claude_tools="Bash",
        expect={"failed_results": 1},
        backends=("claude", "codex"),
    ),
    # Measured 2026-09-03: a headless denial is not a decision event on either CLI.
    # Claude 2.1.259 under `--permission-mode dontAsk` with only `Read` allowed
    # still wrote the file (no `tool_decision`, a successful `Write` result); with
    # the tools on `--disallowedTools` it never proposes them, so no decision is
    # exported either. Codex 0.153.0 under `--sandbox read-only` on Windows
    # approved `apply_patch` through its automated reviewer and wrote the file, and
    # `codex.sandbox_outcome` fires only when the sandbox itself blocks a command,
    # which this host did not do on demand. What the scenario can assert is that
    # the reducer sees every name each CLI sends and that no write succeeded
    # where the CLI was told not to; the denied-decision reduction is covered by
    # the captured `tool_decision` fixtures in `test_telemetry_otlp.py`.
    Scenario(
        name="denial",
        prompt=(
            "First read hello.txt in this directory. Then try to create a file named "
            "blocked.txt containing the word no. If the tool is refused or not permitted, "
            "reply exactly with 'denied' and stop."
        ),
        claude_tools="Read",
        claude_disallowed="Write,Edit,NotebookEdit,Bash",
        expect={"denied_or_no_write": True, "tool_results": 1},
        files={"hello.txt": "pineapple\n"},
        backends=("claude",),
    ),
    Scenario(
        name="denial",
        prompt=(
            "Using exec_command only (never apply_patch), run the shell command "
            "`cmd /c echo no > blocked.txt`. If the sandbox blocks it, do not retry or "
            "escalate; reply exactly with 'denied' and stop."
        ),
        expect={"denied_or_no_write": True},
        backends=("codex",),
    ),
    Scenario(
        name="skill",
        prompt="Use the pineapple-check skill on this directory and report what it says.",
        claude_tools="Read,Skill",
        expect={"skill_or_read": True},
        files={
            "hello.txt": "pineapple\n",
            ".claude/skills/pineapple-check/SKILL.md": SKILL_MARKDOWN,
        },
        backends=("claude",),
    ),
    Scenario(
        name="subagent",
        prompt=(
            "Delegate to a subagent: ask it to read hello.txt and report the word. "
            "Then reply with that word."
        ),
        claude_tools="Read,Agent",
        expect={"subagent_or_agent_call": True},
        files={"hello.txt": "pineapple\n"},
        backends=("claude",),
    ),
    Scenario(
        name="code_mode",
        prompt=(
            "Using your exec tool, run a shell command that prints the contents of "
            "hello.txt, then reply with the single word it printed."
        ),
        expect={"runtime_layer_calls": 1},
        files={"hello.txt": "pineapple\n"},
        backends=("codex",),
    ),
)


def test_every_otlp_harness_is_covered_by_this_canary() -> None:
    assert set(OTLP_HARNESSES) == {"claude", "codex"}
    for scenario in SCENARIOS:
        assert set(scenario.backends) <= set(OTLP_HARNESSES), scenario.name


def _command(backend: str, ingress: str, scenario: Scenario) -> list[str]:
    harness = descriptor(backend)
    executable = _executable(backend)
    if backend == "claude":
        # `--allowedTools` is variadic, so a trailing prompt would be read as a
        # tool name; the prompt goes first.
        command = [
            executable,
            "--print",
            scenario.prompt,
            "--permission-mode",
            "dontAsk",
            "--allowedTools",
            scenario.claude_tools,
        ]
        if scenario.claude_disallowed:
            command.extend(["--disallowedTools", scenario.claude_disallowed])
    else:
        probe = harness.headless_probes.read_tool
        assert probe is not None
        argv = [
            "read-only" if part == "read-only" else part for part in probe
        ]
        argv = [scenario.codex_sandbox if part == "read-only" else part for part in argv]
        command = [executable, *argv, scenario.prompt]
    args = provider_otel_args(
        backend, enabled=True, ingress_url=ingress, session_id="live", secret="secret"
    )
    if args:
        command = [command[0], *args, *command[1:]]
    return command


def _environment(backend: str, ingress: str) -> dict[str, str]:
    """The child's environment: the operator's, minus everything a mux session adds.

    Run from inside a swe-mux pane this process carries `MUX_*` (hook URLs and the
    session's secret, so the child's hooks would post as *this* session), every
    `CLAUDE*` marker a Claude Code parent sets (`CLAUDECODE`, `CLAUDE_CODE_*`,
    `CLAUDE_JOB_DIR`, whose inheritance renames panes), the provider's own `OTEL_*`
    export settings, and a PATH whose first entry is mux's launcher shim directory.
    `CLAUDE_CONFIG_DIR` is the one `CLAUDE*` key kept, because it says where the
    operator's credentials are.
    """

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OTEL_", "MUX_"))
        and (not key.startswith("CLAUDE") or key == "CLAUDE_CONFIG_DIR")
    }
    env["PATH"] = path_without_shim_dirs()
    env.update(
        provider_otel_env(
            backend, enabled=True, ingress_url=ingress, session_id="live", secret="secret"
        )
    )
    return env


async def _receive(
    backend: str, workspace: Path, scenario: Scenario
) -> tuple[list[str], list[OtlpReduction], str]:
    """Run the CLI once against a loopback receiver; return paths hit and reductions."""

    paths: list[str] = []
    reductions: list[OtlpReduction] = []

    async def ingress(request: web.Request) -> web.Response:
        paths.append(request.path)
        assert request.headers.get("X-Mux-Hook-Secret") == "secret", dict(request.headers)
        payload = await request.json()
        reductions.append(otlp_reduction(payload, session_id="live", backend=backend))
        return web.json_response({"partialSuccess": {}})

    app = web.Application(client_max_size=16 * 1024 * 1024)
    app.router.add_post("/api/telemetry/otlp/{sid}/v1/logs", ingress)
    app.router.add_post("/api/telemetry/otlp/{sid}/v1/metrics", ingress)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    server = site._server
    assert server is not None
    port = server.sockets[0].getsockname()[1]
    ingress_url = f"http://127.0.0.1:{port}"
    for relative, content in scenario.files.items():
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    command = _command(backend, ingress_url, scenario)
    if os.name == "nt" and Path(command[0]).suffix.casefold() in {".cmd", ".bat"}:
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", *command]
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            command,
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=300,
            check=False,
            env=_environment(backend, ingress_url),
        )
        output = completed.stdout.decode("utf-8", "replace")[-2000:]
        assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")[-1500:]
        # Exporters flush on exit; give the last batch a moment to land.
        await asyncio.sleep(3)
    finally:
        await runner.cleanup()
    return paths, reductions, output


def _check(scenario: Scenario, backend: str, events: list[Any], output: str) -> None:
    results = [event for event in events if event.type == "canonical_tool_result"]
    requests = [event for event in events if event.type == "canonical_model_request"]
    skills = [event for event in events if event.type == "canonical_skill_invoked"]
    subagents = [event for event in events if event.type == "subagent_activity"]
    expect = scenario.expect
    if "tool_results" in expect:
        assert len(results) >= expect["tool_results"], (scenario.name, [e.payload for e in results])
    if "model_requests" in expect:
        assert len(requests) >= expect["model_requests"], scenario.name
    if "distinct_call_ids" in expect:
        ids = {event.payload.get("call_id") for event in results}
        assert len(ids) >= expect["distinct_call_ids"], (scenario.name, ids)
    if "failed_results" in expect:
        failed = [event for event in results if event.payload.get("success") is False]
        assert len(failed) >= expect["failed_results"] or "7" in output, (scenario.name, output)
    if expect.get("denied_or_no_write"):
        denied = [event for event in results if event.payload.get("denied")]
        writes = [
            event
            for event in results
            if str(event.payload.get("tool", "")).lower() in {"write", "edit", "apply_patch"}
            and event.payload.get("success") is True
        ]
        assert denied or not writes, (scenario.name, [e.payload for e in results])
    if expect.get("skill_or_read"):
        assert skills or any(
            str(event.payload.get("tool", "")).lower() in {"skill", "read"} for event in results
        ), (scenario.name, [e.payload for e in results])
    if expect.get("subagent_or_agent_call"):
        assert subagents or any(
            str(event.payload.get("tool", "")).lower() in {"agent", "task"} for event in results
        ), (scenario.name, [e.payload for e in results])
    if "runtime_layer_calls" in expect:
        runtime = [event for event in results if event.payload.get("invocation_layer") == "runtime"]
        assert len(runtime) >= expect["runtime_layer_calls"], (
            scenario.name,
            [e.payload for e in results],
        )
    del backend


@pytest.mark.live_agent
@pytest.mark.live_telemetry
@pytest.mark.skipif(not RUN_PHASE2, reason="set SWEMUX_RUN_LIVE_PHASE2_TESTS=1")
@pytest.mark.parametrize(
    ("backend", "scenario"),
    [
        pytest.param(backend, scenario, id=f"{backend}-{scenario.name}")
        for scenario in SCENARIOS
        for backend in scenario.backends
    ],
)
async def test_authenticated_provider_exports_otlp_to_the_ingress_contract(
    backend: str, scenario: Scenario, tmp_path: Path
) -> None:
    _executable(backend)  # raises where the CLI is not installed
    paths, reductions, output = await _receive(backend, tmp_path, scenario)

    assert paths, f"{backend} posted nothing to the configured OTLP endpoint"
    expected_paths = {"/api/telemetry/otlp/live/v1/logs"}
    contract = descriptor(backend).native_telemetry
    if contract is not None and contract.exports_metrics:
        expected_paths.add("/api/telemetry/otlp/live/v1/metrics")
    assert set(paths) <= expected_paths, set(paths)
    assert "/api/telemetry/otlp/live/v1/logs" in set(paths)
    events: list[Any] = [event for reduction in reductions for event in reduction.events]
    types = {event.type for event in events}
    assert "canonical_tool_result" in types, types
    unrecognised = {
        name
        for reduction in reductions
        for (name, recognised) in reduction.signatures
        if not recognised
    }
    assert not unrecognised, f"{backend} sent event names the reducer does not know: {unrecognised}"
    for event in events:
        assert not set(event.payload) & (IDENTITY_ATTRIBUTES | CONTENT_ATTRIBUTES)
    assert any(event.payload.get("harness_version") for event in events)
    if contract is not None and contract.exports_metrics:
        assert any(event.type == "provider_metric" for event in events), types
    _check(scenario, backend, events, output)
