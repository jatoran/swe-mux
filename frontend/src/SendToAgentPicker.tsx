import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { useModalFocus } from './modalFocus'
import { stateDotClass } from './sessionStatus'
import {
  agentTargetName, agentTargets, backendFromTargetKey, defaultNewTarget, newTargetKey,
  retargetForProject, sessionIdFromTargetKey, sessionTargetKey,
} from './agentTargets'
import { enqueueMessage } from './queueApi'
import type { Project, Session } from './types'
import type { MessageScope } from './noteSelection'

// Where a note/markdown selection goes: a brand new agent, or one that is already running.
// The message is shown in full and stays editable — this is a user-initiated send, and the
// text that leaves is the text on screen.

export type SendToAgentRequest = {
  projectId: string
  /** The document the text came from: shown in the heading and quoted inside the message. */
  label: string
  scope: MessageScope
  /** The composed message, already bounded; the dialog lets the user edit it before sending. */
  message: string
}

export type SendToAgentTarget =
  | { kind: 'new'; backend: 'claude' | 'codex'; projectId: string }
  | {
      kind: 'session'
      session: Session
      submit: boolean
      /** Set on the retry after a not-safe refusal: the queued message to deliver with the
       *  explicit confirmation, instead of staging a second copy. `bodyChanged` means the
       *  dialog text was edited since it was staged, so the queue item is edited first —
       *  the body delivered is always the body on screen, revision-checked. */
      confirmQueued?: { messageId: string; revision: number; bodyChanged: boolean }
    }

/** What a send came to. `blocked` keeps the dialog open with the daemon's refusal reasons
 *  and (when not protected) arms the explicit "send anyway" retry. */
export type SendToAgentResult =
  | { status: 'done' }
  | { status: 'error'; error: string }
  | { status: 'blocked'; messageId: string; revision: number; reasons: string[]; protected: boolean }

type Props = {
  request: SendToAgentRequest
  projects: Project[]
  sessions: Session[]
  onClose: () => void
  onSend: (target: SendToAgentTarget, message: string) => Promise<SendToAgentResult>
}

