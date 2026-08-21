"""The one budget shape: its arithmetic, its migration, and its blind spot.

Three things are pinned here and nowhere else.

**Migration preserves semantics.** A config written by the previous build must
enforce exactly what it enforced before. That is not a property of any one
budget - it is a property of the mapping, so it is checked per cap against a
table of what the pre-`Budget` code actually compared, including the case that
matters most: a config that set one half of a pair and inherited the other half
from a dataclass default it never mentioned.

**The mode is the contract.** `tokens` must not consult dollars and `usd` must
not consult tokens, or "no cap silently loosens" becomes "no cap silently
tightens", which is the same defect wearing the other hat.

**Unmeasurable cost is stated, never counted as zero.** A bring-your-own
endpoint reports no `usage.cost`, and the failure mode this guards against is
the quiet one: a dollar cap that looks enforced while its ledger sits at $0.00
forever.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from swe_mux import budget
from swe_mux.budget import Budget, coerce_budget
from swe_mux.config import BUDGET_FIELDS, BUDGET_SPECS, Config, load_config, update_config

# ---------------------------------------------------------------- the shape


def test_the_mode_decides_which_axis_binds_and_nothing_else_does() -> None:
    spend = {"tokens": 1_000, "cost_usd": 5.0}
    both = Budget(tokens=100, usd=1.0, mode="either")
    assert budget.spent_out(both, spend, label="x").axis == "tokens"

    tokens_only = Budget(tokens=10_000, usd=1.0, mode="tokens")
    # Over the dollar figure it carries, and under the token figure it enforces.
    assert budget.spent_out(tokens_only, spend, label="x").exhausted is False

    usd_only = Budget(tokens=100, usd=10.0, mode="usd")
    assert budget.spent_out(usd_only, spend, label="x").exhausted is False


def test_either_trips_on_whichever_is_reached_first() -> None:
    cap = Budget(tokens=1_000, usd=1.0, mode="either")
    assert budget.spent_out(cap, {"tokens": 1_000, "cost_usd": 0.0}, label="x").axis == "tokens"
    assert budget.spent_out(cap, {"tokens": 0, "cost_usd": 1.0}, label="x").axis == "usd"
    assert budget.spent_out(cap, {"tokens": 999, "cost_usd": 0.99}, label="x").exhausted is False


def test_spent_out_is_inclusive_and_would_exceed_is_strict() -> None:
    """The two comparisons the pre-`Budget` sites used, kept apart deliberately.

    Landing exactly on a ceiling is allowed by preflight (the estimate is a
    conservative maximum, so refusing there would refuse calls that fit) and
    refused by the after-the-fact check (the money is spent).
    """
    cap = Budget(tokens=100, mode="tokens")
    assert budget.spent_out(cap, {"tokens": 100}, label="x").exhausted is True
    assert budget.would_exceed(cap, {"tokens": 90}, label="x", tokens=10).exhausted is False
    assert budget.would_exceed(cap, {"tokens": 90}, label="x", tokens=11).exhausted is True


def test_a_refusal_names_the_axis_in_words_a_surface_can_render() -> None:
    cap = Budget(tokens=1, usd=0.0, mode="either")
    assert (
        budget.spent_out(cap, {"tokens": 5}, label="the daily Scan timeline").reason
        == "the daily Scan timeline token budget is exhausted"
    )
    assert (
        budget.would_exceed(cap, {"tokens": 0}, label="the global", tokens=99).reason
        == "conservative preflight estimate exceeds the global token budget"
    )
    assert (
        budget.would_exceed(
            cap, {"tokens": 0}, label="the run Scan timeline", tokens=99, phrasing="exhausted"
        ).reason
        == "the run Scan timeline token budget is exhausted"
    )


def test_an_absent_figure_on_an_enforced_axis_is_no_limit_rather_than_zero() -> None:
    """`None` and `0` must never collapse into each other.

    Zero is the strictest cap there is; absent is no cap at all. A coercion that
    read a missing key as zero would switch a feature off on upgrade, and one
    that read zero as missing would ignore a deliberate stop.
    """
    unlimited = budget.spent_out(Budget(tokens=None, mode="tokens"), {"tokens": 10**9}, label="x")
    assert unlimited.exhausted is False
    stopped = budget.spent_out(Budget(tokens=0, mode="tokens"), {"tokens": 0}, label="x")
    assert stopped.exhausted is True


def test_gauges_draw_only_the_axes_that_can_stop_something() -> None:
    rows = budget.gauges(
        Budget(tokens=100, usd=9.0, mode="tokens"),
        {"tokens": 50, "cost_usd": 3.0},
        id_prefix="scan_daily",
        token_label="Scan tokens today",
        usd_label="Scan cost today",
    )
    assert [row["id"] for row in rows] == ["scan_daily_tokens"]


def test_validate_requires_the_axes_the_mode_names() -> None:
    errors: dict[str, str] = {}
    budget.validate(
        Budget(tokens=None, usd=None, mode="either"),
        field="cap", errors=errors, max_tokens=100, max_usd=10.0,
    )
    assert set(errors) == {"cap.tokens", "cap.usd"}

    # The unnamed axis is optional, and bounds-checked only when it has a value.
    errors.clear()
    budget.validate(
        Budget(tokens=10**12, usd=1.0, mode="usd"),
        field="cap", errors=errors, max_tokens=100, max_usd=10.0,
    )
    assert set(errors) == {"cap.tokens"}


def test_coercion_survives_a_hand_edited_file_without_loosening_anything() -> None:
    fallback = Budget(tokens=7, usd=0.5, mode="usd")
    assert coerce_budget("nonsense", fallback=fallback) is fallback
    assert coerce_budget({"mode": "sideways"}, fallback=fallback).mode == "usd"
    # `True` is an `int`, and a truthy 1 arriving as a token cap would be the
    # tightest cap in the app rather than a rejected value.
    assert coerce_budget({"tokens": True, "mode": "tokens"}, fallback=fallback).tokens is None


# ------------------------------------------------------------- the migration

#: What the pre-`Budget` build compared, per cap, as `(legacy keys, mode)`.
#: Written out longhand rather than derived from `BUDGET_SPECS`, because a test
#: that derives its expectation from the thing under test proves only that the
#: code agrees with itself.
LEGACY_SEMANTICS: dict[str, tuple[tuple[str, ...], str]] = {
    "automation_daily_budget": (
        ("automation_daily_token_budget", "automation_daily_budget_usd"), "either",
    ),
    "automation_rule_daily_budget": (
        ("automation_rule_daily_token_budget", "automation_rule_daily_budget_usd"), "either",
    ),
    "scan_timeline_daily_budget": (
        ("scan_timeline_daily_token_budget", "scan_timeline_daily_budget_usd"), "either",
    ),
    "scan_timeline_run_budget": (("scan_timeline_run_token_budget",), "tokens"),
    "assistant_daily_budget": (("assistant_daily_budget_usd",), "usd"),
    "tts_daily_budget": (("tts_daily_budget_usd",), "usd"),
    "project_card_daily_budget": (("project_card_daily_budget_usd",), "usd"),
    "attention_narration_daily_budget": (("attention_narration_daily_budget_usd",), "usd"),
}


def test_every_budget_is_inventoried_here_and_in_the_specs() -> None:
    assert set(LEGACY_SEMANTICS) == set(BUDGET_FIELDS)


def test_each_cap_migrates_onto_the_mode_matching_the_unit_it_enforced() -> None:
    for spec in BUDGET_SPECS:
        keys, mode = LEGACY_SEMANTICS[spec.field]
        assert spec.default.mode == mode, spec.field
        legacy = {key for key in (spec.legacy_tokens, spec.legacy_usd) if key}
        assert legacy == set(keys), spec.field
        # The mode may not name an axis the migration cannot fill.
        if mode in {"tokens", "either"}:
            assert spec.default.tokens is not None, spec.field
        if mode in {"usd", "either"}:
            assert spec.default.usd is not None, spec.field


def _legacy_config(tmp_path: Path, body: str, *, schema: int = 29) -> Config:
    path = tmp_path / "config.toml"
    path.write_text(f"schema_version = {schema}\n{body}", encoding="utf-8")
    return load_config(path)


def test_a_previous_build_config_enforces_exactly_what_it_enforced_before(
    tmp_path: Path,
) -> None:
    """The whole point of the section, checked value by value.

    Deliberate figures, none of them defaults, so a migration that quietly fell
    back to a default would be visible rather than accidentally correct.
    """
    config = _legacy_config(
        tmp_path,
        "automation_daily_token_budget = 111\n"
        "automation_daily_budget_usd = 1.5\n"
        "automation_rule_daily_token_budget = 222\n"
        "automation_rule_daily_budget_usd = 2.5\n"
        "scan_timeline_daily_token_budget = 3333\n"
        "scan_timeline_daily_budget_usd = 3.5\n"
        "scan_timeline_run_token_budget = 4444\n"
        "assistant_daily_budget_usd = 5.5\n"
        "tts_daily_budget_usd = 6.5\n"
        "project_card_daily_budget_usd = 7.5\n"
        "attention_narration_daily_budget_usd = 8.5\n",
    )
    assert config.automation_daily_budget == Budget(111, 1.5, "either")
    assert config.automation_rule_daily_budget == Budget(222, 2.5, "either")
    assert config.scan_timeline_daily_budget == Budget(3333, 3.5, "either")
    assert config.scan_timeline_run_budget == Budget(4444, None, "tokens")
    assert config.assistant_daily_budget == Budget(None, 5.5, "usd")
    assert config.tts_daily_budget == Budget(None, 6.5, "usd")
    assert config.project_card_daily_budget == Budget(None, 7.5, "usd")
    assert config.attention_narration_daily_budget == Budget(None, 8.5, "usd")


def test_half_a_pair_keeps_the_default_that_was_silently_enforcing_the_other_half(
    tmp_path: Path,
) -> None:
    """The case a naive migration loses.

    A config naming only `automation_daily_budget_usd` still had a token cap:
    the dataclass default, enforced by code that never asked whether the file
    mentioned it. Dropping it would turn a bounded install into one with no
    token ceiling at all, which is the exact loosening the rule forbids.
    """
    config = _legacy_config(tmp_path, "automation_daily_budget_usd = 3.0\n")
    assert config.automation_daily_budget.mode == "either"
    assert config.automation_daily_budget.usd == 3.0
    assert (
        config.automation_daily_budget.tokens
        == BUDGET_FIELDS["automation_daily_budget"].default.tokens
    )


def test_a_config_that_mentions_no_budget_at_all_gets_the_defaults_unchanged(
    tmp_path: Path,
) -> None:
    config = _legacy_config(tmp_path, "port = 8765\n")
    for spec in BUDGET_SPECS:
        assert getattr(config, spec.field) == spec.default, spec.field


def test_the_new_table_survives_a_save_and_reload_including_its_absent_axis(
    tmp_path: Path,
) -> None:
    """TOML has no null, so an absent axis has to round-trip as an absent key.

    Serializing it as `0` would be the strictest cap in the app, arrived at by a
    serializer rather than by the operator.
    """
    path = tmp_path / "config.toml"
    config = load_config(path)
    update_config(config, {"scan_timeline_run_budget": {"tokens": 900, "mode": "tokens"}})
    written = path.read_text(encoding="utf-8")
    assert "scan_timeline_run_budget" in written
    assert load_config(path).scan_timeline_run_budget == Budget(900, None, "tokens")


def test_the_schema_23_uplift_still_applies_through_the_new_shape(tmp_path: Path) -> None:
    """Two migrations now compose, and the older one must still win where it applies.

    Schema 23 raised caps that were still at their untouched schema-22 values.
    That decision is about the pre-`Budget` scalars, so it has to be made while
    they are still visible - and a value the operator chose has to survive both.
    """
    config = _legacy_config(
        tmp_path,
        "automation_daily_token_budget = 200000\n"  # untouched schema-22 default
        "scan_timeline_run_token_budget = 123456\n",  # deliberate
        schema=22,
    )
    assert (
        config.automation_daily_budget.tokens
        == BUDGET_FIELDS["automation_daily_budget"].default.tokens
    )
    assert config.scan_timeline_run_budget.tokens == 123_456


def test_a_budget_out_of_range_is_refused_with_the_axis_named(tmp_path: Path) -> None:
    config = load_config(tmp_path / "config.toml")
    with pytest.raises(ValueError) as caught:
        update_config(config, {"assistant_daily_budget": {"usd": 10_000.0, "mode": "usd"}})
    assert "assistant_daily_budget.usd" in caught.value.args[0]


def test_a_budget_survives_the_asdict_round_trip_update_config_performs(
    tmp_path: Path,
) -> None:
    """`Config(**asdict(other))` flattens nested dataclasses to plain dicts.

    Without coercion in `__post_init__` every enforcement site would be handed a
    mapping where it expected a `Budget`, and `enforces_usd` on a dict is an
    `AttributeError` at the moment a cap was about to be checked.
    """
    from dataclasses import asdict

    original = load_config(tmp_path / "config.toml")
    clone = Config(**{**asdict(original), "config_path": original.config_path})
    assert isinstance(clone.automation_daily_budget, Budget)
    assert clone.automation_daily_budget == original.automation_daily_budget


# ------------------------------------------------------------ unmeasured cost


def test_a_call_with_no_reported_cost_is_unpriced_rather_than_free() -> None:
    """`cost_blind` is what stops a dollar cap claiming a bound it does not have."""
    spend = {"tokens": 10, "cost_usd": 0.0, "unpriced_calls": 4}
    verdict = budget.spent_out(Budget(usd=1.0, mode="usd"), spend, label="x")
    assert verdict.exhausted is False
    assert verdict.cost_blind is True
    assert verdict.unpriced_calls == 4
    assert verdict.note == budget.COST_BLIND_NOTE


def test_the_token_axis_is_the_backstop_when_cost_cannot_be_measured() -> None:
    """`either` is the honest configuration against an endpoint with no prices.

    The dollar half never moves - there is nothing to add to it - so the cap
    that actually stops the feature is the token one, and it still does.
    """
    spend = {"tokens": 5_000, "cost_usd": 0.0, "unpriced_calls": 12}
    verdict = budget.spent_out(Budget(tokens=1_000, usd=50.0, mode="either"), spend, label="x")
    assert verdict.axis == "tokens"
    assert verdict.cost_blind is True


def test_a_priced_window_is_never_reported_as_blind() -> None:
    spend = {"tokens": 10, "cost_usd": 0.5, "unpriced_calls": 0}
    assert budget.spent_out(Budget(usd=1.0, mode="usd"), spend, label="x").cost_blind is False
    # Nor is a token-only cap, which never consults cost and must not imply it did.
    blind = {"tokens": 10, "cost_usd": 0.0, "unpriced_calls": 3}
    assert budget.spent_out(Budget(tokens=100, mode="tokens"), blind, label="x").cost_blind is False
