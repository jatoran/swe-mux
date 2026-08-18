import type { JSX } from 'preact'
import { useEffect, useRef } from 'preact/hooks'
import { AgentContextTab } from './AgentContextTab'
import { AgentEnvironmentTab } from './AgentEnvironmentTab'
import { ClipboardTab } from './ClipboardPanel'
import { ActionsTab } from './ActionsTab'
import { NotesTab } from './NotesTab'
import { QueuePane } from './QueuePane'
import { TranscriptTab } from './TranscriptTab'
import { InsightTab } from './InsightTab'
import { ChangeMapPane } from './ChangeMapPane'
import { GitTab } from './GitTab'
import { ProjectResource } from './ProjectResource'
import { NotificationsTab, type NotificationData } from './Notifications'
import { drawerTab, type DrawerTabId } from './drawerTabs'
import {
  drawerCollapseHostStack, drawerTabs, setDrawerSplitRatio,
  type DrawerLayout, type DrawerNode, type DrawerProjectPresentation, type DrawerStack,
} from './drawerLayout'
import { drawerTabVisible, visibleDrawerTabs } from './drawerVisibility'
import { ProcessesTab } from './ProcessesTab'
import { ScheduleTab } from './ScheduleTab'
import type { WatchScope, WatchSnapshot } from './processWatch'
import { parseNoteResourceId } from './layout'
import type { NotePlacement } from './NotesTab'
import { DRAWER_TAB_ICONS } from './railIcons'
import { OverflowRail } from './RailScroller'
import type { SendToAgentRequest, SendToAgentResult, SendToAgentTarget } from './SendToAgentPicker'
import type { LaunchProfile, Project, ProjectBackend, Session } from './types'
import { hasHarnessTranscript } from './harnessRegistry'
import { sessionDisplayName } from './sessionNames'

// Host for the right-edge utility drawer. Two renderings, one component:
//
//  * mobile  — an overlay drawer with a scrim, mirroring the navigation sidebar it
//              is mutually exclusive with.
//  * desktop — an in-flow column of the workspace grid (the caller places it), so
//              the pane tree shrinks instead of being covered. Covering a terminal
//              you opened the drawer to work with is exactly backwards.
//
// The tab strip is inside the drawer on both. On desktop the icon rail outside it
// (rendered by App) is what the *closed* drawer looks like, mirroring the collapsed
// navigation sidebar on the left: it makes these surfaces discoverable without a menu or a
// chord, and it hands its column back to the drawer on open rather than repeating the tab
// strip beside it.
//
// Losing that rail on open also loses the pointer affordance for closing again, so exactly
// one pane heading — the drawer's top-right, see `drawerCollapseHostStack` — carries a
// collapse control. Clicking an already-selected tab still collapses the drawer too; the
// control exists because that gesture is not discoverable.

