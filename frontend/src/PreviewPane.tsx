import { useState } from 'preact/hooks'
import type { Preview } from './ProcessPanel'

export function PreviewPane({preview,onClose}:{preview:Preview;onClose:()=>void}) {
  const [refresh,setRefresh] = useState(0)
  const [viewport,setViewport] = useState(preview.viewport||'responsive')
  return <section class={`preview-pane viewport-${viewport}`}>
    <header><div><span>[PREVIEW]</span><strong title={preview.url}>{preview.host}:{preview.port}</strong><small>{preview.source}</small></div><nav><button onClick={()=>setViewport('mobile')}>mobile</button><button onClick={()=>setViewport('tablet')}>tablet</button><button onClick={()=>setViewport('responsive')}>fit</button><button onClick={()=>setRefresh(value=>value+1)}>refresh</button><button onClick={()=>void navigator.clipboard.writeText(preview.url)}>copy</button><button onClick={()=>window.open(preview.url,'_blank','noopener,noreferrer')}>external</button><button aria-label="Close preview" onClick={onClose}>×</button></nav></header>
    <div class="preview-frame"><iframe key={refresh} title={`Preview ${preview.url}`} src={preview.url} sandbox="allow-forms allow-modals allow-pointer-lock allow-popups allow-same-origin allow-scripts" /></div>
    <footer>best-effort direct loopback preview · HMR/path proxy support arrives with the authenticated proxy</footer>
  </section>
}
