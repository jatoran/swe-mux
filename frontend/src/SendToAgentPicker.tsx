import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { useModalFocus } from './modalFocus'
import { stateDotClass } from './sessionStatus'
import {
  agentTargetName, agentTargets, backendFromTargetKey, defaultNewTarget, newTargetKey,
  retargetForProject, sessionIdFromTargetKey, sessionTargetKey,
} from './agentTargets'
import { ARGV_PROMPT_MAX_CHARS } from './noteSelection'
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
  | { kind: 'session'; session: Session; submit: boolean }

type Props = {
  request: SendToAgentRequest
  projects: Project[]
  sessions: Session[]
  onClose: () => void
  /** Resolves to '' on success, or to a message the dialog shows while staying open. */
  onSend: (target: SendToAgentTarget, message: string) => Promise<string>
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
  // The delivery contract asks for an explicit confirmation when readiness is blocked or
  // unknown, so the button says what it is about to do rather than implying the target is idle.
  const unreadied = !!chosen && !!readiness && readiness.state !== 'safe'
  // A new session carries its first prompt on the command line, which is bounded; a live
  // session is written to over the PTY and is not. Say so instead of failing at spawn time.
  const overArgvBound = !chosen && message.length > ARGV_PROMPT_MAX_CHARS

  const send = async () => {
    if (!message.trim()) {
      setError('There is nothing to send.')
      return
    }
    setBusy(true)
    setError('')
    const payload: SendToAgentTarget = chosen
      ? { kind: 'session', session: chosen, submit }
      : { kind: 'new', backend: backendFromTargetKey(target), projectId }
    const failure = await onSend(payload, message)
    setBusy(false)
    if (failure) setError(failure)
    else onClose()
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
          {unreadied && chosen && readiness && (
            <p class="modal-warning" role="status">
              {agentTargetName(chosen)} is{' '}
              {readiness.state === 'blocked' ? 'not ready for input' : 'in an unknown state'}
              {readiness.reason ? `: ${readiness.reason}` : '.'} Sending anyway is your call.
            </p>
          )}
          {overArgvBound && (
            <p class="modal-warning" role="status">
              {message.length.toLocaleString()} characters is too long to start a new session with
              (the limit is {ARGV_PROMPT_MAX_CHARS.toLocaleString()}, because a first prompt travels
              on the command line). Send it to a live session instead, or trim it.
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
          <button
            type="button"
            class="primary"
            disabled={busy || !message.trim() || overArgvBound}
            onClick={() => void send()}
          >
            {chosen
              ? unreadied
                ? 'Send anyway'
                : 'Send'
              : `Start ${backendFromTargetKey(target) === 'codex' ? 'Codex' : 'Claude'}`}
          </button>
        </div>
      </section>
    </div>
  )
}
