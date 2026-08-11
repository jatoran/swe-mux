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
and a focused device with `suppress: focused` is skipped — the foreground sound
covers it, and skipping keeps us honest with Chrome's userVisibleOnly rule (a
received-but-unshown push is penalized).

`suppress: anyDevice` extends that across devices, using the per-connection
presence in `device_presence.py`: a phone should not buzz about an approval the
user is watching happen on the desktop in front of them. That decision is a
*deferral*, not a drop, for the alerts worth chasing (`attention`, `waiting`).
Dropping them assumes the user stays put; they don't — they get up mid-turn, and
the notification they most needed is exactly the one plain suppression eats. So
the push is held briefly and then delivered anyway, unless the user interacted
with the other device *after* the alert was raised, which is direct evidence they
were there and chose not to act on it.

Routing is only half of it; the other half is whether the alert was ever true.
`waiting` ("the agent is ready for you") is raised by a *state transition*, and a
transition into `idle` is a claim about the future that the next two minutes can
falsify: the CLI's turn-end notify lands mid-turn, the PTY watchdog reads an idle
prompt during a pause, a session settles after startup. So that category is held
for `WAITING_SETTLE_SECONDS` before any routing decision, and the agent resuming
cancels it — the same cancellation the deferral path already used. Categories
that are true the instant they are raised (`attention`, `failure`) are never held.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01
from pywebpush import WebPushException, webpush

from .background_tasks import background
from .device_presence import DevicePresenceStore
from .event_bus import EventBus
from .models import MuxEvent
from .settings_store import SettingsStore, in_quiet_time

log = logging.getLogger(__name__)

PUSH_SENDER_LOOP = "push-sender"
VAPID_SUB = "mailto:swe-mux@localhost"
_MAX_SUBSCRIPTIONS = 50
_DEDUP_WINDOW = 8.0  # seconds: collapse duplicate (session, category) notifications
# A rotated endpoint can time out forever instead of returning 410; drop it after
# this many consecutive non-410 failures so it stops costing every notification.
_MAX_CONSECUTIVE_FAILURES = 5
_PRESENCE_MAX_TTL = 120.0
#: Categories worth chasing the user for, and therefore worth deferring rather than
#: dropping when another device is in use. A completion or a quota reset that the
#: user was sitting in front of is stale by the time a deferral would fire.
DEFERRABLE_CATEGORIES = frozenset({"attention", "waiting"})
#: How long a deferred notification waits for the user to deal with it elsewhere.
#: Long enough to cover answering a prompt at the desk, short enough that walking
#: away mid-turn still reaches the phone while it matters.
DEFERRAL_SECONDS = 45.0
#: Categories held briefly to see whether the agent actually stopped, before any
#: routing decision is made. Only `waiting`: it is the one alert whose whole claim
#: ("the agent wants you now") is falsified by the agent carrying on, and the one
#: that can be late without being useless. An approval is neither.
SETTLED_CATEGORIES = frozenset({"waiting"})
#: How long that hold is. Chosen from measured flap data rather than taste: on a
#: 10-hour, 17-session day, 89 of 211 idle transitions were back to `working`
#: inside 120 s with no human input in between (agent-turn-complete landing
#: mid-turn, the PTY watchdog reading an idle prompt during a pause, startup
#: settling). The 8 s dedup window caught none of them — the flaps run 11-50 s.
WAITING_SETTLE_SECONDS = 120.0
#: Annotation kinds that mean work is running right now. Duplicated from
#: `session.RUNNING_ACTIVITY_KINDS` rather than imported: this module is the
#: notification boundary and must not drag the session machinery in behind it.
#: `session.py` owns the definition; the contract test pins them equal.
RUNNING_ACTIVITY_KINDS = frozenset({"subagents", "background_tasks"})


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def running_work(payload: dict[str, Any]) -> bool:
    """Whether the agent has work in flight that will resume it without the human.

    Two sources, because they were built two months apart and both are still
    load-bearing: the derived `idle_reason` (this session's own PTY footer saying
    it is waiting on background tasks) and the standing-activity annotations
    (subagents and background tasks, counted from hooks and transcript records).
    A payload that carries neither is treated as "nothing running", which is the
    honest reading of an older event and errs toward notifying.
    """
    if payload.get("idle_reason") == "waiting_on_background":
        return True
    standing = payload.get("standing")
    if not isinstance(standing, (list, tuple)):
        return False
    return any(str(kind) in RUNNING_ACTIVITY_KINDS for kind in standing)


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
        # The telemetry store coalesces one provider rollover into a single alert, so the
        # body has to say how many accounts it covers or it reads as a single-account event.
        count = int(payload.get("count") or 1)
        if count > 1:
            provider = str(payload.get("provider") or "provider")
            return {
                "category": "reset",
                "title": "swe-mux",
                "body": f"Unexpected quota reset on {count} {provider} accounts.",
            }
        return {"category": "reset", "title": "swe-mux", "body": "Unexpected quota reset."}
    if kind == "approval_needed":
        if payload.get("kind") == "input":
            return {
                "category": "attention",
                "title": "swe-mux — question",
                "body": "The agent is waiting for your answer.",
            }
        return {
            "category": "attention",
            "title": "swe-mux — approval",
            "body": "The agent needs your approval.",
        }
    # `turn_aborted` is cancellation, including deliberate session shutdown. A
    # real provider/process failure is reported by turn_failed/session_crashed.
    if kind in ("turn_failed", "session_crashed") or (
        kind == "state_changed" and payload.get("state") == "crashed"
    ):
        return {
            "category": "failure",
            "title": "swe-mux — failed",
            "body": "The agent run failed.",
        }
    if running_work(payload):
        # The turn ended; the agent did not. It resumes itself when its subagents
        # or background tasks land, so this is not the moment worth a lock-screen
        # alert — the next completion is. Suppressing here keeps "the alert means
        # the agent is done" true instead of training the user to ignore it.
        return None
    if kind == "turn_ended":
        return {"category": "complete", "title": "swe-mux", "body": "The agent finished a turn."}
    if kind == "state_changed" and payload.get("state") == "idle":
        if payload.get("previous") == "starting":
            # A session that just booted has not finished anything and is not
            # waiting on the human for anything they asked for. Worse, startup
            # `idle` is inferred from PTY quiet and is not even input-ready: the
            # CLI still swallows a submitting CR for several seconds after it.
            return None
        return {
            "category": "waiting",
            "title": "swe-mux — ready",
            "body": "The agent is waiting for your input.",
        }
    return None


