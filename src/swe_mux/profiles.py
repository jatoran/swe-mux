from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Config, LaunchProfile
from .harness import is_agent_harness, reserved_launch_arg_conflict
from .subprocess_flags import background_creation_flags

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    profile_id: str
    executable: str
    argv: tuple[str, ...]
    env: dict[str, str]
    capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolvedAgentProfile:
    """A launch profile applied to an agent harness rather than to a shell.

    Deliberately not a :class:`ResolvedProfile`. A shell profile resolves to the
    complete command line, wrapper and all; an agent profile contributes only the
    middle of one, because the adapter owns the conversation id, the settings file,
    and the MCP registration on either side of it. `executable` is ``None`` when the
    profile inherits `harness_exe`, which is the ordinary case: a profile usually
    exists to add arguments, not to name a different binary.
    """

    profile_id: str
    backend: str
    executable: str | None
    argv: tuple[str, ...]
    env: dict[str, str]


def _available(command: str) -> str | None:
    return shutil.which(command)


def _wsl_distros() -> list[str]:
    executable = _available("wsl.exe")
    if not executable:
        return []
    try:
        result = subprocess.run(
            [executable, "--list", "--quiet"],
            check=False,
            capture_output=True,
            timeout=3,
            creationflags=background_creation_flags(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    raw = result.stdout
    text = raw.decode("utf-16-le", "replace") if b"\x00" in raw else raw.decode(errors="replace")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().casefold().startswith("docker-")
    ]


def detected_profiles() -> list[LaunchProfile]:
    profiles: list[LaunchProfile] = []
    if executable := _available("powershell.exe"):
        profiles.append(
            LaunchProfile(
                "powershell",
                "Windows PowerShell",
                executable,
                ["-NoLogo"],
                marker="ps",
                capabilities=["interactive", "agent-aware"],
            )
        )
    if executable := _available("pwsh.exe"):
        profiles.append(
            LaunchProfile(
                "pwsh",
                "PowerShell 7",
                executable,
                ["-NoLogo"],
                marker="ps7",
                capabilities=["interactive", "agent-aware"],
            )
        )
    if executable := _available("cmd.exe"):
        profiles.append(
            LaunchProfile(
                "cmd",
                "Command Prompt",
                executable,
                ["/Q"],
                marker="cmd",
                capabilities=["interactive", "agent-aware"],
            )
        )
    wsl = _available("wsl.exe")
    if wsl:
        for distro in _wsl_distros():
            profile_id = "wsl-" + re.sub(r"[^a-z0-9]+", "-", distro.casefold()).strip("-")
            profiles.append(
                LaunchProfile(
                    profile_id,
                    f"WSL: {distro}",
                    wsl,
                    ["--distribution", distro],
                    cwd_strategy="wsl",
                    marker="wsl",
                    capabilities=["interactive", "wsl", "agent-bridge-unavailable"],
                )
            )
    return profiles


def profile_payload(config: Config) -> dict[str, object]:
    configured = [asdict(profile) for profile in config.shell_profiles]
    configured_ids = {profile.id for profile in config.shell_profiles}
    detected = [
        {**asdict(profile), "configured": profile.id in configured_ids}
        for profile in detected_profiles()
    ]
    return {
        "default_profile_id": config.default_shell_profile,
        "profiles": configured,
        "detected": detected,
    }


def find_profile(config: Config, profile_id: str) -> LaunchProfile | None:
    """The configured profile with this id, falling back to a detected shell.

    One lookup for both resolvers, so a detected profile that was never written into
    the configuration behaves identically wherever it is named.
    """
    profile = next((item for item in config.shell_profiles if item.id == profile_id), None)
    if profile:
        return profile
    return next((item for item in detected_profiles() if item.id == profile_id), None)


def resolve_agent_profile(config: Config, profile_id: str, backend: str) -> ResolvedAgentProfile:
    """Resolve a launch profile for an agent harness.

    The reserved-argument check runs here as well as in :func:`config._validate`, and
    that is not redundant. Configuration reaches this process by three routes that do
    not share a validator: the settings API, a hand-edited `config.toml`, and a file
    written by a build that predates a newly reserved token. A profile arriving by any
    of them would otherwise spawn a pane whose hook identity or conversation id mux no
    longer controls, and the failure is silent - the CLI runs, and nothing ever reports
    a turn. Refusing at the spawn boundary is the last place that is visible.
    """
    profile = find_profile(config, profile_id)
    if not profile:
        raise ValueError({"profile_id": f"unknown launch profile: {profile_id}"})
    if profile.backend != backend:
        raise ValueError(
            {
                "profile_id": (
                    f"launch profile {profile_id} starts {profile.backend}, "
                    f"not {backend}"
                )
            }
        )
    if not profile.enabled:
        raise ValueError({"profile_id": f"launch profile is disabled: {profile_id}"})
    conflict = reserved_launch_arg_conflict(backend, profile.args)
    if conflict:
        log.warning(
            "launch_profile_reserved_argument profile_id=%s backend=%s token=%s",
            profile_id,
            backend,
            conflict,
        )
        raise ValueError(
            {
                "profile_id": (
                    f"launch profile {profile_id} sets {conflict}, which swe-mux "
                    f"builds for {backend} itself"
                )
            }
        )
    executable = profile.executable.strip()
    return ResolvedAgentProfile(
        profile.id, backend, executable or None, tuple(profile.args), dict(profile.env)
    )


def _wsl_cwd(executable: str, args: list[str], cwd: Path) -> str:
    distro: str | None = None
    if "--distribution" in args:
        index = args.index("--distribution")
        if index + 1 < len(args):
            distro = args[index + 1]
    command = [executable]
    if distro:
        command.extend(["--distribution", distro])
    command.extend(["--", "wslpath", "-a", "-u", str(cwd)])
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=background_creation_flags(),
        )
        translated = result.stdout.strip()
        if result.returncode == 0 and translated.startswith("/"):
            return translated
    except (OSError, subprocess.TimeoutExpired):
        pass
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", str(cwd))
    if match:
        return f"/mnt/{match.group(1).lower()}/{match.group(2).replace('\\', '/')}"
    raise ValueError(f"cannot translate cwd for WSL: {cwd}")


