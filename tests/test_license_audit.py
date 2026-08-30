"""Phase 10.5: the distribution license gate and the generated notices.

The gate has two halves that must both stay alive, because the audit's central
finding is that a wheel's declared license does not describe its shipped
binaries. `packaging/license_audit.py` reads the resolved closure's metadata;
`build_desktop.verify_bundle_licenses` reads the built tree for payloads by
artifact name. These tests pin both, plus the invariants that connect them: the
allowlist, the notices file being generated rather than hand-edited, and the
`collect_all` entries that make the LGPL relink promise true.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from swe_mux import voice_runtime

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    """Import a `packaging/` script by path; it is not an installed package."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "packaging" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


license_audit = _load("license_audit")
build_desktop = _load("build_desktop")


# --------------------------------------------------------------------------- classification


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("MIT", "permissive"),
        ("Apache-2.0", "permissive"),
        ("BSD-3-Clause AND MIT", "permissive"),
        ("MPL-2.0", "file-level-copyleft"),
        ("MPL-2.0 AND MIT", "file-level-copyleft"),
        ("LGPL", "weak-copyleft"),
        ("LGPLv3", "weak-copyleft"),
        ("GNU Lesser General Public License v3 (LGPLv3)", "weak-copyleft"),
        ("GPL-3.0", "strong-copyleft"),
        ("GNU General Public License v2 (GPLv2)", "strong-copyleft"),
        ("AGPL-3.0", "strong-copyleft"),
        ("", "unknown"),
    ],
)
def test_classify_buckets_licenses(text: str, expected: str) -> None:
    assert license_audit.classify(text) == expected


def test_lesser_is_checked_before_plain_gpl() -> None:
    """Ordering is load-bearing: every LGPL string also contains "GPL".

    If the strong-copyleft test ran first, both allowlisted packages would read
    as GPL and the gate would demand their removal instead of their notices.
    """
    assert license_audit.classify("GNU Lesser General Public License") == "weak-copyleft"
    assert license_audit.classify("GNU Library General Public License") == "weak-copyleft"


# --------------------------------------------------------------------------- the closure


def test_python_closure_excludes_dev_only_packages() -> None:
    """`pyinstaller` is GPL-2.0-with-exception and is never distributed.

    Including dev groups would make the gate fire on the one copyleft package
    that provably cannot reach a user, which is how a gate gets disabled.
    """
    closure = license_audit.python_closure()
    assert "pyinstaller" not in closure
    assert "pytest" not in closure
    assert "mypy" not in closure


def test_python_closure_includes_the_desktop_extra_and_its_transitives() -> None:
    closure = license_audit.python_closure()
    assert "pystray" in closure  # the desktop extra ships in the bundle
    assert "num2words" in closure  # declared by the voice-local extra, via misaki.en
    assert "misaki" in closure
    assert "onnxruntime" in closure


def test_the_closure_walk_covers_every_distributed_extra() -> None:
    """The walk is over `DISTRIBUTED_EXTRAS`, not over what happens to be synced.

    `voice-local` is optional at install time, so the machine running the audit
    may not have a single one of its packages present. If the walk followed the
    environment instead of the declaration, the gate would report a clean,
    copyleft-free closure on that machine while the bundle ships LGPL num2words.
    """
    assert "voice-local" in license_audit.DISTRIBUTED_EXTRAS
    assert "desktop" in license_audit.DISTRIBUTED_EXTRAS
    # preview-capture is deliberately absent: Playwright is never bundled.
    assert "preview-capture" not in license_audit.DISTRIBUTED_EXTRAS


def test_the_lgpl_packages_are_reached_only_through_their_extras() -> None:
    """Both allowlisted LGPL packages are optional, which is why the walk matters.

    Neither is a plain runtime dependency any more, so dropping either extra
    from `DISTRIBUTED_EXTRAS` silently removes an LGPL package from the audited
    closure while the bundle keeps shipping it. Pinning the ownership makes that
    a test failure rather than a diligence finding.
    """
    assert license_audit.owning_extra("num2words") == "voice-local"
    assert license_audit.owning_extra("pystray") == "desktop"
    # A non-optional dependency belongs to no extra.
    assert license_audit.owning_extra("aiohttp") is None


