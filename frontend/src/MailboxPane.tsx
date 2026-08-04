import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import {
  cancelQueueMessage, fetchAutoStatus, fetchMailbox, reportUnsafeDelivery, scheduleStatus,
  senderLabel, setAutoPaused, type MailboxAuthor, type MailboxView, type QueueAutoStatus,
  type QueueMessage,
} from './queueApi'
import type { Project } from './types'

type Props = {
  projects: Project[]
  onOpenQueue: (sessionId: string) => void
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

const AUTHOR_SCOPES: { id: MailboxAuthor; label: string; title: string }[] = [
  { id: 'all', label: 'all authors', title: 'Messages authored by anyone' },
  { id: 'non_human', label: 'agents + automation', title: 'Messages authored by agents, rules, or observers' },
  { id: 'human', label: 'human', title: 'Messages authored by you locally or from a remote device' },
]

export function MailboxPane({ projects, onOpenQueue }: Props) {
  const [author, setAuthor] = useState<MailboxAuthor>('all')
  const [projectId, setProjectId] = useState('')
  const [targetSessionId, setTargetSessionId] = useState('')
  const [view, setView] = useState<MailboxView>({ author: 'all', messages: [], targets: [] })
  const [auto, setAuto] = useState<QueueAutoStatus | null>(null)
  const [busyId, setBusyId] = useState('')
  const [error, setError] = useState('')
  const alive = useRef(true)

  const refresh = useCallback(async () => {
    try {
      const [nextView, policy] = await Promise.all([
        fetchMailbox(author, { projectId, targetSessionId }),
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
    <div class="queue-pane mailbox-pane">
      <div class="queue-scope mailbox-author-scope" role="tablist" aria-label="Message author">
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

      <div class="mailbox-filters" aria-label="Mailbox filters">
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

      <div class="queue-mailbox-controls">
        <button
          type="button"
          class={auto?.paused ? 'primary' : 'danger'}
          disabled={busyId === 'auto'}
          title="Stops every automatic delivery immediately, on every session"
          onClick={() => void run('auto', async () => setAuto(await setAutoPaused(!auto?.paused)))}
        >
          {auto?.paused ? 'resume auto-delivery' : 'pause all auto-delivery'}
        </button>
        <button
          type="button"
          disabled={busyId === 'auto'}
          title="Record a delivery that should not have happened"
          onClick={() => void run('auto', async () => {
            await reportUnsafeDelivery('reported from the mailbox')
            setError('Recorded. Auto-delivery is paused and the proving period restarted.')
          })}
        >
          report unsafe delivery
        </button>
        {promotion && (
          <span class="queue-promotion">
            auto sends {promotion.auto_sends}/{promotion.required_sends} · proving{' '}
            {promotion.proving_days}/{promotion.required_days} days · unsafe {promotion.unsafe_reports}
          </span>
        )}
      </div>

      {error && <p class="queue-pane-error" role="alert">{error}</p>}

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
                  <span class="mailbox-project">Project: {projectNames.get(message.project_id) || message.project_id}</span>
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
                  onClick={() => onOpenQueue(message.target_session_id)}
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
    </div>
  )
}
