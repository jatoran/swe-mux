from collections.abc import Callable
from pathlib import Path
from typing import assert_never

from ..bundle_swap import hook_delivery_executable
from ..harness import HARNESSES, HarnessDescriptor
from .base import BackendAdapter, SpawnOptions, SpawnSpec
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .omp import OmpAdapter
from .opencode import OpenCodeAdapter
from .pi import PiAdapter
from .shell import ShellAdapter

# Derived, not listed: a family installs hooks exactly when a harness in it declares
# `native_hooks`. The literal set had to be edited in step with the registry and had
# no guard that it was.
HOOK_INSTALLER_FAMILIES = frozenset(
    harness.adapter_family for harness in HARNESSES.values() if harness.native_hooks
)


def build_agent_adapter(
    harness: HarnessDescriptor,
    *,
    executable: str,
    args: list[str],
    data_dir: Path,
    mcp_url: str,
    command_resolver: Callable[[str], tuple[str, tuple[str, ...]]] | None = None,
    instrument: bool = True,
    approval_hook_timeout: float = 5.0,
    skill: bool = False,
) -> BackendAdapter:
    """Construct an adapter from one descriptor and runtime configuration.

    ``mcp_url`` empty disables the mux MCP registration for this harness (the MCP
    toggle). ``instrument`` false launches it without mux's lifecycle hooks (the
    "launch clean" toggle), which drops the harness to unobserved.
    ``approval_hook_timeout`` bounds how long the CLI waits for mux to answer a
    permission request; it only reaches a harness that declares
    ``hook_approval_decisions``. ``skill`` turns on automatic delivery of the
    swe-mux agent skill (`config.harness_skill_enabled`; off by default): a
    data-dir plugin on the spawn argv for the Claude family, a spawn-time write
    of the shared project skill root for every other family.
    """
    if harness.adapter_family == "claude":
        return ClaudeAdapter(
            executable,
            data_dir,
            args,
            mcp_url=mcp_url,
            name=harness.name,
            config_dir_name=harness.config_dir_name,
            script_base_name=harness.script_base_name,
            data_home_resolver=harness.data_home,
            instrument=instrument,
            approval_hook_timeout=approval_hook_timeout,
            skill=skill,
        )
    if harness.adapter_family == "codex":
        return CodexAdapter(
            executable,
            notify=instrument,
            default_args=args,
            command_resolver=command_resolver,
            mcp_url=mcp_url,
            name=harness.name,
            data_home_resolver=harness.data_home,
            rollout_file_prefix=harness.rollout_file_prefix or "rollout-",
            skill=skill,
            hook_executable=hook_delivery_executable(data_dir),
        )
    if harness.adapter_family == "omp":
        return OmpAdapter(
            executable,
            args,
            data_home=harness.data_home(),
            data_dir=data_dir,
            mcp_url=mcp_url,
            instrument=instrument,
            skill=skill,
        )
    if harness.adapter_family == "pi":
        # No `mcp_url`: pi 0.74.2 has no MCP client at all (its bundled docs
        # carry no MCP page and its dist references none), so there is nothing
        # to register the mux server with. Extension-registered native tools are
        # the route if that surface is ever wanted.
        return PiAdapter(
            executable,
            args,
            data_home=harness.data_home(),
            command_resolver=command_resolver,
            instrument=instrument,
            skill=skill,
        )
    if harness.adapter_family == "opencode":
        return OpenCodeAdapter(
            executable,
            args,
            data_home=harness.data_home(),
            data_dir=data_dir,
            mcp_url=mcp_url,
            command_resolver=command_resolver,
            instrument=instrument,
            skill=skill,
        )
    assert_never(harness.adapter_family)

__all__ = [
    "BackendAdapter",
    "SpawnOptions",
    "SpawnSpec",
    "ClaudeAdapter",
    "CodexAdapter",
    "OmpAdapter",
    "OpenCodeAdapter",
    "PiAdapter",
    "ShellAdapter",
    "build_agent_adapter",
    "HOOK_INSTALLER_FAMILIES",
]