def test_dropping_the_voice_extra_would_lose_the_lgpl_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves the previous test's stake: the walk is load-bearing, not decorative."""
    monkeypatch.setattr(license_audit, "DISTRIBUTED_EXTRAS", ("desktop",))
    narrowed = license_audit.python_closure()
    assert "num2words" not in narrowed
    assert "misaki" not in narrowed
    assert "pystray" in narrowed


def test_notices_tell_a_source_user_which_extra_carries_each_lgpl_package() -> None:
    """A bare `uv sync` installs neither, so a single `uv sync` line would lie."""
    notices = (REPO_ROOT / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
    assert "`uv sync --extra voice-local && uv run muxd`" in notices
    assert "`uv sync --extra desktop && uv run muxd`" in notices


def test_python_closure_drops_unreachable_platform_markers() -> None:
    """`httpx2` carries a Pyodide-only dependency no swe-mux artifact can hold.

    Markers are evaluated against every supported platform rather than the
    running one, so a Linux-only package still counts on Windows - but
    `sys_platform == 'emscripten'` matches nothing swe-mux distributes for.
    """
    assert "httpx2-jsfetch" not in license_audit.python_closure()


def test_markers_are_evaluated_across_platforms_not_just_this_one() -> None:
    assert license_audit._reachable("sys_platform == 'win32'") is True
    assert license_audit._reachable("sys_platform == 'linux'") is True
    assert license_audit._reachable("sys_platform == 'darwin'") is True
    assert license_audit._reachable("sys_platform == 'emscripten'") is False
    assert license_audit._reachable(None) is True


def test_npm_closure_excludes_dev_dependencies() -> None:
    closure = license_audit.npm_closure()
    assert "@xterm/xterm" in closure
    assert "vite" not in closure
    assert "typescript" not in closure


# --------------------------------------------------------------------------- the gate


def test_recorded_closure_has_no_unallowlisted_copyleft() -> None:
    """The gate itself, over the checked-in sidecar. No environment needed."""
    assert license_audit.violations(license_audit.read_sidecar()) == []


def test_recorded_closure_matches_the_lockfiles() -> None:
    """A dependency that entered, left, or moved without regenerating fails here.

    This is the drift half of the gate and the reason the sidecar exists: it
    runs with nothing installed, so CI and every worktree catch a forgotten
    `--write` before a diligence review does.
    """
    assert license_audit.membership_drift(license_audit.read_sidecar()) == []


def test_notices_file_is_generated_not_edited() -> None:
    notices = (REPO_ROOT / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
    assert notices == license_audit.render_notices(license_audit.read_sidecar())


def test_the_only_copyleft_that_ships_is_the_two_allowlisted_lgpl_packages() -> None:
    """Pins the actual posture, so a third entry is a deliberate decision.

    Strong copyleft must be absent outright; weak copyleft must be exactly the
    two recorded packages. MPL is file-level and needs license text only, which
    is why it is not in the allowlist.
    """
    recorded = license_audit.read_sidecar()
    weak = {item.name for item in recorded if item.category == "weak-copyleft"}
    strong = {item.name for item in recorded if item.category == "strong-copyleft"}
    unknown = {item.name for item in recorded if item.category == "unknown"}
    assert unknown == set()
    assert weak == {"pystray", "num2words"}
    assert weak == set(license_audit.ALLOWLIST)
    # Phase 11: strong copyleft is absent from the closure outright. `av` was the
    # only member and the only BUNDLE_EXCLUDED entry; both are now empty, and an
    # entry appearing in either is a decision someone has to make deliberately.
    assert strong == set()
    assert license_audit.BUNDLE_EXCLUDED == {}


def test_pyav_is_classified_by_what_it_ships_not_what_it_declares() -> None:
    """The audit's central finding, kept as a tripwire after `av` was dropped.

    PyAV declares BSD-3-Clause and links GPL x264/x265. The `MISDECLARED` entry
    is what makes the gate read the truth, and it stays after the override
    removed `av` from the closure: it costs nothing while nothing depends on
    PyAV, and it is the difference between the gate failing and the gate waving
    through 63 MB of GPL FFmpeg the day something reintroduces it.
    """
    true_license, evidence = license_audit.MISDECLARED["av"]
    assert license_audit.classify(true_license) == "strong-copyleft"
    assert "BSD-3-Clause" in evidence
    assert license_audit.classify("BSD-3-Clause") == "permissive"


def test_bundle_excluded_packages_record_what_keeps_them_out() -> None:
    """Empty today; the rule stands for whatever lands there next."""
    for name, reason in license_audit.BUNDLE_EXCLUDED.items():
        assert "bundle" in reason.lower(), f"{name} must say what keeps it out"
        assert len(reason) > 200, f"{name} must record the consequence, not just the fact"


def test_pyav_is_out_of_the_resolved_closure() -> None:
    """Phase 11's precondition, measured rather than asserted.

    `faster-whisper` still hard-requires `av>=11`; what removes it is the
    `[tool.uv] override-dependencies` entry, whose marker no supported
    environment satisfies. Both halves are pinned because either one alone is
    reversible by accident: dropping the override silently reinstates 63 MB of
    GPL FFmpeg, and dropping `swe_mux.av_stub` breaks dictation instead.
    """
    assert "av" not in license_audit.python_closure()

    manifest = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "override-dependencies" in manifest
    marker = re.search(r'"av; sys_platform == \'([^\']+)\'"', manifest)
    assert marker, "the av override must stay a marker no environment satisfies"
    assert all(
        environment["sys_platform"] != marker.group(1)
        for environment in license_audit.SUPPORTED_ENVIRONMENTS
    )

    # The lockfile records the override, so `uv sync` and CI resolve the same way
    # rather than depending on whoever last regenerated it.
    lock = (REPO_ROOT / "uv.lock").read_text(encoding="utf-8")
    recorded = (
        f'overrides = [{{ name = "av", '
        f"marker = \"sys_platform == '{marker.group(1)}'\" }}]"
    )
    assert recorded in lock


def test_allowlist_entries_explain_themselves() -> None:
    """An allowlist entry is a decision record, not a suppression."""
    for name, reason in license_audit.ALLOWLIST.items():
        assert "LGPL" in reason, f"{name} must name its license"
        assert len(reason) > 120, f"{name} must record why it may ship"


def test_violations_reject_gpl_and_unknown_licenses() -> None:
    """Negative coverage: the gate has to fail, not just pass on today's data."""
    gpl = license_audit.Package("readline", "8.0", "GPL-3.0", "python")
    silent = license_audit.Package("mystery", "1.0", "", "python")
    allowed = license_audit.Package("pystray", "0.19.5", "LGPLv3", "python")
    problems = license_audit.violations([gpl, silent, allowed])
    assert len(problems) == 2
    assert any("readline" in item for item in problems)
    assert any("mystery" in item and "no license" in item for item in problems)
    assert not any("pystray" in item for item in problems)


# --------------------------------------------------------------------------- the bundle half


def test_spec_collects_the_lgpl_packages_as_replaceable_source() -> None:
    """The LGPL relink condition is a packaging fact, and this is where it lives.

    `collect_all` defaults to `include_py_files=True`, so a name in that loop
    ships as readable source under `_internal/<pkg>/` rather than frozen into
    the executable archive. Dropping the name would leave
    THIRD-PARTY-NOTICES.md promising a replaceability that no longer holds.

    Only the *shipped* half is checked here. `num2words` left the loop with the
    rest of the voice closure and its proof left with it.
    """
    spec = (REPO_ROOT / "packaging" / "swe_mux.spec").read_text(encoding="utf-8")
    for name in build_desktop.RELINKABLE_LGPL:
        assert f'"{name}",' in spec, f"{name} must stay in the collect_all loop"


def test_every_allowlisted_lgpl_package_has_a_relink_proof_per_copy() -> None:
    """Every allowlisted LGPL package is proven replaceable, once per real copy.

    The rule was "exactly one proof" while every package had exactly one
    distribution channel, and ROADMAP Phase 24 made that the wrong unit: since
    2026-08-30 `pystray` genuinely has two copies - the frozen bundle's
    `_internal/pystray/` (proven by `build_desktop.RELINKABLE_LGPL` against the
    built tree) and the acquired desktop closure's tree (proven by
    `desktop_runtime.RELINKABLE_LGPL` against the tree that is unpacked) - and
    each copy needs its own proof, because each is what some recipient actually
    receives. `num2words` still has exactly one (the acquired voice tree;
    `voice_runtime.RELINKABLE_LGPL`), and it must stay out of the bundle proof
    because the bundle does not carry it.

    The failures this prevents are unchanged: an allowlist entry whose promise
    nothing checks, and a proof describing a copy that is not there -
    `ACQUIRED_AT_FIRST_USE` remains exactly the packages the bundle does not
    contain, so the notices never point a reader at an `_internal/` that lacks
    the package.
    """
    from swe_mux import desktop_runtime

    shipped = set(build_desktop.RELINKABLE_LGPL)
    acquired_voice = set(voice_runtime.RELINKABLE_LGPL)
    acquired_desktop = set(desktop_runtime.RELINKABLE_LGPL)
    assert shipped | acquired_voice | acquired_desktop == set(license_audit.ALLOWLIST)
    # The voice closure's packages are exactly the ones the bundle stopped
    # carrying, so bundle-proof overlap there means a proof over a missing copy.
    assert not shipped & acquired_voice
    assert acquired_voice == set(license_audit.ACQUIRED_AT_FIRST_USE)
    # The desktop closure's packages are the opposite case: the bundle still
    # ships them (the frozen app has the extra), so each MUST also keep its
    # bundle proof - a channel without a proof is the original failure.
    assert acquired_desktop <= shipped


def test_the_acquired_lgpl_package_is_not_in_the_bundle_manifest() -> None:
    """`num2words` may not reappear in the shipped closure without the audit moving.

    `EXPECTED_BUNDLE_PACKAGES` is the membership gate the build runs. If
    `num2words` were added back to it, the bundle would ship an LGPL package
    whose relink proof now runs somewhere the bundle is not, and
    `verify_bundle_licenses` would not check it - the exact silent gap the two
    halves of this gate exist to close.
    """
    for name in license_audit.ACQUIRED_AT_FIRST_USE:
        assert name not in build_desktop.EXPECTED_BUNDLE_PACKAGES


def test_bundle_verification_rejects_a_forbidden_payload(tmp_path: Path) -> None:
    internal = tmp_path / "swe-mux" / "_internal"
    for name in build_desktop.RELINKABLE_LGPL:
        (internal / name).mkdir(parents=True, exist_ok=True)
        (internal / name / "__init__.py").write_text("", encoding="utf-8")
    (internal / "espeakng_loader").mkdir(parents=True)
    with pytest.raises(SystemExit, match="espeak-ng loader"):
        build_desktop.verify_bundle_licenses(tmp_path / "swe-mux")


def test_edge_tts_is_external_to_the_distributed_closure_and_bundle(tmp_path: Path) -> None:
    assert "voice-edge" not in license_audit.DISTRIBUTED_EXTRAS
    assert "edge-tts" not in license_audit.python_closure()
    internal = tmp_path / "swe-mux" / "_internal"
    for name in build_desktop.RELINKABLE_LGPL:
        (internal / name).mkdir(parents=True, exist_ok=True)
        (internal / name / "__init__.py").write_text("", encoding="utf-8")
    (internal / "edge_tts").mkdir(parents=True)
    with pytest.raises(SystemExit, match="External integration regression"):
        build_desktop.verify_bundle_licenses(tmp_path / "swe-mux")


def test_bundle_verification_rejects_a_gpl_shared_library(tmp_path: Path) -> None:
    internal = tmp_path / "swe-mux" / "_internal"
    for name in build_desktop.RELINKABLE_LGPL:
        (internal / name).mkdir(parents=True, exist_ok=True)
        (internal / name / "__init__.py").write_text("", encoding="utf-8")
    (internal / "libx264-164.dll").write_text("", encoding="utf-8")
    with pytest.raises(SystemExit, match="GPL x264"):
        build_desktop.verify_bundle_licenses(tmp_path / "swe-mux")


def test_bundle_verification_rejects_frozen_lgpl(tmp_path: Path) -> None:
    """An LGPL package with no loose source is a broken relink promise."""
    internal = tmp_path / "swe-mux" / "_internal"
    internal.mkdir(parents=True)
    # pystray absent entirely, as it would be if dropped from collect_all.
    with pytest.raises(SystemExit, match="pystray"):
        build_desktop.verify_bundle_licenses(tmp_path / "swe-mux")


def test_the_acquired_relink_proof_rejects_a_closure_without_readable_source(
    tmp_path: Path,
) -> None:
    """The other half of the same promise, on the tree that is unpacked.

    `verify_bundle_licenses` cannot say anything about `num2words` any more - the
    bundle does not contain it. This is the assertion that replaced it, and it
    guards the same failure: a wheel that shipped only compiled artifacts would
    satisfy every other check here while leaving a recipient unable to substitute
    their own build.
    """
    site = tmp_path / "site"
    (site / "num2words").mkdir(parents=True)
    (site / "num2words" / "lang_EN.pyc").write_bytes(b"")
    with pytest.raises(voice_runtime.VoiceRuntimeError, match="LGPL relink"):
        voice_runtime._verify_relinkable(site)
    (site / "num2words" / "__init__.py").write_text("", encoding="utf-8")
    voice_runtime._verify_relinkable(site)


def test_bundle_verification_tolerates_misakis_own_espeak_module(tmp_path: Path) -> None:
    """`misaki/espeak.py` is inert without the loader and must not fail a build.

    A bare `*espeak*` glob would reject every bundle over a wrapper that can
    never acquire a backend, which is why the globs match shared libraries only.
    """
    internal = tmp_path / "swe-mux" / "_internal"
    for name in build_desktop.RELINKABLE_LGPL:
        (internal / name).mkdir(parents=True, exist_ok=True)
        (internal / name / "__init__.py").write_text("", encoding="utf-8")
    (internal / "misaki").mkdir(parents=True)
    (internal / "misaki" / "espeak.py").write_text("", encoding="utf-8")
    build_desktop.verify_bundle_licenses(tmp_path / "swe-mux")


def test_bundle_verification_passes_a_compliant_tree(tmp_path: Path) -> None:
    internal = tmp_path / "swe-mux" / "_internal"
    for name in build_desktop.RELINKABLE_LGPL:
        (internal / name).mkdir(parents=True, exist_ok=True)
        (internal / name / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    build_desktop.verify_bundle_licenses(tmp_path / "swe-mux")


# --------------------------------------------------------------- the build environment


def test_the_build_requires_every_distributed_extra() -> None:
    """Build-time and audit-time must cover the same extras.

    They answer different questions - "what is redistributed" and "can this
    machine produce it" - but a package the audit calls redistributed and the
    build does not require is one the bundle can silently omit.
    """
    assert set(build_desktop.REQUIRED_BUILD_EXTRAS) == set(
        license_audit.DISTRIBUTED_EXTRAS
    )
    assert set(build_desktop.REQUIRED_BUILD_GROUPS) == set(
        license_audit.DISTRIBUTED_GROUPS
    )


def test_an_unpublished_group_that_ships_is_still_walked() -> None:
    """`en-core-web-sm` is in a dependency group and in the bundle at the same time.

    Groups are normally the opposite of distributed - `pyinstaller` is a build
    tool and `python_closure` excludes groups because including it would make the
    gate cry wolf on the one copyleft package that cannot matter. `g2p-model` is
    there for a packaging reason rather than a build-tool one: the spaCy model
    exists on no index and therefore cannot be a published requirement at all,
    while `packaging/swe_mux.spec` collects it into `_internal/en_core_web_sm/`.

    So it is unpublished *and* shipped, and a walk that could not see it would
    report a closure the bundle does not match - the same failure
    `DISTRIBUTED_EXTRAS` exists to prevent for `voice-local`'s LGPL `num2words`.
    """
    closure = license_audit.python_closure()
    assert "en-core-web-sm" in closure


def test_dropping_a_distributed_group_removes_a_shipped_package_from_the_walk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The negative control, so the assertion above is known to be load-bearing."""
    monkeypatch.setattr(license_audit, "DISTRIBUTED_GROUPS", ())
    assert "en-core-web-sm" not in license_audit.python_closure()


def test_a_distributed_group_that_no_longer_exists_is_refused_rather_than_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A renamed group must fail loudly, not quietly walk a smaller closure.

    `.get(group, [])` would have turned a rename into a silently narrower audit,
    which is precisely the shape of failure this whole module is written against.
    """
    monkeypatch.setattr(license_audit, "DISTRIBUTED_GROUPS", ("no-such-group",))
    with pytest.raises(SystemExit) as raised:
        license_audit.python_closure()
    assert "no-such-group" in str(raised.value)


def test_a_fully_synced_environment_reports_nothing_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive case, pinned against a fixture rather than against this venv.

    Asserting `[]` for the real `voice-local` would make the test say whether
    the machine happens to have the extra synced, which is not a property of the
    code and fails the deliberately-bare base install.
    """
    (tmp_path / "pyproject.toml").write_text(
        "[project.optional-dependencies]\n"
        'desktop = ["pytest>=8"]\n'
        'voice-local = ["packaging"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(build_desktop, "ROOT", tmp_path)
    assert build_desktop.missing_extra_distributions() == []
    build_desktop.verify_build_extras_installed()  # does not raise


def test_missing_extra_distributions_is_read_from_pyproject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Membership comes from the declaration, so a new package cannot be forgotten."""
    (tmp_path / "pyproject.toml").write_text(
        "[project.optional-dependencies]\n"
        'voice-local = ["definitely-not-installed-xyz>=1.0", "pytest>=8"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(build_desktop, "ROOT", tmp_path)
    missing = build_desktop.missing_extra_distributions(("voice-local",))
    assert missing == ["definitely-not-installed-xyz (--extra voice-local)"]


def test_missing_extra_distributions_skips_packages_this_platform_cannot_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Windows-only package is not missing when the build runs elsewhere.

    Unlike the license walk, which spans every distributed platform, this asks
    whether *this* machine can build - so the marker is evaluated for real.
    """
    other = "linux" if sys.platform == "win32" else "win32"
    (tmp_path / "pyproject.toml").write_text(
        "[project.optional-dependencies]\n"
        f'desktop = ["definitely-not-installed-xyz>=1.0; sys_platform == \'{other}\'"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(build_desktop, "ROOT", tmp_path)
    assert build_desktop.missing_extra_distributions(("desktop",)) == []


def test_building_without_the_extra_is_refused_before_pyinstaller_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal is the whole point, and its reason inverted on 2026-08-29.

    It used to guard a bundle that shipped `_internal/num2words/` and would
    silently ship nothing when `collect_all` found no package. It now guards the
    opposite: `voice_closure_top_levels()` reads the installed distributions'
    metadata to build the spec's excludes, so a build without the extra excludes
    too little and reships the closure - and `verify_bundle_contents` would pass,
    because in an environment that never had the closure there is nothing to
    exclude and nothing to notice.
    """
    (tmp_path / "pyproject.toml").write_text(
        "[project.optional-dependencies]\n"
        'voice-local = ["definitely-not-installed-xyz>=1.0"]\n'
        "desktop = []\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(build_desktop, "ROOT", tmp_path)
    with pytest.raises(SystemExit) as raised:
        build_desktop.verify_build_extras_installed()
    message = str(raised.value)
    assert "definitely-not-installed-xyz" in message
    assert "--extra voice-local" in message
    # Says why the extra is required by a build that no longer ships it.
    assert "prove its own absence" in message


def test_the_spec_excludes_the_voice_closure_rather_than_collecting_it() -> None:
    """The inverse of what this test asserted until 2026-08-29, and deliberately.

    `spacy`, `en_core_web_sm`, `misaki` and `num2words` were in the `collect_all`
    loop because PyInstaller's source graph follows none of the ways they are
    reached - a registry, a data package, a lexicon, a module-scope import. They
    are now excluded for the same reason, inverted: a graph that cannot see an
    import also cannot be trusted to leave the package out, and `collect_all` for
    `PIL` or a contributed hook could drag one back in.

    Read as a pair with `verify_voice_closure_absent`, which asserts the result on
    the built tree. This asserts the mechanism is still wired; that asserts it
    worked.
    """
    spec = (REPO_ROOT / "packaging" / "swe_mux.spec").read_text(encoding="utf-8")
    assert "EXCLUDED_VOICE_CLOSURE = list(voice_closure_top_levels())" in spec
    assert "*EXCLUDED_VOICE_CLOSURE" in spec
    for name in ("spacy", "en_core_web_sm", "misaki", "num2words"):
        assert f'    "{name}",\n' not in spec, f"{name} is back in the collect_all loop"


# ----------------------------------------------------------------- the project's own terms


@pytest.mark.parametrize(
    "name", ["LICENSE", "NOTICE", "CONTRIBUTING.md", "TRADEMARK.md", "THIRD-PARTY-NOTICES.md"]
)
def test_licensing_files_exist(name: str) -> None:
    assert (REPO_ROOT / name).is_file(), f"{name} is required before publication"


def test_license_is_the_full_apache_text() -> None:
    text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in text
    assert "Version 2.0, January 2004" in text
    assert "END OF TERMS AND CONDITIONS" in text
    # §6 is the trademark reservation TRADEMARK.md exists to give a policy to.
    assert "6. Trademarks." in text


def test_contributing_requires_a_dco_signoff_and_not_a_cla() -> None:
    text = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "Developer Certificate of Origin" in text
    assert "Signed-off-by" in text
    assert "git commit -s" in text


def test_the_vendor_relationship_is_stated_where_the_vendors_are_named() -> None:
    """Nominative use of a vendor's mark needs the disclaimer beside it."""
    for relative in ("README.md", "NOTICE", "TRADEMARK.md", "site/index.html"):
        raw = (REPO_ROOT / relative).read_text(encoding="utf-8")
        # Collapsed, because every one of these files hard-wraps its prose and
        # the disclaimer straddles a line break in three of the four.
        text = re.sub(r"\s+", " ", raw).lower()
        assert "anthropic" in text and "openai" in text, relative
        assert "not affiliated" in text, relative


def test_the_wheel_declares_its_license() -> None:
    """An undeclared license publishes a permissive project as all-rights-reserved.

    `pyproject.toml` carried no license field at all, so the wheel's metadata
    said nothing while the repository said Apache-2.0. Metadata silence is the
    one direction that reads as proprietary, so it is pinned here rather than
    left to a release checklist.
    """
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'license = "Apache-2.0"' in pyproject
    for name in ("LICENSE", "NOTICE", "THIRD-PARTY-NOTICES.md"):
        assert name in pyproject, f"{name} must be carried into the wheel by license-files"


def test_no_unresolved_placeholder_remains_in_the_public_site() -> None:
    """`github.com/REPLACE/swe-mux` shipped in the landing page for months."""
    assert "REPLACE" not in (REPO_ROOT / "site" / "index.html").read_text(encoding="utf-8")
