import type { JSX } from 'preact'
import { useEffect, useRef } from 'preact/hooks'
import { AgentContextTab } from './AgentContextTab'
import { ClipboardTab } from './ClipboardPanel'
import { CommandsTab } from './CommandsTab'
import { PromptsTab } from './PromptsTab'
import { NotesTab } from './NotesTab'
import { QueuePane, type QueueScope } from './QueuePane'
import { TranscriptTab } from './TranscriptTab'
import { GitTab } from './GitTab'
import { ProjectResource } from './ProjectResource'
import { NotificationsTab, type NotificationData } from './Notifications'
import { drawerTab, nextDrawerTab, type DrawerTab, type DrawerTabId } from './drawerTabs'
import { ProcessesTab } from './ProcessesTab'
import type { WatchScope, WatchSnapshot } from './processWatch'
import { parseNoteResourceId } from './layout'
import type { NotePlacement } from './NotesTab'
import { DRAWER_TAB_ICONS } from './railIcons'
import { isFocusTraversalKey } from './keys'
import type { SendToAgentRequest, SendToAgentResult, SendToAgentTarget } from './SendToAgentPicker'
import type { Project, ProjectBackend, Session } from './types'

// Host for the right-edge utility drawer. Two renderings, one component:
//
//  * mobile  — an overlay drawer with a scrim, mirroring the navigation sidebar it
//              is mutually exclusive with.
//  * desktop — an in-flow column of the workspace grid (the caller places it), so
//              the pane tree shrinks instead of being covered. Covering a terminal
//              you opened the drawer to work with is exactly backwards.
//
// The tab strip is inside the drawer on both; desktop additionally has an
// always-visible icon rail outside it (rendered by App), which is what makes these
// surfaces discoverable without a menu or a chord.

type Props = {
  tab: DrawerTabId
  onTab: (tab: DrawerTabId) => void
  onClose: () => void
  mobile: boolean
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
  onOpenSettings: (section: string) => void
  onManagePrompts: () => void
  /** Files: open a Project-relative path as a pane tab. */
  onOpenFile: (path: string) => void
  /** Files: open the send-to-agent dialog with a tree file's contents. */
  onSendToAgent?: (request: SendToAgentRequest) => void
  /** Files: desktop-only drag of a file row onto a pane. Omitted on mobile, where
   *  the drawer is an overlay and there is no visible pane to drop onto. */
  onFileDragStart?: (path: string, event: JSX.TargetedPointerEvent<HTMLElement>) => void
  /** Notes: the listing's Project/all-Projects scope, owned by the caller so the
   *  `notes.browse` and `notes.browseProject` commands can each pick one. */
  notesAllProjects: boolean
  onNotesAllProjects: (value: boolean) => void
  focusedNote: { projectId: string; noteId: string; label: string } | null
  onOpenProjectNote: (projectId: string, place: NotePlacement) => void
  onOpenSessionNote: (projectId: string, noteId: string, place: NotePlacement) => void
  /** Processes: the fleet sample `App` already polls. Passed in rather than fetched here so an
   *  open panel adds no process enumeration to the daemon's loop — see `ProcessesTab`. */
  processSnapshot: WatchSnapshot
  projects: Project[]
  processScope: WatchScope
  onProcessScope: (scope: WatchScope) => void
  onRefreshProcesses: () => void
  /** Register a detected loopback server as a preview tab beside its session. */
  onOpenPreview: (sessionId: string, url: string) => void
  /** Escape hatch to the modal inspector, prefiltered to the tab's current scope. */
  onOpenInspector: (projectId: string | null) => void
  /** Notes: the note this Project's drawer is editing, or null to show the index. A note has
   *  one live editor per browser (see `drawerNotes.ts`), so this is also what tells the
   *  matching pane leaf to stand down. */
  drawerNoteId: string | null
  /** Give the note back to the workspace and return to the index. */
  onCloseDrawerNote: () => void
  /** Give it back and put it in a pane, focused. */
  onPopDrawerNoteToTab: (resourceId: string) => void
  /** Tabs in the user's arranged order, and the pointer-drag that rearranges them. Both come
   *  from the caller because the desktop icon rail renders the same order and the same drag,
   *  and the two must never disagree about it. */
  tabs: DrawerTab[]
  onTabDragStart: (event: JSX.TargetedPointerEvent<HTMLElement>, id: DrawerTabId) => void
  /** True while a tab is being dragged, so the strip can suppress its own click. */
  draggingTab: DrawerTabId | null
  /** Template handed off by a command-rail prompt button that needs its fields filled. */
  promptPreselect?: { key: string }
  /** Queue: set when the tab was opened by a deliberate act (the pane chip, a command)
   *  rather than by tab-switching, which is what earns a scope and the composer's caret. */
  queueOpenRequest?: { token: number; scope: QueueScope }
  /** Queue: pop the focused target's queue out into a workspace tab. */
  onQueueOpenAsTab: (sessionId: string) => void
  /** Queue: pending items across every target, badged like the alerts count. Fleet-wide
   *  rather than per-session on purpose — the badge answers "is anything waiting anywhere",
   *  which is the question you have while looking at some other session. */
  queuePending: number
  /** Desktop only: pointer-drag handle for the column width. Typed as the plain
   *  DOM event so this module needs no `JSX` import for it (which would shadow the
   *  global namespace the intrinsic elements below resolve through). */
  onResize?: (event: PointerEvent) => void
}

