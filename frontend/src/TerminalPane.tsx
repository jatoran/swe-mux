import { useEffect, useRef, useState } from 'preact/hooks'
import { memo } from 'preact/compat'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { SearchAddon } from '@xterm/addon-search'
import { WebLinksAddon } from '@xterm/addon-web-links'
import { WebglAddon } from '@xterm/addon-webgl'
import { Unicode11Addon } from '@xterm/addon-unicode11'
import { ClipboardAddon } from '@xterm/addon-clipboard'
import '@xterm/xterm/css/xterm.css'
import { openWebSocket, uploadTerminalImage } from './api'
import type { Session } from './types'
import { keyChord } from './keys'
import { resolvedTheme, terminalThemes, type ThemeName } from './theme'
import { terminalKeyDecision } from './terminalKeys'
import { isTerminalProtocolResponse } from './terminalProtocol'
import { clampContextMenuLeft } from './menuPosition'
import { mobileDragTarget, terminalScrollLines, touchWheelDelta, type MobileInputSettings } from './mobileInput'
import { clipboardImage, copyPreparedText, hasTerminalImage, isTerminalImage, ResilientClipboardProvider } from './terminalClipboard'
import { redrawVisibleTerminal, refitVisibleTerminal } from './terminalViewport'

type StartupMilestone = 'pane_mounted' | 'socket_open' | 'replay_ready'

interface Props {
  session: Session
  onState: (session: Session) => void
  onStartupTiming?: (milestone: StartupMilestone, elapsedMs: number) => void
  startupOrigin?: number
  broadcast: boolean
  keybindings: Record<string, string>
  scrollback: number
  mobileInput: MobileInputSettings
}

function runCommand(command: string) {
  window.dispatchEvent(new CustomEvent('mux:command', { detail: command }))
}

function reportError(message: string) {
  window.dispatchEvent(new CustomEvent('mux:error', { detail: message }))
}

function acceptsClipboardImages(session: Session) {
  return session.backend === 'claude' || session.backend === 'codex'
}

async function insertTerminalImage(term: Terminal, session: Session, blob: Blob) {
  if (!acceptsClipboardImages(session)) throw new Error('Images can be attached only to Claude or Codex sessions.')
  if (!isTerminalImage(blob)) throw new Error('Supported image types are PNG, JPEG, WebP, and GIF.')
  if (session.backend === 'codex' && !term.modes.bracketedPasteMode) {
    throw new Error('Codex is not ready for an image yet. Wait for its chat prompt and try again.')
  }
  const form = new FormData()
  form.append('file', blob, `clipboard.${blob.type.split('/')[1] || 'png'}`)
  const result = await uploadTerminalImage<{reference:string}>(`/api/sessions/${session.id}/media`, form)
  // Claude and Codex both recognize one isolated bracketed paste containing a readable
  // image path. Codex turns it into LocalImage rather than leaving the path as draft text.
  term.paste(result.reference)
  term.focus()
}

async function pasteBrowserClipboard(term: Terminal, session: Session) {
  if (acceptsClipboardImages(session) && typeof navigator.clipboard.read === 'function') {
    try {
      const items = await navigator.clipboard.read()
      const item = items.find(candidate => candidate.types.some(type => type.startsWith('image/')))
      const mediaType = item?.types.find(type => type.startsWith('image/'))
      if (item && mediaType) {
        await insertTerminalImage(term, session, await item.getType(mediaType))
        return
      }
    } catch {
      // Some browsers block the richer Clipboard API while still allowing text reads.
    }
  }
  term.paste(await navigator.clipboard.readText())
  term.focus()
}

