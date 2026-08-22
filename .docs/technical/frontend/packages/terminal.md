# Frontend: terminal viewport and input

Index: `../packages.md`.
Design: `../../../design/features/terminal-input.md`.

## Terminal viewport

`TerminalPane.tsx`, `terminalInputDiagnostics.ts`, `terminalCaretPlacement.ts`, `composerText.ts`,
`composerInsertion.ts`,
`railKeyRepeat.ts`, `RailRepeatKey.tsx`, `terminalAttachments.ts`, `terminalProtocol.ts`,
`terminalViewport.ts`, `terminalRenderer.ts`, `terminalRenderDiagnostics.ts`, `terminalRenderPause.ts`,
`mobileInput.ts`

### `TerminalPane.tsx` owns

- xterm and WebSocket lifecycle, pointer gesture classification, and the redraw-clocked steering loop those decisions drive.
- File picker, drop, and paste attachment uploads, plus unicast draft references.
- Command-rail composition and pre-replay attach sizing.
- Renderer policy and fallback: DOM-only for mobile, Claude, and OMP.
- Replay, including the ring byte-cursor a reconnect offers as `since` so the daemon answers with a delta into an un-reset terminal instead of a fresh bounded window.
- Device-response classification and Codex late-color suppression, input, and responsive fitting.
- Jump-to-latest tail convergence, and which backends are also asked to move their own viewport.
- The forwarded-scroll estimate of an application-owned viewport's distance from its own tail: nothing reports it, so both drag directions are totalled and the chip is that total crossing a row.
- Opt-in render diagnostics.

### Pure models beneath it

`mobileInput.ts` is the touch-gesture arithmetic: drag target, cell/word/selection geometry, explicit sensitivity scaling, linear scroll conversion with `terminalScrollSteps` carrying the sub-row remainder, and application-scroll conversion driven by the harness registry's rows-per-report and minimum-report-interval profile.

`terminalInputDiagnostics.ts` owns content-free physical-input sequence correlation, bounded pending probes, native-event clock normalization, input-to-ack and input-to-render stage arithmetic, and the one shared browser main-thread stall clock.

`terminalCaretPlacement.ts` is the tap-routing and caret-steering arithmetic, and the single declared home for per-harness caret deviations.
Routing keys on the terminal's **measured mouse mode** rather than on a harness name, so any application that negotiated tracking is handed a forwarded touch tap and positions its own caret.
`caretResolverForBackend` is the registry the mouse-less harnesses (Codex, OMP, pi) are steered through; each resolver encodes one measured composer contract and the refusals that keep arrow keys out of that harness's pickers.
A resolver also returns that contract as a `ComposerRegion` rectangle, so reading a draft back and steering a caret into it cannot disagree about where the composer is.
`composerRegionForBackend` is the wider registry over the same measurements: it adds Claude, which negotiates mouse tracking and therefore needs no caret resolver but whose draft is still on screen.

`composerText.ts` is the pure assembly on top: dim cells dropped so a placeholder hint and a ghost completion never reach the clipboard, soft wraps rejoined at the measured wrap column, trailing box padding discarded, and `null` reserved for "this screen is not showing a readable draft" as distinct from an empty one.

`composerInsertion.ts` is the other direction: the bytes that put authored text *into* a composer, for every path that pushes text somebody wrote elsewhere - a prompt template, a skill, a clipboard entry, a dictated draft, a note selection - plus the native paste handlers, all of which reach it through `pasteIntoTerminal`.
It mirrors `composer_input.composer_insertion` in the daemon, and the pair is kept in step by the harness registry rather than by matching constants.
Its one non-obvious rule: a bracketed paste does not protect its own first character, and Codex reads a paste that *begins* with a newline as Enter (measured 2026-08-22 against v0.149.0), so a leading newline run is lifted out and written as the harness's `composerNewline` keys ahead of the paste.
`term.input` rather than prepending to the paste text, because xterm would rewrite it to a bare CR.

`terminalActions.ts` carries the request/acknowledgement contract for those insertions and `insertionRefusal`, the pure predicate that refuses one into a session showing an approval or a question - the same three sub-reasons `prompt_queue.PROTECTED_AWAITING_REASONS` names, because typed text there answers the dialog rather than filling a composer.
The refusal is why insertion is acknowledged at all: a dispatch-and-forget reported every insert as done, including the ones that never happened.

## Multi-device terminal input

`inputOwnership.ts`, `terminalLetterbox.ts`, `terminalWheelPacing.ts`

The pure client half of the daemon's arbitration.

- Epoch-ordered ownership frames, and gesture-versus-passive claim classification.
  The device *class* on a claim comes from `currentProfile()`, the same one the presence heartbeat reports, because the daemon compares them.
