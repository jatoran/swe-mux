import { catalogRevision, t, locale as uiLocale } from './localization/index.ts'
import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { agentTargetName } from './agentTargets'
import { api } from './api'
import {
  branchActionLabel,
  branchIneligibilityMessage,
  branchModeFor,
  branchModeState,
  branchOutcomeSummary,
  branchPointEligible,
  branchPointPreview,
  branchPointsEmptyMessage,
  defaultBranchPoint,
  orderedBranchPoints,
  type BranchMode,
  type BranchPoint,
  type BranchPoints,
  type BranchRequest,
} from './branchPoints'
import { useModalFocus } from './modalFocus'
import { transcriptSpeaker, transcriptTimestampLabel } from './transcriptView'
import type { Session } from './types'

// Choosing where a conversation forks.
//
// A surface of its own rather than buttons added to the Transcript tab. That tab is
// deliberately inert - copy is its only verb - because it is where somebody reviews
// what an agent already did, and a stray click there must never be able to start
// something. Branching starts a session, so it asks first, in a dialog opened by the
// rail's Branch button and showing exactly what the branch will carry.
//
// Nothing here recomputes eligibility. The daemon has read the transcript and knows
// which cuts the provider would reject; this renders that answer and its reason.

type Props = {
  session: Session
  onClose: () => void
  onBranch: (request: BranchRequest) => Promise<string>
}

export function BranchPicker({ session, onClose, onBranch }: Props) {
  const panel = useRef<HTMLElement>(null)
  const [data, setData] = useState<BranchPoints | null>(null)
  const [chosen, setChosen] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useModalFocus(panel, onClose, !busy, 'branch-picker')

  useEffect(() => {
    let live = true
    setLoading(true)
    api<BranchPoints>('GET', `/api/sessions/${session.id}/branch-points`)
      .then(result => {
        if (!live) return
        setData(result)
        setChosen(defaultBranchPoint(result.points)?.message_id || '')
      })
      .catch(cause => live && setError(cause instanceof Error ? cause.message : String(cause)))
      .finally(() => live && setLoading(false))
    return () => {
      live = false
    }
  }, [session.id])

  const points = useMemo(() => orderedBranchPoints(data?.points || []), [data, uiLocale.value, catalogRevision.value])
  const point = points.find(item => item.message_id === chosen) || null
  const mode: BranchMode = point ? branchModeFor(point) : 'after'
  const ready = !!point && branchPointEligible(point, mode)

  const branch = async () => {
    if (!point) return
    setBusy(true)
    setError('')
    const failure = await onBranch({ from_message_id: point.message_id, mode })
    if (failure) {
      setBusy(false)
      setError(failure)
      return
    }
    onClose()
  }

  const row = (item: BranchPoint) => {
    const itemMode = branchModeFor(item)
    const state = branchModeState(item, itemMode)
    const stamp = transcriptTimestampLabel(item.ts)
    return (
      <label
        key={item.message_id}
        class={`branch-point ${item.role} ${chosen === item.message_id ? 'active' : ''} ${state.eligible ? '' : 'blocked'}`}
      >
        <input
          type="radio"
          name="branch-point"
          value={item.message_id}
          checked={chosen === item.message_id}
          disabled={busy || !state.eligible}
          onChange={() => setChosen(item.message_id)}
        />
        <span class="branch-point-body">
          <strong>
            <span class="branch-point-speaker">{transcriptSpeaker(item.role)}</span>
            {stamp && <time>{stamp}</time>}
            <span class="branch-point-action">{branchActionLabel(itemMode)}</span>
          </strong>
          <small>{branchPointPreview(item.text) || t("BranchPicker.66d85990a4")}</small>
          {!state.eligible && (<em class="branch-point-blocked">{branchIneligibilityMessage(state.reason)}</em>)}
        </span>
      </label>
    )
  }

  return (
    <div data-dialog-id="branch"
      class="modal-layer control-plane-modal-layer"
      role="dialog"
      aria-modal="true"
      aria-label={t("BranchPicker.662cf95234")}
      onMouseDown={event => event.target === event.currentTarget && !busy && onClose()}
    >
      <section class="modal control-plane-modal branch-picker-modal" ref={panel}>
        <div class="modal-heading">
          <div>
            <span>{t("BranchPicker.1c24a0217a")}</span>
            <h2>{agentTargetName(session)}</h2>
          </div>
          <button type="button" aria-label={t("BranchPicker.a2c20531f0")} disabled={busy} onClick={onClose}>
            ×
          </button>
        </div>
        <div class="control-plane-modal-body">
          <p>{t("BranchPicker.dade73f655")}</p>
          {error && (<p class="modal-warning" role="alert">
              {error}
            </p>)}
          {loading && <p class="drawer-empty">{t("BranchPicker.65c280a9a0")}</p>}
          {!loading && !points.length && (<p class="drawer-empty">
              {branchPointsEmptyMessage(data?.reason ?? null, session.backend)}
            </p>)}
          {data?.truncated && !!points.length && (<p class="branch-picker-note">{t("BranchPicker.1a3fe74ff2")}</p>)}
          {!!points.length && (<div class="branch-point-list" role="radiogroup" aria-label={t("BranchPicker.a6dbc20d03")}>
              {points.map(row)}
            </div>)}
        </div>
        <div class="modal-footer">
          <span>{point ? branchOutcomeSummary(mode) : ''}</span>
          <button type="button" disabled={busy} onClick={onClose}>{t("BranchPicker.19766ed6cc")}</button>
          <button type="button" class="primary" disabled={busy || !ready} onClick={() => void branch()}>
            {busy ? t("BranchPicker.bd3eda4be9") : t("BranchPicker.bf7f43a656")}
          </button>
        </div>
      </section>
    </div>
  )
}
