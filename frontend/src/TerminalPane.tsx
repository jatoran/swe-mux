import { useEffect, useRef, useState } from 'preact/hooks'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { SearchAddon } from '@xterm/addon-search'
import { WebLinksAddon } from '@xterm/addon-web-links'
import { WebglAddon } from '@xterm/addon-webgl'
import { Unicode11Addon } from '@xterm/addon-unicode11'
import { ClipboardAddon } from '@xterm/addon-clipboard'
import '@xterm/xterm/css/xterm.css'
import { wsUrl } from './api'
import type { Session } from './types'
import { keyChord } from './keys'

interface Props { session: Session; onState: (session: Session) => void; broadcast: boolean; keybindings: Record<string, string> }

function runCommand(command: string) {
  window.dispatchEvent(new CustomEvent('mux:command', { detail: command }))
}

export function TerminalPane({ session, onState, broadcast, keybindings }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const termRef = useRef<Terminal | null>(null)
  const searchRef = useRef<SearchAddon | null>(null)
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)
  const broadcastRef = useRef(broadcast)
  broadcastRef.current = broadcast

  useEffect(() => {
    if (!menu) return
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
    document.addEventListener('pointerdown', dismissOutside)
    window.addEventListener('keydown', dismissEscape, true)
    return () => {
      document.removeEventListener('pointerdown', dismissOutside)
      window.removeEventListener('keydown', dismissEscape, true)
    }
  }, [menu])

  useEffect(() => {
    if (!host.current) return
    const term = new Terminal({
      cursorBlink: true, cursorStyle: 'bar', fontFamily: '"Cascadia Mono", Consolas, monospace',
      fontSize: 11, fontWeight: '600', fontWeightBold: '600', lineHeight: 1.2, scrollback: 10000, allowProposedApi: true,
      screenReaderMode: true,
      theme: { background: '#0a0c0f', foreground: '#d9dde5', cursor: '#9fe870', selectionBackground: '#35512899', black: '#15191f', brightBlack: '#586171', green: '#8bd450', brightGreen: '#b1f477', cyan: '#6fd3d8', blue: '#6e9ef7', yellow: '#e7c768', red: '#f07178', magenta: '#c792ea' },
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
    try {
      const webgl = new WebglAddon()
      term.loadAddon(webgl)
    } catch {
      // Canvas renderer remains active on machines without WebGL support.
    }
    term.attachCustomKeyEventHandler(event => {
      if (event.type !== 'keydown') return true
      const key = event.key.toLowerCase()
      const command = keybindings[keyChord(event)]
      if (command) {
        if (command === 'terminal.find') {
          const query = prompt('Find in terminal')
          if (query) search.findNext(query)
        } else {
          runCommand(command)
        }
        return false
      }
      if (event.ctrlKey && (key === 'v' || (event.shiftKey && key === 'v'))) {
        void navigator.clipboard.readText().then(text => term.paste(text)).catch(() => {
          runCommand('clipboard.help')
        })
        return false
      }
      if (event.ctrlKey && event.shiftKey && key === 'c') {
        if (term.hasSelection()) {
          void navigator.clipboard.writeText(term.getSelection())
          term.clearSelection()
        }
        return false
      }
      if (event.ctrlKey && key === 'c' && term.hasSelection()) {
        void navigator.clipboard.writeText(term.getSelection())
        term.clearSelection()
        return false
      }
      return true
    })
    const socket = new WebSocket(wsUrl(`/pty/${session.id}`))
    socket.binaryType = 'arraybuffer'
    let replaying = false
    let replayEndReceived = false
    let pendingReplayWrites = 0
    const claimInput = () => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'claim_input' }))
    }
    socket.onopen = () => {
      fit.fit()
      socket.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
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
        if (frame.type === 'state') onState(frame.snapshot)
        if (frame.type === 'replay_start') {
          replaying = true
          replayEndReceived = false
        }
        if (frame.type === 'replay_end') {
          replayEndReceived = true
          if (pendingReplayWrites === 0) replaying = false
        }
        if (frame.type === 'exit') term.writeln('\r\n\x1b[38;5;243m[process exited]\x1b[0m')
      }
    }
    const input = term.onData(data => !replaying && socket.readyState === WebSocket.OPEN && socket.send(JSON.stringify({ type: 'input', data, broadcast: broadcastRef.current })))
    const pointerClaim = () => claimInput()
    const openMenu = (event: MouseEvent) => {
      event.preventDefault()
      setMenu({ x: event.clientX, y: event.clientY })
    }
    host.current.addEventListener('pointerdown', pointerClaim)
    host.current.addEventListener('focusin', claimInput)
    host.current.addEventListener('contextmenu', openMenu)
    const observer = new ResizeObserver(() => {
      fit.fit()
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
    })
    observer.observe(host.current)
    return () => { input.dispose(); observer.disconnect(); host.current?.removeEventListener('pointerdown', pointerClaim); host.current?.removeEventListener('focusin', claimInput); host.current?.removeEventListener('contextmenu', openMenu); socket.close(); term.dispose(); termRef.current = null; searchRef.current = null }
  }, [session.id, keybindings])

  const copy = () => {
    const term = termRef.current
    if (term?.hasSelection()) void navigator.clipboard.writeText(term.getSelection())
    term?.clearSelection(); setMenu(null)
  }
  const paste = () => {
    void navigator.clipboard.readText().then(text => termRef.current?.paste(text))
    setMenu(null)
  }
  const find = () => {
    const query = prompt('Find in terminal')
    if (query) searchRef.current?.findNext(query)
    setMenu(null)
  }

  return <><div class="terminal-host" ref={host} /><button class="mobile-paste" onClick={paste}>Paste</button>{menu && <div class="terminal-menu" style={{ left: menu.x, top: menu.y }}>
    <button disabled={!termRef.current?.hasSelection()} onClick={copy}>Copy</button>
    <button onClick={paste}>Paste</button>
    <button onClick={() => { termRef.current?.selectAll(); setMenu(null) }}>Select all</button>
    <button onClick={find}>Find…</button>
    <button onClick={() => { termRef.current?.clear(); setMenu(null) }}>Clear display</button>
  </div>}</>
}
