import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { Dropdown } from './Dropdown'
import { projectDropdownOptions } from './projectOptions'
import { useModalFocus } from './modalFocus'
import { StateIndicator } from './StateIndicator'
import { useSessionRowConfig } from './sessionRowPrefs'
import {
  agentTargetName, agentTargets, backendFromTargetKey, defaultNewTarget, newTargetKey,
  retargetForProject, sessionIdFromTargetKey, sessionTargetKey,
} from './agentTargets'
import { describeReadiness, wordReasons } from './deliveryReadiness'
import { enqueueMessage } from './queueApi'
import type { Project, Session } from './types'
import type { MessageScope } from './noteSelection'
import { harnessDisplayName, promptDeliveryHarnesses } from './harnessRegistry'

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
  /** Present only for a complete selection captured from a Project-owned note. The picker
   *  calls it after the payload has been accepted, never on cancel, refusal, or error. */
  removeSelectionAfterSend?: () => void
}

export type SendToAgentTarget =
  | { kind: 'new'; backend: string; projectId: string }
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
  const rowConfig = useSessionRowConfig()
  const [projectId, setProjectId] = useState(request.projectId)
  const [message, setMessage] = useState(request.message)
  const [target, setTarget] = useState(() =>
    defaultNewTarget(projects.find(item => item.id === request.projectId)),
  )
  const [submit, setSubmit] = useState(true)
  const [removeSelection, setRemoveSelection] = useState(!!request.removeSelectionAfterSend)
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
  const agents = useMemo(() => agentTargets(sessions, projectId), [sessions, projectId, uiLocale.value, catalogRevision.value])
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
  const verdict = describeReadiness(readiness)
  // The refusal belongs to one exact message on one target; switching targets demotes the
  // retry back to a fresh send (the staged message stays in the old target's queue).
  const activeRefusal = refusal && chosen && refusal.sessionId === chosen.id ? refusal : null
  const confirmMode = !!activeRefusal && !activeRefusal.protected

  const finishAccepted = () => {
    try {
      if (removeSelection) request.removeSelectionAfterSend?.()
    } finally {
      onClose()
    }
  }

  const send = async () => {
    if (!message.trim()) {
      setError(t("SendToAgentPicker.1aa7fffc57"))
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
      finishAccepted()
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
          ? t("SendToAgentPicker.612bbb3cc7", { value1: wordReasons(result.reasons) }) +
            t("SendToAgentPicker.12bad6fff7")
          : t("SendToAgentPicker.ee31774ab7", { value1: wordReasons(result.reasons) }),
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
      finishAccepted()
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
      aria-label={t("SendToAgentPicker.1909a1d3b2")}
      onMouseDown={event => event.target === event.currentTarget && !busy && onClose()}
    >
      <section class="modal control-plane-modal send-agent-modal" ref={panel}>
        <div class="modal-heading">
          <div>
            <span>{t("SendToAgentPicker.6a9ee46886")}</span>
            <h2>{request.label}</h2>
          </div>
          <button type="button" aria-label={t("SendToAgentPicker.e1202e6b65")} disabled={busy} onClick={onClose}>
            ×
          </button>
        </div>
        <div class="control-plane-modal-body">
          <p>{t("SendToAgentPicker.50b376c7bb", { value1: (request.scope === 'selection') ? 'yes' : 'no' })}</p>
          <label>{rich("SendToAgentPicker.6cbb9c8057", { value1: <Dropdown
              value={projectId}
              disabled={busy}
              onChange={setProjectId}
              filter
              filterPlaceholder={t("SendToAgentPicker.2e0060c45e")}
              options={projectDropdownOptions(projects, item => ({ value: item.id, label: item.name, detail: item.root }))}
            /> })}</label>
          <div class="send-agent-targets" role="radiogroup" aria-label={t("SendToAgentPicker.10b0a3e6d9")}>
            {promptDeliveryHarnesses().map(harness=>option(newTargetKey(harness.name), null, t("SendToAgentPicker.3afe7e430c", { value1: harness.display_name }), t("SendToAgentPicker.47961913ea")))}
            {agents.map(session =>
              option(
                sessionTargetKey(session.id),
                <StateIndicator session={session} shape={rowConfig.dotShape} />,
                agentTargetName(session),
                `${session.backend} · ${session.state}${session.state_detail ? ` · ${session.state_detail}` : ''}`,
              ),
            )}
            {!agents.length && (<p class="send-agent-empty">{t("SendToAgentPicker.fb24432172")}</p>)}
          </div>
          {chosen && (<label class="send-agent-submit">
              <input
                type="checkbox"
                checked={submit}
                disabled={busy}
                onChange={event => setSubmit(event.currentTarget.checked)}
              />
              <span>{t("SendToAgentPicker.8d5a82d695")}</span>
            </label>)}
          {request.removeSelectionAfterSend && (<label class="send-agent-submit">
              <input
                type="checkbox"
                checked={removeSelection}
                disabled={busy}
                onChange={event => setRemoveSelection(event.currentTarget.checked)}
              />
              <span>{t("SendToAgentPicker.adbadd9b40")}</span>
            </label>)}
          {/* Same vocabulary as the Queue tab's readiness strip, from the same
              module: two hand-worded renderings of one verdict is how two surfaces
              come to describe one session differently. */}
          {unreadied && chosen && verdict && !activeRefusal && (<p class="modal-warning" role="status">{rich("SendToAgentPicker.089d5cf118", { value1: agentTargetName(chosen), headline: verdict.headline, value3: verdict.summary ? t("SendToAgentPicker.fa25e8025e", { summary: verdict.summary }) : '', value4: verdict.clears ? t("SendToAgentPicker.3ac0c78589", { clears: verdict.clears }) : '', value5: (verdict.protected) ? 'yes' : 'no' })}</p>)}
          {error && (<p class="modal-warning" role="alert">
              {error}
            </p>)}
          <label>{rich("SendToAgentPicker.7b7e7d7f08", { value1: <textarea
              class="send-agent-message"
              value={message}
              disabled={busy}
              onInput={event => setMessage(event.currentTarget.value)}
              aria-label={t("SendToAgentPicker.0955b0412e")}
            /> })}</label>
        </div>
        <div class="modal-footer">
          <span>{busy ? t("SendToAgentPicker.65ea0981f8") : t("SendToAgentPicker.1827891d1e", { value1: message.length.toLocaleString(uiLocale.value) })}</span>
          <button type="button" disabled={busy} onClick={onClose}>{t("SendToAgentPicker.19766ed6cc")}</button>
          {chosen && (<button
              type="button"
              disabled={busy || !message.trim()}
              title={t("SendToAgentPicker.c919ae9e5a")}
              onClick={() => void addToQueue()}
            >{t("SendToAgentPicker.34b25c1b96")}</button>)}
          <button
            type="button"
            class="primary"
            disabled={busy || !message.trim() || !!(activeRefusal && activeRefusal.protected)}
            onClick={() => void send()}
          >
            {chosen ? confirmMode ? t("SendToAgentPicker.3bdd9831a4") : t("SendToAgentPicker.f6f4688ff2") : t("SendToAgentPicker.e533494669", { value1: harnessDisplayName(backendFromTargetKey(target)) })}
          </button>
        </div>
      </section>
    </div>
  )
}
