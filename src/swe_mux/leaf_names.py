"""Windows-safe filesystem leaf names, shared by every surface that mints one.

Two callers create exactly-one filesystem entry from a user-supplied name: Project
file resources (`project_files.py`) and new Project folders (`projects.py`, reached
by the Add-project dialog's create mode and the assistant's create_project tool).
The rules are identical - a single path segment that Windows will accept - so they
live here once. Validation never normalizes: what the caller passes is what is
created, or the reason it cannot be is the error. `suggest_folder_name` is the
separate, deliberate normalization step (the daemon-side mirror of the frontend's
`suggestFolderName`), for surfaces like the assistant where the name arrives spoken
and no human is typing the folder leaf.
"""

from __future__ import annotations

import re

from .ui_messages import ui_message

WINDOWS_INVALID_LEAF_CHARS = frozenset('<>:"/\\|?*')
WINDOWS_RESERVED_LEAF_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
    | {f"COM{number}" for number in "¹²³"}
    | {f"LPT{number}" for number in "¹²³"}
)

_INVALID_CHARS_PATTERN = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_WHITESPACE_PATTERN = re.compile(r"\s+")
_HYPHEN_RUNS_PATTERN = re.compile(r"-{2,}")
_EDGE_TRIM_PATTERN = re.compile(r"^[-.]+|[-. ]+$")


def validate_leaf_name(
    name: str,
    *,
    label: str = "name",
    reserved_names: frozenset[str] = frozenset(),
) -> None:
    """Validate one Windows-safe leaf without normalizing what the caller passed.

    `label` prefixes every error so the message names the surface that failed
    ("project folder name", "project resource name"). `reserved_names` are
    casefolded control-directory names the caller refuses outright.
    """

    if not name:
        raise ValueError(ui_message("leaf_names.489fcc5daa", value1=label))
    if name in {".", ".."}:
        raise ValueError(ui_message("leaf_names.15188e3ea8", value1=label))
    if name.casefold() in reserved_names:
        raise ValueError(ui_message("leaf_names.01e968ad25"))
    if name.endswith((" ", ".")):
        raise ValueError(ui_message("leaf_names.8124417c3d", value1=label))
    if any(
        character in WINDOWS_INVALID_LEAF_CHARS or ord(character) < 32 or ord(character) == 127
        for character in name
    ):
        raise ValueError(ui_message("leaf_names.4ac1ec7350", value1=label))
    try:
        utf16_units = len(name.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise ValueError(ui_message("leaf_names.1fea26392d", value1=label)) from exc
    if utf16_units > 255:
        raise ValueError(ui_message("leaf_names.b1eac5c5dc", value1=label))
    device_stem = name.split(".", 1)[0].rstrip(" ").upper()
    if device_stem in WINDOWS_RESERVED_LEAF_STEMS:
        raise ValueError(ui_message("leaf_names.ea9717c00d", value1=label))


def suggest_folder_name(name: str) -> str:
    """Deterministically derive a folder leaf from a free-text name.

    The exact transform the Add-project dialog applies client-side
    (`projectCreate.ts` `suggestFolderName`), so a name produces the same folder
    whether it was typed in the dialog or spoken to the assistant: invalid
    characters become hyphens rather than being dropped (two distinct names must
    not silently collapse into one folder), whitespace joins with hyphens, runs
    collapse, and leading/trailing dot-hyphen noise is trimmed.
    """

    cleaned = _INVALID_CHARS_PATTERN.sub("-", name.strip())
    cleaned = _WHITESPACE_PATTERN.sub("-", cleaned)
    cleaned = _HYPHEN_RUNS_PATTERN.sub("-", cleaned)
    return _EDGE_TRIM_PATTERN.sub("", cleaned)
