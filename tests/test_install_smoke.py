"""Phase 11: the checks `packaging/install_smoke.py` makes about an install.

Nothing here installs anything. The smoke's own value is that it runs a real
`uv pip install` into a real virtualenv, and reproducing that in the unit suite
would spend a minute per test to re-prove what the CI step already proves on
every push. What is worth pinning is the reasoning *around* the install - which
observations count as a pass, and in particular the one check whose failure mode
is a silent false pass: an import that resolved to this checkout rather than to
the installed copy would satisfy every other check in the script.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    """Import a `packaging/` script by path; it is not an installed package."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "packaging" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


install_smoke = _load("install_smoke")


INDEX_HTML = (
    '<!doctype html><html><head><meta name="ui-build" content="abc123">'
    '<link rel="stylesheet" href="/assets/index-Bo9aDfkM.css">'
    '<script type="module" src="/assets/index-B7a0a8Iw.js"></script>'
    "</head><body></body></html>"
)


def _observed(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "package": "/tmp/venv/lib/site-packages/swe_mux",
        "prefix": "/tmp/venv",
        "version": "0.1.0",
        "index_html": INDEX_HTML,
        "assets": ["index-B7a0a8Iw.js", "index-Bo9aDfkM.css", "lazy-chunk-QQ.js"],
    }
    return base | overrides


def test_an_import_from_inside_the_virtualenv_is_isolated(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    package = venv / "Lib" / "site-packages" / "swe_mux"
    package.mkdir(parents=True)
    check = install_smoke._check_import_isolation(_observed(package=str(package)), venv)
    assert check.ok, check.detail


def test_an_import_from_the_checkout_fails_isolation(tmp_path: Path) -> None:
    """The false pass this check exists to make impossible.

    A checkout satisfies every other check by itself, so a smoke that imported
    `src/swe_mux` would report a green install of a wheel it never read.
    """
    venv = tmp_path / "venv"
    venv.mkdir()
    checkout = tmp_path / "checkout" / "src" / "swe_mux"
    checkout.mkdir(parents=True)
    check = install_smoke._check_import_isolation(_observed(package=str(checkout)), venv)
    assert not check.ok
    assert "outside the virtualenv" in check.detail
    assert check.remedy


def test_a_complete_installed_frontend_passes() -> None:
    check = install_smoke._check_frontend_installed(_observed())
    assert check.ok, check.detail
    assert "2 asset(s)" in check.detail


def test_a_missing_installed_index_is_reported_as_no_ui() -> None:
    check = install_smoke._check_frontend_installed(_observed(index_html=None))
    assert not check.ok
    assert "serve no UI" in check.detail
    assert "npm --prefix frontend run build" in check.remedy


def test_an_index_whose_assets_were_not_installed_beside_it_fails() -> None:
    """Present in the wheel is not the same fact as reachable from the package.

    The zip check and this one look alike and answer different questions: this
    one reads the unpacked tree the daemon resolves off `swe_mux.__file__`.
    """
    check = install_smoke._check_frontend_installed(_observed(assets=["lazy-chunk-QQ.js"]))
    assert not check.ok
    assert "index-B7a0a8Iw.js" in check.detail
    assert "index-Bo9aDfkM.css" in check.detail


def test_an_unbuilt_index_template_is_not_a_bundle() -> None:
    check = install_smoke._check_frontend_installed(
        _observed(index_html="<!doctype html><html><body></body></html>", assets=[])
    )
    assert not check.ok
    assert "not a built bundle" in check.detail


def test_the_child_environment_carries_nothing_that_re_exposes_the_checkout(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT / "src"))
    monkeypatch.setenv("VIRTUAL_ENV", str(REPO_ROOT / ".venv"))
    environment = install_smoke._clean_environment()
    assert "PYTHONPATH" not in environment
    assert "VIRTUAL_ENV" not in environment
