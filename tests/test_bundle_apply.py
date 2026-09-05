"""The staged swap, run against a directory tree rather than a live app.

`bundle_apply` is the one implementation both the checkout redeploy script and
the frozen console client run, so its guarantees are asserted here once, on a
layout under `tmp_path`, with the three things that touch a real machine - the
stop, the launch, and the health wait - injected. Every test is about what the
install root looks like afterwards and what the outcome record says, because
those are the two things an operator has once the daemon that started the
update is gone.

The swap hold's settle is replaced with nothing: it exists to let in-flight
hook clients clear a rename window, and a suite renaming empty directories
has no such clients to wait for.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import bundle_apply
from swe_mux.bundle_apply import (
    APP_BUNDLE,
    CLI_BUNDLE,
    MODE_REPLACE,
    MODE_SWAP,
    OUTCOME_ROLLED_BACK,
    OUTCOME_SUCCEEDED,
    OUTCOME_SWAP_FAILED,
    OUTCOME_UNHEALTHY,
    SUPERVISOR_BUNDLE,
    Layout,
    Outcome,
)
from swe_mux.bundle_metadata import bundle_metadata, write_bundle_metadata
from swe_mux.update_install import release_platform_tag


@pytest.fixture(autouse=True)
def quiet_and_instant(monkeypatch: Any) -> None:
    monkeypatch.setattr(bundle_apply, "log", lambda _message: None)

    @contextlib.contextmanager
    def no_hold(_data_dir: Path, **_kwargs: Any) -> Iterator[Path]:
        yield _data_dir

    monkeypatch.setattr(bundle_apply, "hold_bundle_swap", no_hold)
    monkeypatch.setattr(bundle_apply.time, "sleep", lambda _seconds: None)


def tree(root: Path, name: str, marker: bytes) -> Path:
    """A bundle directory with an executable and a marker saying which build it is."""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    # The app's launcher name is the host's (`swe-mux.exe` on Windows, `swe-mux`
    # elsewhere), because `apply_staged` refuses a staging tree without it and
    # CI runs this on all three hosts.
    exe = bundle_apply.app_exe_name() if name == APP_BUNDLE else f"{name}.exe"
    (directory / exe).write_bytes(b"MZ " + marker)
    (directory / "marker").write_bytes(marker)
    if name == APP_BUNDLE:
        write_bundle_metadata(
            directory,
            bundle_metadata(
                version=marker.decode(), supervisor_protocol=1, platform=release_platform_tag()
            ),
        )
    return directory


def installed(tmp_path: Path, *bundles: str) -> Layout:
    layout = Layout(tmp_path / "install")
    for name in bundles:
        tree(layout.install_root, name, b"old")
    return layout


def staged(layout: Layout, *bundles: str) -> None:
    for name in bundles:
        tree(layout.staging_root, name, b"new")


def config_for(tmp_path: Path) -> Any:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    return SimpleNamespace(data_dir=data_dir, port=1)


def marker(layout: Layout, name: str) -> bytes:
    return (layout.live(name) / "marker").read_bytes()


def result(config: Any) -> dict[str, Any]:
    return json.loads((config.data_dir / "redeploy-result.json").read_text(encoding="utf-8"))


class Machine:
    """The stop, the launch, and the health answer, recorded and scripted."""

    def __init__(self, monkeypatch: Any, *, healthy: bool | list[bool] = True) -> None:
        self.stops: list[str] = []
        self.launches: list[Path] = []
        self.answers = healthy if isinstance(healthy, list) else [healthy]
        monkeypatch.setattr(bundle_apply, "stop_for_mode", lambda _c, mode: self.stops.append(mode))
        monkeypatch.setattr(bundle_apply, "abort_if_bundle_held", lambda *_a, **_k: False)
        monkeypatch.setattr(bundle_apply, "resolve_relaunch_hidden", lambda **_k: False)

        def launch(_config: Any, layout: Layout, *, hidden: bool) -> Any:
            self.launches.append(layout.app_exe)
            return SimpleNamespace(poll=lambda: None, returncode=None)

        def wait(_config: Any, *_a: Any, **_k: Any) -> dict[str, Any] | None:
            answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
            return {"ok": True, "live_sessions": 3, "supervisor": True} if answer else None

        monkeypatch.setattr(bundle_apply, "launch_app", launch)
        monkeypatch.setattr(bundle_apply, "wait_healthy", wait)


# --- the layout ------------------------------------------------------------------


def test_the_layout_derives_every_path_from_one_root(tmp_path: Path) -> None:
    layout = Layout.for_bundle(tmp_path / "Programs" / "swe-mux" / "swe-mux")
    assert layout.install_root == tmp_path / "Programs" / "swe-mux"
    assert layout.app == layout.install_root / "swe-mux"
    assert layout.supervisor == layout.install_root / "swe-mux-supervisor"
    assert layout.cli == layout.install_root / "swe-mux-cli"
    assert layout.staging_root == layout.install_root / ".staging"
    assert layout.prev(APP_BUNDLE) == layout.install_root / "swe-mux.prev"
    assert layout.failed(CLI_BUNDLE) == layout.install_root / "swe-mux-cli.failed"
    # A checkout's layout is its `dist/`, which is where the script has always swapped.
    assert Layout.for_checkout(tmp_path / "repo").app == tmp_path / "repo" / "dist" / "swe-mux"


def test_which_bundles_move_is_decided_by_the_mode_and_by_what_was_staged(
    tmp_path: Path,
) -> None:
    layout = installed(tmp_path, APP_BUNDLE)
    staged(layout, APP_BUNDLE, SUPERVISOR_BUNDLE, CLI_BUNDLE)
    # Swap mode never moves the supervisor, whatever the archive carried: that
    # is the process holding every live session.
    assert bundle_apply.bundles_for_mode(layout, MODE_SWAP) == [APP_BUNDLE, CLI_BUNDLE]
    assert bundle_apply.bundles_for_mode(layout, MODE_REPLACE) == [
        APP_BUNDLE,
        SUPERVISOR_BUNDLE,
        CLI_BUNDLE,
    ]
    assert bundle_apply.required_bundles(MODE_SWAP) == {APP_BUNDLE}
    assert bundle_apply.required_bundles(MODE_REPLACE) == {APP_BUNDLE, SUPERVISOR_BUNDLE}
    # An archive from before the siblings were carried stages the app alone.
    bare = installed(tmp_path / "bare", APP_BUNDLE)
    staged(bare, APP_BUNDLE)
    assert bundle_apply.bundles_for_mode(bare, MODE_REPLACE) == [APP_BUNDLE]


# --- the swap ----------------------------------------------------------------------


def test_a_swap_retires_each_bundle_to_its_prev_slot(tmp_path: Path) -> None:
    layout = installed(tmp_path, APP_BUNDLE, CLI_BUNDLE)
    staged(layout, APP_BUNDLE, CLI_BUNDLE)
    ok, swapped = bundle_apply.swap_bundles(
        layout, [APP_BUNDLE, CLI_BUNDLE], required={APP_BUNDLE}, data_dir=tmp_path
    )
    assert ok and swapped == [APP_BUNDLE, CLI_BUNDLE]
    assert marker(layout, APP_BUNDLE) == b"new"
    assert marker(layout, CLI_BUNDLE) == b"new"
    assert (layout.prev(APP_BUNDLE) / "marker").read_bytes() == b"old"
    assert (layout.prev(CLI_BUNDLE) / "marker").read_bytes() == b"old"
    assert not layout.staged(APP_BUNDLE).exists()


def test_a_client_that_will_not_move_is_a_warning_and_an_app_that_will_not_is_a_failure(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = installed(tmp_path, APP_BUNDLE, CLI_BUNDLE)
    staged(layout, APP_BUNDLE, CLI_BUNDLE)
    real_replace = bundle_apply.replace_dir

    def stuck_client(source: Path, target: Path, **kwargs: Any) -> bool:
        if source.name == CLI_BUNDLE and target.name.endswith(".prev"):
            return False
        return real_replace(source, target, **kwargs)

    monkeypatch.setattr(bundle_apply, "replace_dir", stuck_client)
    ok, swapped = bundle_apply.swap_bundles(
        layout, [APP_BUNDLE, CLI_BUNDLE], required={APP_BUNDLE}, data_dir=tmp_path
    )
    # The app swapped; the client stayed put and the run went on.
    assert ok and swapped == [APP_BUNDLE]
    assert marker(layout, APP_BUNDLE) == b"new"
    assert marker(layout, CLI_BUNDLE) == b"old"

    # The same refusal on the app bundle is a failed run - and nothing that
    # already moved is left moved.
    second = installed(tmp_path / "second", APP_BUNDLE, SUPERVISOR_BUNDLE)
    staged(second, APP_BUNDLE, SUPERVISOR_BUNDLE)
    monkeypatch.setattr(bundle_apply, "force_stop_app_images", lambda: None)

    def stuck_supervisor(source: Path, target: Path, **kwargs: Any) -> bool:
        if source.name == SUPERVISOR_BUNDLE and target.name.endswith(".prev"):
            return False
        return real_replace(source, target, **kwargs)

    monkeypatch.setattr(bundle_apply, "replace_dir", stuck_supervisor)
    ok, swapped = bundle_apply.swap_bundles(
        second,
        [APP_BUNDLE, SUPERVISOR_BUNDLE],
        required={APP_BUNDLE, SUPERVISOR_BUNDLE},
        data_dir=tmp_path,
    )
    assert not ok and swapped == []
    assert marker(second, APP_BUNDLE) == b"old"
    assert marker(second, SUPERVISOR_BUNDLE) == b"old"


def test_a_rollback_restores_every_swapped_bundle_and_keeps_the_failed_trees(
    tmp_path: Path,
) -> None:
    layout = installed(tmp_path, APP_BUNDLE, SUPERVISOR_BUNDLE, CLI_BUNDLE)
    staged(layout, APP_BUNDLE, SUPERVISOR_BUNDLE, CLI_BUNDLE)
    names = [APP_BUNDLE, SUPERVISOR_BUNDLE, CLI_BUNDLE]
    ok, swapped = bundle_apply.swap_bundles(
        layout, names, required={APP_BUNDLE, SUPERVISOR_BUNDLE}, data_dir=tmp_path
    )
    assert ok and swapped == names
    assert bundle_apply.rollback_bundles(layout, swapped, data_dir=tmp_path)
    for name in names:
        assert marker(layout, name) == b"old"
        assert (layout.failed(name) / "marker").read_bytes() == b"new"
        assert not layout.prev(name).exists()


# --- the run -------------------------------------------------------------------------


def test_a_healthy_swap_records_success_and_runs_the_post_install_step(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = installed(tmp_path, APP_BUNDLE, CLI_BUNDLE)
    staged(layout, APP_BUNDLE, CLI_BUNDLE)
    config = config_for(tmp_path)
    machine = Machine(monkeypatch, healthy=True)
    outcome = Outcome(config, 1.0)
    after: list[str] = []

    code = bundle_apply.apply_staged(
        config, layout, outcome, mode=MODE_SWAP, on_success=lambda: after.append("done")
    )

    assert code == 0
    assert machine.stops == [MODE_SWAP]
    assert machine.launches == [layout.app_exe]
    assert after == ["done"]
    assert marker(layout, APP_BUNDLE) == b"new"
    assert not layout.staging_root.exists()
    record = result(config)
    assert record["outcome"] == OUTCOME_SUCCEEDED
    assert record["mode"] == MODE_SWAP
    assert record["bundles"] == [APP_BUNDLE, CLI_BUNDLE]
    assert record["swapped"] == [APP_BUNDLE, CLI_BUNDLE]
    assert "3 live session" in record["detail"]


def test_an_unhealthy_app_is_rolled_back_and_the_record_says_the_change_did_not_ship(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = installed(tmp_path, APP_BUNDLE, CLI_BUNDLE)
    staged(layout, APP_BUNDLE, CLI_BUNDLE)
    config = config_for(tmp_path)
    # The new app never turns healthy; the relaunched old one does.
    machine = Machine(monkeypatch, healthy=[False, True])
    outcome = Outcome(config, 1.0)
    after: list[str] = []

    code = bundle_apply.apply_staged(
        config, layout, outcome, mode=MODE_SWAP, on_success=lambda: after.append("done")
    )

    assert code == 1
    assert after == []
    assert marker(layout, APP_BUNDLE) == b"old"
    assert marker(layout, CLI_BUNDLE) == b"old"
    assert (layout.failed(APP_BUNDLE) / "marker").read_bytes() == b"new"
    # Stopped twice: once for the swap, once to take the unhealthy app down.
    assert machine.stops == [MODE_SWAP, MODE_SWAP]
    assert len(machine.launches) == 2
    record = result(config)
    assert record["outcome"] == OUTCOME_ROLLED_BACK
    assert "did NOT ship" in record["detail"]


def test_a_first_install_that_never_turns_healthy_is_left_in_place(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # Nothing to roll back to: moving the only app bundle aside would leave no
    # app at all, which is strictly worse than an unhealthy one.
    layout = Layout(tmp_path / "install")
    staged(layout, APP_BUNDLE)
    config = config_for(tmp_path)
    Machine(monkeypatch, healthy=False)

    code = bundle_apply.apply_staged(config, layout, Outcome(config, 1.0), mode=MODE_SWAP)

    assert code == 1
    assert marker(layout, APP_BUNDLE) == b"new"
    assert result(config)["outcome"] == OUTCOME_UNHEALTHY


def test_replace_mode_stops_with_quit_intent_and_moves_the_supervisor(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = installed(tmp_path, APP_BUNDLE, SUPERVISOR_BUNDLE, CLI_BUNDLE)
    staged(layout, APP_BUNDLE, SUPERVISOR_BUNDLE, CLI_BUNDLE)
    config = config_for(tmp_path)
    machine = Machine(monkeypatch, healthy=True)

    code = bundle_apply.apply_staged(config, layout, Outcome(config, 1.0), mode=MODE_REPLACE)

    assert code == 0
    assert machine.stops == [MODE_REPLACE]
    assert marker(layout, SUPERVISOR_BUNDLE) == b"new"
    assert result(config)["swapped"] == [APP_BUNDLE, SUPERVISOR_BUNDLE, CLI_BUNDLE]


def test_swap_mode_leaves_a_staged_supervisor_where_it_is(tmp_path: Path, monkeypatch: Any) -> None:
    layout = installed(tmp_path, APP_BUNDLE, SUPERVISOR_BUNDLE)
    staged(layout, APP_BUNDLE, SUPERVISOR_BUNDLE)
    config = config_for(tmp_path)
    Machine(monkeypatch, healthy=True)

    assert bundle_apply.apply_staged(config, layout, Outcome(config, 1.0), mode=MODE_SWAP) == 0
    assert marker(layout, SUPERVISOR_BUNDLE) == b"old"
    assert marker(layout, APP_BUNDLE) == b"new"


def test_a_staging_tree_without_an_executable_touches_nothing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = installed(tmp_path, APP_BUNDLE)
    layout.staged(APP_BUNDLE).mkdir(parents=True)
    config = config_for(tmp_path)
    machine = Machine(monkeypatch, healthy=True)

    assert bundle_apply.apply_staged(config, layout, Outcome(config, 1.0), mode=MODE_SWAP) == 1
    assert machine.stops == []
    assert marker(layout, APP_BUNDLE) == b"old"
    assert result(config)["outcome"] == "build_failed"


def test_a_required_bundle_that_will_not_swap_relaunches_the_old_app(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = installed(tmp_path, APP_BUNDLE)
    staged(layout, APP_BUNDLE)
    config = config_for(tmp_path)
    machine = Machine(monkeypatch, healthy=True)
    monkeypatch.setattr(bundle_apply, "swap_bundles", lambda *_a, **_k: (False, []))

    code = bundle_apply.apply_staged(config, layout, Outcome(config, 1.0), mode=MODE_SWAP)

    assert code == 1
    assert machine.launches == [layout.app_exe]
    assert result(config)["outcome"] == OUTCOME_SWAP_FAILED


def test_an_unknown_mode_is_a_programming_error_not_a_default(tmp_path: Path) -> None:
    layout = installed(tmp_path, APP_BUNDLE)
    config = config_for(tmp_path)
    with pytest.raises(ValueError, match="unknown apply mode"):
        bundle_apply.apply_staged(config, layout, Outcome(config, 1.0), mode="yolo")


# --- the stop, per mode ------------------------------------------------------------


def test_the_stop_matches_the_mode(monkeypatch: Any) -> None:
    calls: list[str] = []
    monkeypatch.setattr(bundle_apply, "stop_app_processes", lambda _c: calls.append("detach"))
    monkeypatch.setattr(bundle_apply, "stop_everything", lambda _c: calls.append("quit"))
    bundle_apply.stop_for_mode(None, MODE_SWAP)
    bundle_apply.stop_for_mode(None, MODE_REPLACE)
    assert calls == ["detach", "quit"]


def test_the_quit_stop_asks_for_quit_intent_then_waits_out_the_supervisor(
    tmp_path: Path, monkeypatch: Any
) -> None:
    requested: list[str] = []
    health = [True, None]
    monkeypatch.setattr(bundle_apply, "health", lambda *_a, **_k: health.pop(0) if health else None)
    monkeypatch.setattr(
        bundle_apply, "request_shutdown", lambda _c, mode: requested.append(mode) or True
    )
    monkeypatch.setattr(bundle_apply, "stop_supervisor", lambda _c: requested.append("supervisor"))
    monkeypatch.setattr(bundle_apply, "partition_app_processes", lambda: ([], []))
    bundle_apply.stop_everything(SimpleNamespace(data_dir=tmp_path, port=1))
    assert requested == ["quit", "supervisor"]


def test_the_supervisor_preflight_refuses_only_in_swap_mode(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = installed(tmp_path, APP_BUNDLE)
    config = config_for(tmp_path)
    monkeypatch.setattr(bundle_apply, "supervisor_process", lambda _c: None)
    assert bundle_apply.preflight_supervisor(config, layout, mode=MODE_SWAP, force=False) == 2
    assert bundle_apply.preflight_supervisor(config, layout, mode=MODE_SWAP, force=True) == 0
    assert bundle_apply.preflight_supervisor(config, layout, mode=MODE_REPLACE, force=False) == 0
    # A supervisor running from inside the app bundle is the fallback that the
    # rename would kill; swap mode refuses it, replace mode has consent to.
    inside = (layout.app / "swe-mux.exe", layout.app / "swe-mux.exe")
    monkeypatch.setattr(bundle_apply, "supervisor_process", lambda _c: (7, inside[0]))
    assert bundle_apply.preflight_supervisor(config, layout, mode=MODE_SWAP, force=False) == 2
    assert bundle_apply.preflight_supervisor(config, layout, mode=MODE_REPLACE, force=False) == 0
    beside = layout.supervisor / "swe-mux-supervisor.exe"
    monkeypatch.setattr(bundle_apply, "supervisor_process", lambda _c: (7, beside))
    assert bundle_apply.preflight_supervisor(config, layout, mode=MODE_SWAP, force=False) == 0


# --- the bounce --------------------------------------------------------------------


def test_a_bounce_restarts_without_swapping(tmp_path: Path, monkeypatch: Any) -> None:
    layout = installed(tmp_path, APP_BUNDLE)
    config = config_for(tmp_path)
    machine = Machine(monkeypatch, healthy=True)
    stops: list[str] = []
    monkeypatch.setattr(bundle_apply, "stop_app_processes", lambda _c: stops.append("detach"))

    assert bundle_apply.bounce(config, layout, Outcome(config, 1.0)) == 0
    assert stops == ["detach"]
    assert machine.launches == [layout.app_exe]
    assert marker(layout, APP_BUNDLE) == b"old"
    assert result(config)["outcome"] == OUTCOME_SUCCEEDED


# --- the record --------------------------------------------------------------------


def test_the_record_carries_the_facts_a_reader_needs(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    outcome = Outcome(config, 1000.0)
    outcome.describe(mode=MODE_REPLACE, version="0.3.0")
    outcome.describe(bundles=[APP_BUNDLE])
    outcome.record(OUTCOME_ROLLED_BACK, "Your change did NOT ship.", code=1)
    record = result(config)
    assert record["outcome"] == OUTCOME_ROLLED_BACK
    assert record["mode"] == MODE_REPLACE
    assert record["version"] == "0.3.0"
    assert record["bundles"] == [APP_BUNDLE]
    assert record["started_at"] == 1000.0
