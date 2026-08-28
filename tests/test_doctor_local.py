"""The degraded `mux doctor` report that runs when no daemon answers.

Nothing here starts, reaches, or needs a daemon - which is the point: the report
under test exists for the machine where one will not start, so a test that needed
one would be testing the wrong thing. Every check is exercised against an injected
`Config` under `tmp_path`, so the suite never touches the real data directory.
"""

from __future__ import annotations

import socket
import sqlite3
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import doctor, doctor_local
from swe_mux.config import Config
from swe_mux.harness import AGENT_BACKENDS

_AGENT = next(iter(AGENT_BACKENDS))

_LOCAL_STATUSES = {"ok", "warn", "fail", "unavailable", "unchecked"}


def _config(tmp_path: Path, **overrides: Any) -> Config:
    return Config(data_dir=tmp_path, host="127.0.0.1", **overrides)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    return port


# --------------------------------------------------------------------------- #
# The contract the whole module exists for
# --------------------------------------------------------------------------- #


def test_the_local_report_names_itself_degraded_and_names_the_unreachable_daemon() -> None:
    report = doctor_local.build_local_doctor_report(
        config=None,
        config_error=None,
        unreachable_url="http://127.0.0.1:8765",
        unreachable_detail="connection refused",
        now=1000.0,
        checks=[],
    )
    assert report["mode"] == "local"
    # `ok` keeps the daemon report's meaning (nothing failed); `complete` is the
    # field that says the report is partial, so neither has to carry both facts.
    assert report["complete"] is False
    assert report["daemon"] == {
        "reachable": False,
        "url": "http://127.0.0.1:8765",
        "detail": "connection refused",
    }


def test_a_check_that_did_not_run_is_neither_healthy_nor_unavailable() -> None:
    """The bug the `unchecked` status exists to prevent.

    Folding a skipped check into `ok` claims health nobody measured; folding it
    into `unavailable` claims a capability was measured absent. Both turn a
    degraded report into a confident wrong one.
    """
    rows = doctor_local._unchecked_rows()
    assert rows, "the local report must say what it could not check"
    assert {row["status"] for row in rows} == {"unchecked"}
    assert all(row["detail"] for row in rows)


def test_the_summary_counts_unchecked_rows_separately() -> None:
    report = doctor_local.build_local_doctor_report(
        config=None,
        config_error=None,
        unreachable_url="http://127.0.0.1:8765",
        unreachable_detail="",
        now=1000.0,
        checks=[
            *doctor_local._unchecked_rows(),
            doctor._check(
                id="x",
                category="install",
                title="t",
                status="ok",
                severity="info",
                detail="d",
            ),
        ],
    )
    summary = report["summary"]
    assert summary["unchecked"] == len(doctor_local._unchecked_rows())
    assert summary["ok"] == 1
    assert summary["unavailable"] == 0
    assert report["ok"] is True


def test_every_category_the_daemon_report_emits_is_covered_or_declared_unchecked(
    tmp_path: Path,
) -> None:
    """The anti-drift guard.

    A category added to `build_doctor_report` that the local report neither
    answers nor declares would simply vanish from the degraded report, which is
    the silent half of the defect this work fixes. Reconciled against the
    categories the remote builder actually produces rather than a hand-copied
    list.
    """
    remote = doctor.build_doctor_report(**_remote_sources())
    remote_categories = {check["category"] for check in remote["checks"]}
    declared = {category for category, _, _, _ in doctor_local._DAEMON_ONLY}
    local = _local_categories(tmp_path)
    missing = remote_categories - declared - local
    assert not missing, f"categories the local report neither answers nor declares: {missing}"


def _local_categories(tmp_path: Path) -> set[str]:
    checks = doctor_local.collect_local_checks(
        config=_config(tmp_path, port=_free_port()),
        config_error=None,
        target_url="http://127.0.0.1:1",
    )
    assert {check["status"] for check in checks} <= _LOCAL_STATUSES
    assert {check["severity"] for check in checks} <= {"critical", "optional", "info"}
    return {check["category"] for check in checks}


def test_the_local_report_runs_end_to_end_with_no_daemon(tmp_path: Path) -> None:
    report = doctor_local.build_local_doctor_report(
        config=_config(tmp_path, port=_free_port()),
        config_error=None,
        unreachable_url="http://127.0.0.1:1",
        unreachable_detail="connection refused",
        now=1000.0,
    )
    ids = {check["id"] for check in report["checks"]}
    # The install-integrity spine: each of these is a way the daemon fails before
    # it can serve a request, which is the window this report is the only one for.
    assert {
        "install.python",
        "install.imports",
        "install.config",
        "install.frontend",
        "install.data_dir",
        "install.database",
        "install.port",
        "install.pty",
        "install.supervisor_bundle",
    } <= ids
    assert sum(report["summary"].values()) == len(report["checks"])


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #


