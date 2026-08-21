"""One shape for every spending cap in the install, and one place that enforces it.

Before this module every cap invented its own units. The automation ceilings
carried a token figure *and* a dollar figure and enforced both; the scan
timeline's run budget was tokens only; the assistant, read-aloud summaries, the
Project card, and attention narration were dollars only. Nothing said which axis
a given feature bound on, so the answer had to be read out of the enforcement
code, and a feature that wanted the other unit had no way to ask for it.

`Budget` is that shape: an optional token figure, an optional dollar figure, and
a `mode` naming which of them is *enforced*.

- `tokens` - only the token axis binds. The dollar figure, if present, is
  remembered and ignored.
- `usd` - only the dollar axis binds.
- `either` - both bind, and whichever is hit first stops the call.

The mode is what the reader sees, so it must never disagree with the values:
`validate` requires the axes the mode names. A budget in `either` mode therefore
always carries both numbers, and one in `usd` mode always carries a dollar
figure. The *other* axis is still allowed to hold a value so that switching
modes in the UI does not throw the operator's number away, and the control that
renders it says plainly that an unenforced figure is not enforced.

Migration is the property this module was written around: `config.py` maps each
pre-existing cap onto the mode matching the unit it already enforced, so a
config written by the previous build enforces exactly what it enforced before.
The automation and scan-timeline ceilings enforced *both* units, which is
`either`; the four dollar caps become `usd`; the scan run budget becomes
`tokens`. Nothing is widened by the upgrade, and nothing that used to bind
stops binding.

## When cost is unmeasurable

A dollar cap can only count dollars somebody reported. OpenRouter reports them;
a bring-your-own OpenAI-compatible endpoint (`llm_endpoint.py`) generally does
not, and an absent `usage.cost` means **unknown, never zero**. Writing such a
call into the ledger as `$0.00` would make a dollar cap look enforced while it
silently never approached its limit - the exact failure this codebase refuses
elsewhere ("every signal is observed or absent, never estimated").

So the ledger records unmeasured cost as its own fact (`cost_known = 0`), the
spend rows carry `unpriced_calls`, and this module reports it as
`BudgetVerdict.cost_blind` with a sentence a surface can render verbatim. What
enforcement does with it is stated rather than implied:

- The dollar axis counts the cost that *was* reported and nothing else. It does
  not guess, and it does not refuse the call.
- It therefore **cannot bind** on an endpoint that reports no cost at all, and
  every surface that offers a dollar-only mode says so.
- `either` mode is the honest configuration there, because the token axis still
  counts every token the provider reported and is the backstop that binds.

Refusing to run when cost is unmeasurable was considered and rejected: a model
on the operator's own machine has no bill, and failing closed would switch off
every local-endpoint install for a cost that does not exist.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

__all__ = [
    "AXIS_NOUN",
    "BUDGET_MODES",
    "Budget",
    "BudgetMode",
    "BudgetVerdict",
    "COST_BLIND_NOTE",
    "coerce_budget",
    "gauges",
    "spent_out",
    "validate",
    "would_exceed",
]

#: Which axes a budget enforces. Also the wire vocabulary and the values the
#: shared control offers, so it is a closed set validated on the way in.
BudgetMode = Literal["tokens", "usd", "either"]
BUDGET_MODES: tuple[BudgetMode, ...] = ("tokens", "usd", "either")

BudgetAxis = Literal["tokens", "usd"]

#: The word each axis contributes to a refusal sentence. "token budget" and
#: "dollar budget" are what the existing messages say, and they are what the
#: operator reads in the skip reason, so they live here rather than at each site.
AXIS_NOUN: dict[str, str] = {"tokens": "token", "usd": "dollar"}

COST_BLIND_NOTE = (
    "Some calls in this window reported no cost, so the dollar figure is a floor "
    "rather than the total. A token cap is the axis that can bound them."
)


@dataclass(frozen=True, slots=True)
class Budget:
    """A spending ceiling: how many tokens, how many dollars, and which bind."""

    tokens: int | None = None
    usd: float | None = None
    mode: BudgetMode = "either"

    @property
    def enforces_tokens(self) -> bool:
        return self.mode in {"tokens", "either"} and self.tokens is not None

    @property
    def enforces_usd(self) -> bool:
        return self.mode in {"usd", "either"} and self.usd is not None

    @property
    def enforced_axes(self) -> tuple[BudgetAxis, ...]:
        axes: list[BudgetAxis] = []
        if self.enforces_tokens:
            axes.append("tokens")
        if self.enforces_usd:
            axes.append("usd")
        return tuple(axes)

    def as_dict(self) -> dict[str, Any]:
        """The JSON shape. `None` is preserved: a browser must be able to tell
        "no figure" from "zero", because zero is a real (and total) ceiling."""
        return {"tokens": self.tokens, "usd": self.usd, "mode": self.mode}

    def as_toml_dict(self) -> dict[str, Any]:
        """The TOML shape. TOML has no null, so an absent axis is an absent key."""
        result: dict[str, Any] = {}
        if self.tokens is not None:
            result["tokens"] = int(self.tokens)
        if self.usd is not None:
            result["usd"] = float(self.usd)
        result["mode"] = self.mode
        return result


def coerce_budget(value: Any, *, fallback: Budget) -> Budget:
    """Turn whatever arrived - TOML table, JSON object, `Budget` - into a `Budget`.

    Deliberately tolerant rather than raising: a hand-edited config file and a
    browser round-trip both land here, and `validate` is the thing that refuses.
    A value this cannot read at all falls back to the field's default, which is
    the same tolerance `load_config` already extends to every other setting -
    an unreadable cap must not stop the daemon starting, and the default is
    never looser than what the operator meant.
    """
    if isinstance(value, Budget):
        return value
    if not isinstance(value, Mapping):
        return fallback
    raw_mode = str(value.get("mode") or fallback.mode)
    mode: BudgetMode = raw_mode if raw_mode in BUDGET_MODES else fallback.mode
    tokens = value.get("tokens")
    usd = value.get("usd")
    return Budget(
        tokens=_optional_int(tokens),
        usd=_optional_float(usd),
        mode=mode,
    )


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate(
    budget: Budget,
    *,
    field: str,
    errors: dict[str, str],
    max_tokens: int,
    max_usd: float,
    min_tokens: int = 0,
    min_usd: float = 0.0,
) -> None:
    """Record why this budget cannot be saved, keyed per axis.

    The mode names the axes that are *required*, not merely the ones that are
    read: a budget claiming to enforce dollars with no dollar figure would be a
    cap that silently does nothing, which is the failure shape this whole
    section exists to remove. The unnamed axis is optional and is bounds-checked
    only when it carries a value, so a remembered figure cannot go out of range
    while it waits to be switched back on.
    """
    if budget.mode not in BUDGET_MODES:
        errors[f"{field}.mode"] = f"must be one of {', '.join(BUDGET_MODES)}"
        return
    needs_tokens = budget.mode in {"tokens", "either"}
    needs_usd = budget.mode in {"usd", "either"}
    if needs_tokens and budget.tokens is None:
        errors[f"{field}.tokens"] = f"a token limit is required in {budget.mode} mode"
    if needs_usd and budget.usd is None:
        errors[f"{field}.usd"] = f"a dollar limit is required in {budget.mode} mode"
    if budget.tokens is not None and not min_tokens <= budget.tokens <= max_tokens:
        errors[f"{field}.tokens"] = f"must be between {min_tokens} and {max_tokens}"
    if budget.usd is not None and not min_usd <= budget.usd <= max_usd:
        errors[f"{field}.usd"] = f"must be between {min_usd} and {max_usd}"


@dataclass(frozen=True, slots=True)
class BudgetVerdict:
    """Whether a call may proceed, which axis stopped it, and what to say."""

    exhausted: bool
    axis: str
    """`tokens`, `usd`, or `""` when nothing is exhausted."""

    reason: str
    """A whole sentence, ready to render. Empty when not exhausted."""

    unpriced_calls: int
    """Calls in this window whose cost the provider never reported."""

    cost_blind: bool
    """The dollar axis is enforced and part of the window's cost is unmeasured."""

    @property
    def note(self) -> str:
        return COST_BLIND_NOTE if self.cost_blind else ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "exhausted": self.exhausted,
            "axis": self.axis,
            "reason": self.reason,
            "unpriced_calls": self.unpriced_calls,
            "cost_blind": self.cost_blind,
            "note": self.note,
        }


