"""Phase 21 A4: the bundle's package membership is asserted, not assumed.

A PyInstaller bundle contains whatever the *build venv* made importable, which is
not the same thing as what this repository declares it ships. `dist/swe-mux` as
built 2026-08-27 carried 101 MB of `playwright/driver` behind the lazy `import
playwright` in `preview_capture.py`, while `license_audit.py` says plainly that
`preview-capture` does not ship. Nothing failed: `verify_bundle_licenses` reads
the built tree for copyleft payloads and Playwright is Apache-2.0.

`build_desktop.verify_bundle_contents` closes that hole from both sides - a stray
package and a package that quietly stopped being collected - and these tests pin
its behaviour plus the invariants tying its manifest to the spec.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    """Import a `packaging/` script by path; it is not an installed package."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "packaging" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


build_desktop = _load("build_desktop")


def _voice_metadata_installed() -> bool:
    """Whether the acquired voice distributions are installed *here*.

    False on CI's Linux and macOS legs, which sync no extras deliberately: that
    bare `uv sync` is what proves `pip install swe-mux` yields an importable
    package now that the voice closure sits behind an extra. Adding the extra
    there to green a test would delete the coverage the leg exists for.
    """
    from importlib.metadata import PackageNotFoundError, distribution

    try:
        distribution("spacy")
    except PackageNotFoundError:
        return False
    return True


def _bundle(root: Path, packages: dict[str, int]) -> Path:
    """Build a stand-in bundle whose `_internal/` holds `packages` at given sizes."""
    internal = root / "swe-mux" / "_internal"
    internal.mkdir(parents=True)
    for name, size in packages.items():
        package = internal / name
        package.mkdir()
        (package / "payload.bin").write_bytes(b"x" * size)
    return root / "swe-mux"


def _expected(*extra: str) -> set[str]:
    return set(build_desktop.EXPECTED_BUNDLE_PACKAGES) | set(extra)


# --------------------------------------------------------------------------- listing


def test_the_listing_reports_directories_with_their_sizes(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, {"spacy": 40, "swe_mux": 10})
    assert build_desktop.bundle_top_level_packages(bundle) == {"spacy": 40, "swe_mux": 10}


def test_the_listing_ignores_dist_info_and_loose_files(tmp_path: Path) -> None:
    """Version-carrying metadata directories would churn the manifest for nothing.

    A `numpy-2.5.1.dist-info` entry renames itself on every dependency bump, so a
    manifest listing them would be edited without being read. The package it
    describes is checked in its own right.
    """
    bundle = _bundle(tmp_path, {"numpy": 30, "numpy-2.5.1.dist-info": 1})
    (bundle / "_internal" / "python312.dll").write_bytes(b"loose")
    assert build_desktop.bundle_top_level_packages(bundle) == {"numpy": 30}


# --------------------------------------------------------------------------- the gate


def test_a_bundle_matching_the_manifest_passes(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, {name: 1 for name in build_desktop.EXPECTED_BUNDLE_PACKAGES})
    build_desktop.verify_bundle_contents(bundle)  # does not raise


def test_a_stray_build_venv_dependency_fails_the_build_by_name_and_size(
    tmp_path: Path,
) -> None:
    """The Playwright case, reproduced: an Apache-2.0 package the closure never declared."""
    packages = {name: 1 for name in build_desktop.EXPECTED_BUNDLE_PACKAGES}
    packages["playwright"] = 101_000_000
    bundle = _bundle(tmp_path, packages)

    with pytest.raises(SystemExit) as failure:
        build_desktop.verify_bundle_contents(bundle)

    message = str(failure.value)
    assert "playwright" in message, "the failure must name the offending package"
    assert "101.0 MB" in message, "the failure must say what it costs"
    assert "swe_mux.spec" in message, "the failure must say what to do about it"


