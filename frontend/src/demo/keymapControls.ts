import { api } from '../api.ts'
import { KEYBINDINGS_EVENT, chordHint } from '../keybindingsStore.ts'
import { displayChord } from '../keys.ts'
import { currentKeybindings } from '../keybindingsStore.ts'
import { demoLog } from './diagnostics.ts'
import { stop } from './director.ts'
import { KEYMAP_FIXTURE } from './keymapFixture.ts'
import { onMutation, state } from './store.ts'

export const DEMO_KEYMAP_EVENT = 'swemux-demo:keymap-changed'
export function keymapSnapshot() {
  const chosen = KEYMAP_FIXTURE.presets.find(item => item.id === (state.keymapPreset || 'swemux'))!
  const bindings = currentKeybindings()
  return {
    preset: state.keymapPreset || 'swemux',
    presets: [...KEYMAP_FIXTURE.presets].sort((a,b) => ['swemux','tmux','vscode','vim','emacs'].indexOf(a.id) - ['swemux','tmux','vscode','vim','emacs'].indexOf(b.id)).map(preset => ({ id: preset.id, title: preset.title })),
    hint: [
      ['pane.splitHorizontal', 'split right'], ['pane.next', 'next pane'],
    ].map(([command, label]) => {
      const preferred = chosen.prefix && Object.entries(bindings).find(([key, rules]) => key.startsWith(chosen.prefix + ' ') && rules.some(rule => rule.command === command))?.[0]
      const chord = preferred ? displayChord(preferred, KEYMAP_FIXTURE.platform) : chordHint(command)
      return chord ? `${chord}: ${label}` : ''
    }).filter(Boolean).join(' · '),
  }
}

export async function selectDemoKeymap(preset: string): Promise<void> {
  if (!KEYMAP_FIXTURE.presets.some(item => item.id === preset)) {
    throw new Error('Choose a keyboard preset from the list.')
  }
  stop('dismissed')
  try {
    await api('POST', '/api/keymap-preset', { preset })
    demoLog('keymap_selected', preset)
    // The outer toolbar took focus. Give keyboard input back to the active terminal.
    window.focus()
    document.querySelector<HTMLElement>('.terminal-pane.focused .xterm-helper-textarea')?.focus()
  } catch (error) {
    demoLog('keymap_failed', preset, error instanceof Error ? error.message : String(error), true)
    throw error
  }
}

export function installKeymapControls(): void {
  const publish = () => window.dispatchEvent(new CustomEvent(DEMO_KEYMAP_EVENT))
  onMutation(mutation => { if (mutation.kind === 'keymap-preset') publish() })
  window.addEventListener(KEYBINDINGS_EVENT, publish)
  Object.assign(window, { __demoControls: { keymap: keymapSnapshot, selectKeymap: selectDemoKeymap } })
}
