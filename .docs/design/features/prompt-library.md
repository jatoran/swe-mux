# Universal prompt library

## What it is

Global and Project-scoped reusable text templates that can fill a focused terminal without
submitting, executing, or becoming automation.

## Contract

Reusable prompts are inert text templates, not automation. Global templates live under
`<data_dir>/prompts/`; portable Project templates live under `.swe-mux/prompts/`. Each Markdown
file has strict TOML frontmatter with schema version, stable UUID, title, tags, derived variable
names, compatible backends, and timestamps. Bodies are limited to 64 KiB and reject terminal
control characters.

The Project `prompt_library_scope` selects `off`, `global`, `project`, or `both`. Listing both
scopes preserves same-ID conflicts as separate, visibly labeled entries. Template edits and
deletes require the last file revision so external or concurrent changes fail rather than
overwrite. Favorites, recent-use time, and counts are bounded state in the mux data directory;
they do not modify portable files.

A listing may be widened past the focused Project (`all_projects=1`), which adds the Project
libraries of every registered Project whose own scope admits them.
That is a management read and is opt-in for a structural reason: the default listing is also
what command-rail prompt items resolve against, so widening it by default would let a global
layout reference a Project template.
Every returned template names its owning Project, and a write is routed at that owner rather
than at whichever Project is focused.
Widening does not widen conflicts either: a conflict is two templates the *focused* listing
returns under one stable ID, because that is the pair a `scope:id` key cannot choose between.
Two unfocused Projects holding copies of one file are not ambiguous, and are not flagged.

## Browser flow

The Actions drawer contains a Prompt templates section for browsing, inserting, and authoring templates beside Skills and Clipboard.
Each row is a title with its scope, tags and field count as pills on the same line, over a bounded two-line
excerpt of the body so templates remain distinguishable at a glance.
The pills share the title's row rather than taking one of their own: as a second dim line under the title they
inherited the shared clipboard row's `meta` grid area, which the excerpt also claims, and the two drew on top
of each other. The fix that had covered that was keyed on an ancestor class the drawer no longer emits, which
is why the row styling now keys off the row's own class.
The section is expanded by default, remembers its disclosure state on the device, and its Manage button opens the full responsive library.
The command palette, main menu, and that Manage button all open that same library.
The session context menu no longer does: it opened a whole surface of its own from a menu whose every other row acts on the session and closes, and the library is a palette command and a drawer tab away from wherever you already are (`ui.md`).
A separate `prompts.new` command opens it on a blank template, scoped to the focused session's Project, and is what the rail's Prompts drop-up uses for its `+ New` exit; starting in create mode is a property of the *opening*, so an ordinary open afterwards still lands on the list.

Templates are created and edited where they are used.
The drawer's New control and each row's Edit control open the form in place of the list, and the full library selects straight into the same form with no Edit mode to enter first.
Both surfaces render one shared editor (`PromptTemplateEditor.tsx`), so the two hosts differ in arrangement and never in what a template is; the library keeps the one thing a drawer column cannot do, which is the wide, cross-Project view.
Placeholder fields are derived from the body being typed, before any save, so a new `{{variable}}` gets a field immediately.
Saving stays explicit rather than automatic, because the revision contract means a write can be refused and an autosaving field would have nowhere to report that without discarding what was typed.
The drawer is dismissed by Escape, by a back gesture, and by a tap outside, none of which it can intercept the way a modal's close button can, so an open draft is mirrored on the device and restored when the editor reopens; a mirror whose revision no longer matches the file is dropped rather than replayed over someone else's save.
The whole library is also one tap from any terminal, through the rail's **Prompts** drop-up (`ui.md`), which lists what the focused session's scope admits in favourites-then-recency order and inserts without submitting.
Dedicated template buttons are added and arranged in Configure command rail.
Its second exit opens this library already on a blank template, because a picker of existing templates is where "I want one for this" is most often realised.

