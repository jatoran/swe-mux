"""Per-server MCP tool catalogs, collected only when a human asks for one.

Agent Environment's passive scan reports which MCP servers a CLI is *configured*
with and deliberately stops there: opening the tab never starts a server, never
authenticates, and never claims connection health
(``.docs/design/features/agent-environment.md``).  That invariant is what makes
the tab cheap and safe, and it stands.

What it cannot answer is "which tools does this server actually publish", because
that answer only exists inside a running MCP client.  This module supplies it
behind an explicit per-server action, using the best evidence each harness can
give without a second interactive runtime
(``.docs/development/AGENT_ENVIRONMENT_RUNTIME_INVENTORY.md``):

``swe_mux_owned``
    mux's own server.  The catalog is read from the implementation
    (:data:`swe_mux.mcp.TOOLS`, cross-checked against the closed contract in
    ``mcp_contract.py``), so it is exact and free, and it cannot drift from the
    tools the server actually serves.

``live_process``
    OMP.  The extension mux already injects into the *running* OMP process
    publishes its runtime tool inventory; nothing is spawned and the reading is
    the real session's.

``parallel_probe``
    Codex and Claude.  A short-lived sidecar reproduces the session's capability
    profile - ``codex app-server``'s ``mcpServerStatus/list``, or, for Claude
    (which has no headless path to its own runtime), the daemon dialing the
    configured server itself with the official ``mcp`` client.  The health of a
    sidecar is **not** the health of the user's TUI, and this module never says
    otherwise: every such result is labelled probe evidence.

``not_supported``
    opencode (its server exposes status, not tools) and pi (no MCP client at
    all).  Both stay passive, and say so, rather than reporting an empty catalog
    that would read as "this server has no tools".

Results are cached per *config-content fingerprint* rather than per session, so
many sessions sharing a profile share one probe, and concurrent requests for one
key share a single in-flight probe.  Nothing collected here is persisted, and
credentials never enter a payload: server endpoints are sanitized with Agent
Environment's own rules, and headers/environment reach only the fingerprint
digest, which is one-way.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, assert_never

from .agent_environment import McpServerConfig, safe_endpoint
from .harness import Backend, descriptor, require_backend
from .mcp_contract import READ_TOOL_NAMES, WRITE_TOOL_NAMES
from .subprocess_flags import background_creation_flags, reap_process_tree

log = logging.getLogger(__name__)

Evidence = Literal["swe_mux_owned", "live_process", "parallel_probe", "not_supported"]
Status = Literal["ok", "auth_required", "unsupported", "unavailable", "error"]

#: Ceiling on tools reported for one server. Deliberately generous - Codex's
#: `codex_apps` alone publishes 49 - and the truncation is always declared.
MAX_TOOLS = 256
MAX_NAME_CHARS = 160
MAX_DESCRIPTION_CHARS = 500

#: How long a probe result is reused when the server offers no `ttlMs` hint of
#: its own. Long enough that clicking through several servers costs one probe
#: each, short enough that an account-side connector change (which has no local
#: configuration fingerprint at all) is picked up within a working session.
DEFAULT_TTL_SECONDS = 600.0
MAX_TTL_SECONDS = 3600.0

#: Wall-clock ceilings. The Codex sidecar has to start a Rust binary and connect
#: every configured server, which measured 1.7s against four servers on the
#: development host; the Claude dial is one server.
CODEX_PROBE_TIMEOUT = 30.0
CLAUDE_PROBE_TIMEOUT = 20.0

#: One `mcpServerStatus/list` response arrives as a single JSON-RPC *line*, and
#: `toolsAndAuthOnly` trims resources but still carries every tool's full input
#: and output schema: on the development host, four servers and 89 tools came to
#: well over asyncio's 64 KiB default, which raises rather than truncating. Sized
#: for a large real inventory and still bounded, because the process on the other
#: end is not something this daemon controls.
CODEX_READ_LIMIT = 8 * 1024 * 1024

_STDIO_TRANSPORTS = frozenset({"stdio", "local", ""})
_HTTP_TRANSPORTS = frozenset({"http", "https", "sse", "streamable-http", "streamable_http"})

#: Header names that mean "this HTTP server wants credentials". A server that
#: needs auth is reported as such and never dialled: a probe would either fail
#: confusingly or, worse, succeed by spending a credential the user did not
#: knowingly hand to this feature.
_AUTH_HEADERS = frozenset({"authorization", "proxy-authorization", "x-api-key", "api-key"})


@dataclass(frozen=True, slots=True)
class McpTool:
    """One published tool, reduced to the fields that are safe to show."""

    name: str
    description: str
    read_only: bool | None = None

    def public(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name[:MAX_NAME_CHARS],
            "description": self.description[:MAX_DESCRIPTION_CHARS],
        }
        if self.read_only is not None:
            payload["read_only"] = self.read_only
        return payload


@dataclass(frozen=True, slots=True)
class McpToolCatalog:
    """The result of one fetch, whatever tier produced it."""

    server: str
    backend: str
    evidence: Evidence
    status: Status
    tools: tuple[McpTool, ...] = ()
    note: str = ""
    diagnostic: str = ""
    observed_at: float = 0.0
    ttl_seconds: float = DEFAULT_TTL_SECONDS
    #: `private` forbids sharing this reading beyond the session that asked, per
    #: the server's own `cacheScope` hint.
    cache_scope: Literal["public", "private"] = "public"
    server_version: str = ""

    def public(self, *, fingerprint: str, cached: bool) -> dict[str, Any]:
        tools = list(self.tools[:MAX_TOOLS])
        return {
            "server": self.server[:MAX_NAME_CHARS],
            "backend": self.backend,
            "evidence": self.evidence,
            "status": self.status,
            "tools": [tool.public() for tool in tools],
            "total": len(self.tools),
            "truncated": len(self.tools) > MAX_TOOLS,
            "note": self.note,
            "diagnostic": self.diagnostic[:MAX_DESCRIPTION_CHARS],
            "observed_at": self.observed_at,
            "ttl_ms": int(self.ttl_seconds * 1000),
            "cache_scope": self.cache_scope,
            "server_version": self.server_version[:MAX_NAME_CHARS],
            "fingerprint": fingerprint,
            "cached": cached,
        }


@dataclass(slots=True)
class _CacheEntry:
    catalog: McpToolCatalog
    stored_at: float
    #: Which session this reading was collected for. Only consulted when the
    #: server asked for a `private` cache scope, and then it is what stops the
    #: reading being handed to a different session.
    owner_session: str = ""


@dataclass(slots=True)
class _Cache:
    entries: dict[str, _CacheEntry] = field(default_factory=dict)
    inflight: dict[str, asyncio.Future[McpToolCatalog]] = field(default_factory=dict)
    inflight_owner: dict[str, str] = field(default_factory=dict)


_CACHE = _Cache()
#: Bounded so a long-lived daemon cannot accumulate one entry per (server,
#: profile) ever inspected. Nothing here is durable; eviction only costs a reprobe.
_MAX_CACHE_ENTRIES = 64


# ---------------------------------------------------------------------------
# Evidence tiers
# ---------------------------------------------------------------------------


def evidence_tier(backend: str) -> Evidence:
    """Which tier of evidence this harness's servers can be reported from.

    Two separate facts, kept separate on purpose. The registry declares *how* a
    harness can be interrogated (`mcp_tool_source`), because that is a property
    of the CLI and adding a harness must be a decision rather than a silent "no
    tools". This maps that mechanism onto what the answer *proves*, which is what
    the API and the UI label - and both sidecar mechanisms prove the same thing:
    a separate runtime agreed with the configuration.
    """
    source = descriptor(require_backend(backend)).mcp_tool_source
    if source == "live_process":
        return "live_process"
    if source == "app_server" or source == "client_dial":  # noqa: PLR1714 - narrows for mypy
        return "parallel_probe"
    if source == "none":
        return "not_supported"
    assert_never(source)


_TIER_NOTES: dict[Evidence, str] = {
    "swe_mux_owned": (
        "Published by swe-mux from the running server's own tool definitions, not probed."
    ),
    "live_process": (
        "Reported by the extension inside this running session, so it is the session's own "
        "tool inventory rather than a separate runtime's."
    ),
    "parallel_probe": (
        "Collected by a separate short-lived client that reproduces this session's "
        "configuration. It is evidence about the configuration, not the state of the CLI "
        "running in this terminal."
    ),
    "not_supported": "This harness exposes no runtime tool inventory to swe-mux.",
}


def tier_note(evidence: Evidence) -> str:
    return _TIER_NOTES[evidence]


# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------


def _digest(material: Any) -> str:
    encoded = json.dumps(material, sort_keys=True, default=str).encode("utf-8", "replace")
    return hashlib.sha256(encoded).hexdigest()


def fingerprint(
    *,
    backend: str,
    server: str,
    config: dict[str, Any],
    executable: str,
    version: str | None,
    cwd: Path | str,
    session_id: str = "",
) -> str:
    """A one-way digest of everything that could change what a server publishes.

    Command, arguments, environment, endpoint, headers, the CLI binary and its
    version, and the trusted working directory all go in; only the digest comes
    out, which is what lets a bearer token participate in cache identity without
    being retained anywhere.

    `session_id` is empty for a shareable (`public`) reading and set for a
    `private` one, which is how a server's own `cacheScope` hint is enforced
    rather than merely recorded.
    """
    return _digest(
        {
            "backend": backend,
            "server": server,
            "config": config,
            "executable": executable,
            "version": version or "",
            "cwd": os.path.normcase(str(cwd)),
            "session": session_id,
        }
    )[:32]


# ---------------------------------------------------------------------------
# Tier 1: swe-mux's own server
# ---------------------------------------------------------------------------


def mux_owned_catalog() -> McpToolCatalog:
    """The mux server's tools, read from the implementation that serves them.

    Deliberately not a second hand-maintained list: `mcp.TOOLS` is the array the
    server answers `tools/list` from, and `mcp_contract` is the closed read/write
    split those names are asserted against at import. Reading both here means a
    tool added to the server appears in this drawer with no second edit, and a
    name that somehow escaped the contract is reported rather than hidden.
    """
    from .mcp import TOOLS  # local: the daemon already holds this, tests need not

    declared = set(READ_TOOL_NAMES) | set(WRITE_TOOL_NAMES)
    tools: list[McpTool] = []
    for tool in TOOLS:
        name = str(tool.get("name") or "")
        if not name:
            continue
        description = str(tool.get("description") or "")
        annotations = tool.get("annotations")
        read_only = None
        if isinstance(annotations, dict) and "readOnlyHint" in annotations:
            read_only = bool(annotations["readOnlyHint"])
        tools.append(McpTool(name=name, description=description, read_only=read_only))
    undeclared = sorted({tool.name for tool in tools} - declared)
    diagnostic = (
        f"served but not in the closed contract: {', '.join(undeclared)}" if undeclared else ""
    )
    return McpToolCatalog(
        server="mux",
        backend="",
        evidence="swe_mux_owned",
        status="ok",
        tools=tuple(sorted(tools, key=lambda tool: tool.name)),
        note=tier_note("swe_mux_owned"),
        diagnostic=diagnostic,
        observed_at=time.time(),
        ttl_seconds=DEFAULT_TTL_SECONDS,
    )


def is_mux_server(config: dict[str, Any], mux_mcp_url: str) -> bool:
    """Whether a configured entry points at this daemon's own MCP endpoint.

    Identity by endpoint rather than by the name `mux`: a user is free to call
    their own server `mux`, and publishing swe-mux's catalog for it would be a
    confident lie. Compared after sanitization, so a differing bearer header or
    query string does not defeat the match.
    """
    configured = safe_endpoint(config.get("url"))
    ours = safe_endpoint(mux_mcp_url)
    return bool(configured) and bool(ours) and configured.casefold() == ours.casefold()


# ---------------------------------------------------------------------------
# Tier 2: the live OMP process
# ---------------------------------------------------------------------------

_MCP_TOOL_PREFIX = "mcp__"
_SANITIZE_DISALLOWED = re.compile(r"[^a-z_]+")
_SANITIZE_RUNS = re.compile(r"_+")


def sanitize_omp_name_part(value: str, fallback: str) -> str:
    """Reimplementation of OMP's `sanitizeMCPToolNamePart` (mcp/tool-bridge.ts).

    OMP mints runtime tool names as ``mcp__<sanitized server>_<sanitized tool>``
    and the extension can only report those flat names, so attributing a live
    tool back to the configured server it came from means applying the same
    transformation to the server name we already know from the passive scan.
    Prefix-matching on the raw name would miss every server whose name contains a
    digit, a dash, or a capital.
    """
    sanitized = _SANITIZE_RUNS.sub("_", _SANITIZE_DISALLOWED.sub("_", value.lower())).strip("_")
    return sanitized or fallback


def omp_live_catalog(server: str, snapshot: dict[str, Any] | None) -> McpToolCatalog:
    """Attribute the running OMP process's reported tools to one server."""
    if not isinstance(snapshot, dict) or not snapshot.get("tools"):
        return McpToolCatalog(
            server=server,
            backend="omp",
            evidence="live_process",
            status="unavailable",
            note=tier_note("live_process"),
            diagnostic=(
                "The running session has not reported a tool inventory. Sessions started "
                "before this feature shipped, or launched clean, carry no reporting extension."
            ),
            observed_at=time.time(),
        )
    prefix = f"{_MCP_TOOL_PREFIX}{sanitize_omp_name_part(server, 'server')}_"
    tools: list[McpTool] = []
    for raw in snapshot.get("tools") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "")
        if not name.startswith(prefix):
            continue
        tools.append(McpTool(name=name, description=str(raw.get("description") or "")))
    observed_at = float(snapshot.get("observed_at") or 0.0) or time.time()
    return McpToolCatalog(
        server=server,
        backend="omp",
        evidence="live_process",
        status="ok",
        tools=tuple(sorted(tools, key=lambda tool: tool.name)),
        note=tier_note("live_process"),
        diagnostic=(
            ""
            if tools
            else (
                "The session reports no tools under this server. A server that failed to "
                "connect contributes none, which is indistinguishable here from one that "
                "publishes none."
            )
        ),
        observed_at=observed_at,
        # The live process is authoritative and free to re-read; the reading is
        # replaced by the session's own next publication rather than expiring.
        ttl_seconds=DEFAULT_TTL_SECONDS,
    )