def test_minimum_python_matches_requires_python() -> None:
    """The restated floor cannot drift from the declared one.

    `MINIMUM_PYTHON` is restated in source because a wheel carries no
    `pyproject.toml`, and a restatement with no reconciliation is the thing that
    goes stale.
    """
    root = Path(__file__).resolve().parent.parent
    declared = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    requires = declared["project"]["requires-python"]
    floor = ".".join(str(part) for part in doctor_local.MINIMUM_PYTHON)
    assert requires == f">={floor}"


def test_a_python_below_the_floor_is_a_critical_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor_local.sys, "version_info", (3, 11, 9, "final", 0))
    monkeypatch.delattr(doctor_local.sys, "frozen", raising=False)
    check = doctor_local._python_check()
    assert check["status"] == "fail"
    assert check["severity"] == "critical"
    assert "3.12" in check["remedy"]


def test_a_frozen_build_never_blames_its_bundled_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user cannot act on advice about an interpreter they do not control."""
    monkeypatch.setattr(doctor_local.sys, "version_info", (3, 11, 9, "final", 0))
    monkeypatch.setattr(doctor_local.sys, "frozen", True, raising=False)
    check = doctor_local._python_check()
    assert check["status"] == "ok"
    assert check["remedy"] is None


def test_a_failing_import_reports_the_real_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(name: str) -> Any:
        raise ImportError("No module named 'aiohttp'")

    monkeypatch.setattr(doctor_local.importlib, "import_module", _boom)
    check = doctor_local._imports_check()
    assert check["status"] == "fail"
    assert "aiohttp" in check["detail"]
    assert check["remedy"]


def test_a_config_that_does_not_load_is_a_failing_check() -> None:
    check = doctor_local._config_check(None, "TOMLDecodeError: expected '='")
    assert check["status"] == "fail"
    assert check["severity"] == "critical"
    assert "TOMLDecodeError" in check["detail"]


def test_a_loadable_config_names_its_path(tmp_path: Path) -> None:
    check = doctor_local._config_check(_config(tmp_path), None)
    assert check["status"] == "ok"
    assert str(tmp_path) in check["detail"]


def test_a_missing_frontend_in_an_installed_copy_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(doctor_local, "_package_root", lambda: tmp_path)
    monkeypatch.setattr(doctor_local, "_source_checkout_root", lambda: None)
    check = doctor_local._frontend_check()
    assert check["status"] == "fail"
    assert "packaging fault" in check["remedy"]


def test_a_missing_frontend_in_a_checkout_only_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A fresh clone legitimately has none: the bundle is gitignored build output."""
    monkeypatch.setattr(doctor_local, "_package_root", lambda: tmp_path)
    monkeypatch.setattr(doctor_local, "_source_checkout_root", lambda: tmp_path)
    check = doctor_local._frontend_check()
    assert check["status"] == "warn"
    assert "npm" in check["remedy"]


def test_a_present_frontend_bundle_reports_its_build_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    static = tmp_path / "static"
    static.mkdir()
    build_id = "a" * 64
    (static / "index.html").write_text(
        f'<html><head><meta name="ui-build" content="{build_id}"></head></html>',
        encoding="utf-8",
    )
    monkeypatch.setattr(doctor_local, "_package_root", lambda: tmp_path)
    check = doctor_local._frontend_check()
    assert check["status"] == "ok"
    assert build_id[:12] in check["detail"]


def test_an_unwritable_data_directory_is_a_critical_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(doctor_local, "_writable", lambda _: "PermissionError: denied")
    check = doctor_local._data_dir_check(_config(tmp_path))
    assert check["status"] == "fail"
    assert check["severity"] == "critical"
    assert "MUX_DATA_DIR" in check["remedy"]


def test_a_data_directory_that_does_not_exist_yet_is_fine(tmp_path: Path) -> None:
    check = doctor_local._data_dir_check(_config(tmp_path / "not-created"))
    assert check["status"] == "ok"
    assert "first run will create it" in check["detail"]


def test_a_writable_data_directory_passes(tmp_path: Path) -> None:
    check = doctor_local._data_dir_check(_config(tmp_path))
    assert check["status"] == "ok"
    assert not any(tmp_path.iterdir()), "the probe must leave nothing behind"


def test_an_absent_database_is_not_a_fault(tmp_path: Path) -> None:
    check = doctor_local._database_check(_config(tmp_path))
    assert check["status"] == "ok"
    assert not (tmp_path / "mux.db").exists(), "the probe must not create the store"


