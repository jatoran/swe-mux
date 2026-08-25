# Configurator agent

## What it is

A button that opens an ordinary agent session pointed at swe-mux itself.

The session runs in the operator's own harness, in a registered Project, and starts with a prompt naming this machine's actual state.
It holds four tools no other session is shown: a generated inventory of this install, the shipped configuration guides, the health report, and one validated settings write.

It is not a help page and it is not an assistant mode.
It is a normal session in a normal pane: it appears in the sidebar, it can be interrupted, and it can be closed.

## Why an agent rather than documentation

A new operator meets swe-mux as a large surface with most of its interesting parts switched off on purpose.
Documentation can describe the general case; only something that can *read the install* can answer "why is this panel empty on my machine", and that question is the single most common one the design produces.

The three properties that make it worth building are what the rest of this document is about: everything structural is generated, the prose ships in the build, and the authority is explicit and asymmetric.

## Everything structural is generated at read time

`configurator_capabilities` assembles its answer from live registries on every call:

| Section | Derived from |
|---|---|
| `settings` | the `Config` dataclass, `RESTART_FIELDS`, and `_validate` itself |
| `project_settings` | `PROJECT_CONFIG_FIELDS` and `FORBIDDEN_PROJECT_FIELDS` |
| `harnesses` | `public_harness_registry`, with this machine's detection folded in |
| `automations` | the enablement DAG, with each entry's transitive closure computed |
| `mcp_tools` | the closed MCP contract |
| `install` | the running process: mode, source checkout, data dir, config path |

Nothing here restates a fact that lives somewhere else.
A hand-written mirror of a registry is a second registry, and the copy is what drifts.
Adding a field to `Config` adds a row here with no second edit, which is the whole design constraint.

**The per-field constraints are the sharpest case.**
Rather than transcribing "must be DEBUG, INFO, WARNING, or ERROR" into a table beside the validator, `settings_catalog` asks the validator: it sets a value that cannot be legal on a detached candidate, runs `_validate`, and keeps the sentence that comes back.
The validator is the authority for writes, so quoting it is the only description of a constraint that cannot be wrong.
This survived S12.3, which moved the mechanical families - a numeric range, a fixed set of spellings, a bounded string, a whole-value pattern - out of `_validate`'s branches and into four declarative tables beside it (`_RANGE_RULES`, `_CHOICE_RULES`, `_TEXT_RULES`, `_PATTERN_RULES`).
The probe still asks the validator and still gets the same sentences: the tables carry the exact strings the branches produced, and a differential run over 505 probe values found no divergence.
A field's constraint is now editable in one line rather than three, and `tests/test_config_validation_tables.py` holds the invariants that keep the tables honest - one rule per field, every rule refuses something, no rule refuses its own default.

Three consequences follow and are all deliberate:

- **A field with no check reports no constraint.** Correctly - there is nothing to say beyond its type, and inventing a rule would make an agent refuse a legal write.
- **A validator that raises rather than collecting loses that one row's hint.** An unguarded comparison against the sentinel is a type error; losing one hint is the right trade against a catalog that cannot be built at all.
- **It costs one `_validate` pass per field**, so tens of milliseconds rather than microseconds. This is read on demand by an agent about to change something, not on a hot path, and the alternative is a table that is wrong.

The probe mutates a *candidate* built exactly the way `update_config` builds its own, restoring each field in a `finally`.
`tests/test_configurator.py` asserts the live config is untouched afterwards, because a restore that missed would corrupt the running install with a NUL-prefixed sentinel nothing else could produce.

### Redaction is a forward guard, not a current need

No `Config` field is a credential today - swe-mux keeps its secrets in the secret store.
The catalog redacts credential-shaped names anyway, to `<set>` or `<unset>`, because the failure it prevents is a credential field being added later and reaching a transcript before anyone notices.

The pattern is anchored on **whole singular words**. A loose substring match on "token" swallowed all nine `*_max_output_tokens` ceilings, which is not a harmless excess of caution: it hides a number the operator is asking about, and tells the agent that a budget is a secret.

## Prose ships as an asset

The guides live in `src/swe_mux/assets/configurator/*.md`.

That directory, and not `.docs/`, because it is in both distribution paths - the wheel's `artifacts` list and the PyInstaller spec's `datas`.
A prompt that told an agent to read `.docs/design/features/ui.md` would work on a maintainer's machine and fail silently for every user of the frozen app, which is exactly the audience this feature exists for.

The guide set is closed (`GUIDES` in `configurator.py`) rather than a directory glob: a file in the bundle with no title and summary is undiscoverable, and an entry with no file is a dead link.
`tests/test_configurator.py` holds the two in step and fails when either drifts.

A listed guide whose *file* is missing raises rather than returning empty text, because "reads blank in the frozen app, fine from source" is the packaging fault this whole section is designed around.