_POWERSHELL_NAMES = {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}
# -Command/-File hand PowerShell a script instead of an interactive prompt. There is no
# prompt to instrument and a second -Command would replace the profile's own, so a shell
# carrying either cannot be wrapped at all.
_POWERSHELL_SCRIPT_ARGS = {"-command", "-c", "-file", "-f"}


def _powershell_bootstrap(*, osc7: bool, breakpoints: bool = False) -> str:
    """The ``-Command`` block an interactive PowerShell session runs after ``$PROFILE``.

    Restoring the agent-shim directory is unconditional, because losing it is silent and
    total: ``$PROFILE`` scripts commonly rebuild PATH from the registry
    (``$env:PATH = [Environment]::GetEnvironmentVariable('Path','Machine') + ...``), which
    discards everything the daemon prepended. ``claude`` in that session then resolves to
    the real CLI instead of ``~/.mux/bin/claude.cmd``, so the launch never promotes the
    pane, never receives ``--session-id`` or the per-session hook settings, and the pane
    stays an unnamed shell for the rest of its life. The block runs after the profile, so
    it re-asserts the shim directory at the *front* of PATH rather than trusting whatever
    the profile left behind — presence alone is not enough when the profile prepends
    directories of its own.

    OSC 7 cwd reporting is the opt-in half (``cwd_integration``) and only wraps ``prompt``.

    OSC 133 shell-integration markers are the other reason to wrap ``prompt``: the
    prompt function runs exactly when the human's own command has finished, which
    is the breakpoint attention ranking delivers against. Both features share one
    wrapper, and the original prompt is preserved and called either way.
    """
    # Rebuilt rather than tested-and-prepended so the invariant matches what
    # `launchers.create_agent_shims` establishes at spawn: exactly one shim dir, in front.
    restore = (
        "if($env:MUX_SHIM_DIR){$env:PATH=(@($env:MUX_SHIM_DIR)+"
        "(($env:PATH -split ';')|Where-Object{$_ -and $_ -ne $env:MUX_SHIM_DIR}))"
        " -join ';'};"
    )
    if not osc7 and not breakpoints:
        return restore
    # `$?` has to be read as the prompt's first statement or it reports this
    # function's own last operation instead of the command that just ended.
    marker = (
        "$__mux_ok=if($?){0}else{1};"
        '[Console]::Write("$([char]27)]133;D;$__mux_ok$([char]7)'
        '$([char]27)]133;A$([char]7)");'
        if breakpoints
        else ""
    )
    report_cwd = (
        "try {$u=[System.Uri]::new((Get-Location).ProviderPath).AbsoluteUri;"
        '[Console]::Write("$([char]27)]7;$u$([char]7)")} catch {};'
        if osc7
        else ""
    )
    return restore + (
        "$global:__swe_mux_prompt=$function:prompt;"
        "function global:prompt {"
        + marker
        + report_cwd
        + "if($global:__swe_mux_prompt){& $global:__swe_mux_prompt}"
        'else {"PS $($executionContext.SessionState.Path.CurrentLocation)> "}}'
    )