def test_the_largest_stray_is_reported_first(tmp_path: Path) -> None:
    packages = {name: 1 for name in build_desktop.EXPECTED_BUNDLE_PACKAGES}
    packages["small_passenger"] = 1_000
    packages["large_passenger"] = 90_000_000
    bundle = _bundle(tmp_path, packages)

    with pytest.raises(SystemExit) as failure:
        build_desktop.verify_bundle_contents(bundle)

    message = str(failure.value)
    assert message.index("large_passenger") < message.index("small_passenger")


def test_a_package_that_stopped_being_collected_fails_the_build(tmp_path: Path) -> None:
    """The other direction, and the one that is silent in every other check.

    `tzdata` is pure data with no importable code path of its own; a bundle
    without it starts healthy and fails every timezone-naming schedule, in the
    frozen app only.
    """
    packages = {
        name: 1 for name in build_desktop.EXPECTED_BUNDLE_PACKAGES if name != "tzdata"
    }
    bundle = _bundle(tmp_path, packages)

    with pytest.raises(SystemExit) as failure:
        build_desktop.verify_bundle_contents(bundle)

    assert "tzdata" in str(failure.value)
    assert "collect_all" in str(failure.value)


def test_the_manifest_is_a_parameter_so_the_gate_is_not_only_testable_here(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path, {"only_this": 5})
    build_desktop.verify_bundle_contents(bundle, expected={"only_this"})  # does not raise


# --------------------------------------------------------------------------- invariants


def test_every_collect_all_package_is_in_the_manifest() -> None:
    """The spec and the manifest have to agree about what is deliberately shipped.

    A name in the spec's `collect_all` loop is there because its absence is
    invisible until the frozen app runs. If the manifest does not also expect it,
    the two halves stop describing the same bundle and one of them is decoration.
    """
    spec = (REPO_ROOT / "packaging" / "swe_mux.spec").read_text(encoding="utf-8")
    loop = spec.split("for package in (")[1].split("):")[0]
    collected = {line.strip().strip('",') for line in loop.splitlines() if '"' in line}
    assert collected, "the collect_all loop parsed as empty; this guard would assert nothing"
    assert collected <= set(build_desktop.EXPECTED_BUNDLE_PACKAGES)


def test_every_relinkable_lgpl_package_is_in_the_manifest() -> None:
    """`verify_bundle_licenses` promises these ship as source; this says they ship."""
    assert set(build_desktop.RELINKABLE_LGPL) <= set(build_desktop.EXPECTED_BUNDLE_PACKAGES)


def test_nothing_the_spec_excludes_is_expected_in_the_bundle() -> None:
    """`av` and `edge_tts` are excluded for licensing and distribution-boundary
    reasons; expecting either here would make this gate argue with that one."""
    spec = (REPO_ROOT / "packaging" / "swe_mux.spec").read_text(encoding="utf-8")
    clause = re.search(r"excludes=\[([^\]]*)\]", spec)
    assert clause, "the excludes clause did not parse; this guard would assert nothing"
    excluded = {token.strip().strip('"') for token in clause.group(1).split(",")}
    # `*EXCLUDED_VOICE_CLOSURE` is a splat rather than a literal; the names it
    # contributes are checked by the test below, which resolves it for real.
    excluded = {name for name in excluded if name and not name.startswith("*")}
    assert excluded, "the excludes clause parsed as empty; this guard would assert nothing"
    assert not (excluded & set(build_desktop.EXPECTED_BUNDLE_PACKAGES))


def test_no_acquired_distribution_is_also_a_bundle_package() -> None:
    """The two gates that keep 277 MB out of the bundle must not contradict.

    Read from `voice_wheels.DISTRIBUTIONS` - a generated data table that is always
    present - rather than from installed metadata, so this runs on the CI legs
    that sync no extras. Those are the legs where a bundle manifest claiming to
    ship an acquired package would be least likely to be noticed.

    Compared on canonical names because the two sides spell them differently: the
    pin table carries distribution names (`hf-xet`) and the manifest carries
    import names (`hf_xet`).
    """
    from swe_mux.voice_wheels import DISTRIBUTIONS

    def canon(name: str) -> str:
        return name.lower().replace("-", "_")

    acquired = {canon(name) for name in DISTRIBUTIONS}
    shipped = {canon(name) for name in build_desktop.EXPECTED_BUNDLE_PACKAGES}
    assert "spacy" in acquired and "num2words" in acquired and "onnxruntime" in acquired
    assert not (acquired & shipped)