# ---------------------------------------------------------------------------
# Tier 3a: the Codex app-server sidecar
# ---------------------------------------------------------------------------


def codex_probe_argv(executable: str, args: list[str]) -> list[str]:
    """`codex app-server`, carrying forward only the argv that shapes configuration.

    `-c key=value` overrides and `--profile` decide which MCP servers a Codex
    session has, so a probe that dropped them would answer for a different
    configuration than the terminal is running. Everything else in a session's
    argv is about the interactive run and is deliberately left behind.
    """
    forwarded: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token in {"-c", "--config", "--profile", "-p"} and index + 1 < len(args):
            forwarded.extend([token, args[index + 1]])
            index += 2
            continue
        if token.startswith(("--config=", "--profile=")):
            forwarded.append(token)
        index += 1
    return [executable, "app-server", *forwarded]


class _CodexFrameTooLarge(Exception):
    """One JSON-RPC line exceeded the reader's buffer."""


async def _codex_read_response(
    process: asyncio.subprocess.Process, request_id: int
) -> dict[str, Any] | None:
    """Read framed JSON-RPC lines until the answer to `request_id` arrives.

    The app server interleaves unsolicited notifications (remote-control status,
    MCP status updates) with responses, so the loop skips anything without our id
    rather than treating the first line as the answer.
    """
    assert process.stdout is not None
    # unsupervised-loop-ok: scoped to one probe subprocess, bounded by the
    # caller's `wait_for` and by EOF, and the process is reaped in a `finally`.
    while True:
        try:
            line = await process.stdout.readline()
        except (ValueError, asyncio.LimitOverrunError) as exc:
            # asyncio's StreamReader raises rather than truncating once a line
            # exceeds its buffer, and the buffer is what CODEX_READ_LIMIT sets.
            raise _CodexFrameTooLarge(str(exc)) from exc
        if not line:
            return None
        try:
            message = json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict) and message.get("id") == request_id:
            return message


