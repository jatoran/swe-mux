"""The agent-facing door onto the per-device settings store.

The browser writes whole domains and that stays right for it: it holds the
schema, it normalizes, and the value it sends is one it just built. These tests
cover the *other* editor - one that does not hold the schema - and what has to be
true before it is allowed near a document nothing in this process can validate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swe_mux.settings_store import INTERPRETED_DOMAINS, SettingsStore, domain_digest

RAIL = {
    "version": 3,
    "items": [{"id": "padArrows", "label": "Arrows"}],
    "layouts": {
        "mobile": {
            "strip": [
                {"id": "row-2", "items": ["ctrlU", "padArrows", "up", "down", "left", "right"]}
            ]
        }
    },
    "projects": {"p-other": {"mode": "delta"}},
}

REMOVE_ARROWS = [
    {
        "op": "remove_values",
        "path": "/layouts/mobile/strip/[id=row-2]/items",
        "values": ["up", "down", "left", "right"],
    }
]


@pytest.fixture
def store(tmp_path: Path) -> SettingsStore:
    instance = SettingsStore(tmp_path)
    instance.update("desktop", {"commandRail": RAIL})
    return instance


def test_a_domain_read_carries_the_digest_a_write_must_present(store: SettingsStore) -> None:
    entry = store.domain("desktop", "commandRail")
    assert entry["document"] == RAIL
    assert entry["digest"] == domain_digest(RAIL)
    assert entry["interpreted"] is False
    # The two the daemon reads and enforces policy over say so, because it decides
    # whether a malformed write is refused or merely stored.
    assert store.domain("mobile", "notifications")["interpreted"] is True
    assert set(INTERPRETED_DOMAINS) == {"alerts", "notifications"}


def test_the_digest_is_stable_across_key_order(store: SettingsStore) -> None:
    """Both writers round-trip through `json`; a digest that moved on
    re-serialization would refuse every second write for no reason."""
    assert domain_digest({"a": 1, "b": 2}) == domain_digest({"b": 2, "a": 1})


def test_an_edit_removes_what_it_named_and_leaves_the_rest(store: SettingsStore) -> None:
    digest = store.domain("desktop", "commandRail")["digest"]
    result = store.apply_operations("desktop", "commandRail", REMOVE_ARROWS, digest)
    items = result["document"]["layouts"]["mobile"]["strip"][0]["items"]
    assert items == ["ctrlU", "padArrows"]
    # Catalog, version, and another Project's override: never named, never touched.
    assert result["document"]["items"] == RAIL["items"]
    assert result["document"]["projects"] == RAIL["projects"]
    assert result["digest"] != digest
    assert result["previous_digest"] == digest
    assert "removed 4" in result["applied"][0]


def test_a_stale_digest_is_refused_rather_than_clobbering(store: SettingsStore) -> None:
    """The guard this store had no way to offer before.

    An agent that read a rail, thought about it, and wrote it back would silently
    discard a drag the operator did in between - the store has no revision and
    the browser writes whole domains. Requiring the digest that was read makes
    that a refusal.
    """
    stale = store.domain("desktop", "commandRail")["digest"]
    store.update("desktop", {"commandRail": {**RAIL, "version": 4}})
    with pytest.raises(ValueError, match="changed since you read them"):
        store.apply_operations("desktop", "commandRail", REMOVE_ARROWS, stale)
    # Nothing moved.
    assert store.domain("desktop", "commandRail")["document"]["version"] == 4


def test_an_omitted_digest_skips_the_check(store: SettingsStore) -> None:
    result = store.apply_operations("desktop", "commandRail", REMOVE_ARROWS)
    assert result["document"]["layouts"]["mobile"]["strip"][0]["items"] == ["ctrlU", "padArrows"]


def test_the_previous_file_is_kept_beside_itself(store: SettingsStore) -> None:
    """The safety net that makes an unvalidatable write survivable.

    Nothing here can say a rail is correct - the browser owns that schema - so
    the honest guarantee is not "this write is right" but "the previous document
    is still on disk". `config.toml` already earned this; this file had not.
    """
    before = store.domain("desktop", "commandRail")["document"]
    store.apply_operations("desktop", "commandRail", REMOVE_ARROWS)
    backup = store.path.with_suffix(".json.bak")
    assert backup.is_file()
    restored = json.loads(backup.read_text(encoding="utf-8"))
    assert restored["profiles"]["desktop"]["commandRail"] == before


def test_a_failing_operation_changes_nothing_and_writes_nothing(store: SettingsStore) -> None:
    before = store.domain("desktop", "commandRail")
    with pytest.raises(ValueError, match="row-9"):
        store.apply_operations(
            "desktop",
            "commandRail",
            [{"op": "remove", "path": "/layouts/mobile/strip/[id=row-9]"}],
        )
    assert store.domain("desktop", "commandRail") == before


def test_an_absent_domain_edits_from_an_empty_object(tmp_path: Path) -> None:
    # A profile that has never held a domain reads as `{}` rather than failing,
    # so setting the first key is an ordinary edit rather than a special case.
    fresh = SettingsStore(tmp_path)
    result = fresh.apply_operations(
        "mobile", "sessionRows", [{"op": "set", "path": "/order", "value": ["a"]}]
    )
    assert result["document"] == {"order": ["a"]}


def test_an_unknown_profile_or_domain_is_named(store: SettingsStore) -> None:
    with pytest.raises(ValueError, match="desktop or mobile"):
        store.domain("tablet", "commandRail")
    with pytest.raises(ValueError, match="unknown settings domain"):
        store.domain("desktop", "railCommand")
