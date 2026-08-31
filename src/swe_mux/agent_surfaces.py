"""Which fleet surfaces a harness's sessions hold, and whether the set coheres.

There are two *capability* surfaces an agent session can reach the daemon
through - the mux MCP tools (self-advertising: their schemas arrive in the
tool list) and the `swemux agent` CLI mode (mute: nothing tells an agent it
exists) - plus one *instruction* layer, the shipped skill, which is how a
session learns about whichever capability is present. The three are governed
by three per-harness config maps (`harness_mcp_enabled`, `harness_cli_enabled`,
`harness_skill_enabled`), and this module is the one place that turns those
maps into answers, so the spawn env, the MCP endpoint's surface gate, and the
doctor report cannot disagree about what a harness has.

Two consequences are enforced elsewhere from what this module computes:

- **`MUX_SURFACES` is stamped into every agent pane's environment** at spawn
  (`session.py`), so a skill written for any harness can read what this session
  actually holds instead of guessing - the skill is one file shared by five
  CLIs, and the capability set differs per harness and per install.
- **"Neither" is a real, enforced state** (ROADMAP Phase 23 W4): a harness with
  both capability maps off has sessions whose MCP token authenticates nothing,
  refused at the endpoint rather than in any one client. The refusal lives in
  `mcp.McpService.resolve_caller` via `surface_gate`; this module only answers
  the question.

The coherence warnings are advisory, not validation errors, because every
combination is *legal* - an operator mid-reconfiguration passes through the
incoherent ones - but two of them are traps worth naming: a skill delivered to
a harness with no capability teaches commands that will fail, and a CLI
capability with no skill (and no MCP) is a surface nothing ever tells the
agent about.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .harness import HARNESSES

#: The environment variable stamped into agent panes: a comma-joined subset of
#: {"mcp", "cli"} in this order, or the empty string for "neither".
SURFACES_ENV_VAR = "MUX_SURFACES"

SURFACE_MCP = "mcp"
SURFACE_CLI = "cli"


def harness_surfaces(config: Any, backend: str) -> tuple[str, ...]:
    """The capability surfaces sessions of ``backend`` hold, in canonical order.

    Absent map keys default the way each map documents: MCP and CLI on, which
    is why an empty config yields ``("mcp", "cli")`` for every agent harness.
    A non-agent backend (a shell) holds no fleet surfaces by definition.
    """
    if backend not in HARNESSES:
        return ()
    surfaces: list[str] = []
    if bool(getattr(config, "harness_mcp_enabled", {}).get(backend, True)):
        surfaces.append(SURFACE_MCP)
    if bool(getattr(config, "harness_cli_enabled", {}).get(backend, True)):
        surfaces.append(SURFACE_CLI)
    return tuple(surfaces)


def surfaces_env_value(config: Any, backend: str) -> str:
    """What `MUX_SURFACES` says in a pane of ``backend``. Empty means neither."""
    return ",".join(harness_surfaces(config, backend))


def surface_gate(config: Any) -> Any:
    """A ``backend -> bool`` callable: does any capability surface exist?

    Injected into `McpService` so the endpoint refuses tokens from sessions of
    a harness whose surfaces are all off, without the service importing config
    semantics. Non-agent backends answer True - a shell session's token was
    never surface-gated, and refusing it here would change an unrelated
    contract.
    """

    def gate(backend: str) -> bool:
        if backend not in HARNESSES:
            return True
        return bool(harness_surfaces(config, backend))

    return gate


@dataclass(frozen=True, slots=True)
class SurfaceWarning:
    """One advisory incoherence, per harness, with the reason spelled out."""

    backend: str
    code: str
    message: str


def coherence_warnings(config: Any) -> list[SurfaceWarning]:
    """The incoherent surface/skill combinations worth telling an operator about.

    - ``skill_without_capability``: the skill teaches a surface that does not
      exist for this harness, which is worse than no skill - the agent is
      handed instructions that fail.
    - ``cli_without_instruction``: the CLI capability is on but neither the
      skill nor MCP is - MCP tools advertise themselves, the CLI cannot, so
      nothing ever tells this harness's sessions the commands exist.
    """
    warnings: list[SurfaceWarning] = []
    skill_map = getattr(config, "harness_skill_enabled", {}) or {}
    for backend in HARNESSES:
        surfaces = harness_surfaces(config, backend)
        skill = bool(skill_map.get(backend, False))
        if skill and not surfaces:
            warnings.append(
                SurfaceWarning(
                    backend=backend,
                    code="skill_without_capability",
                    message=(
                        f"{backend}: the skill is delivered but both fleet "
                        "surfaces (MCP, CLI) are off, so it teaches commands "
                        "that will fail. Turn a surface on or stop delivering "
                        "the skill."
                    ),
                )
            )
        elif SURFACE_CLI in surfaces and SURFACE_MCP not in surfaces and not skill:
            warnings.append(
                SurfaceWarning(
                    backend=backend,
                    code="cli_without_instruction",
                    message=(
                        f"{backend}: the CLI surface is on but MCP and the "
                        "skill are both off. The CLI does not advertise "
                        "itself, so nothing tells this harness's sessions the "
                        "commands exist - deliver the skill to make the "
                        "capability discoverable."
                    ),
                )
            )
    return warnings