A template can also be added as a dedicated command-rail action (`ui.md`).
A `prompt` action item stores only the template's `scope:id` key and resolves the body from the library at click time, so editing a template updates every button that points at it and a button can never inject a stale copy.
Its **name** is a pointer on the same terms: a button added without a typed label carries `autoLabel` and renders the template's live title, so renaming a template renames its buttons, while a label somebody typed is never overridden.
The label stored beside the flag is the fallback for before the library has been read and for a template that has gone; the dangling case is reported when the button is pressed, where there is room to name it.
A template with no `{{variables}}` inserts directly and never submits.
A template with variables opens the Actions drawer with Prompt templates expanded and the template preselected, because there is nothing valid to inject until its fields are filled.
A key that no longer resolves, because the template was deleted or a Project scope was switched off, names the offending button instead of failing silently.
Scope confinement is structural: listing with no project returns global templates only, so a global Action layout cannot reference a Project template.
The Action editor keeps that confinement rather than warning about it, and answers the reachability problem the other way round - it opens on the Project the operator was standing in, whose listing is global plus that Project's own, and says in place why the Global scope lists fewer.
Each row in its template picker names the library it is in, so the two scopes are never told apart by title alone.
Search covers title, tags, and body and filters by the focused backend.
A template can be favorited, copied, edited, or filled through its `{{variable}}` fields with a read-only preview.
Successful local creates, edits, deletes, favorites, and uses invalidate every open prompt list.
The Actions drawer refreshes in place even while the full library is open over it.
Insert requests `insertText` from the focused pane and waits for its acknowledgement, which uses paste semantics (`features/terminal-input.md` — "Inserting authored text").
It never adds a newline, Enter, submit, or execute action.
A body that *begins* with a newline is the shape that made this dangerous: sent as the first character of a bracketed paste it is read as Enter by Codex, so the live `Tree` template submitted whatever the operator had half-typed before its own text arrived (measured 2026-08-22 against v0.149.0).
The builder lifts that leading run into the harness's own newline key ahead of the paste, so the template does what its author meant and the standing draft survives.
Insert also refuses outright when the target session is showing an approval or a question, because there the text answers the dialog rather than filling a composer — and the refusal reaches the button, which is why insertion waits for an acknowledgement instead of dispatching and walking away.
Editor changes use explicit Save/Discard and an in-app close confirmation.

Insert routing is **terminals-only** for prompts. Everything else that injects text (clipboard
history, note sends) lands in the last-focused surface, which may be a note or file editor; a
prompt template refuses that target and falls back to the focused terminal, or reports that there
is none. A template is written to be read by an agent, so landing one in whichever document was
touched last is a silent edit to that document rather than a misplaced paste.

Choosing a *recipient* is the one path that may submit.
A prompt row in the Actions drawer answers right-click and touch long-press with a target menu: any live Claude/Codex session in the current Project, or a new Claude/Codex session in that Project.
This uses the same filter as send-to-agent because a shell would run the template as commands.
A live session receives the body as a bracketed paste plus, unless the menu's toggle is turned off, the submit sequence.
A new session carries it as its first argv prompt and is therefore bounded by `ARGV_PROMPT_MAX_CHARS`, which the drawer checks before spawning rather than failing at launch.
Both reuse the delivery path in `App.deliverToAgent`, so there is one implementation of "text into a session that may not be mounted".
Picking a target for a template whose `{{variables}}` are still empty parks that target and expands the fields instead of sending a half-rendered body.
The fields' button then reads `Send to <target>` until dismissed with `Insert instead`.

## Key files

- `src/swe_mux/prompt_library.py`
- `frontend/src/PromptTemplateEditor.tsx`
- `frontend/src/PromptLibrary.tsx`
- `frontend/src/PromptsTab.tsx`
- `frontend/src/ActionsTab.tsx`
- `frontend/src/insertTarget.ts`
- `frontend/src/agentTargets.ts`
- `frontend/src/promptLibraryEvents.ts`
- `frontend/src/promptTemplates.ts`
