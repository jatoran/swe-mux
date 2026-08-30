"""Start Menu, Desktop, and run-at-login shortcuts.

The module is deliberately split so that almost all of it is testable anywhere:
`plan_shortcuts` decides *what* to write, `render_script` decides *how*, and
`parse_script_output` decides what the result was - none of the three touches
Windows. Only `_run_powershell` does, and one test at the bottom drives it for
real (Windows-only, into `tmp_path`, never near a real Start Menu) so the script
text is proved to be valid PowerShell rather than merely proved to contain the
right substrings.

Nothing here writes to the machine's own shell folders. Every test injects
`ShortcutFolders`, so a run of the suite cannot leave a shortcut behind.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from swe_mux import shortcuts
from swe_mux.config import Config
from swe_mux.host_platform import IS_WINDOWS
from swe_mux.install_location import detect_install_location

#: The launcher a shortcut points at, spelled for the host running the suite.
#:
#: `plan_shortcuts` is pure and runs on every leg, but a `Path` renders
#: separators for the platform that is *running* - so the Windows literal this
#: used to be became one relative filename on Linux, and
#: `test_every_shortcut_points_at_the_desktop_shell_not_the_cli` asserted
#: `.name` against the whole string. The launcher keeps its `.exe`, because a
#: `.lnk` only ever points at a Windows executable; what varies is the shape of
#: the directory holding it.
_TARGET = (
    Path(r"C:\Users\ada\.local\bin\swe-mux.exe")
    if IS_WINDOWS
    else Path("/home/ada/.local/bin/swe-mux.exe")
)


def _folders(root: Path) -> shortcuts.ShortcutFolders:
    return shortcuts.ShortcutFolders(
        start_menu=root / "StartMenu" / "Programs",
        desktop=root / "Desktop",
        startup=root / "StartMenu" / "Programs" / "Startup",
    )


def _plan(root: Path, *slots: str) -> tuple[shortcuts.ShortcutSpec, ...]:
    return shortcuts.plan_shortcuts(
        slots=slots or (shortcuts.SLOT_START_MENU, shortcuts.SLOT_DESKTOP),
        folders=_folders(root),
        target=_TARGET,
        working_directory=root / "data",
        icon=f"{root / 'data' / 'icons' / 'swe-mux.ico'},0",
    )


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #


def test_the_default_plan_is_a_start_menu_entry_and_a_desktop_icon(tmp_path: Path) -> None:
    specs = _plan(tmp_path)
    assert [spec.slot for spec in specs] == ["start-menu", "desktop"]
    assert all(spec.path.name == "swe-mux.lnk" for spec in specs)
    assert specs[0].path == tmp_path / "StartMenu" / "Programs" / "swe-mux.lnk"
    assert specs[1].path == tmp_path / "Desktop" / "swe-mux.lnk"


def test_the_login_shortcut_is_the_only_one_that_starts_hidden(tmp_path: Path) -> None:
    """A login launch opens the tray; it must not throw a window at someone who
    has just signed in. That is the same `--hidden` the tray's own "Start with
    Windows" toggle writes (`desktop.startup_command`)."""
    specs = _plan(tmp_path, *shortcuts.ALL_SLOTS)
    arguments = {spec.slot: spec.arguments for spec in specs}
    assert arguments == {"start-menu": "", "desktop": "", "startup": "--hidden"}


def test_the_plan_reads_in_a_fixed_order_whatever_order_the_flags_were_typed(
    tmp_path: Path,
) -> None:
    typed = _plan(tmp_path, shortcuts.SLOT_STARTUP, shortcuts.SLOT_START_MENU)
    assert [spec.slot for spec in typed] == ["start-menu", "startup"]


def test_an_unknown_slot_is_refused_rather_than_silently_dropped(tmp_path: Path) -> None:
    with pytest.raises(shortcuts.ShortcutError, match="unknown shortcut slot"):
        shortcuts.plan_shortcuts(
            slots=("quick-launch",),
            folders=_folders(tmp_path),
            target=_TARGET,
            working_directory=tmp_path,
            icon="",
        )


