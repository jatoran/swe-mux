"""The Windows installer's contracts with the code it installs.

Three of them are contracts in the strong sense - something else reads the thing
the installer produced and decides on it - and each has a failure mode that is
invisible until a real machine runs a real installer:

- **The sibling layout.** `supervisor_client.dedicated_supervisor_exe` resolves
  the PTY supervisor two directories above the app executable, so flattening the
  two bundles into one directory makes a frozen daemon silently fall back to
  `--supervisor-child` and re-create the file-lock collision the separate bundle
  exists to prevent.
- **The login registration.** The tray decides whether "Start with Windows" is
  on by comparing the registry value against `desktop.startup_command()`
  *exactly*, so an installer that writes a different string produces a checkbox
  that reads off while the app autostarts.
- **The artifact name.** The release publishes the installer under
  `update_install.release_installer_name`, which has to stay unmistakably not
  the portable archive the updater looks up by name.

The `.iss` is read as source text on purpose. It is Pascal and a declarative
section list, neither of which this suite can execute, so what is checked is that
the strings the contract depends on are present and that the Python halves still
say what the script was written against - which is exactly the class of drift a
compile on a runner would not catch either.
"""

from __future__ import annotations

import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))

import build_installer  # noqa: E402
import package_desktop_release  # noqa: E402

from swe_mux.bundle_archive import (  # noqa: E402
    ARCHIVE_ROOT,
    TAR_GZ_SUFFIX,
    ZIP_SUFFIX,
    ArchiveError,
    archive_suffix,
    extract_bundle,
    read_archive_metadata,
)
from swe_mux.bundle_metadata import bundle_metadata, write_bundle_metadata  # noqa: E402
from swe_mux.desktop import RUN_KEY, RUN_VALUE, startup_command  # noqa: E402
from swe_mux.supervisor_client import dedicated_supervisor_exe  # noqa: E402
from swe_mux.update_install import (  # noqa: E402
    release_archive_name,
    release_installer_name,
    release_platform_tag,
)

SCRIPT = Path(__file__).resolve().parents[1] / "packaging" / "installer" / "swe-mux.iss"


def script_text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _icon_that_exists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point `build_installer.ICON` at a file, because the real one is build output.

    `packaging/swe-mux.ico` is gitignored (`.gitignore`) and rendered by
    `build_desktop.build_app_bundle`, so it exists in a checkout that has built the
    app and in no other - the development host has one, a fresh worktree and every
    CI runner do not. Four tests below reach `_prepare` for reasons that have
    nothing to do with the icon, and without this they stop at its guard and assert
    against *that* message instead of the platform refusal or the signtool block
    they were written for. They passed on the host that built the bundle and would
    have failed on the first runner: the icon is scaffolding here, not the subject.

    The guard itself is still covered, by
    `test_a_missing_icon_is_named_rather_than_left_to_iscc`, which points `ICON` at
    an absent path from inside the test body and so overrides this.
    """
    icon = tmp_path / "swe-mux.ico"
    icon.write_bytes(b"\x00\x00\x01\x00 fake icon")
    monkeypatch.setattr(build_installer, "ICON", icon)


#: The only platform an installer exists for. Named as a constant rather than
#: read from `release_platform_tag()` wherever the *installer* is the subject:
#: `build_installer` refuses on every other host and would then be answering a
#: different question than the one the test asked. The tests that are about the
#: refusal itself still use the live tag.
WINDOWS = "windows-x64"


def as_windows_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Describe a Windows build host, so a compile can be asserted from any leg.

    `build_installer` produces nothing off Windows by design - there is no
    `.exe` installer for Linux or macOS to name - so the compiler-command tests
    below would otherwise stop at that refusal on two of the three CI legs, and
    each would then assert its `SystemExit` message instead of the signing block
    it was written for. Sound to override here and nowhere near a path: the tag
    is an opaque string that selects the artifact name, and everything the tests
    touch afterwards (`tmp_path`, the recorded argument list) is this host's.
    """
    monkeypatch.setattr(build_installer, "release_platform_tag", lambda: WINDOWS)


