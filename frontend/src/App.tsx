import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { TerminalPane } from './TerminalPane'
import type { Session, Space } from './types'
import { keyChord } from './keys'

const stateRank: Record<string, number> = {
  awaiting: 0, crashed: 1, exited: 2, working: 3, starting: 4, running: 5, idle: 6,
}

function isAgent(session: Session) {
  return session.backend === 'claude' || session.backend === 'codex'
}

function sessionStatus(session: Session): string {
  if (!isAgent(session)) return session.state
  const context = session.context_pct > 0 ? ` · ctx used ${Math.round(session.context_pct * 100)}%` : ''
  if (session.state === 'working') return `working${session.state_detail ? ` · ${session.state_detail}` : ''}${context}`
  if (session.state === 'idle') return `ready · turn complete${context}`
  if (session.state === 'awaiting') return `awaiting approval${session.state_detail ? ` · ${session.state_detail}` : ''}${context}`
  if (session.state === 'starting') return 'starting agent…'
  return `${session.state}${context}`
}

type Command = { id: string; label: string; hint?: string; run: () => void }
type HistoryEntry = {
  id: string; native_id: string; backend: string; name: string; cwd: string
  spawned_at: number; exited_at?: number; exit_reason?: string; transcript_path?: string
}
type Transcript = { entry: HistoryEntry; messages: Array<{ role: string; content: Array<{ type: string; text?: string; name?: string; input?: unknown }> }> }
type ContextState = { session: Session; x: number; y: number } | null
type SpaceContext = { space: Space; x: number; y: number } | null
type RenameTarget = { kind: 'session'; session: Session } | { kind: 'space'; space: Space }
type Worktree = { worktree: string; HEAD?: string; branch?: string; bare?: boolean; detached?: boolean }
type WorktreeState = { session: Session; items: Worktree[] } | null

