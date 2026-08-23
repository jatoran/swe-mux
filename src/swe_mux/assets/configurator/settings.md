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

`configurator_capabilities` reports the allowed and forbidden sets under
`project_settings`. The forbidden ones are a boundary, not an oversight: a
repository must not be able to set this daemon's bind address, its token, or the
command a harness runs. If a Project config is rejected naming one of those, that is
the system working.

This is where a Project's automation opt-ins, its worktree setup and verification
commands, its preferred backend, and its agent authority grants live.

## 3. Per-device UI settings

Stored by the daemon but keyed by device *class* - `desktop` or `mobile` - because
the same install is driven from a browser and from a phone, and they want different
notification behaviour.

Sounds, alert routing, push preferences, the command rail's contents, the file
tree's expanded state, drawer tab order, sidebar row layout.

You do not write these. They are edited where they are used, and several of them are
opaque blobs the browser owns. If someone asks for a change here, point at the
control.

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

1. Read the row in `configurator_capabilities` before proposing a value. The name
   you remember may not be the name it has.
2. Tell the operator the current value, the proposed value, and what will visibly
   differ.
3. Ask.
4. Apply, and report `hot_applied` versus `restart_required` honestly.

If a batch is refused, nothing was written - validation runs over the whole
candidate before anything is saved. The result names the offending fields; fix
those and try again rather than splitting the batch.
