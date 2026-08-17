# Control-plane approvals

## What it is

A per-conversation switch deciding what swe-mux answers on the agent's behalf when the harness
asks for tool permission.
Three positions and no more: `wait` (the default, and the only one that changes nothing),
`allowlisted` (requests matching the Project's rules are answered, the rest reach the human),
and `allow_all` (everything except a floor no mode can cross).

The decision is made from the harness's own structured permission request — `tool_name` plus
`tool_input` — and never from the PTY screen.
That distinction is the whole design.
`pty_tail_state` can say "a dialog is up"; it cannot say what the dialog is asking, so a
screen-driven auto-approval is a blind Enter that lands equally in a permission prompt, a trust
dialog, a `/clear` confirmation, or the composer a millisecond after the dialog resolved.
A hook decision knows the tool and its arguments, so "approve reads and VS Code task writes,
escalate everything else" is expressible; on the screen it is not.

This is why the feature is *not* built on the mechanism the voice approval uses.
Talk writes Enter after fingerprinting the exact visible prompt, and `voice.md` records that no
approve-all command exists there — correctly, because at that layer one could not be written
safely.

## Key concepts

- **Never auto-deny.** `deny` is not in the vocabulary. A denial is a decision the agent acts
  on: it will try something else, usually something worse, and the human never learns a choice
  was made for them. Declining to answer (`ask`) is always available and always safe, so the
  decision type has two values, not three.
- **The floor is not a mode.** `FLOOR_PATTERNS` and `SECRET_PATH_PATTERNS` in `approvals.py`
  are consulted *before* the mode and cannot be switched off by any of them, by any Project, or
  by any operator. They cover pushes and forced Git operations, recursive and forced deletes,
  raw disk and filesystem writes, host power operations, `sudo`, process kills, piping a
  download into a shell, outbound uploads, package and container publishes, GitHub and
  infrastructure writes, and every credential-shaped path (`.env`, `.ssh`, `.aws`, `.npmrc`,
  `.git-credentials`, `.claude.json`, `*.pem`, `secrets/`). The reason is the threat model:
  this daemon is reachable over Tailscale from a phone and writable by agents over MCP, so one
  prompt injection into one session must not be a machine compromise.
- **A grant is bounded twice, and neither bound is optional.** `set_approval_mode` always
  stamps an expiry (`approval_grant_ttl_minutes`, default 30) and an answer budget
  (`approval_max_auto_per_grant`, default 200). There is no path by which an operator produces
  standing authority. The failure this bounds is not malice; it is switching `allow_all` on for
  one task and leaving it on for the day.
- **The grant belongs to the conversation, not the session.** It records the `agent_run_id` it
  was made against, and `ApprovalPolicy.effective_mode` refuses to apply it to any other one.
  A session outlives its task: `/clear`, `/resume`, Branch, and conversation rollover all start
  work nobody granted anything for, and a grant that survived them would be the stuck-status bug
  class applied to authority.
- **Expiry and run-scoping are evaluated at read time, not swept.** A sweep that does not run
  leaves authority standing; a read-time check cannot. `revoke_approval_policy` additionally
  clears the record at every run-identity seam (promotion, demotion, rollover, identity heal,
  process exit) — but that is for *legibility*, not safety: by the time it runs the grant is
  already inert, and what it fixes is the strip and the sidebar badge still rendering a mode
  that no longer applies.
- **Rules are snapshotted when the mode is set.** Two reasons, both load-bearing. The decision
  runs on the agent's blocked turn and must do no file I/O; and a grant is authorization of
  *the rules the operator saw*, so editing the committed Project file must not silently widen a
  grant already standing.
- **A refusal is named.** Every path that will not grant a mode says which one it is
  (`invalid_mode`, `approvals_unavailable`, `above_ceiling`, `empty_allowlist`). An operator
  who selects `allow_all`, silently gets `wait`, and is told nothing concludes the control does
  not work — and then stops trusting the one that does.
- **Returning to `wait` is never refused.** Taking authority back does not depend on the
  install switch, the Project ceiling, or the conversation still being the one the grant was
  made against.

## The mechanism

Claude's `PermissionRequest` hook is the whole channel, and it fits exactly:

- It fires *only* when a permission prompt would be shown — not on every tool call — so it adds
  no traffic to auto-allowed work.
- It carries `tool_name`, `tool_input`, `tool_use_id`, and `permission_mode`.
- It reads `{"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision":
  "allow"|"deny"|"ask"}}` back off the hook command's stdout.
- **No response means `ask`.** The channel fails open by construction: a daemon that is down,
  slow, or answering something it does not understand is indistinguishable from no hook at all.

swe-mux already registered that hook on every Claude session and already wrote
`permissions.allow` rules into its generated per-session settings, so the wiring change was
small:

1. `hook_client.py` gained a decision path for `DECISION_HOOK_EVENTS`. One fast single-shot POST
   (`_DECISION_TIMEOUT`, 3 s), the response body parsed, and the daemon's `hookSpecificOutput`
   relayed to stdout verbatim. Deliberately not retried — a retry loop here is time the agent
   spends parked on a prompt mux might have answered instantly. When it misses, the ordinary
   retry-and-spool path still runs, because the *record* of the approval matters even when the
   answer did not arrive (a permission raised during a session-preserving restart has no second
   source; see `status-detection.md` § Hook spool replay).
2. The daemon composes the harness-specific shape, not the shim. The shim runs as a fresh
   interpreter on the critical path for every hook and imports nothing from the package, so a
   second decision-capable harness is a registry change rather than an edit to the command every
   session already runs. `tests/test_approvals.py` asserts the two event lists cannot drift.
3. The generated Claude settings carry an explicit `timeout` on the decision hook. Claude's own
   default is 600 s and a timed-out hook does not block, so this bounds a *stall*, not a
   decision: without it a wedged daemon costs the agent ten minutes before the CLI gives up and
   shows the prompt.

**It interacts with status detection in the helpful direction.** Today an auto-approved tool is
the documented false-attention bug (`status-detection.md` § Delegated approvals: Codex's own
reviewer answers, nothing records the decision, and the only evidence is the tool finishing —
so anything slower than the 5 s window becomes sidebar attention and a push notification for a
question nobody was asked). When mux is the approver it knows the decision instant, so
`_request_stabilized_approval` is never entered at all: no candidate, no timer, no `awaiting`,
no sound, no push.

## Harness coverage

Declared per harness as `HarnessDescriptor.hook_approval_decisions`, not inferred. The failure
of getting this wrong is silent in the worst direction: a harness wrongly marked capable would
render a mode selector that changes nothing, and the operator would believe requests were being
answered while every one of them sat waiting.

| Harness | Can answer | Why |
| --- | --- | --- |
| claude | yes | `PermissionRequest` returns a decision on stdout |
| codex | no | fires `permission_request`, has no resolution hook, and writes zero approval records. Its only lever is spawn-time `approval_policy`/`sandbox_mode`, which is whole-session and cannot change while the CLI runs |
| omp | no | emits `PermissionRequest` and `approval_resolved` as one-way notifications |
| pi | no | emits no permission event at all |
| opencode | no | *has* a decision-capable permission hook, but the mux plugin subscribes to the observational event bus (`permission.updated` / `permission.replied`). Enabling it is a plugin change, not a registry edit |

Nothing that is not a tool permission request produces a hook at all — trust dialogs, logins,
`/clear` confirmations, Codex startup dialogs — so this can never be described to the user as
"approves everything". The strip says so rather than leaving it to be discovered.

## Configuration

Three layers, matching the auto-delivery shape (`auto-delivery.md`), which is the closest
structural precedent in the codebase.

- **Install-wide** (`config.py`, Settings): `approval_auto_enabled` (default **off** — with it
  off, `PermissionRequest` is observed exactly as before and no session can hold a non-`wait`
  mode), `approval_grant_ttl_minutes`, `approval_max_auto_per_grant`,
  `approval_hook_timeout_seconds`, and `approval_allow_all_permitted`. The last is separate from
  the master switch because "let mux answer the boring ones" and "let mux answer everything" are
  different decisions, and the first is the one most installs want.
- **Per-Project** (`.swe-mux/config.toml`): `approval_allow`, the `Tool` / `Tool(pattern)` rules
  `allowlisted` resolves against, and `approval_ceiling`, the strongest mode a session here may
  hold. The rules live here rather than on the session because "reading this repo's `.claude`
  config is fine" is a property of the codebase, and because a rules editor is the wrong thing
  to hand someone in the moment they want to switch a mode on. Unset `approval_allow` means the
  built-in `DEFAULT_ALLOW_RULES`; an explicit `[]` means "approve nothing automatically here"
  and is preserved on write rather than dropped.
- **Per-conversation** (runtime, on `SessionRecord.approval_policy`): the live mode, its expiry,
  its rule snapshot, and its counters. On the record rather than in SQLite so it rides the
  supervisor snapshot through a session-preserving daemon restart — a routine operation here,
  and one that must not silently return a session to `wait` mid-task.

### Rule syntax

`Tool` matches every request for that tool. `Tool(pattern)` additionally matches the tool's
subject with `fnmatch`: the command for shell tools, the path for file tools, the URL for
`WebFetch`, and so on. The tool half globs too, so `mcp__mux__*` covers one MCP server.

Two rules about matching are not conveniences:

- **A tool with no known subject can only be allowed wholesale.** A patterned rule never
  matches it. An unrecognized tool cannot be narrowed, and narrowing something whose arguments
  cannot be read would be the allowlist claiming a precision it does not have.
- **Every segment of a shell command must be covered.** `Bash(git status*)` must not approve
  `git status && rm -rf .`, so the command is split on `&&`, `||`, `;`, `|`, and newlines, and
  every segment must match some rule (possibly a different one). The floor is scanned over the
  raw text *before* segmentation, so an obfuscation that defeats the splitter still meets it.

## UI

- **The `approvals:` strip** (`ApprovalStrip.tsx`), one collapsed line directly above the pane's
  command rail, disclosing the three-position selector, the rule count and its source, the last
  answered request, and the floor-deferral count. The same shape as the Queue pane's `auto:`
  strip, and for the same reason: a control that changes what mux does on the operator's behalf
  has to be readable from the surface they are already looking at, and a brake reachable only
  through an overlay is a brake nobody reaches in the moment they want it.
  Rendered for every agent pane, **including ones where no mode can be selected** — a control
  that disappears when unavailable teaches the operator it does not exist, while one that stays
  and says why teaches them what would make it work.
- **`Approve` on the command rail**, enabled only while the pane is showing an approval. This is
  the one-shot, and it is an action rather than a mode: it answers the request on screen, once.
  It is the mobile-reachable path, and it is deliberately given no voice alias — Talk keeps its
  two-step challenge, which exists because a spoken caller cannot see the dialog they are
  confirming, and a one-tap duplicate reachable by voice would route around that guard.
- **Sidebar row badge** (`approvals` row field, in the flag strip, on by default). Reads the
  *effective* mode, so a lapsed or superseded grant draws nothing. On by default unlike almost
  every other field, because the mode's entire effect is removing the notification an approval
  would raise — the fleet list is the only place a grant nobody remembers setting can still be
  seen.
- **Command palette**: `session.approveOnce`, and `session.approvals.{wait,allowlisted,allowAll}`.
- **Session context menu**: "Approve this request" while one is showing, and "Stop
  auto-approving here" while a grant stands. Granting is deliberately *not* offered there —
  handing out authority from a right-click on a row you are not looking at is the wrong
  affordance, and the pane's strip is where the mode, its rules, and its budget are visible
  together.

## Audit

An auto-approval is the one class of agent activity that would otherwise leave no trace
anywhere in mux, precisely because the feature removes the notification. So every decision is
recorded:

- `approval_auto_decision` in the transition ledger, carrying the decision, mode, reason,
  matched rule, floor label, bounded request description, `tool_use_id`, and the grant's running
  count. Allows *and* floor deferrals, because "the mode is on and it still asked me" is
  otherwise indistinguishable from a bug.
- `approval_mode_set` and `approval_mode_revoked`, the latter naming the seam and the count the
  retired grant reached.
- The `approval_auto_approved` event on the bus.
- `ApprovalPolicy.auto_approved` / `last_request` / `floor_deferred` on the record, so the strip
  reports what the mode has actually been doing rather than only that it is on.

## Interfaces

```
GET  /api/sessions/{sid}/approvals
PUT  /api/sessions/{sid}/approvals              {mode, set_by?}
POST /api/sessions/{sid}/approvals/approve-once {fingerprint?}
```

The GET/PUT body carries `supported`, `enabled`, `ceiling`, `rules`, `rules_source`,
`unavailable`, `ttl_seconds`, `max_auto`, `policy`, `effective_mode`, and `modes`.
`policy.mode` and `effective_mode` are both present and are different questions: an expired
grant reports its stored mode *and* an effective `wait`, because "it lapsed" reads differently
from "it was refused".

`approve-once` re-checks the agent run, this session's own screen still classifying as an
approval (`pty_tail_state`), and — when the caller supplies one — the prompt fingerprint,
before writing a single `\r` through `_record_operator_input`. The browser routes through the
daemon rather than writing Enter itself because only the server can make those checks; a pane
that wrote the byte would be sending Enter into whatever the screen had become in the meantime.

## Deployment

`hook_client.py` and the adapter changes ride the **app bundle**, so shipping this to the frozen
desktop app needs the full redeploy flow, not a daemon restart (`CLAUDE.md`). It is not a
supervisor change, so it reaps no sessions. Existing live sessions keep the hook settings they
were spawned with, so the `timeout` field reaches a session only on its next spawn; the decision
path itself works immediately, because the daemon owns the answer.

## Key files

- Policy, rules, and the floor: `src/swe_mux/approvals.py`
- `ApprovalMode` / `ApprovalPolicy` and the record field: `src/swe_mux/models.py`
- Grant lifecycle and revocation: `src/swe_mux/session.py`
  (`set_approval_mode`, `revoke_approval_policy`, `approval_mode_within`)
- The decision on the hook path: `src/swe_mux/observation.py` (`auto_approval_decision`,
  `_note_auto_approval`)
- Shim decision relay: `src/swe_mux/hook_client.py`
- Generated hook settings and the decision timeout: `src/swe_mux/adapters/claude.py`
- Endpoints: `src/swe_mux/server.py`
- Project fields: `src/swe_mux/project_files.py`
- UI: `frontend/src/ApprovalStrip.tsx`, `frontend/src/approvals.ts`,
  `frontend/src/commandRail.ts`, `frontend/src/sessionRowFields.ts`

## Relates to

- `status-detection.md` — the approval-stabilization contract this bypasses, and the delegated
  approval problem it improves
- `delivery-readiness.md` — `approval_detected` blocks delivery; an auto-approved request never
  emits it
- `auto-delivery.md` — the three-layer switch/grant/bounds shape this follows
- `backends.md` — the per-harness hook registration and `hook_approval_decisions`
- `voice.md` — the two-step spoken approval, and why it stays separate
- `notifications.md` — what an approval would have raised, and no longer does
