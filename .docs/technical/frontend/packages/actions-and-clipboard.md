# Frontend: Action rails, prompts, skills, and clipboard

Index: `../packages.md`.
Design: `../../../design/features/ui.md`, `../../../design/features/prompt-library.md`.

## Actions system

`commandRail.ts`, `railScope.ts`, `railLayout.ts`, `railDrag.ts`, `railReattach.ts`, `RailEditor.tsx`,
`ActionEditorModal.tsx`, `ActionsTab.tsx`, `PromptsTab.tsx`,
`PromptTemplateEditor.tsx`, `promptRail.ts`, `promptTitles.ts`,
`railKeyRepeat.ts`, `RailRepeatKey.tsx`, `railPadGesture.ts`, `RailPad.tsx`, `railModifiers.ts`,
`railVoice.ts`

The model modules do not touch the DOM outside the editors and `railDrag.ts`.
Rendering stays with the hosts: `TerminalPane.tsx` for the Action rail, `ActionsTab.tsx` for Actions.

### `commandRail.ts` - catalog and layout model

The browser-free model for the shared Action catalog, normalization, layout resolution, the storage blob, and one-way migration from older formats.
It keeps identity and behavior apart from placement, with independent Desktop and Mobile rail layouts.
The v3 normalized shape contains only `strip`; v2 `panel` rows are accepted as migration input, appended to the last rail row, and deduplicated against existing rail placements.

`RETIRED_RAIL_IDS`/`migratedRailItemId` is its durability table, the rail's counterpart to `drawerLayout.ts`'s `migratedTabTarget` and `keybindings.py`'s `_COMMAND_MIGRATIONS`.
A saved layout is device-local, per-Project, and arbitrarily old, and rows are normalized against the live catalog, so a retired id with no entry there is silently dropped from whichever row the operator dragged it into.
Resolution happens in `normalizeRow`, where placement is decided, so a replacement inherits the exact slot rather than landing appended to the end of row one.
Only a *replaced* built-in belongs in the table: one retired outright has nothing to migrate to, and dropping it is correct.

It also owns the three project-scope strengths, detected by shape like every other rail migration: inheritance, `mode: 'delta'` additive overlay (`resolveDeltaScope` - project items and trailing project rows over the live global layout, base wins id collisions), and full fork.

A delta carries two further overlays on the *shared* rows.
A `RailSplice` is an anchored insertion ("place X in shared row R after Y"), anchored by item id so a global reorder re-anchors it; `null` is the head of the row and a missing anchor falls back to its end, and the anchor is resolved against the **last** matching entry so chained splices rebuild a run of duplicates in order instead of reversing it.
A `RailHide` is its subtractive mirror and takes every occurrence of an id in a row.
Hides are applied before splices, so a splice whose anchor this project also hides falls back rather than vanishing with it.

**The rule `resolveDeltaScope` enforces, because every ownership decision downstream reads off it: within one row an id is either the project's or the definition's, never both.**
A splice is applied only when its id cannot also arrive from the row's definition - project-owned, absent from the definition, or hidden by this project (which is how a project-local reorder is said).
The resolution therefore reports per-row *ownership* (`projectPlacements`, `hiddenEntries`) rather than positions, which is what lets an arbitrarily edited row be split back apart.

### `railScope.ts` - the scope router

`resolveRail` returns the effective config plus ownership sets.
`applyScopedRail` splits an edited effective config back to the scopes that own each row and item: shared rows to global, project rows/items and project placements to the delta, everything to a fork.
Its `unresolveSharedRow` is the split: entries `isProjectRailPlacement` calls the project's become splices anchored on whatever they were left sitting behind, hidden occurrences are written back into the definition at the indices they held there, and hides self-prune to the ones that actually applied.
The dedicated ops cover what a diff cannot route: creation, ownership-constrained placement, hiding, and detachment through `addProjectRailRow`, `addScopedRailItem`, `toggleScopedPlacement`, `removeScopedRailItem`, `hideScopedRailEntry`, `unhideScopedRailEntry`, and `detachProjectRail`.

**The invariant it enforces: a shared row's *definition* never contains a project-owned item.**
Only the resolved projection does, so every other project renders that row exactly as written.

### `railReattach.ts` - fork to delta

