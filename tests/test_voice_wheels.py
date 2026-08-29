"""The pinned voice closure: that it describes `uv.lock`, and that it is selectable.

ROADMAP Phase 21 Workstream D. The desktop bundle no longer ships the on-device
speech libraries; `swe_mux.voice_runtime` downloads them from pins generated out
of `uv.lock` by `packaging/generate_voice_pins.py`.

The property these tests exist for is that the generated table and the lockfile
never disagree. A stale table is not a build failure or a broken import - it is a
first-use download of a closure this repository never audited, which no other gate
here would notice.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from swe_mux import voice_wheels

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    """Import a `packaging/` script by path; it is not an installed package."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "packaging" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


generate_voice_pins = _load("generate_voice_pins")


def test_the_committed_pin_table_matches_a_fresh_generation() -> None:
    """The gate this whole module exists for.

    Regenerate with `uv run python packaging/generate_voice_pins.py --write` after
    any change to the `voice-local` extra, the `g2p-model` group, or their
    resolution, and commit the result.
    """
    lock = generate_voice_pins.load_lock()
    names = generate_voice_pins.acquired_packages(lock)
    rows = generate_voice_pins.wheel_rows(lock, names)
    expected = generate_voice_pins.render(rows, names)
    actual = (REPO_ROOT / "src" / "swe_mux" / "voice_wheels.py").read_text(encoding="utf-8")
    assert actual == expected, (
        "src/swe_mux/voice_wheels.py is stale against uv.lock. Run "
        "`uv run python packaging/generate_voice_pins.py --write`."
    )


def test_the_closure_digest_covers_every_pin() -> None:
    """The digest is the identity a state file carries, so it must move with a pin.

    A digest that did not change when a pin did would let an install keep serving
    a tree built from the previous closure while reporting the current one.
    """
    lock = generate_voice_pins.load_lock()
    rows = generate_voice_pins.wheel_rows(lock, generate_voice_pins.acquired_packages(lock))
    assert generate_voice_pins.lock_digest(rows) == voice_wheels.CLOSURE_DIGEST
    tampered = [dict(row) for row in rows]
    tampered[0]["sha256"] = "0" * 64
    assert generate_voice_pins.lock_digest(tampered) != voice_wheels.CLOSURE_DIGEST


def test_every_pin_carries_a_sha256_and_a_size() -> None:
    """No pin may be unverifiable. Both fields are what makes the fetch a check."""
    assert voice_wheels.WHEELS
    for wheel in voice_wheels.WHEELS:
        assert len(wheel.sha256) == 64
        assert set(wheel.sha256) <= set("0123456789abcdef")
        assert wheel.size > 0
        assert wheel.url.startswith("https://")
        assert wheel.filename.endswith(".whl")


def test_the_pins_come_from_the_index_rather_than_from_this_project() -> None:
    """swe-mux hosts none of these bytes, which is what the notices claim.

    `THIRD-PARTY-NOTICES.md` tells a reader that `num2words` is fetched from PyPI
    rather than redistributed by this project. A pin pointing at a swemux.dev URL
    would make that false, and would make the LGPL analysis in `license_audit.
    ACQUIRED_AT_FIRST_USE` wrong along with it.
    """
    for wheel in voice_wheels.WHEELS:
        assert "swemux" not in wheel.url
        assert wheel.url.startswith(
            ("https://files.pythonhosted.org/", "https://github.com/explosion/")
        )


def _pin_implied_bounds() -> tuple[int, int]:
    """The smallest and largest total any platform's selection can have.

    Derived from the pin table, which is the only thing in this repository that
    knows: for each distribution, the smallest and largest wheel it publishes for
    any supported target. Every real selection picks exactly one wheel per
    distribution, so every real total lies between those two sums whatever host
    computes it.

    This replaced `> 50 * 1024 * 1024`, which was a Windows measurement wearing a
    threshold's clothes. The Windows closure is 81.9 MiB and the macOS one is
    49.6 MiB, so the floor passed on the host that produced it and failed on the
    first runner that did not - the same "asked the machine a question instead of
    the repository" shape as the six failures before it.
    """
    per: dict[str, list[int]] = {}
    for wheel in voice_wheels.WHEELS:
        per.setdefault(wheel.distribution, []).append(wheel.size)
    return sum(min(sizes) for sizes in per.values()), sum(
        max(sizes) for sizes in per.values()
    )


def test_this_interpreter_can_select_one_wheel_for_every_distribution() -> None:
    """Selection is total on a supported host, and yields one wheel per distribution.

    `wheels_for_this_interpreter` refuses rather than returning a partial set,
    because a closure missing one native package fails at import time - much
    later, in a place that names the wrong thing.

    The size assertions are properties rather than magnitudes: every selected
    wheel is one the table actually pins, and the total lies inside the range the
    table implies. Both hold on every runner, and together they catch what a floor
    was reaching for - a selection that silently collapsed, or one that picked
    more than one wheel for a distribution.
    """
    selected = voice_wheels.wheels_for_this_interpreter()
    assert {wheel.distribution for wheel in selected} == set(voice_wheels.DISTRIBUTIONS)
    assert len(selected) == len(voice_wheels.DISTRIBUTIONS)
    assert set(selected) <= set(voice_wheels.WHEELS), "a selected wheel must be a pinned wheel"
    assert all(wheel.size > 0 for wheel in selected)

    smallest, largest = _pin_implied_bounds()
    total = voice_wheels.total_bytes(selected)
    assert total == sum(wheel.size for wheel in selected)
    assert smallest <= total <= largest


