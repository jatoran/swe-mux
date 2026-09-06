"""Returning an install to its first-run state without destroying anything else.

A factory reset is the one operation here that is *supposed* to be destructive,
which makes the interesting assertions the ones about its edges rather than its
centre. What it must not take (the user's repositories, their worktree
checkouts, the shim directory on their PATH), what it must not leave (anything
this install wrote, including the settings still live in the running process's
memory), and what it must do when the filesystem refuses - all three are here.

The keep-list is pinned deliberately. Adding a load-bearing file to the data
directory should fail this test and force the decision, rather than be
discovered in the field by someone whose reset took their PATH shims with it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swe_mux.config import Config
from swe_mux.factory_reset import (
    CONFIRMATION_PHRASE,
    KEEP_ENTRIES,
    ResetRequest,
    clear_request,
    describe,
    perform_reset,
    planned_entries,
    read_request,
    read_result,
    request_path,
    restore_defaults,
    result_path,
    worktree_directories,
    write_request,
)


def _install(tmp_path: Path) -> Config:
    """A data directory shaped like a real one: state, logs, and keepers."""
    data_dir = tmp_path / "mux"
    data_dir.mkdir(parents=True)
    # Real TOML, because the reset re-reads this path through `load_config` to
    # rebuild the process's settings, and a fixture that could not be parsed
    # would be testing the parser rather than the reset.
    (data_dir / "config.toml").write_text('theme = "custom"\n', encoding="utf-8")
    for name in ("mux.db", "settings.json", "onboarding.json", "daemon.log"):
        (data_dir / name).write_text("x", encoding="utf-8")
    for name in ("sessions", "telemetry", "provider-accounts", "notes"):
        (data_dir / name).mkdir()
        (data_dir / name / "inner").write_text("x", encoding="utf-8")
    for name in ("bin", "voice-models", "worktrees", "webview"):
        (data_dir / name).mkdir()
        (data_dir / name / "inner").write_text("x", encoding="utf-8")
    (data_dir / "worktrees" / "worktree-feature").mkdir()
    return Config(data_dir=data_dir, config_path=data_dir / "config.toml")


def test_the_keep_list_is_exactly_what_a_reset_must_not_take() -> None:
    """Pinned so that adding to it is a decision, not an accident.

    Each entry is either regenerated on the next start, held open by a process
    that survives the reset, an expensive cached download, or the user's own
    work. Anything else in the data directory is this install's state and goes.
    """
    assert KEEP_ENTRIES == {
        "bin",
        "webview",
        "voice-models",
        "voice-runtime",
        "frontend-overlay",
        "worktrees",
        "desktop-control.token",
        ".trash",
    }


def test_a_request_round_trips_and_an_unreadable_one_is_not_a_request(tmp_path: Path) -> None:
    """An unparseable file is never authority to move somebody's install."""
    data_dir = tmp_path / "mux"
    data_dir.mkdir()
    assert read_request(data_dir) is None
    write_request(data_dir, external=True)
    request = read_request(data_dir)
    assert request is not None
    assert request.external is True
    assert request.requested_at > 0
    request_path(data_dir).write_text("{not json", encoding="utf-8")
    assert read_request(data_dir) is None
    request_path(data_dir).write_text('["a list"]', encoding="utf-8")
    assert read_request(data_dir) is None
    clear_request(data_dir)
    assert not request_path(data_dir).exists()
    clear_request(data_dir)  # idempotent: a second clear is not an error


def test_the_sweep_moves_state_and_leaves_the_keepers(tmp_path: Path) -> None:
    config = _install(tmp_path)
    planned = planned_entries(config.data_dir)
    assert "mux.db" in planned and "sessions" in planned
    assert "bin" not in planned and "worktrees" not in planned

    result = perform_reset(config, ResetRequest())

    assert set(result.moved) >= {"mux.db", "config.toml", "sessions", "telemetry", "notes"}
    assert set(result.kept) >= {"bin", "voice-models", "worktrees", "webview"}
    assert result.failed == []
    for name in ("mux.db", "sessions", "onboarding.json"):
        assert not (config.data_dir / name).exists(), f"{name} should have been moved aside"
    for name in ("bin", "voice-models", "worktrees", "webview"):
        assert (config.data_dir / name / "inner").is_file(), f"{name} must survive untouched"
    # `config.toml` is the one file that comes back immediately, and it comes
    # back empty of this install: rebuilding the process's settings reads the
    # now-absent path, and writing a default config for a path that has none is
    # what an ordinary first start does too.
    assert "config.toml" in result.moved
    assert 'theme = "custom"' not in (config.data_dir / "config.toml").read_text(encoding="utf-8")


def test_nothing_is_deleted_and_the_result_says_where_it_went(tmp_path: Path) -> None:
    """The trash is the safety net, so the result has to name it.

    A reset that silently deleted would be unrecoverable by construction, and a
    reset that moved without saying where would be unrecoverable in practice.
    """
    config = _install(tmp_path)
    result = perform_reset(config, ResetRequest())
    trash = Path(result.trash_path)
    assert trash.is_dir()
    assert trash.parent == config.data_dir / ".trash"
    assert (trash / "mux.db").is_file()
    assert (trash / "sessions" / "inner").is_file()
    recorded = read_result(config.data_dir)
    assert recorded is not None
    assert recorded["trash_path"] == result.trash_path
    assert result_path(config.data_dir).is_file()
    assert "moved" in describe(result) and result.trash_path in describe(result)


