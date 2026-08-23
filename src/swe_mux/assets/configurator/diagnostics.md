# Symptom to evidence

The rule: read a fact before proposing a fix.
Most swe-mux complaints have a report that answers them directly, and guessing at a
cause when a check already names it wastes the operator's time and can produce a
"fix" that changes an unrelated setting.

## The reports

**`configurator_diagnostics`** is the consolidated one. It carries a flat `checks`
list, a `capabilities` block, a `summary` count, and observation-freshness rows.

Two fields on each check matter and are different:

- `status` - `ok`, `warn`, `fail`, or `unavailable`.
- `severity` - `critical`, `optional`, or `info`.

`unavailable` plus `optional` means a feature is simply not configured. That is not
a problem and must not be reported as one. `fail` plus `critical` means something
that compromises terminal ownership, cleanup, or message delivery.

Rank by severity, not by position in the list.

**Export diagnostics** (Settings → Diagnostics) produces a bundle for a bug report.
It carries no terminal bytes and no message content. If the operator is about to
reproduce a problem they intend to report, tell them to set the log level to `DEBUG`
*first*: the bundle carries the daemon log, and the interesting lines only exist if
the level allowed them.

**Per-session diagnostic bundle** is the one to reach for when a single session
misbehaves rather than the install.

## Symptom index

**"The panel is empty."**
Almost always an opt-in, not a fault. Check the automation's `closure` in
`configurator_capabilities`. See the automations guide.

**"An agent's status is stuck on working."**
Status is derived from several sources per harness and the report's
`observation_freshness` rows are the evidence. A session reporting a conversation it
can no longer prove is live is a *delivery-blocking* condition, not a cosmetic one -
a queued message could land in the wrong place. Those rows carry only a session's own
id, reason, and age.

**"A queued message never arrived."**
Delivery waits for readiness and, by default, for a human to approve it. So "not
delivered" usually means "not yet approved" or "the target never became ready".
Check the message's own status before looking at anything else.

**"My change to the UI did nothing."**
The most misleading failure in the whole system, and it is a build question rather
than a code question. A frozen desktop app serves its own bundled copy of the
frontend and respawns its own bundled backend on restart, so an edit to a source
checkout can be entirely correct and still never load. See the `modifying-swe-mux`
guide - do not debug the change itself until the build question is settled.

**"The phone cannot connect."**
Read the remote and firewall checks first. Detection failure leaves loopback working
and reports a diagnostic, so the install is not broken. Tailscale DNS on the phone is
the usual answer for a `.ts.net` name that will not resolve.

**"A harness is not offered in the Run menu."**
Three-state enablement: absent means "follow detection". Check `installed` and
`enabled` in the harness catalog - explicitly disabled and undetected look the same
from the menu and are fixed differently.

**"Something is running that I did not start."**
The background-loop checks report which daemon loops are alive. An unsupervised loop
that dies is otherwise invisible, which is exactly why those checks exist.

## What not to do

Do not propose a settings change as a *diagnosis*. Change a setting when the evidence
says that setting is wrong. A change made hopefully, on a system the operator does not
yet understand, leaves them worse off than the original symptom did - now something is
different and nobody knows why.

Do not treat `unavailable` as broken.

Do not read the log level as trivial: it is applied as soon as it is saved, needs no
restart, and it is what the bundle carries.
