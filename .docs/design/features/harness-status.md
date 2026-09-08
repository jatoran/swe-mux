# Harness-reported session status

## What it is

`SessionRecord.harness_status` holds what a harness reports about its own session that no transcript carries: the reasoning effort in force, the tool-permission mode, whether fast mode and extended thinking are on, the output style, and the provider's rate-limit windows as of the last response.
These are the facts a CLI's own status line draws.
Every field is optional, every harness fills a different subset, and a field the harness never reports stays `None` so the row renders nothing for it.
Nothing here is guessed: an absent field is the absence of a report, and the sidebar and top bar treat it exactly as they treat a missing branch or account.

The contract is `models.HarnessStatus`:

```python
@dataclass
class HarnessStatus:
    effort: str | None                      # low..max (Claude), minimal..xhigh (Codex)
    permission_mode: str | None             # default|acceptEdits|plan|dontAsk|bypassPermissions|auto
    output_style: str | None                # Claude only
    fast_mode: bool | None
    thinking: bool | None                   # Claude only
    rate_limits: dict[str, RateLimitWindow] # five_hour, seven_day, spend_limit, plus unmapped windows
    context_window_size: int                # the CLI's own window, 0 until reported
    sources: dict[str, str]                 # field -> claude-statusline | hook | codex-rollout
    updated_at: float | None
```

`RateLimitWindow` is `{used_pct, resets_at, window_minutes}`; `used_pct` runs 0-100 and may exceed 100 on a spend limit.

## Where each field comes from

Three channels feed the record, and `harness_status.py` is the one place each payload is parsed.
Every parser is pure over the record: it writes the fields it can vouch for, leaves the rest untouched, and returns the names of the fields it changed so the caller publishes only on a real change.

| Field | Claude | Codex |
| --- | --- | --- |
| `permission_mode` | every root-scoped hook payload | every root-scoped hook payload (same vocabulary) |
| `effort` | status-line snapshot `effort.level` | rollout `turn_context.effort`, `thread_settings_applied.thread_settings.reasoning_effort` |
| `fast_mode` | status-line snapshot `fast_mode` | `thread_settings.service_tier == "fast"` |
| `thinking`, `output_style` | status-line snapshot | not reported |
| `rate_limits` | status-line snapshot `rate_limits` | rollout `token_count.rate_limits`, keyed by `window_minutes` (300 is `five_hour`, 10080 is `seven_day`) |
| `context_window_size` | status-line snapshot `context_window.context_window_size` | not needed: `token_count.model_context_window` already feeds `context_window` |

`sources` records which channel last wrote each field, because the fields arrive over different channels at different moments and one provenance for the whole object would name whichever channel spoke last.

### The permission mode rides the hooks

Claude and Codex both put `permission_mode` on every hook payload, in one vocabulary, and no transcript carries it.
`apply_hook_observation` reads it after the scope and foreign-conversation filters, so a subagent's mode and a nested child's never overwrite the pane's own.
It refreshes at the next hook, so a mode switched between turns shows on the next prompt or tool call rather than instantly.

### Codex reports through the rollout it already writes

`turn_context` restates the effort at the head of every turn and `thread_settings_applied` on every change, so the opening setting and a mid-session `/model` switch are both seen.
Every persisted `token_count` carries the account's `rate_limits`, which the observer used to read past.
Both are attribution rather than state and wait for the conversation to be proven, on the same rule as the token counts: a provisionally followed file must not put its effort on this pane.

The context reading was aligned with the CLI's own footer at the same time.
Codex computes `percent_of_context_window_remaining` from the last response's `total_tokens` against the window with a 12,000-token baseline subtracted from both sides, and `codex_context_fraction` mirrors that, so the row reads what the footer reads.
The baseline is a Codex constant (`BASELINE_TOKENS`, read at rust-v0.153.4) and is pinned by a test rather than trusted.

### Claude reports only through its status line, so swe-mux tees it

Claude Code hands a JSON snapshot to the configured `statusLine` command on every assistant message, compaction, and permission or mode change, and there is no other channel for its effort, its own context and cost figures, or its rate limits.
swe-mux already passes a per-session `--settings` file, and Claude's precedence is managed > command line > local > project > user, so a `statusLine` written there replaces the user's rather than adding to it.
The tee therefore delegates.

- At spawn, `claude_status_line.resolve_status_line_delegate` reads the user's own `statusLine` from `.claude/settings.local.json`, `.claude/settings.json` under the spawn directory, and `settings.json` under the Claude data home, in that order, and the first command found wins.
- The delegate's command and source are written into the pane's `hook-identity.json` as flat string keys, and the per-session settings gain a `statusLine` whose command is the ordinary hook shim invoked with the `Status` event and the same `--identity`.
  The user's `padding`, `refreshInterval`, and `hideVimModeIndicator` are copied onto the replacement, so the only thing that changes about their status line is who runs it.