type Props = {
  layout: DrawerLayout
  presentation: DrawerProjectPresentation
  /** Momentary rail-opened tab. Actions taken there close the drawer on either device. */
  transientTab?: DrawerTabId
  /** Select a tab. A direct click on the pane's already-selected tab asks the
   *  caller to collapse the entire drawer; focus and keyboard navigation do not. */
  onTab: (tab: DrawerTabId, collapseIfSelected?: boolean) => void
  onLayout: (layout: DrawerLayout) => void
  onClose: () => void
  mobile: boolean
  tabDisplay: 'icon' | 'title'
  /** Focused session, for the session-scoped tabs. */
  session: Session | null
  project?: Project
  backend?: ProjectBackend
  notifications: NotificationData
  /** Re-read the inbox after a dismiss/restore, which writes read state server-side. */
  onNotificationsChanged: () => void
  unread: number
  onInsert: (text: string) => 'terminal' | 'editor' | 'none'
  /** Prompt inserts are terminals-only: a template dropped into the note or file the
   *  user last touched edits that document instead of filling a composer. */
  onInsertPrompt: (text: string) => 'terminal' | 'editor' | 'none'
  /** Prompt targets: every session (filtered to this Project's live agents there) and
   *  the same delivery path the send-to-agent dialog uses. */
  sessions: Session[]
  onSendPrompt: (target: SendToAgentTarget, text: string) => Promise<SendToAgentResult>
  onOpenSession: (sessionId: string) => void
  /** Open one conversation in the History overlay, for a session that has ended. */
  onOpenHistoryEntry: (historyId: string) => void
  onOpenSettings: (section: string) => void
  onConfigureActions: () => void
  onManagePrompts: () => void
  /** Files: open a Project-relative path as a pane tab. */
  onOpenFile: (path: string) => void
  /** Git: open a file from an exact repository worktree as a durable pane tab. */
  onOpenWorktreeFile: (worktree: string, path: string) => void
  onProjectUpdated: (project: Project) => void
  /** Files: open the send-to-agent dialog with a tree file's contents. */
  onSendToAgent?: (request: SendToAgentRequest) => void
  /** Files: desktop-only drag of a file row onto a pane. Omitted on mobile, where
   *  the drawer is an overlay and there is no visible pane to drop onto. */
  onFileDragStart?: (path: string, event: JSX.TargetedPointerEvent<HTMLElement>) => void
  /** Notes: the listing's Project/all-Projects scope, owned by the caller so the
   *  `notes.browse` and `notes.browseProject` commands can each pick one. */
  notesAllProjects: boolean
  onNotesAllProjects: (value: boolean) => void
  onOpenNote: (projectId: string, noteId: string, title: string, place: NotePlacement) => void
  onOpenScratchpad: (place: NotePlacement) => void
  /** Processes: the fleet sample `App` already polls. Passed in rather than fetched here so an
   *  open panel adds no process enumeration to the daemon's loop — see `ProcessesTab`. */
  processSnapshot: WatchSnapshot
  projects: Project[]
  processScope: WatchScope
  onProcessScope: (scope: WatchScope) => void
  onRefreshProcesses: () => void
  /** Schedule: its own Project/all-Projects scope, owned by the caller like the
   *  Processes one so it survives a tab switch and a Project change. */
  scheduleScope: string
  onScheduleScope: (scope: string) => void
  /** Schedule: the launch profiles the editor offers, where a model flag lives. */
  profiles: LaunchProfile[]
  /** Register a detected loopback server as a preview tab beside its session. */
  onOpenPreview: (sessionId: string, url: string) => void
  /** Escape hatch to the modal inspector, prefiltered to the tab's current scope. */
  onOpenInspector: (projectId: string | null) => void
  /** Scan timeline's Project permission, context, and auto-arm all live there. */
  onOpenProjectSettings: (projectId: string) => void
  /** Insight → Findings footer: open the full Automation dashboard. */
  onOpenAutomationDashboard: () => void
  /** Notes: the note selected for this Project's persistent sub-tab rail. A note has
   *  one live editor per browser (see `drawerNotes.ts`), so this is also what tells the
   *  matching pane leaf to stand down. */
  drawerNoteId: string | null
  /** One-shot voice-navigation request to make the selected note the insertion target. */
  noteTargetClaimToken?: number
  onNoteTargetClaimed: (token: number) => void
  /** Give it back and put it in a pane, focused. */
  onPopDrawerNoteToTab: (resourceId: string) => void
  onTabDragStart: (event: JSX.TargetedPointerEvent<HTMLElement>, id: DrawerTabId) => void
  /** Mobile only: hold-then-drag reorder of the flattened projection rail. Runs alongside
   *  the projection's long-press menu — a still hold opens the menu, a drag reorders. */
  onProjectionTabReorder?: (event: JSX.TargetedPointerEvent<HTMLElement>, id: DrawerTabId) => void
  /** Tabs the user has put away. Global and device-local, like the arrangement itself. */
  hiddenTabs: readonly DrawerTabId[]
  /** Open the drawer-tab display / visibility menu. The tab it was opened from, when it
   *  was opened from one, is what the menu's `Hide …` row acts on. */
  onTabDisplayMenu: (x: number, y: number, tab?: DrawerTabId) => void
  /** True while a tab is being dragged, so the strip can suppress its own click. */
  draggingTab: DrawerTabId | null
  /** Template handed off by an Action rail button that needs its fields filled. */
  promptPreselect?: { key: string }
  /** Queue: deliberate-open counter used to focus the composer. */
  queueOpenToken?: number
  /** Queue: pop the focused target's queue out into a workspace tab. */
  onQueueOpenAsTab: (sessionId: string) => void
  /** Change Map: pop the focused session's map out into a workspace tab. A graph
   *  wants width, and the drawer column is the narrowest surface in the app. */
  onChangeMapOpenAsTab: (sessionId: string) => void
  /** Queue: open the fleet-wide review overlay, the watch-here/act-there counterpart to
   *  this session-scoped tab. */
  onOpenFleetQueue: () => void
  /** Queue: pending items across every target. Fleet-wide rather than per-session on
   *  purpose — it answers "is anything waiting anywhere", which is the question you have
   *  while looking at some other session, and it labels the way to go find out. */
  queuePending: number
  announcement: string
  /** Desktop only: pointer-drag handle for the column width. Typed as the plain
   *  DOM event so this module needs no `JSX` import for it (which would shadow the
   *  global namespace the intrinsic elements below resolve through). */
  onResize?: (event: PointerEvent) => void
  width: number
  minimumWidth: number
  maximumWidth: number
  defaultWidth: number
  onWidth: (width: number) => void
}

