import { ClipboardTab } from './ClipboardPanel'
import { CommandsTab } from './CommandsTab'
import { PromptsTab } from './PromptsTab'
import { NotificationsTab, type NotificationData } from './Notifications'
import { DRAWER_TABS, drawerTab, nextDrawerTab, type DrawerTabId } from './drawerTabs'
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
  unread: number
  onInsert: (text: string) => 'terminal' | 'editor' | 'none'
  onOpenSession: (sessionId: string) => void
  onOpenSettings: (section: string) => void
  onManagePrompts: () => void
  /** Template handed off by a command-rail prompt button that needs its fields filled. */
  promptPreselect?: { key: string }
  /** Desktop only: pointer-drag handle for the column width. Typed as the plain
   *  DOM event so this module needs no `JSX` import (which would shadow the
   *  global namespace the intrinsic elements below resolve through). */
  onResize?: (event: PointerEvent) => void
}

export function UtilityDrawer(props: Props) {
  const { tab, onTab, onClose, mobile, session } = props
  const active = drawerTab(tab)
  // Inserting closes the drawer on mobile (it covers the terminal you just typed
  // into) and leaves it open on desktop, where the column is beside that terminal
  // and a second insert is the common next action.
  const onDone = () => { if (mobile) onClose() }

  const body = tab === 'clipboard'
    ? <ClipboardTab onInsert={props.onInsert} onDone={onDone} onOpenSettings={() => props.onOpenSettings('Input')} />
    : tab === 'commands'
      ? <CommandsTab session={session} onDone={onDone} onOpenSettings={() => props.onOpenSettings('Command rail')} />
      : tab === 'prompts'
        ? <PromptsTab project={props.project} backend={props.backend} onInsert={props.onInsert} onDone={onDone} onManage={props.onManagePrompts} preselect={props.promptPreselect} />
        : <NotificationsTab data={props.notifications} onOpenSession={props.onOpenSession} />

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
        // a filter field or a template's placeholder inputs.
        if (event.key !== 'Tab' || !(event.target as Element | null)?.closest?.('.drawer-tabs')) return
        event.preventDefault()
        onTab(nextDrawerTab(tab, event.shiftKey ? -1 : 1))
      }}
    >
      <div class="drawer-tabs" role="tablist" aria-label="Panel sections">
        {DRAWER_TABS.map(item => <button
          key={item.id}
          role="tab"
          aria-selected={item.id === tab}
          class={item.id === tab ? 'active' : ''}
          title={item.title}
          onClick={() => onTab(item.id)}
        >
          <span aria-hidden="true">{item.glyph}</span>{item.label}
          {item.id === 'notifications' && props.unread > 0 && <i class="drawer-badge">{props.unread > 99 ? '99+' : props.unread}</i>}
        </button>)}
        <button class="drawer-close" aria-label="Close panel" title="Close panel" onClick={onClose}>×</button>
      </div>
      <div class="drawer-body">{body}</div>
    </aside>
  </>
}
