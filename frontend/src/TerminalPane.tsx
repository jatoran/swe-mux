import { useEffect, useRef, useState } from 'preact/hooks'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { SearchAddon } from '@xterm/addon-search'
import { WebLinksAddon } from '@xterm/addon-web-links'
import { WebglAddon } from '@xterm/addon-webgl'
import { Unicode11Addon } from '@xterm/addon-unicode11'
import { ClipboardAddon } from '@xterm/addon-clipboard'
import '@xterm/xterm/css/xterm.css'
import { openWebSocket, upload } from './api'
import type { Session } from './types'
import { keyChord } from './keys'
import { resolvedTheme, terminalThemes, type ThemeName } from './theme'
import { terminalKeyDecision } from './terminalKeys'
import { clampContextMenuLeft } from './menuPosition'

interface Props { session: Session; onState: (session: Session) => void; broadcast: boolean; keybindings: Record<string, string>; scrollback: number }

function runCommand(command: string) {
  window.dispatchEvent(new CustomEvent('mux:command', { detail: command }))
}

function reportError(message: string) {
  window.dispatchEvent(new CustomEvent('mux:error', { detail: message }))
}

export function TerminalPane({ session, onState, broadcast, keybindings, scrollback }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const termRef = useRef<Terminal | null>(null)
  const searchRef = useRef<SearchAddon | null>(null)
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)
  const [findOpen, setFindOpen] = useState(false)
  const [findQuery, setFindQuery] = useState('')
  const [findCase, setFindCase] = useState(false)
  const [findResult, setFindResult] = useState<string>('')
  const broadcastRef = useRef(broadcast)
  broadcastRef.current = broadcast

  useEffect(() => {
    const openFind = (event: Event) => {
      if ((event as CustomEvent<string | null>).detail !== session.id) return
      setFindOpen(true)
      setMenu(null)
    }
    window.addEventListener('mux:terminal-find', openFind)
    return () => window.removeEventListener('mux:terminal-find', openFind)
  }, [session.id])

  useEffect(() => {
    if (!menu) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const frame = requestAnimationFrame(() => document.querySelector<HTMLButtonElement>('.terminal-menu button:not(:disabled)')?.focus())
    const dismissOutside = (event: PointerEvent) => {
      const target = event.target
      if (!(target instanceof Element) || !target.closest('.terminal-menu')) setMenu(null)
    }
    const dismissEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      setMenu(null)
    }
    const navigate = (event: KeyboardEvent) => {
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
      const buttons = [...document.querySelectorAll<HTMLButtonElement>('.terminal-menu button:not(:disabled)')]
      if (!buttons.length) return
      event.preventDefault()
      const current = buttons.indexOf(document.activeElement as HTMLButtonElement)
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
        : (Math.max(current, 0) + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
      buttons[next].focus()
    }
    document.addEventListener('pointerdown', dismissOutside)
    window.addEventListener('keydown', dismissEscape, true)
    window.addEventListener('keydown', navigate, true)
    return () => {
      cancelAnimationFrame(frame)
      document.removeEventListener('pointerdown', dismissOutside)
      window.removeEventListener('keydown', dismissEscape, true)
      window.removeEventListener('keydown', navigate, true)
      previous?.focus()
    }
  }, [menu])

  useEffect(() => {
    if (!host.current) return
    const term = new Terminal({
      cursorBlink: true, cursorStyle: 'bar', fontFamily: '"Cascadia Mono", Consolas, monospace',
      fontSize: 11, fontWeight: '600', fontWeightBold: '600', lineHeight: 1.2, scrollback, allowProposedApi: true,
      screenReaderMode: true,
      theme: terminalThemes[resolvedTheme((document.documentElement.dataset.themeSelection || 'dark') as ThemeName)],
    })
    const fit = new FitAddon()
    const search = new SearchAddon()
    term.loadAddon(fit)
    term.loadAddon(search)
    term.loadAddon(new WebLinksAddon())
    term.loadAddon(new ClipboardAddon())
    term.loadAddon(new Unicode11Addon())
    term.unicode.activeVersion = '11'
    termRef.current = term
    searchRef.current = search
    term.open(host.current)
    const onTheme = (event: Event) => {
      const name = (event as CustomEvent<Exclude<ThemeName,'system'>>).detail
      term.options.theme = terminalThemes[name]
    }
    window.addEventListener('mux:theme', onTheme)
    try {
      const webgl = new WebglAddon()
      term.loadAddon(webgl)
    } catch {
      // Canvas renderer remains active on machines without WebGL support.
    }
    term.attachCustomKeyEventHandler(event => {
      const decision = terminalKeyDecision(event, keybindings[keyChord(event)], term.hasSelection())
      if (decision.kind === 'command') {
        event.preventDefault()
        event.stopPropagation()
        runCommand(decision.command)
        return false
      }
      if (decision.kind === 'pasteText') {
        void navigator.clipboard.readText().then(text => term.paste(text)).catch(() => {
          runCommand('clipboard.help')
        })
        return false
      }
      if (decision.kind === 'copySelection') {
        void navigator.clipboard.writeText(term.getSelection()).catch(() => runCommand('clipboard.help'))
        term.clearSelection()
        return false
      }
      return true
    })
    const socket = openWebSocket(`/pty/${session.id}`)
    socket.binaryType = 'arraybuffer'
    let replaying = false
    let replayEndReceived = false
    let pendingReplayWrites = 0
    let currentRevision = -1
    let exitWritten = false
    let fitFrame = 0
    const scheduleFit = () => {
      window.cancelAnimationFrame(fitFrame)
      fitFrame = window.requestAnimationFrame(() => {
        fit.fit()
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
        }
      })
    }
    const claimInput = () => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'claim_input' }))
    }
    socket.onopen = () => {
      scheduleFit()
      claimInput()
      term.focus()
    }
    socket.onmessage = event => {
      if (event.data instanceof ArrayBuffer) {
        if (replaying) {
          pendingReplayWrites += 1
          term.write(new Uint8Array(event.data), () => {
            pendingReplayWrites -= 1
            if (replayEndReceived && pendingReplayWrites === 0) replaying = false
          })
        } else term.write(new Uint8Array(event.data))
      }
      else {
        const frame = JSON.parse(event.data)
        if (frame.type === 'gap') replaying = true
        if ((frame.type === 'state' || frame.type === 'update') && Number(frame.revision ?? 0) > currentRevision) {
          currentRevision = Number(frame.revision ?? 0)
          onState(frame.snapshot)
        }
        if (frame.type === 'replay_start') {
          if (frame.reason === 'resync') term.reset()
          replaying = true
          replayEndReceived = false
        }
        if (frame.type === 'replay_end') {
          replayEndReceived = true
          if (pendingReplayWrites === 0) replaying = false
        }
        if (frame.type === 'exit') {
          if (frame.snapshot && Number(frame.revision ?? 0) >= currentRevision) {
            currentRevision = Number(frame.revision ?? 0)
            onState(frame.snapshot)
          }
          if (!exitWritten) {
            exitWritten = true
            term.writeln('\r\n\x1b[38;5;243m[process exited]\x1b[0m')
          }
        }
      }
    }
    const input = term.onData(data => !replaying && socket.readyState === WebSocket.OPEN && socket.send(JSON.stringify({ type: 'input', data, broadcast: broadcastRef.current })))
    let longPress: number | null = null
    const cancelLongPress = () => { if (longPress !== null) window.clearTimeout(longPress); longPress = null }
    const pointerClaim = (event: PointerEvent) => {
      claimInput()
      if (event.pointerType === 'touch') {
        cancelLongPress()
        longPress = window.setTimeout(() => {
          navigator.vibrate?.(20)
          setMenu({x:event.clientX,y:event.clientY})
          longPress = null
        },550)
      }
    }
    const openMenu = (event: MouseEvent) => {
      event.preventDefault()
      setMenu({ x: event.clientX, y: event.clientY })
    }
    host.current.addEventListener('pointerdown', pointerClaim)
    host.current.addEventListener('pointerup', cancelLongPress)
    host.current.addEventListener('pointermove', cancelLongPress)
    host.current.addEventListener('pointercancel', cancelLongPress)
    host.current.addEventListener('focusin', claimInput)
    host.current.addEventListener('contextmenu', openMenu)
    const observer = new ResizeObserver(scheduleFit)
    observer.observe(host.current)
    window.addEventListener('resize', scheduleFit)
    return () => { input.dispose(); cancelLongPress(); observer.disconnect(); window.cancelAnimationFrame(fitFrame); window.removeEventListener('resize', scheduleFit); window.removeEventListener('mux:theme', onTheme); host.current?.removeEventListener('pointerdown', pointerClaim); host.current?.removeEventListener('pointerup', cancelLongPress); host.current?.removeEventListener('pointermove', cancelLongPress); host.current?.removeEventListener('pointercancel', cancelLongPress); host.current?.removeEventListener('focusin', claimInput); host.current?.removeEventListener('contextmenu', openMenu); socket.close(); term.dispose(); termRef.current = null; searchRef.current = null }
  }, [session.id, keybindings, scrollback])

  const copy = () => {
    const term = termRef.current
    if (!term?.hasSelection()) { reportError('Copy requires a terminal selection.'); setMenu(null); return }
    void navigator.clipboard.writeText(term.getSelection()).catch(() => runCommand('clipboard.help'))
    term?.clearSelection(); setMenu(null)
  }
  const paste = () => {
    void navigator.clipboard.readText().then(text => termRef.current?.paste(text)).catch(() => runCommand('clipboard.help'))
    setMenu(null)
  }
  const pasteImage = async () => {
    setMenu(null)
    if (session.backend !== 'claude' && session.backend !== 'codex') {
      reportError('Clipboard images can be inserted only into Claude or Codex sessions.')
      return
    }
    try {
      const items = await navigator.clipboard.read()
      const item = items.find(candidate => candidate.types.some(type => type.startsWith('image/')))
      const mediaType = item?.types.find(type => type.startsWith('image/'))
      if (!item || !mediaType) throw new Error('The clipboard does not contain a supported image.')
      const blob = await item.getType(mediaType)
      const form = new FormData()
      form.append('file', blob, `clipboard.${mediaType.split('/')[1] || 'png'}`)
      const result = await upload<{reference:string}>(`/api/sessions/${session.id}/media`, form)
      termRef.current?.paste(result.reference)
      termRef.current?.focus()
    } catch (cause) {
      reportError(cause instanceof Error ? cause.message : 'Clipboard image paste failed.')
    }
  }
  const find = () => {
    setFindOpen(true)
    setMenu(null)
  }

  const search = (previous = false) => {
    if (!findQuery) { setFindResult(''); return }
    const found = previous
      ? searchRef.current?.findPrevious(findQuery, { caseSensitive: findCase })
      : searchRef.current?.findNext(findQuery, { caseSensitive: findCase })
    setFindResult(found ? 'match' : 'no match')
  }

  const closeFind = () => {
    setFindOpen(false)
    setFindResult('')
    termRef.current?.focus()
  }

  useEffect(() => {
    const onAction = (event: Event) => {
      const detail = (event as CustomEvent<{sessionId:string|null;action:string;text?:string;targetKey?:string}>).detail
      if (detail.sessionId !== session.id) return
      if (detail.action === 'copy') copy()
      else if (detail.action === 'paste') paste()
      else if (detail.action === 'pasteImage') void pasteImage()
      else if (detail.action === 'selectAll') { termRef.current?.selectAll(); setMenu(null) }
      else if (detail.action === 'clear') { termRef.current?.clear(); setMenu(null) }
      else if (detail.action === 'find') find()
      else if (detail.action === 'insertText' && detail.text) { termRef.current?.paste(detail.text); termRef.current?.focus() }
      else if (detail.action === 'captureSelection') {
        const term = termRef.current
        if (!term?.hasSelection()) reportError('Select terminal text before capturing it into notes.')
        else window.dispatchEvent(new CustomEvent('mux:terminal-selection', { detail: { sessionId: session.id, text: term.getSelection(), targetKey: detail.targetKey } }))
      }
    }
    window.addEventListener('mux:terminal-action', onAction)
    return () => window.removeEventListener('mux:terminal-action', onAction)
  }, [session.id, session.backend])

  return <><div class="terminal-host" ref={host} />{findOpen && <div class="terminal-find" role="search">
    <input value={findQuery} onInput={event => { setFindQuery(event.currentTarget.value); setFindResult('') }} onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeFind() }
      if (event.key === 'Enter') { event.preventDefault(); search(event.shiftKey) }
    }} placeholder="find in terminal" aria-label="Find in terminal" autofocus />
    <button title="Previous match" onClick={() => search(true)}>↑</button>
    <button title="Next match" onClick={() => search(false)}>↓</button>
    <button class={findCase ? 'active' : ''} title="Match case" aria-pressed={findCase} onClick={() => { setFindCase(value => !value); setFindResult('') }}>Aa</button>
    <span class={findResult === 'no match' ? 'missing' : ''}>{findResult}</span>
    <button title="Close find" onClick={closeFind}>×</button>
  </div>}<button class="mobile-paste" onClick={() => runCommand('terminal.paste')}>Paste</button>{(session.backend === 'claude' || session.backend === 'codex') && <button class="mobile-paste mobile-paste-image" onClick={() => runCommand('terminal.pasteImage')}>Paste image</button>}{menu && <div class="terminal-menu" role="menu" style={{ left: clampContextMenuLeft(menu.x, innerWidth), top: Math.min(menu.y, innerHeight - 230) }}>
    <button role="menuitem" disabled={!termRef.current?.hasSelection()} onClick={() => runCommand('terminal.copy')}>Copy</button>
    <button role="menuitem" onClick={() => runCommand('terminal.paste')}>Paste</button>
    {(session.backend === 'claude' || session.backend === 'codex') && <button role="menuitem" onClick={() => runCommand('terminal.pasteImage')}>Paste image</button>}
    <button role="menuitem" onClick={() => runCommand('terminal.selectAll')}>Select all</button>
    <button role="menuitem" onClick={() => runCommand('terminal.find')}>Find…</button>
    <button role="menuitem" onClick={() => runCommand('terminal.clear')}>Clear display</button>
    <button role="menuitem" onClick={() => runCommand('session.notes')}>Session note…</button>
    <button role="menuitem" onClick={() => runCommand('session.notesSplit')}>Session note in split</button>
    <button role="menuitem" onClick={() => runCommand('processes.open')}>Processes and previews…</button>
    <div class="context-rule" />
    <button role="menuitem" onClick={() => runCommand('pane.splitHorizontal')}>Split right</button>
    <button role="menuitem" onClick={() => runCommand('pane.splitVertical')}>Split below</button>
    <button role="menuitem" onClick={() => runCommand('pane.detach')}>Detach pane</button>
    <button role="menuitem" onClick={() => runCommand('pane.zoom')}>Zoom pane</button>
    <button role="menuitem" class="danger" onClick={() => runCommand('session.kill')}>Kill session</button>
  </div>}</>
}
