# Continuity ask: raise the Android keyboard only on typing intent

**Delivered in SDK 0.2.21 and corrected in SDK 0.2.35.**
swe-mux currently vendors SDK 0.2.35 as `frontend/vendor/continuity-editor-0.2.35.tgz`
(sha256 `af0ceb46f9ffd78c16b0e61bee9280202027970c075cc6d3773ed15c01068fc1`, 475128 bytes).
That is the **rebuilt** 0.2.35 tarball; an earlier build of the same version shipped as
`a45856c9…` and is superseded, so verify the digest rather than the version string alone.
SDK 0.2.21 introduced the `inputmode="none"` gate that keeps a dismissed keyboard down during selection, but applying the gate during every selection gesture also dismissed an already-visible keyboard.
SDK 0.2.35 tracks typing intent and visual-viewport keyboard occlusion per editor, so long-press selection and selection-handle adjustment preserve the keyboard state they found.
Where there is no evidence either way the answer is "keyboard down", which preserves 0.2.21 exactly: a keyboard dismissed with the system back gesture still cannot be re-raised by a selection gesture.
When a selection exists with the keyboard down, the first tap inside that selection raises the keyboard without collapsing the range or removing its action bar; a later tap may collapse or reposition it normally.
Desktop remains untouched and no host change is required.

The platform half is still unverified — headless Chrome has no IME to raise, so the device steps
below remain the only proof. The rest of this document is the original ask, kept because it
records why a focus-based fix could not have worked.

Written against `@continuity-editor/editor` 0.2.20.

## The report

On a phone, with the Android keyboard already dismissed, long-press-and-drag to select text in a
note raises the keyboard.
The wanted behavior is that a selection gesture leaves the keyboard down, and only a tap that
means "type here" raises it.

## Why this is not the bug 0.2.18 fixed

0.2.18 removed every unwanted `focus()` on the touch path, and 0.2.20 still has that shape.
The reading holds up against the shipped source:

- `pointer_gesture.js` `applyPrimaryPointerCapture` returns early for `pointerType === "touch"`,
  so pointerdown neither focuses, captures, nor prevents the default.
- `component_pointer.js` `completeProjectedPointerClick` focuses only under
  `gesture.pointerType === "touch" && gesture.isTap`.
- `applyLongPressSelection` reaches the textarea through `commitProjectedSelection` →
  `applySelectionToInput`, which sets a selection range and does not focus.
- The remaining touch `focus()` sites are `insertText()`, the paste button, and
  `clipboard_bridge.copySelection()`. The last is only reached when
  `navigator.clipboard.writeText` has already failed, which does not happen on swe-mux's
  origin (Tailscale Serve, HTTPS, secure context).

So no `focus()` call explains it.

This is also not a stale-bundle report, which is the trap this repository hits most often.
Verified before filing: the live daemon, `src/swe_mux/static/index.html`, and the frozen
`dist/swe-mux/_internal/swe_mux/static/index.html` all serve `assets/index-Dy281NbO.js`, and
`assets/continuity_wasm_bg-DFUWWeXO.wasm` is sha256-identical to the installed
`internal/continuity_wasm_bg.wasm` for 0.2.20.
The 0.2.18 fix is genuinely deployed.

## The mechanism this leaves

Android keyboard visibility is not a pure function of DOM focus.
Dismissing the keyboard with the system back gesture hides the IME **without blurring** the
focused element, so the editor's `<textarea>` is still `document.activeElement` afterwards.
Chrome then re-raises the IME for a touch that resolves against that same focused editable —
which is what a long-press selection inside the editor is.

No focus policy can close this, because focus never moves.
Not calling `focus()` is necessary and is already done; it is not sufficient.

## The ask

Gate the keyboard on typing intent by holding `inputmode="none"` on the internal textarea and
lifting it only where the editor already decides a touch was a typing tap.

- Default the input surface to `inputMode = "none"` on coarse pointers.
  Chrome does not raise the IME for a focused field in that state, regardless of how the touch
  resolved, which is exactly the property focus bookkeeping cannot provide.
- Lift it to the real input mode at the one existing site that already means "the user asked to
  type here": the `gesture.pointerType === "touch" && gesture.isTap` branch in
  `completeProjectedPointerClick`, immediately before `ctx.input.focus({ preventScroll: true })`.
  It must be set in that same trusted turn — Chrome requires user activation to show the IME.
- Restore `inputMode = "none"` when the editor claims a long-press
  (`applyLongPressSelection`, where `touchSelection.claimAt` succeeds) and when a selection-adjust
  handle is grabbed, so a gesture that begins as selection cannot end up raising it.
- Leave desktop untouched. Mouse and pen already focus on pointerdown, and `inputmode` has no
  effect there.

`inputmode` rather than `virtualkeyboardpolicy`: the policy attribute applies only to
`contenteditable` elements, and this input surface is a `<textarea>`.

## Interaction with the host

swe-mux passes props only and does not reach into the shadow root, so nothing here needs a host
change.
Two host paths deliberately raise the keyboard by inserting text and must keep doing so, because
both are typing intent expressed through a button rather than a tap:

- the `mux:paste` rail action, which calls `element.insertText(text)`;
- clipboard-history and prompt-template inserts routed to the focused editor.

`insertText()` already focuses; it should also lift `inputMode` for the same reason the resolved
tap does.

## How to verify

On a real Android device — the behavior does not reproduce in a desktop browser's touch
emulation, because that path does not run Chrome's IME adapter.

1. Open a note, tap into it, confirm the keyboard rises.
2. Dismiss the keyboard with the **system back gesture**, not by tapping elsewhere.
   This is the state that matters: the textarea is still focused.
3. Long-press a word and drag to extend the selection.
   Expected: the selection tracks the finger and the keyboard stays down.
4. Tap once in the text.
   Expected: the caret lands under the finger and the keyboard rises.
5. Repeat 2-3 with the selection-adjust handles.
   Expected: the keyboard stays down.
6. With a selection and the keyboard down, use the rail Paste action.
   Expected: text is inserted and the keyboard rises, as it does today.

## Adjacent, not part of this ask

`clipboard_bridge.copyCodeBlock` ends with `finally { hooks.refocus() }`, so copying a fenced
code block focuses the textarea unconditionally.
Under the gate above it would no longer raise the keyboard, which is the wanted outcome; without
the gate it is a second, narrower path to the same complaint.
