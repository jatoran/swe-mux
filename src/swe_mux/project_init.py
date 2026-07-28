"""User-authored commands offered when a Project is registered.

These are deliberately *not* Project Actions. A Project Action is imported from the
repository, so it can only run after the user approves an exact content fingerprint;
an init script is typed by the user into Settings, lives in the machine-local daemon
config, and never travels with a checkout. Authoring it *is* the authorization, so
there is no trust file, no fingerprint, and nothing here reads the Project folder.

Execution reuses the Project Action spawn contract: each selected script becomes one
ordinary one-shot shell terminal owned by the new Project, rooted at its canonical
folder. Nothing runs headlessly and nothing is hidden from the user.
"""

from __future__ import annotations

from typing import Any

from .config import Config
from .project_actions import ActionStep


def init_script_catalog(config: Config) -> list[dict[str, Any]]:
    """The init scripts a client should offer, in configured order."""
    return [
        {
            "id": str(script["id"]),
            "label": str(script["label"]),
            "command": str(script["command"]),
            "default_enabled": bool(script.get("default_enabled", False)),
        }
        for script in config.project_init_scripts
        if isinstance(script, dict) and script.get("id")
    ]


def select_init_scripts(
    config: Config, script_ids: list[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve requested ids to scripts in *configured* order, plus unknown ids.

    Configured order wins over request order: the Settings list is where the user
    expresses "git first, then the rest", and a checkbox list has no order of its own.
    """
    wanted = list(dict.fromkeys(script_ids))
    catalog = {script["id"]: script for script in init_script_catalog(config)}
    unknown = [item for item in wanted if item not in catalog]
    chosen = [script for script in init_script_catalog(config) if script["id"] in set(wanted)]
    return chosen, unknown


def init_script_step(script: dict[str, Any], *, root: str) -> ActionStep:
    """Build the one shell step that runs an init script at the Project root.

    The command string is passed through untouched (no args array), so the user's own
    shell syntax — `&&`, `;`, pipelines — works exactly as typed.
    """
    return ActionStep(
        name=str(script["label"]),
        kind="shell",
        command=str(script["command"]),
        cwd=root,
    )