function TerminalPaneImpl({ session, onState, onStartupTiming, startupOrigin, broadcast, keybindings, scrollback, mobileInput }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const termRef = useRef<Terminal | null>(null)
  const searchRef = useRef<SearchAddon | null>(null)
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)
  const [findOpen, setFindOpen] = useState(false)
  const [findQuery, setFindQuery] = useState('')
  const [findCase, setFindCase] = useState(false)
  const [findResult, setFindResult] = useState<string>('')
  const [connectionState,setConnectionState]=useState<'connecting'|'connected'|'reconnecting'|'ended'>('connecting')
  const [preparedClipboard,setPreparedClipboard]=useState('')
  const [manualClipboard,setManualClipboard]=useState(false)
  const [imageDropActive,setImageDropActive]=useState(false)
  const manualClipboardRef=useRef<HTMLTextAreaElement>(null)
  const stateRef=useRef(session.state)
  stateRef.current=session.state
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
    const reportStartup = (milestone: StartupMilestone) => {
      if (startupOrigin !== undefined) onStartupTiming?.(milestone, performance.now() - startupOrigin)
    }
    reportStartup('pane_mounted')
    const term = new Terminal({
      cursorBlink: true, cursorStyle: 'bar', fontFamily: '"Cascadia Mono", Consolas, monospace',
      fontSize: 11, fontWeight: '600', fontWeightBold: '600', lineHeight: 1.2, scrollback, allowProposedApi: true,
      screenReaderMode: false,
      theme: terminalThemes[resolvedTheme((document.documentElement.dataset.themeSelection || 'dark') as ThemeName)],
    })
    const fit = new FitAddon()
    const search = new SearchAddon()
    term.loadAddon(fit)
    term.loadAddon(search)
    term.loadAddon(new WebLinksAddon())
    term.loadAddon(new ClipboardAddon(undefined,new ResilientClipboardProvider(
      text=>{setPreparedClipboard(text);setManualClipboard(false)},
      reportError,
    )))
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
    term.attachCustomKeyEventHandler(event => {
      const decision = terminalKeyDecision(event, keybindings[keyChord(event)], term.hasSelection())
      if (decision.kind === 'command') {
        event.preventDefault()
        event.stopPropagation()
        runCommand(decision.command)
        return false
      }
      if (decision.kind === 'browserPaste') {
        // Suppress xterm's Ctrl+V control byte but leave the browser default untouched.
        // The resulting native paste event is the single owner of clipboard payloads.
        return false
      }
      if (decision.kind === 'copySelection') {
        void navigator.clipboard.writeText(term.getSelection()).catch(() => runCommand('clipboard.help'))
        term.clearSelection()
        return false
      }
      return true
    })
    let socket:WebSocket|null=null
    let reconnectTimer:number|undefined
    let reconnectAttempt=0
    let reconnectReplay=false
    let disposed=false
    let hiddenAt:number|null=document.hidden?Date.now():null
    let replaying = false
    let replayEndReceived = false
    let replayAllowsTerminalResponses = false
    let pendingReplayWrites = 0
    let currentRevision = -1
    let exitWritten = false
    let fitFrame = 0
    let redrawFrame = 0
    let invalidateAtlasOnRedraw = false
    let webgl: WebglAddon | null = null
    const scheduleViewport = (invalidateAtlas: boolean) => {
      invalidateAtlasOnRedraw ||= invalidateAtlas
      window.cancelAnimationFrame(fitFrame)
      window.cancelAnimationFrame(redrawFrame)
      fitFrame = window.requestAnimationFrame(() => {
        if (!refitVisibleTerminal(fit, host.current)) return
        if (socket?.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
        }
        // Canvas/WebGL pixels can be discarded while a pane or browser tab is hidden.
        // Repaint one frame after layout settles so every terminal row is invalidated.
        redrawFrame = window.requestAnimationFrame(() => {
          if (invalidateAtlasOnRedraw) webgl?.clearTextureAtlas()
          invalidateAtlasOnRedraw = false
          redrawVisibleTerminal(term, host.current)
        })
      })
    }
    const scheduleFit = () => scheduleViewport(false)
    const scheduleFullRedraw = () => scheduleViewport(true)
    try {
      const addon = new WebglAddon()
      webgl = addon
      addon.onContextLoss(() => {
        if (webgl !== addon) return
        webgl = null
        addon.dispose()
        scheduleFullRedraw()
      })
      term.loadAddon(addon)
    } catch {
      webgl = null
      // Canvas renderer remains active on machines without WebGL support.
    }
    const claimInput = () => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'claim_input' }))
    }
    const scheduleReconnect=()=>{
      if(disposed||['exited','crashed'].includes(stateRef.current)||reconnectTimer!==undefined)return
      setConnectionState('reconnecting')
      const delay=Math.min(1000*2**reconnectAttempt,10000)
      reconnectAttempt+=1
      reconnectTimer=window.setTimeout(()=>{reconnectTimer=undefined;connect(true)},delay)
    }
    const handleMessage=(event:MessageEvent)=>{
      if (event.data instanceof ArrayBuffer) {
        if (replaying) {
          pendingReplayWrites += 1
          term.write(new Uint8Array(event.data), () => {
            pendingReplayWrites -= 1
            if (replayEndReceived && pendingReplayWrites === 0) {
              replaying = false
              replayAllowsTerminalResponses = false
              scheduleFullRedraw()
            }
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
          if (frame.reason === 'resync' || reconnectReplay) term.reset()
          reconnectReplay=false
          replaying = true
          replayEndReceived = false
          replayAllowsTerminalResponses = frame.reason === 'attach' && frame.allow_terminal_responses === true
        }
        if (frame.type === 'replay_end') {
          reportStartup('replay_ready')
          replayEndReceived = true
          if (pendingReplayWrites === 0) {
            replaying = false
            replayAllowsTerminalResponses = false
            scheduleFullRedraw()
          }
        }
        if (frame.type === 'exit') {
          setConnectionState('ended')
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
    const connect=(reconnecting:boolean)=>{
      if(disposed||['exited','crashed'].includes(stateRef.current))return
      if(reconnectTimer!==undefined){clearTimeout(reconnectTimer);reconnectTimer=undefined}
      reconnectReplay=reconnecting
      setConnectionState(reconnecting?'reconnecting':'connecting')
      const next=openWebSocket(`/pty/${session.id}`)
      next.binaryType='arraybuffer'
      socket=next
      next.onopen=()=>{
        if(socket!==next)return
        reconnectAttempt=0
        setConnectionState('connected')
        if(!reconnecting)reportStartup('socket_open')
        scheduleFit()
        claimInput()
        if(!reconnecting)term.focus()
      }
      next.onmessage=event=>{if(socket===next)handleMessage(event)}
      next.onclose=()=>{if(socket!==next)return;socket=null;scheduleReconnect()}
      next.onerror=()=>{if(socket===next)next.close()}
    }
    const reconnect=()=>{
      if(disposed||['exited','crashed'].includes(stateRef.current))return
      if(socket){socket.onclose=null;socket.close();socket=null}
      connect(true)
    }
    const input = term.onData(data => {
      const replayResponse = replaying && replayAllowsTerminalResponses && isTerminalProtocolResponse(data)
      if ((!replaying || replayResponse) && socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'input', data, broadcast: replayResponse ? false : broadcastRef.current }))
      }
    })
    let longPress: number | null = null
    let touch:{pointerId:number;lastY:number;startY:number}|null=null
    const cancelLongPress = () => { if (longPress !== null) window.clearTimeout(longPress); longPress = null }
    const pointerClaim = (event: PointerEvent) => {
      claimInput()
      if (event.pointerType === 'touch') {
        touch={pointerId:event.pointerId,lastY:event.clientY,startY:event.clientY}
        cancelLongPress()
        if(mobileInput.longPress==='context_menu')longPress = window.setTimeout(() => {
            navigator.vibrate?.(20)
            setMenu({x:event.clientX,y:event.clientY})
            longPress = null
          },550)
      }
    }
    const pointerMove=(event:PointerEvent)=>{
      if(event.pointerType!=='touch'||!touch||event.pointerId!==touch.pointerId)return
      if(Math.abs(event.clientY-touch.startY)>8)cancelLongPress()
      const delta=touchWheelDelta(touch.lastY,event.clientY,mobileInput)
      touch.lastY=event.clientY
      if(Math.abs(delta)<1)return
      const mouseActive=term.modes.mouseTrackingMode!=='none'
      const dragTarget=mobileDragTarget(mobileInput.verticalDrag,mouseActive)
      if(dragTarget==='disabled')return
      event.preventDefault()
      if(dragTarget==='terminal'){
        const rowHeight=(term.element?.getBoundingClientRect().height??term.rows*13)/Math.max(term.rows,1)
        term.scrollLines(terminalScrollLines(delta,rowHeight))
        return
      }
      term.element?.dispatchEvent(new WheelEvent('wheel',{
        bubbles:true,cancelable:true,clientX:event.clientX,clientY:event.clientY,
        deltaY:delta,deltaMode:WheelEvent.DOM_DELTA_PIXEL,
      }))
    }
    const pointerEnd=()=>{cancelLongPress();touch=null}
    const openMenu = (event: MouseEvent) => {
      event.preventDefault()
      setMenu({ x: event.clientX, y: event.clientY })
    }
    const pasteEvent = (event: ClipboardEvent) => {
      if (!acceptsClipboardImages(session) || !event.clipboardData) return
      const image = clipboardImage(Array.from(event.clipboardData.items))
      if (!image) return
      event.preventDefault()
      event.stopPropagation()
      void insertTerminalImage(term, session, image).catch(cause => {
        reportError(cause instanceof Error ? cause.message : 'Clipboard image paste failed.')
      })
    }
    const hasFiles = (event: DragEvent) => Array.from(event.dataTransfer?.types || []).includes('Files')
    const dragEnter = (event: DragEvent) => {
      if (!hasFiles(event)) return
      event.preventDefault()
      if (acceptsClipboardImages(session) && event.dataTransfer && hasTerminalImage(Array.from(event.dataTransfer.items))) {
        setImageDropActive(true)
      }
    }
    const dragOver = (event: DragEvent) => {
      if (!hasFiles(event)) return
      event.preventDefault()
      if (event.dataTransfer) {
        event.dataTransfer.dropEffect = acceptsClipboardImages(session) && hasTerminalImage(Array.from(event.dataTransfer.items)) ? 'copy' : 'none'
      }
    }
    const dragLeave = (event: DragEvent) => {
      const next = event.relatedTarget
      if (next instanceof Node && host.current?.contains(next)) return
      setImageDropActive(false)
    }
    const drop = (event: DragEvent) => {
      if (!hasFiles(event)) return
      event.preventDefault()
      event.stopPropagation()
      setImageDropActive(false)
      if (!acceptsClipboardImages(session)) {
        reportError('Drop images into an open Claude or Codex session.')
        return
      }
      const transfer = event.dataTransfer
      const image = transfer && (clipboardImage(Array.from(transfer.items)) || Array.from(transfer.files).find(isTerminalImage))
      if (!image) {
        reportError('Supported image types are PNG, JPEG, WebP, and GIF.')
        return
      }
      void insertTerminalImage(term, session, image).catch(cause => {
        reportError(cause instanceof Error ? cause.message : 'Image drop failed.')
      })
    }
    host.current.addEventListener('pointerdown', pointerClaim)
    host.current.addEventListener('pointerup', pointerEnd)
    host.current.addEventListener('pointermove', pointerMove)
    host.current.addEventListener('pointercancel', pointerEnd)
    host.current.addEventListener('focusin', claimInput)
    host.current.addEventListener('contextmenu', openMenu)
    host.current.addEventListener('paste', pasteEvent, true)
    host.current.addEventListener('dragenter', dragEnter)
    host.current.addEventListener('dragover', dragOver)
    host.current.addEventListener('dragleave', dragLeave)
    host.current.addEventListener('drop', drop)
    const observer = new ResizeObserver(scheduleFit)
    observer.observe(host.current)
    const intersection = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) scheduleFullRedraw()
    })
    intersection?.observe(host.current)
    window.addEventListener('resize', scheduleFit)
    const onVisibility=()=>{
      if(document.hidden){hiddenAt=Date.now();return}
      const slept=hiddenAt!==null&&Date.now()-hiddenAt>5000
      hiddenAt=null
      if(slept||!socket||socket.readyState!==WebSocket.OPEN)reconnect()
      scheduleFullRedraw()
    }
    const onPageShow=(event:PageTransitionEvent)=>{if(event.persisted)reconnect();scheduleFullRedraw()}
    const onWindowFocus=()=>scheduleFullRedraw()
    const onOnline=()=>{if(!socket||socket.readyState!==WebSocket.OPEN)reconnect()}
    document.addEventListener('visibilitychange',onVisibility)
    window.addEventListener('pageshow',onPageShow)
    window.addEventListener('focus',onWindowFocus)
    window.addEventListener('online',onOnline)
    window.visualViewport?.addEventListener('resize',scheduleFit)
    connect(false)
    return () => { disposed=true;if(reconnectTimer!==undefined)clearTimeout(reconnectTimer);input.dispose();cancelLongPress();observer.disconnect();intersection?.disconnect();window.cancelAnimationFrame(fitFrame);window.cancelAnimationFrame(redrawFrame);window.removeEventListener('resize',scheduleFit);window.visualViewport?.removeEventListener('resize',scheduleFit);document.removeEventListener('visibilitychange',onVisibility);window.removeEventListener('pageshow',onPageShow);window.removeEventListener('focus',onWindowFocus);window.removeEventListener('online',onOnline);window.removeEventListener('mux:theme',onTheme);host.current?.removeEventListener('pointerdown',pointerClaim);host.current?.removeEventListener('pointerup',pointerEnd);host.current?.removeEventListener('pointermove',pointerMove);host.current?.removeEventListener('pointercancel',pointerEnd);host.current?.removeEventListener('focusin',claimInput);host.current?.removeEventListener('contextmenu',openMenu);host.current?.removeEventListener('paste',pasteEvent,true);host.current?.removeEventListener('dragenter',dragEnter);host.current?.removeEventListener('dragover',dragOver);host.current?.removeEventListener('dragleave',dragLeave);host.current?.removeEventListener('drop',drop);if(socket){socket.onclose=null;socket.close()}term.dispose();termRef.current=null;searchRef.current=null }
  }, [session.id, keybindings, scrollback, mobileInput])

  const copy = () => {
    const term = termRef.current
    if (!term?.hasSelection()) { reportError('Copy requires a terminal selection.'); setMenu(null); return }
    void navigator.clipboard.writeText(term.getSelection()).catch(() => runCommand('clipboard.help'))
    term?.clearSelection(); setMenu(null)
  }
  const paste = () => {
    const term = termRef.current
    if (term) void pasteBrowserClipboard(term, session).catch(() => runCommand('clipboard.help'))
    setMenu(null)
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

  const retryPreparedCopy=async()=>{
    if(!preparedClipboard)return
    if(await copyPreparedText(preparedClipboard,manualClipboardRef.current)){
      setPreparedClipboard('');setManualClipboard(false);return
    }
    setManualClipboard(true)
    requestAnimationFrame(()=>{manualClipboardRef.current?.focus();manualClipboardRef.current?.select()})
  }

  return <div class="terminal-surface"><div class="terminal-host" ref={host} />{imageDropActive&&<div class="terminal-image-drop" role="status">Drop image to attach to {session.backend}</div>}{findOpen && <div class="terminal-find" role="search">
    <input value={findQuery} onInput={event => { setFindQuery(event.currentTarget.value); setFindResult('') }} onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeFind() }
      if (event.key === 'Enter') { event.preventDefault(); search(event.shiftKey) }
    }} placeholder="find in terminal" aria-label="Find in terminal" autofocus />
    <button title="Previous match" onClick={() => search(true)}>↑</button>
    <button title="Next match" onClick={() => search(false)}>↓</button>
    <button class={findCase ? 'active' : ''} title="Match case" aria-pressed={findCase} onClick={() => { setFindCase(value => !value); setFindResult('') }}>Aa</button>
    <span class={findResult === 'no match' ? 'missing' : ''}>{findResult}</span>
    <button title="Close find" onClick={closeFind}>×</button>
  </div>}{connectionState!=='connected'&&<div class={`terminal-connection ${connectionState}`} role="status">{connectionState==='ended'?'session ended':connectionState==='connecting'?'connecting…':'reconnecting…'}</div>}{preparedClipboard&&<div class="prepared-clipboard" role="status"><span>Terminal prepared copied text.</span><button onClick={()=>void retryPreparedCopy()}>Copy now</button><button aria-label="Dismiss prepared clipboard" onClick={()=>{setPreparedClipboard('');setManualClipboard(false)}}>×</button><textarea ref={manualClipboardRef} class={manualClipboard?'manual':''} readOnly value={preparedClipboard} aria-label="Prepared terminal clipboard text" onFocus={event=>event.currentTarget.select()} /></div>}{menu && <div class="terminal-menu" role="menu" style={{ left: clampContextMenuLeft(menu.x, innerWidth), top: Math.min(menu.y, innerHeight - 230) }}>
    <button role="menuitem" disabled={!termRef.current?.hasSelection()} onClick={() => runCommand('terminal.copy')}>Copy</button>
    <button role="menuitem" onClick={() => runCommand('terminal.paste')}>Paste</button>
    <button role="menuitem" onClick={() => runCommand('terminal.selectAll')}>Select all</button>
    <button role="menuitem" onClick={() => runCommand('terminal.find')}>Find…</button>
    <button role="menuitem" onClick={() => runCommand('terminal.clear')}>Clear display</button>
    {(session.backend==='claude'||session.backend==='codex')&&<button role="menuitem" onClick={() => runCommand('session.notes')}>Agent-run note…</button>}
    {(session.backend==='claude'||session.backend==='codex')&&<button role="menuitem" onClick={() => runCommand('session.notesSplit')}>Agent-run note in split</button>}
    <button role="menuitem" onClick={() => runCommand('session.projectNote')}>{session.backend==='shell'?'Current':'Run'} project note…</button>
    <button role="menuitem" onClick={() => runCommand('processes.open')}>Processes and previews…</button>
    <div class="context-rule" />
    <button role="menuitem" onClick={() => runCommand('pane.splitHorizontal')}>Split right</button>
    <button role="menuitem" onClick={() => runCommand('pane.splitVertical')}>Split below</button>
    <button role="menuitem" onClick={() => runCommand('pane.detach')}>Detach pane</button>
    <button role="menuitem" onClick={() => runCommand('pane.zoom')}>Zoom pane</button>
    <button role="menuitem" class="danger" onClick={() => runCommand('session.kill')}>{session.state === 'exited' || session.state === 'crashed' ? 'Remove from sidebar' : 'Kill session'}</button>
  </div>}</div>
}

// The terminal body is imperative (xterm owns the DOM), so the component only needs to
// re-render for the handful of session fields its JSX/effects read. Skipping re-renders on
// high-frequency telemetry (context_pct, tokens, model, cwd) avoids diffing every pane and
// the sidebar on every PTY status frame. Callback props (onState/onStartupTiming) are
// intentionally excluded: they are captured by mount-time effects keyed on session.id.
export const TerminalPane = memo(TerminalPaneImpl, (a, b) =>
  a.session.id === b.session.id &&
  a.session.backend === b.session.backend &&
  a.session.state === b.session.state &&
  a.broadcast === b.broadcast &&
  a.scrollback === b.scrollback &&
  a.keybindings === b.keybindings &&
  a.mobileInput === b.mobileInput,
)