export function UtilityDrawer(props: Props) {
  const { tab, onTab, onClose, mobile, session, project } = props
  const active = drawerTab(tab)
  const tabStrip = useRef<HTMLDivElement>(null)
  useEffect(() => {
    tabStrip.current?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')
      ?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [tab])
  // Acting closes the drawer on mobile (it covers the surface just acted on) and
  // leaves it open on desktop, where the column sits beside that surface and a
  // second insert (or a second file) is the common next action.
  const onDone = () => { if (mobile) onClose() }

  const noteIdentity = props.drawerNoteId ? parseNoteResourceId(props.drawerNoteId) : null
  // `file` is a legal parse but never a drawer note: files are the Files tab's business, and
  // the claim is only ever written from the Notes index.
  const drawerNote = props.drawerNoteId && project && noteIdentity && noteIdentity.kind !== 'file'
    ? { resourceId: props.drawerNoteId, identity: noteIdentity }
    : null
  const drawerNoteLabel = drawerNote?.identity.kind === 'session-note' ? 'Session note' : 'Project note'

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
  const noteHost = drawerNote && project
    ? <div class="drawer-note-host" hidden={tab !== 'notes'}>
      <div class="drawer-note-bar">
        <button class="drawer-note-back" title="Back to the note index" onClick={props.onCloseDrawerNote}>‹ Notes</button>
        <span class="drawer-note-kind">{drawerNoteLabel}</span>
        <button
          class="drawer-note-pop"
          title="Move this note into a workspace tab. A note is only ever open in one place, so it leaves the panel."
          onClick={() => { props.onPopDrawerNoteToTab(drawerNote.resourceId); onDone() }}
        >⇥ tab</button>
      </div>
      <ProjectResource
        key={`drawer-note:${project.id}:${drawerNote.resourceId}`}
        project={project}
        resource={drawerNote.identity}
        onOpenFile={path => { props.onOpenFile(path); onDone() }}
        onSendToAgent={props.onSendToAgent}
      />
    </div>
    : null

  // One body per tab. A flat dispatch rather than the nested ternary this grew out of:
  // several branches deep, every added surface used to reindent the ones below it.
  const renderBody = () => {
    switch (tab) {
      case 'clipboard':
        return <ClipboardTab onInsert={insertText} onDone={onInsertDone} onOpenSettings={() => props.onOpenSettings('Input')} />
      case 'commands':
        return <CommandsTab session={session} onDone={onDone} onOpenSettings={() => props.onOpenSettings('Command rail')} />
      case 'prompts':
        return <PromptsTab project={project} backend={props.backend} onInsert={props.onInsertPrompt} onDone={onDone} onManage={props.onManagePrompts} preselect={props.promptPreselect} sessions={props.sessions} onSend={props.onSendPrompt} />
      case 'queue':
        // Follows the focused session, like every other session-scoped tab. A delivery is
        // the one act here that wants the terminal back, so it goes through `onDone`;
        // opening another target's queue from a mailbox row deliberately does not.
        return <QueuePane
          sessionId={session?.id || ''}
          sessions={props.sessions}
          onSelectSession={sessionId => { props.onOpenSession(sessionId); onDone() }}
          onFocusTarget={props.onOpenSession}
          onOpenAsTab={sessionId => { props.onQueueOpenAsTab(sessionId); onDone() }}
          openRequest={props.queueOpenRequest}
        />
      case 'transcript':
        // No `onDone`. Every other session-scoped tab closes the mobile drawer once
        // it has acted, because it acted on the terminal underneath; this one is
        // read there, and closing it after each copy would end the reading.
        return <TranscriptTab session={session} />
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
        // `noteHost` is rendered outside this switch and covers the tab when a note is
        // claimed, so the index only draws when there is none.
        return drawerNote
          ? null
          : <NotesTab
            project={project}
            allProjects={props.notesAllProjects}
            onAllProjects={props.onNotesAllProjects}
            focusedNote={props.focusedNote}
            onOpenProjectNote={props.onOpenProjectNote}
            onOpenSessionNote={props.onOpenSessionNote}
            onDone={onDone}
          />
      case 'context':
        return <AgentContextTab project={project} session={session} />
      case 'git':
        return <GitTab project={project} sessions={props.sessions} />
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
      case 'notifications':
        return <NotificationsTab data={props.notifications} onOpenSession={props.onOpenSession} onChanged={props.onNotificationsChanged} />
    }
  }
  const body = renderBody()

  return <>
    {mobile && <button class="utility-drawer-scrim" aria-label="Close panel" onClick={onClose} />}
    {!mobile && <div
      class="drawer-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize panel"
      title="Drag to resize or collapse"
      onPointerDown={props.onResize}
    />}
    <aside
      class={`utility-drawer ${mobile ? 'overlay' : 'docked'}`}
      role="dialog"
      aria-label={`${active.label} panel`}
      onKeyDown={event => {
        if (event.key === 'Escape') { event.stopPropagation(); onClose(); return }
        // Tab cycling stays on the strip's own buttons so it cannot steal Tab from
        // a filter field or a template's placeholder inputs. It walks the user's order,
        // not the default one, or the keys would jump around a rearranged strip.
        if (!isFocusTraversalKey(event) || !(event.target as Element | null)?.closest?.('.drawer-tabs')) return
        event.preventDefault()
        onTab(nextDrawerTab(tab, event.shiftKey ? -1 : 1, props.tabs.map(item => item.id)))
      }}
    >
      {/* Icon-only, like the desktop rail and from the same icon map. Labelled tabs
          overflowed a phone drawer and silently parked later tabs off-screen; compact icons
          fit many more, with the one-row scroller handling the remainder. The label
          survives as the accessible name and the title as the hover explanation. */}
      <div class="drawer-tabs-shell">
        <div ref={tabStrip} class="drawer-tabs" role="tablist" aria-label="Panel sections">
          {props.tabs.map(item => {
            const Icon = DRAWER_TAB_ICONS[item.id]
            return <button
              key={item.id}
              role="tab"
              data-reorder-id={item.id}
              data-scope={item.scope}
              aria-selected={item.id === tab}
              aria-label={`${item.label}${item.scope === 'session' ? ', session scoped' : ''}`}
              class={`${item.id === tab ? 'active' : ''} ${props.draggingTab === item.id ? 'dragging' : ''}`}
              title={`${item.title}${item.scope === 'session' ? ' · session-scoped' : ''} · drag to rearrange`}
              onPointerDown={event => props.onTabDragStart(event, item.id)}
              onClick={() => onTab(item.id)}
            >
              <Icon />
              {item.id === 'notifications' && props.unread > 0 && <i class="drawer-badge">{props.unread > 99 ? '99+' : props.unread}</i>}
              {item.id === 'queue' && props.queuePending > 0 && <i class="drawer-badge queue-badge">{props.queuePending > 99 ? '99+' : props.queuePending}</i>}
            </button>
          })}
        </div>
        <button class="drawer-close" aria-label="Close panel" title="Close panel" onClick={onClose}>×</button>
      </div>
      <div
        class={`drawer-body drawer-body-${tab}`}
        style={{ '--drawer-panel-title-width': `${Math.min(22, active.heading.length + 2.5)}ch` } as JSX.CSSProperties}
      >
        <h2 class="drawer-panel-title" title={active.title}>{active.heading}</h2>
        {noteHost}{body}
      </div>
    </aside>
  </>
}
