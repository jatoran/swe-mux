import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import {
  cancelQueueMessage, fetchAutoStatus, fetchFleetQueue, scheduleStatus, senderLabel,
  type FleetQueueAuthor, type FleetQueueView, type QueueAutoStatus, type QueueMessage,
} from './queueApi'
import { useModalFocus } from './modalFocus'
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
  draft: 'waiting to be armed',
  armed: 'armed - waiting for order and readiness',
  blocked: 'refused; inspect the target queue',
  delivering: 'delivering...',
  sent: 'delivered',
  failed: 'failed - verify the terminal',
  cancelled: 'cancelled',
  stranded: 'target ended or was replaced',
}

const AUTHOR_SCOPES: { id: FleetQueueAuthor; label: string; title: string }[] = [
  { id: 'non_human', label: 'agents + automation', title: 'Messages authored by agents, rules, or observers' },
  { id: 'human', label: 'human', title: 'Messages authored by you locally or from a remote device' },
  { id: 'all', label: 'all authors', title: 'Messages authored by anyone' },
]

export function FleetQueue({ projects, initialProjectId, onOpenQueue, onClose }: Props) {
  // Opens on non-human authorship: the rows you wrote yourself are the ones you already
  // know about, so "all" would bury the only traffic this view exists to surface.
  const [author, setAuthor] = useState<FleetQueueAuthor>('non_human')
  const [projectId, setProjectId] = useState(initialProjectId || '')
  const [targetSessionId, setTargetSessionId] = useState('')
  const [view, setView] = useState<FleetQueueView>({ author: 'non_human', messages: [], targets: [] })
  const [auto, setAuto] = useState<QueueAutoStatus | null>(null)
  const [busyId, setBusyId] = useState('')
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
    () => new Map(projects.map(project => [project.id, project.name])),
    [projects],
  )
  const projectOptions = useMemo(() => {
    const ids = new Set(view.targets.map(target => target.project_id).filter((id): id is string => !!id))
    return [...ids].sort((a, b) => (projectNames.get(a) || a).localeCompare(projectNames.get(b) || b))
  }, [projectNames, view.targets])
  const targetOptions = useMemo(
    () => view.targets.filter(target => !projectId || target.project_id === projectId),
    [projectId, view.targets],
  )

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

  const promotion = auto?.promotion

  return (
    <div
      class="usage-layer fleet-queue-layer"
      role="dialog"
      aria-modal="true"
      aria-label="Fleet queue"
      onMouseDown={event => event.target === event.currentTarget && onClose()}
    >
      <section class="usage-panel fleet-queue-panel" ref={panel}>
        <header>
          <div>
            <span>QUEUE::FLEET</span>
            <strong>Every queued message across all sessions, by who wrote it</strong>
          </div>
          <div class="usage-header-actions">
            <button aria-label="Close fleet queue" onClick={onClose}>×</button>
          </div>
        </header>

        <div class="fleet-queue-controls">
          {/* Not `.queue-scope`: that class is sized for the drawer's 300px column, where
              equal-flex buttons and a bottom rule are right. Here it is a modal toolbar. */}
          <div class="fleet-queue-author-scope" role="tablist" aria-label="Message author">
            {AUTHOR_SCOPES.map(scope => (
              <button
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
            <span>Project</span>
            <select
              value={projectId}
              onChange={event => {
                const nextProject = event.currentTarget.value
                setProjectId(nextProject)
                if (targetSessionId && !view.targets.some(target =>
                  target.target_session_id === targetSessionId && (!nextProject || target.project_id === nextProject)
                )) setTargetSessionId('')
              }}
            >
              <option value="">All Projects</option>
              {projectOptions.map(id => <option key={id} value={id}>{projectNames.get(id) || id}</option>)}
            </select>
          </label>
          <label>
            <span>Session</span>
            <select
              value={targetSessionId}
              onChange={event => {
                const nextTarget = event.currentTarget.value
                setTargetSessionId(nextTarget)
                const target = view.targets.find(item => item.target_session_id === nextTarget)
                if (target?.project_id) setProjectId(target.project_id)
              }}
            >
              <option value="">All sessions</option>
              {targetOptions.map(target => (
                <option key={target.target_session_id} value={target.target_session_id}>
                  {target.label || target.target_session_id}
                </option>
              ))}
            </select>
          </label>
        </div>

        {/* Read-only: the controls that change this state are one gesture away on the
            Queue tab and on `autodelivery.pause`, not buried behind this overlay. */}
        <div class="fleet-queue-status" role="status">
          <span class={auto?.paused ? 'fleet-queue-paused' : ''}>
            auto-delivery {!auto ? '…' : !auto.master_enabled ? 'off for this install' : auto.paused ? 'paused (emergency stop)' : 'armed'}
          </span>
          {promotion && (
            <span class="queue-promotion">
              auto sends {promotion.auto_sends}/{promotion.required_sends} · proving{' '}
              {promotion.proving_days}/{promotion.required_days} days · unsafe {promotion.unsafe_reports}
            </span>
          )}
        </div>

        {error && <p class="usage-error" role="alert">{error}</p>}

        <main>
          <ul class="queue-list">
            {view.messages.map(message => {
              const busy = busyId === message.id
              return (
                <li key={message.id} class={`queue-item queue-item-${message.state}`}>
                  <div class="queue-item-meta">
                    <span class={`queue-state queue-state-${message.state}`}>{message.state}</span>
                    <span>{senderLabel(message) || 'from you'}</span>
                    <span class="queue-item-target">→ {message.target_label || message.target_session_id}</span>
                    {message.project_id && (
                      <span class="fleet-queue-project">Project: {projectNames.get(message.project_id) || message.project_id}</span>
                    )}
                    {scheduleStatus(message) === 'scheduled' && message.constraints?.not_before && (
                      <span class="queue-item-schedule">
                        scheduled {new Date(message.constraints.not_before * 1000).toLocaleString()}
                      </span>
                    )}
                    <span class="queue-item-note">{STATE_NOTE[message.state] || ''}</span>
                  </div>
                  <pre class={`queue-item-body${message.state === 'sent' ? ' queue-item-sent' : ''}`}>{message.body}</pre>
                  <div class="queue-item-actions">
                    <button
                      type="button"
                      disabled={busy || !message.target_live}
                      title={message.target_live ? 'Open this target session queue' : 'Target session is not live'}
                      onClick={() => { onOpenQueue(message.target_session_id); onClose() }}
                    >
                      Open queue
                    </button>
                    {['draft', 'armed', 'blocked'].includes(message.state) && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void run(message.id, () => cancelQueueMessage(message.id, 'revoked'))}
                      >
                        Revoke
                      </button>
                    )}
                    <button type="button" disabled={busy} onClick={() => void copyBody(message)}>Copy</button>
                  </div>
                </li>
              )
            })}
            {!view.messages.length && (
              <li class="queue-empty">No messages match these authorship and target filters.</li>
            )}
          </ul>
        </main>
      </section>
    </div>
  )
}
