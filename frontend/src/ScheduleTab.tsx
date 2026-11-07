import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { Dropdown } from './Dropdown'
import { CompactGrantFlag, GrantButton } from './GrantGate'
import { promptDeliveryHarnesses } from './harnessRegistry'
import {
  branchModeFor, branchPointEligible, branchPointPreview, branchPointsEmptyMessage,
  orderedBranchPoints, type BranchPoint, type HistoryBranchPoints,
} from './branchPoints'
import {
  BLOCKED_LABELS, CRON_PRESETS, OUTCOME_LABELS, TARGET_KIND_COPY, absoluteLabel, actionLabel,
  authorLabel, cadenceLabel, draftFromSchedule, draftToBody, emptyDraft, frequencyLabel,
  inactiveLabel, isAgentAuthored, isAwaitingApproval, needsAttention, orderSchedules,
  presetForCron, targetIsMissing, untilLabel,
  type Schedule, type ScheduleDraft, type ScheduleStatus, type ScheduleTargetKind,
  type ScheduleWhenLive,
} from './schedules'
import { transcriptSpeaker } from './transcriptView'
import type { LaunchProfile, Project } from './types'

// The drawer's Schedule tab: what this Project starts on its own, and what happened
// last time it did.
//
// It sits beside Processes because the two answer the same question at different
// times - what is running now, and what will run later - and it is a tab rather than a
// modal because the decisions it offers (pause this, run it now, is last night's
// session still open) are judgements about live sessions, which are legible in the
// workspace behind the drawer.
//
// Two things it deliberately does not do:
//
//  * It never computes a fire time. Cron plus a timezone plus daylight saving has one
//    implementation, in the daemon, and the editor asks `/api/schedules/preview` so the
//    times shown before saving come from the code that will actually fire.
//  * It never presents a schedule as working when it cannot fire. `blocked` is a live
//    permission answer, rendered as a row-level warning with the way to fix it, because
//    an armed-looking row that is silently inert is the failure this surface exists to
//    prevent. A resume whose conversation has been deleted is the same class of lie and
//    is rendered the same way.
//
// A *resume* schedule is never authored from a blank form here. It is seeded from the
// conversation it reopens - the History row's "Resume later…", or a pane's own menu -
// because the one thing this tab cannot offer is a way to find a conversation, and an
// editor with an empty "run id" box would be a worse conversation picker than the one
// that already exists. What it does own is everything about the seeded schedule: when,
// how the target may move, and what to say on arrival.

type Props = {
  /** The active Project. Unscoped means "this one", like every Project-scoped tab. */
  project?: Project
  projects: Project[]
  /** Every enabled launch profile; the editor filters to the chosen harness. */
  profiles: LaunchProfile[]
  /** '' = every Project. Owned by the caller so the scope survives a tab switch. */
  scope: string
  onScope: (scope: string) => void
  onOpenSession: (sessionId: string) => void
  /** A resume seeded from elsewhere, opened straight into the editor. Consumed once:
   *  the caller clears it, so re-opening the tab does not reopen the form. */
  seed?: ScheduleDraft | null
  onSeedConsumed?: () => void
  onDone: () => void
}

type ListResponse = { schedules: Schedule[]; status: ScheduleStatus }

const REFRESH_MS = 30_000

