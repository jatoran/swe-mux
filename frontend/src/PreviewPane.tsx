import { useState } from 'preact/hooks'
import type { Preview } from './ProcessPanel'

export function PreviewPane({preview,onClose}:{preview:Preview;onClose:()=>void}) {
  const [refresh,setRefresh] = useState(0)
  const [viewport,setViewport] = useState(preview.viewport||'responsive')
  const proxyUrl=`/preview/${encodeURIComponent(preview.id)}/`
  return <section class={`preview-pane viewport-${viewport}`}>
    <header><div><span>[PREVIEW]</span><strong title={preview.url}>{preview.host}:{preview.port}</strong><small>{preview.source}</small></div><nav><button onClick={()=>setViewport('mobile')}>mobile</button><button onClick={()=>setViewport('tablet')}>tablet</button><button onClick={()=>setViewport('responsive')}>fit</button><button onClick={()=>setRefresh(value=>value+1)}>refresh</button><button onClick={()=>void navigator.clipboard.writeText(new URL(proxyUrl,location.href).toString())}>copy</button><button onClick={()=>window.open(proxyUrl,'_blank','noopener,noreferrer')}>external</button><button aria-label="Close preview" onClick={onClose}>×</button></nav></header>
    <div class="preview-frame"><iframe key={refresh} title={`Preview ${preview.url}`} src={proxyUrl} sandbox="allow-forms allow-modals allow-pointer-lock allow-popups allow-scripts" /></div>
    <footer>session-owned loopback bridge · HTTP + WebSocket/HMR · mobile safe</footer>
  </section>
}
