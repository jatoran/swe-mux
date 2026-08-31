# The command rail, and editing it

The command rail is the strip of buttons beside a terminal: keys, pasted text,
slash commands, skills, prompt-library entries, and pads. It is the most
customized surface in swe-mux and the one most often asked about.

Read this before editing it. The blob is legible once you know its shape and
misleading if you guess.

## Where it lives, and the trap

**One document, under the `desktop` profile, holding both device layouts.**

```
settings.json → profiles → desktop → commandRail
                                     ├─ items[]        the catalog
                                     ├─ layouts.desktop.strip[]
                                     ├─ layouts.mobile.strip[]
                                     └─ projects{}      per-Project overrides
```

So "edit the mobile rail" means `profile=desktop domain=commandRail`, at a path
under `/layouts/mobile`. A `commandRail` document written under the **mobile**
profile is valid, stored, and read by nothing - a change that appears to succeed
and does nothing.

The reason for the single bucket is good: the catalog of commands is shared while
the arrangements are not, so splitting the layouts across the two profile buckets
would make one save into two writes, with a window where one device's layout names
a command the catalog has not got yet.

`configurator_device_settings` with `domain: "commandRail"` returns this resolved:
rows with their items' labels, and the exact path each row's items are at.

## The four moving parts

**`items[]` is the catalog.** Every button that exists, by id. Built-ins are not
listed here - only custom ones the operator authored (a prompt, a skill, a text
snippet, a pad).

**`layouts[device][surface][]` is the arrangement.** A list of rows, each with an
`id` and an `items[]` of catalog ids. Removing an id from a row removes the button
from that row; the catalog entry stays, so the button can be placed again.

**`projects[<id>]` is a per-Project override**, and there are two kinds. A
`delta` adds to the live global layout and keeps tracking it. A `fork` is a
detached copy that no longer follows global edits.

**A delta's `splices` and `hides`** are how it modifies a shared row without
owning it. A splice says "put this item into that row, after that other item". A
hide says "do not draw this item in that row, here".

That anchor matters when you delete things: a splice anchored `after: "right"` in
a row you are about to remove `right` from loses its anchor, and the spliced item
falls back to wherever the fallback puts it. Say so before making the edit.

## Global first

**An unqualified request means the global rail.** "Remove the arrows from row 2"
is `/layouts/mobile/strip/[id=row-2-…]/items`, not a per-Project override.

Most Projects have no override at all. Reach for `/projects/<id>` only when the
operator named a Project, or when the thing they are changing exists only there.

**And never read an override as "this Project's" because it is the only one
present.** An install can have twenty-four Projects and one override, and the
odds it belongs to the one you are standing in are exactly one in twenty-four.
The projection resolves every override to its Project *name* and marks yours;
`configurator_capabilities` puts your Project id in `install.session`. Use them.
Getting this wrong produces a confident, specific, wrong warning, which is worse
than no warning at all.

## Editing it

Path-scoped operations, never a whole document. Nothing in the daemon can tell a
valid rail from a mangled one - the browser owns that schema - so an operation
that names what to change is the only kind that cannot lose what it did not name.

Read first, keep the `digest`, pass it back. That is what makes an operator's drag
between your read and your write a refusal instead of a silent revert.

Removing four buttons from a row:

```json
{"op": "remove_values",
 "path": "/layouts/mobile/strip/[id=row-2-43lio]/items",
 "values": ["up", "down", "left", "right"]}
```

`remove_values` rather than four `remove`s because positional removal shifts the
indices of everything after it: four positional deletes composed against one
reading remove the wrong things after the first. Naming the values is
order-independent and cannot be thrown off.

`[id=row-2-43lio]` rather than `/1` because a row named by its own id cannot be
reordered out from under the write.

Adding a button back, in a chosen place:

```json
{"op": "insert",
 "path": "/layouts/mobile/strip/[id=row-2-43lio]/items",
 "value": "up", "after": "padArrows"}
```

Then read it back and tell the operator what the row is now.

## What to check before an edit

1. Which **device layout** they mean. They are separate arrangements.
2. Whether the row they said ("rail 2", "the second row") is the row you found -
   name its contents back to them.
3. Whether any project delta **splices into that row**, and whether your edit
   removes an anchor. If so, name the Project it belongs to, correctly.
4. Whether the item is in a **pad** as well as loose in the row. A pad holds up to
   four catalog ids; removing the loose copy does not remove it from the pad, and
   that is usually exactly what the operator wants.

## Related surfaces in the same store

`drawerTabs` is the utility drawer's tab order. `sessionRows` is the sidebar row
layout. `sessionTopbar` is the one-to-three-row session pane header layout, including
metrics and drawer shortcuts. `fileTree` is which directories are expanded, per Project. `sounds` is
per-event sound choices. All are edited the same way and all are opaque to the
daemon.

`alerts` and `notifications` are the two the daemon *does* interpret, because the
push sender applies the master switch and quiet hours before any browser tab is
alive to filter. A malformed write to those is refused; a malformed write to the
others is stored.
