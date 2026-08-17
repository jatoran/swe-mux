import { useEffect, useRef } from 'preact/hooks'
import { clampContextMenuLeft, fitScrollingMenuInViewport } from './menuPosition'
import { useDismissLevel } from './modalFocus'
import { StateIndicator } from './StateIndicator'
import { useSessionRowConfig } from './sessionRowPrefs'
import type { Session } from './types'

// The Git tab names sessions in three places — a worktree's live occupants, a commit's
// session links, and the provenance ledger — and in all three the name was previously a
// dead end: the reader could see which session did it and had no way to get to it.
//
// One popover answers all three. It opens at the pointer rather than as a centred modal
// because it is a drill-down from a specific badge, and a centred sheet loses which badge
// was pressed the moment it covers the row.
//
// Where a click goes is decided by the session's own liveness, not by where the list came
// from: a live session is focused in its pane (an already-open tab is activated rather than
// duplicated), and anything ended goes to its History conversation. An entry with neither is
// inert and says why, which is the honest rendering of a session mux no longer has.

export type SessionLinkItem = {
  /** Stable per-row key; provenance rows and worktree occupants can name one session twice. */
  key: string
  label: string
  /** One line of why this session is in the list: its role, or its branch. */
  detail?: string
  /** The live session, when the fleet still has one under this id. */
  session: Session | null
  /** History row id of the conversation, when one exists to open. */
  historyId: string | null
}

export type SessionLinkMenu = {
  title: string
  x: number
  y: number
  items: SessionLinkItem[]
}

/** Whether an entry leads to a pane, to History, or nowhere. */
export function sessionLinkDestination(item: SessionLinkItem): 'session' | 'history' | 'none' {
  const live = item.session && item.session.state !== 'exited' && item.session.state !== 'crashed'
  if (live) return 'session'
  return item.historyId ? 'history' : 'none'
}

const DESTINATION_LABEL: Record<ReturnType<typeof sessionLinkDestination>, string> = {
  session: 'Open its pane',
  history: 'Read it in History',
  none: 'No conversation recorded',
}

type Props = {
  menu: SessionLinkMenu
  onClose: () => void
  /** Navigation belongs to the Git tab, so one rule serves the popover and the ledger rows. */
  onFollow: (item: SessionLinkItem) => void
}

export function GitSessionLinks({ menu, onClose, onFollow }: Props) {
  const rowConfig = useSessionRowConfig()
  const panel = useRef<HTMLDivElement>(null)
  // Back and Escape close the popover, not the drawer underneath it. Registered as a
  // level rather than its own modal: this is a drill-down inside the drawer, and trapping
  // focus for a three-line list would take Tab away from the surface that opened it.
  useDismissLevel(onClose, true, 'git-session-links')
  useEffect(() => {
    // The opening pointerdown has already been dispatched by the time this effect runs,
    // so this only ever sees the *next* one.
    const dismiss = (event: PointerEvent) => {
      const target = event.target
      if (target instanceof Element && panel.current?.contains(target)) return
      onClose()
    }
    document.addEventListener('pointerdown', dismiss)
    return () => document.removeEventListener('pointerdown', dismiss)
  }, [onClose])

  return <div
    ref={element => { panel.current = element; fitScrollingMenuInViewport(element) }}
    class="context-menu git-session-links"
    role="menu"
    aria-label={menu.title}
    style={{ left: clampContextMenuLeft(menu.x, window.innerWidth), top: menu.y }}
  >
    <div class="context-title"><strong>{menu.title}</strong></div>
    {menu.items.map(item => {
      const destination = sessionLinkDestination(item)
      const action = DESTINATION_LABEL[destination]
      return <button
        key={item.key}
        role="menuitem"
        disabled={destination === 'none'}
        title={`${item.label} · ${action}`}
        onClick={() => { onClose(); onFollow(item) }}
      >
        <StateIndicator session={item.session || undefined} shape={rowConfig.dotShape} />
        <span class="git-session-link-label">
          <strong>{item.label}</strong>
          <small>{item.detail ? `${item.detail} · ${action}` : action}</small>
        </span>
      </button>
    })}
    {!menu.items.length && <div class="context-note">No sessions are recorded here.</div>}
  </div>
}