export function SendToAgentPicker({ request, projects, sessions, onClose, onSend }: Props) {
  const panel = useRef<HTMLElement>(null)
  const [projectId, setProjectId] = useState(request.projectId)
  const [message, setMessage] = useState(request.message)
  const [target, setTarget] = useState(() =>
    defaultNewTarget(projects.find(item => item.id === request.projectId)),
  )
  const [submit, setSubmit] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // A not-safe refusal from the queue's send operation: the message is already staged
  // (blocked) and the retry delivers *it*, with confirmation, instead of a second copy.
  const [refusal, setRefusal] = useState<{
    sessionId: string; messageId: string; revision: number; body: string
    reasons: string[]; protected: boolean
  } | null>(null)
  useModalFocus(panel, onClose, !busy)

  const project = projects.find(item => item.id === projectId)
  const agents = useMemo(() => agentTargets(sessions, projectId), [sessions, projectId])
  // Switching project (or a target session ending under the dialog) must never leave a stale
  // session selected: sending to the wrong session is the one unrecoverable outcome here.
  useEffect(() => {
    setTarget(current => retargetForProject(current, agents, project))
  }, [projectId, agents, project])

  const chosenId = sessionIdFromTargetKey(target)
  const chosen = chosenId ? agents.find(item => item.id === chosenId) || null : null
  const readiness = chosen?.delivery_readiness
  // Advisory only: the queue's send operation is where a not-safe target is actually refused
  // and (when allowed) overridden — this warning just says what is likely to happen.
  const unreadied = !!chosen && !!readiness && readiness.state !== 'safe'
  // The refusal belongs to one exact message on one target; switching targets demotes the
  // retry back to a fresh send (the staged message stays in the old target's queue).
  const activeRefusal = refusal && chosen && refusal.sessionId === chosen.id ? refusal : null
  const confirmMode = !!activeRefusal && !activeRefusal.protected

  const send = async () => {
    if (!message.trim()) {
      setError('There is nothing to send.')
      return
    }
    setBusy(true)
    setError('')
    const payload: SendToAgentTarget = chosen
      ? {
          kind: 'session',
          session: chosen,
          submit,
          ...(confirmMode && activeRefusal
            ? {
                confirmQueued: {
                  messageId: activeRefusal.messageId,
                  revision: activeRefusal.revision,
                  bodyChanged: activeRefusal.body !== message,
                },
              }
            : {}),
        }
      : { kind: 'new', backend: backendFromTargetKey(target), projectId }
    const result = await onSend(payload, message)
    setBusy(false)
    if (result.status === 'done') {
      onClose()
      return
    }
    if (result.status === 'blocked') {
      setRefusal({
        sessionId: chosen?.id || '',
        messageId: result.messageId,
        revision: result.revision,
        body: message,
        reasons: result.reasons,
        protected: result.protected,
      })
      setError(
        result.protected
          ? `The queue refused delivery and this cannot be overridden (${result.reasons.join(', ')}). ` +
            'The message stays queued; it can be sent from the Queue tab once the target is free.'
          : `The target is not safe right now (${result.reasons.join(', ')}).`,
      )
      return
    }
    setRefusal(null)
    setError(result.error)
  }

  const addToQueue = async () => {
    if (!chosen || !message.trim()) return
    setBusy(true)
    setError('')
    try {
      await enqueueMessage(chosen.id, message, { armed: true })
      onClose()
    } catch (cause) {
      setBusy(false)
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  const option = (value: string, dot: JSX.Element | null, title: string, detail: string) => (
    <label key={value} class={`send-agent-option ${target === value ? 'active' : ''}`}>
      <input
        type="radio"
        name="send-agent-target"
        value={value}
        checked={target === value}
        disabled={busy}
        onChange={() => setTarget(value)}
      />
      <span class="send-agent-option-body">
        <strong>
          {dot}
          {title}
        </strong>
        <small>{detail}</small>
      </span>
    </label>
  )

  return (
    <div
      class="modal-layer control-plane-modal-layer"
      role="dialog"
      aria-modal="true"
      aria-label="Send to an agent session"
      onMouseDown={event => event.target === event.currentTarget && !busy && onClose()}
    >
      <section class="modal control-plane-modal send-agent-modal" ref={panel}>
        <div class="modal-heading">
          <div>
            <span>SEND::AGENT</span>
            <h2>{request.label}</h2>
          </div>
          <button type="button" aria-label="Close send to agent" disabled={busy} onClick={onClose}>
            ×
          </button>
        </div>
        <div class="control-plane-modal-body">
          <p>
            Sending the {request.scope === 'selection' ? 'selected text' : 'whole document'}. The
            message below is exactly what the agent receives, and nothing is sent until you say so.
          </p>
          <label>
            Target project
            <select
              value={projectId}
              disabled={busy}
              onChange={event => setProjectId(event.currentTarget.value)}
            >
              {projects.map(item => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <div class="send-agent-targets" role="radiogroup" aria-label="Send target">
            {option(newTargetKey('claude'), null, 'New Claude session', 'Starts in this project, opening beside this pane')}
            {option(newTargetKey('codex'), null, 'New Codex session', 'Starts in this project, opening beside this pane')}
            {agents.map(session =>
              option(
                sessionTargetKey(session.id),
                <span class={stateDotClass(session.state)} />,
                agentTargetName(session),
                `${session.backend} · ${session.state}${session.state_detail ? ` · ${session.state_detail}` : ''}`,
              ),
            )}
            {!agents.length && (
              <p class="send-agent-empty">
                No live Claude or Codex session in this project. Shell sessions are not offered:
                a paste would run as commands.
              </p>
            )}
          </div>
          {chosen && (
            <label class="send-agent-submit">
              <input
                type="checkbox"
                checked={submit}
                disabled={busy}
                onChange={event => setSubmit(event.currentTarget.checked)}
              />
              <span>Press Enter after inserting (off fills the composer and leaves it to you)</span>
            </label>
          )}
          {unreadied && chosen && readiness && !activeRefusal && (
            <p class="modal-warning" role="status">
              {agentTargetName(chosen)} is{' '}
              {readiness.state === 'blocked' ? 'not ready for input' : 'in an unknown state'}
              {readiness.reason ? `: ${readiness.reason}` : '.'} Sending will ask the queue to
              deliver; it re-checks and may ask you to confirm.
            </p>
          )}
          {error && (
            <p class="modal-warning" role="alert">
              {error}
            </p>
          )}
          <label>
            Message
            <textarea
              class="send-agent-message"
              value={message}
              disabled={busy}
              onInput={event => setMessage(event.currentTarget.value)}
              aria-label="Message sent to the agent"
            />
          </label>
        </div>
        <div class="modal-footer">
          <span>{busy ? 'sending…' : `${message.length.toLocaleString()} characters`}</span>
          <button type="button" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          {chosen && (
            <button
              type="button"
              disabled={busy || !message.trim()}
              title="Stage the message in the target's queue without delivering it"
              onClick={() => void addToQueue()}
            >
              Add to queue
            </button>
          )}
          <button
            type="button"
            class="primary"
            disabled={busy || !message.trim() || !!(activeRefusal && activeRefusal.protected)}
            onClick={() => void send()}
          >
            {chosen
              ? confirmMode
                ? 'Send anyway'
                : 'Send'
              : `Start ${backendFromTargetKey(target) === 'codex' ? 'Codex' : 'Claude'}`}
          </button>
        </div>
      </section>
    </div>
  )
}
