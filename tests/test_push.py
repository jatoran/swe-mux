from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest

from swe_mux import push as push_module
from swe_mux.device_presence import DevicePresenceStore, DeviceReport
from swe_mux.models import MuxEvent
from swe_mux.push import PushSender, PushStore, classify_notification, notification_plan
from swe_mux.settings_store import SettingsStore, default_notifications


def _event(event_type: str, **payload: object) -> MuxEvent:
    return MuxEvent(
        ts=0.0, session_id="s1", source="daemon", type=event_type, payload=dict(payload)
    )


def test_application_server_key_is_stable_and_valid(tmp_path: Path) -> None:
    store = PushStore(tmp_path)
    key = store.application_server_key
    raw = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
    assert len(raw) == 65 and raw[0] == 0x04  # uncompressed P-256 point
    # A second instance reloads the persisted key rather than minting a new one.
    assert PushStore(tmp_path).application_server_key == key


def test_subscription_add_dedups_and_validates(tmp_path: Path) -> None:
    store = PushStore(tmp_path)
    sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "p", "auth": "a"}}
    store.add(sub, "mobile")
    store.add(sub, "mobile")  # same endpoint replaces, not duplicates
    assert len(store.list()) == 1
    store.remove(sub["endpoint"])
    assert store.list() == []


@pytest.mark.parametrize(
    "bad",
    [
        {"endpoint": "http://insecure/x", "keys": {"p256dh": "p", "auth": "a"}},
        {"endpoint": "https://ok/x", "keys": {"p256dh": "p"}},
        {"endpoint": "https://ok/x"},
        "not-a-dict",
    ],
)
def test_subscription_add_rejects_invalid(tmp_path: Path, bad: object) -> None:
    with pytest.raises(ValueError):
        PushStore(tmp_path).add(bad, "mobile")


