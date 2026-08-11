"""The Claude width envelope spans three files that can only drift silently.

`config.py` decides which caps are accepted, `terminalViewport.ts` decides which are
offered and which are honoured once received, and `TerminalPane.tsx` is the only thing
that applies any of them. A step added to one and not the others produces either a
select entry the daemon rejects on save, or a stored value the browser quietly falls
back from - neither of which surfaces as a failure anywhere else.

The cap exists because Claude Code's live-region renderer can leave stale and
duplicated cells across large column changes. It is a setting rather than a constant
because that evidence is about a CLI on its own release schedule, so the number that
was right when it was measured need not stay right - while the cost of a stale number
lands on a user who cannot tell a deliberate envelope from a terminal that refuses to
resize.
"""

from __future__ import annotations

import re
from pathlib import Path

from swe_mux.config import CLAUDE_MAX_COLUMNS, Config

SRC = Path(__file__).parents[1] / "frontend" / "src"


def test_browser_offers_exactly_the_caps_the_daemon_accepts() -> None:
    source = (SRC / "terminalViewport.ts").read_text(encoding="utf-8")
    steps = re.search(r"CLAUDE_MAX_COLUMN_STEPS\s*=\s*\[([^\]]+)\]", source)
    assert steps, "terminalViewport.ts must export a literal CLAUDE_MAX_COLUMN_STEPS array"
    assert {int(value) for value in steps.group(1).split(",")} == CLAUDE_MAX_COLUMNS


def test_the_default_is_todays_behaviour() -> None:
    """The setting must be inert until someone changes it."""
    config = Config()
    assert config.claude_max_columns == 120
    assert 120 in CLAUDE_MAX_COLUMNS
    source = (SRC / "terminalViewport.ts").read_text(encoding="utf-8")
    assert re.search(
        r"DEFAULT_CLAUDE_MAX_COLUMNS\s*:\s*ClaudeMaxColumns\s*=\s*120", source
    )


def test_zero_is_the_spelling_for_no_cap() -> None:
    """Off has to be a value, not the absence of one.

    A missing key means "daemon older than this build" to the browser, which falls
    back to the default - so an opt-out expressed by omission would silently reinstate
    the cap it was meant to remove. Acceptance of the value itself is exercised in
    `test_config_service.py`, with the rest of the validation.
    """
    assert 0 in CLAUDE_MAX_COLUMNS
    source = (SRC / "terminalViewport.ts").read_text(encoding="utf-8")
    assert "CLAUDE_MAX_COLUMNS_OFF = 0" in source


def test_only_declared_panes_carry_the_envelope() -> None:
    """Codex and shell panes fill their box, which is the asymmetry users report.

    Pinned in the policy function rather than left to the caller: the host style is
    the only place the cap is applied, and a backend check inlined there is one that
    no test can reach without a browser.

    Which panes carry it is read from the registry (`width_envelope`) rather than
    compiled into the browser, so the daemon and the browser cannot disagree about
    it. Claude is the only harness declaring it today, and that is asserted on the
    descriptor rather than on the source text.
    """
    from swe_mux.harness import HARNESSES

    source = (SRC / "terminalViewport.ts").read_text(encoding="utf-8")
    policy = re.search(r"export function claudeWidthCap\((.*?)\n\}", source, re.DOTALL)
    assert policy, "claudeWidthCap must stay the single decision point for the envelope"
    assert "appliesWidthEnvelope(backend)" in policy.group(1)
    assert "compactLayout" in policy.group(1)
    assert {
        name for name, harness in HARNESSES.items() if harness.applies_width_envelope
    } == {"claude"}


def test_the_setting_is_reachable_from_the_terminals_tab() -> None:
    """A cap nobody can find is the state this replaced.

    Settings search indexes the rendered tab, so the prose is what makes "width" and
    "columns" find this control - and the pane's own notice sends the user here.
    """
    settings = (SRC / "Settings.tsx").read_text(encoding="utf-8")
    pane = (SRC / "TerminalPane.tsx").read_text(encoding="utf-8")
    app = (SRC / "App.tsx").read_text(encoding="utf-8")
    assert "draft.claude_max_columns" in settings
    assert "CLAUDE_MAX_COLUMN_STEPS.map" in settings
    for word in ("width", "columns", "No limit"):
        assert word in settings, f"the Claude width control has no {word!r} to search for"
    assert "onConfigureWidth" in pane
    assert "onConfigureWidth={()=>openSettings('Terminals')}" in app