export function ScheduleTab({
  project, projects, profiles, scope, onScope, onOpenSession, seed, onSeedConsumed, onDone,
}: Props) {
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [status, setStatus] = useState<ScheduleStatus | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState<ScheduleDraft>(emptyDraft)
  const [expanded, setExpanded] = useState<string | null>(null)
  const generation = useRef(0)

  const load = async () => {
    const ticket = ++generation.current
    try {
      const query = scope ? `?project_id=${encodeURIComponent(scope)}` : ''
      const payload = await api<ListResponse>('GET', `/api/schedules${query}`, undefined, { timeoutMs: 15_000 })
      if (ticket !== generation.current) return
      setSchedules(payload.schedules)
      setStatus(payload.status)
      setError('')
    } catch (cause) {
      if (ticket !== generation.current) return
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      if (ticket === generation.current) setLoading(false)
    }
  }

  useEffect(() => {
    setLoading(true)
    void load()
    // One cheap GET while the tab is open. Nothing here polls the daemon's process
    // tree or its transcripts, so an open tab costs a query per half minute.
    const timer = setInterval(() => void load(), REFRESH_MS)
    // And one more whenever a schedule changes or fires anywhere, so an agent's new
    // request, a decision taken on the phone, or an expiry shows without the wait.
    const onChanged = () => void load()
    window.addEventListener('mux:schedules-changed', onChanged)
    return () => {
      clearInterval(timer)
      window.removeEventListener('mux:schedules-changed', onChanged)
    }
  }, [scope])

  // A seed is a request to author one schedule, so it opens the editor once and is
  // handed back immediately. Keeping it would reopen the form every time the tab
  // remounted, over a schedule the user may already have saved.
  useEffect(() => {
    if (!seed) return
    setDraft(seed)
    setEditing('new')
    onSeedConsumed?.()
  }, [seed])

  const ordered = useMemo(() => orderSchedules(schedules), [schedules, uiLocale.value, catalogRevision.value])
  const attention = useMemo(() => needsAttention(schedules), [schedules, uiLocale.value, catalogRevision.value])
  const activeProject = projects.find(item => item.id === scope) || project

  const act = async <T,>(key: string, operation: () => Promise<T>): Promise<T | null> => {
    setBusy(key)
    try {
      const result = await operation()
      setError('')
      await load()
      return result
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      return null
    } finally {
      setBusy('')
    }
  }

  const startNew = () => {
    setDraft({ ...emptyDraft(), timezone: '' })
    setEditing('new')
  }

  const startEdit = (schedule: Schedule) => {
    setDraft(draftFromSchedule(schedule))
    setEditing(schedule.id)
  }

  const save = async (schedule: Schedule | null) => {
    const body = draftToBody(draft)
    const saved = await act('save', async () => {
      if (schedule) {
        return api<Schedule>('PATCH', `/api/schedules/${schedule.id}`, { ...body, revision: schedule.revision })
      }
      const target = scope || project?.id || ''
      if (!target) throw new Error(t("ScheduleTab.68047b1692"))
      return api<Schedule>('POST', `/api/projects/${target}/schedules`, body)
    })
    if (saved) setEditing(null)
  }

  if (editing !== null) {
    const schedule = schedules.find(item => item.id === editing) || null
    return <ScheduleEditor
      draft={draft}
      onDraft={setDraft}
      profiles={profiles}
      busy={busy === 'save'}
      error={error}
      projectName={(schedule ? projects.find(item => item.id === schedule.project_id)?.name : activeProject?.name) || ''}
      onCancel={() => { setEditing(null); setError('') }}
      onSave={() => void save(schedule)}
    />
  }

  return <div class="schedule-tab">
    <div class="schedule-filters">
      <Dropdown
        ariaLabel={t("ScheduleTab.b57822813c")}
        value={scope}
        onChange={onScope}
        options={[
          { value: project?.id || '', label: project ? project.name : t("ScheduleTab.f34c2be0d1"), disabled: !project },
          { value: '', get label() { return t("ScheduleTab.4b87271b6b") } },
        ]}
      />
      <button title={t("ScheduleTab.beecf40080")} onClick={() => void load()}>{t("ScheduleTab.d6cc0a088c")}</button>
      <button class="primary" disabled={!scope && !project} onClick={startNew}>{t("ScheduleTab.d74acc791a")}</button>
    </div>
    <p class="schedule-summary">
      {status ? t("ScheduleTab.c3110d0cf4", { armed: status.armed, live_sessions: status.live_sessions, max_concurrent: status.max_concurrent, value4: (status.enabled) ? 'yes' : 'no' }) : t("ScheduleTab.10646fb3fb")}
      {attention.length ? t("ScheduleTab.78a61e1bb9", { length: attention.length, value2: (attention.length === 1) ? 'yes' : 'no' }) : ''}
    </p>
    {/* The install stop is not a row-level condition — it silences every schedule in every
        Project at once — so it is stated once above the list rather than repeated on each
        row that happens to be loaded. */}
    {status && !status.enabled && <CompactGrantFlag id="schedules.install"
      heading={t("ScheduleTab.b98637f58f")}
      consequence={t("ScheduleTab.5463ec94b3")}
      onGranted={load}/>}
    <div class="schedule-body">
      {loading && !ordered.length && <p class="schedule-empty">{t("ScheduleTab.10646fb3fb")}</p>}
      {error && <p class="schedule-error" role="alert">{error}</p>}
      {!loading && !ordered.length && <p class="schedule-empty">
        {scope ? t("ScheduleTab.2f5f825f0d") : t("ScheduleTab.beb4d49009")}
      </p>}
      {ordered.map(schedule => <section
        class={`schedule-row ${schedule.enabled ? '' : 'paused'} ${schedule.blocked || targetIsMissing(schedule) ? 'blocked' : ''} ${isAwaitingApproval(schedule) ? 'awaiting' : ''}`}
        key={schedule.id}
      >
        <button
          class="schedule-heading"
          aria-expanded={expanded === schedule.id}
          onClick={() => setExpanded(current => current === schedule.id ? null : schedule.id)}
        >
          <span>
            <strong>{schedule.label}</strong>
            {!scope && <em class="schedule-project">{schedule.project_name}</em>}
          </span>
          <small>
            {cadenceLabel(schedule)}
            {' · '}
            {schedule.enabled ? untilLabel(schedule.next_fire_at) : inactiveLabel(schedule)}
            {' · '}
            {actionLabel(schedule)}
          </small>
        </button>
        {/* Who wrote it, when it was not the reader. An agent's schedule carries the
            authority its Project granted agents, which is worth knowing before pausing
            or editing it, and its stated reason is the one piece of context a person
            reviewing it cannot get anywhere else. */}
        {isAgentAuthored(schedule) && <p class="schedule-author">
          <span>{authorLabel(schedule)}</span>
          {schedule.request_reason && <em>{schedule.request_reason}</em>}
        </p>}
        {isAwaitingApproval(schedule) && <p class="schedule-approval">
          <strong>{t("ScheduleTab.approvalHeading")}</strong>
          {' '}
          <small>{schedule.approval_reason}{schedule.trigger_kind !== 'once' ? ` (${frequencyLabel(schedule.fires_per_day)})` : ''}</small>
        </p>}
        {/* A resume against a conversation that has been deleted reads armed and can
            never fire, which is the same lie `blocked` exists to prevent. Said on the
            row rather than left for the run history, because the next fire that would
            record it may be a week away. */}
        {targetIsMissing(schedule) && <p class="schedule-blocked">{t("ScheduleTab.c40ee4fea2")}</p>}
        {/* Both blocked reasons are switches, and each row offers the one that is holding
            it: the Project's own opt-in, or the install-wide stop that overrides every
            Project. The install case previously stated the reason and left the reader to
            find the switch. */}
        {schedule.blocked && <p class="schedule-blocked">
          {BLOCKED_LABELS[schedule.blocked] || schedule.blocked}
          {' '}
          {schedule.blocked === 'automation_disabled' && <GrantButton
            id="project.scheduledRuns" projectId={schedule.project_id} onGranted={load}
          >{t("ScheduleTab.2d70ba1a2d")}</GrantButton>}
          {schedule.blocked === 'install_disabled' && <GrantButton
            id="schedules.install" onGranted={load}
          >{t("ScheduleTab.e679ca0953")}</GrantButton>}
        </p>}
        <div class="schedule-meta">
          <span title={schedule.last_reason || undefined}>
            {schedule.last_outcome ? t("ScheduleTab.4040b7c06c", { value1: OUTCOME_LABELS[schedule.last_outcome] || schedule.last_outcome, value2: absoluteLabel(schedule.last_fire_at) }) : t("ScheduleTab.de5f9d00b8")}
          </span>
          {schedule.last_session_id && <button
            class="link"
            title={t("ScheduleTab.5a8c65ccf7")}
            onClick={() => { onOpenSession(schedule.last_session_id as string); onDone() }}
          >{t("ScheduleTab.bf8caf6677")}</button>}
          {schedule.expires_at && <span class="schedule-expiry">{t("ScheduleTab.endsAt", { when: absoluteLabel(schedule.expires_at) })}</span>}
        </div>
        {isAwaitingApproval(schedule) ? <div class="schedule-actions">
          <button
            class="primary"
            disabled={busy !== ''}
            onClick={() => void act(`decide:${schedule.id}`, () => api('POST', `/api/schedules/${schedule.id}/decide`, { decision: 'approve' }))}
          >{t("ScheduleTab.approve")}</button>
          <button
            disabled={busy !== ''}
            onClick={() => void act(`decide:${schedule.id}`, () => api('POST', `/api/schedules/${schedule.id}/decide`, { decision: 'dismiss' }))}
          >{t("ScheduleTab.dismiss")}</button>
          <button disabled={busy !== ''} onClick={() => startEdit(schedule)}>{t("ScheduleTab.262121c537")}</button>
        </div> : <div class="schedule-actions">
          <button
            disabled={busy !== ''}
            title={t("ScheduleTab.755ae1c9b1")}
            onClick={() => void act(`run:${schedule.id}`, () => api('POST', `/api/schedules/${schedule.id}/run`))}
          >{busy === `run:${schedule.id}` ? t("ScheduleTab.407944301e") : t("ScheduleTab.2bb215f2a2")}</button>
          <button
            disabled={busy !== ''}
            onClick={() => void act(`arm:${schedule.id}`, () => api('PATCH', `/api/schedules/${schedule.id}`, { enabled: !schedule.enabled }))}
          >{schedule.enabled ? t("ScheduleTab.6210c0bf05") : t("ScheduleTab.a83a31320d")}</button>
          <button disabled={busy !== ''} onClick={() => startEdit(schedule)}>{t("ScheduleTab.262121c537")}</button>
          <button
            class="danger"
            disabled={busy !== ''}
            onClick={() => void act(`delete:${schedule.id}`, () => api('DELETE', `/api/schedules/${schedule.id}`))}
          >{t("ScheduleTab.6197595503")}</button>
        </div>}
        {expanded === schedule.id && <div class="schedule-detail">
          {schedule.action === 'resume' && schedule.target && !schedule.target.missing && <p class="schedule-target">
            <strong>{TARGET_KIND_COPY[schedule.target_kind].label}</strong>
            <small>
              {t("ScheduleTab.1770faf36f", { backend: schedule.target.backend, name: schedule.target.name })}
              {/* Only worth a second line when the two differ, which is exactly when
                  the pinned row is no longer where the work is. */}
              {schedule.target_kind === 'latest_of_session'
                && schedule.target.resolved_run_id !== schedule.target.run_id && t("ScheduleTab.d50db6315d", { resolved_name: schedule.target.resolved_name })}
              {schedule.context_ceiling_pct > 0 && t("ScheduleTab.4043075b82", { value1: Math.round(schedule.context_ceiling_pct * 100) })}
            </small>
          </p>}
          <p class="schedule-prompt">{schedule.prompt}</p>
          {schedule.follow_ups.length > 0 && <p class="schedule-followups">{rich("ScheduleTab.0267b83808", { length: schedule.follow_ups.length, value2: (schedule.follow_ups.length === 1) ? 'yes' : 'no' })}</p>}
          <ol class="schedule-runs">
            {!schedule.runs.length && <li class="schedule-empty">{t("ScheduleTab.c13e0702db")}</li>}
            {schedule.runs.map(run => <li key={run.id} class={`schedule-run ${run.outcome}`}>
              <span>{OUTCOME_LABELS[run.outcome] || run.outcome}</span>
              <small>{absoluteLabel(run.started_at)}{run.origin === 'manual' ? t("ScheduleTab.c79d3fa09e") : ''}</small>
              {run.reason && <em title={run.reason}>{run.reason}</em>}
              {run.session_id && <button class="link" onClick={() => { onOpenSession(run.session_id as string); onDone() }}>{t("ScheduleTab.3f3af1eceb")}</button>}
            </li>)}
          </ol>
        </div>}
      </section>)}
    </div>
  </div>
}

