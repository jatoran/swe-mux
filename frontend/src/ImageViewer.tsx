import { useEffect, useMemo, useState } from 'preact/hooks'

type Props = {
  projectId: string
  path: string
  revision: string
  mime: string
  width: number
  height: number
  frames: number
  size: number
}

const fileSize = (bytes: number) => bytes < 1024
  ? `${bytes} B`
  : bytes < 1024 * 1024
    ? `${(bytes / 1024).toFixed(1)} KiB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MiB`

export function ImageViewer({ projectId, path, revision, mime, width, height, frames, size }: Props) {
  const [fit, setFit] = useState(true)
  const [zoom, setZoom] = useState(1)
  const [failed, setFailed] = useState(false)
  const source = useMemo(() => `/api/projects/${encodeURIComponent(projectId)}/file/content?path=${encodeURIComponent(path)}&revision=${encodeURIComponent(revision)}`, [projectId, path, revision])

  useEffect(() => {
    setFailed(false)
    setFit(true)
    setZoom(1)
  }, [source])

  return <div class="image-viewer">
    <div class="image-toolbar">
      <span>{mime} · {width.toLocaleString()} × {height.toLocaleString()} · {fileSize(size)}{frames > 1 ? ` · ${frames.toLocaleString()} frames` : ''}</span>
      <div>
        <button class={fit ? 'active' : ''} onClick={() => setFit(true)}>Fit</button>
        <button class={!fit && zoom === 1 ? 'active' : ''} onClick={() => { setFit(false); setZoom(1) }}>100%</button>
        <button disabled={fit || zoom <= .25} aria-label="Zoom out" onClick={() => setZoom(value => Math.max(.25, value - .25))}>−</button>
        <button disabled={fit || zoom >= 4} aria-label="Zoom in" onClick={() => setZoom(value => Math.min(4, value + .25))}>+</button>
      </div>
    </div>
    {failed
      ? <div class="resource-unavailable">The image changed or could not be decoded. Retry the file tab after it refreshes.</div>
      : <div class={`image-stage ${fit ? 'fit' : 'actual'}`}>
          <img
            src={source}
            alt={path.split('/').pop() || path}
            draggable={false}
            onError={() => setFailed(true)}
            style={fit ? undefined : { width: `${Math.max(1, Math.round(width * zoom))}px` }}
          />
        </div>}
  </div>
}
