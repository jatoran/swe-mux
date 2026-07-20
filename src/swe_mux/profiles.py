from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Config, ShellProfile


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    profile_id: str
    executable: str
    argv: tuple[str, ...]
    env: dict[str, str]
    capabilities: tuple[str, ...]


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


def detected_profiles() -> list[ShellProfile]:
    profiles: list[ShellProfile] = []
    if executable := _available("powershell.exe"):
        profiles.append(
            ShellProfile(
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
            ShellProfile(
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
            ShellProfile(
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
                ShellProfile(
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
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=3)
        translated = result.stdout.strip()
        if result.returncode == 0 and translated.startswith("/"):
            return translated
    except (OSError, subprocess.TimeoutExpired):
        pass
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", str(cwd))
    if match:
        return f"/mnt/{match.group(1).lower()}/{match.group(2).replace('\\', '/')}"
    raise ValueError(f"cannot translate cwd for WSL: {cwd}")


def resolve_profile(config: Config, profile_id: str, cwd: Path) -> ResolvedProfile:
    profile = next((item for item in config.shell_profiles if item.id == profile_id), None)
    if not profile:
        profile = next((item for item in detected_profiles() if item.id == profile_id), None)
    if not profile:
        raise ValueError({"profile_id": f"unknown shell profile: {profile_id}"})
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
    if profile.cwd_integration and executable_name in {
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
    }:
        if any(item.casefold() in {"-command", "-c", "-file", "-f"} for item in argv):
            raise ValueError(
                {"profile_id": "cwd integration cannot wrap a profile with Command/File arguments"}
            )
        script = (
            "$global:__swe_mux_prompt=$function:prompt;"
            "function global:prompt {"
            "try {$u=[System.Uri]::new((Get-Location).ProviderPath).AbsoluteUri;"
            '[Console]::Write("$([char]27)]7;$u$([char]7)")} catch {};'
            "if($global:__swe_mux_prompt){& $global:__swe_mux_prompt}"
            'else {"PS $($executionContext.SessionState.Path.CurrentLocation)> "}}'
        )
        if not any(item.casefold() == "-noexit" for item in argv):
            argv.append("-NoExit")
        argv.extend(["-Command", script])
        capabilities.append("cwd-osc7")
    if profile.cwd_strategy == "wsl":
        argv.extend(["--cd", _wsl_cwd(executable, argv, cwd)])
    return ResolvedProfile(
        profile.id, executable, tuple(argv), dict(profile.env), tuple(dict.fromkeys(capabilities))
    )