`planForkReattach` diffs a fork against the current global config and emits the delta that reproduces it; `applyForkReattach` writes it.
Floating (hide plus splice) is chosen per id from a count check and an LCS over what remains, so only the entries that have to leave the definition do.
The plan **verifies itself** against `resolveDeltaScope` and re-does any row that did not come back identical with every id floated, which always reproduces a row at the cost of no longer tracking global changes to it.
`issues` names what a delta cannot express and which global value wins: a renamed shared row, reordered shared rows, a project row interleaved between them, an action whose definition the fork edited.
Nothing calls it on its own - it is reached only from the fork scope's "Reattach to global…" control.

### The rest of the layer

- `railLayout.ts` is the editing algebra for placing, moving, rowing, copying a device rail, catalog add and delete, and two-dimensional drop indexing.
- `railDrag.ts` is the DOM drag controller the modal editor mounts: the `data-rail-row`/`data-reorder-id` contract, committed-config preview recompute, nearest-row drop resolution within `DROP_ROW_MARGIN`, and pointer capture on `document.body`.
  Its gesture layer is imported from `dragReorder.ts` rather than restated - activation constants, the touch-scroll cancel, and the native-`contextmenu` suppression all match `beginPointerDrag` in `App.tsx`, and drifting from them is what made the editor's mobile reorder unreliable (`design/features/workspace-layout.md` § pointer drag contract).
  Its `canDrop` gate is unset now that a delta can express a project action in a shared row; it stays because refusing-as-off-every-row is the drag's own vocabulary, not the scope rule that needed it.
- `ActionEditorModal.tsx` owns the standalone Configure Actions surface.
  It opens on Global unless the focused Project is already detached, and passes the focused Project separately so Global can offer a one-step detach-and-edit action.
- `RailEditor.tsx` renders one device's rail layout first, collapsed custom-action creation next, then the collapsed filterable catalog.
  Every catalog row expands into placement, appearance, backend visibility, and any custom behavior fields.
  Appearance uses the live icon registry and supports a visible-label override plus Automatic, Icon only, Label only, and Icon + label modes where an icon exists.
  Built-in behavior fields remain locked while built-in presentation and backend visibility persist through catalog normalization.
  In a project scope its chips carry the hide control and the ghost chips for what is hidden; in a fork scope its toolbar carries the reattach plan.
- `ActionsTab.tsx` renders the unified Actions drawer with device-local disclosure state for discovered Skills, Prompt templates, and Clipboard.
  A compact Configure command rail button above the sections opens the standalone editor.
  Skills and template rows insert or edit; they do not create a second pinning state.

### Arrow-key repeat

`railKeyRepeat.ts` is the browser-free tap/hold split for the four arrow keys.
A press sends nothing - the tap rides the button's own click, so the rail's pan can suppress it like any other item's.
Travelling `RAIL_PAN_SLOP_PX` ends the press's candidacy for repetition, and a hold that reaches the delay claims the pointer and marks its trailing click as already served.
`RailRepeatKey.tsx` is the button and the window-level pointer listeners that arbitration needs.
It is its own module because the rail's pointer capture retargets moves away from the button, so the behaviour is only provable against a real rail with real touches (`test/renderer/command-rail-keys.spec.ts`).

`isRepeatableRailKey` reads the catalog's own `repeatable` flag rather than a list of ids kept in the module, because a pad slot's default trigger mode reads the same flag: an arrow that repeated as a chip and not inside a pad would be a difference nobody chose.

### Pads

`railPadGesture.ts` is the browser-free gesture for a pad chip; `RailPad.tsx` is the element, its listeners, the dial and the haptics.
The model - orientations, directions, rings, slot bindings, trigger modes and `normalizeRailPad` - lives in `commandRail.ts`, so `railPadGesture.ts` imports from it and never the reverse.

**The fan opens upward and has no downward wedge.**
The rail is on the bottom edge of the screen, so the 180° above the finger is divided instead (plus `RAIL_PAD_SKIRT_DEG` at each end) and the lower half is the abort zone.
Every slot is a wedge: `padSlotKeys` enumerates positions and nothing else, and the retired `center` key is read only by `normalizeRailPad`, which carries its binding onto the first free wedge and is idempotent so fork-equality still holds.

**The fan is divided two ways, and they cost different things.**
`padWedgeCount` divides it by angle (1..`RAIL_PAD_MAX_WEDGES`), costing angular tolerance; `padRingCount` divides it by distance (1 or 2), costing fire-on-entry.
Wedges are the cheaper axis, so every shipped pad is one ring.
`RAIL_PAD_MAX_WEDGES` is 5 because five is ±22° - the tolerance that made an eight-way circle poor - and *not* because of target size, which is still comfortable at six.

