import { codeLabel } from './localization/codeLabels.ts'
import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import {
  cancelQueueMessage, decideControlRequest, decideSpawnRequest, deleteQueueMessage,
  fetchAutoStatus, fetchFleetQueue, scheduleStatus, senderLabel,
  type FleetQueueAuthor, type FleetQueueView, type QueueAutoStatus,
  type QueueMessage, type SpawnRequestRow,
} from './queueApi'
import { Dropdown } from './Dropdown'
import { byProjectName } from './projectOptions'
import { useModalFocus } from './modalFocus'
import { GrantButton } from './GrantGate'
import type { Project } from './types'

// The prompt queue's fleet-scoped review surface.
//
// The same `queue_messages` rows the Queue tab renders, partitioned by *who authored them*
// rather than by which agent they target. The Queue tab answers "what is staged for this
// agent, and should I send it". This answers "what has anything queued anywhere, and who
// wrote it" — a question no number of session-scoped tabs can answer, because it needs
// every target at once. Authorship is the axis because Phase 5 gave the queue writers
// other than the person reading it.
//
// A modal rather than a drawer tab, unlike Queue: nothing here delivers. Queue is docked
// beside its terminal because deciding whether to send is a judgement about an agent's
// live state, and the terminal is the only place that state is legible. This view has no
// send button, so it has nothing to keep on screen beside a terminal — it is opened,
// reviewed, and dismissed, like the process fleet and usage analytics.
//
// The install-wide auto-delivery brakes deliberately do NOT live here. A brake reachable
// only by opening a modal is a brake you cannot reach in the moment you want it; they live
// on the Queue tab's auto-delivery disclosure and on the `autodelivery.pause` command,
// which needs nothing open. This view reports their state and never owns it.

type Props = {
  projects: Project[]
  /** Preselects the Project filter — the Project menu opens this scoped to its own row. */
  initialProjectId?: string
  /** "Show me the target's queue", which is where a message can still be acted on. */
  onOpenQueue: (sessionId: string) => void
  onClose: () => void
}

const STATE_NOTE: Record<string, string> = {
  get draft() { return t("FleetQueue.f77d0650bd") },
  get armed() { return t("FleetQueue.4582cc8374") },
  get blocked() { return t("FleetQueue.537c4703ce") },
  delivering: 'delivering...',
  sent: 'delivered',
  get failed() { return t("FleetQueue.f3ea17604a") },
  cancelled: 'cancelled',
  get stranded() { return t("FleetQueue.61b539ec1a") },
}

const AUTHOR_SCOPES: { id: FleetQueueAuthor; label: string; title: string }[] = [
  { id: 'non_human', get label() { return t("FleetQueue.820768c032") }, get title() { return t("FleetQueue.c52f9bbabe") } },
  { id: 'human', get label() { return t("FleetQueue.79a5478768") }, get title() { return t("FleetQueue.5cc8292a19") } },
  { id: 'all', get label() { return t("FleetQueue.f8a0d56a78") }, get title() { return t("FleetQueue.d05a071457") } },
]