def test_every_shortcut_points_at_the_desktop_shell_not_the_cli(tmp_path: Path) -> None:
    """`swe-mux`, never `mux`: a Start Menu click wants the tray and window."""
    specs = _plan(tmp_path, *shortcuts.ALL_SLOTS)
    assert {spec.target.name for spec in specs} == {"swe-mux.exe"}


def test_the_working_directory_is_the_data_dir_and_never_the_installation(
    tmp_path: Path,
) -> None:
    """A long-lived process anchored inside the installation locks that tree
    against an in-place update - the same reason `desktop.ensure_daemon` anchors
    its daemon in the data directory."""
    specs = _plan(tmp_path, *shortcuts.ALL_SLOTS)
    assert {spec.working_directory for spec in specs} == {tmp_path / "data"}


# --------------------------------------------------------------------------- #
# The script
# --------------------------------------------------------------------------- #


def test_the_install_script_sets_every_property_of_every_link(tmp_path: Path) -> None:
    specs = _plan(tmp_path, *shortcuts.ALL_SLOTS)
    script = shortcuts.render_script(specs, remove=False)
    for spec in specs:
        assert f"'{spec.path}'" in script
        assert f"$link.TargetPath = '{spec.target}'" in script
        assert f"$link.Arguments = '{spec.arguments}'" in script
        assert f"$link.WorkingDirectory = '{spec.working_directory}'" in script
        assert f"$link.IconLocation = '{spec.icon}'" in script
    assert script.count("$link.Save()") == 3


def test_the_install_script_compares_before_writing_so_a_re_run_changes_nothing(
    tmp_path: Path,
) -> None:
    script = shortcuts.render_script(_plan(tmp_path), remove=False)
    assert "$before = & $fields $old" in script
    assert "$after = & $fields $link" in script
    assert "if ($before -eq $after)" in script
    assert "'unchanged'" in script


def test_the_remove_script_deletes_and_never_creates(tmp_path: Path) -> None:
    script = shortcuts.render_script(_plan(tmp_path, *shortcuts.ALL_SLOTS), remove=True)
    assert script.count("Remove-Item -LiteralPath $path -Force") == 3
    assert "CreateShortcut" not in script
    assert "$link.Save()" not in script
    # "It was not there" is its own answer and not a failure.
    assert "'absent'" in script


def test_a_quote_in_a_path_cannot_escape_its_string(tmp_path: Path) -> None:
    """The injection guard. A Windows directory may legally contain `'`."""
    odd = tmp_path / "ada's files"
    specs = shortcuts.plan_shortcuts(
        slots=(shortcuts.SLOT_DESKTOP,),
        folders=shortcuts.ShortcutFolders(start_menu=odd, desktop=odd, startup=odd),
        target=_TARGET,
        working_directory=odd,
        icon="",
    )
    script = shortcuts.render_script(specs, remove=False)
    assert "ada''s files" in script
    assert "ada's files" not in script


def test_every_block_reports_its_own_failure_instead_of_aborting_the_run(
    tmp_path: Path,
) -> None:
    """One unwritable folder must not cost the other two their shortcuts."""
    script = shortcuts.render_script(_plan(tmp_path, *shortcuts.ALL_SLOTS), remove=False)
    assert script.count("} catch {") == 3
    assert script.count("'failed' $_.Exception.Message") == 3


# --------------------------------------------------------------------------- #
# Reading the result
# --------------------------------------------------------------------------- #


def test_outcomes_are_driven_by_the_plan_not_by_the_output(tmp_path: Path) -> None:
    """A shortcut the script never mentioned must not vanish from a report whose
    entire purpose is to say what was written."""
    specs = _plan(tmp_path, *shortcuts.ALL_SLOTS)
    output = '{"slot":"desktop","path":"D:\\\\x.lnk","action":"created","detail":""}\n'
    outcomes = shortcuts.parse_script_output(output, specs)
    assert [outcome.slot for outcome in outcomes] == ["start-menu", "desktop", "startup"]
    by_slot = {outcome.slot: outcome for outcome in outcomes}
    assert by_slot["desktop"].action == "created"
    assert by_slot["start-menu"].action == "failed"
    assert "reported nothing" in by_slot["start-menu"].detail