**Slot keys are positional**: `` `${ring}:${wedge}` ``, or `PAD_CENTER`.
Named compass keys could not survive the wedge count becoming a choice, so `padWedgeName` derives a readable name from the wedge's centre angle instead.
`normalizeRailPad` reads the original compass spellings forever, mapping them onto positions - same durability rule as `RETIRED_RAIL_IDS`, because an unrecognised key is a binding the operator silently loses.

**The only threshold is distance.**
The gesture is live from the first pixel; the sole timer repeats an already-committed direction, and `RAIL_PAD_DIAL_DELAY_MS` governs only when the dial is *drawn*.
`RAIL_PAD_DEAD_RADIUS_PX` is the commit distance and is deliberately unrelated to the wedge sizes, which is what lets the targets be thumb-sized without the control being slow.

**The press and the origin are two different points, and conflating them is the bug the split exists to prevent.**
`RAIL_PAD_LIFT_PX` puts the fan's origin above the press, so a press that has not moved is already `lift` pixels from the origin.
`createRailPadGesture` therefore keeps both: `startX/startY` is the finger, and `originX/originY` is the finger lifted.
Everything geometric — wedge, ring, band, dial — is measured from the origin; the two readings that are genuinely about the finger are the `RAIL_PAN_SLOP_PX` axis arbitration and `RAIL_PAD_TAP_SLOP_PX`, which is what makes a release a tap.
That tap slop is a constant of its own precisely because the hub no longer sits under the finger and hub membership stopped answering "did this press move".

`railPadResolve` is the whole geometry: it biases the point back along the latched wedge's own centre line before reading its angle, which is one expression covering every boundary at every wedge count, then reads the ring off the raw radius with a margin that moves **both ways**.
A one-sided ring margin makes crossing outward free, which is the direction it happens by accident.
`railPadScaleFor` shrinks every radius to the room above the press — the lift is in the *denominator* it fits against, since it is part of the reach — and `peek()` exposes the resulting bands so the dial is drawn from exactly what the gesture is using.
`RailPadBands.lift` is carried on the bands rather than read from the constant for that reason: `RailPad.tsx` positions the dial at `clientY - bands.lift`, so drawing and gesture share one origin at every squeeze.
The invariant that makes the arrangement legible — `bands.lift < bands.dead`, so a press always opens *inside* the neutral hub — holds at every scale because both scale together and `RAIL_PAD_MIN_SCALE` never lets either reach a floor the other does not.

Arbitration reuses `pointerDragClaim` and adds nothing to `RailScroller` or the mobile recognizer.
A pad claims only the axes its bound wedges span; a single-axis pad defers to the pan, decided at `RAIL_PAN_SLOP_PX` so exactly one of them takes the pointer.
Three consequences are invisible to a unit test and are covered by `test/renderer/command-rail-pad.spec.ts` instead.
The chip needs `touch-action:none`, or the strip's own `pan-x` answers a horizontal drag natively and `pointercancel`s the press.
The press's end is announced through the gesture's `end` callback rather than the chip's `pointerup`, which by then belongs to whatever the finger has moved over.
And **a drag delivers no mouse events at all**, so the `onMouseDown` focus refusal every other chip relies on never runs - which is why `RailPad` captures `softKeyboardHolder()` at pointer-down and calls `restoreSoftKeyboard` on a frame after the gesture ends, the same shape `RailScroller` uses for its pan.
The spec asserts the missing `mousedown` directly, because a keyboard test alone passes with the fix removed: desktop Chromium never drops focus during a pad drag, so the spec blurs the field mid-gesture to reproduce what Android does.

Ownership rules the rest of the layer relies on:

- **A slot's mode belongs to the binding, not the Action** - and its default also belongs to the *ring count*.
  `defaultPadTriggerMode` returns `release` for every slot of a two-ring pad, because the near ring is unavoidably transit: reaching the far ring crosses it, so a near slot firing on entry would fire on every pass and again on the way back in.
  A ring you must pass through cannot be a fire-on-entry target. An explicit per-slot mode still wins.
