from collections.abc import Callable
from pathlib import Path
from typing import assert_never

from ..harness import HarnessDescriptor
from .base import BackendAdapter, SpawnOptions, SpawnSpec
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .omp import OmpAdapter
from .opencode import OpenCodeAdapter
from .pi import PiAdapter
from .shell import ShellAdapter

HOOK_INSTALLER_FAMILIES = frozenset({"claude", "codex", "omp", "pi", "opencode"})


def build_agent_adapter(
    harness: HarnessDescriptor,
    *,
    executable: str,
    args: list[str],
    data_dir: Path,
    mcp_url: str,
    command_resolver: Callable[[str], tuple[str, tuple[str, ...]]] | None = None,
) -> BackendAdapter:
    """Construct an adapter from one descriptor and runtime configuration."""
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
        )
    if harness.adapter_family == "codex":
        return CodexAdapter(
            executable,
            notify=True,
            default_args=args,
            command_resolver=command_resolver,
            mcp_url=mcp_url,
            name=harness.name,
            data_home_resolver=harness.data_home,
            rollout_file_prefix=harness.rollout_file_prefix or "rollout-",
        )
    if harness.adapter_family == "omp":
        return OmpAdapter(
            executable,
            args,
            data_home=harness.data_home(),
            data_dir=data_dir,
            mcp_url=mcp_url,
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
        )
    if harness.adapter_family == "opencode":
        # No `mcp_url` yet either: opencode does have MCP, but registering it
        # belongs with the observation work rather than the launch path.
        return OpenCodeAdapter(
            executable,
            args,
            data_home=harness.data_home(),
            data_dir=data_dir,
            command_resolver=command_resolver,
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