@pytest.mark.skipif(
    not _voice_metadata_installed(),
    reason=(
        "reads the acquired distributions' installed metadata, which a build "
        "environment has and a bare `uv sync` does not; the environment-free half "
        "of this invariant is test_no_acquired_distribution_is_also_a_bundle_package"
    ),
)
def test_the_metadata_derived_excludes_agree_with_the_manifest() -> None:
    """The exact list the spec excludes, against the exact list the manifest expects.

    Stronger than the name-level check above because it is the real derivation -
    `top_level.txt` and recorded files, including the entries whose import name is
    nothing like their distribution name. A name in both lists would mean the spec
    excludes a package the manifest requires, and every build would fail on the
    missing-package half with a message about `collect_all`.
    """
    closure = set(build_desktop.voice_closure_top_levels())
    assert "spacy" in closure and "num2words" in closure and "onnxruntime" in closure
    assert not (closure & set(build_desktop.EXPECTED_BUNDLE_PACKAGES))


def test_the_voice_closure_gate_names_the_packages_that_returned(tmp_path: Path) -> None:
    """A second, more specific failure than "unexpected package", on purpose.

    `verify_bundle_contents` would already reject `spacy` as undeclared and point
    at the build venv. This one names the mechanism that was supposed to keep it
    out and the size that is at stake, because "an undeclared package appeared" and
    "the thing you deliberately stopped shipping is back" lead to different fixes.

    The closure is injected rather than derived, so this runs everywhere. It is an
    assertion about what the *refusal says*, and deriving the list from the machine
    running the test would have made it a test of that machine - which is exactly
    how it turned into a red CI leg instead of a verdict.
    """
    closure = ["spacy", "misaki", "num2words", "onnxruntime"]
    bundle = _bundle(tmp_path, {"swe_mux": 10, "spacy": 40})
    with pytest.raises(SystemExit, match="Voice closure regression") as failure:
        build_desktop.verify_voice_closure_absent(bundle, closure)
    assert "spacy" in str(failure.value)
    assert "swe_mux.spec" in str(failure.value)
    clean = _bundle(tmp_path / "clean", {"swe_mux": 10})
    build_desktop.verify_voice_closure_absent(clean, closure)


def test_the_gate_derives_its_closure_when_none_is_injected(tmp_path: Path) -> None:
    """The default path is the one a build takes, so it is asserted too.

    Without this, injecting a list in the test above would make the production
    call - `verify_voice_closure_absent(root)` with no closure - untested.
    """
    calls: list[str] = []
    clean = _bundle(tmp_path, {"swe_mux": 10})
    original = build_desktop.voice_closure_top_levels
    build_desktop.voice_closure_top_levels = lambda: calls.append("derived") or ("spacy",)
    try:
        build_desktop.verify_voice_closure_absent(clean)
    finally:
        build_desktop.voice_closure_top_levels = original
    assert calls == ["derived"]


def test_the_stable_abi_forwarder_is_required_in_a_windows_bundle(tmp_path: Path) -> None:
    """`python3.dll` is present for code the bundle does not contain.

    Every abi3 wheel in the acquired closure links against it by name, and nothing
    in the bundle's own analysis pulls it in - so it is exactly the kind of file
    that disappears silently. Measured on a frozen probe: without it, `tokenizers`
    fails with "DLL load failed", which names neither the file nor the reason.
    """
    internal = tmp_path / "swe-mux" / "_internal"
    internal.mkdir(parents=True)
    (internal / "python312.dll").write_bytes(b"")
    with pytest.raises(SystemExit, match="python3.dll"):
        build_desktop.verify_stable_abi_forwarder(tmp_path / "swe-mux")
    (internal / "python3.dll").write_bytes(b"")
    build_desktop.verify_stable_abi_forwarder(tmp_path / "swe-mux")