- **`enter-repeat-far` arms a repeat by distance rather than dwell**, and is what a repeatable Action defaults to on a one-ring pad.
  It fires once on entry and streams only past `bands.ring`; `setBeyond` starts and stops the timer on band crossings and fires nothing either way, so transit costs nothing and a wiggle restarts the delay rather than resuming.
  It is a property of one slot rather than a second ring slot holding the same Action - the two-slot spelling fires the near one in transit, so the stream would arrive one send early.
  On a two-ring pad `railPadSlotMode` resolves it to plain `enter`, because the band there is already a different slot; the editor does not offer it.
- **`railPadBanded` decides whether a boundary exists at all**, and `railPadBands` is keyed on that rather than on the ring count - a second ring of slots and a push-out repeat want the same radii for different reasons.
- **Slots are stored canonically**: `normalizeRailPad` rebuilds them in `padSlotKeys` order, which is wedge-major within a ring so `railPadResolve` can address them arithmetically.
  A fork stores a copy of a shipped pad and `planForkReattach` asks whether the copy still equals the definition, so a pad authored in reading order and reloaded in canonical order would report as edited by an operator who changed nothing.
  The shipped pads are run through `normalizeRailPad` at their definition site for that reason, which is also why `RETIRED_RAIL_IDS` sits above `BUILTIN_RAIL` - the canonicalization migrates slot ids while the catalog is still being evaluated.
- **An unresolvable slot is kept, an unreachable one is dropped.** A slot naming an absent Action survives the round trip (it may simply be missing from *this* resolution); a slot key outside the pad's own wedge and ring counts cannot be reached at all and goes, which is how shrinking a pad releases what no longer has a position and how a stored `down` binding falls away.
- **`railPadSlotItemIds` is how the rest of the app sees through a pad.** `railVoice.ts` walks it so a padded arrow keeps its spoken alias, which matters because the default rail reaches all four arrows only through `padArrows`.
- **`railPadSlotLabel` is the only route to a wedge's face**, and it cannot land on a `title`.
  A title is a sentence written for a tooltip, and four of them across a dial is prose rather than four labels - which shipped once, from a fallback chain that reached for one.
- `updateRailPadSlot` and `setRailPadShape` in `railLayout.ts` are open to built-in pads, unlike `updateRailCatalogItem`.
  Reshaping carries bindings across position for position as far as the new shape has positions: growing keeps everything, shrinking drops the rest.

### Sticky modifiers

`railModifiers.ts` is the browser-free state machine (off → armed → locked → off) and the sequence algebra.
`applyRailModifiers` is safe to run over everything the rail sends: an unmodified sequence, an empty set, and a sequence with no encoding for the modifier asked for all pass through unchanged.

Terminals encode modified keys two ways and the rail sends both.
The CSI family carries a `1;n` parameter, and an already-modified sequence gains the new modifier rather than replacing it - which is the round trip the shipped `^Home` chip performs.
**The CSI final set is a closed list, not any letter**: back-tab is `ESC[Z`, has no `1;n` form, and a permissive matcher rewrote it to `ESC[1;5Z` - a sequence no terminal reads, from a chip that looked like it worked.
Everything else is bytes: Alt is an ESC prefix, Ctrl folds a single character onto its control code, Shift upper-cases, and Shift+Tab is back-tab.

`TerminalPane` resolves modified bytes where a chip is *rendered* rather than where it is sent, because `railKeyRepeat` captures its sequence at press and `RailPad` closes over its slot handlers - so consuming an armed modifier on the first send cannot pull it out from under the rest of a hold.

### Voice adaptation

`railVoice.ts` is the pure allowlisting adapter from entries placed on the focused session's current-device Action rail to acknowledged terminal action requests.
It admits explicitly voiced safe keys and Paste, and non-submitting configured agent skills and slash commands.
It requires explicit aliases before a configured command may submit, and admits no prompt, literal-text, destructive, or UI-only entry.

## Prompt templates

`promptRail.ts` holds the pure half of the rail's prompt items: key splitting, template lookup, `railItemLabel` (live title for an `autoLabel` button, stored label otherwise), and `activatePromptTemplate`, which both hosts call with their own `insert` - the pane's handle inside `TerminalPane`, the `mux:terminal-action` bus from the drawer.
`fetchPromptTemplates` deliberately never widens past one scope: it is the read a configured prompt action resolves against, and confining it is what stops a global layout referencing a Project template (`prompt-library.md`).
`promptTitles.ts` is the one lazily filled, scope-keyed cache those live titles come from, deduped while in flight and invalidated by any local library write; every reader gates it on whether the rows it is about to draw contain an auto-labelled prompt button, so a rail without one fetches nothing.
A failed read caches "nothing known" rather than staying uncached, because the completion event would otherwise re-trigger the read that just failed.

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

