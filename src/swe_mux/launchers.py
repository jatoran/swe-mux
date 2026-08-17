from __future__ import annotations

import json
import os
import re
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

from .config import Config
from .harness import agent_harnesses
from .host_platform import IS_WINDOWS
from .shim_paths import SHIM_MARKER, path_without_shim_dirs, which_real


def resolve_command(command: str) -> str:
    """Resolve configured commands, including npm-style PATHEXT shims.

    Older configs commonly contain ``codex.exe`` even though npm installs
    ``codex.cmd``. If the explicit value is not found, retry its basename so
    Windows PATHEXT can select the installed command. Resolution never returns
    one of our own ``~/.mux/bin`` agent shims: a daemon whose PATH inherited a
    session's shim directory would otherwise wire ``MUX_*_EXE`` back at the
    shim and every launch would recurse through itself.
    """
    resolved = which_real(command)
    if resolved:
        return resolved
    path = Path(command)
    if path.suffix.casefold() == ".exe" and path.parent == Path("."):
        resolved = which_real(path.stem)
        if resolved:
            return resolved
    return command


def _npm_shim_targets(shim: Path) -> list[str]:
    """Quoted `%dp0%`-relative paths named by an npm-generated `.cmd` shim.

    npm writes one executable line naming its target, in one of two shapes:

        "%_prog%"  "%dp0%\\node_modules\\<pkg>\\dist\\cli.js" %*     (node package)
        "%dp0%\\node_modules\\<pkg>\\bin\\<name>.exe"   %*           (binary package)

    Returns every quoted `%dp0%` path found, newest line first, with `%dp0%`
    expanded against the shim's own directory. `%dp0%` carries a trailing
    separator from `%~dp0`, so the shim's own `\\` produces a doubled separator
    that `Path` normalizes away.
    """
    try:
        text = shim.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    targets: list[str] = []
    for line in reversed(text.splitlines()):
        for token in re.findall(r'"([^"]*%dp0%[^"]*)"', line):
            expanded = token.replace("%dp0%", str(shim.parent) + os.sep)
            targets.append(str(Path(expanded)))
    return targets


def resolve_npm_shim_pty_command(
    command: str, *, windows: bool | None = None
) -> tuple[str, tuple[str, ...]]:
    """Resolve an npm `.cmd` shim to a ConPTY-executable command plus argv prefix.

    ConPTY cannot execute a batch shim: spawning one raises "The system cannot
    find the file specified", which is what a pi or opencode pane did before
    this existed - the pane opened, failed to spawn, and closed.

    Routing through ``cmd.exe`` would work but is declined: it puts a quoting
    layer between mux and an argv it builds exactly (pi's ``--extension <path>``
    and opencode's ``--session <id>``), which is the same corruption
    :func:`resolve_codex_pty_command` exists to avoid.

    Reads the shim instead and launches what it would have launched - the
    package's own `.exe`, or Node against its JS entrypoint. Anything
    unrecognized falls through unchanged rather than guessing.
    """
    resolved = resolve_command(command)
    if windows is None:
        windows = os.name == "nt"
    path = Path(resolved)
    if not windows or path.suffix.casefold() not in {".cmd", ".bat"}:
        return resolved, ()
    for target in _npm_shim_targets(path):
        suffix = Path(target).suffix.casefold()
        if suffix == ".exe" and Path(target).is_file():
            return target, ()
        if suffix in {".js", ".mjs", ".cjs"} and Path(target).is_file():
            bundled_node = path.parent / "node.exe"
            node = str(bundled_node) if bundled_node.is_file() else shutil.which("node")
            if node:
                return node, (target,)
    return resolved, ()


def resolve_codex_pty_command(
    command: str, *, windows: bool | None = None
) -> tuple[str, tuple[str, ...]]:
    """Resolve Codex to a ConPTY-safe executable plus immutable argv prefix.

    npm exposes Codex as a batch shim on Windows. ConPTY cannot execute that shim
    directly, and routing JSON-valued notify configuration through ``cmd.exe``
    corrupts its quoting. Launch the npm package's JS entrypoint with Node instead.
    """
    resolved = resolve_command(command)
    if windows is None:
        windows = os.name == "nt"
    path = Path(resolved)
    if not windows or path.suffix.casefold() not in {".cmd", ".bat"}:
        return resolved, ()
    codex_js = path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    if codex_js.is_file():
        bundled_node = path.parent / "node.exe"
        node = str(bundled_node) if bundled_node.is_file() else shutil.which("node")
        if node:
            return node, (str(codex_js),)
    return resolved, ()


