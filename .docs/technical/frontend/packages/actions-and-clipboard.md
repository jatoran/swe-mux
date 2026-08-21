# Frontend: Action rails, prompts, skills, and clipboard

Index: `../packages.md`.
Design: `../../../design/features/ui.md`, `../../../design/features/prompt-library.md`.

## Actions system

`commandRail.ts`, `railScope.ts`, `railLayout.ts`, `railDrag.ts`, `RailEditor.tsx`, `RailInlineEditor.tsx`,
`ActionEditorModal.tsx`, `ActionsTab.tsx`, `PromptsTab.tsx`, `PromptTemplateEditor.tsx`, `promptRail.ts`,
`railKeyRepeat.ts`, `RailRepeatKey.tsx`, `railVoice.ts`

The model modules do not touch the DOM outside the editors and `railDrag.ts`.
Rendering stays with the hosts: `TerminalPane.tsx` for the Action rail, `ActionsTab.tsx` for Actions.

### `commandRail.ts` - catalog and layout model

The browser-free model for the shared Action catalog, normalization, layout resolution, the storage blob, and one-way migration from the pre-layout format.
It keeps identity and behavior apart from placement, with independent Desktop and Mobile layouts, and retains the `strip`/`panel` storage keys for compatibility.

`RETIRED_RAIL_IDS`/`migratedRailItemId` is its durability table, the rail's counterpart to `drawerLayout.ts`'s `migratedTabTarget` and `keybindings.py`'s `_COMMAND_MIGRATIONS`.
A saved layout is device-local, per-Project, and arbitrarily old, and rows are normalized against the live catalog, so a retired id with no entry there is silently dropped from whichever row the operator dragged it into.
Resolution happens in `normalizeRow`, where placement is decided, so a replacement inherits the exact slot rather than landing appended to the end of row one.
Only a *replaced* built-in belongs in the table: one retired outright has nothing to migrate to, and dropping it is correct.

It also owns the three project-scope strengths, detected by shape like every other rail migration: inheritance, `mode: 'delta'` additive overlay (`resolveDeltaScope` - project items and trailing project rows over the live global layout, base wins id collisions), and full fork.

### `railScope.ts` - the scope router

`resolveRail` returns the effective config plus ownership sets.
`applyScopedRail` splits an edited effective config back to the scopes that own each row and item: shared rows to global, project rows and items to the delta, everything to a fork.
The dedicated ops cover what a diff cannot route - creation, ownership-constrained placement, and one-tap pinning: `addProjectRailRow`, `addScopedRailItem`, `toggleScopedPlacement`, `removeScopedRailItem`, `detachProjectRail`, and `pinSkill`/`pinPrompt` with their `pinned*Item` matchers.

**The invariant it enforces: a project-owned item never lands in a shared row**, whose global write would drop the id.

### The rest of the layer

- `railLayout.ts` is the editing algebra for placing, moving, rowing, copying a surface, catalog add and delete, and two-dimensional drop indexing.
- `railDrag.ts` is the DOM drag controller both editors mount: the `data-rail-row`/`data-reorder-id` contract, committed-config preview recompute, root pointer capture, and the `canDrop` gate.
- `ActionEditorModal.tsx` owns the standalone Configure Actions surface.
- `RailEditor.tsx` renders it progressively: one device's layouts first (defaulting to `currentProfile()`, with a Desktop/Mobile switch at every width), collapsed custom-action creation, then the collapsed filterable catalog whose rows expand into labelled placement and backend checkboxes plus custom-item editing, with the dismissible first-open callout and the Preview-as backend dimmer.
- `RailInlineEditor.tsx` is the in-place rail editor the pane gear opens: the same chips, drag, keyboard moves, and scoped commits rendered inside the terminal pane's rail area, with a per-row picker and hand-offs to the modal.
- `ActionsTab.tsx` renders the unified Actions drawer with device-local disclosure state for Quick actions, discovered Skills, and Prompt templates.
  Quick actions is the current device's configured `panel` surface and may intentionally repeat a skill or template also visible in the complete sections; the Skills and template rows carry the one-tap Pin toggles that call the `railScope.ts` pin ops.

### Arrow-key repeat

