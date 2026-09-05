import { useCallback, useEffect, useRef, useState } from 'preact/hooks'
import { useModalFocus } from './modalFocus'
import {
  fetchUpdateInstall, installFinished, installPhaseLabel, planCostLine, planInstallLine,
  planSessionsLine, refusalNeedsConsent, requestUpdateInstall, requestUpdatePlan,
  UpdateRefusedError,
  type UpdateInstallStatus, type UpdatePlan, type UpdateRefusal,
} from './updateCheck'

/**
 * The confirm dialog for installing a release, and the progress that follows.
 *
 * Every sentence it shows is the daemon's answer: `POST /api/update/plan` says
 * which mode the release needs, whether the operator's sessions survive and how
 * many there are, and how much of the bundle gets rewritten - all before a byte
 * of the archive is fetched. The dialog renders that and sends the press back
 * with the same words. It decides nothing itself, for the reason `updateCheck.ts`
 * does not re-derive `banner`: two implementations of the supervisor question
 * would eventually disagree about whether sessions are about to end.
 *
 * Consent is a second press, on a button that says what it does. A release that
 * replaces the PTY supervisor is refused by the daemon until the request carries
 * `accept_supervisor_update`, and the dialog only ever sends that from the
 * button labelled with the consequence. The plan usually knows in advance (the
 * release's metadata sidecar); when it does not, the install stops after the
 * download and the refusal arrives here with the same consent word, so the
 * second press is offered then. Either way the archive is kept, and the second
 * press costs no second download.
 *
 * Once the daemon hands off to the applier, the dialog closes: the app-wide
 * redeploy chip and outage overlay take over, driven by the same broadcast a
 * rebuild makes. Polling here is only for the download.
 */

/** How often the download's progress is re-read. */
export const INSTALL_POLL_MS = 1000

type Props = {
  version: string
  changelog?: string
  onClose: () => void
}

type Stage =
  | { name: 'planning' }
  | { name: 'planned'; plan: UpdatePlan }
  | { name: 'installing'; status: UpdateInstallStatus }
  | { name: 'refused'; refusal: UpdateRefusal; plan: UpdatePlan | null }
  | { name: 'done'; status: UpdateInstallStatus }
  | { name: 'error'; message: string; plan: UpdatePlan | null }

