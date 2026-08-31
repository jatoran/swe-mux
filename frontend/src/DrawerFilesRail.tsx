import { useEffect, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { OverflowRail } from './RailScroller'
import { useRailLongPress } from './railLongPress'
import { useDismissLevel } from './modalFocus'
import { dismissStack } from './dismissStack.ts'
import { clampContextMenuLeft, fitMenuInViewport } from './menuPosition'
import { absoluteProjectPath } from './fileClipboard'
import { copyPreparedText } from './terminalClipboard'
import { fileTabLabels, type DrawerFileTab } from './drawerFiles'

// The Files tab's second rail: the files this device has open in the drawer.
//
// It sits *below* the `File Explorer | Recent` segment control rather than merging into it,
// and the two rails answer different questions. The segment rail names the index you would
// return to and is registered, addressable, and persisted per Project (`drawerSegments.ts`);
// this one holds documents, is device-local, and its entries come and go. Folding open files
// into the registry would have made a filename look like a palette command and would have
// pushed "File Explorer" - which the docs are explicit about not abbreviating - off the row
// the moment two files were open.
//
// Exactly one of the two rails owns the selection at a time. While a file is showing, the
// segment control stands down (`DrawerViewTabs`'s `selected`), so there is never a second
// highlighted chip claiming to be what the body is drawing; clicking either segment brings
// the index back and hands the selection to it.
//
// Chips carry a close control rather than being permanent the way Notes' rail tabs are.
// Notes' rail is every note in the Project and is therefore bounded by the Project; this one
// is whatever you happened to open, so it closes and it is capped (`drawerFiles.ts`).

type Props = {
  /** Absolute Project root, for the menu's full-path copy. */
  projectRoot: string
  files: readonly DrawerFileTab[]
  /** The file being shown, or null while the index is. */
  active: string | null
  /** Paths with edits that are not on disk. Read live from the draft cache, so closing one
   *  asks first instead of discarding it. */
  unsaved: ReadonlySet<string>
  onSelect: (path: string) => void
  onClose: (path: string) => void
  onCloseOthers: (path: string) => void
  /** Move this file out of the drawer and into a workspace pane. Closes the drawer tab: one
   *  live editor per file per browser is the same correctness rule notes follow. */
  onOpenInPane: (path: string) => void
}

type FileMenu = { path: string; x: number; y: number }

const NOTICE_MS = 1600

export function DrawerFilesRail({ projectRoot, files, active, unsaved, onSelect, onClose, onCloseOthers, onOpenInPane }: Props) {
  const [menu, setMenu] = useState<FileMenu | null>(null)
  // Closing a file with unsaved edits is a two-click act, the same shape the note delete
  // control uses. A confirmation dialog for something this frequent would be worse than the
  // mistake it prevents, and a silent close is not on the table.
  const [confirmClose, setConfirmClose] = useState('')
  const [notice, setNotice] = useState('')
  const press = useRailLongPress()
  const menuPanel = useRef<HTMLDivElement>(null)
  const copyFallback = useRef<HTMLTextAreaElement>(null)
  const noticeTimer = useRef<number | null>(null)

  useDismissLevel(() => { setMenu(null); setConfirmClose('') }, !!menu, 'drawer-file-tab-menu')

  useEffect(() => () => { if (noticeTimer.current !== null) window.clearTimeout(noticeTimer.current) }, [])

  // A file that is no longer open cannot be the subject of an open menu or an armed
  // confirmation. Both would otherwise act on a path the rail has stopped showing.
  useEffect(() => {
    const paths = new Set(files.map(file => file.path))
    if (menu && !paths.has(menu.path)) setMenu(null)
    if (confirmClose && !paths.has(confirmClose)) setConfirmClose('')
  }, [files])

  useEffect(() => {
    if (!menu) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const frame = requestAnimationFrame(() => menuPanel.current?.querySelector<HTMLButtonElement>('button')?.focus())
    const dismiss = (event: Event) => {
      const target = event.target
      if (target instanceof Element && target.closest('.drawer-file-menu')) return
      setMenu(null); setConfirmClose('')
    }
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopImmediatePropagation(); dismissStack.pop(); return
      }
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
      const buttons = [...menuPanel.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') || []]
      if (!buttons.length) return
      event.preventDefault()
      const current = buttons.indexOf(document.activeElement as HTMLButtonElement)
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
        : (Math.max(current, 0) + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
      buttons[next].focus()
    }
    document.addEventListener('pointerdown', dismiss)
    window.addEventListener('blur', dismiss)
    window.addEventListener('keydown', key, true)
    return () => {
      cancelAnimationFrame(frame)
      document.removeEventListener('pointerdown', dismiss)
      window.removeEventListener('blur', dismiss)
      window.removeEventListener('keydown', key, true)
      previous?.focus()
    }
  }, [menu])

  if (!files.length) return null

  const labels = fileTabLabels(files.map(file => file.path))
  const openMenu = (path: string) => (x: number, y: number) => { setConfirmClose(''); setMenu({ path, x, y }) }

  const flash = (message: string) => {
    setNotice(message)
    if (noticeTimer.current !== null) window.clearTimeout(noticeTimer.current)
    noticeTimer.current = window.setTimeout(() => { setNotice(''); noticeTimer.current = null }, NOTICE_MS)
  }
  const copyPath = async (path: string, form: 'relative' | 'absolute') => {
    setMenu(null)
    const text = form === 'absolute' ? absoluteProjectPath(projectRoot, path) : path
    flash(await copyPreparedText(text, copyFallback.current)
      ? `Copied ${form} path`
      : 'Clipboard write was blocked')
  }

  /** Closing is guarded only while there is something to lose, so the ordinary close stays
   *  one click. The check reads the draft cache at click time rather than a remembered flag,
   *  because a save can land between the render and the click. */
  const requestClose = (path: string) => {
    if (unsaved.has(path) && confirmClose !== path) { setConfirmClose(path); return }
    setConfirmClose('')
    setMenu(null)
    onClose(path)
  }

  const focusAdjacent = (event: JSX.TargetedKeyboardEvent<HTMLButtonElement>, offset: number) => {
    const buttons = [...event.currentTarget.closest('[role="tablist"]')?.querySelectorAll<HTMLButtonElement>('[role="tab"]') || []]
    const index = buttons.indexOf(event.currentTarget)
    const next = buttons[(index + offset + buttons.length) % buttons.length]
    if (!next) return
    event.preventDefault(); next.click(); next.focus()
  }

  const menuPath = menu?.path || ''
  const menuLabel = menuPath ? labels.get(menuPath) || menuPath : ''
  const menuUnsaved = !!menuPath && unsaved.has(menuPath)

  return <div class="drawer-file-subtabs-row">
    <OverflowRail
      className="drawer-file-subtabs"
      wrapperClassName="drawer-file-subtabs-rail"
      activeKey={active || ''}
      touchDrag
      stripProps={{ role: 'tablist', 'aria-label': 'Files open in this panel' }}
    >
      {files.map(file => {
        const path = file.path
        const on = active === path
        const dirty = unsaved.has(path)
        const arming = confirmClose === path
        return <div class={`drawer-file-tab-shell ${on ? 'active' : ''}`} key={path}>
          <button
            role="tab"
            aria-selected={on}
            tabIndex={on ? 0 : -1}
            class={`drawer-file-tab ${on ? 'active' : ''}`}
            title={`${path}${dirty ? ' · unsaved' : ''} · right-click or hold for actions`}
            data-path={path}
            onClick={() => onSelect(path)}
            onContextMenu={event => press.contextMenu(event, openMenu(path))}
            onPointerDown={event => press.begin(event, openMenu(path))}
            onClickCapture={press.suppressClick}
            onKeyDown={event => {
              if (press.keyboardMenu(event, openMenu(path))) return
              if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') focusAdjacent(event, event.key === 'ArrowLeft' ? -1 : 1)
            }}
          >{dirty && <i class="drawer-file-dirty" aria-hidden="true">•</i>}{labels.get(path) || path}</button>
          <button
            class={`drawer-file-close ${arming ? 'confirming' : ''}`}
            aria-label={arming
              ? `Confirm closing ${path} with unsaved edits`
              : `Close ${path}${dirty ? ', which has unsaved edits' : ''}`}
            title={arming ? 'Click again to close and discard the unsaved edits' : 'Close'}
            onBlur={() => setConfirmClose(current => current === path ? '' : current)}
            onClick={() => requestClose(path)}
          >{arming ? '×?' : '×'}</button>
        </div>
      })}
    </OverflowRail>
    {/* The same `⇥` the Notes rail carries, in the same corner, for the same act. A menu item
        alone would make the pane placement discoverable only to someone who already suspected
        it existed, and this is the control that has to stay obvious now that a plain click no
        longer lands there. Disabled while the index is showing: there is no file to move. */}
    <button
      class="drawer-file-pop"
      aria-label="Move the file being shown into a workspace pane"
      title="Move the file being shown into a workspace pane"
      disabled={!active}
      onClick={() => { if (active) onOpenInPane(active) }}
    >⇥</button>
    {notice && <p class="drawer-file-notice" role="status">{notice}</p>}
    <textarea ref={copyFallback} class="resource-copy-relay" aria-hidden="true" tabIndex={-1} readOnly />
    {menu && <div
      class="context-menu drawer-file-menu"
      ref={el => { menuPanel.current = el; fitMenuInViewport(el) }}
      role="menu"
      aria-label={`Actions for ${menuLabel}`}
      style={{ left: clampContextMenuLeft(menu.x, window.innerWidth), top: Math.max(4, menu.y) }}
      onClickCapture={press.suppressMenuEcho}
    >
      <div class="context-title"><strong>{menuLabel}</strong></div>
      <button role="menuitem" title="Move this file into a workspace pane, beside the terminal" onClick={() => { setMenu(null); onOpenInPane(menuPath) }}>Open in a pane</button>
      <button role="menuitem" title={menuPath} onClick={() => void copyPath(menuPath, 'relative')}>Copy path from project root</button>
      <button role="menuitem" title={absoluteProjectPath(projectRoot, menuPath)} onClick={() => void copyPath(menuPath, 'absolute')}>Copy full path</button>
      <div class="context-rule" />
      <button
        role="menuitem"
        class={confirmClose === menuPath ? 'danger confirming' : ''}
        onClick={() => requestClose(menuPath)}
      >{confirmClose === menuPath ? 'Confirm close' : 'Close'}</button>
      <button role="menuitem" disabled={files.length < 2} onClick={() => { setMenu(null); onCloseOthers(menuPath) }}>Close others</button>
      {menuUnsaved && <p class="context-note">This file has edits that are not on disk. Closing it discards them.</p>}
    </div>}
  </div>
}
