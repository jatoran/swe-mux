"""Shared Windows-safe leaf validation and the daemon-side folder-name suggestion."""

from __future__ import annotations

import pytest

from swe_mux.leaf_names import suggest_folder_name, validate_leaf_name


def test_validate_leaf_name_accepts_ordinary_names() -> None:
    for name in ("scraper", "vault-spaces", "My.Project", "über-app", "a" * 255):
        validate_leaf_name(name)


@pytest.mark.parametrize(
    ("name", "match"),
    [
        ("", "must not be empty"),
        (".", "single file or folder name"),
        ("..", "single file or folder name"),
        ("trailing ", "space or dot"),
        ("trailing.", "space or dot"),
        ("with/slash", "Windows-invalid character"),
        ("with\\backslash", "Windows-invalid character"),
        ("with:colon", "Windows-invalid character"),
        ("with\x01control", "Windows-invalid character"),
        ("with\x7fdel", "Windows-invalid character"),
        ("a" * 256, "255 UTF-16 code units"),
        ("CON", "reserved by Windows"),
        ("nul", "reserved by Windows"),
        # The device stem rules: the extension does not rescue a device name,
        # and neither does trailing-space trickery. COM1-9/LPT1-9 included.
        ("COM1.txt", "reserved by Windows"),
        ("lpt9", "reserved by Windows"),
        ("aux .log", "reserved by Windows"),
    ],
)
def test_validate_leaf_name_refuses_windows_hostile_names(name: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        validate_leaf_name(name)


def test_validate_leaf_name_labels_errors_for_its_caller() -> None:
    with pytest.raises(ValueError, match="project folder name must not be empty"):
        validate_leaf_name("", label="project folder name")


def test_validate_leaf_name_refuses_caller_reserved_control_directories() -> None:
    reserved = frozenset({".git", ".swe-mux"})
    with pytest.raises(ValueError, match="control directory names are reserved"):
        validate_leaf_name(".GIT", reserved_names=reserved)
    # Without the caller's set the same name is an ordinary (valid) dotted leaf.
    validate_leaf_name(".git")


def test_suggest_folder_name_matches_the_dialog_transform() -> None:
    # The same cases `projectCreate.ts` suggestFolderName covers: invalid
    # characters become hyphens (never silently dropped), whitespace joins,
    # runs collapse, and edge dot/hyphen noise trims away.
    assert suggest_folder_name("  Vault Spaces  ") == "Vault-Spaces"
    assert suggest_folder_name("a<b>c") == "a-b-c"
    assert suggest_folder_name("what? really: yes") == "what-really-yes"
    assert suggest_folder_name("..sneaky..") == "sneaky"
    assert suggest_folder_name("- - -") == ""
    assert suggest_folder_name("tabs\tand\nnewlines") == "tabs-and-newlines"


def test_suggested_names_validate_or_are_empty() -> None:
    # The pipeline the assistant uses: whatever the suggestion produces is either
    # empty (a refusal) or passes validation - except the Windows device stems,
    # which normalization deliberately leaves for validation to refuse.
    for spoken in ("vault spaces", "a/b", "trailing dot.", "  x  "):
        suggested = suggest_folder_name(spoken)
        if suggested:
            validate_leaf_name(suggested)
    assert suggest_folder_name("CON") == "CON"  # still refused by validate_leaf_name
