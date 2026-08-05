from __future__ import annotations

# The screen buffer, and only the screen buffer. `tui.raw_output_mode=true` was
# set here alongside it and is deliberately not: it makes Codex stop rendering a
# rich transcript and echo tool output verbatim instead, which is presentation
# rather than anything mux depends on. Every consequence the rest of the codebase
# attributes to "raw scrollback mode" follows from the alternate-screen key alone:
# the prompt living on the normal screen (`delivery_readiness`), the transcript
# being real scrollback lines rather than repaints of one fixed screen
# (`scrollback`, `config.attach_replay_bytes`), and xterm owning every scroll so
# jump-to-latest works (`terminalViewport.appOwnsTail`). Raw output is off in
# Codex's own defaults, so leaving it out puts `~/.codex/config.toml` in charge
# of it rather than forcing the opposite of what the CLI ships.
_SCROLLBACK_DEFAULTS = (("tui.alternate_screen", '"never"'),)
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
