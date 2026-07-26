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

The command palette, main menu, and session context menu open the same responsive library.
A template can also be pinned to the command rail (`ui.md`): a `prompt` rail item stores only the
template's `scope:id` key and resolves the body from the library at click time, so editing a
template updates every button that points at it and a button can never inject a stale copy. A
template with no `{{variables}}` inserts directly (still never submitting); one with variables
opens the drawer's Prompts tab preselected, because there is nothing valid to inject until its
fields are filled. A key that no longer resolves — deleted template, or a Project scope switched
off — names the offending button instead of failing silently. Scope confinement is structural:
listing with no project returns global templates only, so a global rail cannot pin a Project
template.
Search covers title, tags, and body and filters by the focused backend. A template can be
favorited, copied, edited, or filled through its `{{variable}}` fields with a read-only preview.
Insert dispatches `insertText` to the focused xterm, which uses paste semantics. It never adds a
newline, Enter, submit, or execute action. Editor changes use explicit Save/Discard and an
in-app close confirmation.

## Key files

- `src/swe_mux/prompt_library.py`
- `frontend/src/PromptLibrary.tsx`
- `frontend/src/promptTemplates.ts`
