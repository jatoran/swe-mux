"""One Settings save commits the config and the keybindings together, or neither.

The panel used to spend two requests on one Save - `PATCH /api/config` and
`PUT /api/keybindings` - fired concurrently with a single catch that reported either
failure as "invalid · nothing was changed". Half of the pairs where one fails leave the
other committed, and a `_revision` conflict raised by another device is exactly such a
pair: the config PATCH 409s while the keybindings PUT has already rewritten the file.

Every test here is a claim about the daemon's *disk* after a rejected save, because that
is what the message was lying about. The two documents live in separate files, so the
guarantee is ordered rather than transactional in the database sense - validate both, stage
the keybindings, commit the config, rename the staged file - and the last step is the only
one that can fail after something has committed. That case is tested too: it must report
which half landed instead of denying both.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from swe_mux import app_keys as keys
from swe_mux.config import Config
from swe_mux.routes import settings as settings_routes
from swe_mux.routes.settings import apply_settings


class FakeEvents:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event_type: str, **payload: Any) -> None:
        self.emitted.append((event_type, payload))


class FakeRequest:
    def __init__(
        self, app: dict[Any, Any], body: Any, headers: dict[str, str] | None = None
    ) -> None:
        self.app = app
        self._body = body
        self.headers = headers or {}
        self.query: dict[str, str] = {}

    async def json(self) -> Any:
        return self._body


def _app(tmp_path: Path) -> tuple[dict[Any, Any], Config, FakeEvents]:
    config = Config(data_dir=tmp_path)
    config.config_path = tmp_path / "config.toml"
    events = FakeEvents()
    return {keys.CONFIG: config, keys.EVENTS: events}, config, events


def _payload(response: Any) -> dict[str, Any]:
    return json.loads(response.text or "")


def _keybindings_file(tmp_path: Path) -> Path:
    return tmp_path / "keybindings.json"


def _written_bindings(tmp_path: Path) -> dict[str, str]:
    """The saved document flattened to chord -> command, for these assertions.

    The document is a rule *list* since 2026-08-30 (a rule can be scoped to a host,
    a platform or a `when`, none of which a map can hold), and none of the cases here
    exercise a scope - so flattening keeps them about atomicity, which is what this
    file is for."""
    document = json.loads(_keybindings_file(tmp_path).read_text(encoding="utf-8"))
    return {rule["keys"]: rule["command"] for rule in document["rules"]}


async def _apply(app: dict[Any, Any], body: Any, headers: dict[str, str] | None = None) -> Any:
    return await apply_settings(cast(Any, FakeRequest(app, body, headers)))


# --------------------------------------------------------------- the happy path


async def test_one_request_commits_both_halves(tmp_path: Path) -> None:
    app, config, events = _app(tmp_path)
    before = config.revision

    response = await _apply(
        app,
        {
            "_revision": before,
            "config": {"scrollback_bytes": 7 * 1024 * 1024},
            "keybindings": {"rules": [{"keys": "ctrl+shift+p", "command": "palette.open"}]},
        },
    )

    assert response.status == 200
    payload = _payload(response)
    assert payload["committed"] == ["config", "keybindings"]
    assert payload["config"]["scrollback_bytes"] == 7 * 1024 * 1024
    resolved = payload["keybindings"]["resolved"]
    assert resolved["ctrl+shift+p"] == [{"command": "palette.open", "when": ""}]
    assert config.revision == before + 1
    assert response.headers["ETag"] == f'"{config.revision}"'
    assert _written_bindings(tmp_path) == {"ctrl+shift+p": "palette.open"}
    # One announcement for one transaction, carrying both what changed and the fact that
    # the shortcuts moved with it.
    assert [event for event, _ in events.emitted] == ["configuration_changed"]
    assert events.emitted[0][1]["keybindings"] is True
    assert "scrollback_bytes" in events.emitted[0][1]["changed"]


async def test_a_config_only_save_leaves_the_keybindings_file_alone(tmp_path: Path) -> None:
    app, config, _ = _app(tmp_path)

    response = await _apply(app, {"config": {"git_poll_seconds": 3.0}})

    assert response.status == 200
    assert _payload(response)["committed"] == ["config"]
    assert config.git_poll_seconds == 3.0
    # Absent, not defaulted: a save that names no shortcuts must not be able to blank them.
    assert not _keybindings_file(tmp_path).exists()


# ------------------------------------------------------- neither half commits


async def test_a_rejected_chord_leaves_the_config_untouched(tmp_path: Path) -> None:
    """The direction the old pre-validation covered - now enforced by ordering, not by a probe."""
    app, config, events = _app(tmp_path)
    before = config.revision

    response = await _apply(
        app,
        {
            "config": {"scrollback_bytes": 9 * 1024 * 1024},
            "keybindings": {"rules": [{"keys": "ctrl+shift+p", "command": "no.such.command"}]},
        },
    )

    assert response.status == 422
    payload = _payload(response)
    assert payload["section"] == "keybindings"
    assert payload["committed"] == []
    assert "ctrl+shift+p" in payload["fields"]
    assert config.revision == before
    assert config.scrollback_bytes != 9 * 1024 * 1024
    assert not _keybindings_file(tmp_path).exists()
    assert events.emitted == []


async def test_a_rejected_config_field_leaves_the_shortcuts_untouched(tmp_path: Path) -> None:
    """The direction that used to half-commit, with the panel reporting nothing changed."""
    app, config, events = _app(tmp_path)
    _keybindings_file(tmp_path).write_text(
        json.dumps(
            {
                "version": 3,
                "preset": "custom",
                "rules": [{"keys": "ctrl+shift+p", "command": "palette.open"}],
            }
        ),
        encoding="utf-8",
    )
    before = config.revision

    response = await _apply(
        app,
        {
            "config": {"scrollback_bytes": -1},
            "keybindings": {"rules": [{"keys": "ctrl+shift+n", "command": "notes.open"}]},
        },
    )

    assert response.status == 422
    payload = _payload(response)
    assert payload["section"] == "config"
    assert payload["committed"] == []
    assert payload["fields"]
    assert config.revision == before
    # The file on disk is still the one that was there before the save was attempted...
    assert _written_bindings(tmp_path) == {"ctrl+shift+p": "palette.open"}
    # ...and the staged copy did not survive to be picked up by anything later.
    assert not list(tmp_path.glob("keybindings.json.tmp"))
    assert events.emitted == []


async def test_an_unknown_setting_commits_nothing(tmp_path: Path) -> None:
    app, config, _ = _app(tmp_path)
    before = config.revision

    response = await _apply(
        app,
        {
            "config": {"not_a_setting": 1},
            "keybindings": {"rules": [{"keys": "ctrl+shift+p", "command": "palette.open"}]},
        },
    )

    assert response.status == 422
    assert _payload(response)["committed"] == []
    assert config.revision == before
    assert not _keybindings_file(tmp_path).exists()


# ------------------------------------------------------- the revision contract


@pytest.mark.parametrize("channel", ["body", "header"])
async def test_another_devices_revision_conflict_commits_nothing(
    tmp_path: Path, channel: str
) -> None:
    """The case that produced the false "nothing was changed" - now the message is true."""
    app, config, events = _app(tmp_path)
    stale = config.revision + 5
    body: dict[str, Any] = {
        "config": {"scrollback_bytes": 9 * 1024 * 1024},
        "keybindings": {"rules": [{"keys": "ctrl+shift+p", "command": "palette.open"}]},
    }
    headers: dict[str, str] = {}
    if channel == "body":
        body["_revision"] = stale
    else:
        headers["If-Match"] = f'"{stale}"'

    response = await _apply(app, body, headers)

    assert response.status == 409
    payload = _payload(response)
    assert payload["revision"] == config.revision
    assert config.scrollback_bytes != 9 * 1024 * 1024
    assert not _keybindings_file(tmp_path).exists()
    assert events.emitted == []


async def test_a_matching_revision_is_accepted_from_either_channel(tmp_path: Path) -> None:
    app, config, _ = _app(tmp_path)

    response = await _apply(
        app,
        {"config": {"git_poll_seconds": 2.5}},
        {"If-Match": f'"{config.revision}"'},
    )

    assert response.status == 200
    assert config.git_poll_seconds == 2.5


# ------------------------------------- the one half-commit, reported as a half-commit


async def test_a_failed_keybindings_commit_names_the_half_that_landed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rename is the last step and the only one that can fail after a commit.

    There is no way to put the config back - it is saved, hot-applied, and other devices
    have been told. So the answer says which half committed rather than claiming, as the
    old client did for every failure, that nothing was changed.
    """
    app, config, events = _app(tmp_path)
    before = config.revision

    def explode(_config: Config, _temporary: Path) -> None:
        raise OSError("keybindings.json is locked by another process")

    monkeypatch.setattr(settings_routes, "_publish_keybindings", explode)

    response = await _apply(
        app,
        {
            "config": {"scrollback_bytes": 6 * 1024 * 1024},
            "keybindings": {"rules": [{"keys": "ctrl+shift+p", "command": "palette.open"}]},
        },
    )

    assert response.status == 500
    payload = _payload(response)
    assert payload["committed"] == ["config"]
    assert payload["failed"] == ["keybindings"]
    assert "locked by another process" in payload["error"]
    # The config half really did land, which is why the response admits to it.
    assert config.revision == before + 1
    assert payload["config"]["scrollback_bytes"] == 6 * 1024 * 1024
    # Other devices still hear about the half that committed.
    assert [event for event, _ in events.emitted] == ["configuration_changed"]