def make_bundle(root: Path, *, version: str = "0.9.0", platform: str = WINDOWS) -> Path:
    """A minimally believable built bundle: an executable and its `bundle.json`."""
    (root / "_internal").mkdir(parents=True, exist_ok=True)
    (root / "swe-mux.exe").write_bytes(b"MZ fake")
    (root / "_internal" / "base_library.zip").write_bytes(b"PK fake")
    write_bundle_metadata(
        root,
        bundle_metadata(version=version, supervisor_protocol=1, platform=platform),
    )
    return root


# --- the artifact name --------------------------------------------------------


def test_the_installer_is_named_so_it_cannot_be_mistaken_for_the_archive() -> None:
    assert release_installer_name("1.2.3", "windows-x64") == "swe-mux-1.2.3-windows-x64-setup.exe"
    # The updater looks its own artifact up by exact name, so the two names in a
    # release must not be able to collide under any version string.
    for version in ("1.2.3", "0.1.0a1", "10.0.0"):
        installer = release_installer_name(version, "windows-x64")
        assert installer != release_archive_name(version, "windows-x64")
        assert installer is not None and installer.endswith(".exe")


def test_a_platform_with_no_installer_answers_none_rather_than_inventing_one() -> None:
    # A guessed `...-linux-x64-setup.exe` would be a name no release will ever
    # carry, so a caller looking for one would report a thing missing that was
    # never promised.
    assert release_installer_name("1.2.3", "linux-x64") is None
    assert release_installer_name("1.2.3", "macos-arm64") is None


def test_the_version_resource_takes_four_integers_and_the_display_version_does_not() -> None:
    assert build_installer.file_version("0.1.0") == "0.1.0.0"
    assert build_installer.file_version("1.2.3.4") == "1.2.3.4"
    # A prerelease has no honest four-integer ordering, so the segment is dropped
    # rather than encoded; the display version keeps it.
    assert build_installer.file_version("0.2.0a1") == "0.2.0.0"
    assert build_installer.file_version("1.0.0+local") == "1.0.0.0"
    with pytest.raises(SystemExit):
        build_installer.file_version("nonsense")


# --- the layout the running daemon depends on ---------------------------------


def test_the_installer_lays_the_two_bundles_out_as_siblings(tmp_path: Path) -> None:
    text = script_text()
    assert 'DestDir: "{app}\\swe-mux"' in text
    assert 'DestDir: "{app}\\swe-mux-supervisor"' in text
    # And that layout is the one the resolver actually walks: an app exe at
    # `<app>/swe-mux/swe-mux.exe` must find `<app>/swe-mux-supervisor/...`.
    app = tmp_path / "Programs" / "swe-mux"
    exe = app / "swe-mux" / "swe-mux.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    supervisor = app / "swe-mux-supervisor" / "swe-mux-supervisor.exe"
    supervisor.parent.mkdir(parents=True)
    supervisor.write_bytes(b"MZ")
    found = dedicated_supervisor_exe(executable=str(exe), frozen=True, environ={})
    assert found == supervisor.resolve()


def test_flattening_the_bundles_would_lose_the_supervisor(tmp_path: Path) -> None:
    # The failure this layout prevents, asserted so the reason survives: with
    # both executables in one directory the resolver finds nothing and the daemon
    # falls back to sharing the app image.
    flat = tmp_path / "swe-mux"
    flat.mkdir()
    (flat / "swe-mux.exe").write_bytes(b"MZ")
    (flat / "swe-mux-supervisor.exe").write_bytes(b"MZ")
    assert (
        dedicated_supervisor_exe(
            executable=str(flat / "swe-mux.exe"), frozen=True, environ={}
        )
        is None
    )


# --- the login registration ---------------------------------------------------


def quote_arg(value: str) -> str:
    """The `.iss`'s `QuoteArg`, in Python, over the domain it is applied to."""
    return f'"{value}"' if (" " in value or "\t" in value) else value


