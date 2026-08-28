"""What the published metadata claims, and whether an installer can satisfy it.

`swe-mux` 0.1.0 shipped a `voice-local` extra that **nobody could install**. The
wheel declared `Requires-Dist: en-core-web-sm`, which exists on no index, so both
`pip install "swe-mux[voice-local]"` and `uv pip install "swe-mux[voice-local]"`
refused the extra outright (measured:
`.docs/development/DEPENDENCY_AUDIT_2026-08-28.md` § 4). It resolved perfectly in
this repository the whole time, because `[tool.uv.sources]` pointed it at a GitHub
release - and a `uv` source is a property of *this* project's resolution that is
not carried in a wheel's `Requires-Dist`.

That is the shape this module exists to make impossible to repeat, and the reason
it is a test rather than a note: nothing else in the repository, in the gate, or
in CI installs any extra from an index, so the defect was invisible for the whole
life of the release. Every check here is offline and total - it reads the two
declarations and compares them, and never needs a network or a built wheel.

The rule in one sentence: **anything `[tool.uv.sources]` has to redirect cannot be
a published requirement**, so it lives in a PEP 735 dependency group, which a
wheel never carries.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

REPO_ROOT = Path(__file__).resolve().parents[1]


def _manifest() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _lock() -> dict:
    return tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))


def _published_requirements() -> list[tuple[str, str]]:
    """Every `(where, name)` a wheel's `Requires-Dist` will carry.

    Base dependencies and every extra. Dependency *groups* are deliberately not
    here: PEP 735 groups are never published, which is the whole mechanism this
    module is about.
    """
    manifest = _manifest()
    project = manifest["project"]
    found = [("dependencies", Requirement(entry).name) for entry in project["dependencies"]]
    for extra, entries in project.get("optional-dependencies", {}).items():
        found += [(f"optional-dependencies.{extra}", Requirement(entry).name) for entry in entries]
    return found


def _normalize(name: str) -> str:
    return name.lower().replace("_", "-")


def test_no_published_requirement_needs_a_uv_source_to_resolve() -> None:
    """The 0.1.0 defect, stated as the rule that would have caught it.

    `[tool.uv.sources]` exists for requirements that no index can satisfy. Naming
    one in `[project.dependencies]` or in an extra publishes a requirement that
    only this checkout can meet, and every downstream installer refuses the whole
    extra rather than degrading - which is worse than the dependency being absent.
    """
    declared = _manifest().get("tool", {}).get("uv", {}).get("sources", {})
    sources = {_normalize(name) for name in declared}
    assert sources, "[tool.uv.sources] is empty; this guard would assert nothing"
    offenders = [
        f"{where}: {name}"
        for where, name in _published_requirements()
        if _normalize(name) in sources
    ]
    assert offenders == [], (
        "these are published requirements that only [tool.uv.sources] can resolve, so "
        "`pip install swe-mux[...]` will refuse them outright: " + ", ".join(offenders)
    )


def test_every_published_requirement_resolves_from_a_registry_in_the_lock() -> None:
    """The same rule from the other side, and the stronger of the two.

    A `uv` source can be given on the *dependency* rather than on the project
    (`[tool.uv]` overrides, a workspace member, a future `--find-links`), so the
    check that actually generalises is what `uv.lock` recorded: a published
    requirement has to have resolved from a registry. Anything with a `url`,
    `git`, `path`, or `directory` source is a requirement this project can meet
    and a user cannot.
    """
    packages = {_normalize(entry["name"]): entry for entry in _lock().get("package", [])}
    offenders = []
    for where, name in _published_requirements():
        entry = packages.get(_normalize(name))
        if entry is None:
            # A requirement that is not in the lock at all is its own bug, and
            # naming it here is better than a KeyError from a test.
            offenders.append(f"{where}: {name} (absent from uv.lock)")
            continue
        source = entry.get("source", {})
        if "registry" not in source:
            offenders.append(f"{where}: {name} (source={sorted(source)})")
    assert offenders == [], (
        "a published requirement must resolve from a package index: " + ", ".join(offenders)
    )


def test_the_spacy_model_is_declared_where_a_wheel_cannot_carry_it() -> None:
    """The positive half: the model is still resolved, just not published.

    Dropping the declaration outright would have been a different bug - the
    development checkout, both CI legs and the desktop build all need the model
    present, and `test_kokoro_tts.py`'s real-G2P tests are `importorskip`-guarded,
    so its absence would show up as silent skips rather than as a failure.
    """
    manifest = _manifest()
    groups = manifest["dependency-groups"]
    assert [Requirement(entry).name for entry in groups["g2p-model"]] == ["en-core-web-sm"]
    # `dev` is uv's default group, so a bare `uv sync` and `.worktree-setup`'s
    # `uv sync --extra voice-local` both install it with no extra flag; `package`
    # is what `release.yml` and a desktop build sync.
    for group in ("dev", "package"):
        assert {"include-group": "g2p-model"} in groups[group], (
            f"the {group} group no longer includes g2p-model, so the environments that "
            "build and test the Kokoro G2P would resolve without its spaCy model"
        )


def test_every_module_scope_third_party_import_of_push_is_declared() -> None:
    """`push.py` is imported by the daemon at startup, so its imports are hard.

    `cryptography` and `py-vapid` arrived transitively through `pywebpush` for
    the whole life of the project, which worked and was an assumption about
    somebody else's dependency graph rather than a claim this one had checked.
    The failure mode of that assumption breaking is not a degraded feature, it is
    a daemon that will not start.
    """
    source = (REPO_ROOT / "src" / "swe_mux" / "push.py").read_text(encoding="utf-8")
    declared = {_normalize(name) for _where, name in _published_requirements()}
    # module -> distribution, for the ones whose import name differs.
    for module, distribution in (
        ("cryptography", "cryptography"),
        ("py_vapid", "py-vapid"),
        ("pywebpush", "pywebpush"),
    ):
        assert f"from {module}" in source or f"import {module}" in source
        assert _normalize(distribution) in declared, (
            f"{module} is imported at module scope by push.py and is not declared"
        )


@pytest.mark.parametrize(
    ("module", "distribution"),
    [("numpy", "numpy"), ("ctranslate2", "ctranslate2")],
)
def test_the_voice_local_extra_declares_what_its_code_imports(
    module: str, distribution: str
) -> None:
    """Lazy imports inside a gated feature are still direct dependencies.

    Lower stakes than `push.py` - both call sites are lazy and inside features
    that already answer with a typed diagnostic - and exactly the same rule.
    """
    manifest = _manifest()
    names = {
        _normalize(Requirement(entry).name)
        for entry in manifest["project"]["optional-dependencies"]["voice-local"]
    }
    assert _normalize(distribution) in names
    sources = "".join(
        (REPO_ROOT / "src" / "swe_mux" / name).read_text(encoding="utf-8")
        for name in ("voice.py", "kokoro_tts.py")
    )
    assert f"import {module}" in sources