def test_a_healthy_database_opens_and_counts_its_schema(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "mux.db")
    connection.execute("CREATE TABLE t (a int)")
    connection.commit()
    connection.close()
    check = doctor_local._database_check(_config(tmp_path))
    assert check["status"] == "ok"
    assert "1 object(s)" in check["detail"]


def test_a_corrupt_database_is_a_critical_failure(tmp_path: Path) -> None:
    (tmp_path / "mux.db").write_bytes(b"this is not a sqlite file" * 64)
    check = doctor_local._database_check(_config(tmp_path))
    assert check["status"] == "fail"
    assert check["severity"] == "critical"
    assert check["remedy"]


def test_a_free_port_passes(tmp_path: Path) -> None:
    port = _free_port()
    check = doctor_local._port_check(
        _config(tmp_path, port=port), target_url=f"http://127.0.0.1:{port}"
    )
    assert check["status"] == "ok"
    assert "free" in check["detail"]


def test_a_port_held_by_something_else_is_a_critical_failure(tmp_path: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = int(listener.getsockname()[1])
        check = doctor_local._port_check(
            _config(tmp_path, port=port), target_url=f"http://127.0.0.1:{port}"
        )
    assert check["status"] == "fail"
    assert check["severity"] == "critical"
    assert str(port) in check["remedy"]


def test_a_busy_local_port_is_not_blamed_for_a_remote_target(tmp_path: Path) -> None:
    """`--url`/`MUX_URL` can point elsewhere; a local port is then not the cause."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = int(listener.getsockname()[1])
        check = doctor_local._port_check(
            _config(tmp_path, port=port), target_url="http://other-host:8765"
        )
    assert check["status"] == "ok"
    assert "not the cause" in check["detail"]


def test_an_unimportable_pty_backend_is_a_critical_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(name: str) -> Any:
        raise ImportError("DLL load failed while importing winpty")

    monkeypatch.setattr(doctor_local.importlib, "import_module", _boom)
    check = doctor_local._pty_check()
    assert check["status"] == "fail"
    assert check["severity"] == "critical"
    assert "winpty" in check["detail"]


def test_the_supervisor_bundle_is_not_a_fault_in_a_source_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source daemons launch the supervisor from source, so absent is correct."""
    monkeypatch.delattr(doctor_local.sys, "frozen", raising=False)
    check = doctor_local._supervisor_bundle_check()
    assert check["status"] == "ok"
    assert check["severity"] == "info"


def test_a_frozen_app_without_its_supervisor_bundle_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor_local.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        "swe_mux.supervisor_client.dedicated_supervisor_exe", lambda **_: None
    )
    check = doctor_local._supervisor_bundle_check()
    assert check["status"] == "warn"
    assert check["severity"] == "critical"
    assert "reaps live sessions" in check["remedy"]


def test_an_absent_extra_is_unavailable_and_carries_its_install_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor_local, "_module_resolves", lambda _: False)
    monkeypatch.delattr(doctor_local.sys, "frozen", raising=False)
    checks = {check["id"]: check for check in doctor_local._extras_checks()}
    voice = checks["extra.voice-local"]
    # Not installed is not broken, so it must not read as a warning.
    assert voice["status"] == "unavailable"
    assert voice["severity"] == "optional"
    assert voice["remedy"] == "uv sync --extra voice-local"


def test_preview_capture_is_reported_once_by_the_asset_row_that_knows_more() -> None:
    """W9's capability row subsumes an extras row, so there must not be both.

    `capture_capability()` separates "the extra is missing" from "the extra is
    installed and has no Chromium" and carries the right command for each; a
    second `extra.preview-capture` row would answer half that question twice.
    """
    assert "preview-capture" not in {name for name, _, _, _ in doctor_local._EXTRAS}


def test_a_present_extra_reports_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_local, "_module_resolves", lambda _: True)
    checks = {check["id"]: check for check in doctor_local._extras_checks()}
    assert checks["extra.voice-local"]["status"] == "ok"


def test_a_frozen_app_is_not_told_to_uv_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """Extras are fixed at build time there, so `uv sync` is advice about elsewhere."""
    monkeypatch.setattr(doctor_local, "_module_resolves", lambda _: False)
    monkeypatch.setattr(doctor_local.sys, "frozen", True, raising=False)
    checks = {check["id"]: check for check in doctor_local._extras_checks()}
    assert "uv sync" not in checks["extra.voice-local"]["remedy"]
    assert "Rebuild" in checks["extra.voice-local"]["remedy"]