async def codex_probe(
    server: str,
    *,
    executable: str,
    args: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
) -> McpToolCatalog:
    """Ask a short-lived `codex app-server` what each configured server publishes.

    `mcpServerStatus/list` with `toolsAndAuthOnly` is the only interface that
    answers this: `codex mcp list` reports the *configuration*, which the passive
    scan already has, and misses exactly the surfaces this feature exists to show
    (`codex_apps` and account connectors).
    """
    argv = codex_probe_argv(executable, args)
    process: asyncio.subprocess.Process | None = None
    started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(cwd),
            env={**os.environ, **(env or {})} if env else None,
            limit=CODEX_READ_LIMIT,
            creationflags=background_creation_flags(),
        )
    except (OSError, ValueError) as exc:
        return _probe_failure("codex", server, f"could not start codex app-server: {exc}")

    async def exchange() -> dict[str, Any] | None:
        assert process is not None and process.stdin is not None
        process.stdin.write(
            json.dumps(
                {
                    "id": 1,
                    "method": "initialize",
                    "params": {"clientInfo": {"name": "swe-mux", "version": "1"}},
                }
            ).encode()
            + b"\n"
        )
        await process.stdin.drain()
        if await _codex_read_response(process, 1) is None:
            return None
        process.stdin.write(json.dumps({"method": "initialized"}).encode() + b"\n")
        process.stdin.write(
            json.dumps(
                {
                    "id": 2,
                    "method": "mcpServerStatus/list",
                    "params": {"detail": "toolsAndAuthOnly"},
                }
            ).encode()
            + b"\n"
        )
        await process.stdin.drain()
        return await _codex_read_response(process, 2)

    try:
        try:
            response = await asyncio.wait_for(exchange(), timeout=CODEX_PROBE_TIMEOUT)
        except TimeoutError:
            return _probe_failure(
                "codex",
                server,
                f"codex app-server did not answer within {CODEX_PROBE_TIMEOUT:.0f}s",
            )
        except _CodexFrameTooLarge:
            return _probe_failure(
                "codex",
                server,
                "codex app-server's inventory exceeded "
                f"{CODEX_READ_LIMIT // (1024 * 1024)} MiB and was not read",
            )
        except (OSError, ConnectionError) as exc:
            return _probe_failure("codex", server, f"codex app-server connection failed: {exc}")
    finally:
        if process.returncode is None:
            await reap_process_tree(process)

    if response is None:
        return _probe_failure("codex", server, "codex app-server closed without answering")
    if "error" in response:
        detail = response.get("error")
        message = detail.get("message") if isinstance(detail, dict) else str(detail)
        return _probe_failure("codex", server, f"codex app-server refused the request: {message}")

    result = response.get("result")
    entries = result.get("data") if isinstance(result, dict) else None
    if not isinstance(entries, list):
        return _probe_failure("codex", server, "codex app-server returned no server list")
    log.info(
        "codex mcp probe server=%s servers=%d duration_ms=%.0f",
        server,
        len(entries),
        (time.monotonic() - started) * 1000,
    )
    for entry in entries:
        if not isinstance(entry, dict) or str(entry.get("name") or "") != server:
            continue
        return _codex_entry_catalog(server, entry)
    return McpToolCatalog(
        server=server,
        backend="codex",
        evidence="parallel_probe",
        status="unavailable",
        note=tier_note("parallel_probe"),
        diagnostic=(
            "The probe connected but this server was not in its inventory. It may be "
            "scoped to configuration this probe does not reproduce."
        ),
        observed_at=time.time(),
    )


