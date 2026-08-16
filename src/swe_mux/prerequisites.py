"""Detect the external tools swe-mux features assume, for an onboarding checklist.

Each of these fails gracefully when absent, but the absence is invisible, so a
disabled capability reads as broken rather than unconfigured. This surfaces the
presence of Git, Node, npm, and Tailscale with what each one backs and the next
step to install it. Detection is platform-neutral: the download links and install
commands are Windows-oriented because that is the first release target, but the
presence check itself works anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

from .shim_paths import which_real


@dataclass(frozen=True, slots=True)
class Prerequisite:
    id: str
    label: str
    purpose: str
    download_url: str
    install_command: str


# `npm` ships with Node, so it has no separate installer; the others each name a
# winget package and a download page for a fresh Windows machine.
_PREREQUISITES: tuple[Prerequisite, ...] = (
    Prerequisite(
        "git",
        "Git",
        "Backs worktrees, Git status, and diff review.",
        "https://git-scm.com/download/win",
        "winget install Git.Git",
    ),
    Prerequisite(
        "node",
        "Node.js",
        "Backs ccusage token accounting and npm-installed agent CLIs.",
        "https://nodejs.org/en/download",
        "winget install OpenJS.NodeJS.LTS",
    ),
    Prerequisite(
        "npm",
        "npm",
        "Installs agent CLIs and ccusage; ships with Node.js.",
        "https://nodejs.org/en/download",
        "Comes with Node.js",
    ),
    Prerequisite(
        "tailscale",
        "Tailscale",
        "Backs remote access and mobile use.",
        "https://tailscale.com/download/windows",
        "winget install tailscale.tailscale",
    ),
)


def detect_prerequisites() -> list[dict[str, object]]:
    """Report the presence of each prerequisite tool with its next step.

    Resolution goes through ``which_real`` (the canonical guard) rather than a bare
    ``shutil.which``; these tools are not mux shims, so it behaves like a plain
    lookup here, but it keeps every executable-presence check consistent.
    """
    result: list[dict[str, object]] = []
    for prerequisite in _PREREQUISITES:
        path = which_real(prerequisite.id)
        result.append(
            {
                "id": prerequisite.id,
                "label": prerequisite.label,
                "purpose": prerequisite.purpose,
                "present": bool(path),
                "path": path,
                "download_url": prerequisite.download_url,
                "install_command": prerequisite.install_command,
            }
        )
    return result
