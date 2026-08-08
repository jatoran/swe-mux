"""Server-persisted UI settings, split into per-device-class profiles.

swe-mux is single-user, so settings are global rather than per-account, but the
same install is driven from both a desktop browser and an Android phone, which
want different sound/notification behaviour. Rather than let each device keep its
own localStorage (unshareable, invisible to the push sender that runs on the
server), settings live here keyed by a device *class* — ``desktop`` or
``mobile`` — and every device can read and edit either profile.

The ``sounds``, ``commandRail``, ``fileTree`` and ``drawerTabs`` domains are stored
opaquely: the browser owns their schema and normalization (custom sound data URLs,
per-event sound ids, the file-tree's ``{projectId: expandedPaths[]}`` blob, the
utility drawer's ``{order: tabId[]}``). The ``alerts`` and ``notifications`` domains
are interpreted server-side because the Web Push sender must apply the shared
master, quiet hours, and push-channel policy before any tab is alive to filter it.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

SETTINGS_SCHEMA_VERSION = 1
PROFILES: tuple[str, ...] = ("desktop", "mobile")
DOMAINS: tuple[str, ...] = (
    "alerts",
    "sounds",
    "notifications",
    "commandRail",
    "fileTree",
    "drawerTabs",
)
NOTIFICATION_EVENTS: tuple[str, ...] = ("complete", "waiting", "attention", "failure", "reset")
#: Which presence silences a profile's push. See ``default_notifications``.
SUPPRESS_MODES: tuple[str, ...] = ("never", "focused", "anyDevice")
# Sound customization allows a base64 data URL up to 512 KiB, so a domain blob
# can be large; bound it generously to reject only runaway or hostile payloads.
_MAX_DOMAIN_BYTES = 1024 * 1024


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def default_notifications(profile: str = "mobile") -> dict[str, Any]:
    """Fresh-profile notification defaults.

    Only "the agent stopped and is waiting for you" (``waiting``) and "the agent
    needs approval or asked a question" (``attention``) notify by default;
    per-turn completions and failures stay quiet until the user opts in.

    ``suppress`` decides which presence silences this profile:

    * ``never`` — always deliver.
    * ``focused`` — skip while *this* device is looking at the app; its foreground
      sound already covers that case.
    * ``anyDevice`` — also skip (or defer) while the user is demonstrably working on
      another device. The default for the phone, which is otherwise notified about
      approvals the user is watching happen on the desktop in front of them.

    The desktop defaults to ``focused``: it is the device the user works at, so
    "someone is active elsewhere" is not a reason to keep it quiet.
    """

    return {
        "enabled": True,
        "events": {
            "complete": False,
            "waiting": True,
            "attention": True,
            "failure": False,
            "reset": False,
        },
        "suppress": "anyDevice" if profile == "mobile" else "focused",
        "quietStart": "",
        "quietEnd": "",
    }


def _minutes(value: str) -> int | None:
    if len(value) == 5 and value[2] == ":" and value[:2].isdigit() and value[3:].isdigit():
        return int(value[:2]) * 60 + int(value[3:])
    return None


def in_quiet_time(notifications: dict[str, Any], now: time.struct_time | None = None) -> bool:
    """Mirror the client's ``isQuietTime`` so foreground and background agree."""

    start = _minutes(str(notifications.get("quietStart", "")))
    end = _minutes(str(notifications.get("quietEnd", "")))
    if start is None or end is None or start == end:
        return False
    moment = now or time.localtime()
    current = moment.tm_hour * 60 + moment.tm_min
    if start < end:
        return start <= current < end
    return current >= start or current < end


def normalize_notifications(value: Any, profile: str = "mobile") -> dict[str, Any]:
    base = default_notifications(profile)
    if not isinstance(value, dict):
        return base
    if isinstance(value.get("enabled"), bool):
        base["enabled"] = value["enabled"]
    if value.get("suppress") in SUPPRESS_MODES:
        base["suppress"] = str(value["suppress"])
    elif isinstance(value.get("suppressWhenFocused"), bool):
        # Migration from the boolean this replaced. False was a deliberate "notify me
        # anyway" and is preserved exactly; True was the default, so it maps to the
        # profile's new default rather than pinning every existing install to the old
        # behaviour it never actually chose.
        base["suppress"] = base["suppress"] if value["suppressWhenFocused"] else "never"
    for key in ("quietStart", "quietEnd"):
        if isinstance(value.get(key), str):
            base[key] = value[key]
    events = value.get("events")
    if isinstance(events, dict):
        for event in NOTIFICATION_EVENTS:
            if isinstance(events.get(event), bool):
                base["events"][event] = events[event]
    return base


