"""Finding a tool that is installed but not on PATH, and re-reading PATH on demand.

These cover the onboarding half of executable resolution, which is a different
question from the launcher's (`shim_paths`) and was wrong in a way the launcher's
is not: PATH presence was treated as the definition of "installed", so a clean
Windows 11 machine with Git and a connected Tailscale reported both absent and
told the user to install what they already had.

The separation itself is load-bearing and is asserted here: widening the search
past PATH must not leak into the resolver a spawn uses, because a tool that cannot
be invoked by name is genuinely unusable to something that invokes it by name.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from swe_mux import tool_locations


def test_a_tool_on_path_is_reported_as_invocable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_locations, "which_real", lambda command: f"/usr/bin/{command}")
    location = tool_locations.locate_tool("git")
    assert location.source == "on_path"
    assert location.present is True
    assert location.invocable_by_name is True


def test_a_tool_only_at_a_well_known_location_is_present_but_not_invocable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The distinction the two-state answer could not express.

    "Present" and "spawnable by name" are different facts and the remedies differ:
    one is a PATH entry, the other is an install. Anything that builds an argv must
    use `path`, which is why `invocable_by_name` is false rather than the location
    simply being reported as found.
    """
    binary = tmp_path / "tailscale.exe"
    binary.write_text("", encoding="utf-8")
    monkeypatch.setattr(tool_locations, "which_real", lambda command: None)
    monkeypatch.setattr(tool_locations, "well_known_locations", lambda tool: (str(binary),))
    location = tool_locations.locate_tool("tailscale")
    assert location.source == "off_path"
    assert location.present is True
    assert location.invocable_by_name is False
    assert location.path == str(binary)


def test_nothing_anywhere_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_locations, "which_real", lambda command: None)
    monkeypatch.setattr(tool_locations, "well_known_locations", lambda tool: ())
    location = tool_locations.locate_tool("tailscale")
    assert location.source == "missing"
    assert location.present is False
    assert location.path is None


def test_an_override_beats_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    binary = tmp_path / "chosen"
    binary.write_text("", encoding="utf-8")
    monkeypatch.setattr(tool_locations, "which_real", lambda command: "/usr/bin/git")
    location = tool_locations.locate_tool("git", override=str(binary))
    assert location.source == "override"
    assert location.path == str(binary)


def test_an_override_that_no_longer_exists_falls_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stale entry must degrade to detection, never hide a working install."""
    monkeypatch.setattr(tool_locations, "which_real", lambda command: "/usr/bin/git")
    location = tool_locations.locate_tool("git", override=str(tmp_path / "gone"))
    assert location.source == "on_path"
    assert location.path == "/usr/bin/git"


def test_an_override_expands_environment_variables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Each platform's own variable spelling, because that is the contract.

    `os.path.expandvars` reads `%VAR%` only on Windows; POSIX expands `$VAR`.
    An override is typed by a user on their machine in their shell's spelling,
    so the test asserts the spelling that platform actually documents rather
    than exporting this host's syntax to every runner.
    """
    binary = tmp_path / "git"
    binary.write_text("", encoding="utf-8")
    monkeypatch.setenv("MUX_TEST_TOOL_ROOT", str(tmp_path))
    monkeypatch.setattr(tool_locations, "which_real", lambda command: None)
    spelling = "%MUX_TEST_TOOL_ROOT%/git" if os.name == "nt" else "$MUX_TEST_TOOL_ROOT/git"
    location = tool_locations.locate_tool("git", override=spelling)
    assert location.source == "override"


def test_widening_stays_out_of_the_launcher_resolver() -> None:
    """`which_real` is what a spawn uses and must keep PATH semantics exactly.

    Widening it would mean an agent CLI "found" at a well-known path and then
    spawned by name anyway - the resolution and the launch disagreeing, which is
    the class of bug `shim_paths` exists to prevent.
    """
    import inspect

    from swe_mux import shim_paths

    source = inspect.getsource(shim_paths)
    assert "well_known_locations" not in source
    assert "tool_locations" not in source


def test_well_known_locations_are_absolute_and_expanded() -> None:
    for tool in ("git", "node", "npm", "uv", "tailscale"):
        for candidate in tool_locations.well_known_locations(tool):
            assert Path(candidate).is_absolute(), candidate
            assert "%" not in candidate and "$" not in candidate, candidate


def test_refresh_is_a_no_op_and_honest_about_it_off_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """There is no out-of-band PATH on POSIX, and claiming one would be a lie.

    The daemon's PATH there is simply what it was given, so a refresh that
    reported success would leave the user pressing a button that cannot work.
    """
    monkeypatch.setattr(tool_locations, "IS_WINDOWS", False)
    before = os.environ.get("PATH", "")
    assert tool_locations.refresh_search_path() is False
    assert os.environ.get("PATH", "") == before


@pytest.mark.skipif(os.name != "nt", reason="reads the Windows environment registry")
def test_refresh_never_narrows_the_search_path() -> None:
    """A refresh may only widen. Two ways it could have broken every spawn.

    A registry this process cannot read is not evidence that PATH is empty, and
    the live PATH legitimately carries entries no registry key mentions - the mux
    shim directory is prepended at spawn. Both are kept.
    """
    marker = str(Path(__file__).resolve().parent / "mux-refresh-marker")
    original = os.environ.get("PATH", "")
    os.environ["PATH"] = original + os.pathsep + marker
    try:
        tool_locations.refresh_search_path()
        entries = os.environ["PATH"].split(os.pathsep)
        assert marker in entries
        for entry in original.split(os.pathsep):
            if entry.strip():
                assert entry.strip() in entries
    finally:
        os.environ["PATH"] = original
