import { useCallback, useEffect, useState } from 'preact/hooks'
import { api } from './api'
import {
  APPROVAL_MODES,
  MODE_DESCRIPTIONS,
  MODE_LABELS,
  approvalSummary,
  modeUnavailableReason,
} from './approvals'
import { isAgentBackend } from './harnessRegistry'
import type { ApprovalMode, ApprovalStatus, Session } from './types'

/**
 * The approval control, in the pane it governs.
 *
 * One collapsed line above the command rail, disclosed on click — the same
 * shape, and for the same reason, as the Queue pane's `auto:` strip: a control
 * that changes what mux does on the operator's behalf has to be readable at a
 * glance from the surface they are already looking at, and a brake reachable
 * only by opening an overlay is a brake nobody reaches in the moment they want
 * it.
 *
 * Rendered for every agent pane, including ones where no mode can be selected.
 * A control that disappears when unavailable teaches the operator it does not
 * exist; one that stays and says *why* teaches them what would make it work.
 */

type Props = {
  session: Session
}

/**
 * What the daemon's own snapshot says about this grant, as a change key.
 *
 * The record rides every `update` frame, so this is how the strip learns about
 * a change it did not make: a grant the daemon revoked at a conversation
 * rollover, an answer counted against the budget, a floor deferral. Without it
 * the strip only refreshed on its own mutations and would keep displaying
 * authority the daemon had already dropped.
 */
function policyKey(session: Session): string {
  const policy = session.approval_policy
  if (!policy) return `none:${session.agent_run_id ?? ''}`
  return [
    policy.mode, policy.run_id, policy.expires_at,
    policy.auto_approved, policy.floor_deferred, session.agent_run_id ?? '',
  ].join('|')
}

export function ApprovalStrip({ session }: Props) {
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState<ApprovalStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // Re-rendered on a slow tick so "22m left" ages without the pane re-rendering
  // on every PTY frame. One second is enough for a minute-granularity readout
  // and is what the countdown below is written against.
  const [now, setNow] = useState(() => Date.now() / 1000)

  const load = useCallback(async () => {
    // A shell pane has no permission requests to answer, and this component
    // mounts once per open pane: fetching for every one of them would put a
    // request per pane behind every workspace open for a strip that will not
    // render.
    if (!isAgentBackend(session.backend)) return
    try {
      setStatus(await api<ApprovalStatus>('GET', `/api/sessions/${session.id}/approvals`))
      setError('')
    } catch (cause) {
      setStatus(null)
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }, [session.id, session.backend])

  useEffect(() => {
    void load()
  }, [load, policyKey(session)])

  // The mode is also settable from the palette and the session context menu, and
  // an open strip showing the mode it had a moment ago is worse than one showing
  // nothing: it is the surface the operator would check to confirm the change
  // landed. Same broadcast pattern the Queue surfaces use for `mux:queue-changed`.
  useEffect(() => {
    const onChanged = (event: Event) => {
      const target = (event as CustomEvent<{ sessionId?: string }>).detail?.sessionId
      if (!target || target === session.id) void load()
    }
    window.addEventListener('mux:approvals-changed', onChanged)
    return () => window.removeEventListener('mux:approvals-changed', onChanged)
  }, [load, session.id])

  useEffect(() => {
    if (!open) return
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(timer)
  }, [open])

  const choose = useCallback(
    async (mode: ApprovalMode) => {
      setBusy(true)
      try {
        setStatus(
          await api<ApprovalStatus>('PUT', `/api/sessions/${session.id}/approvals`, {
            mode,
            set_by: 'pane',
          }),
        )
        setError('')
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause))
        // The refusal above is authoritative about the mode that is now in
        // force, which may not be the one the click asked for. Re-read rather
        // than leaving the selector showing the rejected choice.
        void load()
      } finally {
        setBusy(false)
      }
    },
    [session.id, load],
  )

  // A pane that is not an agent conversation has no permission requests to
  // answer, so the strip would be one permanent line of chrome saying nothing.
  if (!isAgentBackend(session.backend)) return null

  const policy = status?.policy
  const active = !!status && status.effective_mode !== 'wait'
  const summary = approvalSummary(status, now)

  return (
    <div class="approval-strip-wrap">
      <div class="approval-strip">
        <button
          type="button"
          class={`approval-summary${active ? ' approval-on' : ''}`}
          aria-expanded={open}
          title="What swe-mux answers for this conversation without asking you"
          onClick={() => setOpen(value => !value)}
        >
          <span aria-hidden="true">{open ? '▾' : '▸'}</span> approvals: {summary}
        </button>
      </div>
      {open && (
        <div class="approval-detail">
          {status ? (
            <>
              <div class="approval-modes" role="radiogroup" aria-label="Approval mode">
                {APPROVAL_MODES.map(mode => {
                  const blocked = modeUnavailableReason(status, mode)
                  const selected = status.policy.mode === mode
                  return (
                    <button
                      key={mode}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      class={`approval-mode${selected ? ' selected' : ''}`}
                      disabled={busy || !!blocked}
                      title={blocked || MODE_DESCRIPTIONS[mode]}
                      onClick={() => void choose(mode)}
                    >
                      {MODE_LABELS[mode]}
                    </button>
                  )
                })}
              </div>
              <p class="approval-note">{MODE_DESCRIPTIONS[status.policy.mode]}</p>
              {status.policy.mode === 'allowlisted' && (
                <p class="approval-note">
                  {status.policy.rules.length} rule
                  {status.policy.rules.length === 1 ? '' : 's'} from{' '}
                  {status.rules_source === 'project'
                    ? '.swe-mux/config.toml'
                    : "swe-mux's defaults"}
                  , fixed when the mode was set.
                </p>
              )}
              {policy && policy.last_request && (
                <p class="approval-note">Last approved: {policy.last_request}</p>
              )}
              {policy && policy.floor_deferred > 0 && (
                <p class="approval-note">
                  {policy.floor_deferred} request
                  {policy.floor_deferred === 1 ? '' : 's'} were left for you: pushes, credential
                  reads, and destructive commands are never auto-approved in any mode.
                </p>
              )}
              {status.unavailable && <p class="approval-note">{status.unavailable}</p>}
            </>
          ) : (
            <p class="approval-note">{error || 'Reading approval settings…'}</p>
          )}
          {error && status && <p class="approval-note approval-error">{error}</p>}
        </div>
      )}
    </div>
  )
}