def test_a_non_windows_bundle_is_not_asked_for_a_windows_forwarder(tmp_path: Path) -> None:
    """The forwarder is a Windows concept; a POSIX bundle must not fail on it."""
    internal = tmp_path / "swe-mux" / "_internal"
    internal.mkdir(parents=True)
    build_desktop.verify_stable_abi_forwarder(tmp_path / "swe-mux")


def test_the_spec_collects_the_forwarder_explicitly() -> None:
    """Asserting the mechanism as well as the result.

    `cryptography` ships an abi3 `.pyd` and would pull `python3.dll` in today, so
    a bundle check alone would keep passing if the explicit collection were
    deleted - right up until the day the base closure changed.
    """
    spec = (REPO_ROOT / "packaging" / "swe_mux.spec").read_text(encoding="utf-8")
    assert "PYTHON3_DLL" in spec
    assert 'binaries += [(str(PYTHON3_DLL), ".")]' in spec


def test_the_spec_ships_the_whole_standard_library() -> None:
    """The sidecar's import graph is invisible to PyInstaller; the stdlib is not ours to guess.

    Measured while proving the sidecar loads at all: a frozen probe carrying only
    its own stdlib closure failed on `platform`, then `ctypes`, then `json`, then
    `http.cookies` - one at a time, each revealed only by fixing the one before.
    """
    spec = (REPO_ROOT / "packaging" / "swe_mux.spec").read_text(encoding="utf-8")
    assert "sys.stdlib_module_names" in spec
    assert "hiddenimports=hiddenimports + STDLIB_HIDDENIMPORTS" in spec


# ------------------------------------------------------------- the console client


def _cli_bundle(
    root: Path, packages: dict[str, int], *, exes: tuple[str, ...] | None = None
) -> Path:
    """A stand-in `dist/swe-mux-cli`: launchers at the top, packages under `_internal/`."""
    bundle = root / "swe-mux-cli"
    internal = bundle / "_internal"
    internal.mkdir(parents=True)
    for name, size in packages.items():
        package = internal / name
        package.mkdir()
        if name == "swe_mux":
            # The real bundle's own-package directory holds nothing but the
            # embedded skill data (spec datas); the fake mirrors that shape so
            # the contents assertion accepts a conforming bundle.
            skill = package / "assets" / "skills" / "swe-mux"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_bytes(b"x" * size)
        else:
            (package / "payload.bin").write_bytes(b"x" * size)
    for name in build_desktop.CLI_EXES if exes is None else exes:
        (bundle / name).write_bytes(b"MZ fake")
    return bundle


def cli_spec_text() -> str:
    return (REPO_ROOT / "packaging" / "swe_mux_cli.spec").read_text(encoding="utf-8")


def test_a_client_bundle_matching_its_manifest_passes(tmp_path: Path) -> None:
    bundle = _cli_bundle(
        tmp_path, {name: 1 for name in build_desktop.EXPECTED_CLI_BUNDLE_PACKAGES}
    )
    build_desktop.verify_cli_bundle_contents(bundle)  # does not raise


def test_the_daemon_leaking_back_into_the_client_fails_the_build(tmp_path: Path) -> None:
    """The regression this bundle exists to prevent, at the size it actually was.

    The first build of `swe_mux_cli.spec` was 143 MiB rather than 28, because
    `cli install-shortcut` imports `swe_mux.shortcuts`, which reaches
    `swe_mux.desktop`, which imports `swe_mux.__main__`, which imports
    `swe_mux.server`. Nothing about that chain is visible in a diff - only in a
    measurement - so the measurement is the gate.
    """
    packages = {name: 1 for name in build_desktop.EXPECTED_CLI_BUNDLE_PACKAGES}
    packages["ctranslate2"] = 40_000_000
    bundle = _cli_bundle(tmp_path, packages)

    with pytest.raises(SystemExit) as failure:
        build_desktop.verify_cli_bundle_contents(bundle)

    message = str(failure.value)
    assert "ctranslate2" in message, "the failure must name the offending package"
    assert "40.0 MB" in message, "the failure must say what it costs"
    assert "swe_mux_cli.spec" in message, "the failure must say what to do about it"


