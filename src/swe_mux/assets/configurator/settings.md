# The three places a setting can live

Getting this wrong is the most common way to change the right value in the wrong
place and see nothing happen.

## 1. Install-wide daemon config

One TOML file, `config.toml` in the data directory (`~/.mux` by default).
This is the big one: ports, themes, terminal behaviour, harness executables and
arguments, budgets, voice, notifications, worktrees, automation bounds.

It is what `configurator_capabilities` enumerates under `settings`, and it is the
only thing `configurator_apply_settings` writes.

Every row in that catalog carries:

- `current` and `default`
- `writable` - four fields are the daemon's own and are refused
- `restart_required` - see below
- `constraint` - the sentence the validator itself will answer with if you send
  something it cannot accept

The constraint text is not a description someone wrote; it is produced by asking
the validator. If it says "must be DEBUG, INFO, WARNING, or ERROR", those are
exactly the four values that will be accepted.

Secrets are reported as `<set>` or `<unset>` and never by value. Keep it that way.

**Do not hand-edit the TOML file** while the daemon is running. It holds a revision
counter and writes are atomic; an external edit is either lost on the next save or
picked up as a conflict. Change settings through the panel or through
`configurator_apply_settings`.

## 2. Per-Project config, committed to the repository

`.swe-mux/config.toml` inside the Project's own folder.
It travels with the repository, so it is shared with everyone who clones it - which
is why it is a deliberately small, closed set of fields, and why some fields are
refused outright rather than ignored.

Read with `configurator_project_settings`, write with
`configurator_apply_project_settings`. Both default to the Project this session is
standing in; name another explicitly to reach it.

The read is worth more than the file, because it resolves the automation opt-ins:
`effective` are the ones actually running, `blocked` are the ones switched on whose
dependencies are not. That distinction is the answer to "why is this panel empty",
and the raw file cannot give it.

The forbidden fields are a boundary, not an oversight: a repository must not be able
to set this daemon's bind address, its token, or the command a harness runs. If a
write is refused naming one of those, that is the system working.

**Say out loud that this file is committed.** Turning an automation on here turns it
on for everyone who clones that repository, which is a different sentence from
"turned it on for you" and the operator deserves the accurate one.

Changes are merged over what is there, not substituted for it, and the write is
revision-guarded.

This is where a Project's automation opt-ins, its worktree setup and verification
commands, its preferred backend, and its agent authority grants live.

## 3. Per-device UI settings

Stored by the daemon but keyed by device *class* - `desktop` or `mobile` - because
the same install is driven from a browser and from a phone, and they want different
notification behaviour.

Sounds, alert routing, push preferences, the command rail's contents, the file
tree's expanded state, drawer tab order, sidebar row layout.

Read with `configurator_device_settings`, write with
`configurator_edit_device_settings`. **The rail has its own guide
(`rail-and-actions`); read it before touching the rail.**

Two of the nine domains - `alerts` and `notifications` - the daemon interprets,
because the push sender has to apply the master switch and quiet hours before any
browser tab is alive to filter. The other five it stores **verbatim**: the browser
owns their schema and nothing here can tell a valid one from a mangled one.

That asymmetry decides how you edit them:

- **Never resend a whole document.** Use path-scoped operations. Everything an
  operation did not name is untouched because the request could not name it, which
  is a property of the shape of the write rather than of how carefully you composed
  it.
- **Read first and pass back the `digest`.** The store has no revision, so without
  it an edit the operator made between your read and your write is silently
  discarded rather than refused.
- **Read the result back** and tell them what it is now.

The previous file is kept beside itself on every write, so a bad edit is
recoverable by hand. That is the honest guarantee available here - not "this write
is correct", which nothing in the daemon can promise about an opaque document.

## Hot versus restart-required

Most settings apply the moment they are saved.
A small set cannot, because something is constructed once when the daemon starts:
the listen address and port, the data directory, the supervisor and recovery stores,
the automation queue, and the per-harness MCP and instrumentation gates (adapters
are built at start, so toggling those does not reach an adapter that already exists).

`configurator_apply_settings` returns both lists - `hot_applied` and
`restart_required` - and you must tell the operator which they just got.
A restart-required change that is reported as done, and then does not appear to
work, reads as the setting being broken.

**Never restart the daemon on your own initiative.** A restart is
session-preserving (every live terminal survives it), but it is still an action with
a blast radius, and it is the operator's to take. Say that a restart is needed and
let them ask.

## The order to work in

1. Work out **which of the three** the setting lives in. Getting this wrong is how
   you change the right value somewhere it does nothing.
2. Read it before proposing a value. The name you remember may not be the name it
   has, and the current value is half the sentence you owe them.
3. Tell the operator the current value, the proposed value, and what will visibly
   differ.
4. Ask.
5. Apply, read it back, and report `hot_applied` versus `restart_required` honestly.

**Do the thing they asked.** They pressed a button that opens an agent, not a help
page. Pointing at the editor instead of making a change they asked for is a
non-answer; name the control *as well*, so they can do it themselves next time.

**Default to the shared scope.** Almost every setting here has a broad form and a
narrow one - a global rail and a per-Project override, an install-wide switch and a
per-Project opt-in. An unqualified request means the broad one.

If a batch is refused, nothing was written - validation runs over the whole
candidate before anything is saved. The result names the offending fields; fix
those and try again rather than splitting the batch.