/** The part of the editor that only a resume has: what it reopens, and how.
 *
 *  The conversation itself is fixed - it came from the row or pane this was seeded
 *  from, and there is no picker here to change it. What is open is the *kind*, which is
 *  the real decision: pin the conversation, follow it, or pin a moment in it. Each
 *  option states its own cost, because the costs are what distinguish them.
 *
 *  The branch-point list is the daemon's, read from the History row. Nothing here
 *  recomputes eligibility: whether a cut is legal depends on the provider's rule about
 *  unanswered tool calls, and a browser that guessed would eventually offer a schedule
 *  a cut that fails every night at 3 a.m. */
function ResumeTargetFields({ draft, onDraft }: {
  draft: ScheduleDraft
  onDraft: (draft: ScheduleDraft) => void
}) {
  const [points, setPoints] = useState<HistoryBranchPoints | null>(null)
  const [loading, setLoading] = useState(false)
  const [failure, setFailure] = useState('')

  // Only fetched when a fixed point is actually being chosen: reading a conversation to
  // list its cuts parses the whole transcript, and a form that did it on open would pay
  // that for every resume schedule whether or not it forks.
  useEffect(() => {
    if (draft.target_kind !== 'fork_point' || !draft.target_run_id) return
    let live = true
    setLoading(true)
    setFailure('')
    api<HistoryBranchPoints>('GET', `/api/history/${encodeURIComponent(draft.target_run_id)}/branch-points`)
      .then(result => { if (live) setPoints(result) })
      .catch(cause => live && setFailure(cause instanceof Error ? cause.message : String(cause)))
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [draft.target_kind, draft.target_run_id])

  const ordered = useMemo(() => orderedBranchPoints(points?.points || []), [points, uiLocale.value, catalogRevision.value])
  const chosen = ordered.find(item => item.message_id === draft.target_cut_message_id) || null
  const pick = (point: BranchPoint) => onDraft({
    ...draft,
    target_cut_message_id: point.message_id,
    target_cut_mode: branchModeFor(point),
  })

  return <>
    <p class="schedule-target-summary">
      <strong>{t("ScheduleTab.2c501fc3ba")}</strong>
      <span>{draft.target_backend ? t("ScheduleTab.bb15fba8fd", { target_backend: draft.target_backend }) : ''}{draft.target_label || draft.target_run_id}</span>
    </p>
    <label>{rich("ScheduleTab.2d755819c7", { value1: <Dropdown
      value={draft.target_kind}
      onChange={value => onDraft({
        ...draft,
        target_kind: value as ScheduleTargetKind,
        // Each kind reads a different field, and carrying the other one across would
        // let a saved schedule keep a cut point it no longer forks at.
        target_cut_message_id: '',
        target_cut_mode: '',
        context_ceiling_pct: value === 'latest_of_session'
          ? (draft.context_ceiling_pct || 0.7)
          : 0,
      })}
      options={(Object.keys(TARGET_KIND_COPY) as ScheduleTargetKind[]).map(kind =>
        ({ value: kind, label: TARGET_KIND_COPY[kind].label }))}
    />, value2: <small>{TARGET_KIND_COPY[draft.target_kind].detail}</small> })}</label>
    {draft.target_kind === 'latest_of_session' && <label>{rich("ScheduleTab.f5a1aa9723", { value1: <input
      type="number"
      min={0}
      max={100}
      value={Math.round(draft.context_ceiling_pct * 100)}
      onInput={event => onDraft({
        ...draft,
        context_ceiling_pct: Math.max(0, Math.min(100, Number(event.currentTarget.value))) / 100,
      })}
    />, value2: <small>{t("ScheduleTab.d5f1008ae1")}</small> })}</label>}
    {draft.target_kind === 'fork_point' && <fieldset class="schedule-branch-points">
      <legend>{t("ScheduleTab.7b231f8651")}</legend>
      {loading && <p class="schedule-empty">{t("ScheduleTab.65c280a9a0")}</p>}
      {failure && <p class="schedule-error" role="alert">{failure}</p>}
      {!loading && !failure && !ordered.length && <p class="schedule-empty">
        {branchPointsEmptyMessage(points?.reason ?? null, points?.backend || draft.target_backend)}
      </p>}
      {points?.truncated && !!ordered.length && <p class="schedule-note">{t("ScheduleTab.346e11112f")}</p>}
      {ordered.map(point => {
        const mode = branchModeFor(point)
        const eligible = branchPointEligible(point, mode)
        return <label
          key={point.message_id}
          class={`schedule-branch-point ${point.role} ${chosen?.message_id === point.message_id ? 'active' : ''} ${eligible ? '' : 'blocked'}`}
        >
          <input
            type="radio"
            name="schedule-branch-point"
            value={point.message_id}
            checked={chosen?.message_id === point.message_id}
            disabled={!eligible}
            onChange={() => pick(point)}
          />
          <span>
            <strong>{transcriptSpeaker(point.role)} · {mode === 'before' ? t("ScheduleTab.4062a9f595") : t("ScheduleTab.a3d7a6bf20")}</strong>
            <small>{branchPointPreview(point.text) || t("ScheduleTab.66d85990a4")}</small>
          </span>
        </label>
      })}
    </fieldset>}
  </>
}

type EditorProps = {
  draft: ScheduleDraft
  onDraft: (draft: ScheduleDraft) => void
  profiles: LaunchProfile[]
  busy: boolean
  error: string
  projectName: string
  onCancel: () => void
  onSave: () => void
}

function ScheduleEditor({ draft, onDraft, profiles, busy, error, projectName, onCancel, onSave }: EditorProps) {
  const [fires, setFires] = useState<number[]>([])
  const [previewError, setPreviewError] = useState('')
  const harnesses = promptDeliveryHarnesses()
  const resuming = draft.action === 'resume'
  // Matched from the expression rather than remembered from the click, so an edit that
  // lands back on a preset is recognised and a hand-written one reads as Custom.
  const preset = presetForCron(draft.cron)
  const change = <K extends keyof ScheduleDraft>(key: K, value: ScheduleDraft[K]) =>
    onDraft({ ...draft, [key]: value })

  // The preview is the whole reason a cron field is usable. Debounced because it
  // runs while the expression is still half-typed, and a half-typed expression is
  // the normal state of the field.
  useEffect(() => {
    let cancelled = false
    const timer = setTimeout(async () => {
      try {
        const payload = await api<{ next_fires: number[] }>('POST', '/api/schedules/preview', draftToBody(draft), { timeoutMs: 10_000 })
        if (cancelled) return
        setFires(payload.next_fires)
        setPreviewError(payload.next_fires.length ? '' : t("ScheduleTab.67d00d3c0f"))
      } catch (cause) {
        if (cancelled) return
        setFires([])
        setPreviewError(cause instanceof Error ? cause.message : String(cause))
      }
    }, 250)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [draft.trigger_kind, draft.cron, draft.interval_minutes, draft.run_at_local, draft.timezone])

  const profilesForBackend = profiles.filter(profile => (profile.backend || 'shell') === draft.backend)

  return <form class="schedule-editor" onSubmit={event => { event.preventDefault(); onSave() }}>
    <header>
      <strong>{projectName ? t("ScheduleTab.b668963d55", { projectName: projectName }) : t("ScheduleTab.f4830a1dae")}</strong>
      <small>{resuming ? t("ScheduleTab.d2e5ccdd55") : t("ScheduleTab.f1f211040b")}</small>
    </header>
    {error && <p class="schedule-error" role="alert">{error}</p>}
    {resuming && <ResumeTargetFields draft={draft} onDraft={onDraft} />}
    <label>{rich("ScheduleTab.5b326d4444", { value1: <input
      autofocus={!resuming}
      value={draft.label}
      maxLength={80}
      placeholder={resuming ? t("ScheduleTab.d69f2af4ac") : t("ScheduleTab.93ef25e0ef")}
      onInput={event => change('label', event.currentTarget.value)}
    /> })}</label>
    <label>{resuming ? t("ScheduleTab.77175c9878") : t("ScheduleTab.5c39123805")}<textarea
      rows={4}
      value={draft.prompt}
      placeholder={resuming ? t("ScheduleTab.d613487084") : t("ScheduleTab.e4bdb6ddde")}
      onInput={event => change('prompt', event.currentTarget.value)}
    />{resuming && <small>{t("ScheduleTab.c7a3712de4")}</small>}</label>
    <label>{rich("ScheduleTab.f61a238dc9", { value1: <Dropdown value={draft.trigger_kind} onChange={value => change('trigger_kind', value as ScheduleDraft['trigger_kind'])} options={[
      { value: 'cron', get label() { return t("ScheduleTab.a914ee0ca8") } },
      { value: 'interval', get label() { return t("ScheduleTab.025156c3d5") } },
      { value: 'once', get label() { return t("ScheduleTab.727af8e666") } },
    ]}/> })}</label>
    {draft.trigger_kind === 'cron' && <label>{rich("ScheduleTab.c9eb1a6329", { value1: <div class="schedule-cron-row">
        <input
          value={draft.cron}
          spellcheck={false}
          placeholder="0 3 * * *"
          onInput={event => change('cron', event.currentTarget.value)}
        />
        <Dropdown
          ariaLabel={t("ScheduleTab.0f6ef8da44")}
          value={preset?.cron || ''}
          onChange={value => { if (value) change('cron', value) }}
          options={[
            { value: '', label: preset ? t("ScheduleTab.d3c25d944a") : t("ScheduleTab.494ca78f73") },
            ...CRON_PRESETS.map(item => ({ value: item.cron, label: item.label })),
          ]}
        />
      </div>, value2: <small>{preset ? preset.teaches : t("ScheduleTab.11a7c2e9c8")}</small> })}</label>}
    {draft.trigger_kind === 'interval' && <label>{rich("ScheduleTab.dd5633797e", { value1: <input
      type="number"
      min={5}
      max={129_600}
      value={draft.interval_minutes}
      onInput={event => change('interval_minutes', Number(event.currentTarget.value))}
    />, value2: <small>{t("ScheduleTab.83556c8661")}</small> })}</label>}
    {draft.trigger_kind === 'once' && <label>{rich("ScheduleTab.e837be609d", { value1: <input
      type="datetime-local"
      value={draft.run_at_local}
      onInput={event => change('run_at_local', event.currentTarget.value)}
    /> })}</label>}
    <label>{rich("ScheduleTab.5a784e199d", { value1: <em>{t("ScheduleTab.ec91fdd925")}</em>, value2: <input
      value={draft.timezone}
      spellcheck={false}
      placeholder={t("ScheduleTab.49144769a4")}
      onInput={event => change('timezone', event.currentTarget.value)}
    />, value3: <small>{t("ScheduleTab.1dbfa8e49a")}</small> })}</label>
    <p class={`schedule-preview ${previewError ? 'bad' : ''}`}>
      {previewError || (fires.length ? t("ScheduleTab.634d8b810d", { value1: fires.slice(0, 3).map(absoluteLabel).join(' · ') }) : t("ScheduleTab.e58b9f1484"))}
    </p>
    {/* An end date belongs to a recurring trigger: a one-off ends by firing. Agents'
        recurring schedules always carry one, and a person may set or clear it here. */}
    {draft.trigger_kind !== 'once' && <label>{rich("ScheduleTab.endsField", { value1: <em>{t("ScheduleTab.ec91fdd925")}</em>, value2: <input
      type="datetime-local"
      value={draft.expires_local}
      onInput={event => change('expires_local', event.currentTarget.value)}
    />, value3: <small>{t("ScheduleTab.endsHint")}</small> })}</label>}
    {/* A resume runs the harness the conversation belongs to, with `--resume <id>` as
        its arguments, in the Project root the conversation resolves from. The daemon
        refuses a backend or a profile on one rather than ignoring it, so offering
        either here would be offering control this form does not have. */}
    {!resuming && <label>{rich("ScheduleTab.84887d3897", { value1: <Dropdown value={draft.backend} onChange={value => onDraft({ ...draft, backend: value, profile_id: '' })} options={[
      { value: '', get label() { return t("ScheduleTab.a6f70f181e") } },
      ...harnesses.map(harness => ({ value: harness.name, label: harness.display_name })),
    ]}/> })}</label>}
    {!resuming && draft.backend && profilesForBackend.length > 0 && <label>{rich("ScheduleTab.1fc22339bf", { value1: <Dropdown
      value={draft.profile_id}
      onChange={value => change('profile_id', value)}
      options={[
        { value: '', get label() { return t("ScheduleTab.db10a8733c") } },
        ...profilesForBackend.map(profile => ({ value: profile.id, label: profile.label })),
      ]}
    />, value2: <small>{t("ScheduleTab.5a94ad7fea")}</small> })}</label>}
    {!resuming && draft.backend && <label>{rich("ScheduleTab.modelField", { value1: <em>{t("ScheduleTab.ec91fdd925")}</em>, value2: <input
      value={draft.model}
      spellcheck={false}
      maxLength={100}
      placeholder={t("ScheduleTab.modelPlaceholder")}
      onInput={event => change('model', event.currentTarget.value)}
    />, value3: <small>{t("ScheduleTab.modelHint")}</small> })}</label>}
    <label>{rich("ScheduleTab.2bd076e3aa", { value1: <em>{t("ScheduleTab.ec91fdd925")}</em>, value2: <input
      value={draft.session_name}
      maxLength={80}
      placeholder={resuming ? t("ScheduleTab.ef1c3fe8bc") : t("ScheduleTab.f4435ba1d9")}
      onInput={event => change('session_name', event.currentTarget.value)}
    /> })}</label>
    {!resuming && <label>{rich("ScheduleTab.2823892840", { value1: <Dropdown value={draft.overlap} onChange={value => change('overlap', value as ScheduleDraft['overlap'])} options={[
      { value: 'skip', get label() { return t("ScheduleTab.d0fc7b77ee") } },
      { value: 'allow', get label() { return t("ScheduleTab.c86ae1e9af") } },
    ]}/> })}</label>}
    {resuming && draft.target_kind !== 'fork_point' && <label>{rich("ScheduleTab.whenLiveField", { value1: <Dropdown
      value={draft.when_live}
      onChange={value => change('when_live', value as ScheduleWhenLive)}
      options={[
        { value: 'skip', get label() { return t("ScheduleTab.whenLiveSkip") } },
        { value: 'deliver', get label() { return t("ScheduleTab.whenLiveDeliver") } },
      ]}
    />, value2: <small>{draft.when_live === 'deliver' ? t("ScheduleTab.whenLiveDeliverHint") : t("ScheduleTab.aef2e66c6b")}</small> })}</label>}
    <label class="schedule-check">{rich("ScheduleTab.9c8dacba78", { value1: <input
      type="checkbox"
      checked={draft.catch_up}
      onChange={event => change('catch_up', event.currentTarget.checked)}
    />, value2: <small>{t("ScheduleTab.8ebbe60f79")}</small> })}</label>
    <label>{rich("ScheduleTab.75c989b56f", { value1: <input
      type="number"
      min={0}
      max={500}
      value={draft.daily_run_cap}
      onInput={event => change('daily_run_cap', Number(event.currentTarget.value))}
    />, value2: <small>{t("ScheduleTab.b53747cb33")}</small> })}</label>
    <fieldset class="schedule-followup-editor">
      <legend>{t("ScheduleTab.a41e6e5b6f")}</legend>
      <small>{t("ScheduleTab.11d0752243")}</small>
      {draft.follow_ups.map((body, index) => <div key={index}>
        <textarea
          rows={2}
          value={body}
          onInput={event => change('follow_ups', draft.follow_ups.map((item, position) => position === index ? event.currentTarget.value : item))}
        />
        <button type="button" onClick={() => change('follow_ups', draft.follow_ups.filter((_item, position) => position !== index))}>{t("ScheduleTab.7e5608ab61")}</button>
      </div>)}
      {draft.follow_ups.length < 20 && <button
        type="button"
        onClick={() => change('follow_ups', [...draft.follow_ups, ''])}
      >{t("ScheduleTab.bef78f4933")}</button>}
    </fieldset>
    <div class="schedule-editor-actions">
      <button type="button" onClick={onCancel}>{t("ScheduleTab.19766ed6cc")}</button>
      {/* A fork schedule with no chosen point has nothing to cut at, and the daemon
          would refuse it. Held here so the refusal is a disabled button with a visible
          empty list rather than an error after a save. */}
      <button
        class="primary"
        type="submit"
        disabled={busy || (resuming && draft.target_kind === 'fork_point' && !draft.target_cut_message_id)}
      >{busy ? t("ScheduleTab.23e39291d6") : t("ScheduleTab.1509f561f2")}</button>
    </div>
  </form>
}