def test_worktrees_are_reported_rather_than_removed(tmp_path: Path) -> None:
    """They are the user's checkouts, and uncommitted work in one has no copy."""
    config = _install(tmp_path)
    before = worktree_directories(config.data_dir)
    assert before == [str(config.data_dir / "worktrees" / "worktree-feature")]
    result = perform_reset(config, ResetRequest())
    assert result.worktrees == before
    assert (config.data_dir / "worktrees" / "worktree-feature").is_dir()


def test_a_file_this_process_holds_open_is_emptied_rather_than_abandoned(
    tmp_path: Path,
) -> None:
    """The daemon's own logs cannot be renamed on Windows, and must not survive.

    A reset that left `daemon.log` full of the previous install's session paths
    and Project names would be leaving exactly the residue it exists to remove.
    Truncation reaches the same end state without a handle to close first, and
    is reported as what it is rather than counted as a move.
    """
    config = _install(tmp_path)
    log_path = config.data_dir / "daemon.log"
    log_path.write_text("previous install\n" * 100, encoding="utf-8")
    with log_path.open("a", encoding="utf-8") as held:
        held.write("still open\n")
        held.flush()
        result = perform_reset(config, ResetRequest())
    assert "daemon.log" not in result.failed
    # Windows refuses the rename and the file is emptied; POSIX renames it. Both
    # outcomes are correct, and neither leaves readable content behind.
    assert "daemon.log" in result.moved + result.truncated
    if log_path.exists():
        assert log_path.read_text(encoding="utf-8") == ""


def test_an_entry_that_cannot_be_moved_is_recorded_and_the_rest_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One refusal must not become a daemon that will not start."""
    config = _install(tmp_path)
    import swe_mux.factory_reset as module

    real_replace = module.os.replace

    def refuse(source: object, destination: object) -> None:
        if str(source).endswith("telemetry"):
            raise OSError(5, "access is denied")
        real_replace(source, destination)  # type: ignore[arg-type]

    monkeypatch.setattr(module.os, "replace", refuse)
    result = perform_reset(config, ResetRequest())
    assert [item["name"] for item in result.failed] == ["telemetry"]
    assert "access is denied" in result.failed[0]["error"]
    assert "mux.db" in result.moved
    assert "could not move 1" in describe(result)


def test_the_running_process_forgets_the_install_it_just_reset(tmp_path: Path) -> None:
    """The sweep alone would leave the old settings live in memory.

    Without this the daemon finishes starting on the configuration it loaded
    before the reset and writes it back the first time anything saves, quietly
    reinstating the install that was just erased.
    """
    config = _install(tmp_path)
    config.theme = "custom"
    config.harness_setup_complete = True
    default = Config(data_dir=config.data_dir)
    perform_reset(config, ResetRequest())
    assert config.theme == default.theme
    assert config.harness_setup_complete == default.harness_setup_complete
    assert config.data_dir == default.data_dir
    assert config.config_path == config.data_dir / "config.toml"


def test_restoring_defaults_does_not_invent_a_new_data_directory(tmp_path: Path) -> None:
    """`data_dir` and `config_path` are this process's identity, not settings."""
    config = _install(tmp_path)
    (config.data_dir / "config.toml").unlink()
    config.theme = "custom"
    restore_defaults(config)
    assert config.data_dir == tmp_path / "mux"
    assert config.config_path == tmp_path / "mux" / "config.toml"
    assert config.theme != "custom"


def test_the_external_group_is_off_unless_it_was_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shortcuts and installed skills were separate disclosed acts.

    Each reaches outside the data directory - `~/.claude/skills` reaches every
    agent that user runs anywhere - so a reset takes them only on request.
    """
    config = _install(tmp_path)
    import swe_mux.factory_reset as module

    called: list[str] = []
    monkeypatch.setattr(module, "_remove_skills", lambda result: called.append("skills"))
    monkeypatch.setattr(module, "_remove_shortcuts", lambda config, result: called.append("cuts"))

    quiet = perform_reset(config, ResetRequest())
    assert called == []
    assert quiet.external == []

    config = _install(tmp_path / "second")
    loud = perform_reset(config, ResetRequest(external=True))
    assert called == ["skills", "cuts"]
    # What has no unattended removal path is named rather than implied away.
    assert [row["item"] for row in loud.external] == ["windows-firewall", "tailscale-serve"]
    assert all(row["action"] == "left" for row in loud.external)


def test_a_failing_external_undo_is_recorded_and_the_reset_still_stands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _install(tmp_path)
    import swe_mux.factory_reset as module

    def explode(result: object) -> None:
        raise RuntimeError("no permission")

    monkeypatch.setattr(module, "_remove_skills", explode)
    monkeypatch.setattr(module, "_remove_shortcuts", lambda config, result: None)
    result = perform_reset(config, ResetRequest(external=True))
    failed = [row for row in result.external if row["action"] == "failed"]
    assert failed and failed[0]["item"] == "agent skills"
    assert "no permission" in failed[0]["detail"]
    assert "mux.db" in result.moved


def test_the_confirmation_phrase_is_stated_once(tmp_path: Path) -> None:
    """The client renders the daemon's phrase; two copies would drift apart."""
    assert CONFIRMATION_PHRASE == "factory reset"


def test_a_result_file_survives_the_sweep_that_would_have_moved_it(tmp_path: Path) -> None:
    """It is written after the sweep, and the sweep skips this module's own files."""
    config = _install(tmp_path)
    write_request(config.data_dir, external=False)
    result = perform_reset(config, ResetRequest())
    assert "factory-reset.json" not in result.moved
    assert "factory-reset-result.json" not in result.moved
    recorded = json.loads(result_path(config.data_dir).read_text(encoding="utf-8"))
    assert recorded["moved"] == result.moved