def test_a_client_dependency_that_stopped_being_collected_fails_the_build(
    tmp_path: Path,
) -> None:
    """`psutil` backs `swe_mux.lifecycle`'s ledger, which every command writes to.

    Its absence is an ImportError at first use, in the frozen client, on a machine
    with no traceback anywhere - the same shape as the app bundle's
    missing-package half, and it needs the same gate.
    """
    packages = {
        name: 1 for name in build_desktop.EXPECTED_CLI_BUNDLE_PACKAGES if name != "psutil"
    }
    bundle = _cli_bundle(tmp_path, packages)

    with pytest.raises(SystemExit, match="psutil"):
        build_desktop.verify_cli_bundle_contents(bundle)


def test_client_code_arriving_beside_the_skill_fails_the_build(tmp_path: Path) -> None:
    """Admitting `swe_mux` to the manifest is for its package data alone, and the
    name must not blunt the gate: anything in that directory other than the
    embedded skill subtree - a module materialised as data, a second asset - is
    the same class of leak the membership check catches for third parties."""
    bundle = _cli_bundle(
        tmp_path, {name: 1 for name in build_desktop.EXPECTED_CLI_BUNDLE_PACKAGES}
    )
    (bundle / "_internal" / "swe_mux" / "server.pyc").write_bytes(b"code")

    with pytest.raises(SystemExit, match="server.pyc"):
        build_desktop.verify_cli_bundle_contents(bundle)


def test_the_client_manifest_is_a_parameter_too(tmp_path: Path) -> None:
    bundle = _cli_bundle(tmp_path, {"only_this": 5})
    build_desktop.verify_cli_bundle_contents(bundle, expected={"only_this"})  # does not raise


def test_the_client_spec_excludes_the_daemon_by_name() -> None:
    """The boundary this bundle *is*, asserted rather than left to one import chain.

    Excluding `swe_mux.desktop` alone cuts today's only path to the daemon. All
    three are named so that a second door - a new module-level import of
    `swe_mux.server` from something the client reaches - fails the build here
    instead of quietly shipping 115 MB more.
    """
    clause = re.search(r"EXCLUDES = \[([^\]]*)\]", cli_spec_text())
    assert clause, "the client spec's excludes did not parse; this guard would assert nothing"
    excluded = {token.strip().strip('"') for token in clause.group(1).split(",")}
    assert {"swe_mux.desktop", "swe_mux.__main__", "swe_mux.server"} <= excluded
    # And nothing the client is expected to carry may also be excluded, which would
    # make the two gates argue and fail every build on the missing-package half.
    assert not (excluded & set(build_desktop.EXPECTED_CLI_BUNDLE_PACKAGES))


def test_the_client_excludes_the_stdlib_door_to_pywin32() -> None:
    """The one dependency the client never asked for, and the manifest is what found it.

    `logging.handlers` defines `NTEventLogHandler`, whose `__init__` imports
    `win32evtlogutil` and `win32evtlog`; PyInstaller follows an import inside a
    function body like any other, so pywin32 landed in a bundle that never logs
    to the NT event log. The first CI run of `installer-cycle` is what surfaced
    it: this host collected `win32` and the runner collected `win32` **and**
    `pywin32_system32`, because whether pywin32's DLL directory is a separate
    top-level entry depends on the install layout.

    Excluding is safe by construction rather than by what happens to work: CPython
    wraps that import in `try/except ImportError`, so `import logging.handlers`
    needs nothing from pywin32 - which matters because `swe_mux.supervisor`
    imports it and is reachable from `swemux doctor`.
    """
    clause = re.search(r"EXCLUDES = \[([^\]]*)\]", cli_spec_text())
    assert clause, "the client spec's excludes did not parse; this guard would assert nothing"
    excluded = {token.strip().strip('"') for token in clause.group(1).split(",")}
    assert {"win32evtlog", "win32evtlogutil"} <= excluded
    # And the manifest agrees: declaring the pair would have encoded one runner's
    # shape instead of removing the difference.
    assert not (
        {"win32", "pywin32_system32"} & set(build_desktop.EXPECTED_CLI_BUNDLE_PACKAGES)
    )