`railKeyRepeat.ts` is the browser-free tap/hold split for the four arrow keys.
A press sends nothing - the tap rides the button's own click, so the rail's pan can suppress it like any other item's.
Travelling `RAIL_PAN_SLOP_PX` ends the press's candidacy for repetition, and a hold that reaches the delay claims the pointer and marks its trailing click as already served.
`RailRepeatKey.tsx` is the button and the window-level pointer listeners that arbitration needs.
It is its own module because the rail's pointer capture retargets moves away from the button, so the behaviour is only provable against a real rail with real touches (`test/renderer/command-rail-keys.spec.ts`).

### Voice adaptation

`railVoice.ts` is the pure allowlisting adapter from entries placed on the focused session's current-device Action rail or Drawer layout to acknowledged terminal action requests.
It admits explicitly voiced safe keys and Paste, and non-submitting configured agent skills and slash commands.
It requires explicit aliases before a configured command may submit, and admits no prompt, literal-text, destructive, or UI-only entry.

## Prompt templates

`PromptTemplateEditor.tsx` is the one prompt-template form, rendered by both `PromptsTab.tsx` in the drawer and `PromptLibrary.tsx` in the modal.
`usePromptDraft` owns the draft, the derived placeholder list, the revision-checked write, and the owning-Project routing that lets the widened management listing edit a template whose Project is not focused.
`PromptDraftFields` and `PromptDraftActions` are the shared markup.
Its `persistKey` mirrors an unsaved drawer draft on the device, because the drawer's dismissals (Escape, back, tap-outside) cannot raise the confirmation a modal's close button can.

## Agent skills

`skills.ts`, the Skills section of `ActionsTab.tsx`

Typed inventory from `/api/sessions/{id}/skills` plus pure grouping, filter, and tooltip helpers.
Read-only by design: nothing here writes a skill, and the disclosure helpers (`inventoryNote`, the `new`/`explicit` flags) exist so a list that cannot be complete never renders as though it were.
Filtering is substring, not the palette's subsequence matcher, which would match nearly any query against long skill descriptions.

## Clipboard capture

`clipboardHistory.ts`, `InteractionHud.tsx`, `insertTarget.ts`

Boot-installed copy capture (a `writeText` wrapper plus capture-phase copy/cut) with client-side dedupe, payload-free successful-copy feedback owned by an isolated HUD below `App.tsx` so clipboard gestures cannot re-render active editors or terminals, and pure last-focused-surface insert routing shared by every injecting surface.

## Clipboard history section

`ClipboardPanel.tsx`

A section of the Actions tab: the drawer's ring browser, with filter, single-open row expansion with a per-entry text cache, per-row insert/copy/pin/forget, and the manual-copy fallback textarea.
Expansion is the row's tap action and inserting is an explicit control.
The expanded body opts out of capture, and the head and foot pin so a long entry keeps its own actions on screen.
Autofocus of the filter is gated on `hasSoftKeyboard()` **and on an `autoFocusToken`**: the section is on screen whenever Actions is, so focus follows a deliberate arrival (the `drawer.actions.clipboard` command, or the terminal strip's Clip key) rather than the render.

## Rail drop-ups

`RailDropup.tsx`, `ClipboardDropup.tsx`, `SkillsDropup.tsx`

`RailDropup.tsx` is the chrome only.

- Upward placement through `anchoredPopoverStyle`, and dismissal on outside pointer or Escape.
- Repositioning on resize and on capture-phase scroll, since the rail is itself a horizontal scroller and a trigger can pan out from under a fixed popover.
- `holdSoftKeyboard` on pointer-down, and an arrow-key walk in document order.
- The sticky first row that leads to the full drawer section.
The five-row cap is CSS (`--rail-dropup-rows` over a fixed `--rail-dropup-row`), so it is a height and never a slice - capping by count would make the sticky row the only route to a sixth entry.

The two content components hold no chrome and no geometry.
`ClipboardDropup.tsx` lists the ring newest-first and inserts through the pane path without ever touching `navigator.clipboard`.
`SkillsDropup.tsx` is a second view of the same inventory `ActionsTab` renders - same endpoint, same `groupSkills` precedence, same `skillTitle`/`inventoryNote` caveats - flattened with the scope as a per-row tag, because two group headings would leave three of five rows for skills.
