"""Phase 5: bounded agent-to-agent messages and drafted spawn requests.

Two capabilities an agent session can reach (through the Phase 4.5 MCP
transport, and only through it):

- ``notify`` — put a message into another session's queue. It is a *caller* over
  ``PromptQueueService.enqueue``, never a second delivery path
  (`CONTROL_PLANE_ROADMAP.md` §7.1): head-of-line ordering, revision checks,
  readiness, identity, and the audit trail all still belong to the queue. What
  this module adds is the relay policy the queue has no opinion about — who may
  address whom, how large, how often, how deep, and never in a cycle.
- ``request_spawn`` - write an inert request surfaced in Fleet Queue. It
  starts nothing. Approval is a separate human act (§7.2/§16): an agent that
  can create actors turns one prompt injection into unbounded fan-out, so the
  capability an agent gets is "ask the human", not "create an actor".

Caller identity is injected, never claimed (§7.4): the MCP token names the
calling session, and no signature here has a sender parameter. Every bound
below is therefore attributable and enforceable *for well-behaved callers* —
see the same-host boundary note in `design/features/agent-messaging.md` for
what that does and does not mean against a compromised same-host process.

Receiver-side policy decides how much a message is worth on arrival. A live
agent conversation accepts agent-authored messages ``armed`` by default, as part
of the per-run grant in ``auto_delivery.py`` — armed still waits for
head-of-line order and delivery readiness like any other queue item, so it never
interrupts an active turn and never bypasses an approval or question prompt.
A session whose operator turned ``accept_agent_messages`` off for that run
receives an inert ``draft`` instead, which only a human can arm.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .config import Config
from .git_projects import ProjectIdentity
from .harness import is_agent_harness
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
        read_observations: Any = None,
    ) -> None:
        self.queue = queue
        self.sessions = sessions
        self.projects = projects
        self.config = config
        self.auto = auto
        # Injected so tests do not need a project on disk, and so the module
        # never reaches into the filesystem layer directly.
        self._append_observation = append_observation
        self._read_observations = read_observations

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
        if not is_agent_harness(target.record.backend):
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
                "The matching message was deleted by the receiving operator and was not recreated."
                if message["state"] == "deleted"
                else
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
        """Write an inert spawn request for review in the Fleet Queue.

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
                "spawn request storage is unavailable",
                status=503,
            )
        text = str(prompt or "").strip()
        if not text:
            raise QueueError("invalid_body", "prompt must not be empty", status=400)
        if backend and not is_agent_harness(backend):
            raise QueueError(
                "invalid_backend", "backend must be a registered agent", status=400
            )
        record = caller.record
        project_id = str(record.project_id or "")
        project = self.projects.projects.get(project_id) if project_id else None
        if project is None:
            raise QueueError(
                "no_project",
                "your session is not registered to a Project",
                status=409,
            )
        summary = " ".join(text.split())[:160]
        label = str(name or "").strip()[:80]
        body = (
            f"Spawn request from {getattr(record, 'name', record.id)}"
            f"{f' - {label}' if label else ''}: {summary}"
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
                "Nothing was started. This is an inert Fleet Queue approval row; "
                "a human decides whether it becomes a session."
            ),
        }

    async def _project_spawn_requests(self, project: Any) -> list[dict[str, Any]]:
        if self._read_observations is None:
            raise QueueError(
                "spawn_requests_unavailable",
                "spawn request storage is unavailable",
                status=503,
            )
        identity = ProjectIdentity(project.id, project.name, project.root, "registered")
        result = await self._read_observations(project.root, project=identity)
        if result.get("status") == "malformed":
            raise QueueError(
                "spawn_requests_unavailable",
                str(result.get("error") or "spawn request storage is unreadable"),
                status=409,
            )
        requests: list[dict[str, Any]] = []
        for item in result.get("observations") or []:
            if item.get("kind") != "spawn_request":
                continue
            payload = dict(item.get("request") or {})
            requests.append(
                {
                    "id": str(item.get("id") or ""),
                    "project_id": project.id,
                    "project_name": project.name,
                    "created_at": float(item.get("created_at") or 0),
                    "done": bool(item.get("done")),
                    "status": str(payload.get("status") or "pending"),
                    "prompt": str(payload.get("prompt") or ""),
                    "backend": str(payload.get("backend") or ""),
                    "name": str(payload.get("name") or ""),
                    "reason": str(payload.get("reason") or ""),
                    "from_session": str(payload.get("from_session") or ""),
                    "from_name": str(payload.get("from_name") or ""),
                    "from_run_id": str(payload.get("from_run_id") or ""),
                    "session_id": str(payload.get("session_id") or "") or None,
                    "decided_by": str(payload.get("decided_by") or "") or None,
                }
            )
        return requests

    async def spawn_requests(self, caller: Any) -> dict[str, Any]:
        """Read only requests attributed to the calling terminal session."""
        project_id = str(caller.record.project_id or "")
        project = self.projects.projects.get(project_id) if project_id else None
        if project is None:
            raise QueueError(
                "no_project", "your session is not registered to a Project", status=409
            )
        caller_id = str(caller.record.id)
        requests = [
            item
            for item in await self._project_spawn_requests(project)
            if item["from_session"] == caller_id
        ]
        requests.sort(key=lambda item: item["created_at"], reverse=True)
        return {
            "project_id": project.id,
            "project_name": project.name,
            "requests": requests,
        }

    async def message_status(self, caller: Any, message_id: str) -> dict[str, Any]:
        """Current outcome of one notify, visible only to its attributed sender."""
        message = await self.queue.store.message(str(message_id or ""))
        caller_id = str(caller.record.id)
        project_id = str(caller.record.project_id or "")
        if (
            message is None
            or message.get("sender_kind") != AGENT_SENDER_KIND
            or str(message.get("sender_id") or "") != caller_id
            or (project_id and str(message.get("project_id") or "") != project_id)
        ):
            raise QueueError(
                "unknown_message", "no such message sent by your session", status=404
            )
        raw_state = str(message.get("state") or "")
        expires_at = (message.get("constraints") or {}).get("expires_at")
        expired = str(message.get("cancel_kind") or "") == "expired" or bool(
            raw_state in {"draft", "armed", "blocked"}
            and isinstance(expires_at, int | float)
            and time.time() >= float(expires_at)
        )
        if expired:
            status = "expired"
        else:
            status = {
                "draft": "drafted",
                "armed": "armed",
                "delivering": "armed",
                "sent": "delivered",
                "stranded": "stranded",
                "blocked": "refused",
                "failed": "refused",
                "cancelled": "refused",
                "deleted": "refused",
            }.get(raw_state, "refused")
        return {
            "message_id": str(message["id"]),
            "status": status,
            "queue_state": raw_state,
            "target_session_id": str(message.get("target_session_id") or ""),
            "target_name": message.get("target_label"),
            "created_at": message.get("created_at"),
            "updated_at": message.get("updated_at"),
            "sent_at": message.get("sent_at"),
            "expires_at": expires_at,
            "blocked_reasons": message.get("blocked_reasons") or [],
            "stranded_reason": message.get("stranded_reason"),
            "cancel_kind": message.get("cancel_kind"),
        }

    # -- mailbox --------------------------------------------------------------

    async def mailbox(
        self,
        *,
        author: str = "all",
        role: str | None = None,
        project_id: str | None = None,
        target_session_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Application-wide authorship view over the one message store."""
        if role is not None:
            if role not in {"all", "inbox", "outbox"}:
                raise QueueError("invalid_role", "role must be all, inbox, or outbox", status=400)
            author = {"inbox": "non_human", "outbox": "human"}.get(role, role)
        if author not in {"all", "human", "non_human"}:
            raise QueueError(
                "invalid_author",
                "author must be all, human, or non_human",
                status=400,
            )
        messages = await self.queue.store.mailbox(
            author=author,
            project_id=project_id,
            target_session_id=target_session_id,
            limit=limit,
        )
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
        spawn_requests: list[dict[str, Any]] = []
        spawn_request_errors: list[dict[str, str]] = []
        if author != "human" and target_session_id is None:
            selected = [
                project
                for project in self.projects.projects.values()
                if not project_id or project.id == project_id
            ]
            for project in selected:
                try:
                    spawn_requests.extend(await self._project_spawn_requests(project))
                except QueueError as exc:
                    log.warning(
                        "spawn requests unavailable project_id=%s code=%s",
                        project.id,
                        exc.code,
                    )
                    spawn_request_errors.append(
                        {"project_id": project.id, "error": exc.code}
                    )
            spawn_requests.sort(key=lambda item: item["created_at"], reverse=True)
            spawn_requests = spawn_requests[: max(1, min(limit, 500))]
        return {
            "author": author,
            "messages": messages,
            "spawn_requests": spawn_requests,
            "spawn_request_errors": spawn_request_errors,
            "targets": await self.queue.store.mailbox_targets(),
        }
