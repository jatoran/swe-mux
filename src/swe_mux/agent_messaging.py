"""Phase 5: bounded agent-to-agent messages and drafted spawn requests.

Two capabilities an agent session can reach (through the Phase 4.5 MCP
transport, and only through it):

- ``notify`` — put a message into another session's queue. It is a *caller* over
  ``PromptQueueService.enqueue``, never a second delivery path
  (`CONTROL_PLANE_ROADMAP.md` §7.1): head-of-line ordering, revision checks,
  readiness, identity, and the audit trail all still belong to the queue. What
  this module adds is the relay policy the queue has no opinion about — who may
  address whom, how large, how often, how deep, and never in a cycle.
- ``request_spawn`` — write an inert draft into the observation inbox. It
  starts nothing. Approval is a separate human act (§7.2/§16): an agent that
  can create actors turns one prompt injection into unbounded fan-out, so the
  capability an agent gets is "ask the human", not "create an actor".

Caller identity is injected, never claimed (§7.4): the MCP token names the
calling session, and no signature here has a sender parameter. Every bound
below is therefore attributable and enforceable *for well-behaved callers* —
see the same-host boundary note in `design/features/agent-messaging.md` for
what that does and does not mean against a compromised same-host process.

Receiver-side policy decides how much a message is worth on arrival: by default
an agent-authored message lands as an inert ``draft`` the human arms. A session
whose operator opted in (``accept_agent_messages``) receives it ``armed``, at
which point it still waits for head-of-line order and delivery readiness like
any other queue item — it never interrupts an active turn and never bypasses an
approval or question prompt.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .config import Config
from .prompt_queue import PromptQueueService, QueueError

log = logging.getLogger("swe_mux.agent_messaging")

# A relay message that nobody delivers should not sit in a queue forever; the
# sender's context is stale long before this.
DEFAULT_MESSAGE_TTL_HOURS = 24
AGENT_SENDER_KIND = "agent"


class AgentMessagingService:
    """Relay policy around the typed queue operations."""

    def __init__(
        self,
        queue: PromptQueueService,
        sessions: Any,
        projects: Any,
        config: Config,
        auto: Any,
        *,
        append_observation: Any = None,
    ) -> None:
        self.queue = queue
        self.sessions = sessions
        self.projects = projects
        self.config = config
        self.auto = auto
        # Injected so tests do not need a project on disk, and so the module
        # never reaches into the filesystem layer directly.
        self._append_observation = append_observation

    # -- scope ----------------------------------------------------------------

    @staticmethod
    def _scope(record: Any) -> tuple[str, str]:
        return (str(record.project_id or ""), str(getattr(record, "project_scope_id", "") or ""))

    def _in_scope(self, caller: Any, record: Any) -> bool:
        """Same Project as the caller — the MCP token's read scope, reused for writes.

        Cross-project messaging does not exist: a write is at least as
        sensitive as a read, and the read scope is already the answer to "what
        may this agent see" (`CONTROL_PLANE_ROADMAP.md` §7.4).
        """
        project_id, scope_id = self._scope(caller.record)
        if project_id:
            return str(record.project_id or "") == project_id
        return bool(scope_id) and str(getattr(record, "project_scope_id", "") or "") == scope_id

    def _resolve_target(self, caller: Any, identity: str) -> Any:
        text = str(identity or "").strip()
        if not text:
            raise QueueError("unknown_target", "target session is required", status=400)
        try:
            target = self.sessions.resolve(text)
        except KeyError:
            target = None
        if target is None or not self._in_scope(caller, target.record):
            # Scope miss and true miss answer identically, exactly as the read
            # tools do: existence outside your Project is not confirmed.
            raise QueueError("unknown_target", "no such session in your Project", status=404)
        if target.record.id == caller.record.id:
            raise QueueError(
                "self_notify",
                "a session cannot notify itself; write to your own terminal instead",
                status=400,
            )
        if target.record.backend not in {"claude", "codex"}:
            raise QueueError(
                "not_agent_target",
                "messages target agent sessions only (a shell would execute a paste)",
                status=400,
            )
        if target.record.state in {"exited", "crashed"}:
            raise QueueError("target_ended", "the target session has ended")
        return target

    # -- notify ---------------------------------------------------------------

    async def notify(
        self,
        caller: Any,
        *,
        target: str,
        body: str,
        reason: str = "",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Stage a message from the calling session into ``target``'s queue."""
        if not self.config.agent_messaging_enabled:
            raise QueueError(
                "agent_messaging_disabled",
                "agent-to-agent messaging is disabled on this mux install",
                status=403,
            )
        text = str(body or "").strip()
        if not text:
            raise QueueError("invalid_body", "body must not be empty", status=400)
        limit = int(self.config.agent_message_max_chars)
        if len(text) > limit:
            raise QueueError(
                "body_too_large",
                f"an agent message may be at most {limit} characters",
                status=400,
            )
        destination = self._resolve_target(caller, target)
        target_id = str(destination.record.id)
        caller_id = str(caller.record.id)

        budget = int(self.config.agent_message_hourly_budget)
        if budget <= 0:
            raise QueueError(
                "origin_budget_exhausted",
                "this install allows no agent-authored messages",
                status=429,
            )
        used = await self.queue.store.sender_message_count(
            AGENT_SENDER_KIND, caller_id, time.time() - 3600
        )
        if used >= budget:
            raise QueueError(
                "origin_budget_exhausted",
                f"this session has staged {used} messages in the last hour (limit {budget})",
                status=429,
            )
        pending_cap = int(self.config.agent_message_pending_per_target)
        outstanding = await self.queue.store.pending_from_sender_kind(
            target_id, AGENT_SENDER_KIND
        )
        if outstanding >= pending_cap:
            raise QueueError(
                "target_backlog_full",
                f"{target_id} already has {outstanding} undelivered agent messages"
                f" (limit {pending_cap})",
                status=429,
            )

        inbound = await self.queue.store.inbound_relay_context(
            caller_id, getattr(caller.record, "agent_run_id", None)
        )
        depth = int(inbound.get("depth") or 0) + 1
        max_depth = int(self.config.agent_message_max_chain_depth)
        if depth > max_depth:
            raise QueueError(
                "chain_depth_exceeded",
                f"relay chain would be {depth} hops deep (limit {max_depth})",
                status=429,
            )
        path = [str(entry) for entry in inbound.get("path") or []]
        path.append(caller_id)
        if target_id in path:
            # Cycle detection over the recorded relay path, not a heuristic:
            # A→B→A is how a pair of agents burn a plan's worth of tokens
            # talking to each other.
            raise QueueError(
                "relay_cycle",
                "that session is already upstream in this relay chain",
                status=409,
            )

        armed = bool(await self.auto.accepts_agent_messages(target_id))
        ttl_seconds = DEFAULT_MESSAGE_TTL_HOURS * 3600
        message = await self.queue.enqueue(
            target_session_id=target_id,
            body=text,
            armed=armed,
            sender_kind=AGENT_SENDER_KIND,
            sender_id=caller_id,
            sender_label=str(getattr(caller.record, "name", "") or caller_id),
            origin_session_id=caller_id,
            correlation_id=str(correlation_id) if correlation_id else None,
            chain_depth=depth,
            origin={
                "path": path,
                "from_session": caller_id,
                "from_name": getattr(caller.record, "name", None),
                "from_run_id": getattr(caller.record, "agent_run_id", None),
                "from_backend": getattr(caller.record, "backend", None),
                "reason": str(reason or "")[:500] or None,
                "created_at": time.time(),
            },
            payload={"kind": "agent_notify", "version": 1},
            constraints={"expires_at": time.time() + ttl_seconds},
        )
        self.queue.events.emit_background(
            "queue_message_received",
            session_id=target_id,
            message_id=str(message["id"]),
            sender_kind=AGENT_SENDER_KIND,
            from_session=caller_id,
            chain_depth=depth,
        )
        return {
            "message_id": str(message["id"]),
            "state": str(message["state"]),
            "target_session_id": target_id,
            "target_name": getattr(destination.record, "name", None),
            "chain_depth": depth,
            "deduplicated": bool(message.get("deduplicated")),
            "expires_at": time.time() + ttl_seconds,
            "note": (
                "Delivered under the receiving session's queue policy: it waits for"
                " head-of-line order and delivery readiness, and never interrupts an"
                " active turn."
                if message["state"] == "armed"
                else "Staged as an inert draft; a human must arm and send it."
            ),
        }

    # -- drafted spawn --------------------------------------------------------

    async def request_spawn(
        self,
        caller: Any,
        *,
        prompt: str,
        backend: str = "",
        name: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        """Write an inert spawn request into the caller's Project inbox.

        Starts nothing, reserves nothing, and costs nothing. The human approves
        it (from the desktop or the phone) and *that* act spawns the session
        with this prompt pre-seeded.
        """
        if not self.config.request_spawn_enabled:
            raise QueueError(
                "request_spawn_disabled",
                "drafted spawn requests are disabled on this mux install",
                status=403,
            )
        if self._append_observation is None:
            raise QueueError(
                "request_spawn_unavailable",
                "the observation inbox is unavailable",
                status=503,
            )
        text = str(prompt or "").strip()
        if not text:
            raise QueueError("invalid_body", "prompt must not be empty", status=400)
        if backend and backend not in {"claude", "codex"}:
            raise QueueError("invalid_backend", "backend must be claude or codex", status=400)
        record = caller.record
        project_id = str(record.project_id or "")
        project = self.projects.projects.get(project_id) if project_id else None
        if project is None:
            raise QueueError(
                "no_project",
                "your session is not registered to a Project, so there is no inbox to draft into",
                status=409,
            )
        summary = " ".join(text.split())[:160]
        label = str(name or "").strip()[:80]
        body = (
            f"Spawn request from {getattr(record, 'name', record.id)}"
            f"{f' — {label}' if label else ''}: {summary}"
        )
        request = {
            "prompt": text,
            "backend": backend or str(record.backend or ""),
            "name": label,
            "reason": str(reason or "")[:500],
            "cwd": str(getattr(record, "cwd", "") or ""),
            "from_session": str(record.id),
            "from_name": str(getattr(record, "name", "") or ""),
            "from_run_id": str(getattr(record, "agent_run_id", "") or ""),
            "project_id": project_id,
            "status": "pending",
        }
        result = await self._append_observation(
            project.root, body, kind="spawn_request", request=request
        )
        request_id = str(result.get("appended_id") or "")
        self.queue.events.emit_background(
            "spawn_request_drafted",
            session_id=str(record.id),
            request_id=request_id,
            project_id=project_id,
            from_session=str(record.id),
        )
        return {
            "request_id": request_id,
            "project_id": project_id,
            "project_name": getattr(project, "name", None),
            "status": "drafted",
            "note": (
                "Nothing was started. This is an inert draft in the Project's"
                " observation inbox; a human decides whether it becomes a session."
            ),
        }

    # -- mailbox --------------------------------------------------------------

    async def mailbox(self, *, role: str = "all", limit: int = 100) -> dict[str, Any]:
        """Inbox/outbox view over the one message store (no second archive)."""
        if role not in {"all", "inbox", "outbox"}:
            raise QueueError("invalid_role", "role must be all, inbox, or outbox", status=400)
        messages = await self.queue.store.mailbox(role=role, limit=limit)
        for item in messages:
            target = self.sessions.sessions.get(str(item["target_session_id"]))
            item["target_live"] = bool(
                target is not None and target.record.state not in {"exited", "crashed"}
            )
            if target is not None:
                item["target_label"] = target.record.name
            origin_id = item.get("origin_session_id")
            if origin_id:
                origin = self.sessions.sessions.get(str(origin_id))
                item["origin_live"] = origin is not None
        return {"role": role, "messages": messages}