def test_selection_is_deterministic() -> None:
    """Two calls on one interpreter must choose the same wheels.

    `sys_tags()` is ordered, so this is a property of the implementation rather
    than a hope - but the store memoizes the answer and the state file records a
    closure digest against it, so a selection that varied would produce a tree
    that re-acquires itself.
    """
    assert voice_wheels.wheels_for_this_interpreter() == (
        voice_wheels.wheels_for_this_interpreter()
    )


def test_every_distribution_publishes_a_wheel_for_every_supported_platform() -> None:
    """The failure that can only appear on a runner, caught from the table instead.

    A distribution with no macOS wheel is a `LookupError` on macOS and nowhere
    else, and no amount of local testing would show it. Checked per distribution
    *and* per platform family, which is stronger than the tag-level coverage
    check: that one passes as long as *some* distribution has a macOS wheel.
    """
    from packaging.utils import parse_wheel_filename

    families = {
        "win_amd64": lambda p: p == "win_amd64",
        "manylinux x86_64": lambda p: "manylinux" in p and p.endswith("x86_64"),
        "macosx arm64": lambda p: p.startswith("macosx") and p.endswith(
            ("arm64", "universal2")
        ),
    }
    covered: dict[str, set[str]] = {name: set() for name in families}
    for wheel in voice_wheels.WHEELS:
        _, _, _, tags = parse_wheel_filename(wheel.filename)
        platforms = {tag.platform for tag in tags}
        for name, matches in families.items():
            if "any" in platforms or any(matches(one) for one in platforms):
                covered[name].add(wheel.distribution)

    expected = set(voice_wheels.DISTRIBUTIONS)
    for name, seen in covered.items():
        assert seen == expected, f"{name} has no wheel for: {sorted(expected - seen)}"


def test_selection_refuses_rather_than_returning_a_partial_closure() -> None:
    dropped = tuple(
        wheel for wheel in voice_wheels.WHEELS if wheel.distribution != "onnxruntime"
    )
    with pytest.raises(LookupError, match="onnxruntime"):
        voice_wheels.wheels_for_this_interpreter(dropped)


def test_the_table_covers_every_supported_platform() -> None:
    """A platform with no wheels is a platform where voice cannot be acquired.

    Checked by tag rather than by running there: the generator keeps wheels for
    the platforms swe-mux ships for, and this asserts the filter did not quietly
    drop one - which would surface only as `LookupError` on somebody else's
    machine.
    """
    from packaging.utils import parse_wheel_filename

    platforms: set[str] = set()
    for wheel in voice_wheels.WHEELS:
        _, _, _, tags = parse_wheel_filename(wheel.filename)
        platforms.update(tag.platform for tag in tags)
    assert any(name == "win_amd64" for name in platforms)
    assert any("manylinux" in name and "x86_64" in name for name in platforms)
    assert any("macosx" in name and "arm64" in name for name in platforms)
    assert "any" in platforms


def test_docopt_is_pinned_nowhere() -> None:
    """The one acquired dependency with no wheel on PyPI.

    `num2words` declares `docopt`, which has published an sdist and never a wheel
    since 2014, so a wheel-only store cannot acquire it and the generator says so
    on stderr rather than failing. Whether that is *safe* is the sibling test
    below; this half is a property of the generated table and runs everywhere.
    """
    assert not any(wheel.distribution == "docopt" for wheel in voice_wheels.WHEELS)
    assert "docopt" not in voice_wheels.DISTRIBUTIONS


@pytest.mark.skipif(
    importlib.util.find_spec("num2words") is None,
    reason=(
        "reads num2words' installed source; it arrives with the voice-local extra, "
        "which CI's Linux and macOS legs deliberately do not sync"
    ),
)
def test_num2words_does_not_import_the_dependency_that_cannot_be_pinned() -> None:
    """Why the omission above is safe, asserted rather than assumed.

    The *importable* `num2words` package does not use `docopt` - only its console
    script does - so the G2P path never touches it. If that ever stops being true
    the fix is a real one (vendor it, or drop misaki's number handling), not a
    wider pin table, and this is where the reasoning is recorded.

    Skipped rather than rewritten where the package is absent, because there is no
    honest way to ask what a package imports without the package. The pin-table
    half above is what runs on those legs.
    """
    import num2words as module

    assert module.__file__ is not None
    source = Path(module.__file__).parent
    offenders = [
        path.name
        for path in source.glob("*.py")
        if "docopt" in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert offenders == []


def test_the_generator_excludes_the_model_another_store_already_owns() -> None:
    """`en-core-web-sm` has had its own store since 2026-08-28; two would disagree."""
    lock = generate_voice_pins.load_lock()
    assert "en-core-web-sm" not in generate_voice_pins.acquired_packages(lock)
    assert not any(
        wheel.distribution == "en_core_web_sm" for wheel in voice_wheels.WHEELS
    )


def test_the_generator_keeps_the_base_application_out_of_the_acquired_set() -> None:
    """Packages the base bundle still ships must never be downloaded on top of it.

    `packaging` is the sharp case and the reason it is a declared dependency:
    `voice_wheels` imports it to select a wheel, so acquiring it would mean
    needing the closure in order to acquire the closure.
    """
    lock = generate_voice_pins.load_lock()
    acquired = set(generate_voice_pins.acquired_packages(lock))
    for name in ("aiohttp", "cryptography", "packaging", "pillow", "psutil", "mcp"):
        assert name not in acquired
