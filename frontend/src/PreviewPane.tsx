import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { captureUnavailableNote, type CaptureResult } from './previewCapture'
import { copyPreparedText } from './terminalClipboard'
import { isStaticPreview, previewLabel, type Preview } from './processFleet'

type Rect = { x: number; y: number; w: number; h: number }
type Clip = { x: number; y: number; width: number; height: number }

// Mirrors TerminalPane: coarse/small viewports get the manual copy overlay because
// clipboard writes across an async gap and over insecure (HTTP) contexts fail there.
const mobileClipboardFallback = () => window.matchMedia('(max-width: 760px), (pointer: coarse)').matches

export function PreviewPane({ preview, onClose }: { preview: Preview; onClose: () => void }) {
  const [refresh,setRefresh] = useState(0)
  const [viewport,setViewport] = useState(preview.viewport||'responsive')
  const [note,setNote] = useState('')
  const [busy,setBusy] = useState(false)
  const [selecting,setSelecting] = useState(false)
  const [rect,setRect] = useState<Rect|null>(null)
  const [prepared,setPrepared] = useState('')
  const [manual,setManual] = useState(false)
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const overlayRef = useRef<HTMLDivElement>(null)
  const manualRef = useRef<HTMLTextAreaElement>(null)
  const dragStart = useRef<{x:number;y:number}|null>(null)
  const proxyUrl=`/preview/${encodeURIComponent(preview.id)}/`
  const fallbackWidth = viewport === 'mobile' ? 390 : viewport === 'tablet' ? 834 : 1280
  const isStatic = isStaticPreview(preview)
  const label = previewLabel(preview)
  const [live,setLive] = useState(true)

  const copyReference = async (text: string) => {
    if (await copyPreparedText(text, manualRef.current)) {
      setPrepared(''); setManual(false); setNote('Screenshot reference copied to clipboard.')
      return
    }
    setPrepared(text); setManual(mobileClipboardFallback()); setNote('Clipboard blocked — copy the prepared text.')
    requestAnimationFrame(() => { manualRef.current?.focus(); manualRef.current?.select() })
  }
  const retryCopy = async () => {
    if (!prepared) return
    if (await copyPreparedText(prepared, manualRef.current)) { setPrepared(''); setManual(false); setNote('Copied.'); return }
    setManual(true)
    requestAnimationFrame(() => { manualRef.current?.focus(); manualRef.current?.select() })
  }

  const capture = async (opts?: { clip: Clip; width: number; height: number }) => {
    const width = opts?.width ?? fallbackWidth
    setBusy(true); setNote(opts ? 'Capturing region…' : 'Capturing preview…')
    try {
      const result = await api<CaptureResult>('POST', `/api/previews/${encodeURIComponent(preview.id)}/capture`,
        { viewport, width, ...(opts ? { height: opts.height, clip: opts.clip } : {}) })
      if (!result.available) { setNote(captureUnavailableNote(result)); return }
      if (result.error || !result.path) { setNote(result.error || 'Capture failed.'); return }
      const scope = result.region ? 'selected region of the' : 'current'
      await copyReference(`Here is the ${scope} ${isStatic?label:`${preview.host}:${preview.port}`} preview at ${width}px width — screenshot saved at ${result.path}. Please read that image file.`)
    } catch (err) { setNote(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }

  // Region select. The iframe renders 1:1 (no CSS scale), so on-screen pixels map
  // straight to page coordinates. The sandbox blocks reading the preview's scroll,
  // so a region is captured from the top of the page — scroll the preview to the
  // top first if you're selecting something below the fold.
  // Coordinates are computed relative to the overlay's own box, so the drawn
  // rectangle always tracks the cursor no matter how the iframe is centered. The
  // clip is converted to iframe (page) pixels only at capture time.
  const startDrag = (e: PointerEvent) => {
    e.preventDefault(); e.stopPropagation()
    const ov = overlayRef.current?.getBoundingClientRect()
    if (!ov) return
    dragStart.current = { x: e.clientX - ov.left, y: e.clientY - ov.top }
    setRect({ x: dragStart.current.x, y: dragStart.current.y, w: 0, h: 0 })
    try { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId) } catch { /* ignore */ }
  }
  const moveDrag = (e: PointerEvent) => {
    if (!dragStart.current) return
    e.preventDefault(); e.stopPropagation()
    const ov = overlayRef.current?.getBoundingClientRect()
    if (!ov) return
    const cx = e.clientX - ov.left, cy = e.clientY - ov.top
    const sx = dragStart.current.x, sy = dragStart.current.y
    setRect({ x: Math.min(sx, cx), y: Math.min(sy, cy), w: Math.abs(cx - sx), h: Math.abs(cy - sy) })
  }
  const endDrag = (e: PointerEvent) => {
    e.preventDefault(); e.stopPropagation()
    const box = rect
    const ov = overlayRef.current?.getBoundingClientRect()
    const frame = iframeRef.current?.getBoundingClientRect()
    dragStart.current = null; setRect(null); setSelecting(false)
    if (!box || !ov || !frame || box.w < 4 || box.h < 4) { setNote('Selection too small — try again.'); return }
    // Overlay-relative display coords → iframe-relative page coords for the clip.
    const dx = frame.left - ov.left, dy = frame.top - ov.top
    void capture({
      clip: { x: Math.max(0, box.x - dx), y: Math.max(0, box.y - dy), width: box.w, height: box.h },
      width: Math.round(frame.width),
      height: Math.round(frame.height),
    })
  }
  const cancelSelect = () => { dragStart.current = null; setRect(null); setSelecting(false); setNote('') }

  useEffect(() => {
    if (!selecting) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); cancelSelect() } }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [selecting])

  // A static preview has no HMR and no server to push a reload, so the daemon's
  // own file watcher is the only thing that can tell it the document moved on.
  // Hold the lease only while `live` is on: an unwatched directory costs the
  // daemon nothing, and a page holding state is not worth blowing away on every
  // keystroke-save, which is why this is a toggle and not the behaviour.
  const watchRoot = preview.doc_root_relative ?? ''
  useEffect(() => {
    if (!isStatic || !live) return
    const leaseId = `preview-${preview.id}-${Math.random().toString(36).slice(2)}`
    const renew = () => void api('PUT', `/api/projects/${encodeURIComponent(preview.project_id)}/watch`,
      { watch_id: leaseId, paths: [watchRoot], worktree: preview.worktree || undefined }).catch(() => {})
    renew()
    const timer = window.setInterval(renew, 30000)
    return () => {
      clearInterval(timer)
      void api('DELETE', `/api/projects/${encodeURIComponent(preview.project_id)}/watch/${encodeURIComponent(leaseId)}`).catch(() => {})
    }
  }, [isStatic, live, preview.id, preview.project_id, preview.worktree, watchRoot])
  useEffect(() => {
    if (!isStatic || !live) return
    const changed = (event: Event) => {
      const detail = (event as CustomEvent<{projectId:string;paths:string[];worktree?:string}>).detail
      if (detail.projectId !== preview.project_id) return
      if ((detail.worktree || '') !== (preview.worktree || '')) return
      // The lease is on the served directory, but the watcher reports the whole
      // Project, so a change elsewhere in the repo must not reload this page.
      if (!detail.paths.some(path => watchRoot ? path === watchRoot || path.startsWith(`${watchRoot}/`) : true)) return
      setRefresh(value => value + 1)
    }
    window.addEventListener('mux:project-files-changed', changed)
    return () => window.removeEventListener('mux:project-files-changed', changed)
  }, [isStatic, live, preview.project_id, preview.worktree, watchRoot])

  return <section class={`preview-pane viewport-${viewport}`}>
    <header><div><span>[PREVIEW]</span><strong title={preview.url}>{label}</strong><small>{preview.source}</small></div><nav><button onClick={()=>setViewport('mobile')}>mobile</button><button onClick={()=>setViewport('tablet')}>tablet</button><button onClick={()=>setViewport('responsive')}>fit</button><button onClick={()=>setRefresh(value=>value+1)}>refresh</button>{isStatic&&<button class={live?'active':''} aria-pressed={live} title={live?'Reloading when the served files change':'Not following file changes'} onClick={()=>setLive(value=>!value)}>live</button>}<button onClick={()=>void navigator.clipboard.writeText(new URL(proxyUrl,location.href).toString())}>copy</button><button onClick={()=>window.open(proxyUrl,'_blank','noopener,noreferrer')}>external</button><button aria-label="Close preview" onClick={onClose}>×</button></nav></header>
    <div class="preview-frame">
      <iframe ref={iframeRef} key={refresh} title={`Preview ${preview.url}`} src={proxyUrl} sandbox="allow-forms allow-modals allow-pointer-lock allow-popups allow-scripts" />
      {selecting && <div ref={overlayRef} class="preview-select-overlay" onPointerDown={startDrag} onPointerMove={moveDrag} onPointerUp={endDrag}>
        {rect && <div class="preview-select-rect" style={`left:${rect.x}px;top:${rect.y}px;width:${rect.w}px;height:${rect.h}px;border:2px solid #00e5ff;background:rgba(0,229,255,.28);box-shadow:0 0 0 1px #000,0 0 8px #00e5ff`} />}
        <span class="preview-select-hint">drag to select a region · Esc to cancel</span>
      </div>}
      {prepared && <div class="prepared-clipboard" role="status">
        <span>Copy the screenshot reference.</span>
        <button onClick={()=>void retryCopy()}>Copy</button>
        <button aria-label="Dismiss" onClick={()=>{setPrepared('');setManual(false)}}>×</button>
        <textarea ref={manualRef} class={manual?'manual':''} readOnly value={prepared} aria-label="Screenshot reference text" onFocus={e=>e.currentTarget.select()} />
      </div>}
    </div>
    <footer class="preview-rail">
      <button class="rail-send" disabled={busy} onClick={()=>void capture()}>⧉ capture</button>
      <button class={`rail-region ${selecting?'active':''}`} disabled={busy} onClick={()=>{ if(selecting){cancelSelect()}else{setSelecting(true);setNote('Drag over the preview to select a region.')} }}>▢ region</button>
      <small>{note||(isStatic?`served from ${preview.doc_root_relative||'the project root'} · copies a screenshot reference`:'session-owned loopback bridge · copies a screenshot reference')}</small>
    </footer>
  </section>
}
