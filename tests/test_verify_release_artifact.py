"""Phase 11: the release-artifact gate that catches a wheel with no frontend.

`src/swe_mux/static/index.html` and `static/assets/` are gitignored build
output, and hatchling includes them only if they are on disk. So a wheel built
from a clean clone contains no UI, builds cleanly, and is indistinguishable
from a good one - which is the failure `packaging/verify_release_artifact.py`
exists to make loud.

Every wheel here is a synthetic zip built in `tmp_path`. Nothing invokes a real
`uv build`: the checks are pure functions over zip entries and the two files
inside one, so a real build would add a minute of PyInstaller-adjacent work and
prove nothing the construction does not. The staleness case in particular is
*easier* to construct by hand than to produce with a real toolchain.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

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


verify_release_artifact = _load("verify_release_artifact")


# --------------------------------------------------------------------------- fixtures


VERSION = "0.1.0"
DIST_INFO = f"swe_mux-{VERSION}.dist-info"

METADATA = (
    "Metadata-Version: 2.4\n"
    "Name: swe-mux\n"
    f"Version: {VERSION}\n"
    "License-Expression: Apache-2.0\n"
    "License-File: LICENSE\n"
    "\n"
)

ENTRY_JS = "index-B7a0a8Iw.js"
ENTRY_CSS = "index-Bo9aDfkM.css"
UI_BUILD = "6f18c770da1072c8105afe6f92822a030aaf924dbf754d4ccdafb52f0b082fb5"


def index_html(
    script: str = ENTRY_JS, stylesheet: str = ENTRY_CSS, identity: str = UI_BUILD
) -> str:
    """A production `index.html` in the exact shape vite emits one.

    Attribute order, the boolean `crossorigin`, the absolute `/assets/` base,
    and the injected `ui-build` meta are all copied from a real build, because
    each of them is something the scanner has to survive.
    """
    meta = f'\n    <meta name="ui-build" content="{identity}">' if identity else ""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "  <head>\n"
        '    <meta charset="UTF-8" />\n'
        '    <link rel="manifest" href="/manifest.webmanifest" />\n'
        '    <link rel="icon" href="/icons/icon-192.png" />\n'
        "    <title>swe-mux</title>\n"
        f'    <script type="module" crossorigin src="/assets/{script}"></script>\n'
        f'    <link rel="stylesheet" crossorigin href="/assets/{stylesheet}">{meta}\n'
        "  </head>\n"
        '  <body><div id="app"></div></body>\n'
        "</html>\n"
    )


# A stand-in for `src/swe_mux/assets/`, so these tests own their own expectation.
# Pointing them at the real source tree would make them assert what today's guide
# set happens to be, which belongs in one place (`test_the_real_source_asset_set...`
# at the bottom of this file) rather than in every case.
SHIPPED = (
    ("configurator/orientation.md", "# guide\n"),
    ("configurator/settings.md", "# guide\n"),
    ("omp_mux_hook.ts", "export {}\n"),
)
SHIPPED_IN_WHEEL = tuple(f"swe_mux/assets/{relative}" for relative, _ in SHIPPED)


def shipped_asset_tree(root: Path) -> None:
    for relative, body in SHIPPED:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def build_wheel(
    path: Path,
    *,
    entries: dict[str, str] | None = None,
    with_frontend: bool = True,
    with_assets: bool = True,
    with_licenses: bool = True,
    license_expression: str | None = "Apache-2.0",
    html: str | None = None,
    static_assets: tuple[str, ...] = (ENTRY_JS, ENTRY_CSS),
    shipped: tuple[str, ...] = SHIPPED_IN_WHEEL,
) -> Path:
    """Write a wheel-shaped zip. Every knob here is one way a release goes wrong."""
    members: dict[str, str] = {
        "swe_mux/__init__.py": "",
        "swe_mux/server.py": "# daemon\n",
        "swe_mux/static/sw.js": "// service worker\n",
        f"{DIST_INFO}/WHEEL": "Wheel-Version: 1.0\n",
        f"{DIST_INFO}/RECORD": "",
    }
    metadata = METADATA
    if license_expression is None:
        metadata = "".join(
            line for line in METADATA.splitlines(keepends=True)
            if not line.startswith("License-Expression:")
        )
    elif license_expression != "Apache-2.0":
        metadata = METADATA.replace(
            "License-Expression: Apache-2.0", f"License-Expression: {license_expression}"
        )
    members[f"{DIST_INFO}/METADATA"] = metadata

    if with_frontend:
        members["swe_mux/static/index.html"] = index_html() if html is None else html
        for name in static_assets:
            members[f"swe_mux/static/assets/{name}"] = f"/* {name} */\n"
            # The compression postbuild writes a `.gz` beside every asset; they
            # are in the wheel too and must not be counted as .js entry points.
            members[f"swe_mux/static/assets/{name}.gz"] = "\x1f\x8b"
    elif html is not None:
        members["swe_mux/static/index.html"] = html

    if with_assets:
        for name in shipped:
            members[name] = "# shipped\n"
    if with_licenses:
        for name in ("LICENSE", "NOTICE", "THIRD-PARTY-NOTICES.md"):
            members[f"{DIST_INFO}/licenses/{name}"] = f"{name} text\n"
    members.update(entries or {})

    with zipfile.ZipFile(path, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return path


@pytest.fixture
def source_assets(tmp_path: Path) -> Path:
    root = tmp_path / "source-assets"
    shipped_asset_tree(root)
    return root


def run(wheel: Path, source_assets: Path):
    return verify_release_artifact.verify(wheel, source_assets)


def verdict(report, name: str) -> bool:
    return next(check.ok for check in report.checks if check.name == name)


def message(report, name: str) -> str:
    check = next(item for item in report.checks if item.name == name)
    return f"{check.detail} {check.remedy}"


# --------------------------------------------------------------------------- the good case


def test_a_complete_wheel_passes_every_check(tmp_path: Path, source_assets: Path) -> None:
    report = run(build_wheel(tmp_path / "good.whl"), source_assets)
    assert report.ok, verify_release_artifact.render(report)
    assert [check.name for check in report.checks] == [
        "artifact-readable",
        "frontend-entry",
        "frontend-assets",
        "frontend-consistency",
        "shipped-assets",
        "license-files",
        "license-metadata",
    ]


def test_every_passing_check_still_says_what_it_observed(
    tmp_path: Path, source_assets: Path
) -> None:
    """A validator that is silent on success cannot be told from one that skipped.

    That is the same failure the artifact itself has - a wheel with no frontend
    builds silently - so the gate must not reproduce it in its own output.
    """
    report = run(build_wheel(tmp_path / "good.whl"), source_assets)
    for check in report.checks:
        assert check.detail.strip(), f"{check.name} passed without saying what it saw"
    rendered = verify_release_artifact.render(report)
    assert "Artifact valid (7 checks passed)." in rendered
    assert "FAIL" not in rendered


def test_lazy_chunks_and_gz_sidecars_are_not_treated_as_staleness(
    tmp_path: Path, source_assets: Path
) -> None:
    """The join runs one way only: unreferenced assets are normal, not evidence.

    Every `import()`ed route is its own chunk reached from the entry rather than
    from the HTML, and the compression postbuild doubles the file count. A
    two-way comparison would fail every real wheel.
    """
    wheel = build_wheel(
        tmp_path / "chunks.whl",
        static_assets=(ENTRY_JS, ENTRY_CSS, "CodeEditor-QAd81Ec_.js", "haskell-Cw1EW3IL.js"),
    )
    report = run(wheel, source_assets)
    assert report.ok, verify_release_artifact.render(report)
    assert "further asset file(s) are lazily-loaded chunks" in message(
        report, "frontend-consistency"
    )


# ------------------------------------------------------------------- the frontend failures


def test_a_wheel_built_from_a_clean_clone_fails_on_the_missing_index(
    tmp_path: Path, source_assets: Path
) -> None:
    """The bug in full: no `npm run build` ran, so hatchling shipped no UI.

    Everything else about the wheel is correct, which is exactly why this is
    invisible without the check.
    """
    report = run(build_wheel(tmp_path / "no-ui.whl", with_frontend=False), source_assets)
    assert not report.ok
    assert verdict(report, "frontend-entry") is False
    assert verdict(report, "frontend-assets") is False
    assert verdict(report, "frontend-consistency") is False
    # The parts that have nothing to do with the frontend still pass, so the
    # diagnostic points at the build step rather than at the whole artifact.
    assert verdict(report, "shipped-assets") is True
    assert verdict(report, "license-files") is True
    assert verdict(report, "license-metadata") is True


def test_the_missing_index_says_which_command_produces_it(
    tmp_path: Path, source_assets: Path
) -> None:
    report = run(build_wheel(tmp_path / "no-ui.whl", with_frontend=False), source_assets)
    text = message(report, "frontend-entry")
    assert "swe_mux/static/index.html" in text
    assert "npm --prefix frontend run build" in text
    assert "uv build --wheel" in text


def test_an_index_with_no_assets_beside_it_fails(tmp_path: Path, source_assets: Path) -> None:
    """Half a bundle: the HTML survived a clean, the hashed chunks did not."""
    wheel = build_wheel(
        tmp_path / "html-only.whl", with_frontend=False, html=index_html()
    )
    report = run(wheel, source_assets)
    assert not report.ok
    assert verdict(report, "frontend-entry") is True
    assert verdict(report, "frontend-assets") is False
    assert "no .js file" in message(report, "frontend-assets")


def test_gz_sidecars_alone_do_not_satisfy_the_asset_check(
    tmp_path: Path, source_assets: Path
) -> None:
    """`.js.gz` is not a `.js`; a tree of only sidecars serves nothing."""
    wheel = build_wheel(
        tmp_path / "gz-only.whl",
        with_frontend=False,
        html=index_html(),
        entries={f"swe_mux/static/assets/{ENTRY_JS}.gz": "\x1f\x8b"},
    )
    report = run(wheel, source_assets)
    assert verdict(report, "frontend-assets") is False


def test_a_stale_index_beside_fresh_assets_fails_the_join(
    tmp_path: Path, source_assets: Path
) -> None:
    """The check that matters. Both halves are present; they are different builds.

    Presence alone passes here - there is an index.html and there are .js
    assets - and only the content-hash join notices that the entry chunk the
    HTML asks the browser to load is not in the wheel.
    """
    wheel = build_wheel(
        tmp_path / "stale.whl",
        html=index_html(script="index-OLDHASH1.js", stylesheet="index-OLDHASH2.css"),
    )
    report = run(wheel, source_assets)
    assert not report.ok
    assert verdict(report, "frontend-entry") is True
    assert verdict(report, "frontend-assets") is True
    assert verdict(report, "frontend-consistency") is False
    text = message(report, "frontend-consistency")
    assert "index-OLDHASH1.js" in text and "index-OLDHASH2.css" in text
    assert "different builds" in text
    assert "npm --prefix frontend run build" in text


def test_a_partially_stale_bundle_names_only_the_missing_reference(
    tmp_path: Path, source_assets: Path
) -> None:
    """A stylesheet that survived a rebuild while its script did not."""
    wheel = build_wheel(
        tmp_path / "partial.whl", html=index_html(script="index-GONE.js")
    )
    report = run(wheel, source_assets)
    assert verdict(report, "frontend-consistency") is False
    text = message(report, "frontend-consistency")
    assert "index-GONE.js" in text
    assert ENTRY_CSS not in text.split("does not")[1].split(".")[0]


def test_an_unbuilt_template_index_is_rejected_even_with_assets_present(
    tmp_path: Path, source_assets: Path
) -> None:
    """`frontend/index.html` copied in by hand references no hashed chunk at all."""
    template = (
        "<!doctype html>\n<html><head><title>swe-mux</title></head>"
        '<body><div id="app"></div><script type="module" src="/src/main.tsx"></script>'
        "</body></html>\n"
    )
    report = run(build_wheel(tmp_path / "template.whl", html=template), source_assets)
    assert verdict(report, "frontend-consistency") is False
    assert "references no file under assets/" in message(report, "frontend-consistency")


def test_an_index_without_the_build_identity_meta_is_not_a_release_artifact(
    tmp_path: Path, source_assets: Path
) -> None:
    """Only `vite build` injects `ui-build`; the dev server deliberately does not.

    A hand-assembled index.html whose filenames happen to line up passes the
    join, so the provenance marker is what separates a built bundle from a
    convincing one.
    """
    wheel = build_wheel(tmp_path / "no-identity.whl", html=index_html(identity=""))
    report = run(wheel, source_assets)
    assert verdict(report, "frontend-consistency") is False
    assert "ui-build" in message(report, "frontend-consistency")


def test_a_relative_asset_base_is_read_the_same_way(
    tmp_path: Path, source_assets: Path
) -> None:
    """`base` is a vite setting, not a fact about whether the bundle is complete."""
    html = index_html().replace('"/assets/', '"./assets/')
    report = run(build_wheel(tmp_path / "relative.whl", html=html), source_assets)
    assert report.ok, verify_release_artifact.render(report)


def test_a_lookalike_directory_is_not_mistaken_for_the_asset_base(
    tmp_path: Path, source_assets: Path
) -> None:
    """`/my-assets/x.js` does not live under `assets/` and must not be joined."""
    assert verify_release_artifact.referenced_assets(
        '<script src="/my-assets/x.js"></script><link href="/assets/real.css">'
    ) == ["real.css"]


# --------------------------------------------------------------------- the shipped guides


def test_a_missing_shipped_guide_fails_and_is_named(
    tmp_path: Path, source_assets: Path
) -> None:
    """These read correctly from source and are silently absent for every user."""
    wheel = build_wheel(
        tmp_path / "no-guide.whl",
        shipped=("swe_mux/assets/configurator/orientation.md", "swe_mux/assets/omp_mux_hook.ts"),
    )
    report = run(wheel, source_assets)
    assert not report.ok
    assert verdict(report, "shipped-assets") is False
    text = message(report, "shipped-assets")
    assert "swe_mux/assets/configurator/settings.md" in text
    assert "src/swe_mux/assets/**" in text


def test_the_expectation_is_derived_from_the_source_tree_not_a_hard_coded_count(
    tmp_path: Path, source_assets: Path
) -> None:
    """A guide added under `src/swe_mux/assets/` is covered the day it lands."""
    (source_assets / "configurator" / "brand-new.md").write_text("# new\n", encoding="utf-8")
    report = run(build_wheel(tmp_path / "good.whl"), source_assets)
    assert verdict(report, "shipped-assets") is False
    assert "swe_mux/assets/configurator/brand-new.md" in message(report, "shipped-assets")


def test_build_caches_in_the_source_tree_are_not_expected_in_the_wheel(
    tmp_path: Path, source_assets: Path
) -> None:
    """`edge_tts_bridge.py` is a real module, so `__pycache__` appears beside it."""
    cache = source_assets / "integrations" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "bridge.cpython-312.pyc").write_bytes(b"\x00")
    report = run(build_wheel(tmp_path / "good.whl"), source_assets)
    assert verdict(report, "shipped-assets") is True


def test_an_unreadable_source_tree_degrades_to_presence_and_says_so(
    tmp_path: Path
) -> None:
    """Checking a downloaded wheel from outside a checkout is a legitimate use.

    Reporting an empty expectation as a clean pass would be the wrong answer,
    so the check narrows to presence and labels the reading as narrowed.
    """
    absent = tmp_path / "not-a-checkout"
    report = run(build_wheel(tmp_path / "good.whl"), absent)
    assert report.ok
    assert "presence only" in message(report, "shipped-assets")


def test_an_unreadable_source_tree_still_fails_on_an_empty_asset_directory(
    tmp_path: Path
) -> None:
    absent = tmp_path / "not-a-checkout"
    report = run(build_wheel(tmp_path / "bare.whl", with_assets=False), absent)
    assert not report.ok
    assert verdict(report, "shipped-assets") is False
    assert "expected set is unknown" in message(report, "shipped-assets")


# ------------------------------------------------------------------------------- licensing


def test_missing_license_files_are_named_individually(
    tmp_path: Path, source_assets: Path
) -> None:
    report = run(build_wheel(tmp_path / "nolic.whl", with_licenses=False), source_assets)
    assert not report.ok
    text = message(report, "license-files")
    for name in ("LICENSE", "NOTICE", "THIRD-PARTY-NOTICES.md"):
        assert name in text
    assert "license-files" in text  # points at the pyproject key that carries them


def test_a_partial_license_set_names_only_what_is_absent(
    tmp_path: Path, source_assets: Path
) -> None:
    wheel = build_wheel(tmp_path / "partial-lic.whl", with_licenses=False)
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr(f"{DIST_INFO}/licenses/LICENSE", "Apache License\n")
        archive.writestr(f"{DIST_INFO}/licenses/NOTICE", "notice\n")
    report = run(wheel, source_assets)
    text = message(report, "license-files")
    assert "THIRD-PARTY-NOTICES.md" in text
    assert text.split("missing from")[0].count("LICENSE") == 0


def test_an_undeclared_license_expression_fails(tmp_path: Path, source_assets: Path) -> None:
    """Metadata silence is the one direction that reads as all-rights-reserved."""
    wheel = build_wheel(tmp_path / "unlicensed.whl", license_expression=None)
    report = run(wheel, source_assets)
    assert not report.ok
    text = message(report, "license-metadata")
    assert "(absent)" in text
    assert "all-rights-reserved" in text
    assert 'license = "Apache-2.0"' in text


def test_a_changed_license_expression_fails_and_is_a_different_fact(
    tmp_path: Path, source_assets: Path
) -> None:
    """A declared-but-wrong expression is a disagreement, not an omission.

    Reporting both as "metadata silence" would tell a reader to look for a
    missing field that is right there.
    """
    wheel = build_wheel(tmp_path / "mit.whl", license_expression="MIT")
    report = run(wheel, source_assets)
    assert verdict(report, "license-metadata") is False
    text = message(report, "license-metadata")
    assert "MIT" in text
    assert "disagree" in text
    assert "all-rights-reserved" not in text


def test_a_wheel_with_no_dist_info_fails_both_license_checks(
    tmp_path: Path, source_assets: Path
) -> None:
    wheel = tmp_path / "not-a-wheel.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("swe_mux/__init__.py", "")
    report = run(wheel, source_assets)
    assert verdict(report, "license-files") is False
    assert verdict(report, "license-metadata") is False
    assert "not a valid wheel" in message(report, "license-files")


# ------------------------------------------------------------------------- unreadable input


def test_a_missing_wheel_reports_the_path_rather_than_raising(
    tmp_path: Path, source_assets: Path
) -> None:
    report = run(tmp_path / "never-built.whl", source_assets)
    assert not report.ok
    assert "does not exist" in message(report, "artifact-readable")
    assert "uv build --wheel" in message(report, "artifact-readable")


def test_a_file_that_is_not_a_zip_reports_that(tmp_path: Path, source_assets: Path) -> None:
    wheel = tmp_path / "truncated.whl"
    wheel.write_bytes(b"not a zip archive")
    report = run(wheel, source_assets)
    assert not report.ok
    assert "could not be read as a wheel" in message(report, "artifact-readable")


# ----------------------------------------------------------------------------- the CLI


def test_the_cli_exits_zero_on_a_good_wheel(
    tmp_path: Path, source_assets: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(verify_release_artifact, "SOURCE_ASSETS", source_assets)
    wheel = build_wheel(tmp_path / "good.whl")
    assert verify_release_artifact.main([str(wheel)]) == 0
    assert "Artifact valid" in capsys.readouterr().out


def test_the_cli_exits_nonzero_with_a_remedy_on_a_bad_wheel(
    tmp_path: Path, source_assets: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(verify_release_artifact, "SOURCE_ASSETS", source_assets)
    wheel = build_wheel(tmp_path / "no-ui.whl", with_frontend=False)
    assert verify_release_artifact.main([str(wheel)]) == 1
    out = capsys.readouterr().out
    assert "Release artifact validation FAILED" in out
    assert "npm --prefix frontend run build" in out


def test_json_output_carries_every_verdict_and_its_evidence(
    tmp_path: Path, source_assets: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(verify_release_artifact, "SOURCE_ASSETS", source_assets)
    wheel = build_wheel(tmp_path / "stale.whl", html=index_html(script="index-OLDHASH1.js"))
    assert verify_release_artifact.main(["--json", str(wheel)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert {check["name"] for check in payload["checks"]} == {
        "artifact-readable",
        "frontend-entry",
        "frontend-assets",
        "frontend-consistency",
        "shipped-assets",
        "license-files",
        "license-metadata",
    }
    assert "index-OLDHASH1.js" in payload["evidence"]["referenced_assets"]
    assert ENTRY_JS in payload["evidence"]["static_assets"]
    assert payload["evidence"]["ui_build_id"] == UI_BUILD
    assert payload["evidence"]["dist_info"] == DIST_INFO


def test_json_output_is_emitted_even_when_the_wheel_cannot_be_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """A CI step parsing stdout must not have to special-case the worst failure."""
    monkeypatch.setattr(verify_release_artifact, "SOURCE_ASSETS", tmp_path / "absent")
    assert verify_release_artifact.main(["--json", str(tmp_path / "nope.whl")]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["checks"][0]["name"] == "artifact-readable"
    # The evidence keys are present and empty rather than absent, so a CI step
    # reading the report never has to branch on how badly the wheel failed.
    assert payload["evidence"]["static_assets"] == []
    assert payload["evidence"]["ui_build_id"] is None
    assert payload["evidence"]["entry_count"] == 0


def test_the_source_assets_default_is_resolved_at_call_time(
    tmp_path: Path, source_assets: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards the indirection the CLI relies on to reach the real checkout.

    A `source_assets=SOURCE_ASSETS` default would bind at import and make the
    module constant unpatchable, which would silently turn every CLI run into
    the degraded presence-only reading.
    """
    monkeypatch.setattr(verify_release_artifact, "SOURCE_ASSETS", source_assets)
    report = verify_release_artifact.verify(build_wheel(tmp_path / "good.whl"))
    assert report.evidence["expected_shipped_assets"] == sorted(SHIPPED_IN_WHEEL)


# ------------------------------------------------------------- the real repository's shape


def test_the_real_source_asset_set_is_what_the_wheel_must_carry() -> None:
    """Ties the derived expectation to the checkout the gate actually runs in."""
    expected = verify_release_artifact.expected_shipped_assets(
        verify_release_artifact.SOURCE_ASSETS
    )
    assert expected, "src/swe_mux/assets/ must exist; these files are tracked"
    assert "swe_mux/assets/configurator/orientation.md" in expected
    assert "swe_mux/assets/omp_mux_hook.ts" in expected
    assert not any(item.endswith(".pyc") for item in expected)


def test_pyproject_still_carries_both_artifact_trees_into_the_wheel() -> None:
    """The checks are meaningless if hatchling stops being asked for these paths."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "src/swe_mux/static/**" in pyproject
    assert "src/swe_mux/assets/**" in pyproject
