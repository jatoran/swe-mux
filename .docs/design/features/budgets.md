# Spending budgets

Every model-cost ceiling in the install has one shape, one editor, and one enforcement path.

Implementation: `src/swe_mux/budget.py` (the shape and the arithmetic), `src/swe_mux/config.py`
(`BudgetSpec` / `BUDGET_SPECS`, validation, migration), `src/swe_mux/automation_store.py`
(the ledger every check reads), `frontend/src/BudgetControl.tsx` (the shared control).

## The shape

A budget is `{tokens?: int, usd?: float, mode: "tokens" | "usd" | "either"}`.

The mode names which axes are **enforced**:

| mode | tokens | dollars | trips when |
| --- | --- | --- | --- |
| `tokens` | enforced | remembered, ignored | the token figure is reached |
| `usd` | remembered, ignored | enforced | the dollar figure is reached |
| `either` | enforced | enforced | whichever is reached first |

Two rules keep the mode from disagreeing with the values:

- **The mode's axes are required.** A budget in `usd` mode always carries a dollar figure, and
  one in `either` mode always carries both. A cap claiming to enforce an axis it has no figure
  for would do nothing while looking enforced.
- **The other axis may still hold a value, and is never consulted.** Switching modes in the
  control keeps the operator's number instead of discarding it, and the control marks the
  unenforced field as unenforced rather than hiding or disabling it.

`None` and `0` are different. `None` is no figure; `0` is a total ceiling and the strictest cap
available. TOML has no null, so an unset axis is serialized as an **absent key** rather than as
zero.

## The inventory

Eight caps, each declared once in `BUDGET_SPECS` with its bounds and the pre-`Budget` settings it
replaced.

| Config field | Bounds it enforces | Default mode | Edited in |
| --- | --- | --- | --- |
| `automation_daily_budget` | every automation, per UTC day | `either` | Automation → Global policy |
| `automation_rule_daily_budget` | one automation rule, per UTC day | `either` | Automation → Global policy |
| `project_card_daily_budget` | Project context card rebuilds | `usd` | Automation → Global policy |
| `scan_timeline_daily_budget` | scan timeline, per UTC day | `either` | Automation → Global policy |
| `scan_timeline_run_budget` | scan timeline, per conversation | `tokens` | Automation → Global policy |
| `attention_narration_daily_budget` | model narration on ranked items | `usd` | Automation → Global policy |
| `tts_daily_budget` | read-aloud summaries | `usd` | Settings → Voice |
| `assistant_daily_budget` | the Mux assistant | `usd` | Settings → Assistant |

**Rate limits are not budgets and are deliberately absent.** `automation_hourly_call_cap`,
`agent_message_hourly_budget`, `attention_daily_interrupt_budget`, `land_hourly_budget`, and their
siblings count *acts*. They never read the spend ledger, and forcing them into a tokens-or-dollars
choice would ask the operator to denominate something that has no price. Per-call ceilings
(`automation_max_output_tokens`, `assistant_max_output_tokens`, `tts_summary_max_tokens`) are
absent for the same reason: they bound one request's size rather than a period's spend.

## Enforcement

Both checks read `AutomationStore.spend()`, which sums the same ledger rows the Automation
dashboard's spend view draws, so a refusal and the figure beside it can never disagree.

- `budget.spent_out(cap, spend, label=...)` - **inclusive** (`>=`). "The money is already gone."
- `budget.would_exceed(cap, spend, label=..., tokens=..., usd=...)` - **strict** (`>`). Preflight
  against a *conservative maximum* for the pending call, so landing exactly on the ceiling is
  allowed; `spent_out` is what refuses the next one.

Both return a `BudgetVerdict` carrying the axis that tripped and a whole sentence to render.
`phrasing` chooses between the two sentences: a refusal a human reads in a drawer says the budget
is exhausted, and a refusal in an automation error says the preflight estimate exceeded it,
because there the difference between "spent" and "would overspend" tells the reader whether a
smaller slice would have gone through.

Tokens are checked before dollars, so an `either` budget over on both axes names the token one.

## Unmeasurable cost

