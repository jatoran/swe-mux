// The live keymap, published for components too deep to receive it as a prop.
//
// App owns the authoritative payload (fetched from `/api/keybindings?host=…` and
// refreshed on `configuration_changed`). A few leaf views - the note editor's
// outline button, which shows the chord bound to `note.outline` in its tooltip -
// sit many levels below that state and have no reason to accept it through the
// whole chain. This mirrors `currentNoteEditorSettings`: App pushes each fresh map
// in with `setKeybindingsStore`, and a leaf reads `currentKeybindings` once and
// subscribes to `KEYBINDINGS_EVENT` for later changes.
//
// What is published is the *resolved* map for this host plus the platform its
// labels are drawn for, because a leaf drawing "Ctrl+Shift+K" on macOS when the
// user's chord is ⌘⇧K is a worse hint than none.

import { bindingFor, type ResolvedBindings } from './keymap.ts'
import { displayChord } from './keys.ts'

export const KEYBINDINGS_EVENT = 'mux:keybindings'

export type KeymapSnapshot = {
  bindings: Readonly<ResolvedBindings>
  platform: string
}

let current: KeymapSnapshot = { bindings: {}, platform: 'win' }

export function currentKeybindings(): Readonly<ResolvedBindings> {
  return current.bindings
}

export function currentKeymap(): KeymapSnapshot {
  return current
}

export function setKeybindingsStore(bindings: ResolvedBindings, platform = 'win'): void {
  current = { bindings, platform }
  window.dispatchEvent(new CustomEvent<KeymapSnapshot>(KEYBINDINGS_EVENT, { detail: current }))
}

/** The readable chord for a command, or '' - what a tooltip or menu row shows. */
export function chordHint(commandId: string, snapshot: KeymapSnapshot = current): string {
  return displayChord(bindingFor(commandId, snapshot.bindings), snapshot.platform)
}
