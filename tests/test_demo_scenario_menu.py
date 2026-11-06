"""The website's generated demo copy follows the same bilingual scenario catalog."""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX = REPO_ROOT / "site/index.html"
SCENARIOS = REPO_ROOT / "frontend/src/demo/scenarios.ts"
CATALOGS = REPO_ROOT / "frontend/src/demo/catalogs"
_ENTRY = re.compile(
    r"""^\s*id: '([a-z]+)',\n\s*get label\(\) \{ return dt\("([^"]+)"\) \},""",
    re.MULTILINE,
)
_OPTION = re.compile(r'<option value="([a-z]+)">([^<]+)</option>')


def _catalogue(locale: str = "en") -> list[tuple[str, str]]:
    source = SCENARIOS.read_text(encoding="utf-8").replace("\r\n", "\n")
    entries = _ENTRY.findall(source)
    assert entries, "no scenarios parsed out of scenarios.ts"
    catalog = json.loads((CATALOGS / f"{locale}.json").read_text(encoding="utf-8"))
    return [(scenario, catalog[message]) for scenario, message in entries]


def _dropdown() -> list[tuple[str, str]]:
    source = INDEX.read_text(encoding="utf-8")
    select = re.search(r'<select id="demoscenario".*?</select>', source, re.DOTALL)
    assert select
    return _OPTION.findall(select.group())


def test_the_dropdown_offers_exactly_the_scenarios_that_exist() -> None:
    assert [item[0] for item in _dropdown()] == [item[0] for item in _catalogue()]


def test_the_dropdown_calls_each_scenario_what_the_catalogue_calls_it() -> None:
    assert dict(_dropdown()) == dict(_catalogue())


def test_generated_website_copy_matches_both_demo_catalogs() -> None:
    copy = json.loads((REPO_ROOT / "site/demo-language.json").read_text(encoding="utf-8"))
    assert set(copy) == {"en", "zh-Hans"}
    for locale in copy:
        assert [(row["id"], row["label"]) for row in copy[locale]["scenarios"]] == _catalogue(
            locale
        )


def test_a_scenario_label_names_an_outcome_rather_than_a_mechanism() -> None:
    openers = {
        "Take",
        "Queue",
        "Let",
        "Open",
        "Land",
        "Find",
        "Bring",
        "Ask",
        "Watch",
        "See",
        "Split",
        "Send",
        "Switch",
        "Run",
        "Read",
        "Start",
    }
    for scenario_id, label in _catalogue():
        assert label[:1].isupper(), f"{scenario_id} is labelled {label!r}"
        assert label.split(" ", 1)[0] in openers