The nine guides: `orientation`, `settings`, `harnesses`, `rail-and-actions`, `automations`, `remote`, `worktrees`, `diagnostics`, `modifying-swe-mux`.

## Three settings locations, three writes

swe-mux keeps settings in three places, and the configurator can now write all
three. Each goes through the same validated path the corresponding UI surface
uses, and each has a different validator because they have different blast radii.

| Location | Read | Write | Validated by |
|---|---|---|---|
| Install-wide `config.toml` | `configurator_capabilities` (`sections: ["settings"]`) | `configurator_apply_settings` | `update_config` / `_validate` |
| Per-device `settings.json` | `configurator_device_settings` | `configurator_edit_device_settings` | nothing can - see below |
| Per-Project `.swe-mux/config.toml` | `configurator_project_settings` | `configurator_apply_project_settings` | `parse_project_config`, closed field set |

Three write tools rather than one with a `location` argument, deliberately:
collapsing them would make the committed, repository-shared write the same call
as the local one.

### The device-settings write takes operations, not documents

Five of the seven device-settings domains are stored **opaquely** - the browser
owns their schema and `settings_store` checks only "is a dict" and "≤ 1 MiB". That
was fine while the browser was the only editor. It is not fine for a second one.

Whole-document replacement is the obvious way to give an agent this power and it
is the wrong one. A rail blob is twelve kilobytes of nested identifiers; asking a
model to resend all of it in order to delete four strings makes every byte it did
not mean to touch part of the blast radius, and the store cannot catch the damage
because it cannot tell a valid rail from a mangled one. The failure is silent and
total: the browser normalizes the wreckage rather than rejecting it.

So a write names what to change and nothing else (`settings_patch.py`). Four
operations - `set`, `remove`, `remove_values`, `insert` - over JSON Pointer paths
with one addition: `[key=value]` selects the element of an array whose field
matches, so a row is named by its own id rather than by position.

`remove_values` carries most of the ergonomic weight, and its existence is a
correctness argument rather than a convenience one: four positional deletes
composed against one reading remove the wrong things after the first, because each
removal shifts the indices of everything after it. Naming the values is
order-independent and cannot be thrown off.

Three guards, each answering a different failure:

- **The operations themselves.** Everything not named is untouched by
  construction - a property of the shape of the request, not of the care taken in
  composing it.
- **A digest precondition.** The store has no revision and the browser writes
  whole domains, so an agent that read a rail, thought, and wrote back would
  silently discard a drag the operator did in between. Presenting the digest that
  was read makes that a refusal.
- **A backup.** Nothing here can promise "this write is correct"; the honest
  guarantee is "the previous document is still on disk". `config.toml` already
  earned that through `save_config(backup=True)`; `settings.json` had not.

A batch is all-or-nothing, enforced by working on a private deep copy: half-edited
is the worst outcome available on an opaque document, because nothing downstream
can tell it from an intended one.

**The write emits `settings_changed`.** That event is what makes every attached
browser refetch its device-settings cache and repaint; without it the write lands
on disk and the rail on screen does not move, which reads to the operator as a
change that did not happen.

### The command rail, and the global-first rule

The rail is where this power will mostly be used, and it has two traps that cost
real money the first time out.

**Storage.** One document, under the **desktop** profile, carrying *both* device
layouts inside it (`deviceSettings.ts`, `RAIL_PROFILE`). "Edit the mobile rail"
therefore means `profile=desktop domain=commandRail` at a path under
`/layouts/mobile`. A `commandRail` document written under the mobile profile is
valid, stored, and read by nothing. The frontend's reason for the single bucket is
good - the catalog is shared while the arrangements are not, so splitting them
would make a save two writes with a window where one layout names a command the
catalog has not got yet - but nothing in the daemon recorded it, so both the read
tool and the `rail-and-actions` guide now state it.

**Scope.** An unqualified request means the **global** rail. Most Projects have no
override at all, and the narrow scope is reached only when the operator named a
Project or the thing being changed exists only there.

That rule is stated in the seed prompt, the tool description, the guide, and in
the read tool's own answer, because its failure mode is not a refusal - it is a
confident, specific, wrong change.

### `rail_projection`: reading an opaque document without guessing

`configurator_device_settings` with `domain: "commandRail"` returns a derived
reading alongside the raw document: rows with their items' labels, the exact path
an edit would name, and **every per-Project override resolved to its Project
name** with the caller's own marked.

That last part is not decoration. It is the fix for a measured failure. On
2026-08-24 a configurator launched into `cmr-capture-manager` read the rail blob
by hand, found the install's single project override, and warned that "this
project (CMR Capture Manager) has a per-project rail delta" - about a button
belonging to `swe-mux`. The read was otherwise byte-exact; the agent simply had no
way to know which of twenty-four Projects it was standing in, and one override in
a blob keyed by bare UUIDs invites exactly that inference.

