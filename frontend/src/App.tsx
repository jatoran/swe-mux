import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { ComponentChildren, JSX } from 'preact'
import { api, openWebSocket } from './api'
import { TerminalPane } from './TerminalPane'
import { Notes } from './Notes'
import { ProcessPanel, type Preview, type ProcessItem } from './ProcessPanel'
import { detectedServers, type DetectedServer } from './sessionProcesses'
import { PreviewPane } from './PreviewPane'
import { Notifications, type NotificationData, type UiNotification } from './Notifications'
import { UsageDashboard } from './UsageDashboard'
import { ProjectRegistry } from './ProjectRegistry'
import { NotesShelf, type NoteShelfItem } from './NotesShelf'
import { NotesWorkspace } from './NotesWorkspace'
import { AutomationDashboard } from './AutomationDashboard'
import { MicButton, VoicePlayer } from './VoicePlayer'
import { autoplayEnabled, enqueueAutoplay, playClip, setAutoplayEnabled, unlockPlayback } from './voice'
import type { ProjectScope, Session, ShellProfile, Space, VoiceClip, VoiceMode, VoiceStatus } from './types'
import { keyChord } from './keys'
import { Settings } from './Settings'
import { applyTheme, configureCustomTheme, type CustomTheme, type ThemeName } from './theme'
import { bindingFor, displayChord, runCommand, searchCommands, type Command } from './commands'
import { clampContextMenuLeft } from './menuPosition'
import { defaultMobileInputSettings, mobileInputSettings, type MobileInputSettings } from './mobileInput'
import { focusMemoryWith, parseFocusMemory, parseViewPreference, resolveInitialFocus, viewUrl } from './viewState'
import {
  emptyLayout, leaves, noteResourceId, parseLayout, parseNoteResourceId,
  reconcilePreviews, reconcileTerminals, removeLeaf, replaceTerminal, setSplitRatio,
  activateContainingStack, activateNoteWorkspace, activateStackChild, addToStack, dissolveStack, groupTerminalsInStack, hideNoteWorkspace, reorderStack, resolveLayout, setNoteWorkspaceMode, setNoteWorkspaceSize, showNoteWorkspace, splitTerminal, stackForSession, stackTerminal, swapTerminals, terminalIds, visibleTerminalIds, type PaneLayout,
  type PaneNode, type SplitDirection,
} from './layout'

const annotationMoney=new Intl.NumberFormat(undefined,{style:'currency',currency:'USD',maximumFractionDigits:4})

function isAgent(session: Session) {
  return session.backend === 'claude' || session.backend === 'codex'
}

function isEndedSession(session: Session) {
  return session.state === 'exited' || session.state === 'crashed'
}

function sessionName(session:Session):string {
  return session.auto_named!==false&&session.generated_title?session.generated_title:session.name
}

function workingCwd(session:Session):string {
  return session.runtime_cwd||session.spawn_cwd||session.cwd
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
  project_scope_id?:string;repo_group_id?:string;space_id?:string;project_root?:string
  context_window?: number; final_context_pct?: number; peak_context_pct?: number
  tokens_in?: number; tokens_out?: number; model?: string; measurement_source?: string
  auto_named?:number;generated_title?:string
}
type DerivedAnnotation={id:string;tag:string;content:string;provenance:string;resolved_model?:string;confidence?:number;cost_usd?:number;created_at:number}
type Transcript = { entry: HistoryEntry; messages: Array<{ role: string; content: Array<{ type: string; text?: string; name?: string; input?: unknown }> }>;annotations:DerivedAnnotation[] }
type LineageEdge={id:string;parent_run_id:string;child_run_id:string;relation:'resume'|'handoff'|'continuation'|'review';metadata:Record<string,unknown>;created_at:number}
type ReviewPreview={source_run_id:string;source_backend:string;backend:'claude'|'codex';cwd:string;worktree_context:string;prompt:string;relation:'review';preview_token:string}
type ReviewState={entry:HistoryEntry;instructions:string;space:string;preview:ReviewPreview;dirty:boolean;loading:boolean;error:string}
type HandoffState={entry:HistoryEntry;markdown:string;message:string}
type HistoryPage = { items: HistoryEntry[]; next_cursor: string | null }
type HistoryProject = { project_id: string | null; label: string; root?: string; sessions: number; last_activity: number }
type ContextState = { session: Session; x: number; y: number } | null
type SpaceContext = { space: Space; x: number; y: number } | null
type SidebarContext = { x:number;y:number } | null
type NoteContext = { resourceId:string;spaceId:string;x:number;y:number } | null
type RenameTarget = { kind: 'session'; session: Session } | { kind: 'space'; space: Space }
type Worktree = { worktree: string; HEAD?: string; branch?: string; bare?: boolean; detached?: boolean }
type WorktreeState = { session: Session; items: Worktree[] } | null
type WorktreeCreate = { session: Session; branch: string; path: string; pathEdited: boolean } | null
type NoteTarget = {cwd:string;projectScopeId?:string;spaceId:string;sessionId:string|null;terminalSessionId:string|null;kind:'projects'|'spaces'|'sessions';ownerLabel:string;projectLabel?:string}
type SpaceNoteSummary = {id:string;label:string;active:boolean;path:string;revision:string}
type StartupMilestone = 'pane_mounted' | 'socket_open' | 'replay_ready'
type ClientStartupTiming = Partial<Record<'api_response' | StartupMilestone, number>>