def test_the_stdlib_import_the_client_excludes_is_the_guarded_one() -> None:
    """Read CPython's own source, because that is what makes the exclusion safe.

    If `logging.handlers` ever imported pywin32 at module scope, or dropped the
    `except ImportError`, excluding it would turn every `import logging.handlers`
    in the frozen client into a crash - and the client does use logging. This
    fails on the day that assumption stops holding rather than on the day a user
    runs `swemux doctor`.
    """
    import inspect
    import logging.handlers

    source = inspect.getsource(logging.handlers)
    module_scope = [
        line
        for line in source.splitlines()
        if line.startswith(("import ", "from ")) and "win32" in line
    ]
    assert not module_scope, f"pywin32 is imported at module scope now: {module_scope}"
    guarded = inspect.getsource(logging.handlers.NTEventLogHandler.__init__)
    assert "import win32evtlogutil, win32evtlog" in guarded
    assert "except ImportError" in guarded


def test_the_two_launchers_the_spec_collects_are_the_ones_the_build_verifies() -> None:
    """`CLI_EXES` is defined independently of the spec, so assert they agree.

    Deriving it from the spec would make the verification read its own subject and
    prove nothing; leaving them unrelated would let a dropped `EXE()` entry ship a
    bundle with one launcher and a smoke test that never looked for the other.
    """
    collected = tuple(re.findall(r'launcher\("([^"]+)"\)', cli_spec_text()))
    assert collected, "no launcher() calls parsed; this guard would assert nothing"
    assert tuple(f"{name}.exe" for name in collected) == build_desktop.CLI_EXES


def test_the_launchers_are_the_client_commands_the_package_looks_for() -> None:
    """The build's claim and `install_location`'s search have to name one set.

    `install_location.CLIENT_COMMANDS` is what `doctor` and `install-shortcut`
    resolve against; `CLI_EXES` is what the build produces. A bundle shipping
    `swemux.exe` while the package hunts for something else is an install that
    reports itself broken.
    """
    from swe_mux.install_location import CLIENT_COMMANDS

    assert {name.removesuffix(".exe") for name in build_desktop.CLI_EXES} == set(
        CLIENT_COMMANDS
    )


def test_the_client_is_a_console_program_and_the_app_is_not() -> None:
    """The whole reason there is a second spec.

    A GUI-subsystem process on Windows has no stdout and no stderr at all, so a
    client built with `console=False` would print nothing while exiting 0.
    """
    assert "console=True" in cli_spec_text()
    assert "console=False" in (REPO_ROOT / "packaging" / "swe_mux.spec").read_text(
        encoding="utf-8"
    )


def test_the_frozen_entry_point_exits_with_the_code_main_returns() -> None:
    """The defect the first smoke run caught, pinned so it cannot come back.

    `swe_mux.cli.main` *returns* its exit code because `[project.scripts]` wraps it
    in `sys.exit(main())`. `packaging/cli_entry.py` calling it bare made
    `swemux ls` against a dead daemon print "cannot reach the mux daemon" and exit
    **0**, so every script branching on the documented codes took the success path.
    """
    entry = (REPO_ROOT / "packaging" / "cli_entry.py").read_text(encoding="utf-8")
    assert "raise SystemExit(main())" in entry


