"""The `project` argument shared by the agent-facing read and write surfaces.

What these pin: the default is the caller's own Project; `"fleet"` and a Project
name or id are the only ways past it; an unregistered caller falls back to its
git Project identity and never widens by accident; and a refusal names the
Projects that do exist, because a caller that guessed wrong needs to be able to
guess again.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from swe_mux.project_scope import (
    AmbiguousProject,
    UnknownProject,
    resolve_project_scope,
    split_qualified_target,
)


def caller(project_id: str = "p1", scope_id: str = "scope-1") -> SimpleNamespace:
    return SimpleNamespace(project_id=project_id, project_scope_id=scope_id)


def projects(*pairs: tuple[str, str]) -> SimpleNamespace:
    return SimpleNamespace(
        projects={
            pid: SimpleNamespace(id=pid, name=name, root=f"D:/{pid}")
            for pid, name in pairs
        }
    )


REGISTRY = projects(("p1", "Horizon of Steel"), ("p2", "Pixel Lab"))


@pytest.mark.parametrize("raw", [None, "", "  ", "self"])
def test_the_default_is_the_callers_own_project(raw: str | None) -> None:
    scope = resolve_project_scope(raw, caller(), REGISTRY)
    assert scope.project_id == "p1"
    assert scope.fleet is False
    assert scope.widened is False
    assert scope.admits("p1", "scope-1")
    assert not scope.admits("p2", "scope-2")


def test_fleet_admits_everything_and_says_it_widened() -> None:
    scope = resolve_project_scope("fleet", caller(), REGISTRY)
    assert scope.fleet is True
    assert scope.widened is True
    assert scope.admits("p2", "scope-2")
    assert scope.admits("", "")


@pytest.mark.parametrize("raw", ["p2", "Pixel Lab", "pixel lab", "PIXEL LAB"])
def test_a_project_resolves_by_id_or_by_name_in_any_case(raw: str) -> None:
    scope = resolve_project_scope(raw, caller(), REGISTRY)
    assert scope.project_id == "p2"
    assert scope.requested == "Pixel Lab"
    assert scope.admits("p2", "")
    # A named Project is exactly that one - it does not fall back to the git
    # identity the way an unregistered caller's own scope does.
    assert not scope.admits("", "scope-2")


def test_an_unregistered_caller_matches_on_its_git_identity_and_never_widens() -> None:
    scope = resolve_project_scope(None, caller(project_id=""), REGISTRY)
    assert scope.project_id == ""
    assert scope.admits("", "scope-1")
    assert not scope.admits("", "scope-2")
    assert not scope.admits("p2", "")


def test_an_unknown_project_lists_the_ones_that_exist() -> None:
    with pytest.raises(UnknownProject) as caught:
        resolve_project_scope("Pixel Labs", caller(), REGISTRY)
    assert "Horizon of Steel" in str(caught.value)
    assert "Pixel Lab" in str(caught.value)
    assert '"fleet"' in str(caught.value)


def test_two_projects_of_one_name_answer_with_their_ids() -> None:
    duplicated = projects(("p1", "Work"), ("p2", "Work"))
    with pytest.raises(AmbiguousProject) as caught:
        resolve_project_scope("Work", caller(), duplicated)
    assert "p1, p2" in str(caught.value)


def test_a_project_name_needs_a_registry_but_fleet_does_not() -> None:
    assert resolve_project_scope("fleet", caller(), None).fleet is True
    with pytest.raises(UnknownProject):
        resolve_project_scope("Pixel Lab", caller(), None)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("claude-s2", ("", "claude-s2")),
        ("Pixel Lab/claude-s2", ("Pixel Lab", "claude-s2")),
        ("Pixel Lab\\claude-s2", ("Pixel Lab", "claude-s2")),
        # The last separator wins, so a session name may contain one.
        ("Pixel Lab/team/backend", ("Pixel Lab/team", "backend")),
        ("/backend", ("", "/backend")),
        ("Pixel Lab/", ("", "Pixel Lab/")),
    ],
)
def test_a_qualified_target_splits_on_its_last_separator(
    text: str, expected: tuple[str, str]
) -> None:
    assert split_qualified_target(text) == expected