def test_noise_around_the_json_lines_is_ignored(tmp_path: Path) -> None:
    """PowerShell profiles and progress records print; the parser reads the
    lines it recognises and refuses to be confused by the rest."""
    specs = _plan(tmp_path, shortcuts.SLOT_DESKTOP)
    output = (
        "WARNING: something about a module\n"
        "not json at all\n"
        '{"slot":"desktop","path":"D:\\\\x.lnk","action":"unchanged","detail":""}\n'
    )
    outcomes = shortcuts.parse_script_output(output, specs)
    assert [outcome.action for outcome in outcomes] == ["unchanged"]


def test_a_failed_outcome_makes_the_whole_report_not_ok(tmp_path: Path) -> None:
    report = shortcuts.ShortcutReport(
        action="install",
        supported=True,
        outcomes=(
            shortcuts.ShortcutOutcome(slot="desktop", path=tmp_path / "a.lnk", action="created"),
            shortcuts.ShortcutOutcome(
                slot="start-menu", path=tmp_path / "b.lnk", action="failed", detail="denied"
            ),
        ),
    )
    assert report.ok is False
    assert "denied" in shortcuts.render_report(report)


def test_a_refusal_with_no_outcomes_is_not_reported_as_a_success() -> None:
    """`all([])` is True, which would turn "there was nothing to point at" into
    a clean bill of health."""
    report = shortcuts.ShortcutReport(
        action="install", supported=True, reason="no swe-mux launcher to point at"
    )
    assert report.ok is False
    assert shortcuts.render_report(report) == "no swe-mux launcher to point at"


# --------------------------------------------------------------------------- #
# The command
# --------------------------------------------------------------------------- #


def _config(tmp_path: Path) -> Config:
    return Config(data_dir=tmp_path / "data", host="127.0.0.1")


def _installed(tmp_path: Path) -> object:
    """A location whose `swe-mux` launcher exists, without needing one to."""
    scripts = tmp_path / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ("mux", "muxd", "swe-mux"):
        (scripts / (f"{name}.exe" if IS_WINDOWS else name)).write_text("", encoding="utf-8")
    return detect_install_location(
        frozen=False,
        executable=str(scripts / "python"),
        package_dir=tmp_path,
        prefix=str(tmp_path),
        base_prefix="/usr",
        scripts_dir=scripts,
        path="",
        home=tmp_path,
        environ={},
    )


def test_a_posix_host_is_told_so_cleanly_rather_than_failing_obscurely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asserted on every host, including Windows.

    The POSIX branch of a Windows-only feature is exactly the code that ships
    untested and then produces a traceback for the first Linux user, so the gate
    exercises it wherever it runs.
    """
    monkeypatch.setattr(shortcuts, "IS_WINDOWS", False)
    report = shortcuts.apply_shortcuts(config=_config(tmp_path), folders=_folders(tmp_path))
    assert report.supported is False
    assert "Windows" in report.reason
    assert report.outcomes == ()
    # It still says how to start swe-mux here, which is the question behind the
    # one that could not be answered.
    assert "muxd" in report.reason
    assert shortcuts.render_report(report) == report.reason
    assert not (tmp_path / "Desktop").exists(), "an unsupported host writes nothing"


def test_an_install_with_no_desktop_launcher_is_refused_with_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shortcuts, "IS_WINDOWS", True)
    empty = detect_install_location(
        frozen=False,
        executable=str(tmp_path / "python"),
        package_dir=tmp_path,
        prefix=str(tmp_path),
        base_prefix="/usr",
        scripts_dir=tmp_path / "nowhere",
        path="",
        home=tmp_path,
        environ={},
    )
    report = shortcuts.apply_shortcuts(
        config=_config(tmp_path), folders=_folders(tmp_path), location=empty
    )
    assert report.ok is False
    assert "no `swe-mux` launcher" in report.reason
    assert "--where" in report.reason


def test_removal_addresses_every_slot_whatever_was_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Undo has to reach a login entry an earlier run added and the user forgot."""
    monkeypatch.setattr(shortcuts, "IS_WINDOWS", True)
    seen: list[str] = []

    def runner(script: str) -> str:
        seen.append(script)
        return "".join(
            f'{{"slot":"{slot}","path":"x","action":"removed","detail":""}}\n'
            for slot in shortcuts.ALL_SLOTS
        )

    report = shortcuts.apply_shortcuts(
        config=_config(tmp_path),
        slots=(shortcuts.SLOT_DESKTOP,),
        remove=True,
        folders=_folders(tmp_path),
        location=_installed(tmp_path),  # type: ignore[arg-type]
        runner=runner,
    )
    assert [outcome.slot for outcome in report.outcomes] == list(shortcuts.ALL_SLOTS)
    assert report.ok is True
    assert "Remove-Item" in seen[0]


