import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { ComponentChildren, JSX } from 'preact'
import { api, openWebSocket } from './api'
import { TerminalPane } from './TerminalPane'
import { Notes } from './Notes'
import { ProcessPanel, type Preview } from './ProcessPanel'
import { PreviewPane } from './PreviewPane'
import { Notifications, type NotificationData, type UiNotification } from './Notifications'
import { UsageDashboard } from './UsageDashboard'
import type { Session, ShellProfile, Space } from './types'
import { keyChord } from './keys'
import { Settings } from './Settings'
import { applyTheme, configureCustomTheme, type CustomTheme, type ThemeName } from './theme'
import { bindingFor, displayChord, runCommand, searchCommands, type Command } from './commands'
import {
  attachLeaf, emptyLayout, leaves, noteResourceId, parseLayout, parseNoteResourceId,
  reconcileTerminals, removeLeaf, replaceTerminal, resourceLeaf, setSplitRatio,
  splitTerminal, swapTerminals, terminalIds, type PaneLayout,
  type PaneNode, type SplitDirection,
} from './layout'

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

type HistoryEntry = {
  id: string; native_id: string; backend: string; name: string; cwd: string
  spawned_at: number; exited_at?: number; exit_reason?: string; transcript_path?: string
  project_id?: string; project_label?: string; final_state?: string; external?: number
  context_window?: number; final_context_pct?: number; peak_context_pct?: number
  tokens_in?: number; tokens_out?: number; model?: string; measurement_source?: string
}
type Transcript = { entry: HistoryEntry; messages: Array<{ role: string; content: Array<{ type: string; text?: string; name?: string; input?: unknown }> }> }
type HistoryPage = { items: HistoryEntry[]; next_cursor: string | null }
type HistoryProject = { project_id: string | null; label: string; root?: string; sessions: number; last_activity: number }
type ContextState = { session: Session; x: number; y: number } | null
type SpaceContext = { space: Space; x: number; y: number } | null
type RenameTarget = { kind: 'session'; session: Session } | { kind: 'space'; space: Space }
type Worktree = { worktree: string; HEAD?: string; branch?: string; bare?: boolean; detached?: boolean }
type WorktreeState = { session: Session; items: Worktree[] } | null
type WorktreeCreate = { session: Session; branch: string; path: string; pathEdited: boolean } | null
type NoteTarget = {cwd:string;spaceId:string;sessionId:string|null;terminalSessionId:string|null;kind:'spaces'|'sessions'}

