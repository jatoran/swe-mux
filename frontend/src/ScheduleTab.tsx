import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { promptDeliveryHarnesses } from './harnessRegistry'
import {
  BLOCKED_LABELS, OUTCOME_LABELS, absoluteLabel, cadenceLabel, draftFromSchedule, draftToBody,
  emptyDraft, needsAttention, orderSchedules, untilLabel,
  type Schedule, type ScheduleDraft, type ScheduleStatus,
} from './schedules'
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
//    prevent.

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
  /** Where the `scheduled_runs` opt-in lives, for a blocked row's one-click fix. */
  onOpenProjectSettings: (projectId: string) => void
  onDone: () => void
}

type ListResponse = { schedules: Schedule[]; status: ScheduleStatus }

const REFRESH_MS = 30_000

export function ScheduleTab({
  project, projects, profiles, scope, onScope, onOpenSession, onOpenProjectSettings, onDone,
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
    <div class="schedule-body">
      {loading && !ordered.length && <p class="schedule-empty">Reading schedules…</p>}
      {error && <p class="schedule-error" role="alert">{error}</p>}
      {!loading && !ordered.length && <p class="schedule-empty">
        {scope
          ? 'Nothing scheduled in this Project. A schedule starts an agent session for you - a nightly health check, a weekly sweep - and can pre-queue what to say next.'
          : 'Nothing scheduled anywhere yet.'}
      </p>}
      {ordered.map(schedule => <section
        class={`schedule-row ${schedule.enabled ? '' : 'paused'} ${schedule.blocked ? 'blocked' : ''}`}
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
          </small>
        </button>
        {schedule.blocked && <p class="schedule-blocked">
          {BLOCKED_LABELS[schedule.blocked] || schedule.blocked}
          {schedule.blocked === 'automation_disabled' && <button
            class="link"
            onClick={() => { onOpenProjectSettings(schedule.project_id); onDone() }}
          >Turn it on</button>}
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
      <small>Starts an agent session on its own. It is not a background job: it opens a real pane you can read.</small>
    </header>
    {error && <p class="schedule-error" role="alert">{error}</p>}
    <label>Name<input
      autofocus
      value={draft.label}
      maxLength={80}
      placeholder="Nightly health check"
      onInput={event => change('label', event.currentTarget.value)}
    /></label>
    <label>Prompt<textarea
      rows={4}
      value={draft.prompt}
      placeholder="Check the deployment's health, and report PASS or the findings."
      onInput={event => change('prompt', event.currentTarget.value)}
    /></label>
    <label>Trigger<select value={draft.trigger_kind} onChange={event => change('trigger_kind', event.currentTarget.value as ScheduleDraft['trigger_kind'])}>
      <option value="cron">On a schedule (cron)</option>
      <option value="interval">Every N minutes</option>
      <option value="once">Once, at a time</option>
    </select></label>
    {draft.trigger_kind === 'cron' && <label>Cron<input
      value={draft.cron}
      spellcheck={false}
      placeholder="0 3 * * *"
      onInput={event => change('cron', event.currentTarget.value)}
    /><small>minute hour day-of-month month day-of-week</small></label>}
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
    <label>Agent<select value={draft.backend} onChange={event => onDraft({ ...draft, backend: event.currentTarget.value, profile_id: '' })}>
      <option value="">This Project's default</option>
      {harnesses.map(harness => <option key={harness.name} value={harness.name}>{harness.display_name}</option>)}
    </select></label>
    {draft.backend && profilesForBackend.length > 0 && <label>Launch profile<select
      value={draft.profile_id}
      onChange={event => change('profile_id', event.currentTarget.value)}
    >
      <option value="">Default arguments</option>
      {profilesForBackend.map(profile => <option key={profile.id} value={profile.id}>{profile.label}</option>)}
    </select><small>A profile is where the model flag lives.</small></label>}
    <label>Session name <em>optional</em><input
      value={draft.session_name}
      maxLength={80}
      placeholder="same as the schedule name"
      onInput={event => change('session_name', event.currentTarget.value)}
    /></label>
    <label>If the last run is still open<select value={draft.overlap} onChange={event => change('overlap', event.currentTarget.value as ScheduleDraft['overlap'])}>
      <option value="skip">Skip this run</option>
      <option value="allow">Start another session</option>
    </select></label>
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
      <button class="primary" type="submit" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
    </div>
  </form>
}