@pytest.mark.parametrize(
    "app_dir",
    [r"C:\Users\a\AppData\Local\Programs\swe-mux", r"C:\Program Files\swe mux"],
)
def test_the_installer_reproduces_the_startup_command_the_tray_compares_against(
    app_dir: str,
) -> None:
    # `desktop.startup_enabled` is an exact string comparison, so a value the
    # installer writes with different quoting or a different argument order reads
    # as "Start with Windows is off" while the app autostarts anyway.
    exe = f"{app_dir}\\swe-mux\\swe-mux.exe"
    config = r"C:\Users\a\.mux\config.toml"
    reproduced = f"{quote_arg(exe)} --hidden --config {quote_arg(config)}"
    assert reproduced == startup_command(Path(config), executable=exe, frozen=True)


def test_the_startup_command_the_script_builds_is_still_the_shape_python_writes() -> None:
    text = script_text()
    # Argument order and spacing are part of the reproduction above; if
    # `startup_command` grows a flag, this fails beside the assertion that
    # pinned the old shape rather than shipping a silently-wrong registry value.
    assert "' --hidden --config '" in text
    assert "QuoteArg(ExpandConstant('{app}\\swe-mux\\swe-mux.exe'))" in text
    assert "QuoteArg(ConfigPath())" in text
    # One key, one value name, and they are the tray's own.
    assert RUN_KEY in text
    assert f'ValueName: "{RUN_VALUE}"' in text
    assert f"'{RUN_KEY}', '{RUN_VALUE}'" in text


def test_the_login_registration_is_offered_rather_than_imposed() -> None:
    text = script_text()
    # Both extra registrations are opt-in tasks, and the registry write is gated
    # on its task rather than unconditional.
    assert 'Name: "startupicon";' in text and "Flags: unchecked" in text
    assert 'Name: "desktopicon";' in text
    assert "Tasks: startupicon" in text
    assert "Tasks: desktopicon" in text


def test_uninstall_removes_only_a_login_value_that_names_this_install() -> None:
    text = script_text()
    # Deliberately not `uninsdeletevalue`: the tray can turn the toggle on after
    # install, and a second install must not have its login entry stripped by
    # this one's uninstaller. Read from the directive rather than the whole file,
    # because the comment beside it names the flag it is refusing.
    registry = text.split("[Registry]", 1)[1].split("\n[", 1)[0]
    directive = next(line for line in registry.splitlines() if line.startswith("Root:"))
    assert "uninsdeletevalue" not in directive
    assert "procedure CurUninstallStepChanged" in text
    assert "if Pos(Lowercase(Root), Lowercase(Existing)) > 0 then" in text
    assert "RegDeleteValue(HKCU," in text


# --- per-user, upgradeable, uninstallable -------------------------------------


def test_the_install_is_per_user_and_never_elevates() -> None:
    text = script_text()
    assert "PrivilegesRequired=lowest" in text
    # No override: one shape, which is also the only one anything is tested
    # against, and the one every per-user thing swe-mux owns already matches.
    assert "PrivilegesRequiredOverridesAllowed" not in text
    assert "DefaultDirName={autopf}\\swe-mux" in text


def test_an_upgrade_replaces_the_bundles_rather_than_merging_into_them() -> None:
    text = script_text()
    # One AppId, defined once, so Add/Remove Programs sees one product and an
    # upgrade replaces it instead of installing a second entry beside it.
    assert text.count("#define AppGuid") == 1
    assert "AppId={{#AppGuid}" in text
    # A PyInstaller onedir tree is not additive: a dropped dependency's stale
    # `.pyd` would still be importable if the new files were copied over the old.
    install_delete = text.split("[InstallDelete]", 1)[1].split("[", 1)[0]
    assert 'Type: filesandordirs; Name: "{app}\\swe-mux"' in install_delete
    assert 'Type: filesandordirs; Name: "{app}\\swe-mux-supervisor"' in install_delete
    # A running app locks its own exe; Restart Manager is what closes it first.
    assert "CloseApplications=yes" in text


