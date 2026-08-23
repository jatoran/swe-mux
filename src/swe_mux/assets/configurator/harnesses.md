# Harnesses, accounts, and the default harness

A **harness** is an agent CLI swe-mux knows how to run and observe.
Which ones exist is a registry in the daemon, not a configuration list, so a harness
cannot be "added" from the UI - it is either in this build or it is not.
`configurator_capabilities` reports the whole registry with this machine's live
detection folded in.

## The three-state enablement rule

Per harness, `harness_enabled` holds **explicit choices only**.
An absent key means "follow detection". That gives three states, and they are
different in a way that matters:

- **Absent** - swe-mux offers the harness if its CLI is detected on this machine,
  and stops offering it if the CLI goes away. This is the default and usually the
  right one.
- **Explicitly true** - offered even when detection fails. Useful when the CLI lives
  somewhere `which` does not look, alongside an `harness_exe` override.
- **Explicitly false** - hidden from the launchers even though it is installed.

This is a **launcher filter only**. A disabled harness stays spawnable by an explicit
API call, and every status, transcript, and history surface keeps seeing all
registered harnesses. So disabling one does not hide its past sessions, and it is not
a way to stop something from running.

Detection is deliberately two signals: the resolved executable, and whether the
harness's own data home exists. A plain `which` is not enough on its own, because
swe-mux puts shims for every harness on PATH.

## The default harness

`default_harness` answers **"which agent, when something needs an agent and nobody
named one"**.

It is a different question from `default_backend`, which answers "what does the Run
menu open by default" and legitimately defaults to `shell`.
A shell is not an answer to the first question - it cannot receive a seeded prompt -
so the two are separate fields.

Empty means "resolve by detection", which is right for the common case of exactly
one agent installed. The resolution order is: the Project's own preference, then
`default_harness`, then `default_backend` if it happens to name an agent, then simply
the first available agent. If nothing is available the answer is "none", and a caller
must say so rather than quietly substituting a shell.

Set it in **Settings → Harnesses** when more than one agent is installed and the
operator has a preference.

## Provider accounts

Some harnesses can have their provider login captured and switched by swe-mux;
`provider_accounts` in the registry says which.
The two paths are "sign in + save", which opens the provider's normal login flow, and
"save current login", which captures a session that is already signed in.

An account is a saved credential, so it is the one part of harness setup that is
worth being careful about in conversation: never echo one, and never suggest putting
one in a Project's committed config (which refuses credentials outright).

## Executables and arguments

`harness_exe` overrides the executable per harness; `harness_args` prepends
arguments to every launch of one.

Arguments are where authority lives. Several flags a harness accepts change what it
is allowed to do without asking, and swe-mux builds some arguments itself - the
conversation id, the settings file, the MCP config, the resume flag. Those are
reserved: setting one by hand is refused, because two sources writing the same flag
produce a session whose identity swe-mux cannot track.

`configurator_capabilities` reports each harness's reserved arguments.

## Launch profiles

A named bundle of executable, arguments, environment, and working-directory strategy.
Shell profiles and agent profiles are both here; an agent profile is constrained
(native working directory only, no cwd integration) because an agent CLI resolves its
own conversation from where it is started.

A Project may *select* a launch profile by name in its committed config, but may not
*define* one there - for the same reason it may not set `harness_args`. Naming a
profile the operator wrote locally is a preference; supplying argv is an escalation.

## The mux MCP server

Per harness, `harness_mcp_enabled` decides whether swe-mux registers its own MCP
server with that agent. On by default where the harness has an MCP client at all.

Turning it off for a harness removes that agent's fleet visibility - it can no longer
see sibling sessions, read Project notes, or queue a message to another session.
It changes nothing else. This is a restart-required setting: adapters are built once
at daemon start.
