from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import logging
import os
import string
import time
import tomllib
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aiohttp import ClientSession, ClientTimeout

from .event_bus import EventBus
from .models import MuxEvent
from .session import SessionManager
from .subprocess_flags import background_creation_flags, reap_process_tree

log = logging.getLogger(__name__)

MAX_HOOK_FILE_BYTES = 256 * 1024
MAX_ACTION_TEXT_BYTES = 64 * 1024
MAX_HTTP_BODY_BYTES = 256 * 1024
MAX_ACTION_TIMEOUT_SECONDS = 60.0
MAX_HTTP_RETRIES = 3
TEMPLATE_FIELDS = {"session_id", "session_name", "type", "source", "seq"}


class HookConfigError(ValueError):
    pass


@dataclass(slots=True)
class HookRule:
    match: dict[str, str]
    action: dict[str, Any]
    rate_limit_seconds: float = 2.0
    last_run: float = 0.0


@dataclass(slots=True)
class DeliveryRecord:
    id: str
    correlation_id: str
    created_at: float
    updated_at: float
    direction: str
    provider: str
    channel: str
    status: str
    attempts: int
    session_id: str | None
    sender: str
    reply_target: str | None
    event_type: str
    error: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


def _validate_template(value: str, field: str) -> None:
    if len(value.encode("utf-8")) > MAX_ACTION_TEXT_BYTES:
        raise HookConfigError(f"{field} exceeds {MAX_ACTION_TEXT_BYTES} bytes")
    try:
        parsed = string.Formatter().parse(value)
        fields = [name for _, name, _, _ in parsed if name]
    except ValueError as exc:
        raise HookConfigError(f"{field} has an invalid template: {exc}") from exc
    invalid = [name for name in fields if name not in TEMPLATE_FIELDS]
    if invalid:
        raise HookConfigError(f"{field} uses unsupported template variable: {invalid[0]}")


def parse_hook_rules(text: str) -> list[HookRule]:
    if len(text.encode("utf-8")) > MAX_HOOK_FILE_BYTES:
        raise HookConfigError(f"hook file exceeds {MAX_HOOK_FILE_BYTES} bytes")
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise HookConfigError(f"invalid hooks TOML: {exc}") from exc
    supplied = raw.get("hook", [])
    if not isinstance(supplied, list):
        raise HookConfigError("hook must be an array of tables")
    rules: list[HookRule] = []
    for index, item in enumerate(supplied):
        if not isinstance(item, dict):
            raise HookConfigError(f"hook[{index}] must be a table")
        match = item.get("match", {})
        action = item.get("action", {})
        if not isinstance(match, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in match.items()
        ):
            raise HookConfigError(f"hook[{index}].match must contain string patterns")
        if not isinstance(action, dict):
            raise HookConfigError(f"hook[{index}].action must be a table")
        kind = action.get("kind")
        if kind not in {"notify", "write_pty", "run", "http"}:
            raise HookConfigError(f"hook[{index}].action.kind is unsupported")
        if kind == "write_pty":
            _validate_template(str(action.get("text", "")), f"hook[{index}].action.text")
        elif kind == "run":
            argv = action.get("argv")
            command = action.get("command")
            if argv is not None and (
                not isinstance(argv, list)
                or not argv
                or not all(isinstance(part, str) and part for part in argv)
            ):
                raise HookConfigError(f"hook[{index}].action.argv must be non-empty strings")
            if argv is None and not isinstance(command, str):
                raise HookConfigError(f"hook[{index}].action requires argv or command")
            for part in argv or [command]:
                _validate_template(str(part), f"hook[{index}].action command")
            timeout = float(action.get("timeout_seconds", 10))
            if not 0.1 <= timeout <= MAX_ACTION_TIMEOUT_SECONDS:
                raise HookConfigError(f"hook[{index}].action timeout is out of range")
        elif kind == "http":
            url = str(action.get("url", ""))
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise HookConfigError(f"hook[{index}].action.url must be HTTP(S)")
            if len(url) > 2048:
                raise HookConfigError(f"hook[{index}].action.url is too long")
            retries = int(action.get("retries", 1))
            if not 1 <= retries <= MAX_HTTP_RETRIES:
                raise HookConfigError(f"hook[{index}].action.retries is out of range")
        rate_limit = float(item.get("rate_limit_seconds", 2))
        if rate_limit < 0.1:
            raise HookConfigError(f"hook[{index}].rate_limit_seconds must be at least 0.1")
        rules.append(HookRule(dict(match), dict(action), rate_limit))
    return rules