def resolve_profile(
    config: Config, profile_id: str, cwd: Path, *, interactive: bool = True
) -> ResolvedProfile:
    profile = find_profile(config, profile_id)
    if not profile:
        raise ValueError({"profile_id": f"unknown shell profile: {profile_id}"})
    if is_agent_harness(profile.backend):
        # Reachable through a stale `default_shell_profile` or a project file naming
        # an agent profile. Refused rather than coerced: the wrapper below builds an
        # interactive shell command line, and applying it to an agent CLI produces a
        # command that neither starts a shell nor starts the agent.
        raise ValueError(
            {"profile_id": f"launch profile {profile_id} starts {profile.backend}, not a shell"}
        )
    if not profile.enabled:
        raise ValueError({"profile_id": f"shell profile is disabled: {profile_id}"})
    if profile.platforms and "windows" not in profile.platforms and os.name == "nt":
        raise ValueError({"profile_id": "shell profile is unavailable on Windows"})
    executable = _available(profile.executable) or profile.executable
    if not Path(executable).is_file() and not _available(executable):
        raise ValueError({"profile_id": f"executable not found: {profile.executable}"})
    argv = list(profile.args)
    capabilities = list(profile.capabilities)
    executable_name = Path(executable).name.casefold()
    if interactive and executable_name in _POWERSHELL_NAMES:
        if any(item.casefold() in _POWERSHELL_SCRIPT_ARGS for item in argv):
            # Only the explicitly requested feature earns a hard failure. The shim-path
            # guard is implicit and applies to every PowerShell profile, so it degrades
            # silently instead of making a previously working profile unspawnable.
            if profile.cwd_integration:
                raise ValueError(
                    {
                        "profile_id": (
                            "cwd integration cannot wrap a profile with Command/File arguments"
                        )
                    }
                )
        else:
            if not any(item.casefold() == "-noexit" for item in argv):
                argv.append("-NoExit")
            breakpoints = bool(getattr(config, "attention_breakpoint_markers", True))
            argv.extend(
                [
                    "-Command",
                    _powershell_bootstrap(
                        osc7=profile.cwd_integration, breakpoints=breakpoints
                    ),
                ]
            )
            if profile.cwd_integration:
                capabilities.append("cwd-osc7")
            if breakpoints:
                capabilities.append("breakpoint-osc133")
    if profile.cwd_strategy == "wsl":
        argv.extend(["--cd", _wsl_cwd(executable, argv, cwd)])
    return ResolvedProfile(
        profile.id, executable, tuple(argv), dict(profile.env), tuple(dict.fromkeys(capabilities))
    )
