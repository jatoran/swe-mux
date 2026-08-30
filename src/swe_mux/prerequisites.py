"""Detect the external tools swe-mux features assume, for an onboarding checklist.

Each of these fails gracefully when absent, but the absence is invisible, so a
disabled capability reads as broken rather than unconfigured. This surfaces the
presence of Git, Node, npm, uv, and Tailscale with what each one backs and the
next step to get it.

Two things here used to be wrong in the same direction, and both were found the
first time this ran on a machine that was not the development host (2026-08-30).

**Presence was equated with PATH.** Git was installed, Tailscale was installed and
connected to a tailnet, and both were reported absent, because their installers do
not add a directory to PATH. `tool_locations.locate_tool` now separates "installed
but not invocable by name" from "not installed": they need different sentences and
different actions, and telling someone to install software they already have is
the worst version of getting that wrong.

**Remediation was Windows-only while the check claimed to be platform-neutral.**
The old docstring said as much and treated it as acceptable for a Windows-first
release; it stopped being acceptable once the daemon ran on Linux and macOS, where
a missing Git produced `winget install Git.Git` and a link to a `/download/win`
page. Every step is now per-platform.

`uv` is on the list as of the same date. Managed integrations shell out to it
(`edge_tts_provider`), so it is a real dependency of a real feature; it was absent
here only because swe-mux's own most common install method happens to be a uv tool,
which made it look like it was always present.
"""

from __future__ import annotations

from dataclasses import dataclass

from .host_platform import IS_MACOS, IS_WINDOWS
from .tool_locations import ToolLocation, locate_tool


@dataclass(frozen=True, slots=True)
class InstallStep:
    """How to get one tool on one platform."""

    command: str
    download_url: str


@dataclass(frozen=True, slots=True)
class Prerequisite:
    id: str
    label: str
    purpose: str
    #: Keyed by `host_platform` family: "windows", "macos", "linux". Every
    #: prerequisite carries all three, because the alternative is the fallback
    #: that shipped Windows instructions to Linux users.
    steps: dict[str, InstallStep]


def _platform_key() -> str:
    if IS_WINDOWS:
        return "windows"
    if IS_MACOS:
        return "macos"
    return "linux"


# `npm` ships with Node, so it has no separate installer anywhere; the others each
# name their platform's usual package source and a download page.
_PREREQUISITES: tuple[Prerequisite, ...] = (
    Prerequisite(
        "git",
        "Git",
        "Backs worktrees, Git status, and diff review.",
        {
            "windows": InstallStep("winget install Git.Git", "https://git-scm.com/download/win"),
            "macos": InstallStep("brew install git", "https://git-scm.com/download/mac"),
            "linux": InstallStep(
                "sudo apt install git  (or dnf/pacman/zypper install git)",
                "https://git-scm.com/download/linux",
            ),
        },
    ),
    Prerequisite(
        "node",
        "Node.js",
        "Backs ccusage token accounting and npm-installed agent CLIs.",
        {
            "windows": InstallStep(
                "winget install OpenJS.NodeJS.LTS", "https://nodejs.org/en/download"
            ),
            "macos": InstallStep("brew install node", "https://nodejs.org/en/download"),
            "linux": InstallStep(
                "sudo apt install nodejs npm  (or use nvm)", "https://nodejs.org/en/download"
            ),
        },
    ),
    Prerequisite(
        "npm",
        "npm",
        "Installs agent CLIs and ccusage; ships with Node.js.",
        {
            "windows": InstallStep("Comes with Node.js", "https://nodejs.org/en/download"),
            "macos": InstallStep("Comes with Node.js", "https://nodejs.org/en/download"),
            "linux": InstallStep("Comes with Node.js", "https://nodejs.org/en/download"),
        },
    ),
    Prerequisite(
        "uv",
        "uv",
        "Builds the isolated environments managed integrations install into, such as Edge TTS.",
        {
            "windows": InstallStep(
                "winget install astral-sh.uv", "https://docs.astral.sh/uv/getting-started/"
            ),
            "macos": InstallStep(
                "brew install uv", "https://docs.astral.sh/uv/getting-started/"
            ),
            "linux": InstallStep(
                "curl -LsSf https://astral.sh/uv/install.sh | sh",
                "https://docs.astral.sh/uv/getting-started/",
            ),
        },
    ),
    Prerequisite(
        "tailscale",
        "Tailscale",
        "Backs remote access and mobile use.",
        {
            "windows": InstallStep(
                "winget install tailscale.tailscale", "https://tailscale.com/download/windows"
            ),
            "macos": InstallStep("brew install tailscale", "https://tailscale.com/download/mac"),
            "linux": InstallStep(
                "curl -fsSL https://tailscale.com/install.sh | sh",
                "https://tailscale.com/download/linux",
            ),
        },
    ),
)

PREREQUISITE_IDS: tuple[str, ...] = tuple(item.id for item in _PREREQUISITES)


def _path_remedy(tool: str, path: str) -> str:
    directory = path.rsplit("\\", 1)[0] if IS_WINDOWS else path.rsplit("/", 1)[0]
    if IS_WINDOWS:
        return (
            f"{tool} is installed at {path} but its folder is not on PATH. Add "
            f'"{directory}" to PATH, then use Re-scan above.'
        )
    shell_file = "~/.zshrc" if IS_MACOS else "~/.bashrc"
    return (
        f"{tool} is installed at {path} but its directory is not on PATH. Add "
        f'`export PATH="{directory}:$PATH"` to {shell_file}, then use Re-scan above.'
    )


def describe(prerequisite: Prerequisite, location: ToolLocation) -> dict[str, object]:
    """One checklist row: what it is, whether it is here, and the next step.

    ``state`` is the three-valued answer the UI branches on, and it exists because
    the two-valued one gave wrong advice. ``present`` is kept alongside it, true
    for both found states, so every existing consumer keeps working and none of
    them silently starts treating an off-PATH tool as missing.
    """
    step = prerequisite.steps[_platform_key()]
    off_path = location.source == "off_path"
    return {
        "id": prerequisite.id,
        "label": prerequisite.label,
        "purpose": prerequisite.purpose,
        "present": location.present,
        "state": (
            "off_path"
            if off_path
            else "present"
            if location.present
            else "missing"
        ),
        "source": location.source,
        "path": location.path,
        "download_url": step.download_url,
        "install_command": step.command,
        # Only ever set for the middle state, because it is the only one whose
        # remedy is not the install command.
        "path_remedy": (
            _path_remedy(prerequisite.label, location.path or "") if off_path else None
        ),
    }


def detect_prerequisites(overrides: dict[str, str] | None = None) -> list[dict[str, object]]:
    """Report the presence of each prerequisite tool with its next step.

    ``overrides`` maps a prerequisite id to an absolute path the user supplied; it
    wins over every search. Resolution otherwise goes through `locate_tool`, which
    wraps the same `which_real` guard the launcher uses and then widens past PATH -
    see that module for why widening belongs here and must not happen at a spawn.
    """
    supplied = overrides or {}
    return [
        describe(prerequisite, locate_tool(prerequisite.id, override=supplied.get(prerequisite.id)))
        for prerequisite in _PREREQUISITES
    ]


__all__ = [
    "PREREQUISITE_IDS",
    "InstallStep",
    "Prerequisite",
    "describe",
    "detect_prerequisites",
]