The projection is a *reading*, never a second writer: writes stay path-scoped
operations that need no schema, so the projection can degrade (it reports
`readable: false`, or an empty `layouts`, rather than raising) without ever making
a write unsafe.

## Session identity

Every configurator answer carries where the caller is standing:
`install.session` on the manifest, `is_this_session_project` on each entry of
`projects[]`, and the same marking inside the rail projection. The project tools
default to it.

The dispatcher attaches it from `caller.record` rather than accepting it as an
argument, because an argument the model has to remember is one it will omit
exactly when it matters.

This is the single highest-value field in the whole surface. A configurator
launched into a foreign Project can read the entire install and, without it,
cannot attribute a single per-Project fact.

## Cost

The first version answered a question about twelve strings in 195 KB of transcript
and 61k input tokens, because the agent had to find and parse
`~/.mux/settings.json` itself and re-read a 56 KB inventory on every turn of the
loop.

Two changes address that directly. `configurator_device_settings` serves the store
structurally, and `configurator_capabilities` takes `sections` - **omitting the
197-row settings catalog by default**, which is 45 KB of the manifest's 56 KB - plus
a `settings_query` substring filter. A first call almost never wants all 197 rows;
it wants to know what kind of thing it is looking at.

## Authority

### The gate

The configurator tools are listed only to a session whose `SessionRecord.configurator` is true, and dispatch applies the same check.
Both, deliberately: a tool advertised and then refused teaches an agent that the surface lies to it, and the refusal arrives after it has already planned around the capability.

A guessed tool name answers **"unknown tool"** rather than "not permitted".
To a session that was never shown it that is the literal truth, and naming a capability that exists elsewhere invites an agent to look for a way to reach it.

**The marker is not a spawn field.** It is set by `POST /api/configurator/launch` and by nothing else.
If it were on `SpawnRequest`, the `request_spawn` MCP tool would be a way for any agent to ask for a session that can rewrite this install's settings, and the human approving that request would have no reason to read it as anything but an ordinary spawn.
`tests/test_configurator_endpoint.py` parses the composed launch body through `SpawnRequest.parse` to keep that true.

It is carried through `snapshot()`/`from_snapshot()` because sessions outlive the daemon: a configurator adopted after a session-preserving restart that came back without its tools would look like the feature silently breaking.

### The write

`configurator_apply_settings` runs `update_config` - the same call `PATCH /api/config` makes - then `_apply_runtime_config`, then emits `configuration_changed` with `source="configurator"`.

So it grants no authority the Settings panel does not already have, cannot skip a validation by arriving through MCP, and cannot half-apply: `_validate` runs over the whole candidate before anything is written.

A refusal comes back as a **result** naming the offending fields, not as an exception, for the same reason the prompt queue's refusals do: the agent needs to know whether to adapt the value or stop asking, and an error string it has to parse tells it neither.

The result reports `hot_applied` and `restart_required` separately, and the seed prompt requires the agent to say which it just caused.
A restart-required change reported as done, that then does not appear to work, reads as the setting being broken.

### The permission allowlist

The configurator **reads** are in `claude_read_permissions()`; the **write** is not.

Pre-allowing the reads grants nothing - a permission rule only decides whether the CLI prompts, and the daemon still refuses a caller that is not a configurator - and it spares the one session that can call them an approval dialog in front of every "what is this setting called" lookup.
A settings change is exactly the thing a human should see before it happens.

### Code changes are not a tool

Editing swe-mux's own source needs a source checkout, needs a rebuild the agent must not run unattended, and one class of it reaps every live session.
None of that is expressible as a bounded tool call, so it is not one.

What the feature does instead is tell the truth about the install: `install.mode` is `source`, `frozen`, or `installed`, and the seed prompt carries a different paragraph for each.
On a frozen app it says plainly that there is no source checkout and that an edit to anything that looks like swe-mux source is not what this app runs - the single most expensive misunderstanding in this codebase.
The `modifying-swe-mux` guide carries the rest, including the supervisor reap.

## The seed prompt

Composed at launch, seeded rather than staged.

**Seeded** because the human pressed a button whose label says it starts a conversation about their install, so the opening turn is the thing they asked for; leaving it unsent in a composer answers a different press.
Nothing the opening turn says changes anything - the one write is a separate, explicit call.

**Small** (under 8 KB, asserted) because it introduces the material rather than containing it.
Inlining the inventory would spend the first turn on text the agent can fetch on demand, and would freeze a copy of it into a transcript that outlives the settings it describes.

What it carries instead is the part no tool call supplies: who the agent is talking to, what authority it holds, and the handful of this-machine facts that decide whether its first suggestion is even applicable - install mode, version, config path, Project count, the harness table, and a one-sentence health summary.