# ------------------------------------------------------------------ body shapes


async def test_the_preset_a_save_names_is_recorded_beside_its_rules(tmp_path: Path) -> None:
    """A save carries which preset the rules came from, and `custom` once they no
    longer come from one. It is what lets a later release seed a new default without
    an accumulating `V<N>_DEFAULT_KEYBINDINGS` constant per release, which is how the
    previous format did it and could only ever grow."""
    app, _, _ = _app(tmp_path)

    response = await _apply(app, {"keybindings": {
        "preset": "tmux",
        "rules": [{"keys": "ctrl+b c", "command": "session.spawnShell"}],
    }})

    assert response.status == 200
    assert _payload(response)["committed"] == ["config", "keybindings"]
    document = json.loads(_keybindings_file(tmp_path).read_text(encoding="utf-8"))
    assert document["preset"] == "tmux"
    assert document["rules"] == [{"keys": "ctrl+b c", "command": "session.spawnShell"}]


@pytest.mark.parametrize("body", [[], "config", {"config": []}, {"keybindings": 3}])
async def test_a_malformed_body_is_a_client_error_and_writes_nothing(
    tmp_path: Path, body: Any
) -> None:
    app, config, _ = _app(tmp_path)
    before = config.revision

    with pytest.raises(ValueError):
        await _apply(app, body)

    assert config.revision == before
    assert not _keybindings_file(tmp_path).exists()