def normalize_alerts(
    value: Any,
    *,
    notifications: Any = None,
    sounds: Any = None,
    profile: str = "mobile",
) -> dict[str, Any]:
    """Normalize the shared alert master and quiet hours.

    Profiles saved before the alerts domain existed derived their effective master
    from either delivery channel being enabled. Push quiet hours win when the push
    channel is active; otherwise the sound schedule is preserved. Once the browser
    saves the unified controls, this compatibility path is no longer consulted.
    """

    push = normalize_notifications(notifications, profile)
    sound = sounds if isinstance(sounds, dict) else {}
    sound_enabled = sound.get("enabled") is True
    push_quiet = (str(push.get("quietStart", "")), str(push.get("quietEnd", "")))
    sound_quiet = (
        str(sound.get("quietStart", "")) if isinstance(sound.get("quietStart"), str) else "",
        str(sound.get("quietEnd", "")) if isinstance(sound.get("quietEnd"), str) else "",
    )
    if push["enabled"] and any(push_quiet):
        quiet_start, quiet_end = push_quiet
    elif sound_enabled and any(sound_quiet):
        quiet_start, quiet_end = sound_quiet
    elif any(push_quiet):
        quiet_start, quiet_end = push_quiet
    else:
        quiet_start, quiet_end = sound_quiet
    result = {
        "enabled": bool(push["enabled"] or sound_enabled),
        "quietStart": quiet_start,
        "quietEnd": quiet_end,
    }
    if not isinstance(value, dict):
        return result
    if isinstance(value.get("enabled"), bool):
        result["enabled"] = value["enabled"]
    for key in ("quietStart", "quietEnd"):
        if isinstance(value.get(key), str):
            result[key] = value[key]
    return result


class SettingsStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "settings.json"

    def _read(self) -> dict[str, Any]:
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
            return parsed if isinstance(parsed, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _profiles(self) -> dict[str, dict[str, Any]]:
        stored = self._read().get("profiles")
        profiles: dict[str, dict[str, Any]] = {}
        for name in PROFILES:
            entry = stored.get(name) if isinstance(stored, dict) else None
            profiles[name] = entry if isinstance(entry, dict) else {}
        return profiles

    def all(self) -> dict[str, Any]:
        return {"schema_version": SETTINGS_SCHEMA_VERSION, "profiles": self._profiles()}

    def update(self, profile: str, patch: Any) -> dict[str, Any]:
        if profile not in PROFILES:
            raise ValueError("profile must be desktop or mobile")
        if not isinstance(patch, dict):
            raise ValueError("settings body must be an object")
        unknown = set(patch) - set(DOMAINS)
        if unknown:
            raise ValueError(f"unknown settings domain: {', '.join(sorted(unknown))}")
        profiles = self._profiles()
        current = dict(profiles[profile])
        for domain, value in patch.items():
            if not isinstance(value, dict):
                raise ValueError(f"{domain} settings must be an object")
            if len(json.dumps(value).encode("utf-8")) > _MAX_DOMAIN_BYTES:
                raise ValueError(f"{domain} settings exceed the size limit")
            current[domain] = value
        profiles[profile] = current
        _atomic_write(
            self.path,
            (
                json.dumps(
                    {"schema_version": SETTINGS_SCHEMA_VERSION, "profiles": profiles},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            ).encode("utf-8"),
        )
        return current

    def notifications(self, profile: str) -> dict[str, Any]:
        """Effective push settings after the shared alert policy is applied."""

        if profile not in PROFILES:
            profile = "mobile"
        stored = self._profiles()[profile]
        notifications = normalize_notifications(stored.get("notifications"), profile)
        alerts = normalize_alerts(
            stored.get("alerts"),
            notifications=stored.get("notifications"),
            sounds=stored.get("sounds"),
            profile=profile,
        )
        notifications["enabled"] = bool(notifications["enabled"] and alerts["enabled"])
        notifications["quietStart"] = alerts["quietStart"]
        notifications["quietEnd"] = alerts["quietEnd"]
        return notifications

    def alerts(self, profile: str) -> dict[str, Any]:
        """Normalized shared alert policy, including legacy-domain migration."""

        if profile not in PROFILES:
            profile = "mobile"
        stored = self._profiles()[profile]
        return normalize_alerts(
            stored.get("alerts"),
            notifications=stored.get("notifications"),
            sounds=stored.get("sounds"),
            profile=profile,
        )
