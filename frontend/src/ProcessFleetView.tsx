import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
import { useEffect, useMemo, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { api } from './api'
import { Dropdown } from './Dropdown'
import type { Project, Session } from './types'
import { buildProcessTree, type ProcessTreeNode } from './processTree'
import { projectDropdownOptions } from './projectOptions'
import { buildProjectGroups } from './projectGroups'
import {
  commandTail, isAbnormalState, memoryLabel, processDetails, processMetrics, processRowKey,
  processState, rollupLabel, sessionRollup,
} from './processRows'
import {
  fleetViewTotals, scopedSessionGroups,
  type FleetSnapshot, type Preview, type ProcessItem, type SessionProcesses,
} from './processFleet'
import { invalidateFleet, refreshFleet, setFleetSessions, subscribeFleet } from './processFleetFeed'
import { detectedServers } from './sessionProcesses'
import { portConflictNotice } from './previewPortConflict'
import { sessionDisplayName } from './sessionNames'

/**
 * Process inspection, drawn the same way wherever it appears.
 *
 * There used to be two surfaces with two answers: a modal inspector with the tree, the evidence,
 * and the terminate actions, and a drawer tab with per-session rollups and nothing else. The
 * split was defended as a width argument — a two-click destructive confirm in a 300 px column is
 * how the wrong tree gets killed — but the cost was that the surface you actually have open
 * beside a terminal could not answer the question you opened it with, and every real
 * investigation ended in "now open the other one".
 *
 * The width argument was about *layout*, not about *capability*, so it is answered with layout:
 * this component is a container query, the narrow rendering is the one the modal already used on
 * a phone, and the confirmation step is the same two-press confirm in both. What differs between
 * the drawer and the modal is chrome and default scope. Nothing differs in what is visible or
 * what can be done.
 *
 * Polling is shared (`processFleetFeed`), so mounting this twice — the drawer can be split, and
 * the modal can be opened over it — costs one request per tick, not one per surface.
 */

type Props = {
  sessions: Session[]
  projects: Project[]
  /** `panel` is the modal's full-width chrome; `drawer` is the side column's. */
  variant: 'panel' | 'drawer'
  /** '' means every Project. Owned by the caller so it survives a drawer tab switch. */
  projectScope: string
  onProjectScope: (scope: string) => void
  /** Drill-down. `null` draws every session in scope. */
  selectedSessionId: string | null
  onSelectedSessionId: (sessionId: string | null) => void
  /** The focused terminal: pinned first and marked, so "what is *this* session running" needs
   *  no drill-down and no scope change. The modal passes null — it is opened *at* a session. */
  focusedSessionId?: string | null
  onAttached: (preview: Preview, project: Project) => void
  /** Reveal a session's terminal. Offered as an explicit control rather than by overloading the
   *  heading, which drills in. */
  onRevealSession?: (sessionId: string) => void
}

const sessionLabel = (session: Session | undefined, sessionId: string) =>
  (session && sessionDisplayName(session)) || sessionId

export function ProcessFleetView({
  sessions, projects, variant, projectScope, onProjectScope,
  selectedSessionId, onSelectedSessionId, focusedSessionId = null,
  onAttached, onRevealSession,
}: Props) {
  const [snapshot, setSnapshot] = useState<FleetSnapshot | null>(null)
  const [registered, setRegistered] = useState<Preview[]>([])
  const [error, setError] = useState('')
  // Empty rather than a seeded port: a seeded 3000 read as an assumed dev-server
  // port. The placeholder shows the shape without implying a running server.
  const [customUrl, setCustomUrl] = useState('')
  const [confirm, setConfirm] = useState('')
  // Ended processes are opt-in: nothing can be done to them and they are already
  // excluded from every total, so they are history rather than fleet state.
  const [includeEnded, setIncludeEnded] = useState(false)
  // Keyed by durable process identity, not PID, so a row survives the refresh that
  // replaces every object in the snapshot.
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set())

  // Declared before the subscription so the feed's older-daemon fallback has the session list
  // it enumerates by the time the first read runs.
  useEffect(() => { setFleetSessions(sessions) }, [sessions])
  const request = useMemo(
    () => ({ sessionId: selectedSessionId, includeEnded }), [selectedSessionId, includeEnded, uiLocale.value, catalogRevision.value])
  useEffect(() => subscribeFleet(request, result => {
    setSnapshot(result.snapshot)
    setRegistered(result.previews)
    setError(result.error)
  }), [request])
  useEffect(() => {
    if (!confirm) return
    const timer = window.setTimeout(() => setConfirm(''), 2000)
    return () => clearTimeout(timer)
  }, [confirm])

  const sessionGroups = useMemo(
    () => scopedSessionGroups(snapshot, sessions, projectScope, selectedSessionId), [snapshot, sessions, projectScope, selectedSessionId, uiLocale.value, catalogRevision.value])
  const projectProcessGroups = useMemo(
    () => buildProjectGroups(sessionGroups, sessions, projects, focusedSessionId), [sessionGroups, sessions, projects, focusedSessionId, uiLocale.value, catalogRevision.value])
  const scoped = !!projectScope || !!selectedSessionId
  const totals = fleetViewTotals(snapshot, sessionGroups, scoped)
  const ownershipDiagnostics = (snapshot?.ownership_diagnostics || []).slice(-10).reverse()
  const selectedSession = sessions.find(item => item.id === selectedSessionId) || null

  const act = async (sessionId: string, process: ProcessItem, action: 'interrupt' | 'terminate' | 'terminate_tree') => {
    const key = `${sessionId}:${process.pid}:${action}`
    if (action !== 'interrupt' && confirm !== key) { setConfirm(key); return }
    try {
      await api('POST', '/api/processes/action', {
        session_id: sessionId, pid: process.pid, identity_id: process.identity_id, action,
      })
      setConfirm('')
      invalidateFleet()
    } catch (cause) { setError((cause as Error).message) }
  }
  const toggleExpanded = (key: string) => setExpanded(current => {
    const next = new Set(current)
    if (!next.delete(key)) next.add(key)
    return next
  })
  const attach = async (sessionId: string, url: string, approved = false) => {
    try {
      const result = await api<{ preview: Preview; project: Project }>('POST', '/api/previews', {
        session_id: sessionId, url, approved, attach: true,
        target_session_id: sessionId, direction: 'horizontal',
      })
      onAttached(result.preview, result.project)
    } catch (cause) { setError((cause as Error).message) }
  }

  const renderGroup = (group: SessionProcesses) => {
    const session = sessions.find(item => item.id === group.session_id)
    const project = projects.find(item => item.id === (session?.project_id || group.project_id))
    const previews = registered.filter(item => item.session_id === group.session_id)
    // Deduped by port rather than listed raw: a server bound to both stacks appears as two
    // listeners and would otherwise draw the same endpoint twice, once un-previewable.
    const servers = detectedServers(group.processes)
    const focused = !!focusedSessionId && group.session_id === focusedSessionId
    const renderProcessNode = (node: ProcessTreeNode<ProcessItem>): JSX.Element => {
      const process = node.process
      const terminateKey = `${group.session_id}:${process.pid}:terminate`
      const treeKey = `${group.session_id}:${process.pid}:terminate_tree`
      const state = processState(process)
      const key = processRowKey(process)
      const open = expanded.has(key)
      return <li class={`${process.exited_at ? 'ended ' : ''}evidence-${state}${open ? ' open' : ''}`} key={key}>
        <button class="process-row" aria-expanded={open} title={process.command || undefined} onClick={() => toggleExpanded(key)}>
          <i class="process-caret" aria-hidden="true"/>
          <i class={`process-state-dot ${state}`} title={t("ProcessFleetView.ca5217646f", { value1: state.replace('_', ' ') })}/>
          <strong>{process.executable}</strong>
          <span class="process-pid">{process.pid}</span>
          <span class="process-args">{commandTail(process)}</span>
          {process.conditions.length > 0 && <em class="process-warn" title={process.conditions.join(' · ')}>⚠ {process.conditions.length}</em>}
          {isAbnormalState(state) && <b class={`process-evidence ${state}`}>{state.replace('_', ' ')}</b>}
          <span class="process-metrics">{processMetrics(process)}</span>
        </button>
        {open && <div class="process-detail">
          <dl>{processDetails(process).map(detail => <div key={detail.label}><dt>{detail.label}</dt><dd>{detail.value}</dd></div>)}</dl>
          <div class="process-actions"><button onClick={() => void navigator.clipboard.writeText(String(process.pid))}>{t("ProcessFleetView.175c2e931a")}</button><button onClick={() => void navigator.clipboard.writeText(process.command || String(process.pid))}>{t("ProcessFleetView.9a01feecae")}</button><button disabled={!!process.exited_at} title={t("ProcessFleetView.e6baa7a529")} onClick={() => void act(group.session_id, process, 'interrupt')}>{t("ProcessFleetView.b625231412")}</button><button disabled={!!process.exited_at} title={t("ProcessFleetView.c2f7a58854")} class={confirm === terminateKey ? 'confirming' : ''} onClick={() => void act(group.session_id, process, 'terminate')}>{confirm === terminateKey ? '✓' : t("ProcessFleetView.f913f09210")}</button><button disabled={!!process.exited_at} title={t("ProcessFleetView.c2934aa4e1")} class={confirm === treeKey ? 'confirming' : ''} onClick={() => void act(group.session_id, process, 'terminate_tree')}>{confirm === treeKey ? '✓' : t("ProcessFleetView.4edf58573d")}</button></div>
        </div>}
        {node.children.length > 0 && <ul>{node.children.map(renderProcessNode)}</ul>}
      </li>
    }
    // The Project heading directly above already names the Project, and the rollup only
    // earns its place once it aggregates more than the single row below it.
    const rollup = sessionRollup(group.processes)
    return <section class={`process-session-group${focused ? ' focused' : ''}`} key={group.session_id}>
      <div class="process-session-bar">
        <button
          class="process-session-heading"
          aria-current={focused ? 'true' : undefined}
          title={t("ProcessFleetView.39bbfa1f05", { value1: project?.name || t("ProcessFleetView.e222820974"), value2: sessionLabel(session, group.session_id), value3: (selectedSessionId) ? 'yes' : 'no' })}
          onClick={() => onSelectedSessionId(selectedSessionId === group.session_id ? null : group.session_id)}
        >
          <span>
            <i class={`state-dot ${session?.state || 'running'}`}/>
            <strong>{sessionLabel(session, group.session_id)}</strong>
            {focused && <em class="process-session-focused" title={t("ProcessFleetView.c39ff383fe")}>{t("ProcessFleetView.b0185429c6")}</em>}
          </span>
          {rollup && <small>{rollupLabel(rollup)}</small>}
        </button>
        {onRevealSession && <button
          class="process-session-reveal"
          title={t("ProcessFleetView.250b4aa6d0")}
          aria-label={t("ProcessFleetView.cd5c1cf423", { value1: sessionLabel(session, group.session_id) })}
          onClick={() => onRevealSession(group.session_id)}
        >↗</button>}
      </div>
      {/* One line each, for the same reason the process rows are: the URL and its owner are
          the whole content, and a heading plus a stacked button pair cost four rows to say it. */}
      {previews.map(preview => <div class="process-link-row" key={preview.id}>
        <i class="process-link-mark registered" title={t("ProcessFleetView.bbb5d524e3")}>▣</i>
        <strong>{preview.url}</strong>
        <span>{rich("ProcessFleetView.a4f718124c", { source: preview.source, port: preview.port })}</span>
        {preview.port_conflict && <em class="process-port-shared" title={portConflictNotice(preview.port_conflict)?.headline}>{t("ProcessFleetView.f5cfcd5be3")}</em>}
        <button onClick={() => void navigator.clipboard.writeText(preview.url)}>{t("ProcessFleetView.6f5a6034e7")}</button>
      </div>)}
      {servers.map(server => <div class="process-link-row" key={`${server.pid}:${server.port}`}>
        <i class="process-link-mark" title={t("ProcessFleetView.c4d004a6f4")}>⇢</i>
        <strong>{server.url}</strong>
        <span>{group.processes.find(process => process.pid === server.pid)?.executable} {server.pid}</span>
        {server.shared_with && <em class="process-port-shared" title={t("ProcessFleetView.a04eb49735", { port: server.port, value2: server.shared_with.join(', '), pid: server.pid })}>{rich("ProcessFleetView.22a345c2c0", { value1: server.shared_with.join(', ') })}</em>}
        <button title={t("ProcessFleetView.de904322f0", { url: server.url })} onClick={() => void attach(group.session_id, server.url)}>{t("ProcessFleetView.5975cf1bba")}</button>
        <button onClick={() => void navigator.clipboard.writeText(server.url)}>{t("ProcessFleetView.6f5a6034e7")}</button>
      </div>)}
      <ul class="process-list process-tree">{buildProcessTree(group.processes).map(renderProcessNode)}</ul>
    </section>
  }

  const renderDaemonGroup = () => {
    const daemon = snapshot?.daemon
    // The runtime belongs to no Project, so a Project-scoped view that listed it would be
    // reporting something the scope says is not there.
    if (!daemon || selectedSessionId || projectScope) return null
    const members = daemon.members || []
    const renderDaemonNode = (node: ProcessTreeNode<ProcessItem>): JSX.Element => {
      const process = node.process
      const role = process.pid === daemon.pid ? 'daemon' : 'infrastructure'
      const key = processRowKey(process)
      const open = expanded.has(key)
      // Runtime members carry no session attribution or evidence chain, so they get the
      // observational subset rather than blank rows where those would be.
      const details = [
        { get label() { return t("ProcessFleetView.5d347fd948") }, value: process.command || t("ProcessFleetView.c564afe628") },
        { get label() { return t("ProcessFleetView.e47125968b") }, value: process.parent_pid ? `PID ${process.parent_pid}` : t("ProcessFleetView.05705fa0ab") },
        { get label() { return t("ProcessFleetView.4b168d88dc") }, value: role === 'daemon' ? 'swe-mux server process' : 'daemon-owned child not attributed to a terminal session' },
        ...processDetails(process).filter(detail => detail.label === 'network' || detail.label === 'warnings'),
      ]
      return <li class={open ? 'open' : ''} key={key}>
        <button class="process-row" aria-expanded={open} title={process.command || undefined} onClick={() => toggleExpanded(key)}>
          <i class="process-caret" aria-hidden="true"/>
          <i class={`process-state-dot ${role === 'daemon' ? 'active' : ''}`} title={role}/>
          <strong>{process.executable}</strong>
          <span class="process-pid">{process.pid}</span>
          <span class="process-args">{commandTail(process)}</span>
          {process.conditions.length > 0 && <em class="process-warn" title={process.conditions.join(' · ')}>⚠ {process.conditions.length}</em>}
          {role === 'daemon' && <b class="process-evidence active">{t("ProcessFleetView.f77b12a53e")}</b>}
          <span class="process-metrics">{processMetrics(process)}</span>
        </button>
        {open && <div class="process-detail">
          <dl>{details.map(detail => <div key={detail.label}><dt>{detail.label}</dt><dd>{detail.value}</dd></div>)}</dl>
          {/* Observational only: the runtime is never interrupt/terminate territory. */}
          <div class="process-actions"><button onClick={() => void navigator.clipboard.writeText(String(process.pid))}>{t("ProcessFleetView.175c2e931a")}</button><button onClick={() => void navigator.clipboard.writeText(process.command || String(process.pid))}>{t("ProcessFleetView.9a01feecae")}</button></div>
        </div>}
        {node.children.length > 0 && <ul>{node.children.map(renderDaemonNode)}</ul>}
      </li>
    }
    return <section class="process-project-group process-daemon-group">
      <h2 data-heading-message="ProcessFleetView.37e808aa51">{t("ProcessFleetView.37e808aa51")}</h2>
      <section class="process-session-group">
        <div class="process-session-bar"><div class="process-session-heading process-daemon-heading"><span><i class="state-dot running"/><strong>{t("ProcessFleetView.301ae7e557")}</strong></span><small>{rich("ProcessFleetView.8400d9e148", { processes: daemon.processes, value2: daemon.cpu_pct.toFixed(1), value3: memoryLabel(daemon.memory_bytes), value4: daemon.listeners || daemon.connections ? t("ProcessFleetView.551d015a20", { value1: daemon.listeners || 0, value2: daemon.connections || 0 }) : '' })}</small></div></div>
        {members.length > 0 ? <ul class="process-list process-tree">{buildProcessTree(members).map(renderDaemonNode)}</ul> : <p class="process-empty">{t("ProcessFleetView.779556f6f0")}</p>}
      </section>
    </section>
  }

  const endedToggle = <button
    class={`process-ended-toggle ${includeEnded ? 'active' : ''}`}
    aria-pressed={includeEnded}
    title={includeEnded ? t("ProcessFleetView.33fabd292d") : t("ProcessFleetView.c8aa6126d9")}
    onClick={() => setIncludeEnded(current => !current)}
  >{includeEnded ? t("ProcessFleetView.452044b979") : t("ProcessFleetView.e87ba7a0b7")}</button>

  // Opening at a Project prefilters the fleet, but the scope stays a visible, clearable
  // control rather than a hidden mode — on both surfaces, so a drawer scoped to the active
  // Project can still answer a question about the whole machine.
  const scopeSelect = <Dropdown
    class="process-scope-select"
    ariaLabel={t("ProcessFleetView.8f5c6aac57")}
    value={projectScope}
    onChange={value => { onProjectScope(value); onSelectedSessionId(null) }}
    filter
    filterPlaceholder={t("ProcessFleetView.2e0060c45e")}
    options={[
      { value: '', get label() { return t("ProcessFleetView.4b87271b6b") } },
      ...projectDropdownOptions(projects, project => ({ value: project.id, label: project.name, detail: project.root })),
    ]}
  />

  const refresh = <button class="process-refresh" title={t("ProcessFleetView.93768d333d")} onClick={() => refreshFleet(request)}>{t("ProcessFleetView.0e91610117")}</button>

  const summary = <div class="process-fleet-summary">
    {scopeSelect}
    <span>{rich("ProcessFleetView.ecbddcb0b5", { sessions: totals.sessions, value2: (totals.sessions === 1) ? 'yes' : 'no' })}</span>
    <span>{rich("ProcessFleetView.977641b5f1", { processes: totals.processes })}</span>
    <span>{rich("ProcessFleetView.d8c52ecda0", { value1: totals.cpu_pct.toFixed(1) })}</span>
    <span>{rich("ProcessFleetView.586b04cdff", { value1: memoryLabel(totals.memory_bytes) })}</span>
    <span>{rich("ProcessFleetView.89d6c6c5b5", { listeners: totals.listeners, connections: totals.connections })}</span>
    {endedToggle}
    {refresh}
  </div>

  // Drilled into one session, the toolbar swaps the fleet figures for the one control that only
  // makes sense there: vouching for a URL this session's listeners cannot prove it owns.
  const sessionToolbar = selectedSession && <div class="process-toolbar">
    <input
      value={customUrl}
      aria-label={t("ProcessFleetView.fc2a9089a4")}
      placeholder={t("ProcessFleetView.c51203df6e")}
      onInput={event => setCustomUrl(event.currentTarget.value)}
    />
    <button
      disabled={!customUrl.trim()}
      title={t("ProcessFleetView.a159d73a80")}
      onClick={() => void attach(selectedSession.id, customUrl, true)}
    >{t("ProcessFleetView.b5fed4f2c9")}</button>
    {endedToggle}
    {refresh}
  </div>

  return <div class={`process-fleet-view ${variant}`}>
    {selectedSession ? sessionToolbar : summary}
    <div class="process-fleet-list">
      {error && <p class="process-error" aria-live="assertive">{error}</p>}
      {!snapshot && !error && <p class="process-empty">{t("ProcessFleetView.26d53d28a1")}</p>}
      {snapshot && !snapshot.available && <p class="process-empty">{snapshot.diagnostic}</p>}
      {ownershipDiagnostics.length > 0 && <details class="process-ownership-diagnostics">
        <summary>{rich("ProcessFleetView.a1ac81c13e", { length: ownershipDiagnostics.length })}</summary>
        <ul>{ownershipDiagnostics.map((item, index) => <li key={`${item.ts}:${item.kind}:${item.pid || 0}:${index}`}>
          <strong>{item.kind.replaceAll('_', ' ')}</strong>
          <span>{item.pid ? t("ProcessFleetView.4a52d42108", { pid: item.pid }) : t("ProcessFleetView.19ed40bf62")}{item.session_id ? t("ProcessFleetView.6248166fd7", { session_id: item.session_id }) : ''}{item.other_session_id ? t("ProcessFleetView.858d14140d", { other_session_id: item.other_session_id }) : ''}{item.parent_pid ? t("ProcessFleetView.4c73845d9d", { parent_pid: item.parent_pid }) : ''}{item.reason ? t("ProcessFleetView.a1712aaece", { reason: item.reason }) : ''} · {new Date(item.ts * 1000).toLocaleString(uiLocale.value)}</span>
        </li>)}</ul>
      </details>}
      {renderDaemonGroup()}
      {projectProcessGroups.map(group => <section class="process-project-group" key={group.id}>
        <h2>{rich("ProcessFleetView.82bf9de762", { label: group.label })}</h2>
        {group.groups.map(renderGroup)}
      </section>)}
      {snapshot?.available && !sessionGroups.length && <p class="process-empty">
        {projectScope ? t("ProcessFleetView.94c70170ad") : t("ProcessFleetView.cd353b3aed")}
      </p>}
    </div>
  </div>
}
