"""Every rule id that spends must be labelled, and its switch must be real.

The spend table's job is to answer "what is costing me, and what turns it off".
It answers that from `FEATURE_SPENDERS`, and a spender missing from that table
falls through to a default meaning "retired, and nothing on this page can turn it
off". That default is indistinguishable from the truth, so the failure is silent
and *inverted*: the operator is told a feature they use every day is dead.

It happened. `builtin:assistant` shipped with Phase 10.6 and was never added, so
Resources -> Tokens described the assistant as `retired · off` while it was
running (reported 2026-08-20). `builtin:adaptive-title` had the same gap and was
found by writing this test.

Two guards, both required, mirroring `test_harness_adapter_matrix.py`:

1. Every `*_RULE_ID` constant in the source that a feature bills under must have
   an entry. Discovery is by scanning the source rather than by importing a list,
   because the whole failure mode is a module nobody remembered to wire in.
2. Every entry's `setting_key` must name a real `Config` field. `getattr(config,
   key, False)` swallows a typo and reports the feature as permanently off, which
   is the same lie in a different place.

The discriminator between the two id families is punctuation, and it is asserted
rather than assumed: automation's own built-in rules use `builtin.` and are
labelled from the live engine, while feature spenders use `builtin:` and are
labelled from this table.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from swe_mux.assistant import ASSISTANT_RULE_ID
from swe_mux.config import Config
from swe_mux.routes.automation import FEATURE_SPENDERS, _label_spend_rows

SOURCE = Path(__file__).resolve().parents[1] / "src" / "swe_mux"
RULE_ID_ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*_RULE_ID)\s*=\s*[\"']([^\"']+)[\"']", re.M)


def declared_rule_ids() -> dict[str, str]:
    """Every module-level `*_RULE_ID = "..."` in the package, by constant name."""
    found: dict[str, str] = {}
    for path in sorted(SOURCE.glob("*.py")):
        for name, value in RULE_ID_ASSIGNMENT.findall(path.read_text(encoding="utf-8")):
            found[f"{path.stem}.{name}"] = value
    return found


def test_every_feature_spender_is_labelled() -> None:
    declared = declared_rule_ids()
    assert declared, "the rule-id scan found nothing; the pattern has drifted"
    features = {
        name: value for name, value in declared.items() if value.startswith("builtin:")
    }
    assert features, "no feature spenders found; the `builtin:` convention has drifted"
    missing = {
        name: value for name, value in features.items() if value not in FEATURE_SPENDERS
    }
    assert not missing, (
        "these rule ids bill the observer budget but have no FEATURE_SPENDERS entry, "
        f"so the spend table calls them retired and off: {missing}"
    )


def test_no_automation_rule_is_labelled_as_a_feature() -> None:
    # Automation's own rules are labelled from the live engine, which knows their
    # real enabled state. Duplicating one here would shadow that with a static
    # answer that cannot follow the toggle.
    engine_style = {
        name: value
        for name, value in declared_rule_ids().items()
        if value.startswith("builtin.")
    }
    overlap = {name: value for name, value in engine_style.items() if value in FEATURE_SPENDERS}
    assert not overlap, f"automation rules must not be listed as feature spenders: {overlap}"


def test_every_feature_switch_is_a_real_config_field() -> None:
    fields = {field.name for field in dataclasses.fields(Config)}
    unknown = {
        rule_id: feature.setting_key
        for rule_id, feature in FEATURE_SPENDERS.items()
        if feature.setting_key not in fields
    }
    assert not unknown, (
        "a setting_key that is not a Config field reads as False forever, so the "
        f"feature is reported permanently off: {unknown}"
    )


def test_every_feature_switch_is_a_boolean_toggle() -> None:
    # The column is rendered as on/off, so a non-boolean setting would be
    # truthiness-tested into a state the operator cannot map back to a control.
    types = {field.name: field.type for field in dataclasses.fields(Config)}
    wrong = {
        rule_id: feature.setting_key
        for rule_id, feature in FEATURE_SPENDERS.items()
        if "bool" not in str(types.get(feature.setting_key, ""))
    }
    assert not wrong, f"feature switches must be boolean config flags: {wrong}"


def spend_row(rule_id: str) -> dict[str, object]:
    return {
        "rule_id": rule_id, "calls": 3, "tokens": 900, "cost_usd": 0.01,
        "today_calls": 1, "today_tokens": 300, "today_cost_usd": 0.004,
    }


def test_a_live_feature_is_not_reported_as_retired(tmp_path: Path) -> None:
    # The reported bug, at the level that produced it: the assistant was enabled
    # and every one of its rows said "retired · off".
    config = Config(data_dir=tmp_path, assistant_enabled=True)
    row = _label_spend_rows([spend_row(ASSISTANT_RULE_ID)], {}, config)[0]
    assert row["kind"] == "feature"
    assert row["enabled"] is True
    assert row["label"] == "Mux assistant"
    assert row["setting_label"] == "Mux assistant"


def test_a_switched_off_feature_reads_as_spent_history(tmp_path: Path) -> None:
    # Still a feature, and still off - which is the distinction the column
    # exists to draw. Asserting True unconditionally, as this once did, told the
    # operator to go turn off something that was already off.
    config = Config(data_dir=tmp_path, assistant_enabled=False)
    row = _label_spend_rows([spend_row(ASSISTANT_RULE_ID)], {}, config)[0]
    assert row["kind"] == "feature"
    assert row["enabled"] is False


def test_an_unknown_id_is_still_retired(tmp_path: Path) -> None:
    # The default has to keep working: an id that really was retired has no
    # control left, and saying so is the honest answer.
    row = _label_spend_rows([spend_row("builtin:long-gone")], {}, Config(data_dir=tmp_path))[0]
    assert row["kind"] == "retired"
    assert row["enabled"] is False
    assert row["label"] == "builtin:long-gone"


def test_a_live_automation_rule_still_wins_over_this_table(tmp_path: Path) -> None:
    # The engine is the authority for its own rules, including their toggles.
    engine = {
        "built_in_rules": [
            {"id": "builtin.session-titler", "name": "Session titler",
             "description": "Names a session", "enabled": True,
             "setting_label": "Session titler"},
        ]
    }
    rows = _label_spend_rows(
        [spend_row("builtin.session-titler")], engine, Config(data_dir=tmp_path)
    )
    row = rows[0]
    assert row["kind"] == "observer"
    assert row["enabled"] is True
    assert row["setting_label"] == "Session titler"


def test_every_feature_spender_names_itself_and_its_setting() -> None:
    blank = {
        rule_id: feature
        for rule_id, feature in FEATURE_SPENDERS.items()
        if not (feature.label.strip() and feature.detail.strip() and feature.setting_label.strip())
    }
    assert not blank, f"a spend row with no name shows its raw rule id: {blank}"
