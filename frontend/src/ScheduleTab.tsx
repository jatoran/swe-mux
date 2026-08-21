import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { GrantButton, GrantGate } from './GrantGate'
import { promptDeliveryHarnesses } from './harnessRegistry'
import {
  branchModeFor, branchPointEligible, branchPointPreview, branchPointsEmptyMessage,
  orderedBranchPoints, type BranchPoint, type HistoryBranchPoints,
} from './branchPoints'
import {
  BLOCKED_LABELS, CRON_PRESETS, OUTCOME_LABELS, TARGET_KIND_COPY, absoluteLabel, actionLabel,
  cadenceLabel, draftFromSchedule, draftToBody, emptyDraft, needsAttention, orderSchedules,
  presetForCron, targetIsMissing, untilLabel,
  type Schedule, type ScheduleDraft, type ScheduleStatus, type ScheduleTargetKind,
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
    return () => clearInterval(timer)
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

  const ordered = useMemo(() => orderSchedules(schedules), [schedules])
  const attention = useMemo(() => needsAttention(schedules), [schedules])
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
      if (!target) throw new Error('Select a Project before adding a schedule.')
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
      <select
        aria-label="Filter schedules by project"
        value={scope}
        onChange={event => onScope(event.currentTarget.value)}
      >
        <option value={project?.id || ''} disabled={!project}>{project ? project.name : 'No project'}</option>
        <option value="">All projects</option>
      </select>
      <button title="Re-read schedules now" onClick={() => void load()}>refresh</button>
      <button class="primary" disabled={!scope && !project} onClick={startNew}>new…</button>
    </div>
    <p class="schedule-summary">
      {status
        ? `${status.armed} armed · ${status.live_sessions}/${status.max_concurrent} live${status.enabled ? '' : ' · switched off for this install'}`
        : 'Reading schedules…'}
      {attention.length ? ` · ${attention.length} need${attention.length === 1 ? 's' : ''} attention` : ''}
    </p>
    {/* The install stop is not a row-level condition — it silences every schedule in every
        Project at once — so it is stated once above the list rather than repeated on each
        row that happens to be loaded. */}
    {status && !status.enabled && <GrantGate ids={['schedules.install']}
      heading="Scheduled runs are switched off for this install."
      onGranted={load}>
      <p>Nothing below can fire, whatever any Project opted into. Turning it on starts
      nothing by itself: each Project still permits scheduled runs separately, and the
      schedules themselves stay exactly as they are.</p>
    </GrantGate>}
    <div class="schedule-body">
      {loading && !ordered.length && <p class="schedule-empty">Reading schedules…</p>}
      {error && <p class="schedule-error" role="alert">{error}</p>}
      {!loading && !ordered.length && <p class="schedule-empty">
        {scope
          ? 'Nothing scheduled in this Project. A schedule starts an agent session for you - a nightly health check, a weekly sweep - and can pre-queue what to say next. To pick a conversation back up later instead, open it in History and choose "Resume later…".'
          : 'Nothing scheduled anywhere yet.'}
      </p>}
      {ordered.map(schedule => <section
        class={`schedule-row ${schedule.enabled ? '' : 'paused'} ${schedule.blocked || targetIsMissing(schedule) ? 'blocked' : ''}`}
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
            {schedule.enabled ? untilLabel(schedule.next_fire_at) : 'paused'}
            {' · '}
            {actionLabel(schedule)}
          </small>
        </button>
        {/* A resume against a conversation that has been deleted reads armed and can
            never fire, which is the same lie `blocked` exists to prevent. Said on the
            row rather than left for the run history, because the next fire that would
            record it may be a week away. */}
        {targetIsMissing(schedule) && <p class="schedule-blocked">
          The conversation this resumes is no longer in History, so it can never open.
        </p>}
        {/* Both blocked reasons are switches, and each row offers the one that is holding
            it: the Project's own opt-in, or the install-wide stop that overrides every
            Project. The install case previously stated the reason and left the reader to
            find the switch. */}
        {schedule.blocked && <p class="schedule-blocked">
          {BLOCKED_LABELS[schedule.blocked] || schedule.blocked}
          {' '}
          {schedule.blocked === 'automation_disabled' && <GrantButton
            id="project.scheduledRuns" projectId={schedule.project_id} onGranted={load}
          >Permit them here</GrantButton>}
          {schedule.blocked === 'install_disabled' && <GrantButton
            id="schedules.install" onGranted={load}
          >Turn it on</GrantButton>}
        </p>}
        <div class="schedule-meta">
          <span title={schedule.last_reason || undefined}>
            {schedule.last_outcome
              ? `${OUTCOME_LABELS[schedule.last_outcome] || schedule.last_outcome} ${absoluteLabel(schedule.last_fire_at)}`
              : 'never run'}
          </span>
          {schedule.last_session_id && <button
            class="link"
            title="Reveal the session this schedule last started"
            onClick={() => { onOpenSession(schedule.last_session_id as string); onDone() }}
          >open session</button>}
        </div>
        <div class="schedule-actions">
          <button
            disabled={busy !== ''}
            title="Start this now. Every guard except lateness still applies."
            onClick={() => void act(`run:${schedule.id}`, () => api('POST', `/api/schedules/${schedule.id}/run`))}
          >{busy === `run:${schedule.id}` ? 'starting…' : 'run now'}</button>
          <button
            disabled={busy !== ''}
            onClick={() => void act(`arm:${schedule.id}`, () => api('PATCH', `/api/schedules/${schedule.id}`, { enabled: !schedule.enabled }))}
          >{schedule.enabled ? 'pause' : 'resume'}</button>
          <button disabled={busy !== ''} onClick={() => startEdit(schedule)}>edit</button>
          <button
            class="danger"
            disabled={busy !== ''}
            onClick={() => void act(`delete:${schedule.id}`, () => api('DELETE', `/api/schedules/${schedule.id}`))}
          >delete</button>
        </div>
        {expanded === schedule.id && <div class="schedule-detail">
          {schedule.action === 'resume' && schedule.target && !schedule.target.missing && <p class="schedule-target">
            <strong>{TARGET_KIND_COPY[schedule.target_kind].label}</strong>
            <small>
              {`[${schedule.target.backend}] ${schedule.target.name}`}
              {/* Only worth a second line when the two differ, which is exactly when
                  the pinned row is no longer where the work is. */}
              {schedule.target_kind === 'latest_of_session'
                && schedule.target.resolved_run_id !== schedule.target.run_id
                && ` · now at ${schedule.target.resolved_name}`}
              {schedule.context_ceiling_pct > 0
                && ` · stops above ${Math.round(schedule.context_ceiling_pct * 100)}% context`}
            </small>
          </p>}
          <p class="schedule-prompt">{schedule.prompt}</p>
          {schedule.follow_ups.length > 0 && <p class="schedule-followups">
            {schedule.follow_ups.length} message{schedule.follow_ups.length === 1 ? '' : 's'} pre-queued behind it
          </p>}
          <ol class="schedule-runs">
            {!schedule.runs.length && <li class="schedule-empty">No runs recorded yet.</li>}
            {schedule.runs.map(run => <li key={run.id} class={`schedule-run ${run.outcome}`}>
              <span>{OUTCOME_LABELS[run.outcome] || run.outcome}</span>
              <small>{absoluteLabel(run.started_at)}{run.origin === 'manual' ? ' · manual' : ''}</small>
              {run.reason && <em title={run.reason}>{run.reason}</em>}
              {run.session_id && <button class="link" onClick={() => { onOpenSession(run.session_id as string); onDone() }}>session</button>}
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

  const ordered = useMemo(() => orderedBranchPoints(points?.points || []), [points])
  const chosen = ordered.find(item => item.message_id === draft.target_cut_message_id) || null
  const pick = (point: BranchPoint) => onDraft({
    ...draft,
    target_cut_message_id: point.message_id,
    target_cut_mode: branchModeFor(point),
  })

  return <>
    <p class="schedule-target-summary">
      <strong>Resumes</strong>
      <span>{draft.target_backend ? `[${draft.target_backend}] ` : ''}{draft.target_label || draft.target_run_id}</span>
    </p>
    <label>What to reopen<select
      value={draft.target_kind}
      onChange={event => onDraft({
        ...draft,
        target_kind: event.currentTarget.value as ScheduleTargetKind,
        // Each kind reads a different field, and carrying the other one across would
        // let a saved schedule keep a cut point it no longer forks at.
        target_cut_message_id: '',
        target_cut_mode: '',
        context_ceiling_pct: event.currentTarget.value === 'latest_of_session'
          ? (draft.context_ceiling_pct || 0.7)
          : 0,
      })}
    >
      {(Object.keys(TARGET_KIND_COPY) as ScheduleTargetKind[]).map(kind =>
        <option key={kind} value={kind}>{TARGET_KIND_COPY[kind].label}</option>)}
    </select><small>{TARGET_KIND_COPY[draft.target_kind].detail}</small></label>
    {draft.target_kind === 'latest_of_session' && <label>Stop above<input
      type="number"
      min={0}
      max={100}
      value={Math.round(draft.context_ceiling_pct * 100)}
      onInput={event => onDraft({
        ...draft,
        context_ceiling_pct: Math.max(0, Math.min(100, Number(event.currentTarget.value))) / 100,
      })}
    /><small>
      Percent of the context window. Above it the run is skipped and recorded instead
      of started: the early half of the conversation has already been compacted into a
      summary by then, so continuing buys a turn that no longer remembers what it is
      continuing. 0 removes the ceiling.
    </small></label>}
    {draft.target_kind === 'fork_point' && <fieldset class="schedule-branch-points">
      <legend>Fork at</legend>
      {loading && <p class="schedule-empty">Reading the conversation…</p>}
      {failure && <p class="schedule-error" role="alert">{failure}</p>}
      {!loading && !failure && !ordered.length && <p class="schedule-empty">
        {branchPointsEmptyMessage(points?.reason ?? null, points?.backend || draft.target_backend)}
      </p>}
      {points?.truncated && !!ordered.length && <p class="schedule-note">
        Older messages are not listed, but a fork always carries the conversation from
        its first message.
      </p>}
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
            <strong>{transcriptSpeaker(point.role)} · {mode === 'before' ? 'before this' : 'after this'}</strong>
            <small>{branchPointPreview(point.text) || '(no text in this message)'}</small>
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
        setPreviewError(payload.next_fires.length ? '' : 'This trigger never fires.')
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
      <strong>{projectName ? `Schedule in ${projectName}` : 'Schedule'}</strong>
      <small>{resuming
        ? 'Reopens a conversation on its own. It is not a background job: it opens a real pane you can read.'
        : 'Starts an agent session on its own. It is not a background job: it opens a real pane you can read.'}</small>
    </header>
    {error && <p class="schedule-error" role="alert">{error}</p>}
    {resuming && <ResumeTargetFields draft={draft} onDraft={onDraft} />}
    <label>Name<input
      autofocus={!resuming}
      value={draft.label}
      maxLength={80}
      placeholder={resuming ? 'Pick this back up' : 'Nightly health check'}
      onInput={event => change('label', event.currentTarget.value)}
    /></label>
    <label>{resuming ? 'Then say' : 'Prompt'}<textarea
      rows={4}
      value={draft.prompt}
      placeholder={resuming
        ? 'Carry on where we left off: finish the migration and run the tests.'
        : "Check the deployment's health, and report PASS or the findings."}
      onInput={event => change('prompt', event.currentTarget.value)}
    />{resuming && <small>
      Staged in the resumed pane's queue rather than typed. It is delivered when the
      agent is ready, which means automatically only where this conversation has
      auto-delivery granted; otherwise it waits in the Queue tab for you.
    </small>}</label>
    <label>Trigger<select value={draft.trigger_kind} onChange={event => change('trigger_kind', event.currentTarget.value as ScheduleDraft['trigger_kind'])}>
      <option value="cron">On a schedule (cron)</option>
      <option value="interval">Every N minutes</option>
      <option value="once">Once, at a time</option>
    </select></label>
    {draft.trigger_kind === 'cron' && <label>Cron
      {/* The preset beside the field rather than instead of it: choosing one fills the
          input, so the next edit is to a working expression rather than to a blank box,
          and the field stays the source of truth. */}
      <div class="schedule-cron-row">
        <input
          value={draft.cron}
          spellcheck={false}
          placeholder="0 3 * * *"
          onInput={event => change('cron', event.currentTarget.value)}
        />
        <select
          aria-label="Common schedules"
          value={preset?.cron || ''}
          onChange={event => { if (event.currentTarget.value) change('cron', event.currentTarget.value) }}
        >
          <option value="">{preset ? 'Common…' : 'Custom'}</option>
          {CRON_PRESETS.map(item => <option key={item.cron} value={item.cron}>{item.label}</option>)}
        </select>
      </div>
      <small>{preset ? preset.teaches : 'minute hour day-of-month month day-of-week'}</small>
    </label>}
    {draft.trigger_kind === 'interval' && <label>Every<input
      type="number"
      min={5}
      max={129_600}
      value={draft.interval_minutes}
      onInput={event => change('interval_minutes', Number(event.currentTarget.value))}
    /><small>minutes, counted from the previous run</small></label>}
    {draft.trigger_kind === 'once' && <label>At<input
      type="datetime-local"
      value={draft.run_at_local}
      onInput={event => change('run_at_local', event.currentTarget.value)}
    /></label>}
    <label>Timezone <em>optional</em><input
      value={draft.timezone}
      spellcheck={false}
      placeholder="this computer's time"
      onInput={event => change('timezone', event.currentTarget.value)}
    /><small>An IANA name such as Europe/Oslo. Empty follows this machine, daylight saving included.</small></label>
    <p class={`schedule-preview ${previewError ? 'bad' : ''}`}>
      {previewError || (fires.length ? `Next: ${fires.slice(0, 3).map(absoluteLabel).join(' · ')}` : 'Working out the next run…')}
    </p>
    {/* A resume runs the harness the conversation belongs to, with `--resume <id>` as
        its arguments, in the Project root the conversation resolves from. The daemon
        refuses a backend or a profile on one rather than ignoring it, so offering
        either here would be offering control this form does not have. */}
    {!resuming && <label>Agent<select value={draft.backend} onChange={event => onDraft({ ...draft, backend: event.currentTarget.value, profile_id: '' })}>
      <option value="">This Project's default</option>
      {harnesses.map(harness => <option key={harness.name} value={harness.name}>{harness.display_name}</option>)}
    </select></label>}
    {!resuming && draft.backend && profilesForBackend.length > 0 && <label>Launch profile<select
      value={draft.profile_id}
      onChange={event => change('profile_id', event.currentTarget.value)}
    >
      <option value="">Default arguments</option>
      {profilesForBackend.map(profile => <option key={profile.id} value={profile.id}>{profile.label}</option>)}
    </select><small>A profile is where the model flag lives.</small></label>}
    <label>Session name <em>optional</em><input
      value={draft.session_name}
      maxLength={80}
      placeholder={resuming ? "the conversation's own name" : 'same as the schedule name'}
      onInput={event => change('session_name', event.currentTarget.value)}
    /></label>
    {!resuming && <label>If the last run is still open<select value={draft.overlap} onChange={event => change('overlap', event.currentTarget.value as ScheduleDraft['overlap'])}>
      <option value="skip">Skip this run</option>
      <option value="allow">Start another session</option>
    </select></label>}
    {resuming && draft.target_kind !== 'fork_point' && <p class="schedule-note">
      A conversation can only be open once, so a run whose conversation is already
      live - in a pane, or in a background agent - is skipped and recorded rather than
      forced.
    </p>}
    <label class="schedule-check"><input
      type="checkbox"
      checked={draft.catch_up}
      onChange={event => change('catch_up', event.currentTarget.checked)}
    />Run it late if this computer was off<small>Otherwise the missed window is recorded and skipped.</small></label>
    <label>Daily limit<input
      type="number"
      min={0}
      max={500}
      value={draft.daily_run_cap}
      onInput={event => change('daily_run_cap', Number(event.currentTarget.value))}
    /><small>0 leaves only the install-wide ceiling.</small></label>
    <fieldset class="schedule-followup-editor">
      <legend>Then send</legend>
      <small>Queued behind the first prompt through the ordinary prompt queue, so delivery still waits for the agent to be ready.</small>
      {draft.follow_ups.map((body, index) => <div key={index}>
        <textarea
          rows={2}
          value={body}
          onInput={event => change('follow_ups', draft.follow_ups.map((item, position) => position === index ? event.currentTarget.value : item))}
        />
        <button type="button" onClick={() => change('follow_ups', draft.follow_ups.filter((_item, position) => position !== index))}>remove</button>
      </div>)}
      {draft.follow_ups.length < 20 && <button
        type="button"
        onClick={() => change('follow_ups', [...draft.follow_ups, ''])}
      >add a message</button>}
    </fieldset>
    <div class="schedule-editor-actions">
      <button type="button" onClick={onCancel}>Cancel</button>
      {/* A fork schedule with no chosen point has nothing to cut at, and the daemon
          would refuse it. Held here so the refusal is a disabled button with a visible
          empty list rather than an error after a save. */}
      <button
        class="primary"
        type="submit"
        disabled={busy || (resuming && draft.target_kind === 'fork_point' && !draft.target_cut_message_id)}
      >{busy ? 'Saving…' : 'Save'}</button>
    </div>
  </form>
}
