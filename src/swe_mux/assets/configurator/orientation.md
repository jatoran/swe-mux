# What swe-mux is, and why so much of it is switched off

## The one-sentence version

swe-mux is a browser-based terminal multiplexer whose terminals are owned by a
local daemon rather than by the browser, so closing the tab, reloading the page, or
restarting the app never stops a session.

That single property is what the rest of the design falls out of.
An agent CLI running under swe-mux keeps running while nobody is watching it.
That makes it worth knowing what it is doing, which is why there is status detection.
It makes it worth reaching from another device, which is why there is remote access.
And it makes several agents at once practical, which is why there are Projects,
worktrees, and a landing queue.

## The objects, from the outside in

**A Project** anchors everything to one real folder on disk.
Sessions, files, notes, previews, history, and the pane layout all belong to a
Project.
Nothing exists outside one, which is why the first thing a new install asks for is a
folder.

**A session** is one process the daemon owns: an agent CLI, or a plain shell.
It has a working directory, a lifecycle, and - when it is an agent swe-mux
recognises - a status, a transcript, and a prompt queue.

**A pane** is a region of the workspace and **a tab** is a view inside one.
A tab can be a terminal, a note, a file, the History browser, or a live preview.
Closing a file or note tab closes only the view; closing a live terminal asks first,
because that stops a process.

**The utility drawer** is where the analysis surfaces live: History, transcripts,
Git, processes, notes, the scan timeline, the queue.
Most of them are per-Project opt-ins.

## The rule that explains most confusion

**Anything that costs money, reads a conversation, or acts without a human starts
switched off, per Project.**

This is deliberate and it is the single most common source of "is this broken?".
A panel that has never been switched on looks exactly like a panel that is failing,
and swe-mux tries hard to say which it is - the notices that appear in empty panels
name the scope, say where the change is written, and disclose whether it can cost
money before you press anything.

So before agreeing that a feature is broken, check whether it was ever enabled.
`configurator_capabilities` reports the automation graph, and each entry's
`closure` is what must actually be on before that entry does anything at all.
A consumer switched on without its substrate is inert, not broken - and that is
the failure that most looks like a bug.

## What is on by default

Terminals, Projects, panes and tabs, the file browser, notes, session status
detection, the prompt queue's manual half, History of sessions swe-mux started,
Git status.
In other words: the multiplexer.

## What is off by default

Transcript storage, deterministic fact capture, the scan timeline, attention
ranking, the code-structure graph, doc-debt detection, agent session control,
agent-initiated spawn, the land queue, auto-delivery of queued messages, remote
access beyond loopback, push notifications, and voice.

Some of those are off because they spend money.
Some are off because they let something act without a person.
Some are off simply because they read conversation content and that should be a
choice.
The distinction matters when advising: "this costs nothing, it just needs turning
on" and "this will bill your OpenRouter key" are very different recommendations.

## Where the controls are

- **Settings** (main menu) holds install-wide configuration, grouped: Workspace,
  Agents, Interface, System.
- **Manage projects** holds each Project's own opt-ins, including which automations
  run there.
- **The Automation dashboard** holds the rule corpus, live/shadow state, spend, and
  per-Project matrix. Settings holds only the install-wide switches and bounds.
- **The utility drawer** holds the per-Project surfaces themselves.

When a one-line answer is "Settings → Terminals → cursor style", give that answer
rather than making the change silently.
The operator will want to change it again, and a control they can find is worth more
than a setting you moved for them.
