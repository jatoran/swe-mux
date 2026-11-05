import { catalogRevision, t, locale as uiLocale } from './localization/index.ts'
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
  worktree?: string
}

const fileSize = (bytes: number) => bytes < 1024
  ? `${bytes} B`
  : bytes < 1024 * 1024
    ? `${(bytes / 1024).toFixed(1)} KiB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MiB`

export function ImageViewer({ projectId, path, revision, mime, width, height, frames, size, worktree }: Props) {
  const [fit, setFit] = useState(true)
  const [zoom, setZoom] = useState(1)
  const [failed, setFailed] = useState(false)
  const source = useMemo(() => {
    const params = new URLSearchParams({ path, revision })
    if (worktree) params.set('worktree', worktree)
    return `/api/projects/${encodeURIComponent(projectId)}/file/content?${params}`
  }, [projectId, path, revision, worktree, uiLocale.value, catalogRevision.value])

  useEffect(() => {
    setFailed(false)
    setFit(true)
    setZoom(1)
  }, [source])

  return <div class="image-viewer">
    <div class="image-toolbar">
      <span>{mime} · {width.toLocaleString(uiLocale.value)} × {height.toLocaleString(uiLocale.value)} · {fileSize(size)}{frames > 1 ? t("ImageViewer.16d00ad334", { value1: frames.toLocaleString(uiLocale.value) }) : ''}</span>
      <div>
        <button class={fit ? 'active' : ''} onClick={() => setFit(true)}>{t("ImageViewer.9f872ed43d")}</button>
        <button class={!fit && zoom === 1 ? 'active' : ''} onClick={() => { setFit(false); setZoom(1) }}>100%</button>
        <button disabled={fit || zoom <= .25} aria-label={t("ImageViewer.bc7b631a68")} onClick={() => setZoom(value => Math.max(.25, value - .25))}>−</button>
        <button disabled={fit || zoom >= 4} aria-label={t("ImageViewer.0e47f09a74")} onClick={() => setZoom(value => Math.min(4, value + .25))}>+</button>
      </div>
    </div>
    {failed ? <div class="resource-unavailable">{t("ImageViewer.c5a75d1cbd")}</div> : <div class={`image-stage ${fit ? 'fit' : 'actual'}`}>
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
