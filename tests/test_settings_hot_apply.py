"""What the new Settings controls promise the daemon actually does.

Every field wired into the Settings panel by the 2026-08-21 coverage pass was
previously reachable only by editing ``~/.mux/config.toml``, which meant nobody had
ever asked whether a *running* daemon would notice a change to it. Four of them would
not have: the attach replay budget and the three session-recovery bounds are handed to
their owners as constructor arguments at startup, so a PATCH would have updated the
config, reported ``hot applied``, and changed nothing until the next restart.

They are plain mutable attributes read at use time, so the honest fix is to push the
change down rather than to declare a restart the operator does not need. That is what
these tests hold - a panel that reports a hot apply must be telling the truth, and the
failure mode this replaces is silent by construction.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from swe_mux import app_keys as keys
from swe_mux.config import RESTART_FIELDS, Config, update_config
from swe_mux.server import _apply_runtime_config
from swe_mux.session import Session, SessionManager
from swe_mux.session_recovery import SessionRecoveryStore
from swe_mux.status_timeline import StatusTimelineStore


class _FakeSession:
    def __init__(self) -> None:
        self.attach_replay_bytes: int | None = 512 * 1024


class _FakeSessionManager:
    """Only the surface `_apply_runtime_config` touches for these fields.

    A stand-in rather than a real manager because building one needs adapters, a
    reaper, a history store, and an event bus - none of which this behaviour reads.
    The attribute names are the coupling, and `test_the_stand_in_matches_the_real
    _classes` below holds them against `Session` and `SessionManager` so a rename
    cannot leave this file passing against a shape nothing has.
    """

    def __init__(self) -> None:
        self.attach_replay_bytes: int | None = 512 * 1024
        self.max_scrollback = 5 * 1024 * 1024
        self.sessions: dict[str, Any] = {"live": _FakeSession()}
        self.adapters: dict[str, Any] = {}
        self.child_env: dict[str, str] = {}


def _apply(
    config: Config, changes: dict[str, Any], **handles: Any
) -> set[str]:
    app = web.Application()
    app[keys.CONFIG] = config
    for name, handle in handles.items():
        app[getattr(keys, name.upper())] = handle
    hot, restart = update_config(config, changes)
    _apply_runtime_config(app, hot)
    assert not restart, f"unexpected restart-required fields: {sorted(restart)}"
    return hot


def test_the_replay_budget_reaches_the_manager_and_every_live_session(tmp_path: Path) -> None:
    config = Config(data_dir=tmp_path)
    sessions = _FakeSessionManager()
    hot = _apply(config, {"attach_replay_bytes": 64 * 1024}, sessions=sessions)
    assert "attach_replay_bytes" in hot
    assert sessions.attach_replay_bytes == 64 * 1024
    # The live session is the half that matters: each one carries its own copy and
    # reads it at attach time, so leaving them behind would apply the new budget only
    # to sessions spawned after the change - which is indistinguishable, from the
    # operator's side, from the setting doing nothing.
    assert sessions.sessions["live"].attach_replay_bytes == 64 * 1024


def test_the_stand_in_matches_the_real_classes() -> None:
    # The fake above is only worth anything while its attribute names are the real
    # ones. Both are constructor keywords, which is the shape a rename has to move.
    assert "attach_replay_bytes" in inspect.signature(Session.__init__).parameters
    assert "attach_replay_bytes" in inspect.signature(SessionManager.__init__).parameters
    # `sessions` is assigned in the body rather than taken as an argument, so it is
    # read from the source of that assignment instead of from a signature.
    assert "self.sessions: dict[str, Session] = {}" in inspect.getsource(SessionManager.__init__)


def test_the_recovery_bounds_reach_the_store(tmp_path: Path) -> None:
    store = SessionRecoveryStore(tmp_path / "mux.db", tmp_path / "recovery")
    try:
        config = Config(data_dir=tmp_path)
        _apply(
            config,
            {
                "session_recovery_checkpoint_bytes": 4096,
                "session_recovery_retention_days": 3,
                "session_recovery_max_sessions": 12,
            },
            session_recovery=store,
        )
        assert store.checkpoint_bytes == 4096
        assert store.retention_days == 3
        assert store.max_cold_sessions == 12
    finally:
        store.close()


def test_the_status_timeline_window_reaches_the_store(tmp_path: Path) -> None:
    store = StatusTimelineStore(tmp_path / "mux.db")
    try:
        config = Config(data_dir=tmp_path)
        _apply(config, {"status_timeline_retention_days": 7}, status_timeline=store)
        assert store.retention_days == 7
    finally:
        store.close()


def test_applying_with_no_handles_is_a_no_op(tmp_path: Path) -> None:
    # The daemon calls this during startup and from tests before the runtime exists,
    # so every handle is optional and a missing one must not raise.
    config = Config(data_dir=tmp_path)
    _apply(config, {"attach_replay_bytes": 4096, "status_timeline_retention_days": 9})


@pytest.mark.parametrize(
    "field",
    [
        "attach_replay_bytes",
        "session_recovery_checkpoint_bytes",
        "session_recovery_retention_days",
        "session_recovery_max_sessions",
        "status_timeline_retention_days",
    ],
)
def test_these_are_deliberately_not_restart_scoped(field: str) -> None:
    # The counterpart claim. Each of these now has a control that says nothing about
    # restarting, so a later change that makes one restart-scoped has to come back
    # here and to the control's help text together.
    assert field not in RESTART_FIELDS


@pytest.mark.parametrize("field", ["automation_queue_size", "openrouter_request_timeout_seconds"])
def test_the_two_restart_scoped_controls_still_are(field: str) -> None:
    # Both gained a control in the same pass, and both say "takes effect on the next
    # daemon restart" in the panel. That sentence is only true while this holds.
    assert field in RESTART_FIELDS
