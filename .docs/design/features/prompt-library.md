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

## Browser flow

The Actions drawer contains a Prompt templates section for browsing and inserting templates beside Quick actions and Skills.
Each row shows a bounded two-line excerpt of the body so templates remain distinguishable without turning the drawer into the full editor.
The section is expanded by default, remembers its disclosure state on the device, and its Manage button opens the full responsive library.
The command palette, main menu, session context menu, and that Manage button all open the same editor modal.
A template can also be pinned to the Action rail or Quick actions (`ui.md`).
A `prompt` action item stores only the template's `scope:id` key and resolves the body from the library at click time, so editing a template updates every button that points at it and a button can never inject a stale copy.
A template with no `{{variables}}` inserts directly and never submits.
A template with variables opens the Actions drawer with Prompt templates expanded and the template preselected, because there is nothing valid to inject until its fields are filled.
A key that no longer resolves, because the template was deleted or a Project scope was switched off, names the offending button instead of failing silently.
Scope confinement is structural: listing with no project returns global templates only, so a global Action layout cannot pin a Project template.
Search covers title, tags, and body and filters by the focused backend.
A template can be favorited, copied, edited, or filled through its `{{variable}}` fields with a read-only preview.
Successful local creates, edits, deletes, favorites, and uses invalidate every open prompt list.
The Actions drawer refreshes in place even while the full library is open over it.
Insert dispatches `insertText` to the focused xterm, which uses paste semantics.
It never adds a newline, Enter, submit, or execute action.
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
- `frontend/src/PromptLibrary.tsx`
- `frontend/src/PromptsTab.tsx`
- `frontend/src/ActionsTab.tsx`
- `frontend/src/insertTarget.ts`
- `frontend/src/agentTargets.ts`
- `frontend/src/promptLibraryEvents.ts`
- `frontend/src/promptTemplates.ts`
