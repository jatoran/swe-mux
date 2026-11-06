import { dt } from './messages.ts'
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
  return <div class="demo-keymaps" role="group" aria-label={dt("demo.1134df1127")}>
    <span>{dt("demo.f0d66a79c1")}</span>
    {view.presets.map(preset => <button
      key={preset.id} aria-pressed={view.preset === preset.id} disabled={busy}
      onClick={async () => {
        setBusy(true); setError('')
        try { await selectDemoKeymap(preset.id) }
        catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
        finally { setBusy(false) }
      }}
    >{preset.id === 'vim' ? 'Vim' : preset.title}</button>)}
    <span class="demo-keymap-hint" role="status">{error || view.hint || dt("demo.5fe645f40b")}</span>
  </div>
}
