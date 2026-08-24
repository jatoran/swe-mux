"""Tier B: a real agent's session driven through the real /mcp wire.

The write and control tools are the ones the stub tests cannot fully prove: their
authority lives in the daemon, the endpoint authenticates a real minted token, and
interrupt/end actuate a real PTY. This tier stands up an isolated daemon on an
ephemeral port, spawns a real agent, recovers its bearer token from the live process
environment (via psutil, the way the manual method does), and drives `/mcp` end to
end. It is heavy and quota-consuming, so it is gated behind `live_mcp` +
`SWEMUX_RUN_LIVE_MCP_TESTS=1` and excluded from CI.

Coverage is derived: every agent harness with an MCP client is driven; pi is
excluded by its declared no-MCP-client capability, never skipped. The
`test_every_*` guards run in the default tier so a new harness or a new write tool
fails loudly until it is covered.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import psutil
import pytest

from swe_mux.harness import HARNESSES, live_control_harnesses
from swe_mux.mcp_contract import WRITE_TOOL_NAMES
from tests.support.live_daemon import IsolatedDaemon, isolated_daemon

RUN_MCP = os.environ.get("SWEMUX_RUN_LIVE_MCP_TESTS") == "1"
CONTROL_HARNESSES = list(live_control_harnesses())

_IDLE_SEED = "Reply with the single word READY and then wait for further instructions."


# ---------------------------------------------------------------- coverage guards


def test_every_mcp_capable_agent_harness_is_covered_by_the_control_canary() -> None:
    """Every agent that can call an MCP tool is driven here, or states why not.

    The registry-derived guard for the wire tier. An agent harness is covered
    exactly when it ships an MCP client (``adapter_family != "pi"``, the same fact
    the frontend gates its per-harness MCP toggle on). pi is excluded by that
    declared capability rather than by a skip inside the test.
    """
    for name, harness in HARNESSES.items():
        expected = harness.adapter_family != "pi"
        assert (name in CONTROL_HARNESSES) == expected, name
    assert CONTROL_HARNESSES, "the control canary covers no harness at all"


def test_every_write_tool_is_covered_by_a_live_wire_test() -> None:
    """A new MCP write tool fails this guard until it is given wire coverage.

    ``run_action`` starts a human-approved task and is covered by the project-actions
    tests (it needs an approved task file, which the wire tier does not author); every
    other write tool is exercised through ``/mcp`` in this module. The partition is
    asserted exhaustively so adding a write tool without coverage fails here.
    """
    driven_on_the_wire = {
        "notify",
        # Driven in the notify canary: both share notify's target resolution and
        # sender attribution, so they need no agents of their own.
        "revoke_message",
        "request_spawn",
        "interrupt",
        "end_session",
        "request_land",
        # Driven in the land canary, from the same worktree and the same session: the
        # two share every bound and differ only in which step the pipeline stops at, so
        # a second agent spawn would prove nothing the first does not.
        "request_verify",
    }
    covered_elsewhere = {"run_action"}
    assert set(WRITE_TOOL_NAMES) == driven_on_the_wire | covered_elsewhere
    assert not (driven_on_the_wire & covered_elsewhere)


# ------------------------------------------------------------------- live helpers


async def _spawn_agent(
    daemon: IsolatedDaemon,
    project_id: str,
    backend: str,
    name: str,
    seed: str,
    cwd: str | None = None,
) -> tuple[str, int, str]:
    """Spawn a real agent and return (session id, pid, recovered MCP token).

    ``cwd`` is the checkout the agent is spawned into. It matters for exactly one
    tool: `request_land` reads the caller's own live cwd rather than a target
    argument, so a canary for it has to be able to put the agent in a worktree.
    """
    snapshot = await daemon.spawn(project_id, backend, seed, name, cwd=cwd)
    sid = str(snapshot["id"])
    pid = int(snapshot["pid"])
    token = ""
    for _ in range(20):
        try:
            token = daemon.token_for(pid)
            break
        except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
            await asyncio.sleep(0.25)
    assert token, f"{backend} spawned no recoverable MCP token"
    return sid, pid, token


# --------------------------------------------------------------- live wire tests


@pytest.mark.live_agent
@pytest.mark.live_mcp
@pytest.mark.skipif(not RUN_MCP, reason="set SWEMUX_RUN_LIVE_MCP_TESTS=1")
@pytest.mark.parametrize("backend", CONTROL_HARNESSES)
async def test_wire_identity_and_env_isolation(backend: str, tmp_path: Path) -> None:
    """The endpoint authenticates the real token, and the child env is isolated.

    Proves the two load-bearing facts: `resolve_caller` maps the recovered bearer
    token to the caller's own session (`get_session(self)` returns its id), and the
    per-session env override held — the spawned agent's `MUX_HOOK_URL` names this
    isolated daemon's port, not the live fleet's, which is what makes the whole tier
    safe to run from a worktree.
    """
    async with isolated_daemon(tmp_path) as daemon:
        project_id = await daemon.register_project()
        sid, pid, token = await _spawn_agent(
            daemon, project_id, backend, "caller", _IDLE_SEED
        )

        env = daemon.child_env(pid)
        assert env.get("MUX_HOOK_URL") == f"http://127.0.0.1:{daemon.port}/api/hooks/{sid}"
        assert env.get("MUX_MCP_TOKEN") == token

        payload, is_error = await daemon.call_tool(token, "get_session", {"session_id": "self"})
        assert not is_error, payload
        assert payload["session_id"] == sid, payload


@pytest.mark.live_agent
@pytest.mark.live_mcp
@pytest.mark.skipif(not RUN_MCP, reason="set SWEMUX_RUN_LIVE_MCP_TESTS=1")
@pytest.mark.parametrize("backend", CONTROL_HARNESSES)
async def test_notify_reaches_a_sibling_queue(backend: str, tmp_path: Path) -> None:
    """A caller's notify lands in a sibling's queue as an attributed message.

    The same wire also covers the two surfaces that bracket it: the pre-send
    check that stages nothing, and the sender's own withdrawal of what it did
    stage. Both share notify's resolution and attribution, so driving them here
    costs no extra agents.
    """
    async with isolated_daemon(tmp_path) as daemon:
        project_id = await daemon.register_project()
        _caller_sid, _caller_pid, caller_token = await _spawn_agent(
            daemon, project_id, backend, "caller", _IDLE_SEED
        )
        target_sid, _target_pid, _target_token = await _spawn_agent(
            daemon, project_id, backend, "target", _IDLE_SEED
        )

        preview, is_error = await daemon.call_tool(
            caller_token,
            "notify",
            {"target": target_sid, "body": "handoff: please continue", "dry_run": True},
        )
        assert not is_error, preview
        assert preview["dry_run"] is True, preview
        assert preview["target_session_id"] == target_sid, preview
        assert "message_id" not in preview, preview
        assert isinstance(preview["target_delivery"]["auto_delivery"], bool), preview

        payload, is_error = await daemon.call_tool(
            caller_token,
            "notify",
            {"target": target_sid, "body": "handoff: please continue", "reason": "wire canary"},
        )
        assert not is_error, payload
        assert payload["target_session_id"] == target_sid, payload
        assert payload.get("message_id"), payload

        # Nothing has delivered it yet, so its sender can still take it back.
        revoked, is_error = await daemon.call_tool(
            caller_token,
            "revoke_message",
            {"message_id": payload["message_id"], "reason": "wire canary"},
        )
        assert not is_error, revoked
        assert revoked["status"] == "revoked", revoked
        assert revoked["queue_state"] == "cancelled", revoked


@pytest.mark.live_agent
@pytest.mark.live_mcp
@pytest.mark.skipif(not RUN_MCP, reason="set SWEMUX_RUN_LIVE_MCP_TESTS=1")
@pytest.mark.parametrize("backend", CONTROL_HARNESSES)
async def test_interrupt_is_readiness_gated_on_the_wire(backend: str, tmp_path: Path) -> None:
    """Interrupt through the wire either acts or is refused with its typed reason.

    Interrupt is fail-closed on readiness, so the deterministic assertion is that the
    granted call resolves to one of two real outcomes — an interrupt, or a typed
    `readiness_not_safe` refusal — never a crash and never a silent success. Both
    prove the gate is on the acting path.
    """
    async with isolated_daemon(tmp_path) as daemon:
        project_id = await daemon.register_project()
        _caller_sid, _caller_pid, caller_token = await _spawn_agent(
            daemon, project_id, backend, "caller", _IDLE_SEED
        )
        target_sid, _target_pid, _target_token = await _spawn_agent(
            daemon, project_id, backend, "target", _IDLE_SEED
        )

        payload, is_error = await daemon.call_tool(
            caller_token, "interrupt", {"target": target_sid, "reason": "wire canary"}
        )
        if is_error:
            assert payload.get("error") == "readiness_not_safe", payload
        else:
            assert payload["status"] == "interrupted", payload
            assert payload["grant"] == "granted", payload


@pytest.mark.live_agent
@pytest.mark.live_mcp
@pytest.mark.skipif(not RUN_MCP, reason="set SWEMUX_RUN_LIVE_MCP_TESTS=1")
@pytest.mark.parametrize("backend", CONTROL_HARNESSES)
async def test_end_session_ends_a_sibling_gracefully(backend: str, tmp_path: Path) -> None:
    """A granted end_session actuates a real graceful, agent-initiated end."""
    async with isolated_daemon(tmp_path) as daemon:
        project_id = await daemon.register_project()
        _caller_sid, _caller_pid, caller_token = await _spawn_agent(
            daemon, project_id, backend, "caller", _IDLE_SEED
        )
        target_sid, _target_pid, _target_token = await _spawn_agent(
            daemon, project_id, backend, "target", _IDLE_SEED
        )

        payload, is_error = await daemon.call_tool(
            caller_token, "end_session", {"target": target_sid, "reason": "wire canary"}
        )
        assert not is_error, payload
        assert payload["status"] == "ended", payload
        assert payload["grant"] == "granted", payload
        assert payload["end_reason"] == "agent_ended", payload

        # The record survives as ended/absent; it never stays live.
        for _ in range(20):
            snapshot = await daemon.session(target_sid)
            if snapshot is None or snapshot.get("state") in {"exited", "crashed"}:
                break
            await asyncio.sleep(0.25)
        else:
            pytest.fail(f"{backend} target never reached a terminal state")


@pytest.mark.live_agent
@pytest.mark.live_mcp
@pytest.mark.skipif(not RUN_MCP, reason="set SWEMUX_RUN_LIVE_MCP_TESTS=1")
@pytest.mark.parametrize("backend", CONTROL_HARNESSES)
async def test_request_spawn_granted_creates_then_ends(backend: str, tmp_path: Path) -> None:
    """A granted request_spawn creates a live session the caller can then end.

    The spawn -> monitor -> end lifecycle the granted-spawn feature exists for: the
    call returns a live session id (not a draft), get_session reads it, and
    end_session tears it down.
    """
    async with isolated_daemon(tmp_path) as daemon:
        project_id = await daemon.register_project()
        _caller_sid, _caller_pid, caller_token = await _spawn_agent(
            daemon, project_id, backend, "caller", _IDLE_SEED
        )

        payload, is_error = await daemon.call_tool(
            caller_token,
            "request_spawn",
            {"prompt": _IDLE_SEED, "backend": backend, "name": "spawned", "reason": "wire canary"},
        )
        assert not is_error, payload
        assert payload["status"] == "spawned", payload
        spawned_id = str(payload["session_id"])

        seen, _ = await daemon.call_tool(caller_token, "get_session", {"session_id": spawned_id})
        assert seen["session_id"] == spawned_id, seen

        ended, end_error = await daemon.call_tool(
            caller_token, "end_session", {"target": spawned_id, "reason": "wire canary cleanup"}
        )
        assert not end_error, ended
        assert ended["status"] == "ended", ended


@pytest.mark.live_agent
@pytest.mark.live_mcp
@pytest.mark.skipif(not RUN_MCP, reason="set SWEMUX_RUN_LIVE_MCP_TESTS=1")
@pytest.mark.parametrize("backend", CONTROL_HARNESSES)
async def test_request_land_enqueues_the_callers_own_worktree(
    backend: str, tmp_path: Path
) -> None:
    """A granted request_land enqueues the checkout the caller is actually in.

    The wire fact this proves is the one the tool's shape depends on: there is no
    target argument, so the branch comes from the caller's own live cwd. An agent
    spawned into a worktree lands *that* worktree, and a caller sitting in the
    primary checkout is refused rather than landing the trunk into itself.
    """
    async with isolated_daemon(tmp_path) as daemon:
        await asyncio.to_thread(
            subprocess.run,
            ["git", "commit", "-q", "--allow-empty", "-m", "initial"],
            cwd=daemon.root,
            check=True,
        )
        worktree = tmp_path / "wt-alpha"
        await asyncio.to_thread(
            subprocess.run,
            ["git", "worktree", "add", "-b", "worktree-alpha", str(worktree)],
            cwd=daemon.root,
            check=True,
        )
        # A branch that is level with the trunk has nothing to land, and the service
        # says so rather than queueing a no-op — so the canary has to give the
        # worktree a commit of its own before it can prove anything about scoping.
        await asyncio.to_thread(
            subprocess.run,
            ["git", "commit", "-q", "--allow-empty", "-m", "work on the branch"],
            cwd=worktree,
            check=True,
        )
        project_id = await daemon.register_project()
        _sid, pid, token = await _spawn_agent(
            daemon, project_id, backend, "brancher", _IDLE_SEED, cwd=str(worktree)
        )
        assert pid

        # The verify-only kind first, because one branch holds one request at a time:
        # both tools reach the same service over the same wire and the only difference
        # is which step the pipeline stops at.
        checked, check_error = await daemon.call_tool(
            token, "request_verify", {"reason": "wire canary"}
        )
        assert not check_error, checked
        assert checked["state"] == "queued", checked
        assert checked["kind"] == "verify", checked
        assert checked["branch"] == "worktree-alpha", checked
        cancelled = await daemon.client.delete(f"/api/land/{checked['id']}")
        assert cancelled.status == 200, await cancelled.text()

        payload, is_error = await daemon.call_tool(
            token, "request_land", {"reason": "wire canary"}
        )
        assert not is_error, payload
        assert payload["state"] == "queued", payload
        assert payload["kind"] == "land", payload
        assert payload["branch"] == "worktree-alpha", payload

        # The same call from the trunk itself has nothing to land into.
        _trunk_sid, _trunk_pid, trunk_token = await _spawn_agent(
            daemon, project_id, backend, "in-trunk", _IDLE_SEED
        )
        refused, refused_error = await daemon.call_tool(
            trunk_token, "request_land", {"reason": "wire canary"}
        )
        assert refused_error, refused
        # Typed, because a refusal is an answer the agent has to act on. This
        # arrived as `500 internal server error` until the service's refusals were
        # translated on this path the way both HTTP routes already translated them.
        assert refused.get("error") == "already_landed", refused
        assert refused.get("message"), refused
