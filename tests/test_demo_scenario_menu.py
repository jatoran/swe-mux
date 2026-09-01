"""The landing page's scenario dropdown still matches the demo's own catalogue.

`site/index.html` is hand-written and static: it never loads the demo bundle, so it cannot
ask `scenarioMenu()` what to offer and the eight options are written out by hand. The
demo's frames are told a scenario **by id**, which is what makes drift silent rather than
loud - a renamed entry keeps working, and the page goes on advertising a name the product
no longer uses. An id that was removed is worse: the option is still there, choosing it
dispatches into the frame, the director finds no such scenario and nothing happens at all,
with nothing on screen to say why.

Both halves are asserted here because they fail differently. The ids must be exactly the
catalogue's, in order, so the dropdown cannot offer one that does not exist or omit one
that does. The labels must be the catalogue's labels, so the two places a visitor can read
a scenario's name - the embed's dropdown and the full-screen bar, which does read
`scenarioMenu()` - agree.

The full-screen bar needs no test: it is generated from the catalogue at render time.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX = REPO_ROOT / "site" / "index.html"
SCENARIOS = REPO_ROOT / "frontend" / "src" / "demo" / "scenarios.ts"

# Each catalogue entry opens `id: '<id>',` and carries `label: '<label>',` on the next
# line. Matched together rather than separately so a stray `id:` elsewhere in the file
# cannot be read as a scenario.
_ENTRY = re.compile(r"^\s*id: '([a-z]+)',\n\s*label: '([^']+)',$", re.MULTILINE)
_OPTION = re.compile(r'<option value="([a-z]+)">([^<]+)</option>')


def _catalogue() -> list[tuple[str, str]]:
    source = SCENARIOS.read_text(encoding="utf-8").replace("\r\n", "\n")
    entries = _ENTRY.findall(source)
    assert entries, "no scenarios parsed out of scenarios.ts"
    return entries


def _dropdown() -> list[tuple[str, str]]:
    # The first option is the empty placeholder ("scenarios"), which `_OPTION` skips
    # because its value is empty.
    return _OPTION.findall(INDEX.read_text(encoding="utf-8"))


def test_the_dropdown_offers_exactly_the_scenarios_that_exist() -> None:
    assert [item[0] for item in _dropdown()] == [item[0] for item in _catalogue()]


def test_the_dropdown_calls_each_scenario_what_the_catalogue_calls_it() -> None:
    assert dict(_dropdown()) == dict(_catalogue())


def test_a_scenario_label_names_an_outcome_rather_than_a_mechanism() -> None:
    """A dropdown row on a marketing page has to answer "why would I click that".

    Not a taste check - two mechanical properties that the labels this replaced both
    failed. A label starts with a capital, because it is a sentence offering to do
    something rather than a fragment describing a feature ("one field finds everything"),
    and it opens with a verb, because the thing being offered is an act.
    """
    openers = {
        "Take", "Queue", "Let", "Open", "Land", "Find", "Bring", "Ask", "Watch", "See",
        "Split", "Send", "Switch", "Run", "Read", "Start",
    }
    for scenario_id, label in _catalogue():
        assert label[:1].isupper(), f"{scenario_id} is labelled {label!r}"
        first = label.split(" ", 1)[0]
        assert first in openers, f"{scenario_id} opens with {first!r}, which is not a verb"
