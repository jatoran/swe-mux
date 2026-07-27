from __future__ import annotations

import time
from pathlib import Path

import pytest

from swe_mux.settings_store import (
    SettingsStore,
    default_notifications,
    in_quiet_time,
    normalize_notifications,
)


def test_empty_store_reports_both_profiles(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    data = store.all()
    assert data["schema_version"] == 1
    assert set(data["profiles"]) == {"desktop", "mobile"}
    assert data["profiles"]["desktop"] == {}


def test_update_merges_domains_per_profile(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.update("mobile", {"notifications": {"enabled": True}})
    store.update("mobile", {"sounds": {"volume": 0.5}})
    store.update("desktop", {"sounds": {"volume": 0.9}})
    profiles = store.all()["profiles"]
    # Second mobile write must not drop the first domain.
    assert profiles["mobile"] == {"notifications": {"enabled": True}, "sounds": {"volume": 0.5}}
    assert profiles["desktop"] == {"sounds": {"volume": 0.9}}


def test_command_rail_domain_is_stored_opaquely(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    payload = {"items": [{"id": "esc", "type": "key", "label": "Esc"}]}
    store.update("desktop", {"commandRail": payload})
    assert store.all()["profiles"]["desktop"]["commandRail"] == payload


def test_file_tree_domain_is_stored_opaquely(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    payload = {"proj-a": ["src", "src/swe_mux"], "proj-b": []}
    store.update("desktop", {"fileTree": payload})
    assert store.all()["profiles"]["desktop"]["fileTree"] == payload


def test_drawer_tab_domain_is_stored_opaquely(tmp_path: Path) -> None:
    """The browser owns tab-order validation, so the daemon must not reshape the blob.

    Order is stored server-side rather than in localStorage because it says which surfaces the
    user reaches for, not anything about a device, so a phone should inherit a desktop's
    arrangement. An unknown id here is the browser's problem to normalize, not a rejection.
    """
    store = SettingsStore(tmp_path)
    payload = {"order": ["files", "notes", "clipboard", "commands", "prompts", "notifications"]}
    store.update("desktop", {"drawerTabs": payload})
    assert store.all()["profiles"]["desktop"]["drawerTabs"] == payload
    store.update("desktop", {"drawerTabs": {"order": ["retired-tab"]}})
    assert store.all()["profiles"]["desktop"]["drawerTabs"] == {"order": ["retired-tab"]}


def test_update_persists_across_instances(tmp_path: Path) -> None:
    SettingsStore(tmp_path).update("desktop", {"sounds": {"volume": 0.2}})
    assert SettingsStore(tmp_path).all()["profiles"]["desktop"] == {"sounds": {"volume": 0.2}}


@pytest.mark.parametrize(
    "profile,patch",
    [
        ("bogus", {}),
        ("mobile", {"unknown_domain": {}}),
        ("mobile", {"sounds": "not-an-object"}),
        ("mobile", "not-an-object"),
    ],
)
def test_update_rejects_invalid_input(tmp_path: Path, profile: str, patch: object) -> None:
    with pytest.raises(ValueError):
        SettingsStore(tmp_path).update(profile, patch)


def test_oversized_domain_rejected(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    with pytest.raises(ValueError):
        store.update("mobile", {"sounds": {"blob": "x" * (1024 * 1024 + 10)}})


def test_notifications_returns_normalized_defaults(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    # Unconfigured profile still yields usable defaults for the push sender.
    assert store.notifications("mobile") == default_notifications()
    # An unknown profile name falls back to mobile rather than raising.
    assert store.notifications("nonsense") == default_notifications()


def test_notifications_overlays_stored_values(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.update("mobile", {"notifications": {"events": {"attention": False, "complete": True}}})
    result = store.notifications("mobile")
    assert result["events"]["attention"] is False
    assert result["events"]["complete"] is True
    # Untouched events keep their defaults.
    assert result["events"]["waiting"] is True


def test_normalize_ignores_wrong_types() -> None:
    result = normalize_notifications({"enabled": "yes", "events": "nope", "quietStart": 5})
    assert result == default_notifications()


def test_quiet_time_same_day_window() -> None:
    window = {"quietStart": "09:00", "quietEnd": "17:00"}
    assert in_quiet_time(window, time.struct_time((2026, 7, 23, 12, 0, 0, 0, 0, -1))) is True
    assert in_quiet_time(window, time.struct_time((2026, 7, 23, 8, 0, 0, 0, 0, -1))) is False


def test_quiet_time_overnight_window_wraps_midnight() -> None:
    window = {"quietStart": "22:00", "quietEnd": "07:00"}
    assert in_quiet_time(window, time.struct_time((2026, 7, 23, 23, 30, 0, 0, 0, -1))) is True
    assert in_quiet_time(window, time.struct_time((2026, 7, 24, 3, 0, 0, 0, 0, -1))) is True
    assert in_quiet_time(window, time.struct_time((2026, 7, 24, 12, 0, 0, 0, 0, -1))) is False


def test_quiet_time_disabled_when_unset_or_equal() -> None:
    assert in_quiet_time({"quietStart": "", "quietEnd": ""}) is False
    assert in_quiet_time({"quietStart": "08:00", "quietEnd": "08:00"}) is False