export function UpdateDialog({ version, changelog, onClose }: Props) {
  const [stage, setStage] = useState<Stage>({ name: 'planning' })
  const [busy, setBusy] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  // Escape and the back gesture close it during the plan and the refusal, never
  // mid-install: a download the operator can no longer see is one they will
  // think failed, and the daemon keeps going regardless.
  const closable = stage.name !== 'installing'
  useModalFocus(ref, () => { if (closable) onClose() }, true, 'update-dialog')

  useEffect(() => {
    let cancelled = false
    void requestUpdatePlan(version).then(plan => {
      if (!cancelled) setStage({ name: 'planned', plan })
    }).catch(cause => {
      if (cancelled) return
      if (cause instanceof UpdateRefusedError) setStage({ name: 'refused', refusal: cause.refusal, plan: null })
      else setStage({ name: 'error', message: cause instanceof Error ? cause.message : String(cause), plan: null })
    })
    return () => { cancelled = true }
  }, [version])

  // While an attempt runs, follow it. `handed_off` closes the dialog and hands
  // the rest to the redeploy chip; a refusal that consent would clear is shown
  // with the consenting button; anything else is shown as what it is.
  const installing = stage.name === 'installing'
  useEffect(() => {
    if (!installing) return
    let cancelled = false
    let timer: number | undefined
    const tick = async () => {
      let status: UpdateInstallStatus | null = null
      try { status = await fetchUpdateInstall() } catch { /* a lost poll is not a verdict */ }
      if (cancelled) return
      if (status && installFinished(status.phase)) {
        if (status.phase === 'handed_off') { onClose(); return }
        if (status.phase === 'refused') {
          setStage(current => ({
            name: 'refused',
            refusal: { ...status, error: status.reason || 'refused', message: status.message || '' },
            plan: current.name === 'installing' ? null : (current as { plan?: UpdatePlan | null }).plan ?? null,
          }))
          return
        }
        setStage({ name: 'done', status })
        return
      }
      if (status) setStage({ name: 'installing', status })
      timer = window.setTimeout(() => { void tick() }, INSTALL_POLL_MS)
    }
    timer = window.setTimeout(() => { void tick() }, INSTALL_POLL_MS)
    return () => { cancelled = true; if (timer !== undefined) window.clearTimeout(timer) }
  }, [installing, onClose])

  const planOf = (current: Stage): UpdatePlan | null =>
    current.name === 'planned' ? current.plan
      : current.name === 'refused' || current.name === 'error' ? current.plan : null

  const install = useCallback(async (accept: boolean) => {
    if (busy) return
    setBusy(true)
    const plan = planOf(stage)
    try {
      const status = await requestUpdateInstall(version, accept)
      setStage({ name: 'installing', status })
    } catch (cause) {
      if (cause instanceof UpdateRefusedError) setStage({ name: 'refused', refusal: cause.refusal, plan })
      else setStage({ name: 'error', message: cause instanceof Error ? cause.message : String(cause), plan })
    } finally {
      setBusy(false)
    }
  }, [busy, stage, version])

  const plan = planOf(stage)
  const consentNeeded = stage.name === 'refused'
    ? refusalNeedsConsent(stage.refusal)
    : stage.name === 'planned' && !!stage.plan.consent
  const reaps = plan?.reaps_sessions || (stage.name === 'refused' && refusalNeedsConsent(stage.refusal))
  const sourceInstall = stage.name === 'refused' && stage.refusal.error === 'source_install'

  return <div class="modal-layer daemon-reload-layer" role="alertdialog" aria-modal="true" aria-label={`Install swe-mux ${version}`} onClick={() => { if (closable) onClose() }}>
    <div class="modal daemon-reload-modal update-dialog" ref={ref} onClick={event => event.stopPropagation()}>
      <h2>Install swe-mux {version}?</h2>
      {stage.name === 'planning' && <p>Reading the release…</p>}

      {plan && <>
        <p>{planInstallLine(plan)}</p>
        {planCostLine(plan) && <p>{planCostLine(plan)}</p>}
        {/* The one line that matters, drawn as the refusal it is when sessions end. */}
        <p class={`redeploy-interrupts${reaps ? ' redeploy-blocked' : ''}`}>
          <strong>{reaps ? 'Every live session ends.' : plan.supervisor.known ? 'Sessions are preserved.' : 'Sessions: decided after download.'}</strong>
          <span>{planSessionsLine(plan)}</span>
        </p>
      </>}

      {stage.name === 'installing' && <p class="update-dialog-progress" aria-live="polite">
        <span class="redeploy-spinner" aria-hidden="true" /> {installPhaseLabel(stage.status) || 'Working'}
      </p>}

      {stage.name === 'refused' && <>
        {sourceInstall && <p>{stage.refusal.message}</p>}
        {sourceInstall && stage.refusal.upgrade_command && <p class="update-dialog-command">
          <code>{stage.refusal.upgrade_command}</code>
          <button type="button" onClick={() => void navigator.clipboard?.writeText(stage.refusal.upgrade_command || '')}>Copy</button>
        </p>}
        {!sourceInstall && <p class={`redeploy-interrupts${consentNeeded ? ' redeploy-blocked' : ''}`}>
          <strong>{consentNeeded ? 'Every live session ends.' : 'Not installed.'}</strong>
          <span>{stage.refusal.message}</span>
        </p>}
      </>}

      {stage.name === 'done' && <p class="redeploy-interrupts redeploy-blocked">
        <strong>Not installed.</strong>
        <span>{stage.status.message || 'The update did not complete. Check daemon.log in the data directory.'}</span>
      </p>}

      {stage.name === 'error' && <p class="redeploy-interrupts redeploy-blocked">
        <strong>Could not plan the update.</strong>
        <span>{stage.message}</span>
      </p>}

      {changelog && stage.name !== 'installing' && <p><a href={changelog} target="_blank" rel="noreferrer">Release notes</a></p>}

      <div class="modal-actions">
        <button type="button" onClick={onClose} disabled={!closable}>{stage.name === 'refused' || stage.name === 'done' || stage.name === 'error' ? 'Close' : 'Cancel'}</button>
        {(stage.name === 'planned' || (stage.name === 'refused' && !sourceInstall)) && (
          consentNeeded
            ? <button type="button" class="primary update-dialog-reap" disabled={busy} onClick={() => void install(true)}>
              {busy ? 'Starting…' : 'End every session and install'}
            </button>
            : <button type="button" class="primary" disabled={busy} onClick={() => void install(false)}>
              {busy ? 'Starting…' : 'Install'}
            </button>
        )}
      </div>
    </div>
  </div>
}