def test_the_ready_page_says_what_an_upgrade_costs_before_it_starts() -> None:
    # Replacing the supervisor ends every live terminal session. Restart Manager
    # offers to close the processes; it does not state that consequence.
    text = script_text()
    assert "function UpdateReadyMemo" in text
    assert "ends every live terminal session" in text
    assert "function InstalledVersion" in text


def test_uninstall_leaves_no_bundle_and_no_empty_install_directory() -> None:
    uninstall = script_text().split("[UninstallDelete]", 1)[1].split("[Code]", 1)[0]
    assert 'Name: "{app}\\swe-mux"' in uninstall
    assert 'Name: "{app}\\swe-mux-supervisor"' in uninstall
    assert 'Type: dirifempty; Name: "{app}"' in uninstall


def test_no_pascal_brace_comment_survives_in_the_code_section() -> None:
    # A `{ ... }` comment ends at its first `}`, and every comment in [Code] is
    # about `{app}` - so a braced one terminates mid-sentence and the rest of the
    # prose compiles as code. ISCC reports it as "'BEGIN' expected" on the line
    # *after* the comment, which is nowhere near the mistake; this fails on the
    # comment itself. (Measured: it cost one compile round-trip to find.)
    code = script_text().split("[Code]", 1)[1]
    offenders = [
        line
        for line in code.splitlines()
        if line.lstrip().startswith("{") and not line.lstrip().startswith("{#")
    ]
    assert offenders == [], f"use // comments in [Code], not braces: {offenders}"


# --- the signing hook ---------------------------------------------------------


def test_signing_is_a_hook_that_is_absent_until_a_certificate_exists() -> None:
    text = script_text()
    # The whole signing block is behind the preprocessor symbol, so an unsigned
    # build emits none of it rather than emitting a `SignTool=` naming nothing.
    signing = text.split("#ifdef SignTool", 1)[1].split("#endif", 1)[0]
    assert "SignTool={#SignTool}" in signing
    assert "SignedUninstaller=yes" in signing
    assert text.count("SignTool=") == 1