def test_add_rejects_unknown_profile(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        PushStore(tmp_path).add(
            {"endpoint": "https://ok/x", "keys": {"p256dh": "p", "auth": "a"}}, "tablet"
        )


def test_presence_expires_with_clock(tmp_path: Path) -> None:
    now = [1000.0]
    store = PushStore(tmp_path, clock=lambda: now[0])
    store.set_presence("https://e/1", True, ttl=30)
    assert store.is_present("https://e/1") is True
    now[0] += 31
    assert store.is_present("https://e/1") is False
    store.set_presence("https://e/1", True, ttl=30)
    store.set_presence("https://e/1", False, ttl=0)
    assert store.is_present("https://e/1") is False


def test_classify_covers_categories() -> None:
    assert classify_notification(_event("approval_needed"))["category"] == "attention"
    assert classify_notification(_event("approval_needed", kind="input"))["category"] == "attention"
    assert classify_notification(_event("turn_ended"))["category"] == "complete"
    assert classify_notification(_event("state_changed", state="idle"))["category"] == "waiting"
    assert classify_notification(_event("turn_failed"))["category"] == "failure"
    assert classify_notification(_event("unexpected_quota_reset"))["category"] == "reset"


def test_classify_excludes_subagent_and_nonroot() -> None:
    assert classify_notification(_event("approval_needed", scope="subagent")) is None
    assert classify_notification(_event("approval_needed", sidechain=True)) is None
    assert classify_notification(_event("tool_use")) is None
    assert classify_notification(_event("state_changed", state="working")) is None


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def __call__(self, *, subscription_info, data, **_kw):
        self.sent.append({"endpoint": subscription_info["endpoint"], "data": data})


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(push_module, "webpush", rec)
    return rec


async def _make_sender(
    tmp_path: Path, clock, *, profile: str = "mobile", presence=None, sleep=None
):
    store = PushStore(tmp_path, clock=clock)
    store.add({"endpoint": "https://push/e1", "keys": {"p256dh": "p", "auth": "a"}}, profile)
    from swe_mux.event_bus import EventBus

    settings = SettingsStore(tmp_path)
    sender = PushSender(
        store,
        settings,
        EventBus(),
        clock=clock,
        presence=presence,
        **({"sleep": sleep} if sleep else {}),
    )
    return store, settings, sender


async def test_sender_delivers_enabled_category(tmp_path: Path, recorder: _Recorder) -> None:
    now = [100.0]
    _store, _settings, sender = await _make_sender(tmp_path, lambda: now[0])
    await sender._handle(_event("approval_needed"))  # attention on by default
    assert len(recorder.sent) == 1


async def test_sender_skips_disabled_category(tmp_path: Path, recorder: _Recorder) -> None:
    now = [100.0]
    _store, _settings, sender = await _make_sender(tmp_path, lambda: now[0])
    await sender._handle(_event("turn_ended"))  # complete off by default
    assert recorder.sent == []


async def test_sender_dedups_within_window(tmp_path: Path, recorder: _Recorder) -> None:
    now = [100.0]
    _store, _settings, sender = await _make_sender(tmp_path, lambda: now[0])
    await sender._handle(_event("approval_needed"))
    await sender._handle(_event("approval_needed"))
    assert len(recorder.sent) == 1  # second suppressed by dedup window
    now[0] += 20
    await sender._handle(_event("approval_needed"))
    assert len(recorder.sent) == 2  # window elapsed


async def test_sender_suppresses_when_focused(tmp_path: Path, recorder: _Recorder) -> None:
    now = [100.0]
    store, _settings, sender = await _make_sender(tmp_path, lambda: now[0])
    store.set_presence("https://push/e1", True, ttl=60)  # device is looking at the app
    await sender._handle(_event("approval_needed"))
    assert recorder.sent == []  # suppressWhenFocused default on


async def test_sender_respects_disabled_profile(tmp_path: Path, recorder: _Recorder) -> None:
    now = [100.0]
    _store, settings, sender = await _make_sender(tmp_path, lambda: now[0])
    settings.update("mobile", {"notifications": {"enabled": False}})
    await sender._handle(_event("approval_needed"))
    assert recorder.sent == []


def test_notification_plan_routes_around_the_device_in_use() -> None:
    settings = default_notifications("mobile")
    plan = lambda **kw: notification_plan(settings, "attention", **kw)  # noqa: E731
    assert plan(device_present=False, other_device_active=False) == "send"
    # This device is looking at the app: the foreground sound covers it.
    assert plan(device_present=True, other_device_active=False) == "skip"
    # Another device is: hold the alert rather than buzz a pocket for something the
    # user is watching happen.
    assert plan(device_present=False, other_device_active=True) == "defer"
    # Only alerts worth chasing are held; a completion is stale by the time a
    # deferral would fire.
    assert notification_plan(
        {**settings, "events": {**settings["events"], "complete": True}},
        "complete",
        device_present=False,
        other_device_active=True,
    ) == "skip"


def test_notification_plan_modes_and_gates() -> None:
    settings = default_notifications("mobile")
    focused = {**settings, "suppress": "focused"}
    # `focused` ignores the other device entirely.
    assert notification_plan(
        focused, "attention", device_present=False, other_device_active=True
    ) == "send"
    never = {**settings, "suppress": "never"}
    assert notification_plan(
        never, "attention", device_present=True, other_device_active=True
    ) == "send"
    off = {**settings, "enabled": False}
    assert notification_plan(
        off, "attention", device_present=False, other_device_active=False
    ) == "skip"


class _Gate:
    """A deferral the test releases by hand instead of waiting on a real clock."""

    def __init__(self) -> None:
        self.released = asyncio.Event()

    async def __call__(self, _seconds: float) -> None:
        await self.released.wait()


async def _drain(sender: PushSender) -> None:
    tasks = list(sender._deferred.values())
    await asyncio.gather(*tasks, return_exceptions=True)


def _desktop_presence(now: list[float], age: float = 1.0) -> DevicePresenceStore:
    presence = DevicePresenceStore(clock=lambda: now[0])
    presence.report(
        "desktop-1",
        DeviceReport(profile="desktop", visible=True, focused=True, interaction_age=age),
    )
    return presence


async def test_a_held_notification_arrives_when_the_user_leaves_the_other_device(
    tmp_path: Path, recorder: _Recorder
) -> None:
    """The case plain suppression loses: the agent asks a question while the user is
    at their desk, and they get up before answering it."""
    now = [100.0]
    gate = _Gate()
    presence = _desktop_presence(now)
    _store, _settings, sender = await _make_sender(
        tmp_path, lambda: now[0], presence=presence, sleep=gate
    )
    await sender._handle(_event("approval_needed"))
    assert recorder.sent == []
    assert sender.pending_deferrals() == 1
    now[0] += 45  # nobody has touched the desktop since
    gate.released.set()
    await _drain(sender)
    assert len(recorder.sent) == 1


async def test_a_held_notification_is_dropped_when_the_user_stayed_at_the_other_device(
    tmp_path: Path, recorder: _Recorder
) -> None:
    now = [100.0]
    gate = _Gate()
    presence = _desktop_presence(now)
    _store, _settings, sender = await _make_sender(
        tmp_path, lambda: now[0], presence=presence, sleep=gate
    )
    await sender._handle(_event("approval_needed"))
    now[0] += 45
    # Typing at the desk after the alert is direct evidence the user was there and
    # chose not to act on it. Chasing them onto the lock screen is noise.
    presence.report(
        "desktop-1",
        DeviceReport(profile="desktop", visible=True, focused=True, interaction_age=1.0),
    )
    gate.released.set()
    await _drain(sender)
    assert recorder.sent == []


async def test_dealing_with_the_session_cancels_what_is_held_for_it(
    tmp_path: Path, recorder: _Recorder
) -> None:
    now = [100.0]
    gate = _Gate()
    _store, _settings, sender = await _make_sender(
        tmp_path, lambda: now[0], presence=_desktop_presence(now), sleep=gate
    )
    await sender._handle(_event("approval_needed"))
    assert sender.pending_deferrals() == 1
    # The user answered the prompt: the held alert is about a question that no
    # longer exists and must never arrive.
    await sender._handle(_event("terminal_input"))
    assert sender.pending_deferrals() == 0
    gate.released.set()
    await asyncio.sleep(0)
    assert recorder.sent == []


async def test_the_agent_resuming_also_cancels_a_held_notification(
    tmp_path: Path, recorder: _Recorder
) -> None:
    now = [100.0]
    gate = _Gate()
    _store, _settings, sender = await _make_sender(
        tmp_path, lambda: now[0], presence=_desktop_presence(now), sleep=gate
    )
    await sender._handle(_event("approval_needed"))
    await sender._handle(_event("state_changed", state="working"))
    assert sender.pending_deferrals() == 0
    assert recorder.sent == []


async def test_repeat_alerts_do_not_stack_deferrals(
    tmp_path: Path, recorder: _Recorder
) -> None:
    now = [100.0]
    gate = _Gate()
    _store, _settings, sender = await _make_sender(
        tmp_path, lambda: now[0], presence=_desktop_presence(now), sleep=gate
    )
    await sender._handle(_event("approval_needed"))
    now[0] += 20  # past the dedup window; the same question, asked again
    await sender._handle(_event("approval_needed"))
    assert sender.pending_deferrals() == 1
    gate.released.set()
    await _drain(sender)
    assert len(recorder.sent) == 1


async def test_a_held_notification_respects_settings_changed_while_it_waited(
    tmp_path: Path, recorder: _Recorder
) -> None:
    now = [100.0]
    gate = _Gate()
    _store, settings, sender = await _make_sender(
        tmp_path, lambda: now[0], presence=_desktop_presence(now), sleep=gate
    )
    await sender._handle(_event("approval_needed"))
    settings.update("mobile", {"notifications": {"enabled": False}})
    gate.released.set()
    await _drain(sender)
    assert recorder.sent == []