A dollar cap can only count dollars somebody reported. OpenRouter reports them on every
completion. A bring-your-own OpenAI-compatible endpoint (`automation.md`,
`src/swe_mux/llm_endpoint.py`) usually reports none, and an absent `usage.cost` means **unknown,
never zero**.

What the system does, stated rather than implied:

- The ledger records the distinction. `add_spend(cost_usd=None)` writes `cost_known = 0` and
  leaves the stored figure at 0, so every existing `SUM(cost_usd)` keeps meaning "the cost we
  actually know about". `spend()` and `spend_breakdown()` carry `unpriced_calls` beside it.
- The dollar axis counts reported cost and nothing else. It does not guess, and it does not
  refuse the call. Failing closed was rejected: a model on the operator's own machine has no
  bill, and refusing would switch off every local-endpoint install for a cost that does not
  exist.
- It therefore **cannot bind** against an endpoint that reports no cost at all. Three surfaces
  say so: `BudgetControl` warns while the dollar axis is selected and the configured provider's
  `reports_cost` is false; `BudgetVerdict.cost_blind` carries the sentence wherever a verdict is
  rendered; and every dollar total drawn from a window containing unpriced calls is prefixed with
  a floor marker and the count.
- **`either` is the honest configuration there.** The token axis still counts every token the
  provider reported, and it is the backstop that binds.

The scan timeline is the one place a *missing* cost was previously substituted with a catalog
estimate. That stand-in survives for OpenRouter, where the model is in the catalog and the figure
is bounded by the prices the preflight already trusted. It is withheld from a custom endpoint,
where the model is in no catalog and the "estimate" would be the bare fallback constant - a number
nobody measured, about a server that may bill nothing.

## Migration

**A config written by the previous build enforces exactly what it enforced before.** Each cap maps
onto the mode matching the unit the pre-`Budget` code compared:

- The automation ceilings and the scan-timeline daily budget checked tokens **and** dollars →
  `either`.
- The scan-timeline run budget checked tokens only → `tokens`.
- The assistant, read-aloud summary, Project card, and attention-narration caps checked dollars
  only → `usd`.

The case a naive migration loses: a config naming only one half of a pair still had the other half
enforced, at the dataclass default, by code that never asked whether the file mentioned it. Each
`BudgetSpec.default` carries that figure, so the absent half is filled from it rather than left
unset. Nothing widens on upgrade.

Schema 23's uplift of untouched schema-22 caps still applies and is made while the legacy scalars
are still visible, so the two migrations compose and a deliberately lowered cap survives both.

`tests/test_budget_shape.py` pins all of this against a table of what the old code compared,
written out longhand rather than derived from `BUDGET_SPECS` - a test that derives its expectation
from the thing under test proves only that the code agrees with itself.

## The control

`BudgetControl` is the single editor. It draws the mode first, because the mode decides whether
the figures under it mean anything, then both axes with the unenforced one dimmed and labelled.
Switching into a mode seeds any axis it starts enforcing from that control's own ceiling, so a
mode change can never produce a budget the daemon rejects for a field the operator never saw.

`frontend/test/budgetControl.test.ts` asserts that every cap in the inventory reaches it and that
no retired scalar field is still edited as a bare number box.

Its layout is three chips on one row and then the two figures side by side, at every width.
The pair is one field - what stops this, counted two ways - so stacking the axes on a phone put
the dollar figure under the fold directly below the chips that were choosing between them, and
bought nothing: two columns hold down to a 320px viewport at roughly 130px an axis, wider than
any figure a cap carries.
What a phone actually needs is a tap target, so a coarse pointer gets a 40px chip; the whole chip
is the label, so that is a 40px hit area for an 18px dot.
The axes start their rows at the top rather than stretching, because only the unenforced one
carries the "kept, not enforced" note and a stretched column put its input below its neighbour's.

`frontend/test/renderer/voice-settings.spec.ts` measures all of it on a 390px viewport: the dot is
`--check-size` square, no chip clips its own word, and the two axes share a row and a top edge.
The clipping assertion is the one that matters, because the defect it replaces passed every
structural check - the chips were one row and under 40px tall, with no room left for their text.