- `hook_client Status` reads the snapshot from stdin, runs the delegate under the shell Claude would use (Git Bash or PowerShell on Windows, `/bin/sh` elsewhere) with the same bytes on stdin, writes the delegate's stdout back unchanged, and only then posts the snapshot to the hook ingress in a single short-budget attempt with no retry and no spool.
  The terminal never waits on the daemon, and a missed snapshot is superseded by the next message.
  On Windows the delegate shell is created with `CREATE_NO_WINDOW`: the frozen GUI hook helper has no console to inherit, and redirected stdio alone would create a visible console at every status refresh, including refreshes during tool execution.
  The flag is applied at the delegate launch, independently of the session's ConPTY, and leaves status output and diagnostics intact.
- The tee is written only when a delegate exists.
  Configuring a status line where the user had none changes their terminal, because Claude hides most of the footer's keyboard hints once any custom status line is set, and swe-mux must not make that choice for them.

The snapshot carries the CLI's own `context_window_size`, `used_percentage`, and `cost.total_cost_usd`, and those replace the derived figures: the window size is preferred over the model table (which lags every new model and lags into a zero), and the cost fills a figure the transcript path never had, which is why a Claude row can show a cost and a Codex row cannot.

## The ingress and the observer

`Status` is accepted on `POST /api/hooks/{sid}` with the hook secret, because it speaks for the same conversation the hooks do.
It is not a lifecycle hook: `apply_hook_observation` folds it into the record and returns before any branch that could move state, the ingress keeps it off the generic EventBus fan-out so the per-message snapshot never lands on the bus whole, and it still reports the CLI's `cwd` like every hook.
A real change emits a compact `harness_status` event carrying the settings and each limit's percentage.

The snapshot's `delegate` block reports whether the user's command ran.
A failure is invisible in the pane, where Claude shows an empty status row, so the observer logs it at WARNING once per distinct error and counts it under `status_delegate_failed` in the status-health counters; recovery is logged once.

## Lifetime

Run-scoped like the token measurements: cleared wherever a conversation is replaced or a new agent run starts, and refilled by the next hook and the next report.
It rides the supervisor snapshot through a session-preserving restart, because a restart that emptied every row's effort and limits until each session's next message would read as the feature flickering off.

## What the rows draw

Four fields on the shared catalogue (`sessionRowConfig.ts`), available to the sidebar rows and the pane top bars alike:

- `effort` prints the level as the harness spelled it; notable when it differs from the project's most common level, on the same rule as the model.
- `mode` prints the permission mode in row-sized words (`accept edits`, `bypass`, `don’t ask`); notable off `default`, and amber for the modes under which the agent acts without asking.
  A mode this build has no word for prints as spelled, because a new mode is a fact rather than a reason to go quiet.
- `limit5h` and `limit7d` print `5h 23%` and `7d 61%`; notable past half used, banded warn/high/crit at 50/75/90, with the reset time in the tooltip.

Two pre-existing fields changed with the data:

- `cost` draws nothing until a harness has reported one.
  `$0.00` was the absence of a measurement wearing the shape of one, on the same rule the duration field states at length.
- `context` gained a `both` rendering, the gauge cells with the exact number beside them as one token.

## Failure modes

- A managed `statusLine` outranks the injected one, so the tee never runs there and Claude's effort stays absent; the identity file still names the delegate, so the data directory shows a tee that was written and never posted rather than one that was never written.
- A delegate command edited mid-session is picked up at the next spawn, not live; the tee resolves it once, at spawn, because the resolution is the daemon's one implementation of Claude's precedence rather than a second copy in the shim.
- Claude cancels the whole tee when a newer update arrives; the delegate is a short status script, and an orphan of it lives for the remainder of that script's run.
  The shim deliberately imports nothing from the package, so it does not borrow the job-object reaper.
- A Codex window of a length the key table does not know keeps its slot name (`primary`, `secondary`) rather than vanishing.

## Key files

- `src/swe_mux/models.py` (`HarnessStatus`, `RateLimitWindow`, `SessionRecord.harness_status`)
- `src/swe_mux/harness_status.py` (the parsers and `codex_context_fraction`)
- `src/swe_mux/claude_status_line.py` (delegate resolution)
- `src/swe_mux/adapters/claude.py` (`_write_hook_settings`, `_write_hook_identity`, `_status_line_delegate`)
- `src/swe_mux/adapters/__init__.py` (the resolver is wired for `claude` only)
- `src/swe_mux/hook_client.py` (`Status`, `_run_delegate`, `_status_shell`)
- `src/swe_mux/routes/agent_ingress.py`
- `src/swe_mux/observation.py` (`_apply_status_snapshot`, the Codex record handlers)
- `frontend/src/types.ts`, `frontend/src/sessionRowConfig.ts`, `frontend/src/sessionRowFields.ts`
- `tests/test_harness_status.py`, `frontend/test/sessionRowFields.test.ts`, `frontend/test/sessionTopbarConfig.test.ts`
