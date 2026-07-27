from __future__ import annotations

_SCROLLBACK_DEFAULTS = (
    ("tui.alternate_screen", '"never"'),
    ("tui.raw_output_mode", "true"),
)
_CONFIG_FLAGS = {"-c", "--config"}


def _configured_keys(args: list[str]) -> set[str]:
    keys: set[str] = set()
    for index, arg in enumerate(args):
        assignment: str | None = None
        if arg in _CONFIG_FLAGS and index + 1 < len(args):
            assignment = args[index + 1]
        elif arg.startswith("--config="):
            assignment = arg.removeprefix("--config=")
        elif arg == "--no-alt-screen":
            keys.add("tui.alternate_screen")
        if assignment and "=" in assignment:
            keys.add(assignment.split("=", 1)[0].strip())
    return keys


def with_scrollback_safe_tui(args: list[str]) -> list[str]:
    """Default interactive Codex to an xterm-compatible scrollback mode.

    Explicit user config wins: defaults are only added for keys that are absent
    from the configured or per-launch arguments.
    """
    configured = _configured_keys(args)
    defaults = [
        value
        for key, setting in _SCROLLBACK_DEFAULTS
        if key not in configured
        for value in ("-c", f"{key}={setting}")
    ]
    return [*defaults, *args]
