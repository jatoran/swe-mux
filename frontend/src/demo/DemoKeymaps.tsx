import { useEffect, useState } from 'preact/hooks'
import { DEMO_KEYMAP_EVENT, keymapSnapshot, selectDemoKeymap } from './keymapControls.ts'

export function DemoKeymaps() {
  const [view, setView] = useState(keymapSnapshot)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const update = () => setView(keymapSnapshot())
    window.addEventListener(DEMO_KEYMAP_EVENT, update)
    return () => window.removeEventListener(DEMO_KEYMAP_EVENT, update)
  }, [])
  return <div class="demo-keymaps" role="group" aria-label="Try a keyboard preset">
    <span>Keys</span>
    {view.presets.map(preset => <button
      key={preset.id} aria-pressed={view.preset === preset.id} disabled={busy}
      onClick={async () => {
        setBusy(true); setError('')
        try { await selectDemoKeymap(preset.id) }
        catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
        finally { setBusy(false) }
      }}
    >{preset.title === 'Vim-flavoured' ? 'Vim' : preset.title}</button>)}
    <span class="demo-keymap-hint" role="status">{error || view.hint || 'Click a pane to try the keys.'}</span>
  </div>
}