class MetaHookEngine:
    def __init__(self, path: Path, events: EventBus, sessions: SessionManager) -> None:
        self.path, self.events, self.sessions = path, events, sessions
        self.rules: list[HookRule] = []
        self.notifications: list[dict[str, Any]] = []
        self.deliveries: list[DeliveryRecord] = []
        self.diagnostic: dict[str, Any] = {"status": "not_loaded", "error": None}
        self._fingerprint: str | None = None
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="meta-hooks")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _reload(self) -> None:
        if not self.path.exists():
            if self._fingerprint is not None:
                self.rules = []
                self._fingerprint = None
            self.diagnostic = {"status": "missing", "error": None, "rules": 0}
            return
        try:
            data = self.path.read_bytes()
        except OSError as exc:
            self.diagnostic = {
                "status": "invalid",
                "error": str(exc),
                "rules": len(self.rules),
                "retained_last_known_good": True,
            }
            await self.events.emit("hook_reload_failed", source="hooks", error=str(exc))
            return
        fingerprint = hashlib.sha256(data).hexdigest()
        if fingerprint == self._fingerprint:
            return
        try:
            rules = parse_hook_rules(data.decode("utf-8"))
        except (UnicodeError, HookConfigError, ValueError) as exc:
            self._fingerprint = fingerprint
            self.diagnostic = {
                "status": "invalid",
                "error": str(exc),
                "rules": len(self.rules),
                "retained_last_known_good": True,
            }
            await self.events.emit("hook_reload_failed", source="hooks", error=str(exc))
            return
        self.rules = rules
        self._fingerprint = fingerprint
        self.diagnostic = {"status": "ready", "error": None, "rules": len(rules)}
        await self.events.emit("hook_rules_reloaded", source="hooks", rules=len(rules))

    async def _run(self) -> None:
        queue = self.events.subscribe()
        try:
            while True:
                await self._reload()
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=2)
                except TimeoutError:
                    continue
                await self._handle(event)
        finally:
            self.events.unsubscribe(queue)

    def _matches(self, rule: HookRule, event: MuxEvent) -> bool:
        session = self.sessions.sessions.get(event.session_id or "")
        record = session.record if session else None
        run_id = getattr(record, "agent_run_id", None)
        trusted_scope = (
            getattr(record, "run_project_scope_id", None)
            if run_id
            else getattr(record, "spawn_project_scope_id", None)
            or getattr(record, "project_scope_id", None)
        )
        fields: dict[str, Any] = {
            **event.payload,
            "type": event.type,
            "source": event.source,
            "session_id": event.session_id,
            "backend": session.record.backend if session else None,
            "session_name": session.record.name if session else None,
            "project_scope_id": trusted_scope,
            "repo_group_id": (
                getattr(record, "run_repo_group_id", None)
                if run_id
                else getattr(record, "spawn_repo_group_id", None)
                or getattr(record, "repo_group_id", None)
            ),
        }
        return all(
            fnmatch.fnmatch(str(fields.get(key, "")), pattern)
            for key, pattern in rule.match.items()
        )

    async def _handle(self, event: MuxEvent) -> None:
        now = time.monotonic()
        for rule in self.rules:
            if not self._matches(rule, event) or now - rule.last_run < rule.rate_limit_seconds:
                continue
            rule.last_run = now
            try:
                await self._act(rule.action, event)
            except Exception as exc:
                log.exception("meta-hook action failed")
                await self.events.emit(
                    "hook_action_failed",
                    session_id=event.session_id,
                    source="hooks",
                    action=rule.action.get("kind"),
                    error=str(exc),
                )

    def _values(self, event: MuxEvent) -> dict[str, Any]:
        session = self.sessions.sessions.get(event.session_id or "")
        record = session.record if session else None
        run_id = getattr(record, "agent_run_id", None)
        return {
            "session_id": event.session_id or "",
            "session_name": session.record.name if session else "",
            "type": event.type,
            "source": event.source,
            "seq": event.seq,
            "project_scope_id": (
                getattr(record, "run_project_scope_id", None)
                if run_id
                else getattr(record, "spawn_project_scope_id", None)
                or getattr(record, "project_scope_id", "")
            ),
            "repo_group_id": (
                getattr(record, "run_repo_group_id", None)
                if run_id
                else getattr(record, "spawn_repo_group_id", None)
                or getattr(record, "repo_group_id", "")
            ),
        }

    def _new_delivery(
        self, action: dict[str, Any], event: MuxEvent, provider: str
    ) -> DeliveryRecord:
        now = time.time()
        record = DeliveryRecord(
            id=str(uuid.uuid4()),
            correlation_id=str(action.get("correlation_id") or event.seq or uuid.uuid4()),
            created_at=now,
            updated_at=now,
            direction="outbound",
            provider=provider,
            channel=str(action.get("channel") or provider),
            status="pending",
            attempts=0,
            session_id=event.session_id,
            sender=str(action.get("sender") or "swe-mux"),
            reply_target=str(action["reply_target"]) if action.get("reply_target") else None,
            event_type=event.type,
        )
        self.deliveries.append(record)
        self.deliveries[:] = self.deliveries[-500:]
        return record

    async def _act(self, action: dict[str, Any], event: MuxEvent) -> None:
        kind = action.get("kind")
        values = self._values(event)
        if kind == "notify":
            delivery = self._new_delivery(action, event, str(action.get("provider") or "ui"))
            item = {
                "ts": event.ts,
                "channel": action.get("channel", "ui"),
                "delivery_id": delivery.id,
                **values,
            }
            self.notifications.append(item)
            self.notifications[:] = self.notifications[-100:]
            delivery.attempts = 1
            delivery.status = "delivered"
            delivery.updated_at = time.time()
            await self.events.emit(
                "notification",
                session_id=event.session_id,
                source="hooks",
                channel=item["channel"],
                delivery_id=delivery.id,
                triggering_event=event.type,
                session_name=values["session_name"],
            )
        elif kind == "write_pty":
            target = str(action.get("session", event.session_id or ""))
            text = str(action.get("text", "")).format_map(values)
            self.sessions.resolve(target).pty.write(text)
        elif kind == "run":
            await self._run_process(action, values, event)
        elif kind == "http":
            await self._post_http(action, event)

    async def _run_process(
        self, action: dict[str, Any], values: dict[str, Any], event: MuxEvent
    ) -> None:
        session = self.sessions.sessions.get(event.session_id or "")
        record = session.record if session else None
        cwd = (
            getattr(record, "run_cwd", None)
            if getattr(record, "agent_run_id", None)
            else getattr(record, "spawn_cwd", None)
        ) or getattr(record, "cwd", None)
        argv = action.get("argv")
        if isinstance(argv, list):
            command = [str(part).format_map(values) for part in argv]
        else:
            text = str(action["command"]).format_map(values)
            shell = str(action.get("shell") or ("powershell" if os.name == "nt" else "sh"))
            shells = {
                "powershell": ["powershell.exe", "-NoProfile", "-Command"],
                "pwsh": ["pwsh", "-NoProfile", "-Command"],
                "cmd": ["cmd.exe", "/d", "/s", "/c"],
                "sh": ["/bin/sh", "-c"],
            }
            if shell not in shells:
                raise HookConfigError(f"unsupported run shell: {shell}")
            command = [*shells[shell], text]
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=background_creation_flags(),
        )
        try:
            code = await asyncio.wait_for(
                process.wait(), timeout=float(action.get("timeout_seconds", 10))
            )
        except TimeoutError:
            await reap_process_tree(process)
            raise TimeoutError("hook subprocess timed out") from None
        if code:
            raise RuntimeError(f"hook subprocess exited with status {code}")

    async def _post_http(self, action: dict[str, Any], event: MuxEvent) -> None:
        payload = event.snapshot()
        if len(json.dumps(payload).encode("utf-8")) > MAX_HTTP_BODY_BYTES:
            raise HookConfigError("hook HTTP body exceeds configured limit")
        delivery = self._new_delivery(action, event, "http")
        retries = int(action.get("retries", 1))
        timeout = min(float(action.get("timeout_seconds", 10)), MAX_ACTION_TIMEOUT_SECONDS)
        try:
            async with ClientSession(timeout=ClientTimeout(total=timeout)) as client:
                for attempt in range(1, retries + 1):
                    delivery.attempts = attempt
                    delivery.updated_at = time.time()
                    try:
                        async with client.post(str(action["url"]), json=payload) as response:
                            if 200 <= response.status < 300:
                                delivery.status = "delivered"
                                return
                            delivery.error = f"HTTP {response.status}"
                            if response.status < 500 and response.status != 429:
                                break
                    except (OSError, TimeoutError) as exc:
                        delivery.error = str(exc)
                    if attempt < retries:
                        delivery.status = "retrying"
                        await asyncio.sleep(min(0.25 * (2 ** (attempt - 1)), 2.0))
        finally:
            delivery.updated_at = time.time()
        delivery.status = "failed"
        raise RuntimeError(delivery.error or "hook HTTP delivery failed")