def test_an_unset_sign_tool_adds_nothing_to_the_compiler_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    as_windows_host(monkeypatch)
    monkeypatch.delenv(build_installer.SIGNTOOL_ENV, raising=False)
    recorded: list[list[str]] = []

    def capture(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        recorded.append(list(command))
        # ISCC would have written the installer; stand in for it so the caller's
        # own "it reported success but wrote nothing" check is exercised too.
        (tmp_path / "out" / str(release_installer_name("0.9.0", WINDOWS))).write_bytes(b"MZ")
        return subprocess.CompletedProcess(command, 0)

    make_bundle(tmp_path / "dist" / "swe-mux")
    (tmp_path / "dist" / "swe-mux-supervisor").mkdir(parents=True)
    (tmp_path / "out").mkdir()
    monkeypatch.setattr(build_installer, "find_iscc", lambda: Path("iscc"))
    monkeypatch.setattr(build_installer.subprocess, "run", capture)

    build_installer.build_installer(tmp_path / "dist", tmp_path / "out")
    assert recorded, "the compiler was never invoked"
    command = recorded[0]
    assert not any(part.startswith(("/S", "/DSignTool")) for part in command)
    assert "/DAppVersion=0.9.0" in command
    assert "/DFileVersion=0.9.0.0" in command


def test_a_configured_sign_tool_registers_and_switches_the_block_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    as_windows_host(monkeypatch)
    monkeypatch.setenv(build_installer.SIGNTOOL_ENV, "signtool.exe sign $f")
    recorded: list[list[str]] = []

    def capture(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        recorded.append(list(command))
        (tmp_path / "out" / str(release_installer_name("0.9.0", WINDOWS))).write_bytes(b"MZ")
        return subprocess.CompletedProcess(command, 0)

    make_bundle(tmp_path / "dist" / "swe-mux")
    (tmp_path / "dist" / "swe-mux-supervisor").mkdir(parents=True)
    (tmp_path / "out").mkdir()
    monkeypatch.setattr(build_installer, "find_iscc", lambda: Path("iscc"))
    monkeypatch.setattr(build_installer.subprocess, "run", capture)

    build_installer.build_installer(tmp_path / "dist", tmp_path / "out")
    command = recorded[0]
    # Both halves or neither: `/S` registers the command, `/D` is what makes the
    # script emit its `SignTool=` line at all.
    assert f"/S{build_installer.SIGNTOOL_NAME}=signtool.exe sign $f" in command
    assert f"/DSignTool={build_installer.SIGNTOOL_NAME}" in command


def test_every_path_define_reaches_the_compiler_absolute(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The three `/D` path defines are absolute, because ISCC anchors on the script.

    A relative `Source:` in the `.iss` is resolved against **the `.iss` file's own
    directory**, not against the compiler's working directory and not against
    `SourceRoot`. So passing `--dist dist` sent `/DAppSource=dist`, the `[Files]`
    section looked for `packaging/installer/dist/swe-mux/*`, and ISCC failed with
    "No files found matching" naming a path no caller had ever written. `cwd=ROOT`
    on the subprocess looks like it should prevent that and does not.

    That cost the v0.1.1 desktop artifact on 2026-08-28: every earlier step passed,
    the bundles built, and the release shipped to PyPI without a GitHub Release or a
    refreshed `version.json`. The `.iss` header already documented `AppSource` as an
    absolute path, so this asserts the caller keeps the contract the script states.

    This is deliberately a check on the *command*, not a compile: the suite cannot
    run ISCC, which is the gap that let the bug through, and a text assertion that
    names the exact failure is worth more than none. A real compile smoke on a stub
    tree, skipped where ISCC is absent, is the stronger version and is owed.
    """
    as_windows_host(monkeypatch)
    recorded: list[list[str]] = []

    def capture(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        recorded.append(list(command))
        (tmp_path / "out" / str(release_installer_name("0.9.0", WINDOWS))).write_bytes(b"MZ")
        return subprocess.CompletedProcess(command, 0)

    make_bundle(tmp_path / "dist" / "swe-mux")
    (tmp_path / "dist" / "swe-mux-supervisor").mkdir(parents=True)
    (tmp_path / "out").mkdir()
    monkeypatch.setattr(build_installer, "find_iscc", lambda: Path("iscc"))
    monkeypatch.setattr(build_installer.subprocess, "run", capture)

    # A *relative* dist path, which is what the release workflow passes and what
    # made this fail. Resolving it is the product's job, not the caller's.
    monkeypatch.chdir(tmp_path)
    build_installer.build_installer(Path("dist"), tmp_path / "out")

    defines = {
        flag.split("=", 1)[0][2:]: flag.split("=", 1)[1]
        for flag in recorded[0]
        if flag.startswith("/D") and "=" in flag
    }
    for name in ("AppSource", "SourceRoot", "IconFile"):
        assert name in defines, f"/D{name} was not passed to the compiler"
        assert Path(defines[name]).is_absolute(), (
            f"/D{name}={defines[name]} is relative; ISCC resolves a relative path "
            "against the .iss file's directory, so the compiler will look for it "
            "under packaging/installer/"
        )


# --- refusals that cost nothing -----------------------------------------------


def test_a_missing_supervisor_bundle_is_refused_before_the_compiler_runs(
    tmp_path: Path,
) -> None:
    # The installer carries both bundles; packaging one produces an app that
    # silently loses its session-preserving supervisor.
    make_bundle(tmp_path / "dist" / "swe-mux")
    with pytest.raises(SystemExit, match="swe-mux-supervisor"):
        build_installer.build_installer(tmp_path / "dist", tmp_path / "out")


def test_a_missing_icon_is_named_rather_than_left_to_iscc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `packaging/swe-mux.ico` is gitignored build output rendered by
    # `build_desktop`, so a fresh clone has none. ISCC's own failure for a missing
    # `SetupIconFile` is "The system cannot find the file specified" and a line
    # number, which names neither the file nor the command that makes it.
    make_bundle(tmp_path / "dist" / "swe-mux")
    (tmp_path / "dist" / "swe-mux-supervisor").mkdir()
    monkeypatch.setattr(build_installer, "ICON", tmp_path / "absent.ico")
    with pytest.raises(SystemExit, match="build_desktop.py"):
        build_installer.build_installer(tmp_path / "dist", tmp_path / "out")


def test_a_bundle_that_describes_nothing_cannot_be_packaged(tmp_path: Path) -> None:
    app = tmp_path / "dist" / "swe-mux"
    app.mkdir(parents=True)
    (app / "swe-mux.exe").write_bytes(b"MZ")
    (tmp_path / "dist" / "swe-mux-supervisor").mkdir()
    with pytest.raises(SystemExit, match="bundle.json"):
        build_installer.build_installer(tmp_path / "dist", tmp_path / "out")


def test_a_bundle_built_for_another_host_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wrong-host refusal, which a single-host run confuses with the next one.

    Both end in a `SystemExit` naming a platform tag, and a `linux-x64` bundle on
    a Linux runner satisfies *this* check and then trips the one below - so a
    match on the tag alone passed for the wrong reason on two of the three legs.
    The host is pinned so only one refusal can be the answer.
    """
    monkeypatch.setattr(build_installer, "release_platform_tag", lambda: "macos-arm64")
    make_bundle(tmp_path / "dist" / "swe-mux", platform=WINDOWS)
    (tmp_path / "dist" / "swe-mux-supervisor").mkdir()
    with pytest.raises(SystemExit, match="build the installer on the host that built it"):
        build_installer.build_installer(tmp_path / "dist", tmp_path / "out")


def test_a_host_with_no_installer_of_its_own_is_told_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Linux and macOS answer, asserted from every host.

    `release_installer_name` returns None off Windows, and a build that then
    invented `swe-mux-0.9.0-linux-x64-setup.exe` would name an artifact no
    release will ever carry.
    """
    monkeypatch.setattr(build_installer, "release_platform_tag", lambda: "linux-x64")
    make_bundle(tmp_path / "dist" / "swe-mux", platform="linux-x64")
    (tmp_path / "dist" / "swe-mux-supervisor").mkdir()
    with pytest.raises(SystemExit, match="there is no installer for linux-x64"):
        build_installer.build_installer(tmp_path / "dist", tmp_path / "out")


# --- the archive-suffix gap ---------------------------------------------------
#
# `_ARCHIVE_SUFFIX` promised `.tar.gz` on POSIX while the reader could open only
# zips. These prove the promise is now kept on both sides.


def test_the_suffix_map_names_only_formats_the_reader_can_open() -> None:
    for tag in ("windows-x64", "linux-x64", "macos-arm64"):
        name = release_archive_name("1.2.3", tag)
        # Raises for anything the reader does not implement.
        assert archive_suffix(Path(name)) in (ZIP_SUFFIX, TAR_GZ_SUFFIX)
    assert archive_suffix(Path("swe-mux-1.2.3-linux-x64.tar.gz")) == TAR_GZ_SUFFIX
    assert archive_suffix(Path("swe-mux-1.2.3-windows-x64.zip")) == ZIP_SUFFIX
    with pytest.raises(ArchiveError, match="not a swe-mux release archive"):
        archive_suffix(Path("swe-mux-1.2.3-linux-x64.tar.xz"))


def write_tarball(archive: Path, bundle: Path) -> Path:
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(bundle.rglob("*")):
            if path.is_dir():
                continue
            tar.add(path, arcname=f"{ARCHIVE_ROOT}/{path.relative_to(bundle).as_posix()}")
    return archive


def test_a_posix_tarball_is_read_and_extracted_like_a_windows_zip(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path / "swe-mux", version="2.0.0", platform="linux-x64")
    archive = write_tarball(tmp_path / release_archive_name("2.0.0", "linux-x64"), bundle)
    metadata = read_archive_metadata(archive)
    assert (metadata.version, metadata.platform) == ("2.0.0", "linux-x64")
    root = extract_bundle(archive, tmp_path / "staging")
    assert root == tmp_path / "staging" / ARCHIVE_ROOT
    assert (root / "swe-mux.exe").read_bytes() == b"MZ fake"
    assert (root / "_internal" / "base_library.zip").is_file()


def test_a_tarball_that_would_write_outside_its_own_tree_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "swe-mux-2.0.0-linux-x64.tar.gz"
    payload = tmp_path / "payload"
    payload.write_bytes(b"x")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname="../escaped")
    with pytest.raises(ArchiveError, match="parent-directory path"):
        read_archive_metadata(archive)
    with pytest.raises(ArchiveError, match="parent-directory path"):
        extract_bundle(archive, tmp_path / "staging")


def test_an_archive_declaring_more_bytes_than_the_ceiling_is_refused_before_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A gzip stream a few hundred kilobytes long can name terabytes of members,
    # and `update_install`'s download ceiling bounds the compressed bytes only.
    # The refusal reads the header table, so nothing is written either way; the
    # ceiling is lowered rather than a multi-gigabyte archive being built, which
    # exercises the same comparison for a fraction of the wall clock.
    monkeypatch.setattr("swe_mux.bundle_archive.MAX_UNCOMPRESSED_BYTES", 1)
    bundle = make_bundle(tmp_path / "swe-mux", version="2.0.0", platform="linux-x64")
    tarball = write_tarball(tmp_path / "swe-mux-2.0.0-linux-x64.tar.gz", bundle)
    with pytest.raises(ArchiveError, match="ceiling"):
        read_archive_metadata(tarball)
    with pytest.raises(ArchiveError, match="ceiling"):
        extract_bundle(tarball, tmp_path / "staging")
    # Both formats, one rule: the zip path had no ceiling at all before this.
    zipped = tmp_path / "swe-mux-2.0.0-windows-x64.zip"
    with zipfile.ZipFile(zipped, "w") as handle:
        handle.write(bundle / "swe-mux.exe", f"{ARCHIVE_ROOT}/swe-mux.exe")
    with pytest.raises(ArchiveError, match="ceiling"):
        extract_bundle(zipped, tmp_path / "staging")
    assert not (tmp_path / "staging" / ARCHIVE_ROOT).exists()


def test_the_writer_produces_the_container_its_own_name_promises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `build_archive` derives the name from the bundle's platform, so a POSIX
    # bundle must come out as a readable tarball rather than as a zip wearing a
    # `.tar.gz` name - which is a refusal on the far side of a 400 MB download.
    bundle = make_bundle(tmp_path / "swe-mux", version="2.0.0", platform="linux-x64")
    monkeypatch.setattr(package_desktop_release, "release_platform_tag", lambda: "linux-x64")
    archive, _ = package_desktop_release.build_archive(bundle, tmp_path / "out")
    assert archive.name == "swe-mux-2.0.0-linux-x64.tar.gz"
    assert tarfile.is_tarfile(archive)
    assert read_archive_metadata(archive).platform == "linux-x64"
    assert not list(tmp_path.joinpath("out").glob("*.part"))


def test_the_windows_writer_still_produces_a_zip(tmp_path: Path) -> None:
    bundle = make_bundle(
        tmp_path / "swe-mux", version="2.0.0", platform=release_platform_tag()
    )
    archive, _ = package_desktop_release.build_archive(bundle, tmp_path / "out")
    assert archive.name == release_archive_name("2.0.0", release_platform_tag())
    if archive.name.endswith(ZIP_SUFFIX):
        assert zipfile.is_zipfile(archive)
    else:
        assert tarfile.is_tarfile(archive)
    assert read_archive_metadata(archive).version == "2.0.0"