export function App() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [spaces, setSpaces] = useState<Space[]>([])
  const [spaceId, setSpaceId] = useState('default')
  const [activeId, setActiveId] = useState<string | null>(null)
  const [layoutMap, setLayoutMap] = useState<Record<string, PaneLayout>>({})
  const [broadcast, setBroadcast] = useState(false)
  const [launcherOpen, setLauncherOpen] = useState(false)
  const [launcherSpace, setLauncherSpace] = useState('default')
  const [launcherSplit, setLauncherSplit] = useState<false | SplitDirection>(false)
  const [cwd, setCwd] = useState('')
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [paletteQuery, setPaletteQuery] = useState('')
  const [paletteIndex, setPaletteIndex] = useState(0)
  const [error, setError] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [historyProjects, setHistoryProjects] = useState<HistoryProject[]>([])
  const [historyNext, setHistoryNext] = useState<string | null>(null)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [historyQuery, setHistoryQuery] = useState('')
  const [historyBackend, setHistoryBackend] = useState('')
  const [historyProject, setHistoryProject] = useState<string | null>(null)
  const [historyState, setHistoryState] = useState('')
  const [historySpace, setHistorySpace] = useState('')
  const [historyExternal, setHistoryExternal] = useState('')
  const [historyFrom, setHistoryFrom] = useState('')
  const [historyTo, setHistoryTo] = useState('')
  const [collapsedProjects, setCollapsedProjects] = useState<Set<string>>(new Set())
  const [confirmHistoryDelete, setConfirmHistoryDelete] = useState<string | null>(null)
  const [transcript, setTranscript] = useState<Transcript | null>(null)
  const [contextMenu, setContextMenu] = useState<ContextState>(null)
  const [spaceMenu, setSpaceMenu] = useState<SpaceContext>(null)
  const [emptyMenu, setEmptyMenu] = useState<{x:number;y:number} | null>(null)
  const [zoomedId, setZoomedId] = useState<string | null>(null)
  const [keybindings, setKeybindings] = useState<Record<string, string>>({ 'ctrl+alt+t': 'session.spawnShell', 'ctrl+shift+p': 'palette.open' })
  const [confirmKillId, setConfirmKillId] = useState<string | null>(null)
  const [confirmSpaceDeleteId, setConfirmSpaceDeleteId] = useState<string | null>(null)
  const [mainMenuOpen, setMainMenuOpen] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [worktrees, setWorktrees] = useState<WorktreeState>(null)
  const [worktreeCreate, setWorktreeCreate] = useState<WorktreeCreate>(null)
  const [confirmWorktreeRemove, setConfirmWorktreeRemove] = useState<string | null>(null)
  const [renameTarget, setRenameTarget] = useState<RenameTarget | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsSection, setSettingsSection] = useState('General')
  const [noteTarget, setNoteTarget] = useState<NoteTarget | null>(null)
  const [mobileNoteId, setMobileNoteId] = useState<string | null>(null)
  const [processSession, setProcessSession] = useState<Session | null>(null)
  const [previews, setPreviews] = useState<Record<string, Preview>>({})
  const [notificationData, setNotificationData] = useState<NotificationData>({notifications:[],deliveries:[]})
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const [notificationUnread, setNotificationUnread] = useState(0)
  const [notificationToast, setNotificationToast] = useState<UiNotification | null>(null)
  const [usageOpen, setUsageOpen] = useState(false)
  const [xtermScrollback, setXtermScrollback] = useState(10000)
  const [profiles, setProfiles] = useState<ShellProfile[]>([])
  const [defaultProfile, setDefaultProfile] = useState('default')
  const [launcherProfile, setLauncherProfile] = useState(localStorage.getItem('mux.lastProfile') || '')
  const [pinnedCwds, setPinnedCwds] = useState<string[]>([])
  const [browserPath, setBrowserPath] = useState<string | null>(null)
  const [browserDirs, setBrowserDirs] = useState<Array<{name:string;path:string}>>([])
  const [browserParent, setBrowserParent] = useState<string | null>(null)
  const spawning = useRef(false)
  const longPressTimer = useRef<number | null>(null)
  const notificationIds = useRef<Set<string>>(new Set())
  const paletteInput = useRef<HTMLInputElement>(null)

  const cancelLongPress = () => {
    if (longPressTimer.current !== null) window.clearTimeout(longPressTimer.current)
    longPressTimer.current = null
  }
  const beginLongPress = (event: JSX.TargetedPointerEvent<HTMLElement>, open: (x:number,y:number)=>void) => {
    if (event.pointerType !== 'touch') return
    cancelLongPress()
    const {clientX,clientY}=event
    longPressTimer.current=window.setTimeout(()=>{navigator.vibrate?.(20);open(clientX,clientY);longPressTimer.current=null},550)
  }

  const loadProfiles = () => api<{default_profile_id:string;profiles:ShellProfile[];detected:ShellProfile[]}>('GET','/api/profiles').then(result => { const combined=[...result.profiles,...result.detected.filter(profile=>!result.profiles.some(item=>item.id===profile.id))];setProfiles(combined.filter(profile=>profile.enabled));setDefaultProfile(result.default_profile_id);setLauncherProfile(current=>current||result.default_profile_id) })

  const loadNotifications = async (announce=false) => {
    const next=await api<NotificationData>('GET','/api/notifications')
    const fresh=next.notifications.filter(item=>!notificationIds.current.has(item.delivery_id))
    notificationIds.current=new Set(next.notifications.map(item=>item.delivery_id))
    setNotificationData(next)
    if(announce&&fresh.length){setNotificationUnread(count=>count+fresh.length);setNotificationToast(fresh[fresh.length-1])}
  }

  const refresh = async () => {
    try {
      const [nextSessions, nextSpaces, nextPreviews] = await Promise.all([
        api<Session[]>('GET', '/api/sessions'), api<Space[]>('GET', '/api/spaces'),
        api<{items:Preview[]}>('GET', '/api/previews'),
      ])
      setSessions(nextSessions)
      setSpaces(nextSpaces)
      setPreviews(Object.fromEntries(nextPreviews.items.map(item => [item.id, item])))
      setLayoutMap(current => {
        const next = { ...current }
        const live = new Set(nextSessions.filter(session => !['exited', 'crashed'].includes(session.state)).map(session => session.id))
        for (const space of nextSpaces) {
          next[space.id] = reconcileTerminals(parseLayout(space.layout), live)
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
    void api<{theme:ThemeName;custom_theme:CustomTheme;xterm_scrollback_lines:number}>('GET','/api/config').then(config => { configureCustomTheme(config.custom_theme); applyTheme(config.theme); setXtermScrollback(config.xterm_scrollback_lines) })
    void loadProfiles()
    void loadNotifications()
    void api<{paths:string[]}>('GET','/api/directories/pins').then(result=>setPinnedCwds(result.paths))
    const loadKeys = () => void api<{ bindings: Record<string, string> }>('GET', '/api/keybindings').then(result => setKeybindings(current => JSON.stringify(current) === JSON.stringify(result.bindings) ? current : result.bindings))
    loadKeys()
    const timer = setInterval(refresh, 2500)
    const keyTimer = setInterval(loadKeys, 5000)
    return () => { clearInterval(timer); clearInterval(keyTimer) }
  }, [])

  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: light)')
    const update = () => document.documentElement.dataset.themeSelection === 'system' && applyTheme('system')
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])

  useEffect(() => {
    let socket: WebSocket | null = null
    let retry: number | undefined
    const connect = () => {
      socket = openWebSocket('/events')
      socket.onmessage = message => {
        void refresh()
        try { if (JSON.parse(String(message.data)).type==='notification') void loadNotifications(true) } catch { /* malformed events are ignored */ }
      }
      socket.onclose = () => { retry = window.setTimeout(connect, 1500) }
    }
    connect()
    return () => { if (retry) clearTimeout(retry); socket?.close() }
  }, [])

  useEffect(()=>{if(!notificationToast)return;const timer=window.setTimeout(()=>setNotificationToast(null),5000);return()=>clearTimeout(timer)},[notificationToast])

  const active = sessions.find(session => session.id === activeId)
  const attention = sessions.filter(session => session.state === 'awaiting').length
  const activeSpace = spaces.find(space => space.id === spaceId)
  const activeLayout = layoutMap[spaceId] || emptyLayout()
  const paneIds = terminalIds(activeLayout).filter(id => sessions.some(session => session.id === id && !['exited', 'crashed'].includes(session.state)))
  const unpanned = sessions.filter(session => session.space_id === spaceId && !['exited', 'crashed'].includes(session.state) && !paneIds.includes(session.id))
  const rememberedCwds = useMemo(() => {
    const stored = JSON.parse(localStorage.getItem('mux.recentCwds') || '[]') as string[]
    return [...new Set([active?.cwd, ...sessions.map(session => session.cwd), ...stored].filter(Boolean))] as string[]
  }, [sessions, active?.cwd])

  const openSettings = (section='General') => { setSettingsSection(section); setSettingsOpen(true); setMainMenuOpen(false); setSpaceMenu(null) }
  const spaceNoteTarget = (space:Space):NoteTarget|null => {
    const session=(active?.space_id===space.id?active:undefined)||sessions.find(item=>item.space_id===space.id&&!['exited','crashed'].includes(item.state))||sessions.find(item=>item.space_id===space.id)
    const targetCwd=session?.cwd||space.default_cwd||rememberedCwds[0]
    const terminalSessionId=session&&!['exited','crashed'].includes(session.state)?session.id:null
    return targetCwd?{cwd:targetCwd,spaceId:space.id,sessionId:session?.id||null,terminalSessionId,kind:'spaces'}:null
  }
  const sessionNoteTarget = (session:Session):NoteTarget => ({
    cwd:session.cwd,spaceId:session.space_id,sessionId:session.id,
    terminalSessionId:['exited','crashed'].includes(session.state)?null:session.id,kind:'sessions',
  })
  const noteIdForTarget = (target:NoteTarget) => {
    const id=target.kind==='spaces'?target.spaceId:target.sessionId
    return id?noteResourceId(target.kind,id):null
  }
  const noteIsDocked = (target:NoteTarget) => {
    const id=noteIdForTarget(target)
    return !!id&&leaves(layoutMap[target.spaceId]||emptyLayout(),'note').some(leaf=>leaf.id===id)
  }
  const noteTargetForResource = (resourceId:string):NoteTarget|null => {
    const identity=parseNoteResourceId(resourceId)
    if(identity?.kind==='spaces'){
      const space=spaces.find(item=>item.id===identity.id)
      return space?spaceNoteTarget(space):null
    }
    if(identity?.kind==='sessions'){
      const session=sessions.find(item=>item.id===identity.id)
      return session?sessionNoteTarget(session):null
    }
    return null
  }
  const openNoteModal = (target:NoteTarget) => {
    const dockedId=noteIdForTarget(target)
    if(dockedId&&noteIsDocked(target)){
      setSpaceId(target.spaceId);setMobileNoteId(dockedId)
      setError('That note is already open in a split pane. Use “pop out” there for the modal editor.')
    }else setNoteTarget(target)
    setSpaceMenu(null);setContextMenu(null);setMainMenuOpen(false);setEmptyMenu(null)
  }
  const openSpaceNotes = (space:Space) => {
    const target=spaceNoteTarget(space)
    if(!target){setError('No project directory is available for this space.');return}
    openNoteModal(target)
  }
  const openSessionNotes = (session:Session) => {
    setActiveId(session.id);openNoteModal(sessionNoteTarget(session))
  }
  const openNotifications = () => { setNotificationsOpen(true);setNotificationUnread(0);setMainMenuOpen(false);void loadNotifications() }

  useEffect(() => {
    document.title = `${attention ? `(${attention}) ` : ''}swe-mux`
  }, [attention])

  useEffect(() => {
    if (!contextMenu && !spaceMenu && !emptyMenu && !mainMenuOpen && !renameTarget && !worktreeCreate) return
    const dismissEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      setContextMenu(null)
      setSpaceMenu(null)
      setEmptyMenu(null)
      setMainMenuOpen(false)
      setRenameTarget(null)
      setWorktreeCreate(null)
    }
    window.addEventListener('keydown', dismissEscape, true)
    return () => window.removeEventListener('keydown', dismissEscape, true)
  }, [contextMenu, spaceMenu, emptyMenu, mainMenuOpen, renameTarget, worktreeCreate])

  useEffect(() => {
    if (!contextMenu && !spaceMenu && !emptyMenu && !mainMenuOpen) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const frame = requestAnimationFrame(() => document.querySelector<HTMLElement>('.context-menu button:not(:disabled)')?.focus())
    const navigate = (event: KeyboardEvent) => {
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
      const buttons = [...document.querySelectorAll<HTMLButtonElement>('.context-menu button:not(:disabled)')]
      if (!buttons.length) return
      event.preventDefault()
      const current = buttons.indexOf(document.activeElement as HTMLButtonElement)
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
        : (Math.max(current, 0) + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
      buttons[next].focus()
    }
    window.addEventListener('keydown', navigate, true)
    return () => { cancelAnimationFrame(frame); window.removeEventListener('keydown', navigate, true); previous?.focus() }
  }, [contextMenu, spaceMenu, emptyMenu, mainMenuOpen])

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

  const spawnTerminal = async (targetSpace = spaceId, targetCwd?: string, split: false | SplitDirection = false, profileId?: string) => {
    if (spawning.current) return
    spawning.current = true
    try {
      const resolvedCwd = targetCwd || active?.cwd || rememberedCwds[0]
      const next = await api<Session>('POST', '/api/sessions', {
        backend: 'shell', space: targetSpace, cwd: resolvedCwd || undefined,
        profile_id: profileId || undefined,
      })
      if (resolvedCwd) rememberCwd(resolvedCwd)
      if (profileId) { localStorage.setItem('mux.lastProfile',profileId); setLauncherProfile(profileId) }
      setSessions(items => [...items, next])
      setSpaceId(targetSpace)
      setActiveId(next.id)
      const currentLayout = layoutMap[targetSpace] || emptyLayout()
      const focused = targetSpace === spaceId ? activeId : terminalIds(currentLayout)[0] || null
      const nextLayout = split
        ? splitTerminal(currentLayout, focused, next.id, split)
        : replaceTerminal(currentLayout, focused, next.id)
      await updateLayout(targetSpace, nextLayout)
      setLauncherOpen(false)
      setCwd('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      spawning.current = false
    }
  }

  const openLauncher = (targetSpace = spaceId, split: false | SplitDirection = false) => {
    setLauncherSpace(targetSpace)
    setLauncherSplit(split)
    setCwd(active?.cwd || rememberedCwds[0] || '')
    setLauncherProfile(localStorage.getItem('mux.lastProfile') || spaces.find(item=>item.id===targetSpace)?.default_profile_id || defaultProfile)
    setBrowserPath(null)
    setLauncherOpen(true)
  }

  const browseDirectory = async (path = cwd) => {
    try {
      if (!path.trim()) {
        const roots = await api<{roots:string[]}>('GET','/api/fs/roots')
        setBrowserPath('daemon roots'); setBrowserParent(null)
        setBrowserDirs(roots.roots.map(root=>({name:root,path:root})))
        return
      }
      const result = await api<{path:string;parent:string|null;directories:Array<{name:string;path:string}>}>('GET',`/api/fs/list?path=${encodeURIComponent(path)}`)
      setBrowserPath(result.path); setBrowserParent(result.parent); setBrowserDirs(result.directories); setCwd(result.path)
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
  }

  const togglePin = async (path:string) => {
    const pinned = pinnedCwds.some(item=>item.toLowerCase()===path.toLowerCase())
    const result = await api<{paths:string[]}>(pinned?'DELETE':'POST','/api/directories/pins',{path})
    setPinnedCwds(result.paths)
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

  const deleteSpace = async (space: Space, disposition: 'move' | 'kill' | 'reject' = 'reject') => {
    await api('DELETE', `/api/spaces/${space.id}`, { disposition, target_space: 'default' })
    setSpaces(items => items.filter(item => item.id !== space.id))
    setConfirmSpaceDeleteId(null)
    if (spaceId === space.id) setSpaceId('default')
  }

  const killNow = async (session: Session) => {
    await api('DELETE', `/api/sessions/${session.id}`)
    setConfirmKillId(null)
    if (activeId === session.id) setActiveId(null)
    await updateLayout(session.space_id, removeLeaf(layoutMap[session.space_id] || emptyLayout(), 'terminal', session.id))
    await refresh()
  }

  const requestKill = (session: Session) => {
    if (confirmKillId === session.id) void killNow(session)
    else setConfirmKillId(session.id)
  }

  const updateLayout = async (targetSpace: string, layout: PaneLayout) => {
    setLayoutMap(current => ({ ...current, [targetSpace]: layout }))
    const revision = spaces.find(space => space.id === targetSpace)?.layout_revision ?? 0
    try {
      const updated = await api<Space>('PATCH', `/api/spaces/${targetSpace}`, { layout, layout_revision: revision })
      setSpaces(items => items.map(item => item.id === updated.id ? updated : item))
      setLayoutMap(current => ({ ...current, [targetSpace]: parseLayout(updated.layout) }))
    } catch (cause) {
      await refresh()
      const message = cause instanceof Error ? cause.message : String(cause)
      setError(message.includes('stale layout revision') ? 'Layout changed in another client; reloaded the current layout.' : message)
    }
  }

  const openNoteInSplit = async (target:NoteTarget) => {
    const resourceId=noteIdForTarget(target)
    const terminalId=target.terminalSessionId
    if(!resourceId||!terminalId||!sessions.some(session=>session.id===terminalId&&!['exited','crashed'].includes(session.state))){
      setError('A live terminal is required to dock this note.');return
    }
    const current=layoutMap[target.spaceId]||emptyLayout()
    setSpaceId(target.spaceId);setActiveId(terminalId);setMobileNoteId(resourceId)
    setContextMenu(null);setSpaceMenu(null);setMainMenuOpen(false);setEmptyMenu(null)
    if(!leaves(current,'note').some(leaf=>leaf.id===resourceId)){
      await updateLayout(target.spaceId,attachLeaf(current,terminalId,resourceLeaf('note',resourceId),'horizontal',.62))
    }
    setNoteTarget(null)
  }

  const removeNotePane = async (targetSpace:string,resourceId:string) => {
    setMobileNoteId(current=>current===resourceId?null:current)
    await updateLayout(targetSpace,removeLeaf(layoutMap[targetSpace]||emptyLayout(),'note',resourceId))
  }

  const popOutNotePane = async (target:NoteTarget,resourceId:string) => {
    await removeNotePane(target.spaceId,resourceId)
    setNoteTarget(target)
  }

  const selectSession = async (session: Session) => {
    const current = layoutMap[session.space_id] || emptyLayout()
    const next = replaceTerminal(current, activeId, session.id)
    setSpaceId(session.space_id)
    setActiveId(session.id)
    setSidebarOpen(false)
    await updateLayout(session.space_id, next)
  }

  const openInSplit = async (session: Session, direction: SplitDirection = 'horizontal') => {
    setSpaceId(session.space_id)
    setActiveId(session.id)
    await updateLayout(session.space_id, splitTerminal(layoutMap[session.space_id] || emptyLayout(), activeId, session.id, direction))
    setContextMenu(null)
  }

  const loadHistory = async (options: {append?:boolean;query?:string;backend?:string;project?:string|null;state?:string;space?:string;external?:string;from?:string;to?:string} = {}) => {
    const query = options.query ?? historyQuery
    const backend = options.backend ?? historyBackend
    const project = options.project === undefined ? historyProject : options.project
    const state = options.state ?? historyState
    const historySpaceId = options.space ?? historySpace
    const external = options.external ?? historyExternal
    const from = options.from ?? historyFrom
    const to = options.to ?? historyTo
    setHistoryLoading(true); setHistoryError('')
    try {
      const parameters = new URLSearchParams({ limit: '50' })
      if (query) parameters.set('q', query)
      if (backend) parameters.set('backend', backend)
      if (project) parameters.set('project', project)
      if (state) parameters.set('state', state)
      if (historySpaceId) parameters.set('space', historySpaceId)
      if (external) parameters.set('external', external)
      if (from) parameters.set('date_from', String(new Date(from).getTime() / 1000))
      if (to) parameters.set('date_to', String(new Date(to).getTime() / 1000))
      if (options.append && historyNext) parameters.set('cursor', historyNext)
      const page = await api<HistoryPage>('GET', `/api/history?${parameters}`)
      setHistory(items => options.append ? [...items, ...page.items] : page.items)
      setHistoryNext(page.next_cursor)
    } catch (cause) { setHistoryError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setHistoryLoading(false) }
  }

  const showHistory = async () => {
    setTranscript(null); setHistoryOpen(true); setHistoryQuery(''); setHistoryBackend(''); setHistoryProject(null); setHistoryState(''); setHistorySpace(''); setHistoryExternal(''); setHistoryFrom(''); setHistoryTo('')
    try {
      const projects = await api<{items:HistoryProject[]}>('GET', '/api/history/projects')
      setHistoryProjects(projects.items)
    } catch (cause) { setHistoryError(cause instanceof Error ? cause.message : String(cause)) }
    await loadHistory({ query: '', backend: '', project: null, state: '', space: '', external: '', from: '', to: '' })
  }

  const deleteHistory = async (entry: HistoryEntry) => {
    if (confirmHistoryDelete !== entry.id) { setConfirmHistoryDelete(entry.id); return }
    await api('DELETE', `/api/history/${entry.id}`)
    setHistory(items => items.filter(item => item.id !== entry.id))
    if (transcript?.entry.id === entry.id) setTranscript(null)
    setConfirmHistoryDelete(null)
  }

  const viewHistory = async (entry: HistoryEntry) => {
    setHistoryError('')
    try { setTranscript(await api<Transcript>('GET', `/api/history/${entry.id}/transcript`)) }
    catch (cause) { setTranscript(null); setHistoryError(cause instanceof Error ? cause.message : String(cause)) }
  }

  const resumeHistoryEntry = async (entry: HistoryEntry) => {
    try {
      const resumed = await api<Session>('POST', `/api/history/${entry.id}/resume`, { space: spaceId, target_session_id: activeId })
      setSessions(items => [...items, resumed]); setActiveId(resumed.id); setHistoryOpen(false)
      await refresh()
    } catch (cause) { setHistoryError(cause instanceof Error ? cause.message : String(cause)) }
  }

  const resumeSession = async (session: Session) => {
    try {
      const resumed = await api<Session>('POST', `/api/history/${session.id}/resume`, { space: session.space_id })
      setSessions(items => [...items, resumed])
      setActiveId(resumed.id)
      setContextMenu(null)
      await updateLayout(session.space_id, replaceTerminal(layoutMap[session.space_id] || emptyLayout(), session.id, resumed.id))
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
  }

  const move = async (session: Session, target: string) => {
    const sourceLayout = layoutMap[session.space_id] || emptyLayout()
    const targetLayout = layoutMap[target] || emptyLayout()
    const updated = await api<Session>('PATCH', `/api/sessions/${session.id}`, { space: target })
    updateSession(updated)
    await updateLayout(session.space_id, removeLeaf(sourceLayout, 'terminal', session.id))
    await updateLayout(target, replaceTerminal(targetLayout, terminalIds(targetLayout)[0] || null, session.id))
    setContextMenu(null)
  }

  const createWorktree = (session: Session) => {
    setContextMenu(null)
    setWorktrees(null)
    setWorktreeCreate({ session, branch: '', path: `${session.cwd}-worktree`, pathEdited: false })
  }

  const submitWorktree = async () => {
    if (!worktreeCreate) return
    const branch = worktreeCreate.branch.trim()
    const path = worktreeCreate.path.trim()
    if (!branch || !path) return
    try {
      const target = worktreeCreate.session
      const result = await api<{path:string;spawn:{status:string;session?:Session;space?:Space;error?:string;worktree_retained?:boolean}}>('POST', '/api/git/worktrees', {
        cwd: target.cwd,
        path,
        branch,
        spawn: { space: target.space_id, target_session_id: target.id },
      })
      setWorktreeCreate(null)
      if (result.spawn.status !== 'created' || !result.spawn.session || !result.spawn.space) {
        throw new Error(`${result.spawn.error || 'terminal spawn failed'} · worktree retained at ${result.path}`)
      }
      setSessions(items => [...items, result.spawn.session!])
      setLayoutMap(current => ({
        ...current,
        [result.spawn.space!.id]: parseLayout(result.spawn.space!.layout),
      }))
      setSpaces(items => items.map(space => space.id === result.spawn.space!.id ? result.spawn.space! : space))
      setSpaceId(result.spawn.space.id)
      setActiveId(result.spawn.session.id)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
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

  const commandSession = contextMenu?.session || active
  const commandSpace = spaceMenu?.space || activeSpace
  const commands: Command[] = [
    { id: 'palette.open', label: 'Open command palette', category: 'view', available: true, run: () => setPaletteOpen(true) },
    { id: 'session.spawnShell', label: 'New terminal here', category: 'session', available: true, run: () => void spawnTerminal() },
    { id: 'session.quickLaunch', label: 'New terminal custom…', category: 'session', available: true, run: () => openLauncher() },
    { id: 'space.create', label: 'Create space', category: 'space', available: true, run: () => void createSpace() },
    { id: 'history.open', label: 'Browse session history', category: 'view', available: true, run: () => void showHistory() },
    { id: 'settings.open', label: 'Open Settings', category: 'view', available: true, run: () => openSettings() },
    { id: 'settings.project', label: 'Open current project settings', category: 'view', available: !!(active?.cwd||rememberedCwds[0]), disabledReason: 'No project directory is available', run: () => openSettings('Current project') },
    { id: 'usage.open', label: 'Open usage analytics', category: 'view', available: true, run: () => {setUsageOpen(true);setMainMenuOpen(false)} },
    { id: 'hooks.open', label: 'Open hooks and notification settings', category: 'view', available: true, run: () => openSettings('Hooks and notifications') },
    { id: 'notifications.open', label: `Open notifications${notificationUnread?` (${notificationUnread} new)`:''}`, category: 'view', available: true, run: openNotifications },
    { id: 'notes.open', label: 'Open current space notes', category: 'view', available: !!activeSpace&&!!spaceNoteTarget(activeSpace), disabledReason: 'No project directory is available', run: () => activeSpace&&openSpaceNotes(activeSpace) },
    { id: 'session.notes', label: 'Open selected session note', category: 'view', available: !!commandSession, disabledReason: 'No session selected', run: () => commandSession&&openSessionNotes(commandSession) },
    { id: 'space.notes', label: 'Open selected space notes', category: 'view', available: !!commandSpace&&!!spaceNoteTarget(commandSpace), disabledReason: 'No project directory is available for this space', run: () => commandSpace&&openSpaceNotes(commandSpace) },
    { id: 'session.notesSplit', label: 'Open selected session note in split', category: 'pane', available: !!commandSession&&!['exited','crashed'].includes(commandSession.state), disabledReason: 'A live session is required', run: () => commandSession&&void openNoteInSplit(sessionNoteTarget(commandSession)) },
    { id: 'space.notesSplit', label: 'Open selected space note in split', category: 'pane', available: !!commandSpace&&!!spaceNoteTarget(commandSpace)?.terminalSessionId, disabledReason: 'A live terminal is required in this space', run: () => {const target=commandSpace&&spaceNoteTarget(commandSpace);if(target)void openNoteInSplit(target)} },
    { id: 'processes.open', label: 'Inspect selected session processes and previews', category: 'view', available: !!commandSession, disabledReason: 'No session selected', run: () => {if(commandSession){setProcessSession(commandSession);setContextMenu(null);setMainMenuOpen(false)}} },
    { id: 'terminal.find', label: 'Find in focused terminal', category: 'terminal', available: !!active, disabledReason: 'No focused terminal', run: () => window.dispatchEvent(new CustomEvent('mux:terminal-find', { detail: activeId })) },
    ...(['copy', 'paste', 'selectAll', 'clear'] as const).map((action): Command => ({
      id: `terminal.${action}`, label: `${action === 'selectAll' ? 'Select all' : action[0].toUpperCase() + action.slice(1)} in focused terminal`,
      category: 'clipboard', available: !!active, disabledReason: 'No focused terminal',
      run: () => window.dispatchEvent(new CustomEvent('mux:terminal-action', { detail: { sessionId: activeId, action } })),
    })),
    { id: 'terminal.pasteImage', label: 'Paste clipboard image into focused agent', category: 'clipboard', available: !!active && isAgent(active), disabledReason: 'Clipboard images require a focused Claude or Codex session', run: () => window.dispatchEvent(new CustomEvent('mux:terminal-action', { detail: { sessionId: activeId, action: 'pasteImage' } })) },
    { id: 'session.kill', label: 'Kill focused session', category: 'session', available: !!active, disabledReason: 'No focused session', run: () => active && requestKill(active) },
    { id: 'session.killImmediate', label: 'Kill selected session immediately', category: 'session', available: !!commandSession, disabledReason: 'No session selected', run: () => commandSession && void killNow(commandSession) },
    { id: 'session.pinAttention', label: active?.pinned_attention ? 'Unpin focused session attention' : 'Pin focused session attention', category: 'session', available: !!active && isAgent(active), disabledReason: 'Attention pinning requires a focused agent', run: () => active && void api<Session>('PATCH', `/api/sessions/${active.id}`, { pin: !active.pinned_attention }).then(updateSession) },
    { id: 'session.open', label: 'Open selected session in focused pane', category: 'session', available: !!commandSession && !['exited', 'crashed'].includes(commandSession.state), disabledReason: 'No live session selected', run: () => commandSession && void selectSession(commandSession) },
    { id: 'session.rename', label: 'Rename selected session', category: 'session', available: !!commandSession, disabledReason: 'No session selected', run: () => commandSession && openRename({ kind: 'session', session: commandSession }) },
    { id: 'session.copyId', label: 'Copy selected session ID', category: 'clipboard', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) void navigator.clipboard.writeText(commandSession.id).catch(() => setError('Clipboard access was blocked.')) ; setContextMenu(null) } },
    { id: 'session.copyCwd', label: 'Copy selected working directory', category: 'clipboard', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) void navigator.clipboard.writeText(commandSession.cwd).catch(() => setError('Clipboard access was blocked.')); setContextMenu(null) } },
    { id: 'session.openSplitHorizontal', label: 'Open selected session in split right', category: 'pane', available: !!commandSession && !['exited', 'crashed'].includes(commandSession.state), disabledReason: 'No live session selected', run: () => commandSession && void openInSplit(commandSession, 'horizontal') },
    { id: 'session.openSplitVertical', label: 'Open selected session in split below', category: 'pane', available: !!commandSession && !['exited', 'crashed'].includes(commandSession.state), disabledReason: 'No live session selected', run: () => commandSession && void openInSplit(commandSession, 'vertical') },
    { id: 'session.reveal', label: 'Reveal selected working directory', category: 'session', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) void api('POST', '/api/reveal', { path: commandSession.cwd }); setContextMenu(null) } },
    { id: 'session.worktreeCreate', label: 'Create worktree and terminal', category: 'git', available: !!commandSession, disabledReason: 'No session selected', run: () => commandSession && createWorktree(commandSession) },
    { id: 'session.worktreesManage', label: 'Manage worktrees', category: 'git', available: !!commandSession, disabledReason: 'No session selected', run: () => commandSession && void manageWorktrees(commandSession) },
    { id: 'session.customSplit', label: 'New custom terminal in selected session split', category: 'pane', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) { setContextMenu(null); openLauncher(commandSession.space_id, 'horizontal') } } },
    { id: 'session.broadcastMembership', label: commandSession?.broadcast ? 'Remove selected session from broadcast' : 'Add selected session to broadcast', category: 'input', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) void api<Session>('POST', `/api/sessions/${commandSession.id}/broadcast-set`, { include: !commandSession.broadcast }).then(updated => { updateSession(updated); setContextMenu(null) }) } },
    { id: 'session.resume', label: 'Resume selected agent as new', category: 'history', available: !!commandSession && isAgent(commandSession) && ['exited', 'crashed'].includes(commandSession.state), disabledReason: 'Select an exited Claude or Codex session', run: () => commandSession && void resumeSession(commandSession) },
    { id: 'space.newTerminal', label: 'New terminal in selected space', category: 'space', available: !!commandSpace, disabledReason: 'No space selected', run: () => { if (commandSpace) void spawnTerminal(commandSpace.id); setSpaceMenu(null) } },
    { id: 'space.newTerminalCustom', label: 'New custom terminal in selected space', category: 'space', available: !!commandSpace, disabledReason: 'No space selected', run: () => { if (commandSpace) openLauncher(commandSpace.id); setSpaceMenu(null) } },
    { id: 'space.rename', label: 'Rename selected space', category: 'space', available: !!commandSpace, disabledReason: 'No space selected', run: () => commandSpace && openRename({ kind: 'space', space: commandSpace }) },
    { id: 'space.settings', label: 'Edit selected space defaults', category: 'space', available: !!commandSpace, disabledReason: 'No space selected', run: () => openSettings('Space defaults') },
    { id: 'space.delete', label: 'Delete selected space…', category: 'space', available: !!commandSpace && commandSpace.id !== 'default', disabledReason: 'The default space cannot be deleted', run: () => { if (commandSpace) { setConfirmSpaceDeleteId(commandSpace.id); setSpaceMenu(current => current || { space: commandSpace, x: innerWidth / 2, y: innerHeight / 2 }) } } },
    ...unpanned.map((session): Command => ({
      id: `session.attach(${session.id})`, label: `Attach live session: ${session.name}`, category: 'pane', available: true,
      run: () => { setActiveId(session.id); setEmptyMenu(null); void updateLayout(spaceId, replaceTerminal(activeLayout, activeId, session.id)) },
    })),
    ...sessions.map((session): Command => ({
      id: `session.requestKill(${session.id})`, label: `Kill session: ${session.name}`, category: 'session', available: !['exited', 'crashed'].includes(session.state), disabledReason: 'Session has already ended',
      run: () => requestKill(session),
    })),
    ...(commandSession ? spaces.filter(item => item.id !== commandSession.space_id).map((target): Command => ({
      id: `session.move(${target.id})`, label: `Move selected session to ${target.name}`, category: 'space', available: true,
      run: () => void move(commandSession, target.id),
    })) : []),
    { id: 'pane.splitHorizontal', label: 'Split focused pane right', category: 'pane', available: !!active, disabledReason: 'No focused terminal', run: () => void spawnTerminal(spaceId, active?.cwd, 'horizontal') },
    { id: 'pane.splitVertical', label: 'Split focused pane below', category: 'pane', available: !!active, disabledReason: 'No focused terminal', run: () => void spawnTerminal(spaceId, active?.cwd, 'vertical') },
    { id: 'pane.detach', label: 'Detach focused pane', category: 'pane', available: !!active, disabledReason: 'No focused terminal', run: () => active && void updateLayout(spaceId, removeLeaf(activeLayout, 'terminal', active.id)) },
    { id: 'pane.zoom', label: zoomedId ? 'Restore pane layout' : 'Zoom focused pane', category: 'pane', available: !!active && paneIds.length > 1, disabledReason: 'Zoom requires multiple panes', run: () => setZoomedId(zoomedId ? null : activeId) },
    { id: 'pane.next', label: 'Focus next pane', category: 'pane', available: paneIds.length > 1, disabledReason: 'Only one pane is open', run: () => focusRelativePane(1) },
    { id: 'pane.previous', label: 'Focus previous pane', category: 'pane', available: paneIds.length > 1, disabledReason: 'Only one pane is open', run: () => focusRelativePane(-1) },
    { id: 'pane.swapNext', label: 'Swap focused pane with next', category: 'pane', available: !!activeId && paneIds.length > 1, disabledReason: 'Swap requires multiple panes', run: () => {
      if (!activeId || paneIds.length < 2) return
      const next = paneIds[(paneIds.indexOf(activeId) + 1) % paneIds.length]
      void updateLayout(spaceId, swapTerminals(activeLayout, activeId, next))
    } },
    { id: 'broadcast.toggle', label: broadcast ? 'Stop broadcasting input' : 'Start broadcasting input', category: 'input', available: true, run: () => setBroadcast(value => !value) },
    ...spaces.slice(0, 9).map((space, index): Command => ({
      id: `space.activate(${index + 1})`, label: `Switch to space ${index + 1}: ${space.name}`,
      category: 'space', available: space.id !== spaceId, disabledReason: 'Space is already active',
      run: () => { setSpaceId(space.id); setActiveId(terminalIds(layoutMap[space.id] || emptyLayout())[0] || null) },
    })),
  ]
  const shownCommands = searchCommands(commands, paletteQuery)
  useEffect(() => setPaletteIndex(0), [paletteQuery, paletteOpen])
  useEffect(()=>{if(!paletteOpen)return;const frame=requestAnimationFrame(()=>{paletteInput.current?.focus();paletteInput.current?.setSelectionRange(paletteInput.current.value.length,paletteInput.current.value.length)});return()=>cancelAnimationFrame(frame)},[paletteOpen])

  function focusRelativePane(offset: number) {
    if (!paneIds.length) return
    const current = activeId ? paneIds.indexOf(activeId) : -1
    setActiveId(paneIds[(Math.max(current, 0) + offset + paneIds.length) % paneIds.length])
  }

  const runNamedCommand = (command: string): boolean => {
    const result = runCommand(commands, command)
    if (result === 'disabled') {
      const disabled = commands.find(item => item.id === command)
      if (disabled?.disabledReason) setError(disabled.disabledReason)
    }
    return result !== 'unknown'
  }

  useEffect(() => {
    const onCommand = (event: Event) => {
      const command = (event as CustomEvent<string>).detail
      if (command === 'clipboard.help') setError('Clipboard access was blocked by the browser. Use the terminal context menu or allow clipboard access for this site.')
      else runNamedCommand(command)
    }
    const onError = (event: Event) => setError(String((event as CustomEvent<string>).detail))
    const onKey = (event: KeyboardEvent) => {
      const command = keybindings[keyChord(event)]
      if (command && runNamedCommand(command)) event.preventDefault()
      if (event.key === 'Escape') {
        setPaletteOpen(false); setLauncherOpen(false); setContextMenu(null); setSpaceMenu(null); setEmptyMenu(null); setMainMenuOpen(false); setWorktrees(null); setSidebarOpen(false); setRenameTarget(null); setWorktreeCreate(null); setNoteTarget(null); setNotificationsOpen(false); setProcessSession(null); setSettingsOpen(false); setHistoryOpen(false); setTranscript(null)
      }
    }
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target
      if (target instanceof Element && target.closest('.context-menu,.menu-trigger')) return
      setContextMenu(null)
      setSpaceMenu(null)
      setEmptyMenu(null)
      setMainMenuOpen(false)
    }
    window.addEventListener('mux:command', onCommand)
    window.addEventListener('mux:error', onError)
    window.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', onPointerDown)
    return () => {
      window.removeEventListener('mux:command', onCommand)
      window.removeEventListener('mux:error', onError)
      window.removeEventListener('keydown', onKey)
      document.removeEventListener('pointerdown', onPointerDown)
    }
  })

  const updateSession = (next: Session) => setSessions(items => items.map(item => item.id === next.id ? next : item))

  const beginResize = (event: JSX.TargetedPointerEvent<HTMLDivElement>, path: string, direction: SplitDirection) => {
    event.preventDefault()
    event.stopPropagation()
    const split = event.currentTarget.parentElement
    if (!split) return
    const rect = split.getBoundingClientRect()
    let latest = activeLayout
    const moveDivider = (pointer: PointerEvent) => {
      const ratio = direction === 'horizontal'
        ? (pointer.clientX - rect.left) / rect.width
        : (pointer.clientY - rect.top) / rect.height
      latest = setSplitRatio(activeLayout, path, ratio)
      setLayoutMap(current => ({ ...current, [spaceId]: latest }))
    }
    const stopResize = () => {
      window.removeEventListener('pointermove', moveDivider)
      window.removeEventListener('pointerup', stopResize)
      void updateLayout(spaceId, latest)
    }
    window.addEventListener('pointermove', moveDivider)
    window.addEventListener('pointerup', stopResize, { once: true })
  }

  const renderPaneNode = (node: PaneNode, path = ''): ComponentChildren => {
    if (node.type === 'split') {
      return <div class={`pane-split ${node.direction}`}>
        <div class="pane-branch" style={{ flex: `${node.ratio} 1 0` }}>{renderPaneNode(node.first, `${path}f`)}</div>
        <div class={`pane-divider ${node.direction}`} role="separator" aria-orientation={node.direction === 'horizontal' ? 'vertical' : 'horizontal'} onPointerDown={event => beginResize(event, path, node.direction)} />
        <div class="pane-branch" style={{ flex: `${1 - node.ratio} 1 0` }}>{renderPaneNode(node.second, `${path}s`)}</div>
      </div>
    }
    if (node.kind === 'preview') {
      const preview = previews[node.id]
      if (!preview) return <section class="workspace-leaf-placeholder"><strong>preview unavailable</strong><span>{node.id}</span></section>
      return <PreviewPane preview={preview} onClose={() => void (async () => {
        await api('DELETE', `/api/previews/${preview.id}`)
        setPreviews(current => { const next = {...current}; delete next[preview.id]; return next })
        await updateLayout(preview.space_id, removeLeaf(layoutMap[preview.space_id] || emptyLayout(), 'preview', preview.id))
      })()} />
    }
    if(node.kind==='note'){
      const identity=parseNoteResourceId(node.id)
      const target=noteTargetForResource(node.id)
      if(!target)return <section class="workspace-leaf-placeholder note-unavailable"><strong>note unavailable</strong><span>{identity?.id||node.id}</span><button onClick={()=>void removeNotePane(spaceId,node.id)}>close pane</button></section>
      return <Notes display="pane" targetKey={`pane:${node.id}`} mobileActive={mobileNoteId===node.id} cwd={target.cwd} spaceId={target.spaceId} sessionId={target.sessionId} terminalSessionId={target.terminalSessionId} initialKind={target.kind} onHide={()=>setMobileNoteId(null)} onClose={()=>removeNotePane(target.spaceId,node.id)} onPopOut={()=>popOutNotePane(target,node.id)} onInsert={text=>window.dispatchEvent(new CustomEvent('mux:terminal-action',{detail:{sessionId:target.terminalSessionId,action:'insertText',text}}))} onCapture={targetKey=>window.dispatchEvent(new CustomEvent('mux:terminal-action',{detail:{sessionId:target.terminalSessionId,action:'captureSelection',targetKey}}))}/>
    }
    if (node.kind !== 'terminal') {
      return <section class="workspace-leaf-placeholder"><strong>{node.kind}</strong><span>{node.id}</span></section>
    }
    const session = sessions.find(item => item.id === node.id)
    if (!session) return null
    const id = session.id
    return <section class={`terminal-pane ${activeId === id ? 'focused' : ''}`} onPointerDown={() => setActiveId(id)}>
      <div class="pane-bar" onDblClick={() => setZoomedId(current => current === id ? null : id)}>
        <div><span class={`state-dot ${session.state}`} /><strong>{isAgent(session) && <span class={`agent-prefix ${session.backend}`}>[{session.backend}]</span>}{session.name}</strong><span class={`pane-state ${session.state}`} title={session.parser_diagnostic}>{sessionStatus(session)}</span>{session.git.branch && <span class="git-chip" title={`${session.git.branch}${session.git.dirty ? ` · ${session.git.dirty} changed` : ''}`}>{session.git.branch}{session.git.dirty ? ` ±${session.git.dirty}` : ''}</span>}</div>
        <div class="pane-path" title={session.cwd}>{session.cwd}</div>
        <div class="pane-tools"><span>PID {session.pid}</span><button class="pane-tool-label" aria-label={`Open note for ${session.name}`} title="Session note" onClick={() => openSessionNotes(session)}>note</button><button class="pane-tool-label" aria-label={`Inspect processes for ${session.name}`} title="Processes and previews" onClick={() => {setActiveId(session.id);setProcessSession(session)}}>proc</button><button title="Detach pane" onClick={() => runNamedCommand('pane.detach')}>—</button><button class={confirmKillId === session.id ? 'confirming' : ''} title={confirmKillId === session.id ? 'Confirm kill' : 'Kill session'} onClick={() => runNamedCommand(`session.requestKill(${session.id})`)}>{confirmKillId === session.id ? '✓' : '×'}</button></div>
      </div>
      <TerminalPane session={session} onState={updateSession} broadcast={broadcast} keybindings={keybindings} scrollback={xtermScrollback} />
    </section>
  }

  return <div class="app-shell">
    <div class="sr-only" role="status" aria-live="polite" aria-atomic="true">{attention ? `${attention} agent${attention === 1 ? '' : 's'} awaiting attention` : 'No agents awaiting attention'}</div>
    <header class="topbar">
      <div class="brand"><button class="nav-toggle" onClick={() => setSidebarOpen(value => !value)}>:nav</button><span class="brand-mark">&gt;_</span><span>swe-mux</span><span class="dev-chip">[local]</span></div>
      <div class="top-context"><span>{activeSpace?.name || 'Main'}</span><select class="mobile-session-switcher" aria-label="Focused session" value={activeId||''} onChange={event=>{const session=sessions.find(item=>item.id===event.currentTarget.value);if(session)void selectSession(session)}}><option value="">Select session</option>{sessions.filter(session=>session.space_id===spaceId&&!['exited','crashed'].includes(session.state)).map(session=><option value={session.id}>{isAgent(session)?`[${session.backend}] `:''}{session.name}</option>)}</select></div>
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
              <div class="space-row" onPointerDown={event=>beginLongPress(event,(x,y)=>setSpaceMenu({space,x,y}))} onPointerUp={cancelLongPress} onPointerCancel={cancelLongPress} onPointerMove={cancelLongPress} onContextMenu={event => { event.preventDefault(); setSpaceMenu({ space, x: event.clientX, y: event.clientY }) }} onClick={() => setSpaceId(space.id)}>
                <span class="space-chevron">{space.id === spaceId ? '▾' : '·'}</span><strong>{space.name}</strong><small>{children.filter(s => !['exited', 'crashed'].includes(s.state)).length}</small>
              </div>
              <div class="session-list">
                {children.map(session => <button class={`session-row ${activeId === session.id ? 'active' : ''} ${session.state}`} onPointerDown={event=>beginLongPress(event,(x,y)=>{setActiveId(session.id);setContextMenu({session,x,y})})} onPointerUp={cancelLongPress} onPointerCancel={cancelLongPress} onPointerMove={cancelLongPress} onContextMenu={event => { event.preventDefault(); setActiveId(session.id); setContextMenu({ session, x: event.clientX, y: event.clientY }) }} onClick={() => void selectSession(session)}>
                  <span class={`state-dot ${session.state}`} />
                  <span class="session-copy"><strong>{isAgent(session) && <span class={`agent-prefix ${session.backend}`}>[{session.backend}]</span>}{session.name}</strong><small class={isAgent(session) ? `agent-status ${session.state}` : ''}>{sessionStatus(session)}</small></span>
                  <span class="session-meta">{isAgent(session) && <em class={`state-label ${session.state}`}>{session.state === 'idle' ? 'ready' : session.state}</em>}</span>
                  <span class="row-actions" onClick={event => event.stopPropagation()}><button class={confirmKillId === session.id ? 'confirming' : ''} title={confirmKillId === session.id ? 'Confirm kill' : 'Kill'} onClick={() => runNamedCommand(`session.requestKill(${session.id})`)}>{confirmKillId === session.id ? '✓' : '×'}</button></span>
                </button>)}
              </div>
            </section>
          })}
        </div>
        <div class="sidebar-footer"><button class="menu-trigger" onClick={() => setMainMenuOpen(value => !value)}><span>:</span> menu</button></div>
      </aside>

      <main class="main-stage" onContextMenu={event => { if (!activeLayout.root) { event.preventDefault(); setEmptyMenu({ x: event.clientX, y: event.clientY }) } }}>
        {activeLayout.root ? <div class="pane-tree">{renderPaneNode(zoomedId ? { type: 'leaf', kind: 'terminal', id: zoomedId } : activeLayout.root)}</div> : <div class="empty-stage">
          <div class="hero-terminal" aria-hidden="true">&gt;_</div>
          <h1>Terminals that stay with you.</h1>
          <p>right-click {activeSpace?.name || 'a workspace'} or open : menu</p>
        </div>}
      </main>
    </div>

    {launcherOpen && <div class="quick-launcher" role="dialog" aria-modal="true" aria-label="New terminal custom">
      <div class="quick-heading"><span>NEW TERMINAL CUSTOM::{spaces.find(space => space.id === launcherSpace)?.name?.toUpperCase()}{launcherSplit?'::SPLIT':''}</span><button onClick={() => setLauncherOpen(false)}>×</button></div>
      <form onSubmit={event => { event.preventDefault(); void spawnTerminal(launcherSpace, cwd, launcherSplit, launcherProfile) }}>
        <label>Shell profile<select value={launcherProfile} onChange={event=>setLauncherProfile(event.currentTarget.value)}>{profiles.map(profile=><option value={profile.id}>{profile.marker} · {profile.label}</option>)}</select><small>{profiles.find(profile=>profile.id===launcherProfile)?.capabilities.join(' · ')}</small></label>
        <label>Working directory<div class="path-entry"><input value={cwd} onInput={event => setCwd(event.currentTarget.value)} placeholder="D:\\projects\\my-project" autofocus /><button type="button" onClick={()=>void browseDirectory()}>Browse</button><button type="button" title="Pin directory" onClick={()=>void togglePin(cwd)}>{pinnedCwds.some(item=>item.toLowerCase()===cwd.toLowerCase())?'◆':'◇'}</button></div></label>
        <button class="primary" type="submit">Open {profiles.find(item=>item.id===launcherProfile)?.label || 'terminal'}</button>
      </form>
      {browserPath && <div class="directory-browser"><span>daemon filesystem::{browserPath}</span>{browserParent && <button onClick={()=>void browseDirectory(browserParent)}>..</button>}{browserDirs.map(item=><button onClick={()=>void browseDirectory(item.path)}>{item.name}\\</button>)}</div>}
      {pinnedCwds.length > 0 && <div class="recent-cwds"><span>PINNED</span>{pinnedCwds.map(item => <button title={item} onClick={() => setCwd(item)}><strong>◆ {item.split(/[\\/]/).filter(Boolean).pop()}</strong><small>{item}</small></button>)}</div>}
      {rememberedCwds.length > 0 && <div class="recent-cwds"><span>RECENT & ACTIVE</span>{rememberedCwds.map(item => <button title={item} onClick={() => setCwd(item)}><strong>{item.split(/[\\/]/).filter(Boolean).pop()}</strong><small>{item}</small></button>)}</div>}
    </div>}

    {paletteOpen && <div class="palette-layer" onMouseDown={event => event.target === event.currentTarget && setPaletteOpen(false)}>
      <div class="palette" role="dialog" aria-modal="true" aria-label="Command palette"><input ref={paletteInput} role="combobox" aria-controls="command-results" aria-expanded="true" aria-activedescendant={shownCommands[paletteIndex]?`command-${shownCommands[paletteIndex].id.replaceAll(/[^a-zA-Z0-9_-]/g,'-')}`:undefined} value={paletteQuery} onInput={event => setPaletteQuery(event.currentTarget.value)} onKeyDown={event => {
        if (event.key === 'Escape') setPaletteOpen(false)
        if (event.key === 'ArrowDown') { event.preventDefault(); setPaletteIndex(index => Math.min(index + 1, Math.max(0, shownCommands.length - 1))) }
        if (event.key === 'ArrowUp') { event.preventDefault(); setPaletteIndex(index => Math.max(0, index - 1)) }
        if (event.key === 'Enter') {
          event.preventDefault()
          const command = shownCommands[paletteIndex]
          if (command && runNamedCommand(command.id)) { setPaletteOpen(false); setPaletteQuery('') }
        }
      }} placeholder="Type a command…" autofocus />
        <div id="command-results" role="listbox">{shownCommands.map((command, index) => <button id={`command-${command.id.replaceAll(/[^a-zA-Z0-9_-]/g,'-')}`} role="option" aria-selected={index===paletteIndex} class={index === paletteIndex ? 'active' : ''} disabled={!command.available} title={command.disabledReason} onMouseEnter={() => setPaletteIndex(index)} onClick={() => { if (runNamedCommand(command.id)) { setPaletteOpen(false); setPaletteQuery('') } }}><span><small>{command.category}</small>{command.label}</span>{bindingFor(command.id, keybindings) && <kbd>{displayChord(bindingFor(command.id, keybindings))}</kbd>}</button>)}</div>
      </div>
    </div>}

    {contextMenu && <div class="context-menu" role="menu" aria-label={`Session actions for ${contextMenu.session.name}`} style={{ left: Math.min(contextMenu.x, innerWidth - 205), top: Math.max(4, Math.min(contextMenu.y, innerHeight - 520)) }}>
      <div class="context-title"><span class={`state-dot ${contextMenu.session.state}`} /><strong>{contextMenu.session.name}</strong></div>
      <button onClick={() => runNamedCommand('session.rename')}>Rename</button>
      <button onClick={() => runNamedCommand('session.open')}>Open in focused pane</button>
      {isAgent(contextMenu.session) && <button onClick={() => runNamedCommand('session.pinAttention')}>{contextMenu.session.pinned_attention ? 'Unpin attention' : 'Pin attention'}</button>}
      {['exited', 'crashed'].includes(contextMenu.session.state) && isAgent(contextMenu.session) && <button onClick={() => runNamedCommand('session.resume')}>Resume as new…</button>}
      <button onClick={() => runNamedCommand('session.copyId')}>Copy session ID</button>
      <button onClick={() => runNamedCommand('session.copyCwd')}>Copy working directory</button>
      <button onClick={() => runNamedCommand('session.notes')}>Open session note…</button>
      <button onClick={() => runNamedCommand('session.notesSplit')}>Open session note in split</button>
      <button onClick={() => runNamedCommand('session.openSplitHorizontal')}>Open in split right</button>
      <button onClick={() => runNamedCommand('session.openSplitVertical')}>Open in split below</button>
      <button onClick={() => runNamedCommand('session.reveal')}>Reveal in Explorer</button>
      <button onClick={() => runNamedCommand('session.worktreeCreate')}>Create worktree + terminal…</button>
      <button onClick={() => runNamedCommand('session.worktreesManage')}>Manage worktrees…</button>
      <button onClick={() => runNamedCommand('processes.open')}>Processes and previews…</button>
      <button onClick={() => runNamedCommand('pane.splitHorizontal')}>New terminal in split right</button>
      <button onClick={() => runNamedCommand('pane.splitVertical')}>New terminal in split below</button>
      <button onClick={() => runNamedCommand('session.customSplit')}>New terminal custom in split…</button>
      <button disabled={paneIds.length < 2} onClick={() => runNamedCommand('pane.swapNext')}>Swap pane with next</button>
      <button disabled={paneIds.length < 2} onClick={() => runNamedCommand('pane.zoom')}>{zoomedId?'Restore pane layout':'Zoom pane'}</button>
      <div class="context-subtitle">MOVE TO SPACE</div>
      {spaces.map(space => <button disabled={space.id === contextMenu.session.space_id} onClick={() => runNamedCommand(`session.move(${space.id})`)}>{space.name}</button>)}
      <div class="context-rule" />
      <button onClick={() => runNamedCommand('session.broadcastMembership')}>{contextMenu.session.broadcast ? 'Remove from broadcast' : 'Add to broadcast'}</button>
      <button class="danger" onClick={() => runNamedCommand('session.killImmediate')}>Kill session</button>
    </div>}

    {spaceMenu && <div class="context-menu" role="menu" aria-label={`Space actions for ${spaceMenu.space.name}`} style={{ left: Math.min(spaceMenu.x, innerWidth - 205), top: Math.max(4, Math.min(spaceMenu.y, innerHeight - 310)) }}>
      <div class="context-title"><strong>{spaceMenu.space.name}</strong></div>
      <button onClick={() => runNamedCommand('space.newTerminal')}>New terminal</button>
      <button onClick={() => runNamedCommand('space.newTerminalCustom')}>New terminal custom…</button>
      <button onClick={() => runNamedCommand('space.notes')}>Open space notes…</button>
      <button onClick={() => runNamedCommand('space.notesSplit')}>Open space note in split</button>
      <button onClick={() => runNamedCommand('space.rename')}>Rename space</button>
      <button onClick={() => runNamedCommand('space.settings')}>Space defaults in Settings…</button>
      {spaceMenu.space.id !== 'default' && confirmSpaceDeleteId !== spaceMenu.space.id && <button class="danger" onClick={() => runNamedCommand('space.delete')}>Delete space…</button>}
      {spaceMenu.space.id !== 'default' && confirmSpaceDeleteId === spaceMenu.space.id && <>
        <div class="context-subtitle">DELETE SPACE · SESSION DISPOSITION</div>
        <button onClick={() => { const target = spaceMenu.space; setSpaceMenu(null); void deleteSpace(target, 'move') }}>Move sessions to Main + delete</button>
        <button class="danger" onClick={() => { const target = spaceMenu.space; setSpaceMenu(null); void deleteSpace(target, 'kill') }}>Kill sessions + delete</button>
        <button onClick={() => setConfirmSpaceDeleteId(null)}>Cancel</button>
      </>}
    </div>}

    {emptyMenu && <div class="context-menu" role="menu" style={{ left: Math.min(emptyMenu.x, innerWidth - 250), top: Math.min(emptyMenu.y, innerHeight - 280) }}>
      <div class="context-title"><strong>EMPTY PANE</strong></div>
      <button role="menuitem" onClick={() => { setEmptyMenu(null); void spawnTerminal() }}>New terminal</button>
      <button role="menuitem" onClick={() => { setEmptyMenu(null); openLauncher() }}>New terminal custom…</button>
      <button role="menuitem" disabled={!activeSpace||!spaceNoteTarget(activeSpace)} onClick={() => runNamedCommand('notes.open')}>Open space notes…</button>
      {unpanned.length > 0 && <div class="context-subtitle">ATTACH LIVE SESSION</div>}
      {unpanned.map(session => <button role="menuitem" onClick={() => runNamedCommand(`session.attach(${session.id})`)}><span class={`state-dot ${session.state}`} />{session.name}</button>)}
    </div>}

    {mainMenuOpen && <div class="context-menu main-menu" role="menu" aria-label="swe-mux menu">
      <div class="context-title"><strong>swe-mux menu</strong></div>
      <div class="context-subtitle">WORKSPACE</div>
      <button onClick={() => { setMainMenuOpen(false); runNamedCommand('session.spawnShell') }}>New terminal in current space</button>
      <button onClick={() => { setMainMenuOpen(false); runNamedCommand('session.quickLaunch') }}>New terminal custom…</button>
      <button onClick={() => { setMainMenuOpen(false); runNamedCommand('history.open') }}>Session history</button>
      <button disabled={!activeSpace||!spaceNoteTarget(activeSpace)} onClick={() => runNamedCommand('notes.open')}>Space notes…</button>
      <button disabled={!active} onClick={() => runNamedCommand('session.notes')}>Session note…</button>
      <button disabled={!activeSpace||!spaceNoteTarget(activeSpace)?.terminalSessionId} onClick={() => runNamedCommand('space.notesSplit')}>Space note in split</button>
      <button disabled={!active||['exited','crashed'].includes(active.state)} onClick={() => runNamedCommand('session.notesSplit')}>Session note in split</button>
      <button disabled={!active} onClick={() => runNamedCommand('processes.open')}>Processes and previews…</button>
      <button onClick={() => runNamedCommand('notifications.open')}>Notifications{notificationUnread?` [${notificationUnread} new]`:''}</button>
      <button onClick={() => { setMainMenuOpen(false); runNamedCommand('space.create') }}>Create workspace</button>
      <button onClick={() => { setMainMenuOpen(false); runNamedCommand('broadcast.toggle') }}>{broadcast ? 'Stop broadcast input' : 'Start broadcast input'}</button>
      <div class="context-subtitle">CONFIGURATION</div>
      <button disabled={!(active?.cwd||rememberedCwds[0])} onClick={() => runNamedCommand('settings.project')}>Project settings…</button>
      <button onClick={() => runNamedCommand('usage.open')}>Usage analytics…</button>
      <button onClick={() => runNamedCommand('hooks.open')}>Hooks and notifications…</button>
      <button onClick={() => runNamedCommand('settings.open')}>All Settings…</button>
      <div class="context-subtitle">SHORTCUTS</div>
      <button onClick={() => { setMainMenuOpen(false); runNamedCommand('palette.open') }}>Command palette <span class="menu-hint">ctrl shift p</span></button>
    </div>}

    {sidebarOpen && <button class="sidebar-scrim" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />}

    {renameTarget && <div class="modal-layer" onMouseDown={event => event.target === event.currentTarget && setRenameTarget(null)}>
      <form class="modal rename-modal" onSubmit={event => { event.preventDefault(); void submitRename() }}>
        <div class="modal-heading"><div><span>RENAME::{renameTarget.kind.toUpperCase()}</span><h2>{renameTarget.kind === 'session' ? renameTarget.session.name : renameTarget.space.name}</h2></div><button type="button" aria-label="Close rename" onClick={() => setRenameTarget(null)}>×</button></div>
        <label>name<input value={renameValue} onInput={event => setRenameValue(event.currentTarget.value)} autofocus /></label>
        <div class="modal-footer"><span>enter::save · esc::cancel</span><button type="button" onClick={() => setRenameTarget(null)}>Cancel</button><button class="primary" type="submit" disabled={!renameValue.trim()}>Rename</button></div>
      </form>
    </div>}

    {worktreeCreate && <div class="modal-layer" onMouseDown={event => event.target === event.currentTarget && setWorktreeCreate(null)}>
      <form class="modal worktree-create-modal" onSubmit={event => { event.preventDefault(); void submitWorktree() }}>
        <div class="modal-heading"><div><span>GIT::NEW WORKTREE</span><h2>Create worktree + terminal</h2></div><button type="button" aria-label="Close worktree dialog" onClick={() => setWorktreeCreate(null)}>×</button></div>
        <label>branch<input value={worktreeCreate.branch} onInput={event => {
          const branch = event.currentTarget.value
          setWorktreeCreate(current => current ? {
            ...current, branch,
            path: current.pathEdited ? current.path : `${current.session.cwd}-${branch.replace(/[^a-z0-9._-]+/gi, '-') || 'worktree'}`,
          } : current)
        }} placeholder="feature/my-change" autofocus /></label>
        <label>worktree directory<input value={worktreeCreate.path} onInput={event => setWorktreeCreate(current => current ? { ...current, path: event.currentTarget.value, pathEdited: true } : current)} /></label>
        <div class="modal-note">source::{worktreeCreate.session.cwd}</div>
        <div class="modal-footer"><span>enter::create · esc::cancel</span><button type="button" onClick={() => setWorktreeCreate(null)}>Cancel</button><button class="primary" type="submit" disabled={!worktreeCreate.branch.trim() || !worktreeCreate.path.trim()}>Create</button></div>
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

    {historyOpen && <div class="history-layer" role="dialog" aria-modal="true" aria-label="Agent session history">
      <div class="history-header"><div><span>SESSION ARCHIVE</span><h2>History</h2></div><button onClick={() => setHistoryOpen(false)}>×</button></div>
      <div class="history-body">
        <aside><div class="history-search"><input placeholder="Search agent history…" value={historyQuery} onInput={event => { const query = event.currentTarget.value; setHistoryQuery(query); void loadHistory({ query }) }} />
          <select aria-label="Filter history backend" value={historyBackend} onChange={event => { const backend = event.currentTarget.value; setHistoryBackend(backend); void loadHistory({ backend }) }}><option value="">Claude + Codex</option><option value="claude">Claude</option><option value="codex">Codex</option></select>
          <select aria-label="Filter history project" value={historyProject || ''} onChange={event => { const project = event.currentTarget.value || null; setHistoryProject(project); void loadHistory({ project }) }}><option value="">All projects</option>{historyProjects.map(project => <option value={project.project_id || '__ungrouped__'}>{project.label}</option>)}</select>
          <select aria-label="Filter history state" value={historyState} onChange={event => { const state=event.currentTarget.value;setHistoryState(state);void loadHistory({state}) }}><option value="">All states</option><option value="idle">Completed</option><option value="exited">Exited</option><option value="crashed">Crashed</option></select>
          <select aria-label="Filter history space" value={historySpace} onChange={event => { const space=event.currentTarget.value;setHistorySpace(space);void loadHistory({space}) }}><option value="">All spaces</option>{spaces.map(space=><option value={space.id}>{space.name}</option>)}</select>
          <select aria-label="Filter external history" value={historyExternal} onChange={event => { const external=event.currentTarget.value;setHistoryExternal(external);void loadHistory({external}) }}><option value="">Mux + external</option><option value="false">Mux sessions</option><option value="true">External sessions</option></select>
          <input type="datetime-local" aria-label="History from date" value={historyFrom} onChange={event=>{const from=event.currentTarget.value;setHistoryFrom(from);void loadHistory({from})}} />
          <input type="datetime-local" aria-label="History to date" value={historyTo} onChange={event=>{const to=event.currentTarget.value;setHistoryTo(to);void loadHistory({to})}} />
        </div>
          {historyError && <div class="history-inline-state error" role="alert">{historyError}</div>}
          {!historyLoading && !historyError && history.length === 0 && <div class="history-inline-state">No Claude or Codex history matches these filters.</div>}
          {historyProjects.filter(project => !historyProject || project.project_id === historyProject || (historyProject === '__ungrouped__' && project.project_id === null)).map(project => {
            const key = project.project_id || 'ungrouped'
            const entries = history.filter(entry => (entry.project_id || null) === project.project_id)
            if (!entries.length) return null
            const collapsed = collapsedProjects.has(key)
            return <section class="history-project"><button class="history-project-heading" aria-expanded={!collapsed} onClick={() => setCollapsedProjects(current => { const next = new Set(current); if (next.has(key)) next.delete(key); else next.add(key); return next })}><strong>{collapsed ? '▸' : '▾'} {project.label}</strong><span>{entries.length}</span></button>
              {!collapsed && entries.map(entry => <article class={`history-row ${transcript?.entry.id === entry.id ? 'active' : ''}`}><button onClick={() => void viewHistory(entry)}><strong>[{entry.backend}] {entry.name}</strong><span>{new Date(entry.spawned_at * 1000).toLocaleString()}</span><small>{entry.final_state || entry.exit_reason || 'indexed'}{entry.external ? ' · external' : ''}</small></button><button class={confirmHistoryDelete === entry.id ? 'danger confirming' : 'danger'} aria-label={`Delete history index entry ${entry.name}`} onClick={() => void deleteHistory(entry)}>{confirmHistoryDelete === entry.id ? '✓' : '×'}</button></article>)}
            </section>
          })}
          {historyLoading && <div class="history-inline-state">Loading agent history…</div>}
          {historyNext && !historyLoading && <button class="history-load-more" onClick={() => void loadHistory({ append: true })}>Load more</button>}
        </aside>
        <main>{transcript ? <><div class="transcript-heading"><button class="history-back" onClick={()=>setTranscript(null)}>← Back</button><div><h3>[{transcript.entry.backend}] {transcript.entry.name}</h3><span>{transcript.entry.project_label || 'Ungrouped'} · {transcript.entry.cwd}</span><small>{transcript.entry.exit_reason || transcript.entry.final_state || 'indexed'} · {transcript.entry.model || 'model unavailable'} · {transcript.entry.external ? 'external' : 'mux session'}</small><small>{transcript.entry.context_window ? `context final ${Math.round((transcript.entry.final_context_pct || 0) * 100)}% · peak ${Math.round((transcript.entry.peak_context_pct || 0) * 100)}% · ${transcript.entry.measurement_source || 'native observation'}` : 'context unavailable'} · tokens in {transcript.entry.tokens_in || 0} / out {transcript.entry.tokens_out || 0}</small></div><button class="primary" onClick={() => void resumeHistoryEntry(transcript.entry)}>Resume as new</button></div>
          <div class="messages">{transcript.messages.length ? transcript.messages.map(message => <article class={message.role}><header>{message.role}</header>{message.content.map(block => block.type === 'text' ? <p>{block.text}</p> : <pre>{block.type === 'tool_use' ? `${block.name}\n${JSON.stringify(block.input, null, 2)}` : block.type}</pre>)}</article>) : <div class="no-transcript">No native transcript is available for this session.</div>}</div></> : <div class="history-placeholder"><span>◷</span><strong>Select a session</strong><p>Read its native transcript without resuming it.</p></div>}</main>
      </div>
    </div>}

    {settingsOpen && <Settings cwd={active?.cwd||rememberedCwds[0]} initialSection={settingsSection} onOpenUsage={()=>{setSettingsOpen(false);setUsageOpen(true)}} onClose={() => { setSettingsOpen(false); void refresh(); void loadProfiles() }} />}

    {usageOpen&&<UsageDashboard onClose={()=>setUsageOpen(false)} onConfigure={()=>{setUsageOpen(false);openSettings('Usage analytics')}}/>}

    {processSession && <ProcessPanel session={processSession} onClose={() => setProcessSession(null)} onAttached={(preview, space) => {
      setPreviews(current => ({...current, [preview.id]: preview}))
      setSpaces(items => items.map(item => item.id === space.id ? space : item))
      setLayoutMap(current => ({...current, [space.id]: parseLayout(space.layout)}))
    }} />}

    {notificationsOpen&&<Notifications data={notificationData} onClose={()=>setNotificationsOpen(false)} onOpenSession={sessionId=>{const session=sessions.find(item=>item.id===sessionId);if(!session){setError('The notification session is no longer live.');return}setNotificationsOpen(false);void selectSession(session)}} />}

    {noteTarget && <Notes targetKey={`modal:${noteIdForTarget(noteTarget)}`} cwd={noteTarget.cwd} spaceId={noteTarget.spaceId} sessionId={noteTarget.sessionId} terminalSessionId={noteTarget.terminalSessionId} initialKind={noteTarget.kind} onClose={() => setNoteTarget(null)} onOpenSplit={()=>openNoteInSplit(noteTarget)} onInsert={text => window.dispatchEvent(new CustomEvent('mux:terminal-action', { detail: { sessionId: noteTarget.terminalSessionId, action: 'insertText', text } }))} onCapture={targetKey => window.dispatchEvent(new CustomEvent('mux:terminal-action', { detail: { sessionId: noteTarget.terminalSessionId, action: 'captureSelection', targetKey } }))} />}

    {notificationToast&&<button class="notification-toast" aria-live="assertive" onClick={()=>{setNotificationToast(null);openNotifications()}}><strong>{notificationToast.session_name||'daemon'}</strong><span>{notificationToast.type.replaceAll('_',' ')}</span><small>open notifications</small></button>}

    {error && <div class="toast" onClick={() => setError('')}>{error}<span>×</span></div>}
  </div>
}