The health line names a count and at most three titles, ranked by severity rather than by list position, and tells the agent to call `configurator_diagnostics` for current detail.

## Resolution: which agent, which Project

**Which agent** is `resolve_default_harness`, given preferences in narrowing order and the machine's available agents.
The first preference that is both a registered agent and available wins; failing all of them, the first available agent does, because "you have exactly one agent installed" is by far the most common shape and asking that operator to choose is asking nothing.

`shell` is skipped rather than rejected: `default_backend` legitimately holds it, and it is a valid answer to a different question. A shell cannot receive a seeded prompt, so falling back to one would turn a missing-harness problem into a launch that succeeds and does nothing.

`None` means there is no agent to launch, and the launcher surfaces "install or enable one".

**`default_harness`** is a new install-wide setting for exactly this question, kept separate from `default_backend` for the reason above. Empty means resolve by detection.

**Which Project**: an explicit ask, then the Project that *is* this swe-mux checkout when the daemon runs from source (so a maintainer's configurator lands where the code is), then the first Project.
No Project at all refuses with `no_project` rather than inventing one.

The two refusals are separate codes because they are different problems: a missing CLI is a prerequisite to install, a missing Project is a step to take in the app, and telling the operator the wrong one sends them somewhere that cannot help.

## Surfaces

- **Sidebar footer**, beside the alert bell, with a twin in the collapsed rail. This is the primary entry point.
- **Settings → Diagnostics**, above Export diagnostics - the same errand one step earlier.
- **Command palette**, `configurator.open`.
- **The guided tutorial's second-to-last step**, anchored on the footer control, so the tour ends by pointing at where help lives afterwards.
- **A harness chooser** on right-click / shift-click / alt-click, and only when more than one agent is available. A plain press launches the default, because one press with no decision is the whole value of the control.

### The footer rule, restated

The sidebar footer's rule was "app-wide switches, not navigation", which is why the gear that once sat beside the bell was removed: Settings is one click inside the menu, and a second permanent door to it saved nothing.

The configurator button is not a door. It starts an agent session about this install, and the footer is where a control belonging to the whole app rather than to the tree above it goes.
The comment in `App.tsx` carries the restated rule so the next reader does not re-derive the old one and delete this.

### Disabled, not hidden

When a prerequisite is missing the control is dimmed with the reason in its title, rather than removed.
A control that vanishes teaches nothing; one that explains itself is how the operator finds out an agent CLI is what they are missing.

An **unanswered** options request leaves the button enabled with a neutral label. A launcher that greys itself out because a status request is in flight reads as broken, and the daemon refuses cleanly on the press anyway.

## Routes

| Route | Answers |
|---|---|
| `GET /api/configurator/options` | Available agent harnesses, the resolved default, the configured default, install mode, source checkout, Project count. Read once when a surface holding the button opens; detection runs off the loop and includes CLI version probes. |
| `POST /api/configurator/launch` | `{project_id?, harness?}` → the session record snapshot, `201`. `409` with `no_harness` or `no_project`. |

The launch returns exactly the body `POST /api/sessions` answers with.
The browser places a new session into a pane itself, and a launcher returning a shape of its own would need a second placement path that drifts from the one every other launch uses.
The record already carries `configurator: true`, so the caller can tell what it got without a wrapper.

An empty `harness` is omitted from the request rather than sent blank: the daemon re-resolves against live detection, so a CLI uninstalled since the options request cannot produce a launch that half-succeeds.

## Key files

- The module: generated catalogs, the rail projection, guides, install shape, seed prompt, service: `src/swe_mux/configurator.py`
- Path-scoped edits to a schema this process does not hold: `src/swe_mux/settings_patch.py`
- The device-settings store's agent-facing door (digest, backup, ops): `src/swe_mux/settings_store.py`
- Shipped guide prose: `src/swe_mux/assets/configurator/*.md`
- The gated tool family and its listing/dispatch checks: `src/swe_mux/mcp.py`, `src/swe_mux/mcp_contract.py`
- Routes, harness/Project resolution, the settings write, the health line: `src/swe_mux/server.py`
- The session marker: `src/swe_mux/models.py`
- `default_harness` and its validation: `src/swe_mux/config.py`; resolution: `src/swe_mux/harness.py`
- Launcher state, chooser gesture, request shaping: `frontend/src/configurator.ts`
- Footer button, rail twin, chooser menu, launch path: `frontend/src/App.tsx`
- Default-harness control and the Diagnostics entry: `frontend/src/Settings.tsx`
- The tutorial hand-off step: `frontend/src/GuidedTutorial.tsx`
- Tests: `tests/test_configurator.py`, `tests/test_configurator_mcp.py`, `tests/test_configurator_endpoint.py`, `tests/test_settings_patch.py`, `tests/test_settings_store_edits.py`, `frontend/test/configurator.test.ts`