def test_a_runner_that_raises_becomes_a_failed_row_per_shortcut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shortcuts, "IS_WINDOWS", True)

    def runner(_script: str) -> str:
        raise OSError("powershell.exe not found")

    report = shortcuts.apply_shortcuts(
        config=_config(tmp_path),
        folders=_folders(tmp_path),
        location=_installed(tmp_path),  # type: ignore[arg-type]
        runner=runner,
    )
    assert report.ok is False
    assert {outcome.action for outcome in report.outcomes} == {"failed"}
    assert all("powershell.exe not found" in outcome.detail for outcome in report.outcomes)


def test_every_outcome_reaches_the_lifecycle_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This command runs where no daemon logging exists, so the durable record of
    who put a link where is the ledger the tray and daemon already share."""
    monkeypatch.setattr(shortcuts, "IS_WINDOWS", True)

    def runner(_script: str) -> str:
        return "".join(
            f'{{"slot":"{slot}","path":"C:\\\\{slot}.lnk","action":"created","detail":""}}\n'
            for slot in (shortcuts.SLOT_START_MENU, shortcuts.SLOT_DESKTOP)
        )

    config = _config(tmp_path)
    shortcuts.apply_shortcuts(
        config=config,
        folders=_folders(tmp_path),
        location=_installed(tmp_path),  # type: ignore[arg-type]
        runner=runner,
    )
    written = (config.data_dir / "lifecycle.log").read_text(encoding="utf-8")
    assert "shortcut install start-menu: created" in written
    assert "shortcut install desktop: created" in written


def test_an_unimportable_shell_is_noted_on_install_and_not_on_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shortcut that opens nothing is worth a sentence; a removed one is not.

    The note's wording changed with the 2026-08-30 dependency move and the
    change is the assertion: it used to say "the `desktop` extra is not
    installed" and hand out a command to add it, which after the move sends
    someone to pass a flag that adds nothing. A missing module here means a
    partially-installed environment, so the sentence names the modules and a
    reinstall.
    """
    monkeypatch.setattr(shortcuts, "IS_WINDOWS", True)
    monkeypatch.setattr(shortcuts, "_missing_shell_modules", lambda: ("pystray",))

    def runner(_script: str) -> str:
        return '{"slot":"desktop","path":"x","action":"created","detail":""}\n'

    common = {
        "config": _config(tmp_path),
        "folders": _folders(tmp_path),
        "location": _installed(tmp_path),
        "runner": runner,
        "slots": (shortcuts.SLOT_DESKTOP,),
    }
    installed = shortcuts.apply_shortcuts(**common)  # type: ignore[arg-type]
    note = next(note for note in installed.notes if "pystray" in note)
    assert "cannot import pystray" in note
    assert "extra" not in note.replace("rather than an extra nobody chose", "")
    removed = shortcuts.apply_shortcuts(remove=True, **common)  # type: ignore[arg-type]
    assert removed.notes == ()