def _codex_entry_catalog(server: str, entry: dict[str, Any]) -> McpToolCatalog:
    raw_tools = entry.get("tools")
    tools: list[McpTool] = []
    if isinstance(raw_tools, dict):
        for name, tool in raw_tools.items():
            description = tool.get("description") if isinstance(tool, dict) else None
            tools.append(McpTool(name=str(name), description=str(description or "")))
    auth = str(entry.get("authStatus") or "unknown")
    info = entry.get("serverInfo")
    version = str(info.get("version") or "") if isinstance(info, dict) else ""
    diagnostic = ""
    status: Status = "ok"
    if auth == "notLoggedIn":
        # Reported, not hidden: Codex answered honestly that the server needs a
        # login, and a bare empty catalog would read as "no tools" instead.
        status = "auth_required"
        diagnostic = "Codex reports this server is not logged in."
    elif not tools:
        diagnostic = "The probe reached this server and it published no tools."
    return McpToolCatalog(
        server=server,
        backend="codex",
        evidence="parallel_probe",
        status=status,
        tools=tuple(sorted(tools, key=lambda tool: tool.name)),
        note=tier_note("parallel_probe"),
        diagnostic=diagnostic,
        observed_at=time.time(),
        server_version=version,
    )


# ---------------------------------------------------------------------------
# Tier 3b: dialling a Claude-configured server directly
# ---------------------------------------------------------------------------


