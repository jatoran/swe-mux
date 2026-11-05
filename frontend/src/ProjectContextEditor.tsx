import { locale as uiLocale } from './localization/index.ts'
import { rich, t } from './localization/index.ts'
import { useEffect, useRef, useState } from 'preact/hooks'
import { api, type ApiError } from './api'

export type ProjectContext = {
  project_id: string; path: string; exists: boolean; revision: string
  markdown: string; max_bytes: number; generation_prompt: string
}

/**
 * The user-owned Project context Markdown, edited in place.
 *
 * It lived in the Timeline drawer tab, beside the per-run scan toggle, which
 * put a Project-wide setting inside a session-scoped surface: every session in
 * the Project showed the same editor, and the tab's actual job — reading this
 * conversation's timeline — competed with it for space. It belongs with the
 * Project's other settings.
 *
 * The revision guard is the point of the component. The file is user-owned and
 * may be edited outside swe-mux, so a save carries the revision it was read at
 * and a concurrent external edit is reported rather than overwritten. A local
 * draft is never discarded by a background refresh either.
 */
export function ProjectContextEditor({ projectId, busy, onError }: {
  projectId: string
  busy?: boolean
  onError?: (message: string) => void
}) {
  const [context, setContext] = useState<ProjectContext | null>(null)
  const [draft, setDraft] = useState('')
  const [message, setMessage] = useState('')
  const [saving, setSaving] = useState(false)
  const saved = useRef('')
  const live = useRef('')
  const revision = useRef('missing')

  useEffect(() => {
    let stale = false
    setContext(null); setDraft(''); setMessage('')
    saved.current = ''; live.current = ''; revision.current = 'missing'
    api<ProjectContext>('GET', `/api/projects/${projectId}/project-context`)
      .then(value => {
        if (stale) return
        setContext(value); setDraft(value.markdown)
        saved.current = value.markdown; live.current = value.markdown
        revision.current = value.revision
      })
      .catch(cause => { if (!stale) onError?.(cause instanceof Error ? cause.message : String(cause)) })
    return () => { stale = true }
  }, [projectId])

  const save = async () => {
    if (!context) return
    setSaving(true); setMessage('')
    try {
      const value = await api<ProjectContext>('PUT', `/api/projects/${projectId}/project-context`, {
        markdown: draft, revision: context.revision,
      })
      setContext(value); setDraft(value.markdown)
      saved.current = value.markdown; live.current = value.markdown
      revision.current = value.revision
      setMessage(t("ProjectContextEditor.b5c120b316"))
    } catch (cause) {
      const text = cause instanceof Error ? cause.message : String(cause)
      if ((cause as ApiError)?.detail?.code === 'revision_conflict') setMessage(t("ProjectContextEditor.0bb12a2719"))
      else onError?.(text)
    } finally { setSaving(false) }
  }

  const copyPrompt = async () => {
    if (!context) return
    try { await navigator.clipboard.writeText(context.generation_prompt); setMessage(t("ProjectContextEditor.b30303bc53")) }
    catch { setMessage(t("ProjectContextEditor.f59058f806")) }
  }

  if (!context) return <div class="scan-context-editor"><p>{t("ProjectContextEditor.f2d41df516")}</p></div>
  const bytes = new TextEncoder().encode(draft).length
  const disabled = !!busy || saving
  return <div class="scan-context-editor">
    <p>{rich("ProjectContextEditor.59ff26176c", { value1: <code>{context.path}</code> })}</p>
    <textarea value={draft} placeholder={t("ProjectContextEditor.6527ebfa11")}
      onInput={event => { live.current = event.currentTarget.value; setDraft(event.currentTarget.value); setMessage('') }} />
    <div>
      <span>{rich("ProjectContextEditor.7bc5a74dd0", { value1: bytes.toLocaleString(uiLocale.value), value2: context.max_bytes.toLocaleString(uiLocale.value) })}</span>
      <button disabled={disabled || draft === context.markdown || bytes > context.max_bytes} onClick={() => void save()}>{t("ProjectContextEditor.1509f561f2")}</button>
      <button disabled={disabled} onClick={() => void copyPrompt()}>{t("ProjectContextEditor.9a5d81ccb4")}</button>
    </div>
    {message && <small role="status">{message}</small>}
  </div>
}