def _reason(label: str, axis: str, *, preflight: bool) -> str:
    noun = AXIS_NOUN[axis]
    if preflight:
        return f"conservative preflight estimate exceeds {label} {noun} budget"
    return f"{label} {noun} budget is exhausted"


def _verdict(
    budget: Budget,
    spend: Mapping[str, Any],
    *,
    label: str,
    axis: str,
    preflight: bool,
) -> BudgetVerdict:
    unpriced = max(0, int(spend.get("unpriced_calls") or 0))
    return BudgetVerdict(
        exhausted=bool(axis),
        axis=axis,
        reason=_reason(label, axis, preflight=preflight) if axis else "",
        unpriced_calls=unpriced,
        cost_blind=budget.enforces_usd and unpriced > 0,
    )


def spent_out(budget: Budget, spend: Mapping[str, Any], *, label: str) -> BudgetVerdict:
    """Has this budget already been reached? Boundary-inclusive (`>=`).

    Tokens are checked before dollars so that a budget in `either` mode names
    the cheaper-to-explain axis when both are simultaneously over, which is the
    order every call site used before they shared this code.
    """
    axis = ""
    if budget.enforces_tokens and int(spend.get("tokens") or 0) >= int(cast(int, budget.tokens)):
        axis = "tokens"
    elif budget.enforces_usd and float(spend.get("cost_usd") or 0.0) >= float(
        cast(float, budget.usd)
    ):
        axis = "usd"
    return _verdict(budget, spend, label=label, axis=axis, preflight=False)


