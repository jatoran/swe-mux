"""The quest log's config half: a closed three-id set, dismissal-only."""

from __future__ import annotations

from pathlib import Path

import pytest

from swe_mux.config import QUEST_IDS, Config, update_config


def _fresh(tmp_path: Path) -> Config:
    config = Config(data_dir=tmp_path)
    config.config_path = tmp_path / "config.toml"
    return config


def test_the_set_is_closed_at_three() -> None:
    """The cap is the feature; widening it is a deliberate edit here AND in
    `frontend/src/questRegistry.ts`, never a data change."""
    assert QUEST_IDS == ("voice", "worktrees", "phone")


def test_dismissals_accept_known_ids_and_refuse_everything_else(tmp_path: Path) -> None:
    config = _fresh(tmp_path)
    update_config(config, {"quests_dismissed": ["voice", "phone"]})
    assert config.quests_dismissed == ["voice", "phone"]
    with pytest.raises(ValueError):
        update_config(config, {"quests_dismissed": ["voice", "todo-42"]})
    assert config.quests_dismissed == ["voice", "phone"], "a refused write changes nothing"
