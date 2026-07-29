"""Which device the human is actually at, so notifications can be routed around it.

The push sender already knew whether the *notified* device was looking at the app
(`PushStore` presence, keyed by push endpoint). It could not know anything about the
other device, so a phone kept buzzing for approvals the user was watching happen on
the desktop three feet away.

Push presence cannot answer that question either, for a structural reason: it is only
reported by devices that hold a push subscription (`push.ts` bails without one), and
the Windows desktop shell is a WebView that cannot subscribe at all. A cross-device
rule built on it would ship and do nothing.

So presence is reported over the `/events` websocket, which every client already
holds regardless of push support, and the connection's lifetime *is* the presence's
lifetime. Two things are recorded per connection:

* `visible`/`focused` — is this window actually on screen and frontmost.
* the age of the last real interaction (pointer or key), measured by the client and
  sent as an age rather than a timestamp so a phone's clock skew cannot make a device
  look permanently active.

Both are required to call a device *active*. Focus alone is not presence: a desktop
left focused while its owner walks away looks identical to one being typed into, and
treating that as presence is how you silence the device the user is carrying. Every
staleness path fails open — an unknown, expired or half-reported device is treated as
absent, because a redundant notification is a much cheaper mistake than a missing one.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: How long after a real interaction a device still counts as in use.
ACTIVITY_WINDOW_SECONDS = 120.0
#: A heartbeat older than this is treated as gone: clients report on an interval and
#: on every visibility/focus change, so silence means the tab died or was frozen.
HEARTBEAT_TTL_SECONDS = 90.0
#: Defensive bound on a client-reported interaction age.
_MAX_INTERACTION_AGE = 3600.0


@dataclass(frozen=True)
class DeviceReport:
    """One client's account of itself. Ages are seconds, never timestamps."""

    profile: str
    visible: bool
    focused: bool
    interaction_age: float | None


@dataclass(frozen=True)
class DevicePresence:
    profile: str
    visible: bool
    focused: bool
    last_interaction_at: float | None
    updated_at: float


def parse_device_report(frame: dict[str, Any]) -> DeviceReport | None:
    """Read a `presence` frame from a client, or None when it is unusable."""
    profile = str(frame.get("profile") or "")
    if profile not in {"desktop", "mobile"}:
        return None
    raw_age = frame.get("interaction_age")
    age: float | None = None
    if isinstance(raw_age, (int, float)) and not isinstance(raw_age, bool):
        age = min(max(float(raw_age), 0.0), _MAX_INTERACTION_AGE)
    return DeviceReport(
        profile=profile,
        visible=bool(frame.get("visible")),
        focused=bool(frame.get("focused")),
        interaction_age=age,
    )


class DevicePresenceStore:
    """Live presence per client connection, aggregated to device classes."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        activity_window: float = ACTIVITY_WINDOW_SECONDS,
        ttl: float = HEARTBEAT_TTL_SECONDS,
    ) -> None:
        self._clock = clock
        self._activity_window = activity_window
        self._ttl = ttl
        self._devices: dict[str, DevicePresence] = {}

    def report(self, connection_id: str, report: DeviceReport) -> None:
        now = self._clock()
        self._devices[connection_id] = DevicePresence(
            profile=report.profile,
            visible=report.visible,
            focused=report.focused,
            last_interaction_at=(
                None if report.interaction_age is None else now - report.interaction_age
            ),
            updated_at=now,
        )

    def drop(self, connection_id: str) -> None:
        """A closed socket is a device that is definitively not being looked at."""
        self._devices.pop(connection_id, None)

    def _live(self, now: float) -> list[DevicePresence]:
        fresh = [
            device for device in self._devices.values() if now - device.updated_at <= self._ttl
        ]
        return fresh

    def _is_active(self, device: DevicePresence, now: float) -> bool:
        if not (device.visible and device.focused):
            return False
        if device.last_interaction_at is None:
            return False
        return now - device.last_interaction_at <= self._activity_window

    def active_profiles(self, now: float | None = None) -> set[str]:
        moment = self._clock() if now is None else now
        return {
            device.profile for device in self._live(moment) if self._is_active(device, moment)
        }

    def other_profile_active(self, profile: str, now: float | None = None) -> bool:
        """Is a device class *other than* `profile` in use right now."""
        return bool(self.active_profiles(now) - {profile})

    def interaction_since(self, moment: float, *, exclude: str | None = None) -> bool:
        """Did any device other than `exclude` register a human interaction after `moment`.

        This is what decides a deferred notification: it is direct evidence that the
        user was present somewhere else while the alert was pending and did not act on
        it, rather than an inference from a window that merely stayed focused.
        """
        now = self._clock()
        for device in self._live(now):
            if exclude is not None and device.profile == exclude:
                continue
            if device.last_interaction_at is not None and device.last_interaction_at > moment:
                return True
        return False

    def snapshot(self) -> dict[str, Any]:
        """Diagnostic view: why a notification was (or was not) held back."""
        now = self._clock()
        return {
            "now": now,
            "active_profiles": sorted(self.active_profiles(now)),
            "devices": [
                {
                    "profile": device.profile,
                    "visible": device.visible,
                    "focused": device.focused,
                    "interaction_age": (
                        None
                        if device.last_interaction_at is None
                        else round(now - device.last_interaction_at, 1)
                    ),
                    "heartbeat_age": round(now - device.updated_at, 1),
                    "active": self._is_active(device, now),
                }
                for device in self._live(now)
            ],
        }
