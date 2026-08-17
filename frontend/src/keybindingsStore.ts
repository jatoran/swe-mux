// The live chord → command map, published for components too deep to receive it as a prop.
//
// App owns the authoritative `keybindings` state (fetched from `/api/keybindings` and refreshed
// on `configuration_changed`). A few leaf views — the note editor's outline button, which wants
// to show the chord bound to `note.outline` in its tooltip — sit many levels below that state and
// have no reason to accept it through the whole chain. This mirrors `currentNoteEditorSettings`:
// App pushes each fresh map in with `setKeybindingsStore`, and a leaf reads `currentKeybindings`
// once and subscribes to `KEYBINDINGS_EVENT` for later changes.

export const KEYBINDINGS_EVENT = 'mux:keybindings'

let current: Readonly<Record<string, string>> = {}

export function currentKeybindings(): Readonly<Record<string, string>> {
  return current
}

export function setKeybindingsStore(bindings: Record<string, string>): void {
  current = bindings
  window.dispatchEvent(new CustomEvent<Record<string, string>>(KEYBINDINGS_EVENT, { detail: bindings }))
}