def _write_shim(bin_dir: Path, backend: str) -> Path:
    """Write one agent shim in this host's executable-script format.

    Both forms do the same thing - hand the launch to `swe_mux.agent_launcher`
    with the harness name and the caller's arguments - and both must forward
    arguments *without* reinterpreting them, because the argv on the other side
    includes Codex's JSON-valued `-c notify=...` and pi's `--extension <path>`.
    `%*` and `"$@"` are the respective spellings that do not re-split.

    The POSIX shim is `exec`ed rather than run as a child so the CLI inherits the
    shim's pid: a wrapper process between the pseudoterminal's root and the real
    agent would make the root exit before the agent does, and every process-tree
    walk would then be rooted at a dead pid.
    """
    if IS_WINDOWS:
        path = bin_dir / f"{backend}.cmd"
        path.write_text(
            f'@echo off\r\n"{sys.executable}" -m swe_mux.agent_launcher {backend} %*\r\n',
            encoding="utf-8",
        )
        return path
    path = bin_dir / backend
    path.write_text(
        "#!/bin/sh\n"
        f"# {SHIM_MARKER}\n"
        f'exec "{sys.executable}" -m swe_mux.agent_launcher {backend} "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    # Executable for the owner only. The shim names an interpreter path and a
    # module, so it is not a secret, but the data dir is the user's and there is
    # no reason for anyone else on the host to run it.
    path.chmod(0o755)
    return path


def create_agent_shims(
    config: Config,
    settings_path: Path | None = None,
    mcp_config_path: Path | None = None,
    *,
    harness_settings: Mapping[str, tuple[Path | None, Path | None]] | None = None,
) -> dict[str, str]:
    """Generate the per-harness shims and the environment a shim-launched CLI reads.

    `harness_settings` maps a harness name to the settings file and MCP config its
    adapter materialized, published as `MUX_<NAME>_SETTINGS` and
    `MUX_<NAME>_MCP_CONFIG` - the same per-name variables `agent_launcher` reads for
    every other adapter fact. The positional pair is the older Claude-only form,
    retained for callers that only have those two paths; passing both forms lets the
    mapping win.

    Keyed by name rather than written for one harness because the variables always
    were per-name: a second harness in the Claude adapter family would read
    `MUX_<ITS_NAME>_SETTINGS`, which the Claude-only form never wrote, so its
    shim-launched sessions would silently start with no hook settings at all.
    """
    bin_dir = config.data_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for backend in agent_harnesses():
        _write_shim(bin_dir, backend)
    result = {
        # Strip inherited shim directories (ours or a stale data dir's) before
        # prepending, so sessions see exactly one shim dir at the front.
        "PATH": f"{bin_dir}{os.pathsep}{path_without_shim_dirs()}",
        "MUX_SHIM_DIR": str(bin_dir),
        # Roots for the per-session artifacts a shim-launched harness has to
        # materialize itself, because a shell promotion has no adapter to do it.
        # Missing `MUX_OPENCODE_CONFIG_ROOT` meant `agent_launcher._opencode`
        # read an unset variable and silently skipped the whole config: an
        # opencode started by typing `opencode` in a shell pane got no plugin, so
        # no hooks, no state, and no MCP — while the same harness launched from
        # the Run menu worked, which is the hardest shape of bug to notice.
        "MUX_OMP_EXTENSION_ROOT": str(config.data_dir / "omp-extensions"),
        "MUX_OPENCODE_CONFIG_ROOT": str(config.data_dir / "opencode-configs"),
    }
    for backend in agent_harnesses():
        prefix = f"MUX_{backend.upper().replace('-', '_')}"
        result[f"{prefix}_EXE"] = resolve_command(config.harness_exe[backend])
        result[f"{prefix}_ARGS"] = json.dumps(config.harness_args[backend])
    declared: dict[str, tuple[Path | None, Path | None]] = {}
    if settings_path or mcp_config_path:
        declared[agent_harnesses()[0]] = (settings_path, mcp_config_path)
    declared.update(harness_settings or {})
    for backend, (settings, mcp_config) in declared.items():
        if backend not in config.harness_exe:
            continue
        prefix = f"MUX_{backend.upper().replace('-', '_')}"
        if settings:
            result[f"{prefix}_SETTINGS"] = str(settings)
        if mcp_config:
            result[f"{prefix}_MCP_CONFIG"] = str(mcp_config)
    return result