- The visible-and-focused gate on re-claiming is displacement only, never a refusal, and never twice inside the cooldown.
- What the take-over strip is allowed to speak for, and one-shot replay of refused keystrokes.
- `focusHeldByOtherField` - whether the keyboard is already in a text field outside a terminal - holds an attach's `term.focus()` off, because the socket opens on the daemon's clock and an attach landing mid-word in the sidebar filter or a rename dialog would swallow it.
- The font-size math for rendering a size another device chose.

`terminalWheelPacing.ts` is the ack-clocked wheel-report pacer for application-owned scrolling: batch release gated on the repaint arriving, capped queue, direction-reversal drop.
Its input is one wheel event per row, because xterm emits exactly one scroll report per wheel event whatever magnitude that event carries.

`TerminalPane.tsx` owns the socket, the DOM, and the take-over strip.

## Mobile soft keyboard

`mobileKeyboard.ts`, `mobileTerminalIme.ts`

`mobileKeyboard.ts` decides which focused element holds the on-screen keyboard up, and owns all three things a caller can do about it.
Each is expressed as a focus move, because Android exposes no keyboard API:

- `dismissSoftKeyboard` lowers it, and is the one blur.
- `holdSoftKeyboard` keeps a non-text control's press from taking focus off it, by cancelling the `mousedown` default.
- `restoreSoftKeyboard` hands it back to the field a gesture took it from.
  It is gated on `softKeyboardLost` so focus that moved to another text field stands, and abandoned outright when `softKeyboardDismissals` moved during the gesture, so a deliberate dismissal always wins.

`softKeyboardInputMode` is the terminal bridge's typing-intent gate: coarse-pointer synthetic actions refocus with `inputmode="none"` while the keyboard is down, whereas visible-keyboard preservation and explicit typing intent use text mode.

`softKeyboardInset` is the thresholded layout-minus-visual viewport difference `App.tsx` publishes as `--keyboard-inset`.
The keyboard overlays the layout rather than resizing it, so this is a slide distance and never a new height.

`softKeyboardVisualOffset` is the second half of that geometry and answers a different question: how far the browser has scrolled the visual viewport inside the layout one, published as `--visual-offset`.
Chrome scrolls it to lift a focused field above the keys, and with the document at `overflow:hidden` and the panels `position:fixed` there is nothing else it can scroll.
The inset alone is therefore wrong for any surface that *shortens*: it would stop that many pixels above the bottom of what the operator can see.
`style.css` derives `--keyboard-cover` as the inset minus the offset, and every shortening surface subtracts that instead; the terminal's slide keeps the raw inset because `--peek-offset` is denominated in it.
The clamp to the inset is load-bearing rather than defensive, since a larger value would grow a surface past full height.

The pre-arrival reservation lives in `App.tsx` beside the measurement, not here: `focusin` on a keyboard-raising field sets `--keyboard-pending` from `lastSoftKeyboardInset()` and `soft-keyboard-pending` on the root, so a panel has already shortened when the keys appear and the browser never needs to scroll.
It borrows `reservedKeyboardPx` and `RESERVE_INTENT_WINDOW_MS` from `keyboardReserve.ts` because it is the same bet on the same animation, and is retired outright once a real inset is measured rather than being shadowed by `soft-keyboard-open` - a keyboard dismissed while the field keeps focus would otherwise re-arm it over nothing.

Its predicates and shadow-root walk are pure and duck-typed.
`deepActiveElement` exists because `document.activeElement` retargets to a shadow host and the Continuity editor's `<textarea>` lives behind one.

`mobileTerminalIme.ts` converts Android composition replacements to incremental PTY deltas and owns the backend-aware Enter payload: agent newline (`ESC+CR`) versus shell submit (`CR`).
`TerminalPane.tsx` applies it consistently to `keydown`, `beforeinput`, and the value-delta fallback, and owns the fixed mobile agent Send end-cap plus sticky read/select mode.

## Preview links and views

`previewLinks.ts`, `PreviewPane.tsx`, `TerminalPane.tsx`

Loopback normalization, link dispatch, and a sandboxed registered viewport.
Plain-text URLs (the web-links addon) and OSC 8 hyperlinks (`term.options.linkHandler`) must share one handler: an OSC 8 link renders as a label with no URL text to regex, which is how a Codex-announced server had no clickable route to a Preview.

## Ended and recovered panes

`coldSession.ts`, `EndedPaneBanner.tsx`

`coldSession.ts` is the browser-free model: whether a session was recovered rather than merely observed, which way back it has, and the wording for a pane whose content is missing on purpose or older than the crash.
An agent gets Resume and a shell gets Restart, because replaying an agent's argv would start a fresh conversation while re-injecting the old one's `--session-id`.
`EndedPaneBanner.tsx` renders it above the terminal; `App.tsx` owns the actions it invokes and the layout rule that keeps an ended session's leaf (`../../../design/features/session-recovery.md`).
