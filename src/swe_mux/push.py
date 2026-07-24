"""Web Push delivery for session notifications.

The frontend already fires notification *sounds* from a live tab over the events
WebSocket, but that channel dies the moment Android suspends a backgrounded tab
or the screen locks — exactly when a "needs approval" or "session stopped" alert
matters most. Web Push is the tab-independent path: the browser's push service
(FCM on Android) wakes a service worker even with no page alive.

Filtering lives here, server-side, because the decision to deliver has to be made
before any tab exists to filter it. Each subscription records which device-class
profile it belongs to, and we consult that profile's notification settings. The
one thing the server cannot know is whether a device is currently looking at the
app; the client reports that via short-TTL presence heartbeats (see PushStore),
and a focused device with suppress-when-focused set is skipped — the foreground
sound covers it, and skipping keeps us honest with Chrome's userVisibleOnly rule
(a received-but-unshown push is penalized).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01
from pywebpush import WebPushException, webpush

from .event_bus import EventBus
from .models import MuxEvent
from .settings_store import SettingsStore, in_quiet_time

log = logging.getLogger(__name__)

VAPID_SUB = "mailto:swe-mux@localhost"
_MAX_SUBSCRIPTIONS = 50
_DEDUP_WINDOW = 8.0  # seconds: collapse duplicate (session, category) notifications
_PRESENCE_MAX_TTL = 120.0


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def classify_notification(event: MuxEvent) -> dict[str, str] | None:
    """Map a live event to a notification, mirroring the frontend classifySoundEvent.

    Root-agent events only: subagent/sidechain activity and non-root scopes are
    excluded so a busy fleet does not spam the lock screen.
    """

    payload = event.payload or {}
    if payload.get("scope", "root") != "root":
        return None
    if payload.get("sidechain") is True or payload.get("subagent") is True:
        return None
    kind = event.type
    if kind == "unexpected_quota_reset":
        return {"category": "reset", "title": "swe-mux", "body": "Unexpected quota reset."}
    if kind == "approval_needed":
        if payload.get("kind") == "input":
            return {"category": "attention", "title": "swe-mux — question", "body": "The agent is waiting for your answer."}
        return {"category": "attention", "title": "swe-mux — approval", "body": "The agent needs your approval."}
    if kind in ("turn_failed", "turn_aborted", "session_crashed") or (
        kind == "state_changed" and payload.get("state") == "crashed"
    ):
        return {"category": "failure", "title": "swe-mux — failed", "body": "The agent run failed."}
    if kind == "turn_ended":
        return {"category": "complete", "title": "swe-mux", "body": "The agent finished a turn."}
    if kind == "state_changed" and payload.get("state") == "idle":
        return {"category": "waiting", "title": "swe-mux — ready", "body": "The agent is waiting for your input."}
    return None


class PushStore:
    """VAPID identity, push subscriptions, and ephemeral focus presence."""

    def __init__(self, data_dir: Path, *, clock: Callable[[], float] = time.time) -> None:
        self._pem_path = data_dir / "push-vapid.pem"
        self._subs_path = data_dir / "push-subscriptions.json"
        self._clock = clock
        self._presence: dict[str, float] = {}
        self._vapid = self._load_or_create_vapid()

    def _load_or_create_vapid(self) -> Vapid01:
        if self._pem_path.is_file():
            try:
                return Vapid01.from_pem(self._pem_path.read_bytes())
            except Exception:
                log.exception("stored VAPID key unreadable; regenerating (existing subscriptions will be dropped)")
        vapid = Vapid01()
        vapid.generate_keys()
        _atomic_write(self._pem_path, vapid.private_pem())
        return vapid

    @property
    def application_server_key(self) -> str:
        """Base64url uncompressed public point — the browser's applicationServerKey."""

        raw = self._vapid.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return _b64url(raw)

    @property
    def vapid(self) -> Vapid01:
        return self._vapid

    # --- subscriptions ---
    def _read(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self._subs_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _write(self, subs: list[dict[str, Any]]) -> None:
        _atomic_write(self._subs_path, (json.dumps(subs, indent=2) + "\n").encode("utf-8"))

    def list(self) -> list[dict[str, Any]]:
        return self._read()

    def add(self, subscription: Any, profile: str) -> None:
        if not isinstance(subscription, dict):
            raise ValueError("subscription must be an object")
        endpoint = subscription.get("endpoint")
        keys = subscription.get("keys")
        if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
            raise ValueError("subscription endpoint must be an https URL")
        if not isinstance(keys, dict) or not isinstance(keys.get("p256dh"), str) or not isinstance(keys.get("auth"), str):
            raise ValueError("subscription is missing p256dh/auth keys")
        if profile not in ("desktop", "mobile"):
            raise ValueError("profile must be desktop or mobile")
        subs = [item for item in self._read() if item.get("endpoint") != endpoint]
        subs.append(
            {
                "endpoint": endpoint,
                "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]},
                "profile": profile,
                "created_at": self._clock(),
            }
        )
        self._write(subs[-_MAX_SUBSCRIPTIONS:])

    def remove(self, endpoint: str) -> None:
        subs = self._read()
        kept = [item for item in subs if item.get("endpoint") != endpoint]
        if len(kept) != len(subs):
            self._write(kept)
        self._presence.pop(endpoint, None)

    # --- presence (ephemeral, in-memory) ---
    def set_presence(self, endpoint: str, focused: bool, ttl: float) -> None:
        if focused:
            self._presence[endpoint] = self._clock() + max(0.0, min(ttl, _PRESENCE_MAX_TTL))
        else:
            self._presence.pop(endpoint, None)

    def is_present(self, endpoint: str, now: float | None = None) -> bool:
        expiry = self._presence.get(endpoint)
        return expiry is not None and expiry > (now if now is not None else self._clock())


class PushSender:
    """Background EventBus consumer that turns live events into Web Push messages."""

    def __init__(
        self,
        store: PushStore,
        settings: SettingsStore,
        events: EventBus,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._settings = settings
        self._events = events
        self._clock = clock
        self._recent: dict[tuple[str, str], float] = {}

    async def run(self) -> None:
        queue = self._events.subscribe()
        try:
            while True:
                event = await queue.get()
                try:
                    await self._handle(event)
                except Exception:
                    log.exception("push notification handling failed")
        finally:
            self._events.unsubscribe(queue)

    async def _handle(self, event: MuxEvent) -> None:
        note = classify_notification(event)
        if not note:
            return
        subs = self._store.list()
        if not subs:
            return
        now = self._clock()
        dedup_key = (event.session_id or "", note["category"])
        if self._recent.get(dedup_key, 0.0) > now - _DEDUP_WINDOW:
            return
        self._recent = {key: at for key, at in self._recent.items() if at > now - 60}
        delivered = False
        for sub in subs:
            settings = self._settings.notifications(str(sub.get("profile", "mobile")))
            if not settings.get("enabled"):
                continue
            if not settings.get("events", {}).get(note["category"]):
                continue
            if in_quiet_time(settings):
                continue
            if settings.get("suppressWhenFocused") and self._store.is_present(str(sub["endpoint"]), now):
                continue
            await self._send(sub, note)
            delivered = True
        if delivered:
            self._recent[dedup_key] = now

    async def _send(self, sub: dict[str, Any], note: dict[str, str]) -> None:
        payload = json.dumps(
            {"title": note["title"], "body": note["body"], "type": note["category"], "tag": note["category"], "url": "/"}
        )
        try:
            await asyncio.to_thread(
                webpush,
                subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
                data=payload,
                vapid_private_key=self._store.vapid,
                vapid_claims={"sub": VAPID_SUB},
                timeout=10,
            )
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                self._store.remove(str(sub["endpoint"]))
                log.info("pruned expired push subscription (%s)", status)
            else:
                log.warning("web push delivery failed: %s", exc)
        except Exception:
            log.exception("web push delivery raised")