export function App() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [spaces, setSpaces] = useState<Space[]>([])
  const [spaceId, setSpaceId] = useState('default')
  const [activeId, setActiveId] = useState<string | null>(null)
  const [paneMap, setPaneMap] = useState<Record<string, string[]>>({})
  const [broadcast, setBroadcast] = useState(false)
  const [launcherOpen, setLauncherOpen] = useState(false)
  const [launcherSpace, setLauncherSpace] = useState('default')
  const [cwd, setCwd] = useState('')
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [paletteQuery, setPaletteQuery] = useState('')
  const [error, setError] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [transcript, setTranscript] = useState<Transcript | null>(null)
  const [contextMenu, setContextMenu] = useState<ContextState>(null)
  const [spaceMenu, setSpaceMenu] = useState<SpaceContext>(null)
  const [zoomedId, setZoomedId] = useState<string | null>(null)
  const [keybindings, setKeybindings] = useState<Record<string, string>>({ 'ctrl+alt+t': 'session.spawnShell', 'ctrl+shift+p': 'palette.open' })
  const [confirmKillId, setConfirmKillId] = useState<string | null>(null)
  const [confirmSpaceDeleteId, setConfirmSpaceDeleteId] = useState<string | null>(null)
  const [mainMenuOpen, setMainMenuOpen] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [worktrees, setWorktrees] = useState<WorktreeState>(null)
  const [confirmWorktreeRemove, setConfirmWorktreeRemove] = useState<string | null>(null)
  const [renameTarget, setRenameTarget] = useState<RenameTarget | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const spawning = useRef(false)

  const refresh = async () => {
    try {
      const [nextSessions, nextSpaces] = await Promise.all([
        api<Session[]>('GET', '/api/sessions'), api<Space[]>('GET', '/api/spaces'),
      ])
      setSessions(nextSessions)
      setSpaces(nextSpaces)
      setPaneMap(current => {
        const next = { ...current }
        for (const space of nextSpaces) {
          if (!(space.id in next)) {
            const layout = space.layout as { panes?: string[] } | null
            next[space.id] = (layout?.panes || []).filter(id => nextSessions.some(session => session.id === id && !['exited', 'crashed'].includes(session.state)))
          }
        }
        return next
      })
      setError('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  useEffect(() => {
    void refresh()
    const loadKeys = () => void api<{ bindings: Record<string, string> }>('GET', '/api/keybindings').then(result => setKeybindings(current => JSON.stringify(current) === JSON.stringify(result.bindings) ? current : result.bindings))
    loadKeys()
    const timer = setInterval(refresh, 2500)
    const keyTimer = setInterval(loadKeys, 5000)
    return () => { clearInterval(timer); clearInterval(keyTimer) }
  }, [])

  useEffect(() => {
    let socket: WebSocket | null = null
    let retry: number | undefined
    const connect = () => {
      const url = new URL('/events', location.href)
      url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
      const token = localStorage.getItem('mux.token')
      if (token) url.searchParams.set('token', token)
      socket = new WebSocket(url)
      socket.onmessage = () => void refresh()
      socket.onclose = () => { retry = window.setTimeout(connect, 1500) }
    }
    connect()
    return () => { if (retry) clearTimeout(retry); socket?.close() }
  }, [])

  const active = sessions.find(session => session.id === activeId)
  const attention = sessions.filter(session => session.state === 'awaiting').length
  const activeSpace = spaces.find(space => space.id === spaceId)
  const paneIds = (paneMap[spaceId] || []).filter(id => sessions.some(session => session.id === id && !['exited', 'crashed'].includes(session.state)))
  const rememberedCwds = useMemo(() => {
    const stored = JSON.parse(localStorage.getItem('mux.recentCwds') || '[]') as string[]
    return [...new Set([active?.cwd, ...sessions.map(session => session.cwd), ...stored].filter(Boolean))] as string[]
  }, [sessions, active?.cwd])

  useEffect(() => {
    document.title = `${attention ? `(${attention}) ` : ''}swe-mux`
  }, [attention])

  useEffect(() => {
    if (!contextMenu && !spaceMenu && !mainMenuOpen && !renameTarget) return
    const dismissEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      setContextMenu(null)
      setSpaceMenu(null)
      setMainMenuOpen(false)
      setRenameTarget(null)
    }
    window.addEventListener('keydown', dismissEscape, true)
    return () => window.removeEventListener('keydown', dismissEscape, true)
  }, [contextMenu, spaceMenu, mainMenuOpen, renameTarget])

  useEffect(() => {
    if (!confirmKillId) return
    const timer = window.setTimeout(() => {
      setConfirmKillId(current => current === confirmKillId ? null : current)
    }, 2000)
    return () => window.clearTimeout(timer)
  }, [confirmKillId])

  const rememberCwd = (value: string) => {
    const next = [value, ...rememberedCwds.filter(item => item !== value)].slice(0, 8)
    localStorage.setItem('mux.recentCwds', JSON.stringify(next))
  }

  const spawnTerminal = async (targetSpace = spaceId, targetCwd?: string, split = false) => {
    if (spawning.current) return
    spawning.current = true
    try {
      const resolvedCwd = targetCwd || active?.cwd || rememberedCwds[0]
      const next = await api<Session>('POST', '/api/sessions', {
        backend: 'shell', space: targetSpace, cwd: resolvedCwd || undefined,
      })
      if (resolvedCwd) rememberCwd(resolvedCwd)
      setSessions(items => [...items, next])
      setSpaceId(targetSpace)
      setActiveId(next.id)
      const currentPanes = paneMap[targetSpace] || []
      const focusedIndex = targetSpace === spaceId && activeId ? currentPanes.indexOf(activeId) : -1
      const nextPanes = split
        ? [...currentPanes, next.id]
        : currentPanes.length
          ? currentPanes.map((id, index) => index === (focusedIndex >= 0 ? focusedIndex : 0) ? next.id : id)
          : [next.id]
      await updatePanes(targetSpace, nextPanes)
      setLauncherOpen(false)
      setCwd('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      spawning.current = false
    }
  }

  const openLauncher = (targetSpace = spaceId) => {
    setLauncherSpace(targetSpace)
    setCwd(active?.cwd || rememberedCwds[0] || '')
    setLauncherOpen(true)
  }

  const createSpace = async () => {
    const next = await api<Space>('POST', '/api/spaces', { name: `Space ${spaces.length + 1}` })
    setSpaces(items => [...items, next])
    setSpaceId(next.id)
  }

  const openRename = (target: RenameTarget) => {
    setContextMenu(null)
    setSpaceMenu(null)
    setRenameTarget(target)
    setRenameValue(target.kind === 'session' ? target.session.name : target.space.name)
  }

  const submitRename = async () => {
    if (!renameTarget) return
    const name = renameValue.trim()
    const currentName = renameTarget.kind === 'session' ? renameTarget.session.name : renameTarget.space.name
    if (!name || name === currentName) {
      setRenameTarget(null)
      return
    }
    try {
      if (renameTarget.kind === 'session') {
        const updated = await api<Session>('PATCH', `/api/sessions/${renameTarget.session.id}`, { name })
        updateSession(updated)
      } else {
        const updated = await api<Space>('PATCH', `/api/spaces/${renameTarget.space.id}`, { name })
        setSpaces(items => items.map(item => item.id === updated.id ? updated : item))
      }
      setRenameTarget(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  const deleteSpace = async (space: Space) => {
    await api('DELETE', `/api/spaces/${space.id}`)
    setSpaces(items => items.filter(item => item.id !== space.id))
    setConfirmSpaceDeleteId(null)
    if (spaceId === space.id) setSpaceId('default')
  }

  const killNow = async (session: Session) => {
    await api('DELETE', `/api/sessions/${session.id}`)
    setConfirmKillId(null)
    if (activeId === session.id) setActiveId(null)
    await updatePanes(session.space_id, (paneMap[session.space_id] || []).filter(id => id !== session.id))
    await refresh()
  }

  const requestKill = (session: Session) => {
    if (confirmKillId === session.id) void killNow(session)
    else setConfirmKillId(session.id)
  }

  const updatePanes = async (targetSpace: string, ids: string[]) => {
    const unique = [...new Set(ids)].slice(0, 4)
    setPaneMap(current => ({ ...current, [targetSpace]: unique }))
    const updated = await api<Space>('PATCH', `/api/spaces/${targetSpace}`, { layout: { panes: unique } })
    setSpaces(items => items.map(item => item.id === updated.id ? updated : item))
  }

  const selectSession = async (session: Session) => {
    const current = paneMap[session.space_id] || []
    const focusedIndex = activeId ? current.indexOf(activeId) : -1
    const next = current.length === 0
      ? [session.id]
      : current.map((id, index) => index === (focusedIndex >= 0 ? focusedIndex : 0) ? session.id : id)
    setSpaceId(session.space_id)
    setActiveId(session.id)
    setSidebarOpen(false)
    await updatePanes(session.space_id, next)
  }

  const openInSplit = async (session: Session) => {
    setSpaceId(session.space_id)
    setActiveId(session.id)
    await updatePanes(session.space_id, [...(paneMap[session.space_id] || []), session.id])
    setContextMenu(null)
  }

  const showHistory = async () => {
    setHistory(await api<HistoryEntry[]>('GET', '/api/history'))
    setTranscript(null)
    setHistoryOpen(true)
  }

  const move = async (session: Session, target: string) => {
    const updated = await api<Session>('PATCH', `/api/sessions/${session.id}`, { space: target })
    updateSession(updated)
    setContextMenu(null)
  }

  const createWorktree = async (session: Session) => {
    const branch = prompt('New worktree branch')?.trim()
    if (!branch) return
    const suggested = `${session.cwd}-${branch.replace(/[^a-z0-9._-]+/gi, '-')}`
    const path = prompt('Worktree directory', suggested)?.trim()
    if (!path) return
    await api('POST', '/api/git/worktrees', { cwd: session.cwd, path, branch })
    await spawnTerminal(session.space_id, path)
  }

  const manageWorktrees = async (session: Session) => {
    const items = await api<Worktree[]>('GET', `/api/git/worktrees?cwd=${encodeURIComponent(session.cwd)}`)
    setContextMenu(null)
    setWorktrees({ session, items })
  }

  const removeWorktree = async (path: string) => {
    if (!worktrees) return
    if (confirmWorktreeRemove !== path) {
      setConfirmWorktreeRemove(path)
      return
    }
    await api('DELETE', '/api/git/worktrees', { cwd: worktrees.session.cwd, path })
    const items = worktrees.items.filter(item => item.worktree !== path)
    setWorktrees({ ...worktrees, items })
    setConfirmWorktreeRemove(null)
  }

  const commands: Command[] = [
    { id: 'palette.open', label: 'Open command palette', hint: 'Ctrl Shift P', run: () => setPaletteOpen(true) },
    { id: 'session.spawnShell', label: 'New terminal here', hint: 'Ctrl Alt T', run: () => void spawnTerminal() },
    { id: 'session.quickLaunch', label: 'New terminal in directory…', run: () => openLauncher() },
    { id: 'space.create', label: 'Create space', run: () => void createSpace() },
    { id: 'history.open', label: 'Browse session history', run: () => void showHistory() },
    { id: 'pane.detach', label: 'Detach focused pane', run: () => active && void updatePanes(spaceId, paneIds.filter(id => id !== active.id)) },
    { id: 'pane.zoom', label: zoomedId ? 'Restore pane layout' : 'Zoom focused pane', run: () => setZoomedId(zoomedId ? null : activeId) },
    { id: 'pane.next', label: 'Focus next pane', run: () => focusRelativePane(1) },
    { id: 'pane.previous', label: 'Focus previous pane', run: () => focusRelativePane(-1) },
    { id: 'broadcast.toggle', label: broadcast ? 'Stop broadcasting input' : 'Start broadcasting input', run: () => setBroadcast(value => !value) },
  ]
  const shownCommands = commands.filter(command => command.label.toLowerCase().includes(paletteQuery.toLowerCase()))

  function focusRelativePane(offset: number) {
    if (!paneIds.length) return
    const current = activeId ? paneIds.indexOf(activeId) : -1
    setActiveId(paneIds[(Math.max(current, 0) + offset + paneIds.length) % paneIds.length])
  }

  const runNamedCommand = (command: string): boolean => {
    const direct = commands.find(item => item.id === command)
    if (direct) { direct.run(); return true }
    const match = /^space\.activate\((\d)\)$/.exec(command)
    if (match) {
      const space = spaces[Number(match[1]) - 1]
      if (space) { setSpaceId(space.id); setActiveId((paneMap[space.id] || [])[0] || null) }
      return true
    }
    return false
  }

  useEffect(() => {
    const onCommand = (event: Event) => {
      const command = (event as CustomEvent<string>).detail
      if (command === 'clipboard.help') setError('Clipboard access was blocked by the browser. Use the terminal context menu or allow clipboard access for this site.')
      else runNamedCommand(command)
    }
    const onKey = (event: KeyboardEvent) => {
      const command = keybindings[keyChord(event)]
      if (command && runNamedCommand(command)) event.preventDefault()
      if (event.key === 'Escape') {
        setPaletteOpen(false); setLauncherOpen(false); setContextMenu(null); setSpaceMenu(null); setMainMenuOpen(false); setWorktrees(null); setSidebarOpen(false); setRenameTarget(null)
      }
    }
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target
      if (target instanceof Element && target.closest('.context-menu,.menu-trigger')) return
      setContextMenu(null)
      setSpaceMenu(null)
      setMainMenuOpen(false)
    }
    window.addEventListener('mux:command', onCommand)
    window.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', onPointerDown)
    return () => {
      window.removeEventListener('mux:command', onCommand)
      window.removeEventListener('keydown', onKey)
      document.removeEventListener('pointerdown', onPointerDown)
    }
  })

  const updateSession = (next: Session) => setSessions(items => items.map(item => item.id === next.id ? next : item))

  return <div class="app-shell">
    <header class="topbar">
      <div class="brand"><button class="nav-toggle" onClick={() => setSidebarOpen(value => !value)}>:nav</button><span class="brand-mark">&gt;_</span><span>swe-mux</span><span class="dev-chip">[local]</span></div>
      <div class="top-context"><span>{activeSpace?.name || 'Main'}</span></div>
      <div class="top-actions">
        <span class="daemon-ok" title="daemon::connected" aria-label="daemon connected"><i aria-hidden="true" /></span>
      </div>
    </header>

    {broadcast && <div class="broadcast-banner"><strong>Broadcast input is on</strong><span>Keystrokes mirror to sessions in the broadcast set.</span><button onClick={() => setBroadcast(false)}>Stop broadcasting</button></div>}

    <div class="workspace">
      <aside class={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <div class="sidebar-heading"><span>~/WORKSPACES</span></div>
        <div class="space-tree">
          {spaces.map(space => {
            const children = sessions
              .filter(session => session.space_id === space.id)
              .sort((a, b) => (stateRank[a.state] ?? 9) - (stateRank[b.state] ?? 9) || a.name.localeCompare(b.name))
            return <section class={`space-group ${space.id === spaceId ? 'active' : ''}`}>
              <div class="space-row" onContextMenu={event => { event.preventDefault(); setSpaceMenu({ space, x: event.clientX, y: event.clientY }) }} onClick={() => setSpaceId(space.id)}>
                <span class="space-chevron">{space.id === spaceId ? '▾' : '·'}</span><strong>{space.name}</strong><small>{children.filter(s => !['exited', 'crashed'].includes(s.state)).length}</small>
              </div>
              <div class="session-list">
                {children.map(session => <button class={`session-row ${activeId === session.id ? 'active' : ''} ${session.state}`} onContextMenu={event => { event.preventDefault(); setContextMenu({ session, x: event.clientX, y: event.clientY }) }} onClick={() => void selectSession(session)}>
                  <span class={`state-dot ${session.state}`} />
                  <span class="session-copy"><strong>{isAgent(session) && <span class={`agent-prefix ${session.backend}`}>[{session.backend}]</span>}{session.name}</strong><small class={isAgent(session) ? `agent-status ${session.state}` : ''}>{sessionStatus(session)}</small></span>
                  <span class="session-meta">{isAgent(session) && <em class={`state-label ${session.state}`}>{session.state === 'idle' ? 'ready' : session.state}</em>}</span>
                  <span class="row-actions" onClick={event => event.stopPropagation()}><button class={confirmKillId === session.id ? 'confirming' : ''} title={confirmKillId === session.id ? 'Confirm kill' : 'Kill'} onClick={() => requestKill(session)}>{confirmKillId === session.id ? '✓' : '×'}</button></span>
                </button>)}
              </div>
            </section>
          })}
        </div>
        <div class="sidebar-footer"><button class="menu-trigger" onClick={() => setMainMenuOpen(value => !value)}><span>:</span> menu</button></div>
      </aside>

      <main class="main-stage">
        {paneIds.length ? <div class={`pane-grid count-${paneIds.length} ${zoomedId ? 'zoomed' : ''}`}>{paneIds.map(id => {
          const session = sessions.find(item => item.id === id)
          if (!session) return null
          return <section class={`terminal-pane ${activeId === id ? 'focused' : ''} ${zoomedId === id ? 'zoom-target' : ''}`} onPointerDown={() => setActiveId(id)}>
            <div class="pane-bar" onDblClick={() => setZoomedId(current => current === id ? null : id)}>
              <div><span class={`state-dot ${session.state}`} /><strong>{isAgent(session) && <span class={`agent-prefix ${session.backend}`}>[{session.backend}]</span>}{session.name}</strong><span class={`pane-state ${session.state}`}>{sessionStatus(session)}</span>{session.git.branch && <span class="git-chip" title={`${session.git.branch}${session.git.dirty ? ` · ${session.git.dirty} changed` : ''}`}>{session.git.branch}{session.git.dirty ? ` ±${session.git.dirty}` : ''}</span>}</div>
              <div class="pane-path" title={session.cwd}>{session.cwd}</div>
              <div class="pane-tools"><span>PID {session.pid}</span><button title="Detach pane" onClick={() => void updatePanes(spaceId, paneIds.filter(paneId => paneId !== id))}>—</button><button class={confirmKillId === session.id ? 'confirming' : ''} title={confirmKillId === session.id ? 'Confirm kill' : 'Kill session'} onClick={() => requestKill(session)}>{confirmKillId === session.id ? '✓' : '×'}</button></div>
            </div>
            <TerminalPane session={session} onState={updateSession} broadcast={broadcast} keybindings={keybindings} />
          </section>
        })}</div> : <div class="empty-stage">
          <div class="hero-terminal" aria-hidden="true">&gt;_</div>
          <h1>Terminals that stay with you.</h1>
          <p>right-click {activeSpace?.name || 'a workspace'} or open : menu</p>
        </div>}
      </main>
    </div>

    {launcherOpen && <div class="quick-launcher">
      <div class="quick-heading"><span>NEW TERMINAL IN {spaces.find(space => space.id === launcherSpace)?.name?.toUpperCase()}</span><button onClick={() => setLauncherOpen(false)}>×</button></div>
      <form onSubmit={event => { event.preventDefault(); void spawnTerminal(launcherSpace, cwd) }}>
        <label>Working directory<input value={cwd} onInput={event => setCwd(event.currentTarget.value)} placeholder="D:\\projects\\my-project" autofocus /></label>
        <button class="primary" type="submit">Open terminal</button>
      </form>
      {rememberedCwds.length > 0 && <div class="recent-cwds"><span>RECENT & ACTIVE</span>{rememberedCwds.map(item => <button title={item} onClick={() => void spawnTerminal(launcherSpace, item)}><strong>{item.split(/[\\/]/).filter(Boolean).pop()}</strong><small>{item}</small></button>)}</div>}
    </div>}

    {paletteOpen && <div class="palette-layer" onMouseDown={event => event.target === event.currentTarget && setPaletteOpen(false)}>
      <div class="palette"><input value={paletteQuery} onInput={event => setPaletteQuery(event.currentTarget.value)} onKeyDown={event => { if (event.key === 'Escape') setPaletteOpen(false) }} placeholder="Type a command…" autofocus />
        <div>{shownCommands.map(command => <button onClick={() => { setPaletteOpen(false); setPaletteQuery(''); command.run() }}><span>{command.label}</span>{command.hint && <kbd>{command.hint}</kbd>}</button>)}</div>
      </div>
    </div>}

    {contextMenu && <div class="context-menu" style={{ left: Math.min(contextMenu.x, innerWidth - 205), top: Math.min(contextMenu.y, innerHeight - 330) }}>
      <div class="context-title"><span class={`state-dot ${contextMenu.session.state}`} /><strong>{contextMenu.session.name}</strong></div>
      <button onClick={() => openRename({ kind: 'session', session: contextMenu.session })}>Rename</button>
      <button onClick={() => { void navigator.clipboard.writeText(contextMenu.session.id); setContextMenu(null) }}>Copy session ID</button>
      <button onClick={() => { void navigator.clipboard.writeText(contextMenu.session.cwd); setContextMenu(null) }}>Copy working directory</button>
      <button onClick={() => void openInSplit(contextMenu.session)}>Open in split</button>
      <button onClick={() => { void api('POST', '/api/reveal', { path: contextMenu.session.cwd }); setContextMenu(null) }}>Reveal in Explorer</button>
      <button onClick={() => { const target = contextMenu.session; setContextMenu(null); void createWorktree(target) }}>Create worktree + terminal…</button>
      <button onClick={() => void manageWorktrees(contextMenu.session)}>Manage worktrees…</button>
      <button onClick={() => { const target = contextMenu.session; setContextMenu(null); void spawnTerminal(target.space_id, target.cwd, true) }}>New terminal in split</button>
      <div class="context-subtitle">MOVE TO SPACE</div>
      {spaces.map(space => <button disabled={space.id === contextMenu.session.space_id} onClick={() => void move(contextMenu.session, space.id)}>{space.name}</button>)}
      <div class="context-rule" />
      <button onClick={async () => { const updated = await api<Session>('POST', `/api/sessions/${contextMenu.session.id}/broadcast-set`, { include: !contextMenu.session.broadcast }); updateSession(updated); setContextMenu(null) }}>{contextMenu.session.broadcast ? 'Remove from broadcast' : 'Add to broadcast'}</button>
      <button class="danger" onClick={() => { const target = contextMenu.session; setContextMenu(null); void killNow(target) }}>Kill session</button>
    </div>}

    {spaceMenu && <div class="context-menu" style={{ left: Math.min(spaceMenu.x, innerWidth - 205), top: Math.min(spaceMenu.y, innerHeight - 190) }}>
      <div class="context-title"><strong>{spaceMenu.space.name}</strong></div>
      <button onClick={() => { const target = spaceMenu.space.id; setSpaceMenu(null); void spawnTerminal(target) }}>New terminal</button>
      <button onClick={() => { const target = spaceMenu.space.id; setSpaceMenu(null); openLauncher(target) }}>New terminal in directory…</button>
      <button onClick={() => openRename({ kind: 'space', space: spaceMenu.space })}>Rename space</button>
      {spaceMenu.space.id !== 'default' && <button class={`danger ${confirmSpaceDeleteId === spaceMenu.space.id ? 'confirming' : ''}`} onClick={() => { const target = spaceMenu.space; if (confirmSpaceDeleteId === target.id) { setSpaceMenu(null); void deleteSpace(target) } else setConfirmSpaceDeleteId(target.id) }}>{confirmSpaceDeleteId === spaceMenu.space.id ? 'Click again to delete' : 'Delete space'}</button>}
    </div>}

    {mainMenuOpen && <div class="context-menu main-menu">
      <div class="context-title"><strong>swe-mux menu</strong></div>
      <button onClick={() => { setMainMenuOpen(false); void spawnTerminal() }}>New terminal in current space</button>
      <button onClick={() => { setMainMenuOpen(false); openLauncher() }}>New terminal in directory…</button>
      <button onClick={() => { setMainMenuOpen(false); void showHistory() }}>Session history</button>
      <button onClick={() => { setMainMenuOpen(false); void createSpace() }}>Create workspace</button>
      <button onClick={() => { setMainMenuOpen(false); setBroadcast(value => !value) }}>{broadcast ? 'Stop broadcast input' : 'Start broadcast input'}</button>
      <div class="context-subtitle">SHORTCUTS</div>
      <button onClick={() => { setMainMenuOpen(false); setPaletteOpen(true) }}>Command palette <span class="menu-hint">ctrl shift p</span></button>
    </div>}

    {sidebarOpen && <button class="sidebar-scrim" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />}

    {renameTarget && <div class="modal-layer" onMouseDown={event => event.target === event.currentTarget && setRenameTarget(null)}>
      <form class="modal rename-modal" onSubmit={event => { event.preventDefault(); void submitRename() }}>
        <div class="modal-heading"><div><span>RENAME::{renameTarget.kind.toUpperCase()}</span><h2>{renameTarget.kind === 'session' ? renameTarget.session.name : renameTarget.space.name}</h2></div><button type="button" aria-label="Close rename" onClick={() => setRenameTarget(null)}>×</button></div>
        <label>name<input value={renameValue} onInput={event => setRenameValue(event.currentTarget.value)} autofocus /></label>
        <div class="modal-footer"><span>enter::save · esc::cancel</span><button type="button" onClick={() => setRenameTarget(null)}>Cancel</button><button class="primary" type="submit" disabled={!renameValue.trim()}>Rename</button></div>
      </form>
    </div>}

    {worktrees && <div class="worktree-layer" onMouseDown={event => event.target === event.currentTarget && setWorktrees(null)}>
      <section class="worktree-panel">
        <header><div><span>GIT WORKTREES</span><strong>{worktrees.session.cwd}</strong></div><button onClick={() => setWorktrees(null)}>×</button></header>
        <div class="worktree-list">{worktrees.items.length ? worktrees.items.map(item => <article>
          <div><strong>{item.worktree}</strong><span>{item.branch?.replace('refs/heads/', '') || (item.detached ? 'detached HEAD' : 'worktree')}</span></div>
          <button onClick={() => { void spawnTerminal(worktrees.session.space_id, item.worktree); setWorktrees(null) }}>Open terminal</button>
          {item.worktree.toLowerCase() !== worktrees.session.cwd.toLowerCase() && <button class={confirmWorktreeRemove === item.worktree ? 'danger confirming' : 'danger'} onClick={() => void removeWorktree(item.worktree)}>{confirmWorktreeRemove === item.worktree ? 'Remove?' : 'Remove'}</button>}
        </article>) : <div class="no-worktrees">No git worktrees found for this directory.</div>}</div>
        <footer><button onClick={() => { const target = worktrees.session; setWorktrees(null); void createWorktree(target) }}>Create worktree + terminal…</button></footer>
      </section>
    </div>}

    {historyOpen && <div class="history-layer">
      <div class="history-header"><div><span>SESSION ARCHIVE</span><h2>History</h2></div><button onClick={() => setHistoryOpen(false)}>×</button></div>
      <div class="history-body">
        <aside><div class="history-search"><input placeholder="Search history…" onInput={async event => setHistory(await api<HistoryEntry[]>('GET', `/api/history?q=${encodeURIComponent(event.currentTarget.value)}`))} /></div>
          {history.map(entry => <button class={transcript?.entry.id === entry.id ? 'active' : ''} onClick={async () => setTranscript(await api<Transcript>('GET', `/api/history/${entry.id}/transcript`))}><strong>{entry.name}</strong><span>{entry.backend} · {new Date(entry.spawned_at * 1000).toLocaleString()}</span><small>{entry.cwd}</small></button>)}
        </aside>
        <main>{transcript ? <><div class="transcript-heading"><div><h3>{transcript.entry.name}</h3><span>{transcript.entry.cwd}</span></div><button class="primary" onClick={async () => { const resumed = await api<Session>('POST', `/api/history/${transcript.entry.id}/resume`, { space: spaceId }); setSessions(items => [...items, resumed]); setActiveId(resumed.id); setHistoryOpen(false) }}>Resume as new</button></div>
          <div class="messages">{transcript.messages.length ? transcript.messages.map(message => <article class={message.role}><header>{message.role}</header>{message.content.map(block => block.type === 'text' ? <p>{block.text}</p> : <pre>{block.type === 'tool_use' ? `${block.name}\n${JSON.stringify(block.input, null, 2)}` : block.type}</pre>)}</article>) : <div class="no-transcript">No native transcript is available for this session.</div>}</div></> : <div class="history-placeholder"><span>◷</span><strong>Select a session</strong><p>Read its native transcript without resuming it.</p></div>}</main>
      </div>
    </div>}

    {error && <div class="toast" onClick={() => setError('')}>{error}<span>×</span></div>}
  </div>
}
