import { createPortal } from 'preact/compat'
import { useCallback, useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { anchoredPopoverStyle } from './providerAccountDisplay'
import {
  APPROVAL_MODES,
  MODE_DESCRIPTIONS,
  MODE_LABELS,
  approvalChipLabel,
  approvalSummary,
  modeUnavailableReason,
} from './approvals'
import { isAgentBackend } from './harnessRegistry'
import { useDismissLevel } from './modalFocus'
import type { ApprovalMode, ApprovalStatus, Session } from './types'

/**
 * The approval control, in the pane bar beside `tts:`.
 *
 * A chip rather than a strip. Both chips answer the same class of question —
 * "what is swe-mux doing for this session without being asked each time" — so
 * they belong in the same row, at the same weight, and the pane bar is the one
 * surface that is always visible while the pane is. The earlier full-width
 * disclosure above the command rail spent a permanent line on a control that is
 * `wait` almost all the time.
 *
 * The label is deliberately tiny (`appr:wait` / `appr:list` / `appr:ALL`) and the
 * detail lives in the drop-down, because the chip's job on a glance is only to
 * say whether authority is standing here at all.
 *
 * Rendered for every agent pane, including ones where no mode can be selected. A
 * control that disappears when unavailable teaches the operator it does not
 * exist; one that stays and says *why* teaches them what would make it work.
 */

type Props = {
  session: Session
}

/**
 * What the daemon's own snapshot says about this grant, as a change key.
 *
 * The record rides every `update` frame, so this is how the chip learns about a
 * change it did not make: a grant the daemon revoked at a conversation rollover,
 * an answer counted against the budget, a floor deferral. Without it the chip
 * only refreshed on its own mutations and would keep displaying authority the
 * daemon had already dropped.
 */
function policyKey(session: Session): string {
  const policy = session.approval_policy
  if (!policy) return `none:${session.agent_run_id ?? ''}`
  return [
    policy.mode, policy.run_id, policy.expires_at,
    policy.auto_approved, policy.floor_deferred, session.agent_run_id ?? '',
  ].join('|')
}

export function ApprovalChip({ session }: Props) {
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState<ApprovalStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // Ticked only while the menu is open, so "22m left" ages there without the
  // closed chip re-rendering once a second in every pane on screen.
  const [now, setNow] = useState(() => Date.now() / 1000)
  const wrap = useRef<HTMLDivElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const [menuStyle, setMenuStyle] = useState<Record<string, string>>({})

  const load = useCallback(async () => {
    // A shell pane has no permission requests to answer, and this component
    // mounts once per open pane: fetching for every one of them would put a
    // request per pane behind every workspace open for a chip that will not
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
  // a chip showing the mode it had a moment ago is worse than one showing
  // nothing: it is the surface the operator checks to confirm the change landed.
  // Same broadcast pattern the Queue surfaces use for `mux:queue-changed`.
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

  // Escape, the platform back gesture, and the mobile back swipe all close this
  // the same way every other dismissable level closes (`dismissStack.ts`).
  useDismissLevel(() => setOpen(false), open, 'approval-menu')

  // Anchored from the chip's viewport rect, because the menu is portalled to the
  // body rather than positioned inside the chip. It has to be: `.pane-voice` is a
  // horizontal scroller with a mask, and an `overflow-x: auto` ancestor clips an
  // absolutely-positioned descendant on *both* axes — the menu rendered fine and
  // was cropped to nothing, which looks exactly like a control that does not work.
  const position = useCallback(() => {
    const rect = wrap.current?.getBoundingClientRect()
    if (rect) {
      setMenuStyle(
        anchoredPopoverStyle(rect, true, { width: innerWidth, height: innerHeight }),
      )
    }
  }, [])

  // A pointer-down anywhere else closes it, which is what makes a chip menu feel
  // like a menu rather than a panel you have to click twice to leave. The menu is
  // portalled, so it is not inside `wrap` and has to be tested separately.
  useEffect(() => {
    if (!open) return
    position()
    const onDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (!wrap.current?.contains(target) && !menu.current?.contains(target)) setOpen(false)
    }
    const reposition = () => position()
    window.addEventListener('pointerdown', onDown, true)
    window.addEventListener('resize', reposition)
    // Capturing, so a pane or drawer scrolling under the chip moves the menu with
    // it rather than leaving it stranded over the wrong pane.
    window.addEventListener('scroll', reposition, true)
    return () => {
      window.removeEventListener('pointerdown', onDown, true)
      window.removeEventListener('resize', reposition)
      window.removeEventListener('scroll', reposition, true)
    }
  }, [open, position])

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
        setOpen(false)
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause))
        // The refusal is authoritative about the mode now in force, which may not
        // be the one the click asked for. Re-read rather than leaving the menu
        // showing the rejected choice, and stay open so the reason is readable.
        void load()
      } finally {
        setBusy(false)
      }
    },
    [session.id, load],
  )

  if (!isAgentBackend(session.backend)) return null

  const policy = status?.policy
  const active = !!status && status.effective_mode !== 'wait'
  const label = approvalChipLabel(status)

  return (
    <div class="approval-chip-wrap" ref={wrap}>
      <button
        type="button"
        class={`voice-chip approval-chip${active ? ' approval-on' : ''}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Approvals for this conversation: ${approvalSummary(status, now)}. Click to change.`}
        title={`Approvals: ${approvalSummary(status, now)}`}
        onClick={() => setOpen(value => !value)}
      >
        appr:{label}
      </button>
      {open && createPortal(
        <div ref={menu} class="approval-menu ui-portal" role="menu" style={menuStyle}>
          {status ? (
            <>
              {APPROVAL_MODES.map(mode => {
                const blocked = modeUnavailableReason(status, mode)
                const selected = status.policy.mode === mode
                return (
                  <button
                    key={mode}
                    type="button"
                    role="menuitemradio"
                    aria-checked={selected}
                    class={`approval-menu-item${selected ? ' selected' : ''}`}
                    disabled={busy || !!blocked}
                    onClick={() => void choose(mode)}
                  >
                    <span class="approval-menu-label">{MODE_LABELS[mode]}</span>
                    <span class="approval-menu-why">{blocked || MODE_DESCRIPTIONS[mode]}</span>
                  </button>
                )
              })}
              <p class="approval-menu-note">{approvalSummary(status, now)}</p>
              {status.policy.mode === 'allowlisted' && (
                <p class="approval-menu-note">
                  {status.policy.rules.length} rule
                  {status.policy.rules.length === 1 ? '' : 's'} from{' '}
                  {status.rules_source === 'project' ? '.swe-mux/config.toml' : 'defaults'}, fixed
                  when the mode was set.
                </p>
              )}
              {policy?.last_request && (
                <p class="approval-menu-note">Last: {policy.last_request}</p>
              )}
              {!!policy?.floor_deferred && (
                <p class="approval-menu-note">
                  Pushes, credential reads, and destructive commands are never auto-approved.
                </p>
              )}
            </>
          ) : (
            <p class="approval-menu-note">{error || 'Reading approval settings…'}</p>
          )}
          {error && status && <p class="approval-menu-note approval-error">{error}</p>}
        </div>,
        document.body,
      )}
    </div>
  )
}