def _transport_kind(config: dict[str, Any]) -> str:
    declared = str(config.get("type") or config.get("transport") or "").strip().casefold()
    if declared in _HTTP_TRANSPORTS or config.get("url"):
        return "http"
    if declared in _STDIO_TRANSPORTS and config.get("command"):
        return "stdio"
    return declared or "unknown"


def http_auth_required(config: dict[str, Any]) -> bool:
    """Whether an HTTP entry carries credentials, and so must not be dialled."""
    headers = config.get("headers")
    if isinstance(headers, dict) and any(
        str(key).casefold() in _AUTH_HEADERS for key in headers
    ):
        return True
    return bool(config.get("oauth") or config.get("authorization_token"))


def claude_skip_reason(config: dict[str, Any]) -> str:
    """Why this configured entry will not be dialled, or '' when it will be."""
    kind = _transport_kind(config)
    if kind == "http":
        if http_auth_required(config):
            return (
                "This server authenticates with credentials Claude holds. swe-mux does not "
                "reuse them, so its tools are not probed."
            )
        return ""
    if kind == "stdio":
        return "" if config.get("command") else "This entry names no command to run."
    return f"Unsupported transport: {kind}."


async def claude_probe(
    server: str,
    config: dict[str, Any],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> McpToolCatalog:
    """Dial a configured server with the official MCP client and list its tools.

    Claude Code exposes no headless path to the tool registry of an already
    running TUI, so this is the only evidence available - and it is strictly
    weaker than what `/mcp` shows inside that TUI, because dialling the
    configuration reaches neither account connectors nor plugin gating. The
    result is labelled `parallel_probe` for exactly that reason and must never
    be rendered as the session's live state.
    """
    skip = claude_skip_reason(config)
    if skip:
        return McpToolCatalog(
            server=server,
            backend="claude",
            evidence="parallel_probe",
            status="auth_required" if http_auth_required(config) else "unsupported",
            note=tier_note("parallel_probe"),
            diagnostic=skip,
            observed_at=time.time(),
        )
    try:
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters, stdio_client
        from mcp.client.streamable_http import streamable_http_client
    except ImportError as exc:  # pragma: no cover - dependency is declared
        return _probe_failure("claude", server, f"the MCP client is unavailable: {exc}")

    if _transport_kind(config) == "http":
        endpoint = safe_endpoint(config.get("url"))
        if not endpoint:
            return _probe_failure("claude", server, "the configured URL is not a usable endpoint")
        transport: Any = streamable_http_client(str(config.get("url")))
    else:
        command = str(config.get("command") or "")
        raw_args = config.get("args")
        raw_env = config.get("env")
        transport = stdio_client(
            StdioServerParameters(
                command=command,
                args=[str(item) for item in raw_args] if isinstance(raw_args, list) else [],
                # The server's own declared environment, layered over the
                # daemon's: a stdio server usually needs PATH and a home
                # directory to start at all.
                env={
                    **os.environ,
                    **(env or {}),
                    **(
                        {str(key): str(value) for key, value in raw_env.items()}
                        if isinstance(raw_env, dict)
                        else {}
                    ),
                },
                cwd=str(cwd),
            )
        )

    started = time.monotonic()
    try:
        catalog = await asyncio.wait_for(
            _claude_list(server, transport, Client), timeout=CLAUDE_PROBE_TIMEOUT
        )
    except TimeoutError:
        return _probe_failure(
            "claude", server, f"the server did not answer within {CLAUDE_PROBE_TIMEOUT:.0f}s"
        )
    except Exception as exc:  # noqa: BLE001 - one unreachable server must not break the drawer
        # anyio task groups surface transport failures as an ExceptionGroup, and
        # the interesting cause is the leaf rather than the wrapper.
        cause = exc.exceptions[0] if isinstance(exc, BaseExceptionGroup) else exc
        return _probe_failure("claude", server, f"{type(cause).__name__}: {cause}")
    log.info(
        "claude mcp probe server=%s tools=%d duration_ms=%.0f",
        server,
        len(catalog.tools),
        (time.monotonic() - started) * 1000,
    )
    return catalog


async def _claude_list(server: str, transport: Any, client_factory: Any) -> McpToolCatalog:
    async with client_factory(transport) as client:
        result = await client.list_tools()
        info = getattr(client, "server_info", None)
        version = str(getattr(info, "version", "") or "")
    tools = [
        McpTool(name=str(tool.name), description=str(getattr(tool, "description", "") or ""))
        for tool in result.tools
    ]
    ttl_ms = int(getattr(result, "ttl_ms", 0) or 0)
    scope = str(getattr(result, "cache_scope", "private") or "private")
    return McpToolCatalog(
        server=server,
        backend="claude",
        evidence="parallel_probe",
        status="ok",
        tools=tuple(sorted(tools, key=lambda tool: tool.name)),
        note=tier_note("parallel_probe"),
        diagnostic="" if tools else "The server connected and published no tools.",
        observed_at=time.time(),
        ttl_seconds=(
            min(ttl_ms / 1000, MAX_TTL_SECONDS) if ttl_ms > 0 else DEFAULT_TTL_SECONDS
        ),
        cache_scope="private" if scope == "private" else "public",
        server_version=version,
    )


def _probe_failure(backend: str, server: str, message: str) -> McpToolCatalog:
    log.info("mcp tool probe failed backend=%s server=%s reason=%s", backend, server, message)
    return McpToolCatalog(
        server=server,
        backend=backend,
        evidence="parallel_probe",
        status="error",
        note=tier_note("parallel_probe"),
        diagnostic=message,
        observed_at=time.time(),
        # A failure is cached briefly so a stuck server cannot be re-probed by
        # repeated clicking, but never for the success TTL.
        ttl_seconds=30.0,
    )


def unsupported_catalog(backend: str, server: str) -> McpToolCatalog:
    """Say that nothing can be asked, rather than answering with an empty list.

    The per-harness reason lives in the registry's declaration and in
    `agent-environment.md`; what has to be true *here* is only that this reads as
    "not reportable" rather than "publishes no tools".
    """
    return McpToolCatalog(
        server=server,
        backend=backend,
        evidence="not_supported",
        status="unsupported",
        note=tier_note("not_supported"),
        diagnostic=(
            f"{descriptor(require_backend(backend)).display_name} exposes no runtime tool "
            "inventory to swe-mux, so its configuration is all that can be reported here."
        ),
        observed_at=time.time(),
    )


# ---------------------------------------------------------------------------
# The cached, coalesced entry point
# ---------------------------------------------------------------------------


def _evict() -> None:
    while len(_CACHE.entries) > _MAX_CACHE_ENTRIES:
        oldest = min(_CACHE.entries, key=lambda key: _CACHE.entries[key].stored_at)
        _CACHE.entries.pop(oldest, None)


async def fetch_server_tools(
    *,
    backend: str,
    server: str,
    entry: McpServerConfig | None,
    cwd: Path,
    executable: str,
    args: list[str],
    version: str | None = None,
    mux_mcp_url: str = "",
    live_snapshot: dict[str, Any] | None = None,
    session_id: str = "",
    env: dict[str, str] | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Return one server's tool catalog, collecting it only when needed.

    Cached per config fingerprint rather than per session, so several sessions
    with the same profile share one probe. Sharing is then *withdrawn* for a
    reading the server marked `cacheScope: private`: the entry remembers which
    session it was collected for, and a different session misses and probes
    again. That check has to live on the read rather than in the key, because
    the scope is something the answer tells us and the key has to exist before
    the question is asked. Concurrent callers for one key await the same probe -
    subject to the same check on the result.
    """
    resolved: Backend = require_backend(backend)
    config = dict(entry.config) if entry else {}
    key = fingerprint(
        backend=resolved,
        server=server,
        config=config,
        executable=executable,
        version=version,
        cwd=cwd,
        session_id=session_id if _prefers_private(resolved) else "",
    )

    def shareable(catalog: McpToolCatalog, owner: str) -> bool:
        return catalog.cache_scope != "private" or owner == session_id

    now = time.monotonic()
    if not refresh:
        cached = _CACHE.entries.get(key)
        if (
            cached
            and now - cached.stored_at < cached.catalog.ttl_seconds
            and shareable(cached.catalog, cached.owner_session)
        ):
            return cached.catalog.public(fingerprint=key, cached=True)

    existing = _CACHE.inflight.get(key)
    if existing is not None:
        leader = await asyncio.shield(existing)
        if shareable(leader, _CACHE.inflight_owner.get(key, session_id)):
            return leader.public(fingerprint=key, cached=True)
        # The leader's answer turned out to be private and belongs to another
        # session; fall through and collect our own rather than borrow it.

    loop = asyncio.get_running_loop()
    future: asyncio.Future[McpToolCatalog] = loop.create_future()
    _CACHE.inflight[key] = future
    _CACHE.inflight_owner[key] = session_id
    try:
        try:
            catalog = await _collect(
                backend=resolved,
                server=server,
                config=config,
                cwd=cwd,
                executable=executable,
                args=args,
                mux_mcp_url=mux_mcp_url,
                live_snapshot=live_snapshot,
                env=env,
            )
        except Exception as exc:  # noqa: BLE001 - a probe must never break the drawer
            log.warning(
                "mcp tool fetch failed backend=%s server=%s", resolved, server, exc_info=True
            )
            catalog = _probe_failure(resolved, server, f"{type(exc).__name__}: {exc}")
        # Every waiter gets the same answer, including the failure: a coalesced
        # caller that hung because the leader raised would be worse than the
        # failure it was waiting for.
        if not future.done():
            future.set_result(catalog)
    finally:
        _CACHE.inflight.pop(key, None)
        _CACHE.inflight_owner.pop(key, None)

    _CACHE.entries[key] = _CacheEntry(
        catalog=catalog, stored_at=time.monotonic(), owner_session=session_id
    )
    _evict()
    return catalog.public(fingerprint=key, cached=False)


def _prefers_private(backend: Backend) -> bool:
    """Whether a reading for this harness is session-scoped before it is taken.

    Only OMP, whose evidence *is* one process's snapshot: sharing it under a
    config fingerprint would hand one session's live reading to another session
    with the same configuration, which is precisely the confusion the evidence
    tiers exist to prevent. Every other harness's scope is decided by the answer
    (`cacheScope`) and enforced on the read instead.
    """
    return descriptor(backend).mcp_tool_source == "live_process"


async def _collect(
    *,
    backend: Backend,
    server: str,
    config: dict[str, Any],
    cwd: Path,
    executable: str,
    args: list[str],
    mux_mcp_url: str,
    live_snapshot: dict[str, Any] | None,
    env: dict[str, str] | None,
) -> McpToolCatalog:
    if mux_mcp_url and is_mux_server(config, mux_mcp_url):
        owned = mux_owned_catalog()
        return McpToolCatalog(
            server=server,
            backend=backend,
            evidence=owned.evidence,
            status=owned.status,
            tools=owned.tools,
            note=owned.note,
            diagnostic=owned.diagnostic,
            observed_at=owned.observed_at,
            ttl_seconds=owned.ttl_seconds,
        )
    # Dispatch on the declared mechanism rather than on the harness name, so a
    # harness added to the registry without a decision is a type error here.
    source = descriptor(backend).mcp_tool_source
    if source == "none":
        return unsupported_catalog(backend, server)
    if source == "live_process":
        return omp_live_catalog(server, live_snapshot)
    if source == "app_server":
        return await codex_probe(server, executable=executable, args=args, cwd=cwd, env=env)
    if source == "client_dial":
        return await claude_probe(server, config, cwd=cwd, env=env)
    assert_never(source)


def clear_cache() -> None:
    """Drop every cached catalog. Called when configuration changed underneath."""
    _CACHE.entries.clear()


# ---------------------------------------------------------------------------
# The live-process snapshot store
# ---------------------------------------------------------------------------

MAX_SNAPSHOT_TOOLS = 512


def normalize_live_snapshot(body: Any) -> dict[str, Any]:
    """Validate and bound a runtime inventory published by an injected extension.

    Whitelisted rather than stored as received: an extension runs inside the
    user's agent and its payload is untrusted input like any other request body,
    and a tool's input schema (which is unbounded and can embed anything) has no
    place in a drawer that lists names.
    """
    if not isinstance(body, dict):
        raise ValueError("runtime inventory must be a JSON object")
    raw_tools = body.get("tools")
    if not isinstance(raw_tools, list):
        raise ValueError("runtime inventory must carry a tools array")
    tools: list[dict[str, str]] = []
    for raw in raw_tools[:MAX_SNAPSHOT_TOOLS]:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "")[:MAX_NAME_CHARS]
        if not name.startswith(_MCP_TOOL_PREFIX):
            # Only MCP-fronted tools belong here; OMP's built-ins and extension
            # tools are already covered by the documented catalog section.
            continue
        tools.append(
            {"name": name, "description": str(raw.get("description") or "")[:MAX_DESCRIPTION_CHARS]}
        )
    return {
        "tools": tools,
        "observed_at": time.time(),
        "reason": str(body.get("reason") or "")[:64],
    }


@dataclass(slots=True)
class LiveSnapshotStore:
    """The newest runtime inventory per session, in memory and bounded.

    Not persisted, and deliberately so: it describes one process generation, and
    a snapshot that outlived its process would be the exact false-liveness claim
    the evidence model forbids.
    """

    limit: int = 64
    snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)

    def put(self, session_id: str, snapshot: dict[str, Any]) -> None:
        self.snapshots.pop(session_id, None)
        self.snapshots[session_id] = snapshot
        while len(self.snapshots) > self.limit:
            self.snapshots.pop(next(iter(self.snapshots)), None)

    def get(self, session_id: str) -> dict[str, Any] | None:
        return self.snapshots.get(session_id)

    def drop(self, session_id: str) -> None:
        self.snapshots.pop(session_id, None)

    def sweep(self, live_session_ids: set[str]) -> None:
        for stale in [sid for sid in self.snapshots if sid not in live_session_ids]:
            self.snapshots.pop(stale, None)
