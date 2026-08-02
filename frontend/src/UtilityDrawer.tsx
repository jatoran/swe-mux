import type { JSX } from 'preact'
import { useEffect, useRef } from 'preact/hooks'
import { AgentContextTab } from './AgentContextTab'
import { ClipboardTab } from './ClipboardPanel'
import { CommandsTab } from './CommandsTab'
import { PromptsTab } from './PromptsTab'
import { NotesTab } from './NotesTab'
import { QueuePane, type QueueScope } from './QueuePane'
import { GitTab } from './GitTab'
import { ProjectResource } from './ProjectResource'
import { NotificationsTab, type NotificationData } from './Notifications'
import { drawerTab, nextDrawerTab, type DrawerTab, type DrawerTabId } from './drawerTabs'
import { DRAWER_TAB_ICONS } from './railIcons'
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
  onOpenProjectNote: (projectId: string) => void
  onOpenSessionNote: (projectId: string, noteId: string) => void
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

  // One body per tab. A flat dispatch rather than the nested ternary this grew out of:
  // several branches deep, every added surface used to reindent the ones below it.
  const renderBody = () => {
    switch (tab) {
      case 'clipboard':
        return <ClipboardTab onInsert={props.onInsert} onDone={onDone} onOpenSettings={() => props.onOpenSettings('Input')} />
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
        return <NotesTab
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
      title="Drag to resize"
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
        if (event.key !== 'Tab' || !(event.target as Element | null)?.closest?.('.drawer-tabs')) return
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
              aria-selected={item.id === tab}
              aria-label={item.label}
              class={`${item.id === tab ? 'active' : ''} ${props.draggingTab === item.id ? 'dragging' : ''}`}
              title={`${item.title} · drag to rearrange`}
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
      <div class="drawer-body">{body}</div>
    </aside>
  </>
}
