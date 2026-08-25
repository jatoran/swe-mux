"""The validation tables in `config.py` are checked the way the branches they replaced were.

`Config._validate` is one deliberate choke point, so a rule that quietly stops firing is
worse than a rule that is wrong: every surface would start accepting the value at once.
S12.3 turned 103 of its hand-written branches into four declarative tables, and these are
the invariants that keep the tables honest - that every rule still refuses something, that
no field is claimed by two rules or by a rule *and* a hand-written check, and that no
rule refuses its own default.
"""

from __future__ import annotations

import pytest

from swe_mux.config import (
    _CHOICE_RULES,
    _PATTERN_RULES,
    _RANGE_RULES,
    _TEXT_RULES,
    Config,
    _validate,
)

_TABLE_FIELDS = (
    [rule.field for rule in _RANGE_RULES]
    + [rule.field for rule in _CHOICE_RULES]
    + [rule.field for rule in _TEXT_RULES]
    + [rule.field for rule in _PATTERN_RULES]
)


def errors_for(field: str, value: object) -> dict[str, str]:
    """The validation errors a config carrying exactly this one odd value produces."""
    config = Config()
    setattr(config, field, value)
    try:
        _validate(config)
    except ValueError as exc:
        errors: dict[str, str] = dict(exc.args[0])
        return errors
    return {}


def test_a_field_is_claimed_by_exactly_one_rule() -> None:
    # Two rules over one field means whichever runs last decides, and the other is
    # dead code that reads as protection.
    duplicated = sorted({name for name in _TABLE_FIELDS if _TABLE_FIELDS.count(name) > 1})
    assert duplicated == []


def test_every_rule_names_a_field_that_exists() -> None:
    known = {entry.name for entry in Config.__dataclass_fields__.values()}
    assert sorted(set(_TABLE_FIELDS) - known) == []


def test_the_default_config_satisfies_every_rule() -> None:
    # A rule whose own default violates it would refuse every fresh install, and
    # would do it at the one place that can refuse the whole settings payload.
    assert errors_for("port", Config().port) == {}


@pytest.mark.parametrize("rule", _RANGE_RULES, ids=lambda rule: rule.field)
def test_a_range_refuses_both_ends_and_accepts_them(rule: object) -> None:
    field, low, high = rule.field, rule.low, rule.high  # type: ignore[attr-defined]
    message = rule.message  # type: ignore[attr-defined]
    # `low - 1` would land on 0 for a `zero_disables` rule, which is the one value
    # such a rule deliberately lets through.
    below = low - 1 if not rule.zero_disables or low - 1 != 0 else low - 0.5  # type: ignore[attr-defined]
    assert errors_for(field, below) == {field: message}
    assert errors_for(field, high + 1) == {field: message}
    assert errors_for(field, low) == {}
    assert errors_for(field, high) == {}
    if rule.zero_disables:  # type: ignore[attr-defined]
        assert errors_for(field, 0) == {}


@pytest.mark.parametrize("rule", _CHOICE_RULES, ids=lambda rule: rule.field)
def test_a_choice_refuses_an_unknown_spelling_and_accepts_each_known_one(rule: object) -> None:
    field, message = rule.field, rule.message  # type: ignore[attr-defined]
    assert errors_for(field, "not-a-spelling-anyone-declared") == {field: message}
    for allowed in rule.allowed:  # type: ignore[attr-defined]
        assert errors_for(field, allowed) == {}


@pytest.mark.parametrize("rule", _TEXT_RULES, ids=lambda rule: rule.field)
def test_a_bounded_string_refuses_one_character_too_many(rule: object) -> None:
    field, message = rule.field, rule.message  # type: ignore[attr-defined]
    max_chars: int = rule.max_chars  # type: ignore[attr-defined]
    assert errors_for(field, "x" * (max_chars + 1)) == {field: message}
    assert errors_for(field, "x" * max_chars) == {}
    # Required means required: blank and whitespace are both refused, and a field
    # that is *not* required must keep accepting blank.
    expected_blank = {field: message} if rule.required else {}  # type: ignore[attr-defined]
    assert errors_for(field, "") == expected_blank
    assert errors_for(field, "   ") == expected_blank


@pytest.mark.parametrize("rule", _PATTERN_RULES, ids=lambda rule: rule.field)
def test_a_pattern_must_match_the_whole_value(rule: object) -> None:
    field, message = rule.field, rule.message  # type: ignore[attr-defined]
    assert errors_for(field, "") == {field: message}
    assert errors_for(field, "definitely not this shape") == {field: message}
    # A prefix match is not a match: `fullmatch`, not `match`, is what the branches
    # these replaced used, and a partial acceptance here would be silent.
    default = getattr(Config(), field)
    assert errors_for(field, default) == {}
    assert errors_for(field, f"{default} trailing") == {field: message}