def test_the_smoke_test_refuses_a_bundle_that_is_missing_a_launcher(tmp_path: Path) -> None:
    """A dropped `EXE()` entry has to fail here rather than at an install.

    Built with no launchers at all, which is what `CLI_EXES` having one entry
    now makes the only way to be missing one - and the reason this asserts on
    `CLI_EXES` rather than on a literal filename: the check is "everything the
    spec promises is present", and a second launcher added later must be covered
    without anyone remembering to edit this.
    """
    bundle = _cli_bundle(tmp_path, {"psutil": 1}, exes=())
    with pytest.raises(SystemExit, match=build_desktop.CLI_EXES[0]):
        build_desktop.smoke_cli_bundle(bundle)


def test_the_smoke_test_fails_on_the_wrong_exit_code(tmp_path: Path) -> None:
    """The assertion is the exit code, not the output, and this proves it.

    A launcher that printed the right error and exited 0 is exactly what shipped
    on the first build, and a smoke test reading stdout would have called it fine.

    The runner is injected rather than monkeypatched onto `subprocess`, which is
    a module shared by everything else running in this interpreter.
    """
    import subprocess as subprocess_module

    def always_zero(command: list[str], **_: object) -> Any:
        return subprocess_module.CompletedProcess(command, 0, stdout="", stderr="")

    bundle = _cli_bundle(tmp_path, {"psutil": 1})
    with pytest.raises(SystemExit, match="documented exit code"):
        build_desktop.smoke_cli_bundle(bundle, run=always_zero)


def test_the_smoke_test_never_reads_the_operators_data_directory(tmp_path: Path) -> None:
    """A build script that touched `~/.mux` would be reaching into a live install.

    Every invocation gets a throwaway `MUX_DATA_DIR`, so the client's config read
    lands somewhere disposable rather than on the machine's real one.
    """
    import subprocess as subprocess_module

    seen: list[str] = []

    def record(command: list[str], **kwargs: Any) -> Any:
        environment = kwargs.get("env") or {}
        seen.append(str(environment.get("MUX_DATA_DIR")))
        if command[1] == "--skill":
            return subprocess_module.CompletedProcess(
                command, 0, stdout="MUX_SESSION_ID", stderr=""
            )
        code = 0 if command[1] == "--help" else 3
        return subprocess_module.CompletedProcess(command, code, stdout="", stderr="")

    bundle = _cli_bundle(tmp_path, {"psutil": 1})
    build_desktop.smoke_cli_bundle(bundle, run=record)

    assert seen and all(value not in {"None", ""} for value in seen)
    assert all(Path(value).name.startswith("swe-mux-cli-smoke-") for value in seen)


def test_the_smoke_test_fails_when_the_embedded_skill_prints_empty(tmp_path: Path) -> None:
    """The skill is a data file, so a dropped spec datas entry is invisible to
    membership checks and can even exit 0 (an empty file reads fine). Only the
    content assertion catches that shape."""
    import subprocess as subprocess_module

    def hollow(command: list[str], **_: object) -> Any:
        code = 3 if command[1] == "ls" else 0
        return subprocess_module.CompletedProcess(command, code, stdout="", stderr="")

    bundle = _cli_bundle(tmp_path, {"psutil": 1})
    with pytest.raises(SystemExit, match="agent skill did not print"):
        build_desktop.smoke_cli_bundle(bundle, run=hollow)


def test_a_staged_redeploy_never_builds_the_client_bundle() -> None:
    """`dist/swe-mux-cli` is an installer input, not part of the running app.

    A staged redeploy builds into `dist/.staging` precisely so it writes nothing
    live; the client bundle has no staging path and always goes to `dist/`, so the
    redeploy has to opt out of it explicitly. And a `swemux` sitting in a terminal
    would fail that build on a locked exe minutes in.
    """
    redeploy = (REPO_ROOT / "packaging" / "redeploy_desktop.py").read_text(encoding="utf-8")
    assert '"--app-distpath", str(STAGING_ROOT), "--skip-cli"' in redeploy
