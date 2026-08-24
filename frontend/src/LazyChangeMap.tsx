import { useEffect, useState } from 'preact/hooks'
import type { ComponentType } from 'preact'
import type { ChangeMapPaneProps } from './ChangeMapPane'

/**
 * The Change Map, fetched the first time one is opened.
 *
 * Sigma and Graphology are a WebGL graph renderer and a graph datastructure, and nothing
 * outside this one surface uses either. Imported statically they rode in the entry chunk,
 * so every page load — every phone, where the pane does not even draw a canvas — paid for
 * a renderer most sessions never open. The pane is only ever mounted by a deliberate act
 * (the drawer's Changes tab, or a map pinned as a workspace pane), which is exactly the
 * moment to fetch it.
 *
 * The stand-in keeps the pane's own box and header-less full height so the drawer does not
 * reflow when the real pane arrives, and the caption is delayed for the same reason the
 * editor's is: on a local chunk this window is a frame or two, and a message that appears
 * and disappears inside it reads as a flicker rather than as progress.
 */
export function LazyChangeMap(props: ChangeMapPaneProps) {
  const [Pane, setPane] = useState<ComponentType<ChangeMapPaneProps> | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let live = true
    void import('./ChangeMapPane')
      .then(module => { if (live) setPane(() => module.ChangeMapPane) })
      .catch(cause => { if (live) setError(cause instanceof Error ? cause.message : String(cause)) })
    return () => { live = false }
  }, [])
  if (error) return <div class="change-map-pane change-map-state error" role="alert">Change Map unavailable: {error}</div>
  if (!Pane) return <div class="change-map-pane change-map-state" aria-busy="true" aria-label="Change Map"><span>Preparing the Change Map…</span></div>
  return <Pane {...props} />
}
