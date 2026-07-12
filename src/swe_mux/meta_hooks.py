from __future__ import annotations

import asyncio
import fnmatch
import logging
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import ClientSession

from .event_bus import EventBus
from .models import MuxEvent
from .session import SessionManager

log = logging.getLogger(__name__)


@dataclass(slots=True)
class HookRule:
    match: dict[str, str]
    action: dict[str, Any]
    rate_limit_seconds: float = 2.0
    last_run: float = 0.0


class MetaHookEngine:
    def __init__(self, path: Path, events: EventBus, sessions: SessionManager) -> None:
        self.path, self.events, self.sessions = path, events, sessions
        self.rules: list[HookRule] = []
        self.notifications: list[dict[str, Any]] = []
        self._mtime = 0.0
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="meta-hooks")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def _reload(self) -> None:
        if not self.path.exists():
            self.rules = []
            return
        mtime = self.path.stat().st_mtime
        if mtime == self._mtime:
            return
        raw = tomllib.loads(self.path.read_text(encoding="utf-8"))
        self.rules = [
            HookRule(
                item.get("match", {}),
                item.get("action", {}),
                float(item.get("rate_limit_seconds", 2)),
            )
            for item in raw.get("hook", [])
        ]
        self._mtime = mtime

    async def _run(self) -> None:
        queue = self.events.subscribe()
        try:
            while True:
                self._reload()
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=2)
                except TimeoutError:
                    continue
                await self._handle(event)
        finally:
            self.events.unsubscribe(queue)

    def _matches(self, rule: HookRule, event: MuxEvent) -> bool:
        session = self.sessions.sessions.get(event.session_id or "")
        fields: dict[str, Any] = {
            "type": event.type,
            "source": event.source,
            "session_id": event.session_id,
            "backend": session.record.backend if session else None,
            "session_name": session.record.name if session else None,
            **event.payload,
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
            except Exception:
                log.exception("meta-hook action failed")

    async def _act(self, action: dict[str, Any], event: MuxEvent) -> None:
        kind = action.get("kind")
        session = self.sessions.sessions.get(event.session_id or "")
        values = {
            "session_id": event.session_id or "",
            "session_name": session.record.name if session else "",
            "type": event.type,
        }
        if kind == "notify":
            item = {"ts": event.ts, "channel": action.get("channel", "ui"), **values}
            self.notifications.append(item)
            self.notifications[:] = self.notifications[-100:]
            await self.events.emit("notification", session_id=event.session_id, **item)
        elif kind == "write_pty":
            target = str(action.get("session", event.session_id or ""))
            text = str(action.get("text", "")).format_map(values)
            self.sessions.resolve(target).pty.write(text)
        elif kind == "run":
            command = str(action["command"]).format_map(values)
            await asyncio.create_subprocess_exec(
                "powershell.exe",
                "-NoProfile",
                "-Command",
                command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        elif kind == "http":
            async with ClientSession() as client:
                await client.post(str(action["url"]), json=event.snapshot())