def _resolves_attention(event: MuxEvent) -> bool:
    """Whether this event means a held notification has been answered.

    Human input into the session, or the agent resuming, both mean the thing the
    alert was about is being dealt with. A deferral that fired anyway would be a
    lock-screen buzz for a question the user had already answered.
    """
    if event.type in {"terminal_input", "turn_started", "session_exited", "session_crashed"}:
        return True
    return event.type == "state_changed" and (event.payload or {}).get("state") == "working"


def notification_plan(
    settings: dict[str, Any],
    category: str,
    *,
    device_present: bool,
    other_device_active: bool,
) -> str:
    """Decide one subscription's fate: ``send``, ``skip``, or ``defer``.

    Pure so the routing rules can be tested without a push service, a socket, or a
    clock. Everything that is not an explicit reason to stay quiet ends in ``send``:
    an unreported or stale device looks absent, so the failure mode of the presence
    machinery is a redundant buzz rather than silence.
    """
    if not settings.get("enabled"):
        return "skip"
    if not settings.get("events", {}).get(category):
        return "skip"
    if in_quiet_time(settings):
        return "skip"
    suppress = str(settings.get("suppress") or "focused")
    if suppress == "never":
        return "send"
    if device_present:
        # This very device is looking at the app; its foreground sound covers it.
        return "skip"
    if suppress == "anyDevice" and other_device_active:
        return "defer" if category in DEFERRABLE_CATEGORIES else "skip"
    return "send"


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
                log.exception(
                    "stored VAPID key unreadable; regenerating "
                    "(existing subscriptions will be dropped)"
                )
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
        if (
            not isinstance(keys, dict)
            or not isinstance(keys.get("p256dh"), str)
            or not isinstance(keys.get("auth"), str)
        ):
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
        presence: DevicePresenceStore | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._store = store
        self._settings = settings
        self._events = events
        self._clock = clock
        self._presence = presence or DevicePresenceStore(clock=clock)
        self._sleep = sleep
        self._recent: dict[tuple[str, str], float] = {}
        self._failures: dict[str, int] = {}
        self._deferred: dict[tuple[str, str, str], asyncio.Task[None]] = {}
        self._settling: dict[tuple[str, str], asyncio.Task[None]] = {}

    async def run(self) -> None:
        queue = self._events.subscribe(name="push")
        try:
            while True:
                event = await queue.get()
                with background.iteration(PUSH_SENDER_LOOP):
                    await self._handle(event)
        finally:
            self._events.unsubscribe(queue)
            self.cancel_pending()

    def cancel_pending(self, session_id: str | None = None) -> int:
        """Drop everything held for a session (or all of them): settles and deferrals.

        Both maps answer the same question from different ends — "is this alert
        still true?" — so anything that resolves a session has to clear both, or a
        settled alert outlives the resume that falsified it.
        """
        return self.cancel_deferred(session_id) + self._cancel_settling(session_id)

    def cancel_deferred(self, session_id: str | None = None) -> int:
        """Drop pending deferrals, for one session or all of them."""
        keys = [
            key
            for key in self._deferred
            if session_id is None or key[0] == session_id
        ]
        for key in keys:
            self._deferred.pop(key).cancel()
        return len(keys)

    def _cancel_settling(self, session_id: str | None = None) -> int:
        keys = [key for key in self._settling if session_id is None or key[0] == session_id]
        for key in keys:
            self._settling.pop(key).cancel()
        return len(keys)

    def pending_deferrals(self) -> int:
        return len(self._deferred)

    def pending_settles(self) -> int:
        return len(self._settling)

    async def _handle(self, event: MuxEvent) -> None:
        if _resolves_attention(event):
            # The user dealt with this session (typed into it, or the agent resumed),
            # so anything held for it is answered and must not arrive late.
            self.cancel_pending(event.session_id or "")
        note = classify_notification(event)
        if not note:
            return
        if note["category"] in SETTLED_CATEGORIES:
            # Hold before deciding anything. An idle that the agent walks back is
            # indistinguishable from a real one at the moment it is raised; the
            # only thing that tells them apart is what happens next.
            self._settle(event.session_id or "", note)
            return
        await self._dispatch(event.session_id or "", note)

    def _settle(self, session_id: str, note: dict[str, str]) -> None:
        key = (session_id, note["category"])
        if key in self._settling:
            # Already waiting out this session's readiness; a repeat idle inside the
            # hold is the same claim, not a new one.
            return
        task = asyncio.create_task(self._dispatch_settled(key, note), name="push-settle")
        self._settling[key] = task

        def finished(done: asyncio.Task[None]) -> None:
            if self._settling.get(key) is done:
                del self._settling[key]
            if done.cancelled():
                return
            try:
                done.result()
            except Exception:
                log.exception("settled push dispatch failed")

        task.add_done_callback(finished)

    async def _dispatch_settled(self, key: tuple[str, str], note: dict[str, str]) -> None:
        await self._sleep(WAITING_SETTLE_SECONDS)
        # Leave the map before dispatching: past this point the alert has survived
        # the hold and is being delivered, and a cancel landing mid-`gather` would
        # tear down a send already in flight rather than preventing one.
        if self._settling.get(key) is asyncio.current_task():
            del self._settling[key]
        await self._dispatch(key[0], note)

    async def _dispatch(self, session_id: str, note: dict[str, str]) -> None:
        subs = self._store.list()
        if not subs:
            return
        now = self._clock()
        dedup_key = (session_id, note["category"])
        if self._recent.get(dedup_key, 0.0) > now - _DEDUP_WINDOW:
            return
        self._recent = {key: at for key, at in self._recent.items() if at > now - 60}
        targets = []
        for sub in subs:
            profile = str(sub.get("profile", "mobile"))
            settings = self._settings.notifications(profile)
            plan = notification_plan(
                settings,
                note["category"],
                device_present=self._store.is_present(str(sub["endpoint"]), now),
                other_device_active=self._presence.other_profile_active(profile, now),
            )
            if plan == "send":
                targets.append(sub)
            elif plan == "defer":
                self._defer(sub, note, session_id, now)
        if not targets:
            return
        # Concurrently: one stale endpoint that times out rather than returning
        # 410 otherwise delays every later endpoint by the full 10s push timeout,
        # and on a busy fleet the queue backs up until lock-screen alerts arrive
        # minutes late — the exact moment the feature exists for.
        results = await asyncio.gather(*(self._send(sub, note) for sub in targets))
        # Only a real delivery arms the dedup window. Counting a failed send as
        # delivered swallowed the follow-up event too, so a transient push-service
        # outage produced no notification at all.
        if any(results):
            self._recent[dedup_key] = now

    def _defer(
        self, sub: dict[str, Any], note: dict[str, str], session_id: str, raised_at: float
    ) -> None:
        key = (session_id, note["category"], str(sub["endpoint"]))
        if key in self._deferred:
            # Already waiting on this exact alert; a repeat is the same question.
            return
        task = asyncio.create_task(
            self._deliver_later(key, sub, note, raised_at), name="push-deferred"
        )
        self._deferred[key] = task

        def finished(done: asyncio.Task[None]) -> None:
            if self._deferred.get(key) is done:
                del self._deferred[key]
            if done.cancelled():
                return
            try:
                done.result()
            except Exception:
                log.exception("deferred push delivery failed")

        task.add_done_callback(finished)

    async def _deliver_later(
        self,
        key: tuple[str, str, str],
        sub: dict[str, Any],
        note: dict[str, str],
        raised_at: float,
    ) -> None:
        await self._sleep(DEFERRAL_SECONDS)
        profile = str(sub.get("profile", "mobile"))
        settings = self._settings.notifications(profile)
        now = self._clock()
        if not settings.get("enabled") or not settings.get("events", {}).get(note["category"]):
            return
        # Quiet hours are re-checked here, not only when the alert was raised: a
        # deferral can cross the boundary into them.
        if in_quiet_time(settings):
            return
        if settings.get("suppress") != "never" and self._store.is_present(
            str(sub["endpoint"]), now
        ):
            return
        if self._presence.interaction_since(raised_at, exclude=profile):
            # The user has touched another device since this was raised: they were
            # there, they can see it, and they did not act. Chasing them is noise.
            return
        # Nothing since the alert — they are away from the device that held it back.
        # This is the case plain suppression loses, so deliver, late but useful.
        if await self._send(sub, note):
            self._recent[(key[0], key[1])] = self._clock()

    async def _send(self, sub: dict[str, Any], note: dict[str, str]) -> bool:
        payload = json.dumps(
            {
                "title": note["title"],
                "body": note["body"],
                "type": note["category"],
                "tag": note["category"],
                "url": "/",
            }
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
                self._failures.pop(str(sub["endpoint"]), None)
                log.info("pruned expired push subscription (%s)", status)
            else:
                log.warning("web push delivery failed: %s", exc)
                self._note_failure(str(sub["endpoint"]))
            return False
        except Exception:
            log.exception("web push delivery raised")
            self._note_failure(str(sub["endpoint"]))
            return False
        self._failures.pop(str(sub["endpoint"]), None)
        return True

    def _note_failure(self, endpoint: str) -> None:
        """Quarantine an endpoint that keeps failing without ever returning 410.

        A rotated FCM endpoint can time out indefinitely instead of reporting
        itself gone, and every timeout is paid on the notification path.
        """
        count = self._failures.get(endpoint, 0) + 1
        self._failures[endpoint] = count
        if count >= _MAX_CONSECUTIVE_FAILURES:
            self._store.remove(endpoint)
            self._failures.pop(endpoint, None)
            log.warning(
                "removed push subscription after %d consecutive failures", _MAX_CONSECUTIVE_FAILURES
            )