def would_exceed(
    budget: Budget,
    spend: Mapping[str, Any],
    *,
    label: str,
    tokens: int = 0,
    usd: float = 0.0,
    phrasing: str = "preflight",
) -> BudgetVerdict:
    """Would one more call of this size cross the budget? Strict (`>`).

    Preflight, not accounting: `tokens` and `usd` are the *conservative maximum*
    the pending call could cost, so the comparison is strict - landing exactly on
    the ceiling is allowed, and `spent_out` is what refuses the call after it.

    `phrasing` chooses the sentence rather than the arithmetic. A refusal a human
    reads in a drawer says the budget is exhausted, because "the estimate exceeds
    it" is a distinction about *this* call that the operator cannot act on; a
    refusal in an automation error says the estimate was the thing that stopped
    it, because there the difference between "spent" and "would overspend" is
    what tells the reader whether a smaller slice would have gone through.
    """
    axis = ""
    if budget.enforces_tokens and int(spend.get("tokens") or 0) + int(tokens) > int(
        cast(int, budget.tokens)
    ):
        axis = "tokens"
    elif budget.enforces_usd and float(spend.get("cost_usd") or 0.0) + float(usd) > float(
        cast(float, budget.usd)
    ):
        axis = "usd"
    return _verdict(budget, spend, label=label, axis=axis, preflight=phrasing == "preflight")


def gauges(
    budget: Budget,
    spend: Mapping[str, Any],
    *,
    id_prefix: str,
    token_label: str,
    usd_label: str,
) -> list[dict[str, Any]]:
    """One row per *enforced* axis, for a surface that draws how close a cap is.

    Only enforced axes appear: a drawer that listed a remembered-but-inactive
    figure beside the ones that can stop the feature would be answering "what is
    my limit" with a number that cannot stop anything, which is the same class of
    lie as a percentage drawn against a fictional denominator.
    """
    rows: list[dict[str, Any]] = []
    if budget.enforces_tokens:
        rows.append(
            {
                "id": f"{id_prefix}_tokens",
                "label": token_label,
                "unit": "tokens",
                "used": int(spend.get("tokens") or 0),
                "limit": int(cast(int, budget.tokens)),
            }
        )
    if budget.enforces_usd:
        rows.append(
            {
                "id": f"{id_prefix}_usd",
                "label": usd_label,
                "unit": "usd",
                "used": float(spend.get("cost_usd") or 0.0),
                "limit": float(cast(float, budget.usd)),
                "unpriced_calls": max(0, int(spend.get("unpriced_calls") or 0)),
            }
        )
    return rows
