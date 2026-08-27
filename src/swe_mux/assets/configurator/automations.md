# Automations, the enablement DAG, and spending

This is the guide to read before telling anyone that an analysis surface is broken.

## Substrate and consumers

Automations come in two kinds.

**Substrate** captures facts. It never acts and, with one exception, never spends:
the raw transcript store, deterministic fact capture, the code-structure graph.

**Consumers** are assembled from substrate. They are the things with visible output -
provenance, declared-vs-verified status, loop detection, doc debt, attention ranking,
session control, the land queue.

Every automation is **per-Project opt-in**. Not install-wide. A Project that did not
opt in runs nothing, and that is a literal statement rather than a default.

Above the opt-ins sits an **install-wide ceiling**: an automation the operator
disallowed globally (`automation_global_allow`, plus dedicated switches for the scan
timeline, scheduled runs, and the land queue) is off in every Project along with
everything that depends on it, however the Project's own map reads. A Project's
settings report such an id under `globally_disabled` - the fix is the Automation
workspace's Policy matrix, not a Project toggle and not a grant (grants refuse it
with `automation_globally_disabled`).

## The rule that produces most of the confusion

**A consumer only does anything when the full transitive closure of its dependencies
is also opted in.**

Switch on a consumer without its substrate and it is *inert*: no error, no output,
no complaint. It looks exactly like a broken feature.

`configurator_capabilities` reports, for every automation:

- `requires` - the declared edges
- `closure` - everything that must actually be on, transitively
- `implemented` - false for reserved ids with nothing behind them yet
- `spends` - whether switching it on can bill the operator
- `needs_llm` - whether it needs a model at all
- `recommended` - whether it is in the free starting set new Projects get

Read `closure`, not `requires`. That is the whole diagnosis for "I turned it on and
nothing happened".

## `spends` and `needs_llm` are not the same question

They coincide today, but they answer different things and the difference is real.

`spends` is a **disclosure**: this can bill you.
`needs_llm` is a **predicate**: this has a dependency outside the graph, and the
switch is inert until a model provider is proven.

A bring-your-own local endpoint is exactly where they come apart: a model running on
the operator's own machine needs the provider and costs nothing.

When advising, say which one applies. "This costs nothing, it just needs turning on"
and "this will spend against your OpenRouter key" are different recommendations and
should never be delivered in the same tone.

## Where the controls are

- **The Automation dashboard** is the one workspace, three tabs. Its **Policy** tab
  holds the matrix - every automation's install-wide Global switch beside each
  Project's opt-in, the starting-set presets, and (behind disclosures) the
  install-wide budgets/bounds and the rule corpus with the rules.toml editor.
  **Usage** is the spend breakdown; **Activity** is the attention inbox mirror,
  the firing trail, learned fixes, and diagnostics.
- **Settings → Automation** is a status portal that opens the workspace; it edits
  nothing.
- **Manage projects** keeps the agent-authority table and links to the matrix for
  opt-ins.

Every model setting is edited in Settings → Accounts → Models; each feature keeps a
read-only row naming its resolved model with a link there.

## Diagnosing "it fired but nothing happened"

A missing spend row does **not** prove an automation never ran.
The observer-call records are the evidence for whether it was invoked; spend only
appears if a billable call completed. An automation that was invoked and failed
schema validation produces calls and no spend, and reads identically to one that
never fired.

So the order is: check the opt-in and its closure, then check whether the observer
was called, then check spend. Do not start at spend.

## Budgets

Spending caps validate against bounds declared beside them, so every budget has a
range and a surface that advertises it.
An **unset** axis is absent, not zero - zero would be the strictest possible cap
arrived at by a serializer rather than by the operator, which is why the config file
writes an absent key instead.

If someone wants to try a spending automation cautiously, setting a small budget
first is the right advice, and it is a normal install-wide setting you can apply.