# --------------------------------------------------------------------------- #
# The icon
# --------------------------------------------------------------------------- #


def test_the_icon_is_rendered_into_the_data_dir_because_the_wheel_ships_none(
    tmp_path: Path,
) -> None:
    """`packaging/swe-mux.ico` is generated at build time under `packaging/`,
    which the wheel does not carry - so the mark is drawn from the same
    `create_tray_image` the tray uses, once, and reused after that."""
    icon, detail = shortcuts.ensure_icon(tmp_path)
    written = shortcuts.icon_path(tmp_path)
    assert written.is_file()
    assert icon == f"{written},0"
    assert detail.startswith("wrote ")
    again, detail_again = shortcuts.ensure_icon(tmp_path)
    assert again == icon
    assert detail_again.startswith("reused ")


def test_a_frozen_bundle_uses_the_icon_pyinstaller_embedded(tmp_path: Path) -> None:
    exe = tmp_path / "swe-mux.exe"
    icon, detail = shortcuts.ensure_icon(tmp_path, frozen_executable=exe)
    assert icon == f"{exe},0"
    assert "embedded" in detail
    assert not shortcuts.icon_path(tmp_path).exists(), "nothing is drawn for a bundle"


def test_an_icon_that_cannot_be_written_leaves_a_working_shortcut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An icon is the least important thing about a shortcut; an empty
    `IconLocation` inherits the target executable's own."""

    def boom(_size: int = 64) -> object:
        raise RuntimeError("Pillow is broken")

    monkeypatch.setattr("swe_mux.desktop.create_tray_image", boom)
    icon, detail = shortcuts.ensure_icon(tmp_path)
    assert icon == ""
    assert "Pillow is broken" in detail


# --------------------------------------------------------------------------- #
# The one part that needs Windows
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not IS_WINDOWS, reason="WScript.Shell shell links are Windows-only")
def test_powershell_really_writes_reuses_and_removes_a_shell_link(tmp_path: Path) -> None:
    """The end-to-end proof, into `tmp_path` and never near a real shell folder.

    Everything above asserts the script's *text*; this asserts that Windows
    accepts it. It is the only test that can catch a syntax error, a COM property
    that does not exist, or an idempotence comparison that never matches - and
    the last of those is the one that would silently rewrite a user's Start Menu
    on every run.
    """
    config = _config(tmp_path)
    folders = _folders(tmp_path)
    location = detect_install_location()
    created = shortcuts.apply_shortcuts(
        config=config, slots=shortcuts.ALL_SLOTS, folders=folders, location=location
    )
    assert created.ok, shortcuts.render_report(created)
    assert {outcome.action for outcome in created.outcomes} == {"created"}
    for outcome in created.outcomes:
        assert outcome.path.is_file()

    again = shortcuts.apply_shortcuts(
        config=config, slots=shortcuts.ALL_SLOTS, folders=folders, location=location
    )
    assert {outcome.action for outcome in again.outcomes} == {"unchanged"}

    removed = shortcuts.apply_shortcuts(
        config=config, folders=folders, remove=True, location=location
    )
    assert {outcome.action for outcome in removed.outcomes} == {"removed"}
    assert not any(outcome.path.exists() for outcome in removed.outcomes)

    absent = shortcuts.apply_shortcuts(
        config=config, folders=folders, remove=True, location=location
    )
    assert {outcome.action for outcome in absent.outcomes} == {"absent"}


@pytest.mark.skipif(not IS_WINDOWS, reason="known folders are a Windows shell concept")
def test_the_shell_answers_for_all_three_known_folders_on_this_host() -> None:
    """The env-var layout is a fallback, and a silent fall back to it would put a
    Desktop shortcut in the un-redirected directory on any machine whose Desktop
    OneDrive has moved - invisible to the person who asked for it."""
    folders = shortcuts.resolve_folders()
    assert set(folders.resolved_by_shell) == set(shortcuts.ALL_SLOTS)
    assert folders.startup.name == "Startup"
    assert folders.start_menu.name == "Programs"