export function FleetQueue({ projects, initialProjectId, onOpenQueue, onClose }: Props) {
  // Opens on non-human authorship: the rows you wrote yourself are the ones you already
  // know about, so "all" would bury the only traffic this view exists to surface.
  const [author, setAuthor] = useState<FleetQueueAuthor>('non_human')
  const [projectId, setProjectId] = useState(initialProjectId || '')
  const [targetSessionId, setTargetSessionId] = useState('')
  const [view, setView] = useState<FleetQueueView>({
    author: 'non_human', messages: [], spawn_requests: [], spawn_request_errors: [],
    control_requests: [], targets: [],
  })
  const [auto, setAuto] = useState<QueueAutoStatus | null>(null)
  const [busyId, setBusyId] = useState('')
  const [deleteConfirmId, setDeleteConfirmId] = useState('')
  const [error, setError] = useState('')
  const alive = useRef(true)
  const panel = useRef<HTMLElement>(null)
  useModalFocus(panel, onClose)

  const refresh = useCallback(async () => {
    try {
      const [nextView, policy] = await Promise.all([
        fetchFleetQueue(author, { projectId, targetSessionId }),
        fetchAutoStatus(),
      ])
      if (!alive.current) return
      setView(nextView)
      setAuto(policy)
      setError('')
    } catch (cause) {
      if (alive.current) setError(cause instanceof Error ? cause.message : String(cause))
    }
  }, [author, projectId, targetSessionId])

  useEffect(() => {
    alive.current = true
    void refresh()
    const onQueueChanged = () => void refresh()
    window.addEventListener('mux:queue-changed', onQueueChanged)
    window.addEventListener('mux:events-connected', onQueueChanged)
    return () => {
      alive.current = false
      window.removeEventListener('mux:queue-changed', onQueueChanged)
      window.removeEventListener('mux:events-connected', onQueueChanged)
    }
  }, [refresh])

  const projectNames = useMemo(
    () => new Map(projects.map(project => [project.id, project.name])), [projects, uiLocale.value, catalogRevision.value])
  const projectOptions = useMemo(() => {
    const ids = new Set(view.targets.map(target => target.project_id).filter((id): id is string => !!id))
    for (const request of view.spawn_requests) ids.add(request.project_id)
    for (const request of view.control_requests) ids.add(request.project_id)
    return byProjectName([...ids], id => projectNames.get(id) || id)
  }, [projectNames, view.control_requests, view.spawn_requests, view.targets, uiLocale.value, catalogRevision.value])
  const targetOptions = useMemo(
    () => view.targets.filter(target => !projectId || target.project_id === projectId), [projectId, view.targets, uiLocale.value, catalogRevision.value])

  const run = async (identity: string, action: () => Promise<unknown>) => {
    setBusyId(identity)
    setError('')
    try {
      await action()
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusyId('')
    }
  }

  const copyBody = async (message: QueueMessage) => {
    try {
      await navigator.clipboard.writeText(message.body)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  const copyPrompt = async (request: SpawnRequestRow) => {
    try {
      await navigator.clipboard.writeText(request.prompt)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  const promotion = auto?.promotion

  return (
    <div data-dialog-id="fleet"
      class="usage-layer fleet-queue-layer"
      role="dialog"
      aria-modal="true"
      aria-label={t("FleetQueue.60c2b52761")}
      onMouseDown={event => event.target === event.currentTarget && onClose()}
    >
      <section class="usage-panel fleet-queue-panel" ref={panel}>
        <header>
          <div>
            <span>{t("FleetQueue.0683040cf3")}</span>
            <strong>{t("FleetQueue.2e9f92af46")}</strong>
          </div>
          <div class="usage-header-actions">
            <button aria-label={t("FleetQueue.35c0ae4fb6")} onClick={onClose}>×</button>
          </div>
        </header>

        <div class="fleet-queue-controls">
          {/* Not `.queue-scope`: that class is sized for the drawer's 300px column, where
              equal-flex buttons and a bottom rule are right. Here it is a modal toolbar. */}
          <div data-tab-group="FleetQueue.4a0734833f" class="fleet-queue-author-scope" role="tablist" aria-label={t("FleetQueue.4a0734833f")}>
            {AUTHOR_SCOPES.map(scope => (
              <button data-tab-id={scope.id}
                key={scope.id}
                type="button"
                role="tab"
                aria-selected={author === scope.id}
                class={author === scope.id ? 'active' : ''}
                title={scope.title}
                onClick={() => setAuthor(scope.id)}
              >
                {scope.label}
              </button>
            ))}
          </div>
          <label>
            <span>{t("FleetQueue.9859597853")}</span>
            <Dropdown
              value={projectId}
              filter
              filterPlaceholder={t("FleetQueue.2e0060c45e")}
              onChange={nextProject => {
                setProjectId(nextProject)
                if (targetSessionId && !view.targets.some(target =>
                  target.target_session_id === targetSessionId && (!nextProject || target.project_id === nextProject)
                )) setTargetSessionId('')
              }}
              options={[
                { value: '', get label() { return t("FleetQueue.cdd1b88308") } },
                ...projectOptions.map(id => ({ value: id, label: projectNames.get(id) || id })),
              ]}
            />
          </label>
          <label>
            <span>{t("FleetQueue.6959b41595")}</span>
            <Dropdown
              value={targetSessionId}
              onChange={nextTarget => {
                setTargetSessionId(nextTarget)
                const target = view.targets.find(item => item.target_session_id === nextTarget)
                if (target?.project_id) setProjectId(target.project_id)
              }}
              options={[
                { value: '', get label() { return t("FleetQueue.78648d4d66") } },
                ...targetOptions.map(target => ({
                  value: target.target_session_id,
                  label: target.label || target.target_session_id,
                })),
              ]}
            />
          </label>
        </div>

        {/* Read-only: the controls that change this state are one gesture away on the
            Queue tab and on `autodelivery.pause`, not buried behind this overlay. */}
        <div class="fleet-queue-status" role="status">
          <span class={auto?.paused ? 'fleet-queue-paused' : ''}>{rich("FleetQueue.19b3675b8b", { value1: !auto ? '…' : !auto.master_enabled ? t("FleetQueue.b57f160c0f") : auto.paused ? t("FleetQueue.358a4d279a") : t("FleetQueue.dc083dbe05") })}</span>
          {/* The emergency pause is a state to read here (its controls are on the Queue tab),
              but "off for this install" is not a state — it is a switch nobody has turned on,
              and the reader is one click from it. */}
          {auto && !auto.master_enabled && <GrantButton id="queue.autoDelivery"
            onGranted={refresh}>{t("FleetQueue.b67bc0ed9e")}</GrantButton>}
          {promotion && (<span class="queue-promotion">{rich("FleetQueue.4beb1c328d", { auto_sends: promotion.auto_sends, required_sends: promotion.required_sends, proving_days: promotion.proving_days, required_days: promotion.required_days, unsafe_reports: promotion.unsafe_reports })}</span>)}
        </div>

        {error && <p class="usage-error" role="alert">{error}</p>}
        {!!view.spawn_request_errors.length && (<p class="usage-error" role="alert">{rich("FleetQueue.a656f61559", { length: view.spawn_request_errors.length })}</p>)}

        <main>
          <ul class="queue-list">
            {!targetSessionId && view.spawn_requests.map(request => {
              const busy = busyId === request.id
              const pending = request.status === 'pending'
              return (
                <li key={`spawn:${request.project_id}:${request.id}`} class={`observation-request${pending ? '' : ' done'}`}>
                  <div class="observation-request-head">
                    <span class="observation-request-tag">{t("FleetQueue.5e2f6b11ae")}</span>
                    <span>{request.from_name || request.from_session || t("FleetQueue.647936ec3e")}</span>
                    {request.backend && <span>{request.backend}</span>}
                    {request.model && <span class="observation-request-model">{rich("FleetQueue.93b522f810", { model: request.model })}</span>}
                    <span class="fleet-queue-project">{rich("FleetQueue.b1c7dfe207", { value1: projectNames.get(request.project_id) || request.project_name || request.project_id })}</span>
                    <span class="observation-request-status">{codeLabel(request.status)}</span>
                  </div>
                  {request.reason && <p class="observation-request-reason">{request.reason}</p>}
                  <pre class="observation-request-prompt">{request.prompt}</pre>
                  <div class="observation-request-actions">
                    {pending ? <>
                      <button
                        type="button"
                        class="primary"
                        disabled={busy}
                        onClick={() => void run(request.id, () => decideSpawnRequest(request.project_id, request.id, 'approve'))}
                      >{t("FleetQueue.57de86e073")}</button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void run(request.id, () => decideSpawnRequest(request.project_id, request.id, 'dismiss'))}
                      >{t("FleetQueue.48845bff33")}</button>
                    </> : (<span>{request.status === 'approved' ? t("FleetQueue.a6a7c4b6ed") : t("FleetQueue.701b07dbed")}</span>)}
                    <button type="button" disabled={busy} onClick={() => void copyPrompt(request)}>{t("FleetQueue.ffc64b8bb8")}</button>
                  </div>
                </li>
              )
            })}
            {!targetSessionId && view.control_requests.map(request => {
              const busy = busyId === request.id
              const pending = request.status === 'pending'
              const verb = request.action === 'interrupt' ? 'interrupt' : 'end'
              return (
                <li key={`control:${request.project_id}:${request.id}`} class={`observation-request${pending ? '' : ' done'}`}>
                  <div class="observation-request-head">
                    <span class="observation-request-tag">{rich("FleetQueue.0078a87cf4", { verb: verb })}</span>
                    <span>{request.from_name || request.from_session || t("FleetQueue.647936ec3e")}</span>
                    <span>→ {request.target_name || request.target_session_id}</span>
                    <span class="fleet-queue-project">{rich("FleetQueue.b1c7dfe207", { value1: projectNames.get(request.project_id) || request.project_name || request.project_id })}</span>
                    <span class="observation-request-status">{request.outcome || request.status}</span>
                  </div>
                  {request.reason && <p class="observation-request-reason">{request.reason}</p>}
                  <div class="observation-request-actions">
                    {pending ? <>
                      <button
                        type="button"
                        class="primary"
                        disabled={busy}
                        onClick={() => void run(request.id, () => decideControlRequest(request.project_id, request.id, 'approve'))}
                      >{rich("FleetQueue.13aff5f4a8", { verb: verb })}</button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void run(request.id, () => decideControlRequest(request.project_id, request.id, 'dismiss'))}
                      >{t("FleetQueue.48845bff33")}</button>
                    </> : (<span>{request.status === 'approved' ? t("FleetQueue.805b51d3d8", { value1: request.outcome || t("FleetQueue.9e4cff8361") }) : t("FleetQueue.1c9abdc73f")}</span>)}
                  </div>
                </li>
              )
            })}
            {view.messages.map(message => {
              const busy = busyId === message.id
              return (
                <li key={message.id} class={`queue-item queue-item-${message.state}`}>
                  <div class="queue-item-meta">
                    <span class={`queue-state queue-state-${message.state}`}>{codeLabel(message.state)}</span>
                    <span>{senderLabel(message) || t("FleetQueue.7157b02b58")}</span>
                    <span class="queue-item-target">→ {message.target_label || message.target_session_id}</span>
                    {message.project_id && (<span class="fleet-queue-project">{rich("FleetQueue.b1c7dfe207", { value1: projectNames.get(message.project_id) || message.project_id })}</span>)}
                    {scheduleStatus(message) === 'scheduled' && message.constraints?.not_before && (<span class="queue-item-schedule">{rich("FleetQueue.cabc4287ed", { value1: new Date(message.constraints.not_before * 1000).toLocaleString(uiLocale.value) })}</span>)}
                    <span class="queue-item-note">{STATE_NOTE[message.state] || ''}</span>
                  </div>
                  <pre class={`queue-item-body${message.state === 'sent' ? ' queue-item-sent' : ''}`}>{message.body}</pre>
                  <div class="queue-item-actions">
                    <button
                      type="button"
                      disabled={busy || !message.target_live}
                      title={message.target_live ? t("FleetQueue.b99060a573") : t("FleetQueue.66a1d706b1")}
                      onClick={() => { onOpenQueue(message.target_session_id); onClose() }}
                    >{t("FleetQueue.976eba72da")}</button>
                    {['draft', 'armed', 'blocked'].includes(message.state) && (<button
                        type="button"
                        disabled={busy}
                        onClick={() => void run(message.id, () => cancelQueueMessage(message.id, 'revoked'))}
                      >{t("FleetQueue.87e6d00bbf")}</button>)}
                    <button type="button" disabled={busy} onClick={() => void copyBody(message)}>{t("FleetQueue.e21f935f11")}</button>
                    {message.state !== 'delivering' && (<button
                        type="button"
                        class={`danger${deleteConfirmId === message.id ? ' confirming' : ''}`}
                        disabled={busy}
                        onClick={() => {
                          if (deleteConfirmId !== message.id) {
                            setDeleteConfirmId(message.id)
                            return
                          }
                          setDeleteConfirmId('')
                          void run(message.id, () => deleteQueueMessage(message.id))
                        }}
                      >
                        {deleteConfirmId === message.id ? t("FleetQueue.280b10c89c") : t("FleetQueue.e2d0a54968")}
                      </button>)}
                  </div>
                </li>
              )
            })}
            {!view.messages.length && (!view.spawn_requests.length || !!targetSessionId) && (<li class="queue-empty">{t("FleetQueue.27483023ff")}</li>)}
          </ul>
        </main>
      </section>
    </div>
  )
}
