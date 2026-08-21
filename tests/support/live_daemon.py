"""An isolated swe-mux daemon on an ephemeral port, for the live MCP wire canary.

Tier B needs the real thing: a daemon that mints a real per-session MCP token, a
real agent process that carries that token in its environment, and the real
``/mcp`` endpoint authenticating it. This builds exactly that, in process, on an
OS-allocated free port under a temp ``data_dir`` — never the operator's ``~/.mux``
and never port 8765 — so it is safe to run from a worktree the way the runtime
collision note requires. The daemon's ingress URL is derived from ``config.port``
(``session.py`` builds ``MUX_HOOK_URL``/``MUX_MCP_URL`` from it), so the harness
binds the test server to the same chosen port; that is what makes a spawned agent's
hook and MCP env point back at this daemon rather than the live one.

The spawned agent's token is recovered the way the manual method does it — via
``psutil.Process(pid).environ()`` — because it is env-expanded into the child and
never written to disk. That same read is what lets a test assert the env isolation
held (the child's ``MUX_HOOK_URL`` names this daemon's port, not the live fleet's).
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import psutil
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.config import Config
from swe_mux.server import create_app, wait_runtime_ready

# A registered Project that has granted agents direct interrupt/end and spawn, so
# the wire tests exercise the acting path rather than only the inert draft.
_GRANT_CONFIG = (
    # `version` is required by parse_project_config; without it the whole file is
    # rejected and both grant readers silently fall back to their draft default.
    "version = 1\n"
    'session_control_grant = "granted"\n'
    'spawn_grant = "granted"\n'
    "\n"
    'land_grant = "granted"\n'
    "\n"
    "[automations]\n"
    "session_control = true\n"
    # Phase 14: the land queue is its own capability with its own switch, so the
    # scratch Project has to opt into it separately from session control.
    "land_queue = true\n"
)


def free_port() -> int:
    """One OS-allocated loopback port, released immediately for the daemon to take.

    A brief race between release and re-bind is acceptable for a single-operator
    test box and far safer than a hardcoded port that could collide with the live
    daemon.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class IsolatedDaemon:
    """A thin HTTP driver over the isolated daemon and its ``/mcp`` endpoint."""

    def __init__(self, client: TestClient, root: Path, port: int) -> None:
        self.client = client
        self.root = root
        self.port = port

    async def register_project(self, name: str = "live") -> str:
        response = await self.client.post(
            "/api/projects",
            json={"name": name, "root": str(self.root), "create_missing": False},
        )
        assert response.status in (200, 201), await response.text()
        return str((await response.json())["id"])

    async def spawn(
        self,
        project_id: str,
        backend: str,
        seed_text: str,
        name: str,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        response = await self.client.post(
            "/api/sessions",
            json={
                "project_id": project_id,
                "backend": backend,
                # An explicit cwd is how a caller spawns into a worktree of this
                # repository; containment admits it through `resolve_listed_cwd`,
                # which asks Git rather than trusting the path.
                "cwd": cwd or str(self.root),
                "seed_text": seed_text,
                "name": name,
            },
        )
        assert response.status == 201, await response.text()
        return await response.json()

    async def session(self, sid: str) -> dict[str, Any] | None:
        response = await self.client.get(f"/api/sessions/{sid}")
        if response.status == 404:
            return None
        assert response.status == 200, await response.text()
        return await response.json()

    @staticmethod
    def child_env(pid: int) -> dict[str, str]:
        """The live environment of a spawned agent process, read via psutil."""
        return psutil.Process(pid).environ()

    def token_for(self, pid: int) -> str:
        """Recover one session's MCP bearer token from its live process env."""
        return self.child_env(pid)["MUX_MCP_TOKEN"]

    async def rpc(
        self, token: str, method: str, params: dict[str, Any] | None = None, *, mid: int = 1
    ) -> dict[str, Any]:
        response = await self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status == 200, await response.text()
        return await response.json()

    async def call_tool(
        self, token: str, name: str, arguments: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """Drive one tool through the wire; return its parsed payload and error flag."""
        envelope = await self.rpc(token, "tools/call", {"name": name, "arguments": arguments})
        result = envelope["result"]
        payload = json.loads(result["content"][0]["text"])
        return payload, bool(result.get("isError"))


@asynccontextmanager
async def isolated_daemon(tmp_path: Path) -> AsyncIterator[IsolatedDaemon]:
    """Bring up an isolated daemon with a granted, git-scoped scratch Project."""
    port = free_port()
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        subprocess.run, ["git", "init", "-q"], cwd=root, check=True
    )
    swe_dir = root / ".swe-mux"
    swe_dir.mkdir(exist_ok=True)
    (swe_dir / "config.toml").write_text(_GRANT_CONFIG, encoding="utf-8")

    config = Config(
        data_dir=tmp_path / ".mux",
        port=port,
        host="127.0.0.1",
        tailnet_enabled=False,
        reconcile_external_history=False,
        pty_supervisor_enabled=False,
        session_control_enabled=True,
    )
    server = TestServer(create_app(config), host="127.0.0.1", port=port)
    client = TestClient(server)
    await client.start_server()
    # The daemon binds its listeners before it builds its runtime, so a started
    # server is a *reachable* daemon and not yet a usable one. Every route but
    # health and the static document answers 503 until this returns.
    await wait_runtime_ready(client.app)
    try:
        yield IsolatedDaemon(client, root, port)
    finally:
        await client.close()