def test_the_first_use_assets_are_reported_without_a_daemon(tmp_path: Path) -> None:
    """W9's rows need no `VoiceService`; the local report builds the same ones.

    `capture_capability()` is an import plus a filesystem read and both model
    stores answer from a data directory, so the daemon-side gatherer is the only
    part that needed a running process.
    """
    checks = doctor_local._optional_asset_local_checks(_config(tmp_path))
    ids = {check["id"] for check in checks}
    assert "optional_asset:preview_capture" in ids
    assert any(check_id.startswith("optional_asset:voice_whisper:") for check_id in ids)
    # None of these is a fault, however absent: severity is optional throughout.
    assert {check["severity"] for check in checks} == {"optional"}


def test_a_failing_asset_probe_says_so_rather_than_reporting_absence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """This report runs on broken installs, so a raise must not become "absent"."""

    def _boom(_config: Any) -> Any:
        raise RuntimeError("huggingface_hub is half-installed")

    monkeypatch.setattr(doctor_local, "_optional_asset_rows_local", _boom)
    checks = doctor_local._optional_asset_local_checks(_config(tmp_path))
    assert [check["status"] for check in checks] == ["unchecked"]
    assert "huggingface_hub" in checks[0]["detail"]


def test_the_config_loader_returns_its_failure_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError("port must be an integer")

    monkeypatch.setattr("swe_mux.config.load_config", _boom)
    config, error = doctor_local.load_config_for_doctor()
    assert config is None
    assert error is not None
    assert "port must be an integer" in error


# --------------------------------------------------------------------------- #
# A remote report exercising every category the builder can emit
# --------------------------------------------------------------------------- #


def _remote_sources() -> dict[str, Any]:
    return {
        "health": {
            "ok": True,
            "version": "0.1.0",
            "live_sessions": 1,
            "ui_build_id": "abcdef123456",
            "supervisor_state": "connected",
            "supervisor_unadopted": 0,
        },
        "remote": {
            "tailnet_enabled": True,
            "connection_state": "connected",
            "device_name": "host.ts.net",
            "connection_detail": "Connected.",
            "serve_configured": True,
            "serve_url": "https://host.ts.net/",
        },
        "firewall": {
            "supported": True,
            "inspection_available": True,
            "needs_repair": False,
            "detail": "Inbound admitted.",
        },
        "prerequisites": [
            {
                "id": "git",
                "label": "Git",
                "present": True,
                "path": "/usr/bin/git",
                "purpose": "worktrees",
                "install_command": "winget install Git.Git",
            }
        ],
        "status_health": {
            "alarm": False,
            "identity_collisions": [],
            "classifier_blind_sessions": [],
            "stuck_sessions": [],
        },
        "background": {"degraded": [], "total_faults": 0},
        "harnesses": {
            "harnesses": [
                {
                    "name": _AGENT,
                    "display_name": "Agent",
                    "installed": True,
                    "cli_version": "1.2.3",
                    "version_untested": False,
                    "level": "controlled",
                    "resolved_path": "/bin/agent",
                    "capabilities": {},
                }
            ]
        },
        "freshness": [],
        "platform": {"system": "win32", "python": "3.12.0", "frozen": True},
        "daemon": {"host": "127.0.0.1", "port": 8765},
        "now": 1000.0,
        "wsl_bridges": [
            {
                "distro": "Ubuntu",
                "enabled": True,
                "available": True,
                "installed": True,
                "harnesses": [{"name": _AGENT}],
                "reasons": [],
            }
        ],
        "optional_assets": [
            {
                "id": "preview_capture",
                "label": "Preview capture (Playwright + Chromium)",
                "state": "ready",
                "detail": "installed",
                "remedy": None,
            }
        ],
    }


def test_the_remote_report_still_produces_no_unchecked_rows() -> None:
    """A daemon that answered has an answer for every check it runs."""
    remote = doctor.build_doctor_report(**_remote_sources())
    assert "unchecked" not in remote["summary"]
    assert all(check["status"] != "unchecked" for check in remote["checks"])
    assert "mode" not in remote, "the daemon report's payload must stay as it was"


def test_observation_freshness_still_reads_agent_sessions() -> None:
    """A guard that the shared module is untouched, not a new behaviour."""
    record = SimpleNamespace(
        id="s1",
        name="one",
        backend=_AGENT,
        observation_stale_since=900.0,
        observation_diagnostic=None,
    )
    session = SimpleNamespace(record=record, observation_stale_reason="transcript_missing")
    rows = doctor.observation_freshness([session], now=1000.0)
    assert rows[0]["delivery_blocking"] is True