`clipboardHistory.ts`, `InteractionHud.tsx`, `insertTarget.ts`, `railClearance.ts`

Boot-installed copy capture (a `writeText` wrapper plus capture-phase copy/cut) with client-side dedupe, payload-free successful-copy feedback owned by an isolated HUD below `App.tsx` so clipboard gestures cannot re-render active editors or terminals, and pure last-focused-surface insert routing shared by every injecting surface.
The HUD is pinned to the viewport's bottom-right corner, which on a maximised window is where the terminal's command rail is, so it reads `--rail-clearance` from `railClearance.ts` to sit above the rail rather than on it (`layout-and-chrome.md`).

## Clipboard history section

`ClipboardPanel.tsx`

A section of the Actions tab: the drawer's ring browser, with filter, single-open row expansion with a per-entry text cache, per-row insert/copy/pin/forget, and the manual-copy fallback textarea.
Expansion is the row's tap action and inserting is an explicit control.
The expanded body opts out of capture, and the head and foot pin so a long entry keeps its own actions on screen.
Autofocus of the filter is gated on `hasSoftKeyboard()` **and on an `autoFocusToken`**: the section is on screen whenever Actions is, so focus follows a deliberate arrival (the `drawer.actions.clipboard` command, or the terminal strip's Clip key) rather than the render.

## Rail drop-ups

`RailDropup.tsx`, `ClipboardDropup.tsx`, `SkillsDropup.tsx`, `PromptsDropup.tsx`

`RailDropup.tsx` is the chrome only.

- Upward placement through `railOverlayPlacement.ts`, shared with the rail's overflow popover, and dismissal on outside pointer or Escape.
  It used to borrow `anchoredPopoverStyle` from the account switcher: the same *shape*, a different problem, since that one knows nothing about a phone's width budget or about the soft keyboard resizing the visual viewport and re-anchoring every fixed element under the transformed terminal surface.
- Repositioning on resize, on capture-phase scroll, and on `visualViewport` resize and scroll, since the rail is itself a horizontal scroller, a trigger can pan out from under a fixed popover, and the keyboard's open and close fire nothing else.
- Glass, at the shared `--rail-glass`: the panel, its sticky exits, and a hovered row. A drop-up row is transparent over that panel, so it is the *single-layer* composition that sets the number for every rail overlay (`layout-and-chrome.md`).
- `holdSoftKeyboard` on pointer-down, and an arrow-key walk in document order.
- The sticky first row that leads to the full drawer section, or a bar of them side by side when a picker has two exits.
The five-row cap is CSS (`--rail-dropup-rows` over a fixed `--rail-dropup-row`), so it is a height and never a slice - capping by count would make the sticky row the only route to a sixth entry, and it is also why a second exit shares the sticky bar rather than taking a list row.

A drop-up trigger may itself be inside the rail's overflow popover (`RailStrip.tsx`, `layout-and-chrome.md`), which is why `RailDropup` sits one z-index above it and why it anchors to its own trigger rather than to the rail.
The two exemptions that make the pairing work belong to the popover rather than to this component: the pointer that opens a drop-up is not an outside press for the panel behind it, and Escape belongs to the drop-up for as long as one is open.
Nothing here changes for that case, which is the point: the panel holds the same real chips the row does, so a picker opened from it is the same picker.

The three content components hold no chrome and no geometry.
`ClipboardDropup.tsx` lists the ring newest-first and inserts through the pane path without ever touching `navigator.clipboard`.
`SkillsDropup.tsx` is a second view of the same inventory `ActionsTab` renders - same endpoint, same `groupSkills` precedence, same `skillTitle`/`inventoryNote` caveats - flattened with the scope as a per-row tag, because two group headings would leave three of five rows for skills.
`PromptsDropup.tsx` is the same relationship to the library: same scoped read, same backend filter, `orderPromptTemplates` (favourites, recency, title) for the order, and `activatePromptTemplate` for the click, so insert-versus-fill-the-fields is decided in one place.
Its second sticky exit opens `PromptLibrary` on a blank template (`prompts.new` -> `startCreating`), which is where a picker of existing templates most often ends.
