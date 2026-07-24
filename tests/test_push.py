from __future__ import annotations

import base64
from pathlib import Path

import pytest

from swe_mux import push as push_module
from swe_mux.models import MuxEvent
from swe_mux.push import PushSender, PushStore, classify_notification
from swe_mux.settings_store import SettingsStore


def _event(event_type: str, **payload: object) -> MuxEvent:
    return MuxEvent(ts=0.0, session_id="s1", source="daemon", type=event_type, payload=dict(payload))


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
        PushStore(tmp_path).add({"endpoint": "https://ok/x", "keys": {"p256dh": "p", "auth": "a"}}, "tablet")


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


async def _make_sender(tmp_path: Path, clock, *, profile: str = "mobile"):
    store = PushStore(tmp_path, clock=clock)
    store.add({"endpoint": "https://push/e1", "keys": {"p256dh": "p", "auth": "a"}}, profile)
    settings = SettingsStore(tmp_path)
    return store, settings, PushSender(store, settings, __import__("swe_mux.event_bus", fromlist=["EventBus"]).EventBus(), clock=clock)


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