export function UtilityDrawer(props: Props) {
  const { layout, presentation, onTab, onClose, mobile, session, project } = props
  const tabLongPressRef = useRef<{
    pointerId: number
    timer: number
    cleanup: () => void
  } | null>(null)
  const suppressTabClickUntilRef = useRef(0)

  const cancelTabLongPress = (pointerId?: number) => {
    const state = tabLongPressRef.current
    if (!state || (pointerId !== undefined && state.pointerId !== pointerId)) return
    window.clearTimeout(state.timer)
    state.cleanup()
    tabLongPressRef.current = null
  }

  useEffect(() => () => cancelTabLongPress(), [])

  const beginTabLongPress = (event: JSX.TargetedPointerEvent<HTMLElement>, id: DrawerTabId) => {
    if (!mobile || event.pointerType !== 'touch' || !event.isPrimary) return
    cancelTabLongPress()
    const { pointerId, clientX: x, clientY: y } = event
    const move = (moveEvent: PointerEvent) => {
      if (moveEvent.pointerId !== pointerId) return
      if (Math.hypot(moveEvent.clientX - x, moveEvent.clientY - y) > 8) cancelTabLongPress(pointerId)
    }
    const finish = (finishEvent: PointerEvent) => cancelTabLongPress(finishEvent.pointerId)
    const cleanup = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', finish)
      window.removeEventListener('pointercancel', finish)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', finish)
    window.addEventListener('pointercancel', finish)
    const state = { pointerId, cleanup, timer: 0 }
    state.timer = window.setTimeout(() => {
      if (tabLongPressRef.current !== state) return
      suppressTabClickUntilRef.current = performance.now() + 700
      navigator.vibrate?.(20)
      props.onTabDisplayMenu(x, y, id)
    }, 550)
    tabLongPressRef.current = state
  }
  const focusedTab = presentation.focused_tab
  const stackOrder = drawerTabs(layout)
  const mobileStack: DrawerStack = { type: 'stack', id: 'mobile-projection', tabs: stackOrder }
  // Mobile flattens the tree to one stack, so that stack is trivially its own top-right.
  const collapseHostId = mobile ? mobileStack.id : drawerCollapseHostStack(layout.root).id
  // Structural availability and the user's hidden set, resolved together in
  // `drawerVisibility.ts` so the launcher rail outside this component answers the same
  // question the same way. A hidden tab reached by name is peeked, not unhidden.
  const visibility = {
    hidden: props.hiddenTabs,
    hasTranscript: hasHarnessTranscript(session?.backend),
    peek: props.transientTab ?? null,
  }
  const tabAvailable = (id: DrawerTabId) => drawerTabVisible(id, visibility)
  // Acting closes the drawer on mobile (it covers the surface just acted on) and
  // leaves it open on desktop, where the column sits beside that surface and a
  // second insert (or a second file) is the common next action.
  const onDone = () => { if (mobile || props.transientTab) onClose() }

  const noteIdentity = props.drawerNoteId ? parseNoteResourceId(props.drawerNoteId) : null
  // `file` is a legal parse but never a drawer note: files are the Files tab's business, and
  // the claim is only ever written from the Notes index.
  const drawerNote = props.drawerNoteId && project && noteIdentity && noteIdentity.kind !== 'file' && noteIdentity.kind !== 'worktree-file'
    ? { resourceId: props.drawerNoteId, identity: noteIdentity }
    : null
  // Where the last Clipboard insert landed. `ClipboardTab` reports its own outcome to
  // `onInsert` and then calls `onDone` with nothing, so the two are joined here rather than by
  // widening that contract for one caller.
  const lastInsert = useRef<'terminal' | 'editor' | 'none'>('none')
  const insertText = (text: string) => {
    const target = props.onInsert(text)
    lastInsert.current = target
    return target
  }
  /**
   * On mobile, inserting normally closes the drawer: it covers the terminal the text was for.
   * When the text went into the note *this panel is hosting*, closing would hide the very
   * result that was asked for, so the panel stays and returns to the note. Desktop is
   * unchanged — the column sits beside the workspace, and a second insert is the common next
   * action, so nothing should move.
   */
  const onInsertDone = () => {
    if (!mobile) return
    if (drawerNote && lastInsert.current === 'editor') { onTab('notes'); return }
    onDone()
  }

  /**
   * The one body kept mounted across tab switches, and the only one that needs to be.
   *
   * Two things break if this unmounts. Cursor position and undo history die on every switch,
   * which makes the panel unusable for writing. Worse, `insertTarget` refuses a detached
   * editor handle (`isConnected`), so switching to Clipboard to paste *into this note* would
   * route the paste to a terminal instead — silently, and into an agent's prompt. That is the
   * exact failure the "Notes is an index, not an editor" rule used to avoid by not hosting an
   * editor at all; keeping it mounted is what makes hosting one safe.
   *
   * `hidden` rather than conditional rendering for the same reason, and the CSS backs it up
   * (`.drawer-note-host[hidden]` must stay `display:none` even though the class sets a flex
   * layout, since a class rule would otherwise beat the UA default).
   */
  const renderNoteHost = (visible: boolean) => <div class="drawer-note-host" hidden={!visible}>
      <NotesTab
        project={project}
        allProjects={props.notesAllProjects}
        onAllProjects={props.onNotesAllProjects}
        onOpenNote={props.onOpenNote}
        onOpenScratchpad={props.onOpenScratchpad}
        onDone={onDone}
        selectedResourceId={props.drawerNoteId}
        onPopSelected={() => {
          if (!drawerNote) return
          props.onPopDrawerNoteToTab(drawerNote.resourceId)
          onClose()
        }}
        editor={drawerNote && project
          ? <ProjectResource
            key={`drawer-note:${project.id}:${drawerNote.resourceId}`}
            project={project}
            resource={drawerNote.identity}
            onOpenFile={path => { props.onOpenFile(path); onDone() }}
            onSendToAgent={props.onSendToAgent}
            claimInsertTargetToken={props.noteTargetClaimToken}
            onInsertTargetClaimed={props.onNoteTargetClaimed}
          />
          : null}
      />
    </div>

  // One body per tab. A flat dispatch rather than the nested ternary this grew out of:
  // several branches deep, every added surface used to reindent the ones below it.
  const renderBody = (tab: DrawerTabId) => {
    switch (tab) {
      case 'clipboard':
        return <ClipboardTab onInsert={insertText} onDone={onInsertDone} onOpenSettings={() => props.onOpenSettings('Input')} />
      case 'actions':
        return <ActionsTab session={session} project={project} backend={props.backend} onDone={onDone} onConfigureActions={props.onConfigureActions} onInsert={props.onInsertPrompt} onManage={props.onManagePrompts} preselect={props.promptPreselect} sessions={props.sessions} onSend={props.onSendPrompt} />
      case 'queue':
        // Follows the focused session, like every other session-scoped tab.
        return <QueuePane
          sessionId={session?.id || ''}
          sessions={props.sessions}
          onSelectSession={sessionId => { props.onOpenSession(sessionId); onDone() }}
          onOpenAsTab={sessionId => { props.onQueueOpenAsTab(sessionId); onDone() }}
          onOpenFleetQueue={() => { props.onOpenFleetQueue(); onDone() }}
          fleetPending={props.queuePending}
          openRequestToken={props.queueOpenToken}
        />
      case 'transcript':
        // No `onDone`. Every other session-scoped tab closes the mobile drawer once
        // it has acted, because it acted on the terminal underneath; this one is
        // read there, and closing it after each copy would end the reading.
        return <TranscriptTab session={session} />
      case 'insight':
        return <InsightTab session={session} project={project} onOpenProjectSettings={props.onOpenProjectSettings} onOpenAutomationDashboard={props.onOpenAutomationDashboard} />
      case 'changemap':
        // No `onDone`. Like Transcript and Insight, this is read beside the terminal
        // rather than acted on, so popping the drawer shut after a node click would end
        // the reading it exists for.
        return <ChangeMapPane
          session={session}
          project={project}
          onPopOut={session ? () => props.onChangeMapOpenAsTab(session.id) : undefined}
          // `onDone` on purpose: opening a file *is* acting on something other than
          // the terminal underneath, so on mobile the drawer should get out of the
          // way and show the pane it just opened.
          onOpenFile={(path, worktree) => {
            if (worktree) props.onOpenWorktreeFile(worktree, path)
            else props.onOpenFile(path)
            onDone()
          }}
        />
      case 'agent':
        return <AgentEnvironmentTab session={session} />
      case 'files':
        return project
          ? <ProjectResource
            key={`drawer-files:${project.id}`}
            project={project}
            resource={{ kind: 'files', id: project.id }}
            onOpenFile={path => { props.onOpenFile(path); onDone() }}
            onFileDragStart={props.onFileDragStart}
            onSendToAgent={props.onSendToAgent}
          />
          : <p class="drawer-empty">Select a Project to browse its files.</p>
      case 'notes':
        // The persistent Notes workspace is rendered outside this switch so its editor,
        // cursor, and undo history survive visits to other utility tabs.
        return null
      case 'context':
        return <AgentContextTab project={project} session={session} />
      case 'git':
        return <GitTab project={project} sessions={props.sessions} onOpenFile={props.onOpenFile} onOpenWorktreeFile={props.onOpenWorktreeFile} onSendToAgent={props.onSendToAgent} onProjectUpdated={props.onProjectUpdated} onOpenSession={sessionId=>{props.onOpenSession(sessionId);onDone()}} onOpenHistory={props.onOpenHistoryEntry} />
      case 'processes':
        return <ProcessesTab
          snapshot={props.processSnapshot}
          sessions={props.sessions}
          projects={props.projects}
          projectId={project?.id || ''}
          focusedSessionId={session?.id || null}
          scope={props.processScope}
          onScope={props.onProcessScope}
          onSelectSession={props.onOpenSession}
          onOpenPreview={props.onOpenPreview}
          onOpenInspector={props.onOpenInspector}
          onRefresh={props.onRefreshProcesses}
          onDone={onDone}
        />
      case 'schedule':
        return <ScheduleTab
          project={project}
          projects={props.projects}
          profiles={props.profiles}
          scope={props.scheduleScope}
          onScope={props.onScheduleScope}
          onOpenSession={props.onOpenSession}
          onOpenProjectSettings={props.onOpenProjectSettings}
          // `onDone` on purpose: revealing the session a schedule started, or
          // opening the Project's opt-in, is acting on something other than the
          // terminal underneath, so on mobile the drawer gets out of the way.
          onDone={onDone}
        />
      case 'notifications':
        return <NotificationsTab data={props.notifications} onOpenSession={props.onOpenSession} onChanged={props.onNotificationsChanged} />
    }
  }
  const scopeContext = (tab: DrawerTabId) => {
    const scope = drawerTab(tab).scope
    // The display name, not the raw one: this heading names the same session the sidebar
    // and the tab strip name, and a drawer that still said `claude-0e7d93` after a title
    // arrived read as a different session than the pane it describes.
    if (scope === 'session') return session ? `Session: ${sessionDisplayName(session)}` : 'No focused session'
    if (scope === 'project') return project ? `Project: ${project.name}` : 'No active Project'
    return 'Application-wide'
  }

  const tabDomId = (stackId: string, tab: DrawerTabId) => `drawer-tab-${stackId}-${tab}`
  const panelDomId = (stackId: string) => `drawer-panel-${stackId}`

  const renderTabMark = (id: DrawerTabId) => {
    const item = drawerTab(id)
    if (props.tabDisplay === 'title') return <span class="drawer-tab-title">{item.label}</span>
    const Icon = DRAWER_TAB_ICONS[id]
    return <Icon />
  }

  const focusTabButton = (stackId: string, tab: DrawerTabId) => {
    queueMicrotask(() => document.getElementById(tabDomId(stackId, tab))?.focus())
  }

  const renderRail = (stack: DrawerStack, selected: DrawerTabId, projection = false) => <OverflowRail
    className="drawer-tabs"
    itemLabel="panel tabs"
    wrapperClassName={`drawer-tabs-rail drawer-pane-rail ${props.tabDisplay === 'title' ? 'title-mode' : 'icon-mode'}`}
    activeKey={selected}
    focusKey={focusedTab === selected ? selected : undefined}
    touchDrag={projection}
    touchDragGain={2.5}
    stripProps={{
      'data-drawer-stack-id':stack.id,
      role:'tablist',
      'aria-label':projection ? 'Panel sections' : `Panel section ${stack.id}`,
    }}
  >
    {visibleDrawerTabs(stack.tabs, visibility).map((id, index, visibleTabs) => {
      const item = drawerTab(id)
      return <button
        id={tabDomId(stack.id, id)}
        key={id}
        role="tab"
        // The guided tour anchors its Notes step on whichever Notes control is currently
        // rendered. The launcher rail owns that anchor while the drawer is closed and this
        // strip owns it while the drawer is open, so exactly one element ever carries it —
        // a click-gated step has no Next button and an absent anchor strands the tour.
        data-tutorial={id === 'notes' ? 'project-notes' : undefined}
        data-reorder-id={id}
        data-drawer-tab-id={id}
        data-scope={item.scope}
        aria-controls={panelDomId(stack.id)}
        aria-selected={id === selected}
        aria-label={`${item.label}${item.scope === 'session' ? ', session scoped' : ''}`}
        tabIndex={id === selected ? 0 : -1}
        class={`${id === selected ? 'active' : ''} ${props.draggingTab === id ? 'dragging' : ''}`}
        title={`${item.title}${item.scope === 'session' ? ' - session scoped' : ''}${projection ? '' : ' - drag to rearrange or split'}`}
        onPointerDown={projection
          ? event => { beginTabLongPress(event, id); props.onProjectionTabReorder?.(event, id) }
          : event => props.onTabDragStart(event, id)}
        onContextMenu={event => {
          event.preventDefault()
          event.stopPropagation()
          props.onTabDisplayMenu(event.clientX, event.clientY, id)
        }}
        onClick={() => {
          if (performance.now() < suppressTabClickUntilRef.current) return
          onTab(id, id === selected)
        }}
        onKeyDown={event => {
          if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
          event.preventDefault()
          const offset = event.key === 'ArrowLeft' ? -1 : 1
          const next = visibleTabs[(index + offset + visibleTabs.length) % visibleTabs.length]
          onTab(next)
          focusTabButton(stack.id, next)
        }}
      >
        {renderTabMark(id)}
        {id === 'notifications' && props.unread > 0 && <i class="drawer-badge">{props.unread > 99 ? '99+' : props.unread}</i>}
      </button>
    })}
  </OverflowRail>

  const renderStack = (stack: DrawerStack, selectedOverride?: DrawerTabId) => {
    const visibleTabs=visibleDrawerTabs(stack.tabs, visibility)
    const requested = selectedOverride || presentation.selected_tabs[stack.id] || visibleTabs[0]
    const selected = visibleTabs.includes(requested) ? requested : visibleTabs[0]
    const active = drawerTab(selected)
    const notesHere = stack.tabs.includes('notes')
    return <section
      key={stack.id}
      class={`drawer-pane ${focusedTab === selected ? 'focused' : ''}`}
      data-drawer-stack-id={stack.id}
      onFocusCapture={() => { if (focusedTab !== selected) onTab(selected) }}
    >
      {renderRail(stack, selected, selectedOverride !== undefined)}
      <div
        id={panelDomId(stack.id)}
        role="tabpanel"
        aria-labelledby={tabDomId(stack.id, selected)}
        class={`drawer-body drawer-body-${selected}`}
        style={{ '--drawer-panel-title-width': `${Math.min(22, active.heading.length + 2.5)}ch` } as JSX.CSSProperties}
      >
        <div class="drawer-pane-heading">
          <h2 class="drawer-panel-title" title={active.title}>{active.heading}</h2>
          <span class="drawer-scope-context">{scopeContext(selected)}</span>
          {stack.id === collapseHostId && <button
            class="drawer-collapse"
            aria-label="Collapse side panel"
            title="Collapse side panel"
            onClick={onClose}
          >×</button>}
        </div>
        {notesHere && renderNoteHost(selected === 'notes')}
        {selected !== 'notes' && renderBody(selected)}
      </div>
    </section>
  }

  const resizeSplit = (event: JSX.TargetedPointerEvent<HTMLDivElement>, node: Extract<DrawerNode, { type: 'split' }>) => {
    if (event.button !== 0) return
    event.preventDefault()
    const separator = event.currentTarget
    const splitElement = separator.parentElement
    if (!splitElement) return
    separator.setPointerCapture(event.pointerId)
    let ratio = node.ratio
    const apply = (next: number) => {
      ratio = Math.max(0.1, Math.min(0.9, next))
      const first = splitElement.children[0] as HTMLElement
      const second = splitElement.children[2] as HTMLElement
      first.style.flex = `${ratio} 1 0`
      second.style.flex = `${1 - ratio} 1 0`
      separator.setAttribute('aria-valuenow', String(Math.round(ratio * 100)))
    }
    const move = (moveEvent: PointerEvent) => {
      const rect = splitElement.getBoundingClientRect()
      apply(node.direction === 'horizontal'
        ? (moveEvent.clientX - rect.left) / Math.max(1, rect.width)
        : (moveEvent.clientY - rect.top) / Math.max(1, rect.height))
    }
    const cleanup = () => {
      separator.removeEventListener('pointermove', move)
      separator.removeEventListener('pointerup', finish)
      separator.removeEventListener('pointercancel', cancel)
      separator.removeEventListener('lostpointercapture', cancel)
      window.removeEventListener('blur', cancel)
      window.removeEventListener('keydown', key, true)
    }
    const finish = () => {
      cleanup()
      props.onLayout(setDrawerSplitRatio(layout, node.id, ratio))
    }
    const cancel = () => {
      cleanup()
      apply(node.ratio)
    }
    const key = (keyboard: KeyboardEvent) => {
      if (keyboard.key !== 'Escape') return
      keyboard.preventDefault()
      cancel()
    }
    separator.addEventListener('pointermove', move)
    separator.addEventListener('pointerup', finish)
    separator.addEventListener('pointercancel', cancel)
    separator.addEventListener('lostpointercapture', cancel)
    window.addEventListener('blur', cancel)
    window.addEventListener('keydown', key, true)
  }

  /**
   * The drawer with nothing left to draw.
   *
   * `canHideDrawerTab` stops the user hiding the last tab, so reaching this needs the
   * *structural* filter to take the remainder — hide everything but Transcript, then
   * focus a shell session. Rare, but a drawer with no tab strip has no context menu to
   * right-click, so it carries its own way back rather than sending the reader to
   * Settings to guess what happened.
   */
  const renderEmptyDrawer = () => <section class="drawer-pane">
    <div class="drawer-body">
      <div class="drawer-pane-heading">
        <h2 class="drawer-panel-title">Side panel</h2>
        <button class="drawer-collapse" aria-label="Collapse side panel" title="Collapse side panel" onClick={onClose}>×</button>
      </div>
      <p class="drawer-empty">Nothing to show here. Every panel is either hidden or unavailable for the focused session.</p>
      <p class="drawer-empty">
        <button onClick={event => {
          const rect = event.currentTarget.getBoundingClientRect()
          props.onTabDisplayMenu(rect.left, rect.bottom)
        }}>Choose panels…</button>
      </p>
    </div>
  </section>

  // A pane whose every tab is hidden (or structurally absent) is dropped and its space
  // handed back to its sibling, rather than drawn as an empty box under whichever
  // heading the selection happened to fall back to. The layout keeps the split either
  // way, so showing the tab again restores the arrangement exactly.
  const renderNode = (node: DrawerNode): JSX.Element | null => {
    if (node.type === 'stack') return node.tabs.some(tabAvailable) ? renderStack(node) : null
    const first = renderNode(node.first)
    const second = renderNode(node.second)
    if (!first) return second
    if (!second) return first
    const updateRatio = (ratio: number) => props.onLayout(setDrawerSplitRatio(layout, node.id, ratio))
    return <div key={node.id} class={`drawer-split ${node.direction}`} data-drawer-split-id={node.id}>
      <div class="drawer-split-branch drawer-split-first" style={{ flex: `${node.ratio} 1 0` }}>{first}</div>
      <div
        class="drawer-splitter"
        role="separator"
        aria-label="Resize drawer panes"
        aria-orientation={node.direction === 'horizontal' ? 'vertical' : 'horizontal'}
        aria-valuemin={10}
        aria-valuemax={90}
        aria-valuenow={Math.round(node.ratio * 100)}
        tabIndex={0}
        onPointerDown={event => resizeSplit(event, node)}
        onDblClick={() => updateRatio(0.5)}
        onKeyDown={event => {
          const decrease = node.direction === 'horizontal' ? 'ArrowLeft' : 'ArrowUp'
          const increase = node.direction === 'horizontal' ? 'ArrowRight' : 'ArrowDown'
          let ratio: number | null = null
          if (event.key === decrease) ratio = node.ratio - 0.05
          if (event.key === increase) ratio = node.ratio + 0.05
          if (event.key === 'Home') ratio = 0.1
          if (event.key === 'End') ratio = 0.9
          if (ratio === null) return
          event.preventDefault()
          updateRatio(ratio)
        }}
      />
      <div class="drawer-split-branch drawer-split-second" style={{ flex: `${1 - node.ratio} 1 0` }}>{second}</div>
    </div>
  }

  return <>
    {mobile && <button class="utility-drawer-scrim" aria-label="Close panel" onClick={onClose} />}
    {!mobile && <div
      class="drawer-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize panel"
      aria-valuemin={props.minimumWidth}
      aria-valuemax={props.maximumWidth}
      aria-valuenow={Math.round(props.width)}
      tabIndex={0}
      title="Drag to resize, or double click to restore the default width"
      onPointerDown={props.onResize}
      onDblClick={() => props.onWidth(props.defaultWidth)}
      onKeyDown={event => {
        let width: number | null = null
        if (event.key === 'ArrowLeft') width = props.width + 10
        if (event.key === 'ArrowRight') width = props.width - 10
        if (event.key === 'Home') width = props.minimumWidth
        if (event.key === 'End') width = props.maximumWidth
        if (width === null) return
        event.preventDefault()
        props.onWidth(width)
      }}
    />}
    <aside
      class={`utility-drawer ${mobile ? 'overlay' : 'docked'}`}
      role={mobile ? 'dialog' : 'complementary'}
      aria-modal={mobile || undefined}
      aria-label="Utility drawer"
      onKeyDown={event => {
        if (event.key === 'Escape' && !event.defaultPrevented) { event.stopPropagation(); onClose() }
      }}
    >
      <div class="drawer-tree">
        {(mobile ? mobileStack.tabs : drawerTabs(layout)).some(tabAvailable)
          ? (mobile ? renderStack(mobileStack, focusedTab) : renderNode(layout.root))
          : renderEmptyDrawer()}
      </div>
      <span class="sr-only" aria-live="polite">{props.announcement}</span>
    </aside>
  </>
}