function formatStartupMs(value:number):string {
  return value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${Math.round(value)}ms`
}

function startupSummary(session:Session):{label:string;value:number}|null {
  const server=session.startup_timing_ms||{}
  const value=server.first_prompt??server.first_output??server.server_ready
  if(value===undefined)return null
  return {label:server.first_prompt!==undefined?'ready':'boot',value}
}

function startupTimingTitle(session:Session,client:ClientStartupTiming):string {
  const server=session.startup_timing_ms||{}
  const browser=Object.keys(client).length?client:session.client_startup_timing_ms||{}
  const lines=['SESSION STARTUP']
  const add=(label:string,value:number|undefined)=>{if(value!==undefined)lines.push(`${label}: ${formatStartupMs(value)}`)}
  add('project resolution',server.project_resolution)
  add('project config',server.project_config)
  add('profile resolution',server.profile_resolution)
  add('PTY spawn',server.pty_spawn)
  add('registration',server.registration)
  add('server ready (total)',server.server_ready)
  add('first output (total)',server.first_output)
  add('first prompt (total)',server.first_prompt)
  add('API response (browser total)',browser.api_response)
  add('pane mounted (browser total)',browser.pane_mounted)
  add('socket open (browser total)',browser.socket_open)
  add('replay ready (browser total)',browser.replay_ready)
  return lines.join('\n')
}

function historyName(entry:HistoryEntry):string {
  return entry.auto_named!==0&&entry.generated_title?entry.generated_title:entry.name
}

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
  const [lineage,setLineage]=useState<LineageEdge[]>([])
  const [reviewState,setReviewState]=useState<ReviewState|null>(null)
  const [handoffState,setHandoffState]=useState<HandoffState|null>(null)
  const [contextMenu, setContextMenu] = useState<ContextState>(null)
  const [spaceMenu, setSpaceMenu] = useState<SpaceContext>(null)
  const [sidebarMenu,setSidebarMenu]=useState<SidebarContext>(null)
  const [noteMenu,setNoteMenu]=useState<NoteContext>(null)
  const [emptyMenu, setEmptyMenu] = useState<{x:number;y:number} | null>(null)
  const [zoomedId, setZoomedId] = useState<string | null>(null)
  const [keybindings, setKeybindings] = useState<Record<string, string>>({ 'ctrl+alt+t': 'session.spawnShell', 'ctrl+alt+p': 'palette.open' })
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
  const [settingsCwd,setSettingsCwd]=useState<string|null>(null)
  const [mobileNoteId, setMobileNoteId] = useState<string | null>(null)
  const [processSession, setProcessSession] = useState<Session | null>(null)
  const [processViewerOpen,setProcessViewerOpen]=useState(false)
  const [sessionProcesses,setSessionProcesses]=useState<Record<string,ProcessItem[]>>({})
  const [previews, setPreviews] = useState<Record<string, Preview>>({})
  const [notificationData, setNotificationData] = useState<NotificationData>({notifications:[],deliveries:[]})
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const [notificationUnread, setNotificationUnread] = useState(0)
  const [notificationToast, setNotificationToast] = useState<UiNotification | null>(null)
  const [usageOpen, setUsageOpen] = useState(false)
  const [automationOpen,setAutomationOpen]=useState(false)
  const [projectsOpen,setProjectsOpen]=useState(false)
  const [notesShelfOpen,setNotesShelfOpen]=useState(false)
  const [notesShelfHidden,setNotesShelfHidden]=useState(false)
  const [notesShelfReturnId,setNotesShelfReturnId]=useState<string|null>(null)
  const [notesDefaultOpen,setNotesDefaultOpen]=useState<'dock'|'popout'>('dock')
  const [projectScopes,setProjectScopes]=useState<ProjectScope[]>([])
  const [savedSpaceNotes,setSavedSpaceNotes]=useState<SpaceNoteSummary[]>([])
  const [dragSessionId,setDragSessionId]=useState<string|null>(null)
  const [xtermScrollback, setXtermScrollback] = useState(10000)
  const [mobileInput, setMobileInput] = useState<MobileInputSettings>(defaultMobileInputSettings)
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus | null>(null)
  const [profiles, setProfiles] = useState<ShellProfile[]>([])
  const [defaultProfile, setDefaultProfile] = useState('default')
  const [launcherProfile, setLauncherProfile] = useState(localStorage.getItem('mux.lastProfile') || '')
  const [pinnedCwds, setPinnedCwds] = useState<string[]>([])
  const [browserPath, setBrowserPath] = useState<string | null>(null)
  const [browserDirs, setBrowserDirs] = useState<Array<{name:string;path:string}>>([])
  const [browserParent, setBrowserParent] = useState<string | null>(null)
  const [clientStartupTimings,setClientStartupTimings]=useState<Record<string,ClientStartupTiming>>({})
  const clientStartupTimingValues=useRef<Record<string,ClientStartupTiming>>({})
  const startupOrigins=useRef<Record<string,number>>({})
  const spawning = useRef(false)
  const longPressTimer = useRef<number | null>(null)
  const notificationIds = useRef<Set<string>>(new Set())
  const paletteInput = useRef<HTMLInputElement>(null)
  const refreshInFlight = useRef<Promise<void> | null>(null)
  const refreshQueued = useRef(false)
  const layoutRevisions = useRef<Record<string,number>>({})
  const layoutWriteChains = useRef<Record<string,Promise<void>>>({})
  const layoutWriteGeneration = useRef<Record<string,number>>({})
  const requestedView = useRef(parseViewPreference(location.search))
  const focusMemory = useRef(parseFocusMemory(localStorage.getItem('mux.focus.v1')))
  const [focusHydrated,setFocusHydrated]=useState(false)

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
    const fresh=next.notifications.filter(item=>!notificationIds.current.has(`legacy:${item.delivery_id}`))
    const freshAutomation=(next.automation||[]).filter(item=>!notificationIds.current.has(`automation:${item.id}`))
    notificationIds.current=new Set([...next.notifications.map(item=>`legacy:${item.delivery_id}`),...(next.automation||[]).map(item=>`automation:${item.id}`)])
    setNotificationData(next)
    if(announce&&(fresh.length||freshAutomation.length)){setNotificationUnread(count=>count+fresh.length+freshAutomation.length);const latest=fresh[fresh.length-1];const observer=freshAutomation[freshAutomation.length-1];setNotificationToast(observer?{ts:observer.created_at,channel:'ui',delivery_id:observer.id,session_id:observer.session_id,session_name:'automation',type:observer.kind}:latest)}
  }

  const refresh = (): Promise<void> => {
    if (refreshInFlight.current) {
      refreshQueued.current = true
      return refreshInFlight.current
    }
    const operation = (async () => {
      try {
      const [nextSessions, nextSpaces, nextPreviews, nextSpaceNotes] = await Promise.all([
        api<Session[]>('GET', '/api/sessions'), api<Space[]>('GET', '/api/spaces'),
        api<{items:Preview[]}>('GET', '/api/previews'),
        api<SpaceNoteSummary[]>('GET','/api/space-notes'),
      ])
      setSessions(nextSessions)
      setSpaces(nextSpaces)
      for(const space of nextSpaces)layoutRevisions.current[space.id]=space.layout_revision
      setPreviews(Object.fromEntries(nextPreviews.items.map(item => [item.id, item])))
      setSavedSpaceNotes(nextSpaceNotes)
      setLayoutMap(current => {
        const next = { ...current }
        const live = new Set(nextSessions.filter(session => !['exited', 'crashed'].includes(session.state)).map(session => session.id))
        const livePreviews = new Set(nextPreviews.items.map(item => item.id))
        for (const space of nextSpaces) {
          next[space.id] = reconcilePreviews(reconcileTerminals(parseLayout(space.layout), live), livePreviews)
        }
        return next
      })
      setError('')
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause))
      } finally {
        refreshInFlight.current = null
        if (refreshQueued.current) {
          refreshQueued.current = false
          void refresh()
        }
      }
    })()
    refreshInFlight.current = operation
    return operation
  }

  useEffect(() => {
    void refresh()
    void api<{theme:ThemeName;custom_theme:CustomTheme;xterm_scrollback_lines:number;notes_default_open:'dock'|'popout'}&Record<string,unknown>>('GET','/api/config').then(config => { configureCustomTheme(config.custom_theme); applyTheme(config.theme); setXtermScrollback(config.xterm_scrollback_lines);setNotesDefaultOpen(config.notes_default_open);setMobileInput(mobileInputSettings(config)) })
    void loadProfiles()
    void api<VoiceStatus>('GET','/api/voice').then(setVoiceStatus).catch(()=>setVoiceStatus(null))
    void api<{items:ProjectScope[]}>('GET','/api/projects').then(result=>setProjectScopes(result.items))
    void loadNotifications()
    void api<{paths:string[]}>('GET','/api/directories/pins').then(result=>setPinnedCwds(result.paths))
    const loadKeys = () => void api<{ bindings: Record<string, string> }>('GET', '/api/keybindings').then(result => setKeybindings(current => JSON.stringify(current) === JSON.stringify(result.bindings) ? current : result.bindings))
    loadKeys()
    // The /events WebSocket already pushes a refresh on every change, so these intervals
    // are only a safety net. Skip them while the tab is hidden (no point re-fetching and
    // re-rendering a backgrounded tab) and refresh once on return to foreground.
    const tick = () => { if (!document.hidden) void refresh() }
    const keyTick = () => { if (!document.hidden) loadKeys() }
    const timer = setInterval(tick, 15000)
    const keyTimer = setInterval(keyTick, 30000)
    const onVisible = () => { if (!document.hidden) { void refresh(); loadKeys() } }
    document.addEventListener('visibilitychange', onVisible)
    return () => { clearInterval(timer); clearInterval(keyTimer); document.removeEventListener('visibilitychange', onVisible) }
  }, [])

  // Sidebar rows nest the long-running children a session spawned. The daemon's
  // inspector already samples every 2s on its own loop and this read reuses that
  // cached sample, so polling costs no process enumeration; keep it slow anyway
  // because nothing here is latency-sensitive. Fails quiet: an older daemon or a
  // missing psutil simply means no nested rows.
  useEffect(() => {
    const loadProcesses = async () => {
      try {
        const snapshot = await api<{sessions?:Array<{session_id:string;processes?:ProcessItem[]}>}>('GET','/api/processes')
        setSessionProcesses(Object.fromEntries((snapshot.sessions||[]).map(group=>[group.session_id,group.processes||[]])))
      } catch { setSessionProcesses({}) }
    }
    void loadProcesses()
    const tick = () => { if (!document.hidden) void loadProcesses() }
    const timer = setInterval(tick, 8000)
    const onVisible = () => { if (!document.hidden) void loadProcesses() }
    document.addEventListener('visibilitychange', onVisible)
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', onVisible) }
  }, [])

  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: light)')
    const update = () => document.documentElement.dataset.themeSelection === 'system' && applyTheme('system')
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])

  useEffect(() => {
    const viewport = window.visualViewport
    const updateAppHeight = () => {
      const height = Math.round(viewport?.height ?? window.innerHeight)
      document.documentElement.style.setProperty('--app-height', `${height}px`)
    }
    updateAppHeight()
    window.addEventListener('resize', updateAppHeight)
    viewport?.addEventListener('resize', updateAppHeight)
    viewport?.addEventListener('scroll', updateAppHeight)
    return () => {
      window.removeEventListener('resize', updateAppHeight)
      viewport?.removeEventListener('resize', updateAppHeight)
      viewport?.removeEventListener('scroll', updateAppHeight)
      document.documentElement.style.removeProperty('--app-height')
    }
  }, [])

  useEffect(() => {
    let socket: WebSocket | null = null
    let retry: number | undefined
    let refreshTimer: number | undefined
    let hiddenAt: number | null = document.hidden ? Date.now() : null
    const queueRefresh = () => {
      if (refreshTimer !== undefined) return
      refreshTimer = window.setTimeout(() => {
        refreshTimer = undefined
        void refresh()
      }, 100)
    }
    const connect = () => {
      if(retry){clearTimeout(retry);retry=undefined}
      socket = openWebSocket('/events')
      socket.onmessage = message => {
        queueRefresh()
        try {
          const event = JSON.parse(String(message.data))
          if (['notification','notification_created'].includes(event.type)) void loadNotifications(true)
          if (event.type === 'voice_clip_ready' || event.type === 'voice_clip_failed') {
            const clipId = String(event.payload?.clip_id || '')
            window.dispatchEvent(new CustomEvent('mux:voice-clip', { detail: {
              sessionId: event.session_id, clipId,
              status: event.type === 'voice_clip_ready' ? 'ready' : 'failed',
              trigger: event.payload?.trigger,
            } }))
            if (event.type === 'voice_clip_ready' && event.payload?.trigger === 'auto' && clipId && autoplayEnabled()) enqueueAutoplay(clipId)
          }
          if (event.type === 'configuration_changed') void api<VoiceStatus>('GET','/api/voice').then(setVoiceStatus).catch(()=>{})
        } catch { /* malformed events are ignored */ }
      }
      socket.onclose = () => { retry = window.setTimeout(connect, 1500) }
    }
    const reconnect = () => {
      if(socket&&(socket.readyState===WebSocket.OPEN||socket.readyState===WebSocket.CONNECTING)){socket.onclose=null;socket.close()}
      connect()
    }
    const onVisibility = () => {
      if(document.hidden){hiddenAt=Date.now();return}
      const slept=hiddenAt!==null&&Date.now()-hiddenAt>5000
      hiddenAt=null
      if(slept||!socket||socket.readyState!==WebSocket.OPEN)reconnect()
    }
    const onPageShow = (event:PageTransitionEvent) => { if(event.persisted)reconnect() }
    const onOnline = () => { if(!socket||socket.readyState!==WebSocket.OPEN)reconnect() }
    connect()
    document.addEventListener('visibilitychange',onVisibility)
    window.addEventListener('pageshow',onPageShow)
    window.addEventListener('online',onOnline)
    return () => { if (retry) clearTimeout(retry); if(refreshTimer)clearTimeout(refreshTimer);if(socket){socket.onclose=null;socket.close()}document.removeEventListener('visibilitychange',onVisibility);window.removeEventListener('pageshow',onPageShow);window.removeEventListener('online',onOnline) }
  }, [])

  useEffect(()=>{if(!notificationToast)return;const timer=window.setTimeout(()=>setNotificationToast(null),5000);return()=>clearTimeout(timer)},[notificationToast])

  const active = sessions.find(session => session.id === activeId)
  const attention = sessions.filter(session => session.state === 'awaiting').length
  const activeSpace = spaces.find(space => space.id === spaceId)
  const activeLayout = layoutMap[spaceId] || emptyLayout()
  const paneIds = terminalIds(activeLayout).filter(id => sessions.some(session => session.id === id && !['exited', 'crashed'].includes(session.state)))
  const activeStack=activeId?stackForSession(activeLayout,activeId):null
  // Detaching only means something when the layout would actually change: another
  // pane/preview region exists, or the focused terminal shares a tab stack.
  const layoutLeafCount=paneIds.length+leaves(activeLayout,'preview').length
  const activeDetachable=layoutLeafCount>1||(!!activeStack&&activeStack.children.length>1)
  const unpanned = sessions.filter(session => session.space_id === spaceId && !['exited', 'crashed'].includes(session.state) && !paneIds.includes(session.id))
  const focusedOutsideLayout=!!active&&!['exited','crashed'].includes(active.state)&&active.space_id===spaceId&&!paneIds.includes(active.id)

  useEffect(() => {
    if (focusHydrated || spaces.length === 0) return
    const visibleBySpace=Object.fromEntries(spaces.map(space=>[
      space.id,
      visibleTerminalIds(layoutMap[space.id]||parseLayout(space.layout)),
    ]))
    const selected=resolveInitialFocus(sessions,spaces.map(space=>space.id),visibleBySpace,requestedView.current,focusMemory.current)
    setSpaceId(selected.spaceId)
    setActiveId(selected.sessionId)
    setFocusHydrated(true)
  },[focusHydrated,sessions,spaces,layoutMap])

  useEffect(() => {
    if(!focusHydrated)return
    const session=sessions.find(item=>item.id===activeId&&item.space_id===spaceId&&!isEndedSession(item))
    focusMemory.current=focusMemoryWith(focusMemory.current,spaceId,session?.id||null)
    localStorage.setItem('mux.focus.v1',JSON.stringify(focusMemory.current))
    const next=viewUrl(location.href,spaceId,session?.id||null)
    if(`${location.pathname}${location.search}${location.hash}`!==next)window.history.replaceState(window.history.state,'',next)
  },[focusHydrated,spaceId,activeId,sessions])

  useEffect(() => {
    if(!focusHydrated)return
    const live = sessions.filter(session => session.space_id === spaceId && !['exited', 'crashed'].includes(session.state))
    if (zoomedId && !live.some(session => session.id === zoomedId)) setZoomedId(null)
    if (live.some(session => session.id === activeId)) return
    const liveIds = new Set(live.map(session => session.id))
    const nextId = visibleTerminalIds(activeLayout).find(id => liveIds.has(id))
      ?? terminalIds(activeLayout).find(id => liveIds.has(id))
      ?? live[0]?.id
      ?? null
    if (nextId === activeId) return
    setActiveId(nextId)
    if (nextId && terminalIds(activeLayout).includes(nextId)) {
      setLayoutMap(current => ({
        ...current,
        [spaceId]: activateContainingStack(current[spaceId] ?? activeLayout, nextId),
      }))
    }
  }, [focusHydrated,sessions, spaceId, activeId, zoomedId, layoutMap])
  const rememberedCwds = useMemo(() => {
    const stored = JSON.parse(localStorage.getItem('mux.recentCwds') || '[]') as string[]
    return [...new Set([active?.runtime_cwd||active?.spawn_cwd||active?.cwd, ...sessions.map(session => session.runtime_cwd||session.spawn_cwd||session.cwd), ...stored].filter(Boolean))] as string[]
  }, [sessions, active?.runtime_cwd,active?.spawn_cwd,active?.cwd])

  const openSettings = (section='General',cwdOverride?:string) => { setSettingsSection(section);setSettingsCwd(cwdOverride||null); setSettingsOpen(true); setMainMenuOpen(false); setSpaceMenu(null) }
  const scopeLabel=(scopeId?:string)=>projectScopes.find(item=>item.id===scopeId)?.label
  const spaceNoteTarget = (space:Space):NoteTarget => {
    const session=(active?.space_id===space.id?active:undefined)||sessions.find(item=>item.space_id===space.id&&!['exited','crashed'].includes(item.state))||sessions.find(item=>item.space_id===space.id)
    const terminalSessionId=session&&!['exited','crashed'].includes(session.state)?session.id:null
    return {cwd:'',spaceId:space.id,sessionId:null,terminalSessionId,kind:'spaces',ownerLabel:space.name}
  }
  const sessionNoteTarget = (session:Session):NoteTarget|null => !isAgent(session)?null
    : {cwd:session.run_cwd||session.cwd,projectScopeId:session.run_project_scope_id||session.project_scope_id,spaceId:session.space_id,sessionId:session.agent_run_id||session.id,
      terminalSessionId:['exited','crashed'].includes(session.state)?null:session.id,kind:'sessions',ownerLabel:sessionName(session),projectLabel:session.project_label||scopeLabel(session.run_project_scope_id||session.project_scope_id)}
  const projectNoteTarget=(session:Session,current=!isAgent(session)):NoteTarget|null=>{
    const projectScopeId=current?session.runtime_project_scope_id:(session.run_project_scope_id||session.spawn_project_scope_id||session.project_scope_id)
    const targetCwd=current?(session.runtime_cwd||session.spawn_cwd||session.cwd):(session.run_cwd||session.spawn_cwd||session.cwd)
    const currentLabel=targetCwd.split(/[\\/]/).filter(Boolean).pop()
    const projectLabel=current?scopeLabel(projectScopeId)||currentLabel:(session.project_label||session.spawn_project_label||scopeLabel(projectScopeId))
    return projectScopeId?{cwd:targetCwd,projectScopeId,spaceId:session.space_id,sessionId:null,terminalSessionId:['exited','crashed'].includes(session.state)?null:session.id,kind:'projects',ownerLabel:projectLabel||targetCwd,projectLabel:projectLabel||targetCwd}:null
  }
  const noteIdForTarget = (target:NoteTarget) => {
    const id=target.kind==='projects'?target.projectScopeId:target.kind==='spaces'?target.spaceId:target.sessionId
    return id?noteResourceId(target.kind,id):null
  }
  const noteIsOpen = (target:NoteTarget) => {
    const id=noteIdForTarget(target)
    return !!id&&leaves(layoutMap[target.spaceId]||emptyLayout(),'note').some(leaf=>leaf.id===id)
  }
  const noteTargetForResource = (resourceId:string,targetSpace=spaceId):NoteTarget|null => {
    const identity=parseNoteResourceId(resourceId)
    if(identity?.kind==='spaces'){
      const space=spaces.find(item=>item.id===identity.id)
      return space?spaceNoteTarget(space):null
    }
    if(identity?.kind==='sessions'){
      const session=sessions.find(item=>item.id===identity.id||item.agent_run_id===identity.id)
      const target=session?sessionNoteTarget(session):null
      return target?{...target,spaceId:targetSpace,terminalSessionId:session?.space_id===targetSpace?target.terminalSessionId:null}:{cwd:'',spaceId:targetSpace,sessionId:identity.id,terminalSessionId:null,kind:'sessions',ownerLabel:'agent note'}
    }
    if(identity?.kind==='projects'){
      const scope=projectScopes.find(item=>item.id===identity.id)
      const matches=sessions.filter(item=>item.runtime_project_scope_id===identity.id||item.run_project_scope_id===identity.id||item.spawn_project_scope_id===identity.id||item.project_scope_id===identity.id)
      const session=matches.find(item=>item.id===activeId&&item.space_id===targetSpace)||matches.find(item=>item.space_id===targetSpace)||matches[0]
      if(session){
        const target=projectNoteTarget(session,session.runtime_project_scope_id===identity.id)
        if(target)return {...target,cwd:scope?.root||target.cwd,projectScopeId:identity.id,spaceId:targetSpace,terminalSessionId:session.space_id===targetSpace?target.terminalSessionId:null,ownerLabel:scope?.label||target.ownerLabel,projectLabel:scope?.label||target.projectLabel}
      }
      const terminal=sessions.find(item=>item.space_id===targetSpace&&!['exited','crashed'].includes(item.state))
      return {cwd:scope?.root||'',projectScopeId:identity.id,spaceId:targetSpace,sessionId:null,terminalSessionId:terminal?.id||null,kind:'projects',ownerLabel:scope?.label||'project note',projectLabel:scope?.label}
    }
    return null
  }
  const preferredNotesMode=(targetSpace:string):'dock'|'popout'=>spaces.find(space=>space.id===targetSpace)?.notes_open_mode||notesDefaultOpen
  const openNoteDefault=(target:NoteTarget)=>{
    const resourceId=noteIdForTarget(target)
    const current=resolveLayout(layoutMap[target.spaceId],spaces.find(space=>space.id===target.spaceId)?.layout)
    const mode=noteIsOpen(target)&&current.note_workspace.visible?current.note_workspace.mode:preferredNotesMode(target.spaceId)
    if(resourceId)void showNotesForTarget(target,mode)
  }
  const openSpaceNotes = (space:Space) => {
    openNoteDefault(spaceNoteTarget(space))
  }
  const openSessionNotes = (session:Session) => {
    const target=sessionNoteTarget(session)
    if(!target){void openProjectNotes(session,true);return}
    setActiveId(session.id);openNoteDefault(target)
  }
  const openProjectNotes=async(session:Session,current=!isAgent(session))=>{
    let target:NoteTarget|null=null
    if(current){
      try{
        const cwd=workingCwd(session)
        const scope=await api<ProjectScope>('POST','/api/projects/resolve',{cwd,session_id:session.id})
        setProjectScopes(items=>items.some(item=>item.id===scope.id)?items:[...items,scope])
        target={cwd:scope.root,projectScopeId:scope.id,spaceId:session.space_id,sessionId:null,terminalSessionId:session.id,kind:'projects',ownerLabel:scope.label,projectLabel:scope.label}
      }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    }else target=projectNoteTarget(session,current)
    if(target){setActiveId(session.id);openNoteDefault(target)}
    else{setError('No project is available for this terminal location.');setContextMenu(null)}
  }
  const openProjectScopeNote=(scope:ProjectScope)=>{setProjectScopes(items=>items.some(item=>item.id===scope.id)?items:[...items,scope]);setProjectsOpen(false);openNoteDefault({cwd:scope.root,projectScopeId:scope.id,spaceId:activeSpace?.id||'default',sessionId:null,terminalSessionId:active?.id||null,kind:'projects',ownerLabel:scope.label,projectLabel:scope.label})}
  const openNotesShelf=()=>{if(activeLayout.note_workspace.visible)void updateLayout(spaceId,hideNoteWorkspace(activeLayout));setNotesShelfOpen(true);setNotesShelfHidden(false);setNotesShelfReturnId(null);setMainMenuOpen(false);setProjectsOpen(false);setSpaceMenu(null);setContextMenu(null)}
  const openShelfNote=(item:NoteShelfItem)=>{
    if(!item.openable||!item.kind){setError('This recovered file is not linked to a durable note owner. Inspect it from Projects.');return}
    let target:NoteTarget
    if(item.kind==='spaces'){
      const space=spaces.find(candidate=>candidate.id===item.identity)
      const terminal=sessions.find(candidate=>candidate.space_id===item.identity&&!['exited','crashed'].includes(candidate.state))
      target={cwd:'',spaceId:item.identity,sessionId:null,terminalSessionId:terminal?.id||null,kind:'spaces',ownerLabel:space?.name||item.owner_label}
    }else if(item.kind==='projects'){
      const terminal=sessions.find(candidate=>(candidate.runtime_project_scope_id||candidate.run_project_scope_id||candidate.project_scope_id)===item.project_scope_id&&!['exited','crashed'].includes(candidate.state))
      target={cwd:item.project_root||'',projectScopeId:item.project_scope_id||item.identity,spaceId:terminal?.space_id||activeSpace?.id||'default',sessionId:null,terminalSessionId:terminal?.id||null,kind:'projects',ownerLabel:item.owner_label,projectLabel:item.project_label||item.owner_label}
    }else{
      const terminal=sessions.find(candidate=>(candidate.agent_run_id||candidate.id)===item.identity&&!['exited','crashed'].includes(candidate.state))
      target={cwd:item.project_root||'',projectScopeId:item.project_scope_id||undefined,spaceId:terminal?.space_id||item.space_id||'default',sessionId:item.identity,terminalSessionId:terminal?.id||null,kind:'sessions',ownerLabel:item.owner_label,projectLabel:item.project_label||undefined}
    }
    const resourceId=noteIdForTarget(target)
    setNotesShelfHidden(true);setNotesShelfReturnId(resourceId)
    openNoteDefault(target)
  }
  const openNotifications = () => { setNotificationsOpen(true);setNotificationUnread(0);setMainMenuOpen(false);void loadNotifications() }

  useEffect(() => {
    if (!contextMenu && !spaceMenu && !sidebarMenu && !noteMenu && !emptyMenu && !mainMenuOpen && !renameTarget && !worktreeCreate) return
    const dismissEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      setContextMenu(null)
      setSpaceMenu(null)
      setSidebarMenu(null)
      setNoteMenu(null)
      setEmptyMenu(null)
      setMainMenuOpen(false)
      setRenameTarget(null)
      setWorktreeCreate(null)
    }
    window.addEventListener('keydown', dismissEscape, true)
    return () => window.removeEventListener('keydown', dismissEscape, true)
  }, [contextMenu, spaceMenu, sidebarMenu, noteMenu, emptyMenu, mainMenuOpen, renameTarget, worktreeCreate])

  useEffect(() => {
    if (!contextMenu && !spaceMenu && !sidebarMenu && !noteMenu && !emptyMenu && !mainMenuOpen) return
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
  }, [contextMenu, spaceMenu, sidebarMenu, noteMenu, emptyMenu, mainMenuOpen])

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

  const spawnTerminal = async (targetSpace = spaceId, targetCwd?: string, split: false | SplitDirection | 'stack' = false, profileId?: string, targetSessionId?: string) => {
    if (spawning.current) return
    spawning.current = true
    const startupOrigin=performance.now()
    try {
      const resolvedCwd = targetCwd?.trim() || undefined
      const next = await api<Session>('POST', '/api/sessions', {
        backend: 'shell', space: targetSpace, cwd: resolvedCwd || undefined,
        profile_id: profileId || undefined,
      })
      startupOrigins.current[next.id]=startupOrigin
      const browserTiming={api_response:performance.now()-startupOrigin}
      clientStartupTimingValues.current[next.id]=browserTiming
      setClientStartupTimings(current=>({...current,[next.id]:browserTiming}))
      if (resolvedCwd) rememberCwd(resolvedCwd)
      if (profileId) { localStorage.setItem('mux.lastProfile',profileId); setLauncherProfile(profileId) }
      setSessions(items => [...items, next])
      setSpaceId(targetSpace)
      setActiveId(next.id)
      const currentLayout = layoutMap[targetSpace] || emptyLayout()
      const focused = targetSessionId ?? (targetSpace === spaceId ? activeId : terminalIds(currentLayout)[0] || null)
      const nextLayout = split
        ? split==='stack'&&focused?stackTerminal(currentLayout,focused,next.id)
          : splitTerminal(currentLayout, focused, next.id, split as SplitDirection)
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

  const openLauncher = (targetSpace = spaceId, split: false | SplitDirection = false, initialCwd?:string) => {
    setLauncherSpace(targetSpace)
    setLauncherSplit(split)
    setCwd(initialCwd ?? spaces.find(item=>item.id===targetSpace)?.default_cwd ?? '')
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
    setRenameValue(target.kind === 'session' ? sessionName(target.session) : target.space.name)
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
    setContextMenu(null)
    const currentLayout = resolveLayout(
      layoutMap[session.space_id],
      spaces.find(space => space.id === session.space_id)?.layout,
    )
    let nextLayout = removeLeaf(currentLayout, 'terminal', session.id)
    const surviving = sessions.filter(item => item.id !== session.id && item.space_id === session.space_id && !['exited', 'crashed'].includes(item.state))
    const survivingIds = new Set(surviving.map(item => item.id))
    const nextActiveId = activeId === session.id
      ? visibleTerminalIds(nextLayout).find(id => survivingIds.has(id))
        ?? terminalIds(nextLayout).find(id => survivingIds.has(id))
        ?? surviving[0]?.id
        ?? null
      : activeId
    if (nextActiveId && terminalIds(nextLayout).includes(nextActiveId)) {
      nextLayout = activateContainingStack(nextLayout, nextActiveId)
    }
    setSessions(items => items.filter(item => item.id !== session.id))
    delete startupOrigins.current[session.id]
    delete clientStartupTimingValues.current[session.id]
    setClientStartupTimings(current=>{const next={...current};delete next[session.id];return next})
    if (activeId === session.id) setActiveId(nextActiveId)
    if (zoomedId === session.id) setZoomedId(null)
    await updateLayout(session.space_id, nextLayout)
    await refresh()
  }

  const requestKill = (session: Session) => {
    if (confirmKillId === session.id) void killNow(session)
    else setConfirmKillId(session.id)
  }

  const updateLayout = async (targetSpace: string, layout: PaneLayout) => {
    setLayoutMap(current => ({ ...current, [targetSpace]: layout }))
    const generation=(layoutWriteGeneration.current[targetSpace]||0)+1
    layoutWriteGeneration.current[targetSpace]=generation
    const previous=layoutWriteChains.current[targetSpace]||Promise.resolve()
    const operation=previous.catch(()=>undefined).then(async()=>{
      const revision=layoutRevisions.current[targetSpace]??spaces.find(space=>space.id===targetSpace)?.layout_revision??0
      try {
        const updated = await api<Space>('PATCH', `/api/spaces/${targetSpace}`, { layout, layout_revision: revision })
        layoutRevisions.current[targetSpace]=updated.layout_revision
        setSpaces(items => items.map(item => item.id === updated.id ? updated : item))
        if(layoutWriteGeneration.current[targetSpace]===generation){
          setLayoutMap(current => ({ ...current, [targetSpace]: parseLayout(updated.layout) }))
        }
      } catch (cause) {
        await refresh()
        const message = cause instanceof Error ? cause.message : String(cause)
        setError(message.includes('stale layout revision') ? 'Layout changed in another client; reloaded the current layout.' : message)
      }
    })
    layoutWriteChains.current[targetSpace]=operation
    await operation
    if(layoutWriteChains.current[targetSpace]===operation)delete layoutWriteChains.current[targetSpace]
  }

  const showNotesForTarget = async (target:NoteTarget,mode:'dock'|'popout') => {
    const resourceId=noteIdForTarget(target)
    const targetSpace=spaces.some(space=>space.id===target.spaceId)?target.spaceId:(activeSpace?.id||spaces[0]?.id)
    if(!resourceId||!targetSpace){setError('A live space is required to open this note.');return}
    const current=resolveLayout(layoutMap[targetSpace],spaces.find(space=>space.id===targetSpace)?.layout)
    setSpaceId(targetSpace);if(target.terminalSessionId)setActiveId(target.terminalSessionId);setMobileNoteId(resourceId)
    setContextMenu(null);setSpaceMenu(null);setNoteMenu(null);setMainMenuOpen(false);setEmptyMenu(null)
    await updateLayout(targetSpace,showNoteWorkspace(current,resourceId,mode))
  }

  const showNoteResource=(resourceId:string,targetSpace:string,mode:'dock'|'popout')=>{
    const target=noteTargetForResource(resourceId,targetSpace)
    if(!target){setError('This note is no longer linked to a durable owner.');setNoteMenu(null);return}
    void showNotesForTarget(target,mode)
  }

  const openNoteContext=(resourceId:string,targetSpace:string,x:number,y:number)=>{
    setContextMenu(null);setSpaceMenu(null);setSidebarMenu(null);setMainMenuOpen(false)
    setNoteMenu({resourceId,spaceId:targetSpace,x,y})
  }

  const openProcessViewer=(session:Session|null=null)=>{
    setProcessSession(session);setProcessViewerOpen(true);setContextMenu(null);setSidebarMenu(null);setMainMenuOpen(false)
  }

  const removeWorkspaceNote = async (targetSpace:string,resourceId:string) => {
    setMobileNoteId(current=>current===resourceId?null:current)
    const current=resolveLayout(layoutMap[targetSpace],spaces.find(space=>space.id===targetSpace)?.layout)
    await updateLayout(targetSpace,removeLeaf(current,'note',resourceId))
    if(notesShelfReturnId===resourceId){setNotesShelfHidden(false);setNotesShelfReturnId(null)}
  }

  const hideActiveNotesWorkspace=()=>{
    setMobileNoteId(null)
    void updateLayout(spaceId,hideNoteWorkspace(activeLayout))
    if(notesShelfOpen&&notesShelfHidden){setNotesShelfHidden(false);setNotesShelfReturnId(null)}
  }

  const changeNotesWorkspaceMode=(mode:'dock'|'popout')=>{
    void updateLayout(spaceId,setNoteWorkspaceMode(activeLayout,mode))
  }

  const selectSession = async (session: Session) => {
    const current = resolveLayout(layoutMap[session.space_id],spaces.find(item=>item.id===session.space_id)?.layout)
    const isPaned=terminalIds(current).includes(session.id)
    setSpaceId(session.space_id)
    setActiveId(session.id)
    setSidebarOpen(false)
    if(isPaned)await updateLayout(session.space_id,activateContainingStack(current,session.id))
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
    setLineage([])
    try {
      setTranscript(await api<Transcript>('GET', `/api/history/${entry.id}/transcript`))
      api<{items:LineageEdge[]}>('GET',`/api/lineage?run_id=${encodeURIComponent(entry.id)}`).then(result=>setLineage(result.items)).catch(()=>setLineage([]))
    }
    catch (cause) { setTranscript(null); setHistoryError(cause instanceof Error ? cause.message : String(cause)) }
  }

  const openHandoff = async (entry:HistoryEntry) => {
    setHistoryError('')
    try {
      const result=await api<{markdown:string}>('GET',`/api/history/${entry.id}/handoff`)
      setHandoffState({entry,markdown:result.markdown,message:'Review this export before using it as agent context.'})
    } catch(cause){setHistoryError(cause instanceof Error?cause.message:String(cause))}
  }

  const previewSecondOpinion = async (entry:HistoryEntry,instructions='',targetSpace=entry.space_id||spaceId) => {
    setHistoryError('')
    try {
      const result=await api<{preview:ReviewPreview}>('POST',`/api/history/${entry.id}/second-opinion`,{confirm:false,instructions})
      setReviewState({entry,instructions,space:targetSpace,preview:result.preview,dirty:false,loading:false,error:''})
    } catch(cause){setHistoryError(cause instanceof Error?cause.message:String(cause))}
  }

  const refreshSecondOpinion = async () => {
    if(!reviewState)return
    setReviewState(current=>current?{...current,loading:true,error:''}:current)
    try{
      const result=await api<{preview:ReviewPreview}>('POST',`/api/history/${reviewState.entry.id}/second-opinion`,{confirm:false,instructions:reviewState.instructions})
      setReviewState(current=>current?{...current,preview:result.preview,dirty:false,loading:false,error:''}:current)
    }catch(cause){setReviewState(current=>current?{...current,loading:false,error:cause instanceof Error?cause.message:String(cause)}:current)}
  }

  const confirmSecondOpinion = async () => {
    if(!reviewState||reviewState.dirty||reviewState.loading)return
    setReviewState(current=>current?{...current,loading:true,error:''}:current)
    try{
      const result=await api<{session:Session}>('POST',`/api/history/${reviewState.entry.id}/second-opinion`,{confirm:true,preview_token:reviewState.preview.preview_token,instructions:reviewState.instructions,backend:reviewState.preview.backend,space:reviewState.space,target_session_id:activeId})
      setReviewState(null);setHistoryOpen(false);await refresh();setSpaceId(result.session.space_id);setActiveId(result.session.id)
    }catch(cause){setReviewState(current=>current?{...current,loading:false,error:cause instanceof Error?cause.message:String(cause)}:current)}
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

  const effectiveVoiceMode = (session: Session): VoiceMode => {
    const mode = session.voice_mode
    if (mode === 'off' || mode === 'on_demand' || mode === 'auto') return mode
    return voiceStatus?.enabled ? voiceStatus.default_mode : 'off'
  }
  const setVoiceMode = (session: Session, mode: VoiceMode) =>
    api<Session>('PATCH', `/api/sessions/${session.id}`, { voice_mode: mode }).then(updateSession).catch(cause => setError(cause instanceof Error ? cause.message : String(cause)))
  const cycleVoiceMode = (session: Session) => {
    const order: VoiceMode[] = ['off', 'on_demand', 'auto']
    void setVoiceMode(session, order[(order.indexOf(effectiveVoiceMode(session)) + 1) % order.length])
  }
  const voiceModeLabel = (mode: VoiceMode) => mode === 'on_demand' ? 'on demand' : mode === 'auto' ? 'auto on reply' : 'off'
  const speakLastReply = async (session: Session) => {
    unlockPlayback()
    try {
      const clip = await api<VoiceClip>('POST', `/api/sessions/${session.id}/voice/generate`)
      if (clip?.id) void playClip(clip.id).catch(() => {})
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
    setWorktreeCreate({ session, branch: '', path: `${workingCwd(session)}-worktree`, pathEdited: false })
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
    const items = await api<Worktree[]>('GET', `/api/git/worktrees?cwd=${encodeURIComponent(workingCwd(session))}`)
    setContextMenu(null)
    setWorktrees({ session, items })
  }

  const removeWorktree = async (path: string) => {
    if (!worktrees) return
    if (confirmWorktreeRemove !== path) {
      setConfirmWorktreeRemove(path)
      return
    }
    await api('DELETE', '/api/git/worktrees', { cwd: workingCwd(worktrees.session), path })
    const items = worktrees.items.filter(item => item.worktree !== path)
    setWorktrees({ ...worktrees, items })
    setConfirmWorktreeRemove(null)
  }

  const commandSession = contextMenu?.session || active
  const commandSpace = spaceMenu?.space || activeSpace
  const commands: Command[] = [
    { id: 'palette.open', label: 'Open command palette', category: 'view', available: true, run: () => setPaletteOpen(true) },
    { id: 'session.spawnShell', label: 'New terminal in current space', category: 'session', available: true, run: () => void spawnTerminal() },
    { id: 'session.quickLaunch', label: 'New terminal custom…', category: 'session', available: true, run: () => openLauncher() },
    { id: 'space.create', label: 'Create space', category: 'space', available: true, run: () => void createSpace() },
    { id: 'history.open', label: 'Browse session history', category: 'view', available: true, run: () => void showHistory() },
    { id: 'projects.open', label: 'Browse project registry', category: 'view', available: true, run: () => {setProjectsOpen(true);setMainMenuOpen(false)} },
    { id: 'notes.shelf', label: 'Browse all notes', category: 'view', available: true, run: openNotesShelf },
    { id: 'settings.open', label: 'Open Settings', category: 'view', available: true, run: () => openSettings() },
    { id: 'settings.project', label: 'Open current project settings', category: 'view', available: !!(active||rememberedCwds[0]), disabledReason: 'No project directory is available', run: () => openSettings('Current project') },
    { id: 'usage.open', label: 'Open usage analytics', category: 'view', available: true, run: () => {setUsageOpen(true);setMainMenuOpen(false)} },
    { id: 'hooks.open', label: 'Open Automation', category: 'view', available: true, run: () => {setAutomationOpen(true);setMainMenuOpen(false)} },
    { id: 'notifications.open', label: `Open notifications${notificationUnread?` (${notificationUnread} new)`:''}`, category: 'view', available: true, run: openNotifications },
    { id: 'notes.open', label: 'Open current space note', category: 'view', available: !!activeSpace, disabledReason: 'No space selected', run: () => activeSpace&&openSpaceNotes(activeSpace) },
    { id: 'session.notes', label: 'Open selected agent-run note', category: 'view', available: !!commandSession&&isAgent(commandSession), disabledReason: 'Session notes exist only for Claude and Codex runs', run: () => commandSession&&openSessionNotes(commandSession) },
    { id: 'session.projectNote', label: `Open selected ${commandSession&&isAgent(commandSession)?'run':'current'} project note`, category: 'view', available: !!commandSession, disabledReason: 'No session selected', run: () => commandSession&&void openProjectNotes(commandSession) },
    { id: 'session.currentProjectNote', label: 'Open current runtime project note', category: 'view', available: !!commandSession?.runtime_cwd_live, disabledReason: 'Live cwd telemetry is unavailable', run: () => commandSession&&void openProjectNotes(commandSession,true) },
    { id: 'space.notes', label: 'Open selected space note', category: 'view', available: !!commandSpace, disabledReason: 'No space selected', run: () => commandSpace&&openSpaceNotes(commandSpace) },
    { id: 'session.notesSplit', label: 'Dock selected agent note', category: 'pane', available: !!commandSession&&isAgent(commandSession), disabledReason: 'A Claude or Codex run is required', run: () => {const target=commandSession&&sessionNoteTarget(commandSession);if(target)void showNotesForTarget(target,'dock')} },
    { id: 'space.notesSplit', label: 'Dock selected space note', category: 'pane', available: !!commandSpace, disabledReason: 'A space is required', run: () => {const target=commandSpace&&spaceNoteTarget(commandSpace);if(target)void showNotesForTarget(target,'dock')} },
    { id: 'processes.open', label: 'Inspect selected session processes and previews', category: 'view', available: !!commandSession, disabledReason: 'No session selected', run: () => {if(commandSession)openProcessViewer(commandSession)} },
    { id: 'processes.all', label: 'Open unified process viewer', category: 'view', available: true, run: () => openProcessViewer() },
    { id: 'terminal.find', label: 'Find in focused terminal', category: 'terminal', available: !!active, disabledReason: 'No focused terminal', run: () => window.dispatchEvent(new CustomEvent('mux:terminal-find', { detail: activeId })) },
    ...(['copy', 'paste', 'selectAll', 'clear'] as const).map((action): Command => ({
      id: `terminal.${action}`, label: `${action === 'selectAll' ? 'Select all' : action[0].toUpperCase() + action.slice(1)} in focused terminal`,
      category: 'clipboard', available: !!active, disabledReason: 'No focused terminal',
      run: () => window.dispatchEvent(new CustomEvent('mux:terminal-action', { detail: { sessionId: activeId, action } })),
    })),
    { id: 'session.kill', label: active && isEndedSession(active) ? 'Remove focused session from sidebar' : 'Kill focused session', category: 'session', available: !!active, disabledReason: 'No focused session', run: () => active && requestKill(active) },
    { id: 'session.killImmediate', label: commandSession && isEndedSession(commandSession) ? 'Remove selected session from sidebar' : 'Kill selected session immediately', category: 'session', available: !!commandSession, disabledReason: 'No session selected', run: () => commandSession && void killNow(commandSession) },
    { id: 'session.pinAttention', label: active?.pinned_attention ? 'Unpin focused session attention' : 'Pin focused session attention', category: 'session', available: !!active && isAgent(active), disabledReason: 'Attention pinning requires a focused agent', run: () => active && void api<Session>('PATCH', `/api/sessions/${active.id}`, { pin: !active.pinned_attention }).then(updateSession) },
    { id: 'voice.cycleMode', label: `Read aloud: cycle focused session mode${active && isAgent(active) ? ` (now ${voiceModeLabel(effectiveVoiceMode(active))})` : ''}`, category: 'voice', available: !!active && isAgent(active) && !!voiceStatus?.enabled, disabledReason: 'Read aloud requires a focused agent and TTS enabled in Settings', run: () => { if (active) cycleVoiceMode(active); setContextMenu(null) } },
    { id: 'voice.speak', label: 'Read aloud: speak focused session’s last reply', category: 'voice', available: !!active && isAgent(active) && !!voiceStatus?.enabled, disabledReason: 'Read aloud requires a focused agent and TTS enabled in Settings', run: () => { if (active) void speakLastReply(active); setContextMenu(null) } },
    { id: 'voice.autoplayDevice', label: `Read aloud: turn device autoplay ${autoplayEnabled() ? 'off' : 'on'}`, category: 'voice', available: !!voiceStatus?.enabled, disabledReason: 'Enable read aloud in Settings first', run: () => { setAutoplayEnabled(!autoplayEnabled()); setContextMenu(null) } },
    { id: 'session.open', label: 'Open selected session in focused pane', category: 'session', available: !!commandSession && !['exited', 'crashed'].includes(commandSession.state), disabledReason: 'No live session selected', run: () => commandSession && void selectSession(commandSession) },
    { id: 'session.rename', label: 'Rename selected session', category: 'session', available: !!commandSession, disabledReason: 'No session selected', run: () => commandSession && openRename({ kind: 'session', session: commandSession }) },
    { id: 'session.copyId', label: 'Copy selected session ID', category: 'clipboard', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) void navigator.clipboard.writeText(commandSession.id).catch(() => setError('Clipboard access was blocked.')) ; setContextMenu(null) } },
    { id: 'session.copyCwd', label: 'Copy selected working directory', category: 'clipboard', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) void navigator.clipboard.writeText(workingCwd(commandSession)).catch(() => setError('Clipboard access was blocked.')); setContextMenu(null) } },
    { id: 'session.openSplitHorizontal', label: 'Open selected session in split right', category: 'pane', available: !!commandSession && !['exited', 'crashed'].includes(commandSession.state), disabledReason: 'No live session selected', run: () => commandSession && void openInSplit(commandSession, 'horizontal') },
    { id: 'session.openSplitVertical', label: 'Open selected session in split below', category: 'pane', available: !!commandSession && !['exited', 'crashed'].includes(commandSession.state), disabledReason: 'No live session selected', run: () => commandSession && void openInSplit(commandSession, 'vertical') },
    { id: 'session.groupStack', label: 'Stack selected session with focused terminal', category: 'pane', available: !!commandSession&&!!activeId&&commandSession.id!==activeId&&commandSession.space_id===spaceId, disabledReason: 'Choose two live sessions in the same space', run:()=>commandSession&&activeId&&void updateLayout(spaceId,groupTerminalsInStack(activeLayout,activeId,commandSession.id)) },
    { id: 'session.reveal', label: 'Reveal selected working directory', category: 'session', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) void api('POST', '/api/reveal', { path: commandSession.cwd }); setContextMenu(null) } },
    { id: 'session.worktreeCreate', label: 'Create worktree and terminal', category: 'git', available: !!commandSession, disabledReason: 'No session selected', run: () => commandSession && createWorktree(commandSession) },
    { id: 'session.worktreesManage', label: 'Manage worktrees', category: 'git', available: !!commandSession, disabledReason: 'No session selected', run: () => commandSession && void manageWorktrees(commandSession) },
    { id: 'session.customSplit', label: 'New custom terminal in selected session split', category: 'pane', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) { setContextMenu(null); openLauncher(commandSession.space_id, 'horizontal',workingCwd(commandSession)) } } },
    { id: 'session.broadcastMembership', label: commandSession?.broadcast ? 'Remove selected session from broadcast' : 'Add selected session to broadcast', category: 'input', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) void api<Session>('POST', `/api/sessions/${commandSession.id}/broadcast-set`, { include: !commandSession.broadcast }).then(updated => { updateSession(updated); setContextMenu(null) }) } },
    { id: 'session.resume', label: 'Resume selected agent as new', category: 'history', available: !!commandSession && isAgent(commandSession) && ['exited', 'crashed'].includes(commandSession.state), disabledReason: 'Select an exited Claude or Codex session', run: () => commandSession && void resumeSession(commandSession) },
    { id: 'space.newTerminal', label: 'New terminal in selected space', category: 'space', available: !!commandSpace, disabledReason: 'No space selected', run: () => { if (commandSpace) void spawnTerminal(commandSpace.id); setSpaceMenu(null) } },
    { id: 'space.newTerminalCustom', label: 'New custom terminal in selected space', category: 'space', available: !!commandSpace, disabledReason: 'No space selected', run: () => { if (commandSpace) openLauncher(commandSpace.id); setSpaceMenu(null) } },
    { id: 'space.rename', label: 'Rename selected space', category: 'space', available: !!commandSpace, disabledReason: 'No space selected', run: () => commandSpace && openRename({ kind: 'space', space: commandSpace }) },
    { id: 'space.settings', label: 'Edit selected space defaults', category: 'space', available: !!commandSpace, disabledReason: 'No space selected', run: () => openSettings('Space defaults') },
    { id: 'space.delete', label: 'Delete selected space…', category: 'space', available: !!commandSpace && commandSpace.id !== 'default', disabledReason: 'The default space cannot be deleted', run: () => { if (commandSpace) { setConfirmSpaceDeleteId(commandSpace.id); setSpaceMenu(current => current || { space: commandSpace, x: innerWidth / 2, y: innerHeight / 2 }) } } },
    ...unpanned.map((session): Command => ({
      id: `session.attach(${session.id})`, label: `Attach live session: ${sessionName(session)}`, category: 'pane', available: true,
      run: () => { setActiveId(session.id); setEmptyMenu(null); void updateLayout(spaceId, replaceTerminal(activeLayout, activeId, session.id)) },
    })),
    ...sessions.map((session): Command => ({
      id: `session.requestKill(${session.id})`, label: `${isEndedSession(session) ? 'Remove session' : 'Kill session'}: ${sessionName(session)}`, category: 'session', available: true,
      run: () => requestKill(session),
    })),
    ...(commandSession ? spaces.filter(item => item.id !== commandSession.space_id).map((target): Command => ({
      id: `session.move(${target.id})`, label: `Move selected session to ${target.name}`, category: 'space', available: true,
      run: () => void move(commandSession, target.id),
    })) : []),
    { id: 'pane.splitHorizontal', label: 'Split focused pane right', category: 'pane', available: !!active, disabledReason: 'No focused terminal', run: () => void spawnTerminal(spaceId, active&&workingCwd(active), 'horizontal') },
    { id: 'pane.splitVertical', label: 'Split focused pane below', category: 'pane', available: !!active, disabledReason: 'No focused terminal', run: () => void spawnTerminal(spaceId, active&&workingCwd(active), 'vertical') },
    { id: 'pane.stackNew', label: 'New terminal as tab in focused stack', category: 'pane', available: !!active, disabledReason: 'No focused terminal', run:()=>void spawnTerminal(spaceId,active&&workingCwd(active),'stack') },
    { id:'stack.tabLeft',label:'Move focused tab left',category:'pane',available:!!activeStack&&activeStack.children.findIndex(child=>child.id===activeId)>0,disabledReason:'Focused tab is already first or is not stacked',run:()=>{if(!activeStack||!activeId)return;const ids=activeStack.children.map(child=>child.id);const at=ids.indexOf(activeId);[ids[at-1],ids[at]]=[ids[at],ids[at-1]];void updateLayout(spaceId,reorderStack(activeLayout,activeStack.id,ids))}},
    { id:'stack.tabRight',label:'Move focused tab right',category:'pane',available:!!activeStack&&activeStack.children.findIndex(child=>child.id===activeId)<activeStack.children.length-1,disabledReason:'Focused tab is already last or is not stacked',run:()=>{if(!activeStack||!activeId)return;const ids=activeStack.children.map(child=>child.id);const at=ids.indexOf(activeId);[ids[at+1],ids[at]]=[ids[at],ids[at+1]];void updateLayout(spaceId,reorderStack(activeLayout,activeStack.id,ids))}},
    { id:'stack.dissolve',label:'Dissolve focused tab stack into splits',category:'pane',available:!!activeStack,disabledReason:'Focused session is not in a stack',run:()=>activeStack&&void updateLayout(spaceId,dissolveStack(activeLayout,activeStack.id))},
    { id: 'pane.detach', label: activeStack ? 'Remove focused session from tab group' : 'Detach focused pane from layout', category: 'pane', available: !!active && activeDetachable, disabledReason: 'Nothing to detach: the focused terminal is the only pane', run: () => active && void updateLayout(spaceId, removeLeaf(activeLayout, 'terminal', active.id)) },
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
        setPaletteOpen(false); setLauncherOpen(false); setContextMenu(null); setSpaceMenu(null);setSidebarMenu(null);setNoteMenu(null); setEmptyMenu(null); setMainMenuOpen(false); setWorktrees(null); setSidebarOpen(false); setRenameTarget(null); setWorktreeCreate(null); setNotificationsOpen(false); setProcessSession(null);setProcessViewerOpen(false); setSettingsOpen(false); setHistoryOpen(false); setTranscript(null);setReviewState(null);setHandoffState(null)
      }
    }
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target
      if (target instanceof Element && target.closest('.context-menu,.menu-trigger')) return
      setContextMenu(null)
      setSpaceMenu(null)
      setSidebarMenu(null)
      setNoteMenu(null)
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
  const recordClientStartupTiming=(sessionId:string,milestone:StartupMilestone,elapsedMs:number)=>{
    const current=clientStartupTimingValues.current[sessionId]||{}
    if(current[milestone]!==undefined)return
    const next={...current,[milestone]:elapsedMs}
    clientStartupTimingValues.current[sessionId]=next
    setClientStartupTimings(values=>({...values,[sessionId]:next}))
    if(milestone==='replay_ready'){
      void api('POST',`/api/sessions/${sessionId}/startup-metrics`,{timing_ms:next}).catch(()=>undefined)
    }
  }

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

  const beginNoteDockResize = (event: JSX.TargetedPointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.stopPropagation()
    const workspace = event.currentTarget.parentElement
    if (!workspace) return
    const rect = workspace.getBoundingClientRect()
    let latest = activeLayout
    const moveDivider = (pointer: PointerEvent) => {
      latest = setNoteWorkspaceSize(activeLayout, (rect.right - pointer.clientX) / rect.width)
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

  const openSessionMenu = (session:Session,x:number,y:number) => {
    setSpaceId(session.space_id)
    setActiveId(session.id)
    setContextMenu({session,x,y})
  }

  const renderPaneNode = (node: PaneNode, path = '', insideStack = false): ComponentChildren => {
    if (node.type === 'split') {
      return <div class={`pane-split ${node.direction}`}>
        <div class="pane-branch" style={{ flex: `${node.ratio} 1 0` }}>{renderPaneNode(node.first, `${path}f`)}</div>
        <div class={`pane-divider ${node.direction}`} role="separator" aria-orientation={node.direction === 'horizontal' ? 'vertical' : 'horizontal'} onPointerDown={event => beginResize(event, path, node.direction)} />
        <div class="pane-branch" style={{ flex: `${1 - node.ratio} 1 0` }}>{renderPaneNode(node.second, `${path}s`)}</div>
      </div>
    }
    if(node.type==='stack'){
      const activeChild=node.children.find(child=>child.id===node.active_child_id)||node.children[0]
      // `+` spawns a terminal into this region, so anchor it to a terminal tab: the
      // active tab may be a spawned preview, which cannot host a spawn.
      const anchorId=node.children.find(child=>child.kind==='terminal')?.id
      const anchorSession=anchorId?sessions.find(item=>item.id===anchorId):undefined
      return <section class="pane-stack"><div class="stack-tabs" role="tablist" aria-label="Session and preview tabs">
        {node.children.map(child=>{
          const activate=()=>{if(child.kind==='terminal')setActiveId(child.id);if(child.id!==activeChild.id)void updateLayout(spaceId,activateStackChild(activeLayout,node.id,child.id))}
          if(child.kind==='preview'){
            const preview=previews[child.id]
            return <button role="tab" aria-label={`${preview?.url||child.id} preview tab`} title={preview?.url||child.id} aria-selected={child.id===activeChild.id} class={`preview-tab ${child.id===activeChild.id?'active':''}`} onClick={activate}><span class="preview-tab-glyph" aria-hidden="true">◱</span>{preview?`:${preview.port}`:child.id}</button>
          }
          const session=sessions.find(item=>item.id===child.id)
          return <button role="tab" aria-label={`${session?.name||child.id} session tab`} aria-selected={child.id===activeChild.id} class={`${child.id===activeChild.id?'active':''} ${session?.state||''}`} onClick={activate} onContextMenu={event=>{event.preventDefault();event.stopPropagation();activate();if(session)openSessionMenu(session,event.clientX,event.clientY)}}><span class={`state-dot ${session?.state||'running'}`}/>{session?.name||child.id}</button>
        })}
        <button class="stack-add" aria-label="New terminal tab" title="New terminal tab" disabled={!anchorSession} onClick={()=>anchorSession&&void spawnTerminal(anchorSession.space_id,workingCwd(anchorSession),'stack',undefined,anchorSession.id)}>+</button>
      </div><div class="stack-active">{renderPaneNode(activeChild,`${path}t`,true)}</div></section>
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
    if (node.kind !== 'terminal') {
      return <section class="workspace-leaf-placeholder"><strong>{node.kind}</strong><span>{node.id}</span></section>
    }
    const session = sessions.find(item => item.id === node.id)
    if (!session) return null
    const id = session.id
    const displayedCwd=session.runtime_cwd||session.spawn_cwd||session.cwd
    const cwdIsLive=session.runtime_cwd_live
    const paneProjectLabel=isAgent(session)
      ? session.project_label||scopeLabel(session.run_project_scope_id||session.project_scope_id)||'agent run'
      : scopeLabel(session.runtime_project_scope_id)||displayedCwd.split(/[\\/]/).filter(Boolean).pop()||'project'
    const openPaneMenu=(event:{clientX:number;clientY:number;preventDefault?:()=>void;stopPropagation?:()=>void})=>{event.preventDefault?.();event.stopPropagation?.();openSessionMenu(session,event.clientX,event.clientY)}
    const sessionStack=stackForSession(activeLayout,id)
    const paneDetachable=layoutLeafCount>1||(!!sessionStack&&sessionStack.children.length>1)
    const voiceMode=voiceStatus?.enabled&&isAgent(session)?effectiveVoiceMode(session):'off'
    const voiceStripVisible=voiceStatus?.enabled&&isAgent(session)&&voiceMode!=='off'
    const terminalPane=<section class={`terminal-pane ${activeId === id ? 'focused' : ''} ${voiceStripVisible?'with-voice':''}`} onPointerDown={() => setActiveId(id)}>
      <div class="pane-bar" onContextMenu={openPaneMenu} onDblClick={() => setZoomedId(current => current === id ? null : id)}>
        <div><span class={`pane-state ${session.state}`} title={session.parser_diagnostic}>{sessionStatus(session)}</span></div>
        <div class={`pane-path ${cwdIsLive?'live':'last-known'}`} title={cwdIsLive?`live cwd · ${displayedCwd}`:`last known (spawn) cwd · ${displayedCwd}`}>{cwdIsLive?'':<span>last-known::</span>}{displayedCwd}</div>
        <div class="pane-tools"><button class="pane-tool-label" aria-label={`Open note for ${sessionName(session)}`} title={isAgent(session)?`Agent-run note · ${paneProjectLabel}`:`Current project note · ${paneProjectLabel}`} onClick={() => openSessionNotes(session)}>{isAgent(session)?'run-note':`note:${paneProjectLabel}`}</button><button class="pane-tool-label" aria-label={`Inspect processes for ${sessionName(session)}`} title="Processes and previews" onClick={() => {setActiveId(session.id);openProcessViewer(session)}}>proc</button>{voiceStatus?.enabled&&isAgent(session)&&<button class={`pane-tool-label voice-chip ${voiceMode}`} aria-label={`Read aloud mode for ${sessionName(session)}: ${voiceModeLabel(voiceMode)}. Click to change.`} title={`Read aloud: ${voiceModeLabel(voiceMode)} · click to cycle off → on demand → auto`} onClick={()=>cycleVoiceMode(session)}>tts:{voiceMode==='on_demand'?'tap':voiceMode}</button>}{voiceStatus?.stt_enabled&&isAgent(session)&&<MicButton sessionId={session.id}/>}{paneDetachable&&<button title={sessionStack&&sessionStack.children.length>1?'Remove from tab group':'Detach pane from layout'} onClick={() => runNamedCommand('pane.detach')}>—</button>}<button aria-label={`More actions for ${sessionName(session)}`} title="Session actions" onClick={event=>{const rect=event.currentTarget.getBoundingClientRect();openPaneMenu({clientX:rect.right,clientY:rect.bottom,stopPropagation:()=>event.stopPropagation()})}}>⋯</button><button class={confirmKillId === session.id ? 'confirming' : ''} title={confirmKillId === session.id ? (isEndedSession(session) ? 'Confirm remove' : 'Confirm kill') : (isEndedSession(session) ? 'Remove from sidebar' : 'Kill session')} onClick={() => runNamedCommand(`session.requestKill(${session.id})`)}>{confirmKillId === session.id ? '✓' : '×'}</button></div>
      </div>
      {voiceStripVisible&&voiceStatus&&<VoicePlayer session={session} status={voiceStatus} mode={voiceMode as 'on_demand'|'auto'} onSession={updateSession} />}
      <TerminalPane session={session} onState={updateSession} startupOrigin={startupOrigins.current[session.id]} onStartupTiming={(milestone,elapsedMs)=>recordClientStartupTiming(session.id,milestone,elapsedMs)} broadcast={broadcast} keybindings={keybindings} scrollback={xtermScrollback} mobileInput={mobileInput} />
    </section>
    if(insideStack)return terminalPane
    return <section class="pane-stack singleton-stack"><div class="stack-tabs" role="tablist" aria-label="Terminal tabs">
      <button role="tab" aria-label={`${sessionName(session)} session tab`} aria-selected="true" class={`active ${session.state}`} onClick={()=>setActiveId(id)} onContextMenu={event=>{event.preventDefault();event.stopPropagation();setActiveId(id);openSessionMenu(session,event.clientX,event.clientY)}}><span class={`state-dot ${session.state}`}/>{sessionName(session)}</button>
      <button class="stack-add" aria-label="New terminal tab" title="New terminal tab" onClick={()=>void spawnTerminal(session.space_id,workingCwd(session),'stack',undefined,id)}>+</button>
    </div><div class="stack-active">{terminalPane}</div></section>
  }

  const workspaceNoteIds=(targetSpace:string)=>leaves(resolveLayout(layoutMap[targetSpace],spaces.find(item=>item.id===targetSpace)?.layout),'note').map(leaf=>leaf.id)
  const noteOwnerSession=(resourceId:string,targetSpace:string):Session|null=>{
    const identity=parseNoteResourceId(resourceId)
    const candidates=sessions.filter(item=>item.space_id===targetSpace)
    if(identity?.kind==='sessions')return candidates.find(item=>item.agent_run_id===identity.id||item.id===identity.id)||null
    return null
  }
  const sidebarNoteRow=(resourceId:string,targetSpace:string,ownerSession?:Session|null,detached=false)=>{
    const identity=parseNoteResourceId(resourceId)
    if(!identity)return null
    const noteLayout=resolveLayout(layoutMap[targetSpace],spaces.find(item=>item.id===targetSpace)?.layout)
    const workspaceOpen=workspaceNoteIds(targetSpace).includes(resourceId)
    const selected=targetSpace===spaceId&&noteLayout.note_workspace.visible&&noteLayout.note_workspace.active_id===resourceId
    const kind=identity.kind==='spaces'?'space note':identity.kind==='projects'?'project note':'agent note'
    return <button class={`sidebar-note-row ${selected?'active':''} ${detached?'unattached':''}`} title={`${kind} · right-click for presentation`} onContextMenu={event=>{event.preventDefault();event.stopPropagation();openNoteContext(resourceId,targetSpace,event.clientX,event.clientY)}} onClick={event=>{event.stopPropagation();setSpaceId(targetSpace);if(ownerSession)setActiveId(ownerSession.id);if(workspaceOpen){setMobileNoteId(resourceId);const mode=noteLayout.note_workspace.visible?noteLayout.note_workspace.mode:preferredNotesMode(targetSpace);void updateLayout(targetSpace,showNoteWorkspace(noteLayout,resourceId,mode))}else if(identity.kind==='spaces'){const space=spaces.find(item=>item.id===identity.id);if(space)openSpaceNotes(space)}setSidebarOpen(false)}}>
      <span class="note-branch" aria-hidden="true">└</span><span class="note-copy"><strong>{kind}</strong></span>
    </button>
  }
  // A server a session spawned lives beside it: nested under its sidebar row and
  // activated as a tab in the same region, so it is always one click away.
  const sidebarPreviewRow=(preview:Preview,session:Session)=>{
    const layout=layoutMap[session.space_id]||parseLayout(spaces.find(item=>item.id===session.space_id)?.layout)
    const owner=stackForSession(layout,session.id)
    const selected=owner?.active_child_id===preview.id
    return <button key={preview.id} class={`sidebar-note-row preview-row ${selected?'active':''}`} title={`${preview.url} · ${preview.source} preview spawned by this session`} onClick={event=>{event.stopPropagation();setSpaceId(session.space_id);if(owner)void updateLayout(session.space_id,activateStackChild(layout,owner.id,preview.id));setSidebarOpen(false)}}>
      <span class="note-branch" aria-hidden="true">└</span><span class="note-copy"><strong>server :{preview.port}</strong></span>
    </button>
  }
  // A detected server has no preview yet. Opening one registers it and, because the
  // daemon groups previews, it lands as a tab beside this session.
  const openDetectedServer=async(server:DetectedServer,session:Session)=>{
    try{
      const result=await api<{preview:Preview;space:Space}>('POST','/api/previews',{session_id:session.id,url:server.url,attach:true})
      setPreviews(current=>({...current,[result.preview.id]:result.preview}))
      setSpaces(items=>items.map(item=>item.id===result.space.id?result.space:item))
      setLayoutMap(current=>({...current,[result.space.id]:parseLayout(result.space.layout)}))
      setSpaceId(session.space_id)
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }
  const sidebarServerRow=(server:DetectedServer,session:Session)=>
    <button key={`server-${server.port}`} class="sidebar-note-row preview-row" title={`${server.url} · detected server · open it as a tab`} onClick={event=>{event.stopPropagation();void openDetectedServer(server,session);setSidebarOpen(false)}}>
      <span class="note-branch" aria-hidden="true">└</span><span class="note-copy"><strong>server :{server.port}</strong></span>
    </button>
  const sessionRow=(session:Session,relation?:'tab')=>{
    const spawnedPreviews=Object.values(previews).filter(item=>item.session_id===session.id)
    // Only servers earn a sidebar row. A session's other children are bookkeeping
    // noise and stay in the process inspector. Ones already open as a preview are
    // rendered by that row instead.
    const spawnedServers=detectedServers(sessionProcesses[session.id]||[])
      .filter(server=>!spawnedPreviews.some(preview=>preview.port===server.port))
    const attachedNotes=workspaceNoteIds(session.space_id).filter(resourceId=>noteOwnerSession(resourceId,session.space_id)?.id===session.id)
    return <div class="session-entry"><button draggable class={`session-row ${activeId === session.id ? 'active' : ''} ${session.state}`} onDragStart={event=>{event.dataTransfer!.effectAllowed='move';setDragSessionId(session.id)}} onDragEnd={()=>setDragSessionId(null)} onDragOver={event=>{if(dragSessionId&&dragSessionId!==session.id)event.preventDefault()}} onDrop={event=>{event.preventDefault();const dragged=sessions.find(item=>item.id===dragSessionId);if(!dragged||dragged.id===session.id)return;if(dragged.space_id!==session.space_id){setError('Move the session into this space before grouping it.');return}void updateLayout(session.space_id,groupTerminalsInStack(layoutMap[session.space_id]||emptyLayout(),session.id,dragged.id));setDragSessionId(null)}} onPointerDown={event=>beginLongPress(event,(x,y)=>openSessionMenu(session,x,y))} onPointerUp={cancelLongPress} onPointerCancel={cancelLongPress} onPointerMove={cancelLongPress} onContextMenu={event => { event.preventDefault(); openSessionMenu(session,event.clientX,event.clientY) }} onClick={() => void selectSession(session)}>
      <span class={`state-dot ${session.state}`} />
      <span class="session-copy"><strong>{isAgent(session) && <span class={`agent-prefix ${session.backend}`}>[{session.backend}]</span>}{sessionName(session)}{relation==='tab'&&<span class="layout-affinity tab" title="Shares one pane region with the other bracketed sessions">▤</span>}{session.broadcast&&<span class="broadcast-flag" title="In the broadcast set — keystrokes mirror here while broadcast input is on">⇶</span>}</strong><small class={isAgent(session) ? `agent-status ${session.state}` : ''}>{sessionStatus(session)}</small></span>
      <span class="session-meta">{isAgent(session) && <em class={`state-label ${session.state}`}>{session.state === 'idle' ? 'ready' : session.state}</em>}</span>
      <span class="row-actions" onClick={event => event.stopPropagation()}><button class={confirmKillId === session.id ? 'confirming' : ''} title={confirmKillId === session.id ? (isEndedSession(session) ? 'Confirm remove' : 'Confirm kill') : (isEndedSession(session) ? 'Remove from sidebar' : 'Kill')} onClick={() => runNamedCommand(`session.requestKill(${session.id})`)}>{confirmKillId === session.id ? '✓' : '×'}</button></span>
    </button>{spawnedPreviews.map(preview=>sidebarPreviewRow(preview,session))}{spawnedServers.map(server=>sidebarServerRow(server,session))}{attachedNotes.map(resourceId=>sidebarNoteRow(resourceId,session.space_id,session))}</div>
  }
  const sidebarNode=(node:PaneNode|null|undefined,relation?:'tab'):ComponentChildren=>{
    if(!node)return null
    if(node.type==='leaf'){
      if(node.kind!=='terminal')return null
      const session=sessions.find(item=>item.id===node.id)
      return session?sessionRow(session,relation):null
    }
    const nodeLayout:PaneLayout={...emptyLayout(),root:node}
    const ids=terminalIds(nodeLayout)
    const dropGroup=(event:JSX.TargetedDragEvent<HTMLElement>)=>{event.preventDefault();event.stopPropagation();const dragged=sessions.find(item=>item.id===dragSessionId);if(!dragged)return;const owner=sessions.find(item=>ids.includes(item.id));if(!owner||dragged.space_id!==owner.space_id){setError('Move the session into this space before changing its layout group.');return}if(node.type!=='stack'){setError('Drop onto a session to create a tab group, or onto an existing tab bracket to add a tab.');return}const sourceLayout=layoutMap[dragged.space_id]||parseLayout(spaces.find(item=>item.id===dragged.space_id)?.layout);const without=removeLeaf(sourceLayout,'terminal',dragged.id);void updateLayout(dragged.space_id,addToStack(without,node.id,dragged.id));setDragSessionId(null)}
    const branches=(node.type==='stack'?node.children:[node.first,node.second]).filter(child=>terminalIds({...emptyLayout(),root:child}).length>0)
    if(branches.length===0)return null
    if(branches.length===1)return sidebarNode(branches[0],relation)
    const label=node.type==='stack'?'Sessions sharing one tabbed pane':`${node.direction} split branches`
    return <section class={`layout-cluster ${node.type} ${node.type==='split'?node.direction:''}`} role="group" aria-label={label} onDragOver={event=>{if(dragSessionId)event.preventDefault()}} onDrop={dropGroup}>
      <span class="layout-cluster-glyph" aria-hidden="true" title={label}>{node.type==='stack'?'▤':node.direction==='horizontal'?'↔':'↕'}</span>
      {branches.map((child,index)=><div class={`layout-branch ${index===0?'first':''} ${index===branches.length-1?'last':''}`} key={child.id}>{sidebarNode(child,node.type==='stack'?'tab':undefined)}</div>)}
    </section>
  }

  const activeWorkspaceNoteIds=activeLayout.note_workspace.open_ids
  const activeWorkspaceNoteId=(mobileNoteId&&activeWorkspaceNoteIds.includes(mobileNoteId)?mobileNoteId:null)
    ||activeLayout.note_workspace.active_id
    ||activeWorkspaceNoteIds[0]
    ||null
  const noteTabLabel=(resourceId:string)=>{
    const identity=parseNoteResourceId(resourceId)
    if(identity?.kind==='spaces')return 'space note'
    if(identity?.kind==='projects')return `project · ${projectScopes.find(item=>item.id===identity.id)?.label||'note'}`
    const run=sessions.find(item=>item.agent_run_id===identity?.id||item.id===identity?.id)
    return `agent · ${run?sessionName(run):'note'}`
  }
  const activateWorkspaceNote=(resourceId:string)=>{
    setMobileNoteId(resourceId)
    void updateLayout(spaceId,activateNoteWorkspace(activeLayout,resourceId))
  }

  return <div class="app-shell">
    <div class="sr-only" role="status" aria-live="polite" aria-atomic="true">{attention ? `${attention} agent${attention === 1 ? '' : 's'} awaiting attention` : 'No agents awaiting attention'}</div>
    <div class="mobile-toolbar">
      <button class="nav-toggle mobile-nav-toggle" onClick={() => setSidebarOpen(value => !value)}>:nav</button>
      <select class="mobile-session-switcher" aria-label="Focused session" value={activeId||''} onChange={event=>{const session=sessions.find(item=>item.id===event.currentTarget.value);if(session)void selectSession(session)}}><option value="">No focused session</option>{sessions.filter(session=>!['exited','crashed'].includes(session.state)).map(session=><option value={session.id}>{spaces.find(space=>space.id===session.space_id)?.name} · {sessionName(session)}</option>)}</select>
      <button class="mobile-new-session" aria-label={`New terminal in ${activeSpace?.name||'current space'}`} title="New terminal" onClick={()=>void spawnTerminal(spaceId,active?.space_id===spaceId?workingCwd(active):activeSpace?.default_cwd||undefined)}>+</button>
    </div>

    {broadcast && <div class="broadcast-banner"><strong>Broadcast input is on</strong><span>Keystrokes mirror to sessions in the broadcast set.</span><button onClick={() => setBroadcast(false)}>Stop broadcasting</button></div>}

    <div class="workspace">
      <aside class={`sidebar ${sidebarOpen ? 'open' : ''}`} onContextMenu={event=>{const target=event.target as Element;if(target.closest('.sidebar-heading,.space-row,.session-row,.sidebar-note-row,.sidebar-footer'))return;event.preventDefault();setContextMenu(null);setSpaceMenu(null);setNoteMenu(null);setMainMenuOpen(false);setSidebarMenu({x:event.clientX,y:event.clientY})}}>
        <div class="sidebar-heading"><strong>swe_mux</strong><span class="daemon-ok" title="daemon::connected" aria-label="daemon connected"><i aria-hidden="true" /></span></div>
        <div class="space-tree">
          {spaces.map(space => {
            const children = sessions
              .filter(session => session.space_id === space.id)
              .sort((a,b)=>a.created_at-b.created_at||a.id.localeCompare(b.id))
            const spaceLayout=resolveLayout(layoutMap[space.id],space.layout)
            const spacePaneIds=terminalIds(spaceLayout)
            const unpanedChildren=children.filter(session=>!spacePaneIds.includes(session.id))
            const noteIds=leaves(spaceLayout,'note').map(leaf=>leaf.id)
            const savedSpaceNote=savedSpaceNotes.some(note=>note.id===space.id&&note.active)
            const spaceNoteIds=[...new Set([
              ...noteIds.filter(id=>parseNoteResourceId(id)?.kind==='spaces'),
              ...(savedSpaceNote?[noteResourceId('spaces',space.id)]:[]),
            ])]
            const unattachedNoteIds=noteIds.filter(id=>parseNoteResourceId(id)?.kind!=='spaces'&&!noteOwnerSession(id,space.id))
            return <section class={`space-group ${space.id === spaceId ? 'active' : ''}`}>
              <div class="space-row" onDragOver={event=>{if(dragSessionId)event.preventDefault()}} onDrop={event=>{event.preventDefault();const dragged=sessions.find(item=>item.id===dragSessionId);if(dragged&&dragged.space_id!==space.id)void move(dragged,space.id);setDragSessionId(null)}} onPointerDown={event=>beginLongPress(event,(x,y)=>setSpaceMenu({space,x,y}))} onPointerUp={cancelLongPress} onPointerCancel={cancelLongPress} onPointerMove={cancelLongPress} onContextMenu={event => { event.preventDefault(); setSpaceMenu({ space, x: event.clientX, y: event.clientY }) }} onClick={() => setSpaceId(space.id)}>
                <span class="space-chevron" aria-hidden="true">{space.id === spaceId ? '◆' : '◇'}</span><strong>{space.name}</strong>
              </div>
              {spaceNoteIds.length>0&&<div class="space-note-list">{spaceNoteIds.map(resourceId=>sidebarNoteRow(resourceId,space.id,null))}</div>}
              <div class="session-list">
                {sidebarNode(spaceLayout.root)}
                {unpanedChildren.map(session=>sessionRow(session))}
                {unattachedNoteIds.map(resourceId=>sidebarNoteRow(resourceId,space.id,null,true))}
              </div>
            </section>
          })}
        </div>
        <div class="sidebar-footer"><button class="menu-trigger" onClick={() => setMainMenuOpen(value => !value)}><span>:</span> menu</button><button class="notes-shelf-trigger" onClick={openNotesShelf}><span>#</span> notes</button></div>
      </aside>

      <main class="main-stage" onContextMenu={event => { if (!activeLayout.root) { event.preventDefault(); setEmptyMenu({ x: event.clientX, y: event.clientY }) } }}>
        <div class={`space-workspace ${activeLayout.note_workspace.visible&&activeLayout.note_workspace.mode==='dock'&&activeWorkspaceNoteId?'with-notes-dock':''}`}>
          <div class="terminal-workspace">
            {(activeLayout.root||focusedOutsideLayout) ? <div class="pane-tree">{renderPaneNode(zoomedId ? { type: 'leaf', kind: 'terminal', id: zoomedId } : focusedOutsideLayout&&activeId ? {type:'leaf',kind:'terminal',id:activeId} : activeLayout.root!)}</div> : <div class="empty-stage">
              <div class="hero-terminal" aria-hidden="true">&gt;_</div>
              <h1>Terminals that stay with you.</h1>
              <p>right-click {activeSpace?.name || 'a workspace'} or open : menu</p>
            </div>}
          </div>
          {activeWorkspaceNoteIds.length>0&&<NotesWorkspace visible={activeLayout.note_workspace.visible} mode={activeLayout.note_workspace.mode} size={activeLayout.note_workspace.size} tabs={activeWorkspaceNoteIds.map(id=>({id,label:noteTabLabel(id)}))} activeId={activeWorkspaceNoteId} onActivate={activateWorkspaceNote} onMode={changeNotesWorkspaceMode} onHide={hideActiveNotesWorkspace} onTabContext={(resourceId,event)=>{event.preventDefault();event.stopPropagation();openNoteContext(resourceId,spaceId,event.clientX,event.clientY)}} onResize={beginNoteDockResize}>
                {activeWorkspaceNoteIds.map(resourceId=>{
                  const target=noteTargetForResource(resourceId)
                  return <div key={resourceId} class={`notes-workspace-item ${resourceId===activeWorkspaceNoteId?'active':''}`} aria-hidden={resourceId!==activeWorkspaceNoteId}>
                    {target?<Notes display="pane" targetKey={`workspace:${spaceId}:${resourceId}`} mobileActive={resourceId===activeWorkspaceNoteId&&activeLayout.note_workspace.visible} cwd={target.cwd} projectScopeId={target.projectScopeId} spaceId={target.spaceId} sessionId={target.sessionId} terminalSessionId={target.terminalSessionId} initialKind={target.kind} ownerLabel={target.ownerLabel} projectLabel={target.projectLabel} onClose={()=>removeWorkspaceNote(spaceId,resourceId)} onBrowse={openNotesShelf} onInsert={text=>window.dispatchEvent(new CustomEvent('mux:terminal-action',{detail:{sessionId:target.terminalSessionId,action:'insertText',text}}))} onCapture={targetKey=>window.dispatchEvent(new CustomEvent('mux:terminal-action',{detail:{sessionId:target.terminalSessionId,action:'captureSelection',targetKey}}))}/>
                      :<section class="workspace-leaf-placeholder note-unavailable"><strong>note unavailable</strong><span>{resourceId}</span><button onClick={()=>void removeWorkspaceNote(spaceId,resourceId)}>close note</button><button onClick={openNotesShelf}>browse notes</button></section>}
                  </div>
                })}
          </NotesWorkspace>}
        </div>
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

    {contextMenu && <div class="context-menu" role="menu" aria-label={`Session actions for ${sessionName(contextMenu.session)}`} style={{ left: clampContextMenuLeft(contextMenu.x, innerWidth), top: Math.max(4, Math.min(contextMenu.y, innerHeight - 520)) }}>
      <div class="context-title"><span class={`state-dot ${contextMenu.session.state}`} /><strong>{sessionName(contextMenu.session)}</strong></div>
      <div class="context-session-info">
        <span title="Process ID of the session's root process">PID {contextMenu.session.pid}</span>
        {contextMenu.session.git.branch&&<span class="git-chip" title={`Git branch ${contextMenu.session.git.branch}${contextMenu.session.git.dirty?` · ${contextMenu.session.git.dirty} changed files`:' · clean'}`}>git:{contextMenu.session.git.branch}{contextMenu.session.git.dirty?` +${contextMenu.session.git.dirty}`:''}</span>}
        {(()=>{const startup=startupSummary(contextMenu.session);return startup&&<span class="startup-chip" title={startupTimingTitle(contextMenu.session,clientStartupTimings[contextMenu.session.id]||{})}>{startup.label}:{formatStartupMs(startup.value)}</span>})()}
      </div>
      <button onClick={() => runNamedCommand('session.rename')}>Rename</button>
      <button onClick={() => runNamedCommand('session.open')}>Open in focused pane</button>
      {isAgent(contextMenu.session) && <button onClick={() => runNamedCommand('session.pinAttention')}>{contextMenu.session.pinned_attention ? 'Unpin attention' : 'Pin attention'}</button>}
      {['exited', 'crashed'].includes(contextMenu.session.state) && isAgent(contextMenu.session) && <button onClick={() => runNamedCommand('session.resume')}>Resume as new…</button>}
      <button onClick={() => runNamedCommand('session.copyId')}>Copy session ID</button>
      <button onClick={() => runNamedCommand('session.copyCwd')}>Copy working directory</button>
      {isAgent(contextMenu.session)&&<button onClick={() => runNamedCommand('session.notes')}>Open agent-run note…</button>}
      <button onClick={() => runNamedCommand('session.projectNote')}>Open {isAgent(contextMenu.session)?'run':'current'} project note…</button>
      {isAgent(contextMenu.session)&&contextMenu.session.runtime_cwd_live&&contextMenu.session.runtime_cwd!==(contextMenu.session.run_cwd||contextMenu.session.spawn_cwd)&&<button onClick={() => runNamedCommand('session.currentProjectNote')}>Open current project note…</button>}
      <button onClick={() => runNamedCommand('session.openSplitHorizontal')}>Open in split right</button>
      <button onClick={() => runNamedCommand('session.openSplitVertical')}>Open in split below</button>
      <button disabled={!activeId||activeId===contextMenu.session.id} onClick={()=>runNamedCommand('session.groupStack')}>Stack with focused terminal</button>
      {activeDetachable&&<button onClick={() => runNamedCommand('pane.detach')}>{activeStack&&activeStack.children.length>1?'Remove from tab group':'Detach from layout'}</button>}
      <button onClick={() => runNamedCommand('session.reveal')}>Reveal in Explorer</button>
      <button onClick={() => runNamedCommand('session.worktreeCreate')}>Create worktree + terminal…</button>
      <button onClick={() => runNamedCommand('session.worktreesManage')}>Manage worktrees…</button>
      <button onClick={() => runNamedCommand('processes.open')}>Processes and previews…</button>
      <button onClick={() => runNamedCommand('pane.splitHorizontal')}>New terminal in split right</button>
      <button onClick={() => runNamedCommand('pane.splitVertical')}>New terminal in split below</button>
      <button onClick={()=>runNamedCommand('pane.stackNew')}>New terminal as tab</button>
      {activeStack&&<><button disabled={!commands.find(item=>item.id==='stack.tabLeft')?.available} onClick={()=>runNamedCommand('stack.tabLeft')}>Move tab left</button><button disabled={!commands.find(item=>item.id==='stack.tabRight')?.available} onClick={()=>runNamedCommand('stack.tabRight')}>Move tab right</button><button onClick={()=>runNamedCommand('stack.dissolve')}>Dissolve tab stack into splits</button></>}
      <button onClick={() => runNamedCommand('session.customSplit')}>New terminal custom in split…</button>
      <button disabled={paneIds.length < 2} onClick={() => runNamedCommand('pane.swapNext')}>Swap pane with next</button>
      <button disabled={paneIds.length < 2} onClick={() => runNamedCommand('pane.zoom')}>{zoomedId?'Restore pane layout':'Zoom pane'}</button>
      {voiceStatus?.enabled&&isAgent(contextMenu.session)&&<>
        <div class="context-subtitle">READ ALOUD</div>
        {(['off','on_demand','auto'] as VoiceMode[]).map(mode=><button key={mode} onClick={()=>{void setVoiceMode(contextMenu.session,mode);setContextMenu(null)}}>{effectiveVoiceMode(contextMenu.session)===mode?'✓ ':''}{mode==='off'?'Off':mode==='on_demand'?'On demand':'Auto on reply'}</button>)}
        <button onClick={()=>{const target=contextMenu.session;setContextMenu(null);void speakLastReply(target)}}>Speak last reply now</button>
      </>}
      <div class="context-subtitle">MOVE TO SPACE</div>
      {spaces.map(space => <button disabled={space.id === contextMenu.session.space_id} onClick={() => runNamedCommand(`session.move(${space.id})`)}>{space.name}</button>)}
      <div class="context-rule" />
      <button onClick={() => runNamedCommand('session.broadcastMembership')}>{contextMenu.session.broadcast ? 'Remove from broadcast' : 'Add to broadcast'}</button>
      <button class="danger" onClick={() => runNamedCommand('session.killImmediate')}>{isEndedSession(contextMenu.session) ? 'Remove from sidebar' : 'Kill session'}</button>
    </div>}

    {spaceMenu && <div class="context-menu" role="menu" aria-label={`Space actions for ${spaceMenu.space.name}`} style={{ left: clampContextMenuLeft(spaceMenu.x, innerWidth), top: Math.max(4, Math.min(spaceMenu.y, innerHeight - 310)) }}>
      <div class="context-title"><strong>{spaceMenu.space.name}</strong></div>
      <button onClick={() => runNamedCommand('space.newTerminal')}>New terminal</button>
      <button onClick={() => runNamedCommand('space.newTerminalCustom')}>New terminal custom…</button>
      <button onClick={() => runNamedCommand('space.notes')}>Open space note…</button>
      <button onClick={()=>{setSpaceMenu(null);openNotesShelf()}}>Browse notes…</button>
      <button onClick={()=>{setSpaceMenu(null);setProjectsOpen(true)}}>Projects…</button>
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

    {sidebarMenu&&<div class="context-menu" role="menu" aria-label="Sidebar actions" style={{left:clampContextMenuLeft(sidebarMenu.x,innerWidth),top:Math.max(4,Math.min(sidebarMenu.y,innerHeight-190))}}>
      <div class="context-title"><strong>WORKSPACE</strong></div>
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('space.create')}}>Create space</button>
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('processes.all')}}>All processes and previews…</button>
      <div class="context-rule" />
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('settings.open')}}>All Settings…</button>
    </div>}

    {noteMenu&&<div class="context-menu" role="menu" aria-label="Note presentation" style={{left:clampContextMenuLeft(noteMenu.x,innerWidth),top:Math.max(4,Math.min(noteMenu.y,innerHeight-220))}}>
      <div class="context-title"><strong>{noteTabLabel(noteMenu.resourceId)}</strong></div>
      <button onClick={()=>showNoteResource(noteMenu.resourceId,noteMenu.spaceId,'dock')}>Dock notes workspace</button>
      <button onClick={()=>showNoteResource(noteMenu.resourceId,noteMenu.spaceId,'popout')}>Pop out notes workspace</button>
      <button onClick={()=>{setNoteMenu(null);openNotesShelf()}}>Browse all notes…</button>
      {workspaceNoteIds(noteMenu.spaceId).includes(noteMenu.resourceId)&&<><div class="context-rule"/><button onClick={()=>{const target=noteMenu;setNoteMenu(null);void removeWorkspaceNote(target.spaceId,target.resourceId)}}>Close note tab</button></>}
    </div>}

    {emptyMenu && <div class="context-menu" role="menu" style={{ left: clampContextMenuLeft(emptyMenu.x, innerWidth), top: Math.min(emptyMenu.y, innerHeight - 280) }}>
      <div class="context-title"><strong>EMPTY PANE</strong></div>
      <button role="menuitem" onClick={() => { setEmptyMenu(null); void spawnTerminal() }}>New terminal</button>
      <button role="menuitem" onClick={() => { setEmptyMenu(null); openLauncher() }}>New terminal custom…</button>
      <button role="menuitem" disabled={!activeSpace} onClick={() => runNamedCommand('notes.open')}>Open space note…</button>
      {unpanned.length > 0 && <div class="context-subtitle">ATTACH LIVE SESSION</div>}
      {unpanned.map(session => <button role="menuitem" onClick={() => runNamedCommand(`session.attach(${session.id})`)}><span class={`state-dot ${session.state}`} />{sessionName(session)}</button>)}
    </div>}

    {mainMenuOpen && <div class="context-menu main-menu" role="menu" aria-label="swe-mux menu">
      <div class="context-title"><strong>swe-mux menu</strong></div>
      <div class="context-subtitle">WORKSPACE</div>
      <button onClick={() => { setMainMenuOpen(false); runNamedCommand('session.spawnShell') }}>New terminal in current space</button>
      <button onClick={() => { setMainMenuOpen(false); runNamedCommand('session.quickLaunch') }}>New terminal custom…</button>
      <button onClick={() => { setMainMenuOpen(false); runNamedCommand('history.open') }}>Session history</button>
      <button onClick={() => runNamedCommand('notes.shelf')}>Notes</button>
      <button onClick={() => runNamedCommand('projects.open')}>Projects</button>
      <button onClick={() => runNamedCommand('processes.all')}>Process fleet…</button>
      <button disabled={!activeSpace} onClick={() => runNamedCommand('notes.open')}>Space note <span class="menu-hint">app data</span></button>
      {active&&isAgent(active)&&<button onClick={() => runNamedCommand('session.notes')}>Agent-run note <span class="menu-hint">{active.project_label||'project'}</span></button>}
      <button disabled={!active} onClick={() => runNamedCommand('session.projectNote')}>{active&&isAgent(active)?'Run':'Current'} project note…</button>
      {active&&isAgent(active)&&active.runtime_cwd_live&&active.runtime_cwd!==(active.run_cwd||active.spawn_cwd)&&<button onClick={() => runNamedCommand('session.currentProjectNote')}>Current project note…</button>}
      <button onClick={() => runNamedCommand('notifications.open')}>Notifications{notificationUnread?` [${notificationUnread} new]`:''}</button>
      <button onClick={() => { setMainMenuOpen(false); runNamedCommand('broadcast.toggle') }}>{broadcast ? 'Stop broadcast input' : 'Start broadcast input'}</button>
      <div class="context-subtitle">CONFIGURATION</div>
      <button disabled={!(active||rememberedCwds[0])} onClick={() => runNamedCommand('settings.project')}>Project settings…</button>
      <button onClick={() => runNamedCommand('usage.open')}>Usage analytics…</button>
      <button onClick={() => runNamedCommand('hooks.open')}>Automation…</button>
      <button onClick={() => runNamedCommand('settings.open')}>All Settings…</button>
      <div class="context-subtitle">SHORTCUTS</div>
      <button onClick={() => { setMainMenuOpen(false); runNamedCommand('palette.open') }}>Command palette <span class="menu-hint">ctrl alt p</span></button>
    </div>}

    {sidebarOpen && <button class="sidebar-scrim" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />}

    {renameTarget && <div class="modal-layer" onMouseDown={event => event.target === event.currentTarget && setRenameTarget(null)}>
      <form class="modal rename-modal" onSubmit={event => { event.preventDefault(); void submitRename() }}>
        <div class="modal-heading"><div><span>RENAME::{renameTarget.kind.toUpperCase()}</span><h2>{renameTarget.kind === 'session' ? sessionName(renameTarget.session) : renameTarget.space.name}</h2></div><button type="button" aria-label="Close rename" onClick={() => setRenameTarget(null)}>×</button></div>
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
            path: current.pathEdited ? current.path : `${workingCwd(current.session)}-${branch.replace(/[^a-z0-9._-]+/gi, '-') || 'worktree'}`,
          } : current)
        }} placeholder="feature/my-change" autofocus /></label>
        <label>worktree directory<input value={worktreeCreate.path} onInput={event => setWorktreeCreate(current => current ? { ...current, path: event.currentTarget.value, pathEdited: true } : current)} /></label>
        <div class="modal-note">source::{workingCwd(worktreeCreate.session)}</div>
        <div class="modal-footer"><span>enter::create · esc::cancel</span><button type="button" onClick={() => setWorktreeCreate(null)}>Cancel</button><button class="primary" type="submit" disabled={!worktreeCreate.branch.trim() || !worktreeCreate.path.trim()}>Create</button></div>
      </form>
    </div>}

    {worktrees && <div class="worktree-layer" onMouseDown={event => event.target === event.currentTarget && setWorktrees(null)}>
      <section class="worktree-panel">
        <header><div><span>GIT WORKTREES</span><strong>{workingCwd(worktrees.session)}</strong></div><button onClick={() => setWorktrees(null)}>×</button></header>
        <div class="worktree-list">{worktrees.items.length ? worktrees.items.map(item => <article>
          <div><strong>{item.worktree}</strong><span>{item.branch?.replace('refs/heads/', '') || (item.detached ? 'detached HEAD' : 'worktree')}</span></div>
          <button onClick={() => { void spawnTerminal(worktrees.session.space_id, item.worktree); setWorktrees(null) }}>Open terminal</button>
          {item.worktree.toLowerCase() !== workingCwd(worktrees.session).toLowerCase() && <button class={confirmWorktreeRemove === item.worktree ? 'danger confirming' : 'danger'} onClick={() => void removeWorktree(item.worktree)}>{confirmWorktreeRemove === item.worktree ? 'Remove?' : 'Remove'}</button>}
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
              {!collapsed && entries.map(entry => <article class={`history-row ${transcript?.entry.id === entry.id ? 'active' : ''}`}><button onClick={() => void viewHistory(entry)}><strong>[{entry.backend}] {historyName(entry)}</strong><span>{new Date(entry.spawned_at * 1000).toLocaleString()}</span><small>{entry.final_state || entry.exit_reason || 'indexed'}{entry.external ? ' · external' : ''}</small></button><button class={confirmHistoryDelete === entry.id ? 'danger confirming' : 'danger'} aria-label={`Delete history index entry ${historyName(entry)}`} onClick={() => void deleteHistory(entry)}>{confirmHistoryDelete === entry.id ? '✓' : '×'}</button></article>)}
            </section>
          })}
          {historyLoading && <div class="history-inline-state">Loading agent history…</div>}
          {historyNext && !historyLoading && <button class="history-load-more" onClick={() => void loadHistory({ append: true })}>Load more</button>}
        </aside>
        <main>{transcript ? <><div class="transcript-heading"><button class="history-back" onClick={()=>setTranscript(null)}>← Back</button><div><h3>[{transcript.entry.backend}] {historyName(transcript.entry)}</h3><span>{transcript.entry.project_label || 'Ungrouped'} · {transcript.entry.cwd}</span><small>{transcript.entry.exit_reason || transcript.entry.final_state || 'indexed'} · {transcript.entry.model || 'model unavailable'} · {transcript.entry.external ? 'external' : 'mux session'}</small><small>{transcript.entry.context_window ? `context final ${Math.round((transcript.entry.final_context_pct || 0) * 100)}% · peak ${Math.round((transcript.entry.peak_context_pct || 0) * 100)}% · ${transcript.entry.measurement_source || 'native observation'}` : 'context unavailable'} · tokens in {transcript.entry.tokens_in || 0} / out {transcript.entry.tokens_out || 0}</small></div><button class="primary" onClick={() => void resumeHistoryEntry(transcript.entry)}>Resume as new</button></div>
          <div class="transcript-actions"><button onClick={()=>openNoteDefault({cwd:transcript.entry.cwd,projectScopeId:transcript.entry.project_scope_id,spaceId:transcript.entry.space_id||'default',sessionId:transcript.entry.id,terminalSessionId:null,kind:'sessions',ownerLabel:historyName(transcript.entry),projectLabel:transcript.entry.project_label})}>Agent-run note</button><button onClick={()=>void openHandoff(transcript.entry)}>Export handoff</button><button class="primary" onClick={()=>void previewSecondOpinion(transcript.entry)}>Review with {transcript.entry.backend==='claude'?'Codex':'Claude'}</button></div>{lineage.length>0&&<section class="transcript-lineage"><h4>Work lineage</h4>{lineage.map(edge=><article><strong>{edge.relation}</strong><span>{edge.parent_run_id} → {edge.child_run_id}</span><small>{new Date(edge.created_at*1000).toLocaleString()}</small></article>)}</section>}{transcript.annotations.length>0&&<section class="transcript-annotations"><h4>Derived annotations</h4>{transcript.annotations.map(item=><details><summary>{item.tag} · {item.content}</summary><small>{new Date(item.created_at*1000).toLocaleString()} · {item.provenance} · model::{item.resolved_model||'deterministic'} · confidence::{item.confidence??'—'} · cost::{annotationMoney.format(item.cost_usd||0)}</small></details>)}</section>}<div class="messages">{transcript.messages.length ? transcript.messages.map(message => <article class={message.role}><header>{message.role}</header>{message.content.map(block => block.type === 'text' ? <p>{block.text}</p> : <pre>{block.type === 'tool_use' ? `${block.name}\n${JSON.stringify(block.input, null, 2)}` : block.type}</pre>)}</article>) : <div class="no-transcript">No native transcript is available for this session.</div>}</div></> : <div class="history-placeholder"><span>◷</span><strong>Select a session</strong><p>Read its native transcript without resuming it.</p></div>}</main>
      </div>
    </div>}

    {reviewState&&<div class="modal-layer control-plane-modal-layer" role="dialog" aria-modal="true" aria-label="Cross-vendor second opinion" onMouseDown={event=>event.target===event.currentTarget&&setReviewState(null)}><section class="modal control-plane-modal"><div class="modal-heading"><div><span>CROSS-VENDOR REVIEW</span><h2>{reviewState.preview.source_backend} → {reviewState.preview.backend}</h2></div><button aria-label="Close review" onClick={()=>setReviewState(null)}>×</button></div><div class="control-plane-modal-body"><p>This is user-initiated. The generated prompt is shown in full and no rule or observer can start this session.</p><label>Target space<select value={reviewState.space} onChange={event=>setReviewState(current=>current?{...current,space:event.currentTarget.value}:current)}>{spaces.map(space=><option value={space.id}>{space.name}</option>)}</select></label><label>Additional review instructions<textarea value={reviewState.instructions} onInput={event=>setReviewState(current=>current?{...current,instructions:event.currentTarget.value,dirty:true}:current)} placeholder="Optional constraints or review focus" /></label><label>Reviewed prompt<textarea class="review-prompt" readOnly value={reviewState.preview.prompt}/></label>{reviewState.dirty&&<p class="modal-warning">Instructions changed. Refresh the prompt before spawning.</p>}{reviewState.error&&<p class="modal-warning" role="alert">{reviewState.error}</p>}</div><div class="modal-footer"><span>{reviewState.loading?'working…':reviewState.dirty?'preview stale':'prompt reviewed'}</span><button onClick={()=>setReviewState(null)}>Cancel</button><button onClick={()=>void refreshSecondOpinion()} disabled={reviewState.loading}>Refresh preview</button><button class="primary" disabled={reviewState.loading||reviewState.dirty} onClick={()=>void confirmSecondOpinion()}>Spawn {reviewState.preview.backend} review</button></div></section></div>}

    {handoffState&&<div class="modal-layer control-plane-modal-layer" role="dialog" aria-modal="true" aria-label="Handoff export" onMouseDown={event=>event.target===event.currentTarget&&setHandoffState(null)}><section class="modal control-plane-modal"><div class="modal-heading"><div><span>HANDOFF::EXPORT</span><h2>{historyName(handoffState.entry)}</h2></div><button aria-label="Close handoff" onClick={()=>setHandoffState(null)}>×</button></div><div class="control-plane-modal-body"><p>{handoffState.message}</p><textarea class="handoff-export" readOnly value={handoffState.markdown}/></div><div class="modal-footer"><span>read-only annotation export</span><button onClick={()=>{const blob=new Blob([handoffState.markdown],{type:'text/markdown'});const url=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=url;anchor.download=`handoff-${handoffState.entry.id}.md`;anchor.click();URL.revokeObjectURL(url)}}>Download</button><button class="primary" onClick={()=>void navigator.clipboard.writeText(handoffState.markdown).then(()=>setHandoffState(current=>current?{...current,message:'Copied to clipboard.'}:current)).catch(()=>setHandoffState(current=>current?{...current,message:'Clipboard blocked. Select the text and copy it manually.'}:current))}>Copy</button></div></section></div>}

    {settingsOpen && <Settings cwd={settingsCwd||(active&&workingCwd(active))||rememberedCwds[0]} initialSection={settingsSection} onOpenUsage={()=>{setSettingsOpen(false);setUsageOpen(true)}} onOpenAutomation={()=>{setSettingsOpen(false);setAutomationOpen(true)}} onClose={() => { setSettingsOpen(false);setSettingsCwd(null); void refresh(); void loadProfiles(); void api<{notes_default_open?:'dock'|'popout'}&Record<string,unknown>>('GET','/api/config').then(config=>{setNotesDefaultOpen(config.notes_default_open||'dock');setMobileInput(mobileInputSettings(config))}) }} />}

    {projectsOpen&&<ProjectRegistry onOpenNote={openProjectScopeNote} onOpenSettings={scope=>{setProjectsOpen(false);openSettings('Current project',scope.root)}} onClose={()=>setProjectsOpen(false)}/>}
    {notesShelfOpen&&<NotesShelf hidden={notesShelfHidden} onOpen={openShelfNote} onClose={()=>{setNotesShelfOpen(false);setNotesShelfHidden(false);setNotesShelfReturnId(null)}}/>}

    {usageOpen&&<UsageDashboard onClose={()=>setUsageOpen(false)} onConfigure={()=>{setUsageOpen(false);openSettings('Usage analytics')}}/>}
    {automationOpen&&<AutomationDashboard onClose={()=>setAutomationOpen(false)} onConfigure={()=>{setAutomationOpen(false);openSettings('Automation')}} onOpenSession={sessionId=>{const session=sessions.find(item=>item.id===sessionId);if(!session){setError('The automation session is no longer live.');return}setAutomationOpen(false);void selectSession(session)}}/>}

    {processViewerOpen && <ProcessPanel initialSessionId={processSession?.id||null} sessions={sessions} spaces={spaces} onClose={() => {setProcessViewerOpen(false);setProcessSession(null)}} onAttached={(preview, space) => {
      setPreviews(current => ({...current, [preview.id]: preview}))
      setSpaces(items => items.map(item => item.id === space.id ? space : item))
      setLayoutMap(current => ({...current, [space.id]: parseLayout(space.layout)}))
    }} />}

    {notificationsOpen&&<Notifications data={notificationData} onClose={()=>setNotificationsOpen(false)} onOpenSession={sessionId=>{const session=sessions.find(item=>item.id===sessionId);if(!session){setError('The notification session is no longer live.');return}setNotificationsOpen(false);void selectSession(session)}} />}

    {notificationToast&&<button class="notification-toast" aria-live="assertive" onClick={()=>{setNotificationToast(null);openNotifications()}}><strong>{notificationToast.session_name||'daemon'}</strong><span>{notificationToast.type.replaceAll('_',' ')}</span><small>open notifications</small></button>}

    {error && <div class="toast" onClick={() => setError('')}>{error}<span>×</span></div>}
  </div>
}
