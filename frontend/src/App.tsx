import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { ComponentChildren, JSX } from 'preact'
import { api, openWebSocket } from './api'
import { TerminalPane } from './TerminalPane'
import { ProjectResource } from './ProjectResource'
import { ContinuityBanner } from './ContinuityBanner'
import { DirectoryPicker } from './DirectoryPicker'
import { folderNameFromPath } from './pathNames'
import { ProcessPanel, type FleetSnapshot, type Preview, type ProcessItem } from './ProcessPanel'
import { ResourceUsageSummary } from './ResourceUsage'
import { ProjectsManager } from './ProjectsManager'
import { detectedServers, type DetectedServer } from './sessionProcesses'
import { PreviewPane } from './PreviewPane'
import { Notifications, type NotificationData, type UiNotification } from './Notifications'
import { UsageDashboard } from './UsageDashboard'
import { HistoryBrowser } from './HistoryBrowser'
import { AccountSwitcher } from './ProviderAccounts'
import { PromptLibrary } from './PromptLibrary'
import { ProjectRunMenu } from './ProjectRunMenu'
import { AutomationDashboard } from './AutomationDashboard'
import { MicButton, VoicePlayer } from './VoicePlayer'
import { autoplayEnabled, enqueueAutoplay, playClip, setAutoplayEnabled, unlockPlayback } from './voice'
import { handleSessionSound, type NormalizedMuxEvent } from './sessionSounds'
import type { Project, ProjectGroup, Session, ShellProfile, VoiceClip, VoiceMode, VoiceStatus } from './types'
import { keyChord } from './keys'
import { Settings } from './Settings'
import { applyTheme, configureCustomTheme, type CustomTheme, type ThemeName } from './theme'
import { bindingFor, displayChord, runCommand, searchCommands, type Command } from './commands'
import { clampContextMenuLeft } from './menuPosition'
import { defaultMobileInputSettings, mobileInputSettings, type MobileInputSettings } from './mobileInput'
import { adjacentMobileTab, mobileWorkspaceProjection } from './mobileWorkspace'
import { focusMemoryWith, parseFocusMemory, parseViewPreference, resolveInitialFocus, viewUrl } from './viewState'
import { reorderForHover, reorderTargetFromContainer, type DropSide } from './dragReorder'
import {
  browserUuid, emptyLayout, leaves, noteResourceId, paneStack, parseLayout, parseNoteResourceId, resourceLeaf,
  reconcilePreviews, reconcileTerminals, removeLeaf, replaceTerminal, setSplitRatio,
  activateContainingStack, activateStackChild, addToStack, dissolveStack, groupTerminalsInStack, moveLeafToSplit, moveLeafToStack, openTab, paneNeighborIds, paneStacks, reorderStack, resolveLayout, splitTerminal, splitView, stackForView, stackTerminal, terminalIds, terminalLeaf, visibleTerminalIds, type PaneLayout,
  type PaneDirection, type PaneLeaf, type PaneLeafKind, type PaneNode, type SplitDirection,
} from './layout'

const annotationMoney=new Intl.NumberFormat(undefined,{style:'currency',currency:'USD',maximumFractionDigits:4})
const paneDirectionOptions:Array<{id:PaneDirection;glyph:string;direction:SplitDirection;position:'before'|'after'}>=[
  {id:'left',glyph:'←',direction:'horizontal',position:'before'},
  {id:'right',glyph:'→',direction:'horizontal',position:'after'},
  {id:'up',glyph:'↑',direction:'vertical',position:'before'},
  {id:'down',glyph:'↓',direction:'vertical',position:'after'},
]

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
  const compactions = session.compaction_count > 0 ? ` · compacted ${session.compaction_count}×` : ''
  if (session.state === 'working') return `working${session.state_detail ? ` · ${session.state_detail}` : ''}${context}${compactions}`
  if (session.state === 'idle') return `ready · turn complete${context}${compactions}`
  if (session.state === 'awaiting') return `awaiting approval${session.state_detail ? ` · ${session.state_detail}` : ''}${context}${compactions}`
  if (session.state === 'starting') return 'starting agent…'
  return `${session.state}${context}${compactions}`
}

type HistoryEntry = {
  id: string; native_id: string; backend: string; name: string; cwd: string
  spawned_at: number; exited_at?: number; exit_reason?: string; transcript_path?: string
  project_id?:string;project_label?:string;final_state?:string;external?:number
  project_scope_id?:string;repo_group_id?:string;project_root?:string
  context_window?: number; final_context_pct?: number; peak_context_pct?: number
  tokens_in?: number; tokens_out?: number; model?: string; measurement_source?: string
  compaction_count?:number;last_compaction_at?:number;compaction_capability?:string;compaction_confidence?:string
  auto_named?:number;generated_title?:string
  note_id?:string
}
type DerivedAnnotation={id:string;tag:string;content:string;provenance:string;resolved_model?:string;confidence?:number;cost_usd?:number;created_at:number}
type Transcript = { entry: HistoryEntry; messages: Array<{ role: string; content: Array<{ type: string; text?: string; name?: string; input?: unknown }> }>;annotations:DerivedAnnotation[] }
type LineageEdge={id:string;parent_run_id:string;child_run_id:string;relation:'resume'|'handoff'|'continuation'|'review';metadata:Record<string,unknown>;created_at:number}
type ReviewPreview={source_run_id:string;source_backend:string;backend:'claude'|'codex';cwd:string;worktree_context:string;prompt:string;relation:'review';preview_token:string}
type ReviewState={entry:HistoryEntry;instructions:string;project:string;preview:ReviewPreview;dirty:boolean;loading:boolean;error:string}
type HandoffState={entry:HistoryEntry;markdown:string;message:string}
type HistoryPage = { items: HistoryEntry[]; next_cursor: string | null }
type HistoryProject = { project_id: string | null; label: string; root?: string; sessions: number; last_activity: number }
type ContextState = { session: Session; x: number; y: number; source: 'sidebar'|'tab'|'pane'|'mobile' } | null
type ProjectContext = { project: Project; x: number; y: number } | null
type SidebarContext = { x:number;y:number } | null
type NoteContext = { resourceId:string;projectId:string;x:number;y:number } | null
type TabContext = { leaf:PaneLeaf;label:string;projectId:string;x:number;y:number;source:'tab'|'mobile' } | null
type RenameTarget = { kind: 'session'; session: Session } | { kind: 'project'; project: Project }
type NoteTarget={projectId:string;terminalSessionId:string|null;kind:'note'|'session-note'|'files'|'file';ownerLabel:string}
type StartupMilestone = 'pane_mounted' | 'socket_open' | 'replay_ready'
type ClientStartupTiming = Partial<Record<'api_response' | StartupMilestone, number>>
type PendingSpawnPlacement = {
  projectId:string
  split:false|SplitDirection|'stack'
  targetId:string|null
  position:'before'|'after'
  resolvedId?:string
}
type RunMenuState={project:Project;x:number;y:number}

function placePendingTerminal(layout:PaneLayout,id:string,placement:PendingSpawnPlacement):PaneLayout {
  if(!placement.split)return openTab(layout,placement.targetId,terminalLeaf(id))
  if(placement.split==='stack'&&placement.targetId)return stackTerminal(layout,placement.targetId,id)
  return splitTerminal(layout,placement.targetId,id,placement.split as SplitDirection,placement.position)
}

function pendingTerminal(id:string,project:Project,backend:'shell'|'claude'|'codex'='shell'):Session {
  const now=Date.now()/1000
  return {
    id,name:`starting ${backend==='shell'?'terminal':backend}…`,project_id:project.id,backend,native_session_id:id,
    cwd:project.root,exe:'',args:[],pid:-1,created_at:now,state:'starting',tokens_in:0,
    process_job_assignment:'pending',tokens_out:0,context_window:0,context_pct:0,last_activity_ts:now,
    git:{dirty:0,ahead:0,behind:0},pinned_attention:false,broadcast:false,context_peak_pct:0,
    compaction_count:0,runtime_cwd:project.root,runtime_cwd_live:false,runtime_cwd_source:'spawn',
    runtime_cwd_dropped:0,pending:true,
  }
}

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
  add('durable registration',server.durable_registration)
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
  const [projects, setProjects] = useState<Project[]>([])
  const [projectId, setProjectId] = useState('')
  const [activeId, setActiveId] = useState<string | null>(null)
  const [focusedViewId,setFocusedViewId]=useState<string|null>(null)
  const [layoutMap, setLayoutMap] = useState<Record<string, PaneLayout>>({})
  const layoutValues=useRef<Record<string,PaneLayout>>({})
  const [broadcast, setBroadcast] = useState(false)
  const [launcherOpen, setLauncherOpen] = useState(false)
  const [runMenu,setRunMenu]=useState<RunMenuState|null>(null)
  const [launcherProject, setLauncherProject] = useState('')
  const [launcherSplit, setLauncherSplit] = useState<false | SplitDirection>(false)
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
  const [historyProject, setHistoryProject] = useState<string>('')
  const [historyState, setHistoryState] = useState('')
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
  const [projectMenu, setProjectMenu] = useState<ProjectContext>(null)
  const [sidebarMenu,setSidebarMenu]=useState<SidebarContext>(null)
  const [noteMenu,setNoteMenu]=useState<NoteContext>(null)
  const [tabMenu,setTabMenu]=useState<TabContext>(null)
  const [emptyMenu, setEmptyMenu] = useState<{x:number;y:number} | null>(null)
  const [zoomedId, setZoomedId] = useState<string | null>(null)
  const [keybindings, setKeybindings] = useState<Record<string, string>>({ 'ctrl+alt+t': 'session.spawnShell', 'ctrl+alt+p': 'palette.open' })
  const [confirmKillId, setConfirmKillId] = useState<string | null>(null)
  const [confirmProjectDeleteId, setConfirmProjectDeleteId] = useState<string | null>(null)
  const [mainMenuOpen, setMainMenuOpen] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [sidebarCollapsed,setSidebarCollapsed]=useState(()=>localStorage.getItem('mux.sidebar.collapsed.v1')==='true')
  const [sidebarWidth,setSidebarWidth]=useState(()=>{
    const stored=Number(localStorage.getItem('mux.sidebar.width.v1'))
    return Number.isFinite(stored)&&stored>=190&&stored<=480?stored:254
  })
  const [renameTarget, setRenameTarget] = useState<RenameTarget | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [projectCreate,setProjectCreate]=useState<{name:string;root:string;group_id:string}>({name:'',root:'',group_id:''})
  const [projectCreateOpen,setProjectCreateOpen]=useState(false)
  const [projectsManagerOpen,setProjectsManagerOpen]=useState(false)
  const [folderPickerOpen,setFolderPickerOpen]=useState(false)
  const [groupEdit,setGroupEdit]=useState<{id?:string;name:string}|null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsSection, setSettingsSection] = useState('General')
  const [settingsCwd,setSettingsCwd]=useState<string|null>(null)
  const [processSession, setProcessSession] = useState<Session | null>(null)
  const [processViewerOpen,setProcessViewerOpen]=useState(false)
  const [sessionProcesses,setSessionProcesses]=useState<Record<string,ProcessItem[]>>({})
  const [processFleet,setProcessFleet]=useState<FleetSnapshot|null>(null)
  const [previews, setPreviews] = useState<Record<string, Preview>>({})
  const [notificationData, setNotificationData] = useState<NotificationData>({notifications:[],deliveries:[]})
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const [notificationUnread, setNotificationUnread] = useState(0)
  const [notificationToast, setNotificationToast] = useState<UiNotification | null>(null)
  const [usageOpen, setUsageOpen] = useState(false)
  const [automationOpen,setAutomationOpen]=useState(false)
  const [projectGroups,setProjectGroups]=useState<ProjectGroup[]>([])
  const dragSessionTargetRef=useRef<{sessionId?:string;stackId?:string;projectId:string}|null>(null)
  type ProjectDrag={id:string;previewIds:string[];overId:string|null;side:DropSide|null}
  type PaneDropZone='tabs'|'left'|'right'|'top'|'bottom'
  type StackTabDrag={stackId:string;childId:string;kind:PaneLeafKind;targetStackId:string;zone:PaneDropZone;previewIds:string[];overId:string|null;side:DropSide|null}
  const [dragProject,setDragProjectState]=useState<ProjectDrag|null>(null)
  const dragProjectRef=useRef<ProjectDrag|null>(null)
  const [dragStackTab,setDragStackTabState]=useState<StackTabDrag|null>(null)
  const dragStackTabRef=useRef<StackTabDrag|null>(null)
  const suppressDragClickRef=useRef<string|null>(null)
  const pointerDropIndicatorRef=useRef<HTMLElement|null>(null)
  const setDragProject=(next:ProjectDrag|null)=>{dragProjectRef.current=next;setDragProjectState(next)}
  const setDragStackTab=(next:StackTabDrag|null)=>{dragStackTabRef.current=next;setDragStackTabState(next)}
  const previewDragStackTab=(next:StackTabDrag)=>{dragStackTabRef.current=next}
  const [promptLibraryOpen,setPromptLibraryOpen]=useState(false)
  const [xtermScrollback, setXtermScrollback] = useState(10000)
  const [mobileInput, setMobileInput] = useState<MobileInputSettings>(defaultMobileInputSettings)
  const [mobileWorkspace,setMobileWorkspace]=useState(()=>window.matchMedia('(max-width:760px)').matches)
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus | null>(null)
  const [profiles, setProfiles] = useState<ShellProfile[]>([])
  const [defaultProfile, setDefaultProfile] = useState('default')
  const [launcherProfile, setLauncherProfile] = useState(localStorage.getItem('mux.lastProfile') || '')
  const [clientStartupTimings,setClientStartupTimings]=useState<Record<string,ClientStartupTiming>>({})
  const clientStartupTimingValues=useRef<Record<string,ClientStartupTiming>>({})

  const showPointerDropIndicator=(element:HTMLElement|null,indicator?:string)=>{
    const current=pointerDropIndicatorRef.current
    if(current===element&&element?.dataset.pointerDropIndicator===indicator)return
    current?.removeAttribute('data-pointer-drop-indicator')
    pointerDropIndicatorRef.current=element
    if(element&&indicator)element.dataset.pointerDropIndicator=indicator
  }

  const beginPointerDrag=(
    event:JSX.TargetedPointerEvent<HTMLElement>,label:string,identity:string,
    onStart:()=>void,onMove:(event:PointerEvent)=>void,onDrop:()=>void,onCancel:()=>void,
  )=>{
    if(event.button!==0||!event.isPrimary)return
    const source=event.currentTarget
    const pointerId=event.pointerId,startX=event.clientX,startY=event.clientY
    let active=false,done=false,ghost:HTMLDivElement|null=null
    const cleanup=()=>{
      window.removeEventListener('pointermove',move)
      window.removeEventListener('pointerup',up)
      window.removeEventListener('pointercancel',cancelPointer)
      window.removeEventListener('blur',cancel)
      window.removeEventListener('keydown',key,true)
      source.removeEventListener('lostpointercapture',lostCapture)
      if(source.hasPointerCapture(pointerId))source.releasePointerCapture(pointerId)
      document.body.classList.remove('workspace-pointer-dragging')
      showPointerDropIndicator(null)
      ghost?.remove()
    }
    const finish=(commit:boolean)=>{
      if(done)return
      done=true;cleanup()
      if(!active)return
      window.setTimeout(()=>{if(suppressDragClickRef.current===identity)suppressDragClickRef.current=null},0)
      if(commit)onDrop();else onCancel()
    }
    const move=(pointer:PointerEvent)=>{
      if(pointer.pointerId!==pointerId)return
      if(!active&&Math.hypot(pointer.clientX-startX,pointer.clientY-startY)<5)return
      if(!active){
        active=true;suppressDragClickRef.current=identity;document.body.classList.add('workspace-pointer-dragging')
        source.setPointerCapture(pointerId)
        ghost=document.createElement('div');ghost.className='mux-pointer-drag-ghost';ghost.textContent=label;document.body.appendChild(ghost)
        onStart()
      }
      pointer.preventDefault()
      if(ghost)ghost.style.transform=`translate3d(${pointer.clientX+14}px,${pointer.clientY+12}px,0)`
      onMove(pointer)
    }
    const up=(pointer:PointerEvent)=>{if(pointer.pointerId===pointerId)finish(true)}
    const cancelPointer=(pointer:PointerEvent)=>{if(pointer.pointerId===pointerId)finish(false)}
    const lostCapture=(pointer:PointerEvent)=>{if(pointer.pointerId===pointerId)finish(false)}
    const cancel=()=>finish(false)
    const key=(keyboard:KeyboardEvent)=>{if(keyboard.key==='Escape'){keyboard.preventDefault();finish(false)}}
    window.addEventListener('pointermove',move,{passive:false})
    window.addEventListener('pointerup',up)
    window.addEventListener('pointercancel',cancelPointer)
    window.addEventListener('blur',cancel)
    window.addEventListener('keydown',key,true)
    source.addEventListener('lostpointercapture',lostCapture)
  }
  const startupOrigins=useRef<Record<string,number>>({})
  const pendingSpawns=useRef<Record<string,PendingSpawnPlacement>>({})
  const spawning = useRef(false)
  const longPressTimer = useRef<number | null>(null)
  const notificationIds = useRef<Set<string>>(new Set())
  const paletteInput = useRef<HTMLInputElement>(null)
  const refreshInFlight = useRef<Promise<void> | null>(null)
  const refreshQueued = useRef(false)
  const sessionsRef=useRef<Session[]>([])
  const projectsRef=useRef<Project[]>([])
  const layoutRevisions = useRef<Record<string,number>>({})
  const layoutWriteChains = useRef<Record<string,Promise<void>>>({})
  const layoutWriteGeneration = useRef<Record<string,number>>({})
  const requestedView = useRef(parseViewPreference(location.search))
  const focusMemory = useRef(parseFocusMemory(localStorage.getItem('mux.focus.v1')))
  const [focusHydrated,setFocusHydrated]=useState(false)
  sessionsRef.current=sessions
  projectsRef.current=projects

  const cancelLongPress = () => {
    if (longPressTimer.current !== null) window.clearTimeout(longPressTimer.current)
    longPressTimer.current = null
  }

  const toggleSidebar=()=>setSidebarCollapsed(value=>{
    const next=!value
    localStorage.setItem('mux.sidebar.collapsed.v1',String(next))
    return next
  })
  const persistSidebarWidth=(value:number)=>{
    const next=Math.max(190,Math.min(480,value))
    setSidebarWidth(next);localStorage.setItem('mux.sidebar.width.v1',String(Math.round(next)))
  }

  const beginSidebarResize=(event:JSX.TargetedPointerEvent<HTMLDivElement>)=>{
    if(sidebarCollapsed)return
    event.preventDefault()
    const startX=event.clientX,startWidth=sidebarWidth
    document.body.classList.add('sidebar-resizing')
    const move=(pointer:PointerEvent)=>setSidebarWidth(Math.max(190,Math.min(480,startWidth+pointer.clientX-startX)))
    const stop=(pointer:PointerEvent)=>{
      persistSidebarWidth(startWidth+pointer.clientX-startX)
      document.body.classList.remove('sidebar-resizing')
      window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',stop)
    }
    window.addEventListener('pointermove',move);window.addEventListener('pointerup',stop,{once:true})
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
      const [nextSessions, nextProjects, nextPreviews, nextGroups] = await Promise.all([
        api<Session[]>('GET', '/api/sessions'), api<Project[]>('GET', '/api/projects'),
        api<{items:Preview[]}>('GET', '/api/previews'),
        api<ProjectGroup[]>('GET','/api/project-groups'),
      ])
      setSessions(current => {
        const optimistic=current.filter(session=>session.pending&&pendingSpawns.current[session.id]&&!pendingSpawns.current[session.id].resolvedId)
        return [...nextSessions,...optimistic]
      })
      setProjects(nextProjects)
      for(const project of nextProjects)layoutRevisions.current[project.id]=project.layout_revision
      setPreviews(Object.fromEntries(nextPreviews.items.map(item => [item.id, item])))
      setProjectGroups(nextGroups)
      setLayoutMap(current => {
        const next = { ...current }
        const live = new Set(nextSessions.filter(session => !['exited', 'crashed'].includes(session.state)).map(session => session.id))
        const livePreviews = new Set(nextPreviews.items.map(item => item.id))
        for (const project of nextProjects) {
          next[project.id] = reconcilePreviews(reconcileTerminals(parseLayout(project.layout), live), livePreviews)
          for(const [pendingId,placement] of Object.entries(pendingSpawns.current)){
            if(placement.projectId!==project.id)continue
            next[project.id]=placePendingTerminal(next[project.id],placement.resolvedId||pendingId,placement)
          }
        }
        layoutValues.current=next
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
    void api<{theme:ThemeName;custom_theme:CustomTheme;xterm_scrollback_lines:number}&Record<string,unknown>>('GET','/api/config').then(config => { configureCustomTheme(config.custom_theme); applyTheme(config.theme); setXtermScrollback(config.xterm_scrollback_lines);setMobileInput(mobileInputSettings(config)) })
    void loadProfiles()
    void api<VoiceStatus>('GET','/api/voice').then(setVoiceStatus).catch(()=>setVoiceStatus(null))
    void loadNotifications()
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
  const loadProcesses = async () => {
    try {
      const snapshot = await api<FleetSnapshot>('GET','/api/processes')
      setProcessFleet(snapshot)
      setSessionProcesses(Object.fromEntries((snapshot.sessions||[]).map(group=>[group.session_id,group.processes||[]])))
    } catch { setProcessFleet(null);setSessionProcesses({}) }
  }

  useEffect(() => {
    void loadProcesses()
    const tick = () => { if (!document.hidden) void loadProcesses() }
    const timer = setInterval(tick, 8000)
    const onVisible = () => { if (!document.hidden) void loadProcesses() }
    document.addEventListener('visibilitychange', onVisible)
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', onVisible) }
  }, [])

  useEffect(()=>{
    const openFromTerminal=(event:Event)=>{
      const detail=(event as CustomEvent<{sessionId:string;url:string}>).detail
      const session=sessionsRef.current.find(item=>item.id===detail?.sessionId)
      if(!session||!detail?.url)return
      void api<{preview:Preview;project:Project}>('POST','/api/previews',{session_id:session.id,url:detail.url,approved:true,attach:true}).then(result=>{
        setPreviews(current=>({...current,[result.preview.id]:result.preview}))
        setProjects(items=>items.map(item=>item.id===result.project.id?result.project:item))
        setLayoutMap(current=>({...current,[result.project.id]:parseLayout(result.project.layout)}))
        setProjectId(session.project_id);setFocusedViewId(result.preview.id);setSidebarOpen(false)
      }).catch(cause=>setError(cause instanceof Error?cause.message:String(cause)))
    }
    window.addEventListener('mux:open-terminal-preview',openFromTerminal)
    return()=>window.removeEventListener('mux:open-terminal-preview',openFromTerminal)
  },[])

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
      socket.onopen = () => window.dispatchEvent(new CustomEvent('mux:events-connected'))
      socket.onmessage = message => {
        queueRefresh()
        try {
          const event = JSON.parse(String(message.data))
          const soundEvent=event as NormalizedMuxEvent
          const eventSession=sessionsRef.current.find(item=>item.id===soundEvent.session_id)
          const eventProject=projectsRef.current.find(item=>item.id===(eventSession?.project_id||String(soundEvent.payload?.project_id||'')))
          handleSessionSound(soundEvent,eventProject?.effective_options?.notification_sounds_enabled!==false)
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
          if(event.type==='project_files_changed')window.dispatchEvent(new CustomEvent('mux:project-files-changed',{detail:{projectId:event.payload?.project_id,paths:event.payload?.paths||[]}}))
          if(event.type==='project_note_changed'||event.type==='session_note_changed')window.dispatchEvent(new CustomEvent('mux:note-changed',{detail:{projectId:String(event.payload?.project_id||''),kind:event.type==='session_note_changed'?'session-note':'note',noteId:event.type==='session_note_changed'?String(event.payload?.note_id||''):null,revision:String(event.payload?.revision||'')}}))
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

  useEffect(()=>{
    const query=window.matchMedia('(max-width:760px)')
    const changed=()=>setMobileWorkspace(query.matches)
    changed();query.addEventListener('change',changed)
    return()=>query.removeEventListener('change',changed)
  },[])

  const active = sessions.find(session => session.id === activeId)
  const attention = sessions.filter(session => session.state === 'awaiting').length
  const activeProject = projects.find(project => project.id === projectId)
  const orderedProjects = [...projects].sort((a,b)=>a.position-b.position||a.name.localeCompare(b.name)||a.id.localeCompare(b.id))
  const visibleProjects = orderedProjects.filter(project => project.sidebar_visible !== false)
  const activeLayout = layoutMap[projectId] || emptyLayout()
  const paneIds = terminalIds(activeLayout).filter(id => sessions.some(session => session.id === id && !['exited', 'crashed'].includes(session.state)))
  const workspacePanes=paneStacks(activeLayout)
  const paneViewIds=workspacePanes.map(pane=>pane.active_child_id)
  const focusedTabId=leaves(activeLayout).find(leaf=>leaf.id===(focusedViewId||activeId))?.id||null
  const activeStack=focusedTabId?stackForView(activeLayout,focusedTabId):null
  const unpanned = sessions.filter(session => session.project_id === projectId && !['exited', 'crashed'].includes(session.state) && !paneIds.includes(session.id))
  const focusedOutsideLayout=!!active&&!['exited','crashed'].includes(active.state)&&active.project_id===projectId&&!paneIds.includes(active.id)

  useEffect(()=>{
    if(!activeLayout.root){if(focusedViewId)setFocusedViewId(null);return}
    if(focusedViewId&&stackForView(activeLayout,focusedViewId))return
    const next=paneStacks(activeLayout)[0]?.active_child_id||null
    setFocusedViewId(next)
  },[projectId,activeLayout,focusedViewId])

  useEffect(()=>{
    if(!window.matchMedia('(max-width:760px)').matches)return
    const frame=requestAnimationFrame(()=>{
      const selected=document.querySelector<HTMLElement>('.mobile-unified-tabs [role="tab"][aria-selected="true"]')
        ||document.querySelector<HTMLElement>('.pane-stack.focused-pane .stack-tabs [role="tab"][aria-selected="true"]')
        ||document.querySelector<HTMLElement>('.pane-stack .stack-tabs [role="tab"][aria-selected="true"]')
      selected?.scrollIntoView({block:'nearest',inline:'nearest'})
    })
    return()=>cancelAnimationFrame(frame)
  },[projectId,focusedViewId,activeId,mobileWorkspace])

  useEffect(() => {
    if (focusHydrated || projects.length === 0) return
    const visibleByProject=Object.fromEntries(projects.map(project=>[
      project.id,
      visibleTerminalIds(layoutMap[project.id]||parseLayout(project.layout)),
    ]))
    const selected=resolveInitialFocus(sessions,projects.map(project=>project.id),visibleByProject,requestedView.current,focusMemory.current)
    setProjectId(selected.projectId)
    setActiveId(selected.sessionId)
    setFocusedViewId(selected.sessionId)
    setFocusHydrated(true)
  },[focusHydrated,sessions,projects,layoutMap])

  useEffect(() => {
    if(!focusHydrated)return
    const session=sessions.find(item=>item.id===activeId&&item.project_id===projectId&&!isEndedSession(item))
    focusMemory.current=focusMemoryWith(focusMemory.current,projectId,session?.id||null)
    localStorage.setItem('mux.focus.v1',JSON.stringify(focusMemory.current))
    const next=viewUrl(location.href,projectId,session?.id||null)
    if(`${location.pathname}${location.search}${location.hash}`!==next)window.history.replaceState(window.history.state,'',next)
  },[focusHydrated,projectId,activeId,sessions])

  useEffect(() => {
    if(!focusHydrated)return
    const live = sessions.filter(session => session.project_id === projectId && !['exited', 'crashed'].includes(session.state))
    if (zoomedId && !leaves(activeLayout).some(leaf => leaf.id === zoomedId)) setZoomedId(null)
    if (live.some(session => session.id === activeId)) return
    const liveIds = new Set(live.map(session => session.id))
    const nextId = visibleTerminalIds(activeLayout).find(id => liveIds.has(id))
      ?? terminalIds(activeLayout).find(id => liveIds.has(id))
      ?? live[0]?.id
      ?? null
    if (nextId === activeId) return
    setActiveId(nextId)
    if(nextId)setFocusedViewId(nextId)
    if (nextId && terminalIds(activeLayout).includes(nextId)) {
      setLayoutMap(current => ({
        ...current,
        [projectId]: activateContainingStack(current[projectId] ?? activeLayout, nextId),
      }))
    }
  }, [focusHydrated,sessions, projectId, activeId, zoomedId, layoutMap])
  const openSettings = (section='General',cwdOverride?:string) => { setSettingsSection(section);setSettingsCwd(cwdOverride||null); setSettingsOpen(true); setMainMenuOpen(false); setProjectMenu(null) }
  const projectNoteTarget=(value:Project|Session):NoteTarget=>{
    const project='root' in value?value:projects.find(item=>item.id===value.project_id)
    return {projectId:project?.id||value.id,terminalSessionId:'backend' in value&&!isEndedSession(value)?value.id:null,kind:'note',ownerLabel:project?.name||'Project note'}
  }
  const sessionNoteTarget=(session:Session):NoteTarget=>({projectId:session.project_id,terminalSessionId:isEndedSession(session)?null:session.id,kind:'session-note',ownerLabel:session.note_id||session.id})
  const noteIdForTarget=(target:NoteTarget)=>noteResourceId(target.kind,target.kind==='file'||target.kind==='session-note'?target.ownerLabel:target.projectId)
  const noteTargetForResource=(resourceId:string,targetProject=projectId):NoteTarget|null=>{
    const identity=parseNoteResourceId(resourceId)
    if(!identity)return null
    return {projectId:targetProject,terminalSessionId:null,kind:identity.kind,ownerLabel:identity.id}
  }
  const openNoteDefault=(target:NoteTarget)=>void showResourceForTarget(target)
  const openProjectNotes=(value:Project|Session)=>openNoteDefault(projectNoteTarget(value))
  const initializeAndOpenSessionNote=async(target:NoteTarget)=>{
    try{
      await api('POST',`/api/projects/${target.projectId}/session-notes/${encodeURIComponent(target.ownerLabel)}`)
      setSessions(items=>items.map(item=>item.id===target.terminalSessionId||item.id===target.ownerLabel?{...item,note_id:target.ownerLabel,note_exists:true}:item))
      await showResourceForTarget(target)
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }
  const openSessionNotes=(session:Session)=>{setActiveId(session.id);void initializeAndOpenSessionNote(sessionNoteTarget(session))}
  const openHistorySessionNote=(entry:HistoryEntry)=>{
    if(!entry.project_id||!entry.note_id){setError('This historical session is not linked to a Project note owner.');return}
    void initializeAndOpenSessionNote({projectId:entry.project_id,terminalSessionId:null,kind:'session-note',ownerLabel:entry.note_id})
  }
  const openProjectFiles=(project:Project)=>openNoteDefault({projectId:project.id,terminalSessionId:null,kind:'files',ownerLabel:project.id})
  const openProjectFile=(project:Project,path:string,targetViewId?:string)=>void showResourceForTarget({projectId:project.id,terminalSessionId:null,kind:'file',ownerLabel:path},targetViewId)
  const openNotifications = () => { setNotificationsOpen(true);setNotificationUnread(0);setMainMenuOpen(false);void loadNotifications() }

  const commitProjectOrder=async(nextIds:string[])=>{
    const expected=orderedProjects.map(project=>project.id)
    if(nextIds.join('\0')===expected.join('\0'))return
    const positions=new Map(nextIds.map((id,index)=>[id,index]))
    setProjects(items=>items.map(item=>({...item,position:positions.get(item.id)??item.position})))
    try{
      const next=await api<Project[]>('PUT','/api/projects/order',{project_ids:nextIds,expected_order:expected})
      setProjects(next)
    }catch(cause){
      await refresh()
      setError(cause instanceof Error?cause.message:String(cause))
    }
  }
  const moveProjectRelative=(project:Project,direction:-1|1)=>{
    const peers=orderedProjects.filter(item=>(item.group_id||null)===(project.group_id||null))
    const index=peers.findIndex(item=>item.id===project.id)
    const other=peers[index+direction]
    if(!other)return
    const ids=orderedProjects.map(item=>item.id)
    const from=ids.indexOf(project.id),to=ids.indexOf(other.id)
    ;[ids[from],ids[to]]=[ids[to],ids[from]]
    setProjectMenu(null)
    void commitProjectOrder(ids)
  }
  const beginProjectPointerDrag=(event:JSX.TargetedPointerEvent<HTMLElement>,project:Project,peerIds:string[])=>{
    const bucket=event.currentTarget.closest<HTMLElement>('.sidebar-project-bucket')
    const initial:ProjectDrag={id:project.id,previewIds:orderedProjects.map(item=>item.id),overId:null,side:null}
    beginPointerDrag(event,project.name,`project:${project.id}`,
      ()=>{cancelLongPress();dragProjectRef.current=initial},
      pointer=>{
        const current=dragProjectRef.current
        if(!current||!bucket||!peerIds.includes(current.id)){showPointerDropIndicator(null);return}
        const target=reorderTargetFromContainer(bucket,current.id,'vertical',pointer.clientY)
        if(!target){showPointerDropIndicator(null);return}
        const previewIds=reorderForHover(current.previewIds,current.id,target.id,target.side)
        dragProjectRef.current={...current,previewIds,overId:target.id,side:target.side}
        const targetElement=Array.from(bucket.querySelectorAll<HTMLElement>(':scope > [data-reorder-id]')).find(item=>item.dataset.reorderId===target.id)||null
        showPointerDropIndicator(targetElement,`insert-${target.side}`)
      },
      ()=>{const current=dragProjectRef.current;setDragProject(null);if(current)void commitProjectOrder(current.previewIds)},
      ()=>setDragProject(null),
    )
  }
  const beginSessionPointerDrag=(event:JSX.TargetedPointerEvent<HTMLElement>,session:Session)=>{
    beginPointerDrag(event,sessionName(session),`session:${session.id}`,
      ()=>{cancelLongPress();dragSessionTargetRef.current=null},
      pointer=>{
        const hit=document.elementFromPoint(pointer.clientX,pointer.clientY) as HTMLElement|null
        const sessionTarget=hit?.closest<HTMLElement>('[data-sidebar-session-id]')
        const targetSessionId=sessionTarget?.dataset.sidebarSessionId
        if(targetSessionId&&targetSessionId!==session.id){
          const targetProjectId=sessionTarget.dataset.sidebarProjectId||session.project_id
          dragSessionTargetRef.current={sessionId:targetSessionId,projectId:targetProjectId}
          showPointerDropIndicator(sessionTarget,targetProjectId===session.project_id?'group-session':'invalid')
          return
        }
        const stackTarget=hit?.closest<HTMLElement>('[data-sidebar-stack-id]')
        const stackId=stackTarget?.dataset.sidebarStackId
        if(stackId){
          const targetProjectId=stackTarget.dataset.sidebarProjectId||session.project_id
          dragSessionTargetRef.current={stackId,projectId:targetProjectId}
          showPointerDropIndicator(stackTarget,targetProjectId===session.project_id?'join-stack':'invalid')
          return
        }
        dragSessionTargetRef.current=null
        showPointerDropIndicator(null)
      },
      ()=>{
        const target=dragSessionTargetRef.current;dragSessionTargetRef.current=null
        if(!target)return
        if(target.projectId!==session.project_id){setError('Move the session into this project before changing its layout group.');return}
        const current=layoutValues.current[session.project_id]||layoutMap[session.project_id]||parseLayout(projects.find(item=>item.id===session.project_id)?.layout)
        if(target.sessionId){void updateLayout(session.project_id,groupTerminalsInStack(current,target.sessionId,session.id));return}
        if(target.stackId){const without=removeLeaf(current,'terminal',session.id);void updateLayout(session.project_id,addToStack(without,target.stackId,session.id))}
      },
      ()=>{dragSessionTargetRef.current=null},
    )
  }

  useEffect(() => {
    if (!contextMenu && !projectMenu && !sidebarMenu && !noteMenu && !tabMenu && !emptyMenu && !mainMenuOpen && !renameTarget) return
    const dismissEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      setContextMenu(null)
      setProjectMenu(null)
      setSidebarMenu(null)
      setNoteMenu(null)
      setTabMenu(null)
      setEmptyMenu(null)
      setMainMenuOpen(false)
      setRenameTarget(null)
    }
    window.addEventListener('keydown', dismissEscape, true)
    return () => window.removeEventListener('keydown', dismissEscape, true)
  }, [contextMenu, projectMenu, sidebarMenu, noteMenu, tabMenu, emptyMenu, mainMenuOpen, renameTarget])

  useEffect(() => {
    if (!contextMenu && !projectMenu && !sidebarMenu && !noteMenu && !tabMenu && !emptyMenu && !mainMenuOpen) return
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
  }, [contextMenu, projectMenu, sidebarMenu, noteMenu, tabMenu, emptyMenu, mainMenuOpen])

  useEffect(() => {
    if (!confirmKillId) return
    const timer = window.setTimeout(() => {
      setConfirmKillId(current => current === confirmKillId ? null : current)
    }, 2000)
    return () => window.clearTimeout(timer)
  }, [confirmKillId])

  const spawnTerminal = async (targetProject = projectId, split: false | SplitDirection | 'stack' = false, profileId?: string, targetSessionId?: string, position:'before'|'after'='after', backend:'shell'|'claude'|'codex'='shell') => {
    if (spawning.current) return
    const target=projectsRef.current.find(item=>item.id===targetProject)
    if(!target){setError('Project is not available yet.');return}
    spawning.current = true
    const startupOrigin=performance.now()
    const pendingId=`pending-${browserUuid()}`
    const currentLayout=layoutValues.current[targetProject]||layoutMap[targetProject]||parseLayout(target.layout)
    const focused=targetSessionId??(targetProject===projectId?focusedViewId||activeId:leaves(currentLayout)[0]?.id||null)
    const placement:PendingSpawnPlacement={projectId:targetProject,split,targetId:focused,position}
    pendingSpawns.current[pendingId]=placement
    const optimisticLayout=placePendingTerminal(currentLayout,pendingId,placement)
    layoutValues.current[targetProject]=optimisticLayout
    setSessions(items=>[...items,pendingTerminal(pendingId,target,backend)])
    setLayoutMap(current=>({...current,[targetProject]:optimisticLayout}))
    setProjectId(targetProject)
    setActiveId(pendingId)
    setFocusedViewId(pendingId)
    setLauncherOpen(false)
    try {
      const next = await api<Session>('POST', '/api/sessions', {
        backend, project_id: targetProject,
        profile_id: backend==='shell' ? profileId || undefined : undefined,
      })
      startupOrigins.current[next.id]=startupOrigin
      const browserTiming={api_response:performance.now()-startupOrigin}
      clientStartupTimingValues.current[next.id]=browserTiming
      setClientStartupTimings(current=>({...current,[next.id]:browserTiming}))
      if (profileId) { localStorage.setItem('mux.lastProfile',profileId); setLauncherProfile(profileId) }
      placement.resolvedId=next.id
      setSessions(items => [...items.filter(item=>item.id!==pendingId&&item.id!==next.id),next])
      setActiveId(next.id)
      setFocusedViewId(next.id)
      const latestLayout=layoutValues.current[targetProject]||optimisticLayout
      const withPending=terminalIds(latestLayout).includes(pendingId)?latestLayout:placePendingTerminal(latestLayout,pendingId,placement)
      const nextLayout=replaceTerminal(withPending,pendingId,next.id)
      await updateLayout(targetProject, nextLayout)
      // Protect against an event refresh that began with the pre-spawn layout.
      window.setTimeout(()=>{delete pendingSpawns.current[pendingId]},500)
    } catch (cause) {
      delete pendingSpawns.current[pendingId]
      setSessions(items=>items.filter(item=>item.id!==pendingId))
      const failedLayout=removeLeaf(layoutValues.current[targetProject]||optimisticLayout,'terminal',pendingId)
      layoutValues.current[targetProject]=failedLayout
      setLayoutMap(current=>({...current,[targetProject]:failedLayout}))
      const fallback=terminalIds(failedLayout)[0]||null
      setActiveId(current=>current===pendingId?fallback:current)
      setFocusedViewId(current=>current===pendingId?fallback:current)
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      spawning.current = false
    }
  }

  const openLauncher = (targetProject = projectId, split: false | SplitDirection = false) => {
    setLauncherProject(targetProject)
    setLauncherSplit(split)
    setLauncherProfile(localStorage.getItem('mux.lastProfile') || projects.find(item=>item.id===targetProject)?.effective_options?.profile_id || defaultProfile)
    setLauncherOpen(true)
  }

  const openRunMenu=(project:Project,element:HTMLElement)=>{
    const rect=element.getBoundingClientRect()
    setRunMenu({project,x:Math.max(6,Math.min(rect.left,window.innerWidth-306)),y:Math.min(rect.bottom+4,window.innerHeight-50)})
    setProjectMenu(null);setMainMenuOpen(false)
  }

  const attachActionSessions=async(targetProject:string,nextSessions:Session[])=>{
    if(!nextSessions.length)return
    const target=projectsRef.current.find(item=>item.id===targetProject)
    if(!target)return
    let nextLayout=layoutValues.current[targetProject]||layoutMap[targetProject]||parseLayout(target.layout)
    let targetId=targetProject===projectId?(focusedViewId||activeId):leaves(nextLayout)[0]?.id||null
    for(const session of nextSessions){nextLayout=openTab(nextLayout,targetId,terminalLeaf(session.id));targetId=session.id}
    layoutValues.current[targetProject]=nextLayout
    setSessions(items=>[...items.filter(item=>!nextSessions.some(next=>next.id===item.id)),...nextSessions])
    setLayoutMap(current=>({...current,[targetProject]:nextLayout}))
    setProjectId(targetProject);setActiveId(nextSessions.at(-1)!.id);setFocusedViewId(nextSessions.at(-1)!.id);setSidebarOpen(false)
    await updateLayout(targetProject,nextLayout)
  }

  const createProject = async () => {
    setProjectCreate({name:'',root:'',group_id:''})
    setProjectCreateOpen(true)
  }

  const openProjectsManager=()=>{
    setProjectsManagerOpen(true);setMainMenuOpen(false);setSidebarMenu(null);setProjectMenu(null)
  }

  const submitProject=async()=>{
    const next=await api<Project>('POST','/api/projects',{name:projectCreate.name,root:projectCreate.root,group_id:projectCreate.group_id||null})
    setProjects(items=>[...items,next]);setProjectId(next.id);setProjectCreateOpen(false);setFolderPickerOpen(false)
  }

  const submitGroup=async()=>{
    if(!groupEdit?.name.trim())return
    if(groupEdit.id){
      const updated=await api<ProjectGroup>('PATCH',`/api/project-groups/${groupEdit.id}`,{name:groupEdit.name})
      setProjectGroups(items=>items.map(item=>item.id===updated.id?updated:item))
    }else{
      const created=await api<ProjectGroup>('POST','/api/project-groups',{name:groupEdit.name})
      setProjectGroups(items=>[...items,created])
    }
    setGroupEdit(null)
  }

  const deleteGroup=async(group:ProjectGroup)=>{
    await api('DELETE',`/api/project-groups/${group.id}`)
    setProjectGroups(items=>items.filter(item=>item.id!==group.id))
    setProjects(items=>items.map(item=>item.group_id===group.id?{...item,group_id:null}:item))
  }

  const openRename = (target: RenameTarget) => {
    setContextMenu(null)
    setProjectMenu(null)
    setRenameTarget(target)
    setRenameValue(target.kind === 'session' ? sessionName(target.session) : target.project.name)
  }

  const submitRename = async () => {
    if (!renameTarget) return
    const name = renameValue.trim()
    const currentName = renameTarget.kind === 'session' ? renameTarget.session.name : renameTarget.project.name
    if (!name || name === currentName) {
      setRenameTarget(null)
      return
    }
    try {
      if (renameTarget.kind === 'session') {
        const updated = await api<Session>('PATCH', `/api/sessions/${renameTarget.session.id}`, { name })
        updateSession(updated)
      } else {
        const updated = await api<Project>('PATCH', `/api/projects/${renameTarget.project.id}`, { name })
        setProjects(items => items.map(item => item.id === updated.id ? updated : item))
      }
      setRenameTarget(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  const deleteProject = async (project: Project) => {
    await api('DELETE', `/api/projects/${project.id}`)
    setProjects(items => items.filter(item => item.id !== project.id))
    setConfirmProjectDeleteId(null)
    if (projectId === project.id) setProjectId(projects.find(item=>item.id!==project.id)?.id||'')
  }

  const patchManagedProject=async(project:Project,changes:Partial<Pick<Project,'name'|'group_id'|'sidebar_visible'>>)=>{
    const updated=await api<Project>('PATCH',`/api/projects/${project.id}`,changes)
    setProjects(items=>items.map(item=>item.id===updated.id?updated:item))
    if(changes.sidebar_visible===false&&projectId===project.id){
      const fallback=projects.find(item=>item.id!==project.id&&item.sidebar_visible!==false)
      if(fallback){setProjectId(fallback.id);setFocusedViewId(leaves(layoutMap[fallback.id]||parseLayout(fallback.layout))[0]?.id||null)}
    }
    return updated
  }

  const killNow = async (session: Session) => {
    await api('DELETE', `/api/sessions/${session.id}`)
    setConfirmKillId(null)
    setContextMenu(null)
    const currentLayout = resolveLayout(
      layoutMap[session.project_id],
      projects.find(project => project.id === session.project_id)?.layout,
    )
    let nextLayout = removeLeaf(currentLayout, 'terminal', session.id)
    const surviving = sessions.filter(item => item.id !== session.id && item.project_id === session.project_id && !['exited', 'crashed'].includes(item.state))
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
    await updateLayout(session.project_id, nextLayout)
    await refresh()
  }

  const requestKill = (session: Session) => {
    if (confirmKillId === session.id) void killNow(session)
    else setConfirmKillId(session.id)
  }

  const updateLayout = async (targetProject: string, layout: PaneLayout) => {
    layoutValues.current[targetProject]=layout
    setLayoutMap(current => ({ ...current, [targetProject]: layout }))
    const generation=(layoutWriteGeneration.current[targetProject]||0)+1
    layoutWriteGeneration.current[targetProject]=generation
    const previous=layoutWriteChains.current[targetProject]||Promise.resolve()
    const operation=previous.catch(()=>undefined).then(async()=>{
      const revision=layoutRevisions.current[targetProject]??projects.find(project=>project.id===targetProject)?.layout_revision??0
      try {
        const updated = await api<Project>('PATCH', `/api/projects/${targetProject}`, { layout, layout_revision: revision })
        layoutRevisions.current[targetProject]=updated.layout_revision
        setProjects(items => items.map(item => item.id === updated.id ? updated : item))
        if(layoutWriteGeneration.current[targetProject]===generation){
          const persisted=parseLayout(updated.layout)
          layoutValues.current[targetProject]=persisted
          setLayoutMap(current => ({ ...current, [targetProject]: persisted }))
        }
      } catch (cause) {
        await refresh()
        const message = cause instanceof Error ? cause.message : String(cause)
        setError(message.includes('stale layout revision') ? 'Layout changed in another client; reloaded the current layout.' : message)
      }
    })
    layoutWriteChains.current[targetProject]=operation
    await operation
    if(layoutWriteChains.current[targetProject]===operation)delete layoutWriteChains.current[targetProject]
  }

  const showResourceForTarget = async (target:NoteTarget,targetViewId?:string) => {
    const resourceId=noteIdForTarget(target)
    const targetProject=projects.some(project=>project.id===target.projectId)?target.projectId:(activeProject?.id||projects[0]?.id)
    if(!resourceId||!targetProject){setError('A live Project is required to open this resource.');return}
    const current=resolveLayout(layoutMap[targetProject],projects.find(project=>project.id===targetProject)?.layout)
    const focused=targetViewId||(targetProject===projectId&&focusedViewId&&stackForView(current,focusedViewId)?focusedViewId:null)||target.terminalSessionId||terminalIds(current)[0]||leaves(current)[0]?.id||null
    setProjectId(targetProject);if(target.terminalSessionId)setActiveId(target.terminalSessionId);setFocusedViewId(resourceId)
    setContextMenu(null);setProjectMenu(null);setNoteMenu(null);setMainMenuOpen(false);setEmptyMenu(null)
    await updateLayout(targetProject,openTab(current,focused,resourceLeaf('note',resourceId)))
  }

  const showNoteResource=(resourceId:string,targetProject:string)=>{
    const target=noteTargetForResource(resourceId,targetProject)
    if(!target){setError('This resource is no longer linked to a durable owner.');setNoteMenu(null);return}
    void showResourceForTarget(target)
  }
  const placeNoteResourceInFocusedPane=async(resourceId:string,targetProject:string)=>{
    const identity=noteTargetForResource(resourceId,targetProject)
    if(!identity){setError('This resource is no longer linked to a durable owner.');setNoteMenu(null);return}
    const current=resolveLayout(layoutMap[targetProject],projects.find(project=>project.id===targetProject)?.layout)
    const targetId=targetProject===projectId&&focusedViewId&&focusedViewId!==resourceId&&stackForView(current,focusedViewId)?focusedViewId:null
    const targetPane=targetId?stackForView(current,targetId):null
    const owner=stackForView(current,resourceId)
    let next=owner
      ?targetPane&&targetPane.id!==owner.id?moveLeafToStack(current,'note',resourceId,targetPane.id):activateContainingStack(current,resourceId)
      :openTab(current,targetId,resourceLeaf('note',resourceId))
    next=activateContainingStack(next,resourceId)
    setProjectId(targetProject);setFocusedViewId(resourceId);setNoteMenu(null)
    await updateLayout(targetProject,next)
  }
  const splitNoteResource=async(resourceId:string,targetProject:string,direction:SplitDirection,position:'before'|'after'='after')=>{
    const current=resolveLayout(layoutMap[targetProject],projects.find(project=>project.id===targetProject)?.layout)
    const owner=stackForView(current,resourceId)
    const target=targetProject===projectId&&focusedViewId&&focusedViewId!==resourceId&&stackForView(current,focusedViewId)
      ?focusedViewId
      :owner?.children.find(child=>child.id!==resourceId)?.id||null
    setProjectId(targetProject);setFocusedViewId(resourceId);setNoteMenu(null)
    await updateLayout(targetProject,splitView(current,target,resourceLeaf('note',resourceId),direction,position))
  }

  const openNoteContext=(resourceId:string,targetProject:string,x:number,y:number)=>{
    setContextMenu(null);setProjectMenu(null);setSidebarMenu(null);setMainMenuOpen(false)
    setNoteMenu({resourceId,projectId:targetProject,x,y})
  }

  const openProcessViewer=(session:Session|null=null)=>{
    setProcessSession(session);setProcessViewerOpen(true);setContextMenu(null);setSidebarMenu(null);setMainMenuOpen(false)
  }

  const removeWorkspaceNote = async (targetProject:string,resourceId:string) => {
    if(focusedViewId===resourceId)setFocusedViewId(activeId)
    const current=resolveLayout(layoutMap[targetProject],projects.find(project=>project.id===targetProject)?.layout)
    await updateLayout(targetProject,removeLeaf(current,'note',resourceId))
  }

  const selectSession = async (session: Session) => {
    const current = resolveLayout(layoutMap[session.project_id],projects.find(item=>item.id===session.project_id)?.layout)
    const isPaned=terminalIds(current).includes(session.id)
    setProjectId(session.project_id)
    setActiveId(session.id)
    setFocusedViewId(session.id)
    setSidebarOpen(false)
    if(session.pending){
      const next=isPaned?activateContainingStack(current,session.id):openTab(current,focusedViewId,terminalLeaf(session.id))
      layoutValues.current[session.project_id]=next
      setLayoutMap(layouts=>({...layouts,[session.project_id]:next}))
      return
    }
    await updateLayout(session.project_id,isPaned?activateContainingStack(current,session.id):openTab(current,focusedViewId,terminalLeaf(session.id)))
  }

  const openInSplit = async (session: Session, direction: SplitDirection = 'horizontal', position:'before'|'after'='after', targetId=activeId) => {
    setProjectId(session.project_id)
    setActiveId(session.id)
    setFocusedViewId(session.id)
    await updateLayout(session.project_id, splitTerminal(layoutMap[session.project_id] || emptyLayout(), targetId, session.id, direction,position))
    setContextMenu(null)
  }

  const splitExistingLeaf=async(leaf:PaneLeaf,targetProject:string,direction:SplitDirection,position:'before'|'after')=>{
    const current=resolveLayout(layoutMap[targetProject],projects.find(project=>project.id===targetProject)?.layout)
    const owner=stackForView(current,leaf.id)
    const anchor=owner?.children.find(child=>child.id!==leaf.id)?.id||null
    if(!anchor)return
    setFocusedViewId(leaf.id);if(leaf.kind==='terminal')setActiveId(leaf.id)
    setContextMenu(null);setTabMenu(null)
    await updateLayout(targetProject,splitView(current,anchor,leaf,direction,position))
  }

  const moveTabDirection=async(leaf:PaneLeaf,targetProject:string,direction:PaneDirection)=>{
    const current=resolveLayout(layoutMap[targetProject],projects.find(project=>project.id===targetProject)?.layout)
    const targetStackId=paneNeighborIds(current,leaf.id)[direction]
    if(!targetStackId)return
    setFocusedViewId(leaf.id);if(leaf.kind==='terminal')setActiveId(leaf.id)
    setContextMenu(null);setTabMenu(null)
    await updateLayout(targetProject,moveLeafToStack(current,leaf.kind,leaf.id,targetStackId))
  }

  const directionRow=(label:string,onDirection:(option:typeof paneDirectionOptions[number])=>void,available:(direction:PaneDirection)=>boolean=()=>true)=>
    <div class="context-direction-row"><span>{label}</span><div>{paneDirectionOptions.map(option=><button aria-label={`${label} ${option.id}`} title={`${label} ${option.id}`} disabled={!available(option.id)} onClick={()=>onDirection(option)}>{option.glyph}</button>)}</div></div>

  const loadHistory = async (options: {append?:boolean;query?:string;backend?:string;project?:string;state?:string;external?:string;from?:string;to?:string} = {}) => {
    const query = options.query ?? historyQuery
    const backend = options.backend ?? historyBackend
    const project = options.project === undefined ? historyProject : options.project
    const state = options.state ?? historyState
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
    if(!activeProject){setError('Select a Project before opening History.');return}
    const id='history:archive'
    const current=resolveLayout(layoutValues.current[activeProject.id],activeProject.layout)
    const next=openTab(current,focusedViewId,resourceLeaf('history',id))
    setFocusedViewId(id)
    await updateLayout(activeProject.id,next)
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
    } catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }

  const previewSecondOpinion = async (entry:HistoryEntry,instructions='',targetProject=entry.project_id||projectId) => {
    setHistoryError('')
    try {
      const result=await api<{preview:ReviewPreview}>('POST',`/api/history/${entry.id}/second-opinion`,{confirm:false,instructions})
      setReviewState({entry,instructions,project:targetProject,preview:result.preview,dirty:false,loading:false,error:''})
    } catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
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
      const result=await api<{session:Session}>('POST',`/api/history/${reviewState.entry.id}/second-opinion`,{confirm:true,preview_token:reviewState.preview.preview_token,instructions:reviewState.instructions,backend:reviewState.preview.backend,project_id:reviewState.project,target_session_id:activeId})
      setReviewState(null);setHistoryOpen(false);await refresh();setProjectId(result.session.project_id);setActiveId(result.session.id)
    }catch(cause){setReviewState(current=>current?{...current,loading:false,error:cause instanceof Error?cause.message:String(cause)}:current)}
  }

  const resumeHistoryEntry = async (entry: HistoryEntry) => {
    try {
      const resumed = await api<Session>('POST', `/api/history/${entry.id}/resume`, { project_id: projectId, target_session_id: activeId })
      setSessions(items => [...items, resumed]); setActiveId(resumed.id); setHistoryOpen(false)
      await refresh()
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
  }

  const resumeSession = async (session: Session) => {
    try {
      const resumed = await api<Session>('POST', `/api/history/${session.id}/resume`, { project_id: session.project_id })
      setSessions(items => [...items, resumed])
      setActiveId(resumed.id)
      setContextMenu(null)
      await updateLayout(session.project_id, replaceTerminal(layoutMap[session.project_id] || emptyLayout(), session.id, resumed.id))
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

  const commandSession = contextMenu?.session || active
  const commandProject = projectMenu?.project || activeProject
  const commands: Command[] = [
    { id: 'palette.open', label: 'Open command palette', category: 'view', available: true, run: () => setPaletteOpen(true) },
    { id:'prompts.open',label:'Open prompt library',category:'input',available:true,run:()=>{setPromptLibraryOpen(true);setMainMenuOpen(false)} },
    { id: 'session.spawnShell', label: 'New terminal in current project', category: 'session', available: !!activeProject, disabledReason:'Create or select a project first', run: () => void spawnTerminal() },
    { id: 'session.quickLaunch', label: 'New terminal custom…', category: 'session', available: !!activeProject, disabledReason:'Create or select a project first', run: () => openLauncher() },
    { id: 'project.create', label: 'Manage projects', category: 'project', available: true, run: openProjectsManager },
    { id: 'history.open', label: 'Browse session history', category: 'view', available: true, run: () => void showHistory() },
    { id: 'project.files', label: 'Browse current project files', category: 'view', available: !!activeProject, disabledReason: 'No project selected', run: () => activeProject&&openProjectFiles(activeProject) },
    { id: 'settings.open', label: 'Open Settings', category: 'view', available: true, run: () => openSettings() },
    { id: 'settings.project', label: 'Open current project settings', category: 'view', available: !!activeProject, disabledReason: 'No project is selected', run: () => activeProject&&openSettings('Current project',activeProject.root) },
    { id: 'usage.open', label: 'Open usage analytics', category: 'view', available: true, run: () => {setUsageOpen(true);setMainMenuOpen(false)} },
    { id: 'hooks.open', label: 'Open Automation', category: 'view', available: true, run: () => {setAutomationOpen(true);setMainMenuOpen(false)} },
    { id: 'notifications.open', label: `Open notifications${notificationUnread?` (${notificationUnread} new)`:''}`, category: 'view', available: true, run: openNotifications },
    { id: 'notes.open', label: 'Open current project note', category: 'view', available: !!activeProject, disabledReason: 'No project selected', run: () => activeProject&&openProjectNotes(activeProject) },
    { id: 'session.note', label: 'Open selected session note', category: 'view', available: !!commandSession, disabledReason: 'No session selected', run: () => commandSession&&openSessionNotes(commandSession) },
    { id: 'project.note', label: 'Open selected project note', category: 'view', available: !!commandProject, disabledReason: 'No project selected', run: () => commandProject&&openProjectNotes(commandProject) },
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
    { id: 'session.groupStack', label: 'Stack selected session with focused terminal', category: 'pane', available: !!commandSession&&!!activeId&&commandSession.id!==activeId&&commandSession.project_id===projectId, disabledReason: 'Choose two live sessions in the same project', run:()=>commandSession&&activeId&&void updateLayout(projectId,groupTerminalsInStack(activeLayout,activeId,commandSession.id)) },
    { id: 'session.reveal', label: 'Reveal selected working directory', category: 'session', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) void api('POST', '/api/reveal', { path: commandSession.cwd }); setContextMenu(null) } },
    { id: 'session.customSplit', label: 'New custom terminal in selected session split', category: 'pane', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) { setContextMenu(null); openLauncher(commandSession.project_id, 'horizontal') } } },
    { id: 'session.broadcastMembership', label: commandSession?.broadcast ? 'Remove selected session from broadcast' : 'Add selected session to broadcast', category: 'input', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) void api<Session>('POST', `/api/sessions/${commandSession.id}/broadcast-set`, { include: !commandSession.broadcast }).then(updated => { updateSession(updated); setContextMenu(null) }) } },
    { id: 'session.resume', label: 'Resume selected agent as new', category: 'history', available: !!commandSession && isAgent(commandSession) && ['exited', 'crashed'].includes(commandSession.state), disabledReason: 'Select an exited Claude or Codex session', run: () => commandSession && void resumeSession(commandSession) },
    { id: 'project.newTerminal', label: 'New terminal in selected project', category: 'project', available: !!commandProject, disabledReason: 'No project selected', run: () => { if (commandProject) void spawnTerminal(commandProject.id); setProjectMenu(null) } },
    { id: 'project.newTerminalCustom', label: 'New custom terminal in selected project', category: 'project', available: !!commandProject, disabledReason: 'No project selected', run: () => { if (commandProject) openLauncher(commandProject.id); setProjectMenu(null) } },
    { id: 'project.reveal', label: 'Reveal selected project in Explorer', category: 'project', available: !!commandProject, disabledReason: 'No project selected', run: () => { if (commandProject) void api('POST', '/api/reveal', { path: commandProject.root }); setProjectMenu(null) } },
    { id: 'project.rename', label: 'Rename selected project', category: 'project', available: !!commandProject, disabledReason: 'No project selected', run: () => commandProject && openRename({ kind: 'project', project: commandProject }) },
    { id:'project.moveUp',label:'Move selected Project up',category:'project',available:!!commandProject&&orderedProjects.filter(item=>(item.group_id||null)===(commandProject.group_id||null))[0]?.id!==commandProject.id,disabledReason:'Project is already first in its Group',run:()=>commandProject&&moveProjectRelative(commandProject,-1) },
    { id:'project.moveDown',label:'Move selected Project down',category:'project',available:!!commandProject&&orderedProjects.filter(item=>(item.group_id||null)===(commandProject.group_id||null)).at(-1)?.id!==commandProject.id,disabledReason:'Project is already last in its Group',run:()=>commandProject&&moveProjectRelative(commandProject,1) },
    { id: 'project.settings', label: 'Edit selected project defaults', category: 'project', available: !!commandProject, disabledReason: 'No project selected', run: () => openSettings('Project defaults') },
    { id: 'project.delete', label: 'Delete selected project…', category: 'project', available: !!commandProject&&!sessions.some(session=>session.project_id===commandProject.id), disabledReason: 'Remove this project’s sessions first', run: () => { if (commandProject) { setConfirmProjectDeleteId(commandProject.id); setProjectMenu(current => current || { project: commandProject, x: innerWidth / 2, y: innerHeight / 2 }) } } },
    ...unpanned.map((session): Command => ({
      id: `session.attach(${session.id})`, label: `Attach live session: ${sessionName(session)}`, category: 'pane', available: true,
      run: () => { setActiveId(session.id); setEmptyMenu(null); void updateLayout(projectId, replaceTerminal(activeLayout, activeId, session.id)) },
    })),
    ...sessions.map((session): Command => ({
      id: `session.requestKill(${session.id})`, label: `${isEndedSession(session) ? 'Remove session' : 'Kill session'}: ${sessionName(session)}`, category: 'session', available: true,
      run: () => requestKill(session),
    })),
    { id: 'pane.splitHorizontal', label: 'Split focused pane right', category: 'pane', available: !!activeProject, disabledReason: 'No Project selected', run: () => void spawnTerminal(projectId, 'horizontal') },
    { id: 'pane.splitVertical', label: 'Split focused pane below', category: 'pane', available: !!activeProject, disabledReason: 'No Project selected', run: () => void spawnTerminal(projectId, 'vertical') },
    { id: 'pane.stackNew', label: 'New terminal as tab in focused pane', category: 'pane', available: !!activeProject, disabledReason: 'No Project selected', run:()=>void spawnTerminal(projectId,'stack') },
    { id:'stack.dissolve',label:'Dissolve focused tab stack into splits',category:'pane',available:!!activeStack&&activeStack.children.length>1,disabledReason:'Focused pane has only one tab',run:()=>activeStack&&void updateLayout(projectId,dissolveStack(activeLayout,activeStack.id))},
    { id: 'pane.zoom', label: zoomedId ? 'Restore pane layout' : 'Zoom focused pane', category: 'pane', available: !!focusedTabId && workspacePanes.length > 1, disabledReason: 'Zoom requires multiple panes', run: () => setZoomedId(zoomedId ? null : focusedTabId) },
    { id: 'pane.next', label: 'Focus next pane', category: 'pane', available: workspacePanes.length > 1, disabledReason: 'Only one pane is open', run: () => focusRelativePane(1) },
    { id: 'pane.previous', label: 'Focus previous pane', category: 'pane', available: workspacePanes.length > 1, disabledReason: 'Only one pane is open', run: () => focusRelativePane(-1) },
    { id: 'broadcast.toggle', label: broadcast ? 'Stop broadcasting input' : 'Start broadcasting input', category: 'input', available: true, run: () => setBroadcast(value => !value) },
    ...visibleProjects.slice(0, 9).map((project, index): Command => ({
      id: `project.activate(${index + 1})`, label: `Switch to project ${index + 1}: ${project.name}`,
      category: 'project', available: project.id !== projectId, disabledReason: 'Project is already active',
      run: () => { const layout=layoutMap[project.id]||emptyLayout();const first=leaves(layout)[0]||null;setProjectId(project.id);setFocusedViewId(first?.id||null);setActiveId(terminalIds(layout)[0]||null) },
    })),
  ]
  const shownCommands = searchCommands(commands, paletteQuery)
  useEffect(() => setPaletteIndex(0), [paletteQuery, paletteOpen])
  useEffect(()=>{if(!paletteOpen)return;const frame=requestAnimationFrame(()=>{paletteInput.current?.focus();paletteInput.current?.setSelectionRange(paletteInput.current.value.length,paletteInput.current.value.length)});return()=>cancelAnimationFrame(frame)},[paletteOpen])

  function focusRelativePane(offset: number) {
    if (!paneViewIds.length) return
    const current = focusedTabId ? paneViewIds.indexOf(focusedTabId) : -1
    const nextId=paneViewIds[(Math.max(current, 0) + offset + paneViewIds.length) % paneViewIds.length]
    const next=leaves(activeLayout).find(leaf=>leaf.id===nextId)
    setFocusedViewId(nextId)
    if(next?.kind==='terminal')setActiveId(next.id)
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
        setPaletteOpen(false); setLauncherOpen(false); setContextMenu(null); setProjectMenu(null);setSidebarMenu(null);setNoteMenu(null);setTabMenu(null); setEmptyMenu(null); setMainMenuOpen(false); setSidebarOpen(false); setRenameTarget(null); setNotificationsOpen(false); setProcessSession(null);setProcessViewerOpen(false); setSettingsOpen(false); setProjectsManagerOpen(false); setHistoryOpen(false); setTranscript(null);setReviewState(null);setHandoffState(null)
      }
    }
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target
      if (target instanceof Element && target.closest('.context-menu,.menu-trigger')) return
      setContextMenu(null)
      setProjectMenu(null)
      setSidebarMenu(null)
      setNoteMenu(null)
      setTabMenu(null)
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
      setLayoutMap(current => ({ ...current, [projectId]: latest }))
    }
    const stopResize = () => {
      window.removeEventListener('pointermove', moveDivider)
      window.removeEventListener('pointerup', stopResize)
      void updateLayout(projectId, latest)
    }
    window.addEventListener('pointermove', moveDivider)
    window.addEventListener('pointerup', stopResize, { once: true })
  }

  const openSessionMenu = (session:Session,x:number,y:number,source:NonNullable<ContextState>['source']) => {
    setProjectId(session.project_id)
    if(source!=='sidebar'){setActiveId(session.id);setFocusedViewId(session.id)}
    setTabMenu(null);setNoteMenu(null)
    setContextMenu({session,x,y,source})
  }

  const openTabMenu=(leaf:PaneLeaf,label:string,x:number,y:number,source:'tab'|'mobile'='tab')=>{
    setContextMenu(null);setNoteMenu(null);setProjectMenu(null);setSidebarMenu(null);setMainMenuOpen(false)
    setFocusedViewId(leaf.id);if(leaf.kind==='terminal')setActiveId(leaf.id)
    setTabMenu({leaf,label,projectId,x,y,source})
  }

  const beginWorkspaceTabDrag=(event:JSX.TargetedPointerEvent<HTMLElement>,initial:StackTabDrag,label:string)=>{
    beginPointerDrag(event,label,`tab:${initial.childId}`,
      ()=>{dragStackTabRef.current=initial},
      pointer=>{
        const hit=document.elementFromPoint(pointer.clientX,pointer.clientY) as HTMLElement|null
        const paneElement=hit?.closest<HTMLElement>('.pane-stack[data-pane-stack-id]')
        const targetStackId=paneElement?.dataset.paneStackId
        if(!paneElement||!targetStackId){showPointerDropIndicator(null);return}
        const latest=layoutValues.current[projectId]||activeLayout
        const targetPane=paneStacks(latest).find(pane=>pane.id===targetStackId)
        const current=dragStackTabRef.current
        if(!targetPane||!current){showPointerDropIndicator(null);return}
        const tabStrip=paneElement.querySelector<HTMLElement>(':scope > .stack-tabs')
        const tabBox=tabStrip?.getBoundingClientRect()
        if(tabStrip&&tabBox&&pointer.clientY>=tabBox.top&&pointer.clientY<=tabBox.bottom){
          const target=reorderTargetFromContainer(tabStrip,current.childId,'horizontal',pointer.clientX)
          const base=current.targetStackId===targetStackId&&current.zone==='tabs'?current.previewIds:[...targetPane.children.map(child=>child.id),current.childId]
          const previewIds=target?reorderForHover(base,current.childId,target.id,target.side):base
          previewDragStackTab({...current,targetStackId,zone:'tabs',previewIds,overId:target?.id||null,side:target?.side||null})
          const targetElement=target?Array.from(tabStrip.querySelectorAll<HTMLElement>(':scope > [data-reorder-id]')).find(item=>item.dataset.reorderId===target.id)||null:null
          showPointerDropIndicator(targetElement||tabStrip,targetElement?`insert-${target?.side}`:'tab-bar')
          return
        }
        const box=paneElement.getBoundingClientRect(),x=(pointer.clientX-box.left)/box.width,y=(pointer.clientY-box.top)/box.height
        const edges:[PaneDropZone,number][]=[['left',x],['right',1-x],['top',y],['bottom',1-y]]
        const nearest=edges.sort((a,b)=>a[1]-b[1])[0]
        const zone:PaneDropZone=nearest[1]<.2?nearest[0]:'tabs'
        const previewIds=zone==='tabs'?[...targetPane.children.filter(child=>child.id!==current.childId).map(child=>child.id),current.childId]:targetPane.children.map(child=>child.id)
        previewDragStackTab({...current,targetStackId,zone,previewIds,overId:null,side:null})
        showPointerDropIndicator(zone==='tabs'?(tabStrip||paneElement):paneElement,zone==='tabs'?'tab-bar':`split-${zone}`)
      },
      ()=>{
        const current=dragStackTabRef.current
        setDragStackTab(null)
        if(!current)return
        setFocusedViewId(current.childId);if(current.kind==='terminal')setActiveId(current.childId)
        const latest=layoutValues.current[projectId]||activeLayout
        if(!paneStacks(latest).some(pane=>pane.id===current.targetStackId))return
        if(current.zone!=='tabs'){
          const direction=current.zone==='left'||current.zone==='right'?'horizontal':'vertical'
          const position=current.zone==='left'||current.zone==='top'?'before':'after'
          void updateLayout(projectId,moveLeafToSplit(latest,current.kind,current.childId,current.targetStackId,direction,position));return
        }
        const moved=current.stackId===current.targetStackId?latest:moveLeafToStack(latest,current.kind,current.childId,current.targetStackId)
        void updateLayout(projectId,reorderStack(moved,current.targetStackId,current.previewIds))
      },
      ()=>setDragStackTab(null),
    )
  }

  const renderPaneNode = (node: PaneNode|PaneLeaf, path = '', insideStack = false): ComponentChildren => {
    if (node.type === 'split') {
      return <div class={`pane-split ${node.direction}`}>
        <div class="pane-branch" style={{ flex: `${node.ratio} 1 0` }}>{renderPaneNode(node.first, `${path}f`)}</div>
        <div class={`pane-divider ${node.direction}`} role="separator" aria-orientation={node.direction === 'horizontal' ? 'vertical' : 'horizontal'} onPointerDown={event => beginResize(event, path, node.direction)} />
        <div class="pane-branch" style={{ flex: `${1 - node.ratio} 1 0` }}>{renderPaneNode(node.second, `${path}s`)}</div>
      </div>
    }
    if(node.type==='stack'){
      const activeChild=node.children.find(child=>child.id===node.active_child_id)||node.children[0]
      const previewIds=dragStackTab?.targetStackId===node.id&&dragStackTab.zone==='tabs'?dragStackTab.previewIds:node.children.map(child=>child.id)
      const paneDropClass=dragStackTab?.targetStackId===node.id?`tab-drop-active drop-zone-${dragStackTab.zone}`:''
      const focusedPane=!!focusedViewId&&node.children.some(child=>child.id===focusedViewId)
      const closeTab=(child:PaneLeaf,label:string,session?:Session)=>{
        const terminal=child.kind==='terminal'
        const confirming=terminal&&confirmKillId===child.id
        const ended=!!session&&isEndedSession(session)
        const title=terminal
          ? confirming?(ended?'Confirm remove session':'Confirm kill terminal'):(ended?'Remove session':'Close and kill terminal')
          : `Close ${label} tab`
        return <button class={`tab-close ${confirming?'confirming':''}`} disabled={terminal&&(!session||!!session.pending)} aria-label={`${title}: ${label}`} title={title} onPointerDown={event=>event.stopPropagation()} onClick={event=>{
          event.preventDefault();event.stopPropagation()
          if(terminal){if(session&&!session.pending)requestKill(session);return}
          if(child.kind==='note'){void removeWorkspaceNote(projectId,child.id);return}
          const latest=layoutValues.current[projectId]||activeLayout
          void updateLayout(projectId,removeLeaf(latest,child.kind,child.id))
        }}>{confirming?'✓':'×'}</button>
      }
      return <section data-pane-stack-id={node.id} class={`pane-stack ${focusedPane?'focused-pane':''} ${paneDropClass}`} onPointerDown={()=>setFocusedViewId(activeChild.id)}><div class="stack-tabs" role="tablist" aria-label="Workspace tabs">
        {node.children.map(child=>{
          const activate=()=>{if(suppressDragClickRef.current===`tab:${child.id}`){suppressDragClickRef.current=null;return}setFocusedViewId(child.id);if(child.kind==='terminal')setActiveId(child.id);if(child.id!==activeChild.id)void updateLayout(projectId,activateStackChild(activeLayout,node.id,child.id))}
          const dragClass=dragStackTab?.overId===child.id&&dragStackTab.side?`drag-over drop-${dragStackTab.side}`:''
          const dragStyle={order:previewIds.indexOf(child.id)}
          if(child.kind==='preview'){
            const preview=previews[child.id]
            const label=preview?.url||child.id
            return <div key={child.id} data-reorder-id={child.id} style={dragStyle} class={`stack-tab-shell draggable-tab ${dragStackTab?.childId===child.id?'dragging':''} ${dragClass}`} onPointerDown={event=>beginWorkspaceTabDrag(event,{stackId:node.id,childId:child.id,kind:child.kind,targetStackId:node.id,zone:'tabs',previewIds:node.children.map(item=>item.id),overId:null,side:null},label)}><button role="tab" aria-label={`${label} preview tab`} title={label} aria-selected={child.id===activeChild.id} class={`tab-main preview-tab ${child.id===activeChild.id?'active':''}`} onClick={activate} onContextMenu={event=>{event.preventDefault();event.stopPropagation();activate();openTabMenu(child,label,event.clientX,event.clientY)}}><span class="preview-tab-glyph" aria-hidden="true">◱</span>{preview?`:${preview.port}`:child.id}</button>{closeTab(child,label)}</div>
          }
          if(child.kind==='note'){
            const label=noteTabLabel(child.id)
            return <div key={child.id} data-reorder-id={child.id} style={dragStyle} class={`stack-tab-shell draggable-tab resource-tab ${dragStackTab?.childId===child.id?'dragging':''} ${dragClass}`} onPointerDown={event=>beginWorkspaceTabDrag(event,{stackId:node.id,childId:child.id,kind:child.kind,targetStackId:node.id,zone:'tabs',previewIds:node.children.map(item=>item.id),overId:null,side:null},label)}><button role="tab" aria-label={`${label} resource tab`} title={label} aria-selected={child.id===activeChild.id} class={`tab-main ${child.id===activeChild.id?'active':''}`} onClick={activate} onContextMenu={event=>{event.preventDefault();event.stopPropagation();activate();openTabMenu(child,label,event.clientX,event.clientY)}}><span class="preview-tab-glyph" aria-hidden="true">◇</span>{label}</button>{closeTab(child,label)}</div>
          }
          if(child.kind==='history'){
            const label='History'
            return <div key={child.id} data-reorder-id={child.id} style={dragStyle} class={`stack-tab-shell draggable-tab resource-tab ${dragStackTab?.childId===child.id?'dragging':''} ${dragClass}`} onPointerDown={event=>beginWorkspaceTabDrag(event,{stackId:node.id,childId:child.id,kind:child.kind,targetStackId:node.id,zone:'tabs',previewIds:node.children.map(item=>item.id),overId:null,side:null},label)}><button role="tab" aria-label="History tab" title="Search session history" aria-selected={child.id===activeChild.id} class={`tab-main ${child.id===activeChild.id?'active':''}`} onClick={activate} onContextMenu={event=>{event.preventDefault();event.stopPropagation();activate();openTabMenu(child,label,event.clientX,event.clientY)}}><span class="preview-tab-glyph" aria-hidden="true">◷</span>{label}</button>{closeTab(child,label)}</div>
          }
          const session=sessions.find(item=>item.id===child.id)
          const label=session?.name||child.id
          return <div key={child.id} data-reorder-id={child.id} style={dragStyle} class={`stack-tab-shell draggable-tab ${session?.pending?'pending-terminal-tab':''} ${dragStackTab?.childId===child.id?'dragging':''} ${dragClass}`} onPointerDown={event=>{if(!session?.pending)beginWorkspaceTabDrag(event,{stackId:node.id,childId:child.id,kind:child.kind,targetStackId:node.id,zone:'tabs',previewIds:node.children.map(item=>item.id),overId:null,side:null},label)}}><button role="tab" aria-label={`${label} session tab`} aria-selected={child.id===activeChild.id} class={`tab-main ${child.id===activeChild.id?'active':''} ${session?.state||''}`} onClick={activate} onContextMenu={event=>{event.preventDefault();event.stopPropagation();activate();if(session&&!session.pending)openSessionMenu(session,event.clientX,event.clientY,'tab')}}><span class={`state-dot ${session?.state||'running'}`}/>{label}</button>{closeTab(child,label,session)}</div>
        })}
        <button class="stack-add" style={{order:node.children.length}} aria-label="New terminal tab" title="New terminal tab" disabled={!activeProject} onClick={()=>activeProject&&void spawnTerminal(activeProject.id,'stack',undefined,activeChild.id)}>+</button>
      </div><div class="stack-active">{renderPaneNode(activeChild,`${path}t`,true)}</div></section>
    }
    if(node.kind==='note'){
      const identity=parseNoteResourceId(node.id)
      if(!identity||!activeProject)return <section class="workspace-leaf-placeholder note-unavailable"><strong>resource unavailable</strong><span>{node.id}</span><button onClick={()=>void removeWorkspaceNote(projectId,node.id)}>close tab</button></section>
      return <ProjectResource key={`${activeProject.id}:${node.id}`} project={activeProject} resource={identity} onOpenFile={path=>openProjectFile(activeProject,path,node.id)} onClose={()=>void removeWorkspaceNote(projectId,node.id)}/>
    }
    if(node.kind==='history')return <HistoryBrowser key={`${activeProject?.id||projectId}:${node.id}`} projects={projects} initialProjectId={activeProject?.id||projectId} onResume={resumeHistoryEntry} onProjectNote={openProjectNotes} onSessionNote={openHistorySessionNote} onSecondOpinion={previewSecondOpinion} onHandoff={openHandoff}/>
    if (node.kind === 'preview') {
      const preview = previews[node.id]
      if (!preview) return <section class="workspace-leaf-placeholder"><strong>preview unavailable</strong><span>{node.id}</span></section>
      return <PreviewPane preview={preview} onClose={() => void (async () => {
        await updateLayout(preview.project_id, removeLeaf(layoutMap[preview.project_id] || emptyLayout(), 'preview', preview.id))
      })()} />
    }
    if (node.kind !== 'terminal') {
      return <section class="workspace-leaf-placeholder"><strong>{node.kind}</strong><span>{node.id}</span></section>
    }
    const session = sessions.find(item => item.id === node.id)
    if (!session) return null
    const id = session.id
    if(session.pending)return <section class={`terminal-pane pending-terminal-pane ${activeId===id?'focused':''}`} onPointerDown={()=>{setActiveId(id);setFocusedViewId(id)}}>
      <div class="pane-bar"><div><span class="pane-state starting">starting terminal…</span></div><div class="pane-path">{session.cwd}</div></div>
      <div class="pending-terminal-body" role="status" aria-live="polite"><span class="pending-terminal-spinner" aria-hidden="true"/><strong>Starting terminal</strong><small>Resolving the project and opening the shell…</small></div>
    </section>
    const displayedCwd=session.runtime_cwd||session.spawn_cwd||session.cwd
    const cwdIsLive=session.runtime_cwd_live
    const openPaneMenu=(event:{clientX:number;clientY:number;preventDefault?:()=>void;stopPropagation?:()=>void})=>{event.preventDefault?.();event.stopPropagation?.();openSessionMenu(session,event.clientX,event.clientY,'pane')}
    const voiceMode=voiceStatus?.enabled&&isAgent(session)?effectiveVoiceMode(session):'off'
    const voiceStripVisible=voiceStatus?.enabled&&isAgent(session)&&voiceMode!=='off'
    const terminalPane=<section class={`terminal-pane ${activeId === id ? 'focused' : ''} ${voiceStripVisible?'with-voice':''}`} onPointerDown={() => {setActiveId(id);setFocusedViewId(id)}}>
      <div class="pane-bar" onContextMenu={openPaneMenu} onDblClick={() => setZoomedId(current => current === id ? null : id)}>
        <div><span class={`pane-state ${session.state}`} title={[session.parser_diagnostic,session.delivery_readiness&&`delivery::${session.delivery_readiness.state} (${session.delivery_readiness.reason}) · authorized::no`].filter(Boolean).join('\n')}>{sessionStatus(session)}</span></div>
        <div class={`pane-path ${cwdIsLive?'live':'last-known'}`} title={cwdIsLive?`live cwd · ${displayedCwd}`:`last known (spawn) cwd · ${displayedCwd}`}>{cwdIsLive?'':<span>last-known::</span>}{displayedCwd}</div>
        <div class="pane-tools"><button class="pane-tool-label" aria-label={`Inspect processes for ${sessionName(session)}`} title="Processes and previews" onClick={() => {setActiveId(session.id);openProcessViewer(session)}}>proc</button>{voiceStatus?.enabled&&isAgent(session)&&<button class={`pane-tool-label voice-chip ${voiceMode}`} aria-label={`Read aloud mode for ${sessionName(session)}: ${voiceModeLabel(voiceMode)}. Click to change.`} title={`Read aloud: ${voiceModeLabel(voiceMode)} · click to cycle off → on demand → auto`} onClick={()=>cycleVoiceMode(session)}>tts:{voiceMode==='on_demand'?'tap':voiceMode}</button>}{voiceStatus?.stt_enabled&&isAgent(session)&&<MicButton sessionId={session.id}/>}<button aria-label={`More actions for ${sessionName(session)}`} title="Session actions" onClick={event=>{const rect=event.currentTarget.getBoundingClientRect();openPaneMenu({clientX:rect.right,clientY:rect.bottom,stopPropagation:()=>event.stopPropagation()})}}>⋯</button></div>
      </div>
      {voiceStripVisible&&voiceStatus&&<VoicePlayer session={session} status={voiceStatus} mode={voiceMode as 'on_demand'|'auto'} onSession={updateSession} />}
      <TerminalPane session={session} onState={updateSession} startupOrigin={startupOrigins.current[session.id]} onStartupTiming={(milestone,elapsedMs)=>recordClientStartupTiming(session.id,milestone,elapsedMs)} broadcast={broadcast} keybindings={keybindings} scrollback={xtermScrollback} mobileInput={mobileInput} />
    </section>
    if(insideStack)return terminalPane
    return <section class="pane-stack singleton-stack"><div class="stack-tabs" role="tablist" aria-label="Terminal tabs">
      <div class="stack-tab-shell"><button role="tab" aria-label={`${sessionName(session)} session tab`} aria-selected="true" class={`tab-main active ${session.state}`} onClick={()=>setActiveId(id)} onContextMenu={event=>{event.preventDefault();event.stopPropagation();setActiveId(id);openSessionMenu(session,event.clientX,event.clientY,'tab')}}><span class={`state-dot ${session.state}`}/>{sessionName(session)}</button><button class={`tab-close ${confirmKillId===id?'confirming':''}`} aria-label={`${confirmKillId===id?'Confirm close':'Close'} terminal: ${sessionName(session)}`} title={confirmKillId===id?'Confirm kill terminal':'Close and kill terminal'} onClick={event=>{event.stopPropagation();requestKill(session)}}>{confirmKillId===id?'✓':'×'}</button></div>
      <button class="stack-add" aria-label="New terminal tab" title="New terminal tab" onClick={()=>void spawnTerminal(session.project_id,'stack',undefined,id)}>+</button>
    </div><div class="stack-active">{terminalPane}</div></section>
  }

  const workspaceNoteIds=(targetProject:string)=>leaves(resolveLayout(layoutMap[targetProject],projects.find(item=>item.id===targetProject)?.layout),'note').map(leaf=>leaf.id)
  const sidebarNoteRow=(resourceId:string,targetProject:string)=>{
    const identity=parseNoteResourceId(resourceId)
    if(!identity)return null
    const noteLayout=resolveLayout(layoutMap[targetProject],projects.find(item=>item.id===targetProject)?.layout)
    const workspaceOpen=workspaceNoteIds(targetProject).includes(resourceId)
    const selected=targetProject===projectId&&stackForView(noteLayout,resourceId)?.active_child_id===resourceId
    const label=identity.kind==='note'?'Project note':identity.kind==='session-note'?'Session note':identity.kind==='files'?'Files':identity.id.split('/').pop()||'File'
    return <button class={`sidebar-note-row ${selected?'active':''} ${workspaceOpen?'open':''}`} title={`${label} · opens in the focused pane`} onContextMenu={event=>{event.preventDefault();event.stopPropagation();openNoteContext(resourceId,targetProject,event.clientX,event.clientY)}} onClick={event=>{event.stopPropagation();showNoteResource(resourceId,targetProject);setSidebarOpen(false)}}>
      <span class="note-branch" aria-hidden="true">└</span><span class="note-copy"><strong>{label}</strong></span>
    </button>
  }
  // A server a session spawned lives beside it: nested under its sidebar row and
  // activated as a tab in the same region, so it is always one click away.
  const sidebarPreviewRow=(preview:Preview,session:Session)=>{
    const layout=layoutMap[session.project_id]||parseLayout(projects.find(item=>item.id===session.project_id)?.layout)
    const previewStack=stackForView(layout,preview.id)
    const selected=previewStack?.active_child_id===preview.id
    return <button key={preview.id} class={`sidebar-note-row preview-row ${selected?'active':''}`} title={`${preview.url} · ${preview.source} preview spawned by this session`} onClick={event=>{event.stopPropagation();if(previewStack){setProjectId(session.project_id);setFocusedViewId(preview.id);void updateLayout(session.project_id,activateStackChild(layout,previewStack.id,preview.id))}else void openDetectedServer(preview,session);setSidebarOpen(false)}}>
      <span class="note-branch" aria-hidden="true">└</span><span class="note-copy"><strong>server :{preview.port}</strong></span>
    </button>
  }
  // A detected server has no preview yet. Opening one registers it and, because the
  // daemon groups previews, it lands as a tab beside this session.
  const openDetectedServer=async(server:Pick<DetectedServer,'url'>,session:Session)=>{
    try{
      const result=await api<{preview:Preview;project:Project}>('POST','/api/previews',{session_id:session.id,url:server.url,attach:true})
      setPreviews(current=>({...current,[result.preview.id]:result.preview}))
      setProjects(items=>items.map(item=>item.id===result.project.id?result.project:item))
      setLayoutMap(current=>({...current,[result.project.id]:parseLayout(result.project.layout)}))
      setProjectId(session.project_id)
      setFocusedViewId(result.preview.id)
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
    const sessionNoteId=noteResourceId('session-note',session.note_id||session.id)
    const showSessionNote=!!session.note_exists||workspaceNoteIds(session.project_id).includes(sessionNoteId)
    return <div class="session-entry"><button data-sidebar-session-id={session.id} data-sidebar-project-id={session.project_id} class={`session-row ${activeId === session.id ? 'active' : ''} ${session.state} ${session.pending?'pending-terminal-row':''}`} onPointerDown={event=>{if(!session.pending){beginLongPress(event,(x,y)=>openSessionMenu(session,x,y,'sidebar'));beginSessionPointerDrag(event,session)}}} onPointerUp={cancelLongPress} onPointerCancel={cancelLongPress} onPointerMove={cancelLongPress} onContextMenu={event => { event.preventDefault();if(!session.pending)openSessionMenu(session,event.clientX,event.clientY,'sidebar') }} onClick={() => {if(suppressDragClickRef.current===`session:${session.id}`){suppressDragClickRef.current=null;return}void selectSession(session)}}>
      <span class={`state-dot ${session.state}`} />
      <span class="session-copy"><strong>{isAgent(session) && <span class={`agent-prefix ${session.backend}`}>[{session.backend}]</span>}{sessionName(session)}{relation==='tab'&&<span class="layout-affinity tab" title="Shares one pane region with the other bracketed sessions">▤</span>}{session.broadcast&&<span class="broadcast-flag" title="In the broadcast set — keystrokes mirror here while broadcast input is on">⇶</span>}</strong><small class={isAgent(session) ? `agent-status ${session.state}` : ''}>{sessionStatus(session)}</small></span>
      <span class="session-meta">{isAgent(session) && <em class={`state-label ${session.state}`}>{session.state === 'idle' ? 'ready' : session.state}</em>}</span>
      {!session.pending&&<span class="row-actions" onPointerDown={event=>event.stopPropagation()} onClick={event => event.stopPropagation()}><button class={confirmKillId === session.id ? 'confirming' : ''} title={confirmKillId === session.id ? (isEndedSession(session) ? 'Confirm remove' : 'Confirm kill') : (isEndedSession(session) ? 'Remove from sidebar' : 'Kill')} onClick={() => runNamedCommand(`session.requestKill(${session.id})`)}>{confirmKillId === session.id ? '✓' : '×'}</button></span>}
    </button>{showSessionNote&&sidebarNoteRow(sessionNoteId,session.project_id)}{spawnedPreviews.map(preview=>sidebarPreviewRow(preview,session))}{spawnedServers.map(server=>sidebarServerRow(server,session))}</div>
  }
  const sidebarNode=(node:PaneNode|PaneLeaf|null|undefined,relation?:'tab'):ComponentChildren=>{
    if(!node)return null
    if(node.type==='leaf'){
      if(node.kind!=='terminal')return null
      const session=sessions.find(item=>item.id===node.id)
      return session?sessionRow(session,relation):null
    }
    const nodeLayout:PaneLayout={...emptyLayout(),root:node}
    const ids=terminalIds(nodeLayout)
    const branches=(node.type==='stack'?node.children:[node.first,node.second]).filter(child=>child.type==='leaf'?child.kind==='terminal':terminalIds({...emptyLayout(),root:child}).length>0)
    if(branches.length===0)return null
    if(branches.length===1)return sidebarNode(branches[0],relation)
    const label=node.type==='stack'?'Sessions sharing one tabbed pane':`${node.direction} split branches`
    const owner=sessions.find(item=>ids.includes(item.id))
    return <section data-sidebar-stack-id={node.type==='stack'?node.id:undefined} data-sidebar-project-id={node.type==='stack'?owner?.project_id:undefined} class={`layout-cluster ${node.type} ${node.type==='split'?node.direction:''}`} role="group" aria-label={label}>
      <span class="layout-cluster-glyph" aria-hidden="true" title={label}>{node.type==='stack'?'▤':node.direction==='horizontal'?'↔':'↕'}</span>
      {branches.map((child,index)=><div class={`layout-branch ${index===0?'first':''} ${index===branches.length-1?'last':''}`} key={child.id}>{sidebarNode(child,node.type==='stack'?'tab':undefined)}</div>)}
    </section>
  }

  const noteTabLabel=(resourceId:string)=>{
    const identity=parseNoteResourceId(resourceId)
    if(identity?.kind==='note')return 'Project note'
    if(identity?.kind==='session-note'){
      const owner=sessions.find(session=>(session.note_id||session.id)===identity.id)
      return owner?`Note · ${sessionName(owner)}`:'Session note'
    }
    if(identity?.kind==='files')return 'Files'
    return identity?.id.split('/').pop()||'File'
  }
  const projectPreviewIds=dragProject?.previewIds||orderedProjects.map(project=>project.id)
  const projectBuckets=[
    ...[...projectGroups].sort((a,b)=>a.position-b.position||a.name.localeCompare(b.name)).map(group=>({id:group.id,name:group.name,items:visibleProjects.filter(project=>project.group_id===group.id)})),
    {id:'ungrouped',name:'Projects',items:visibleProjects.filter(project=>!project.group_id||!projectGroups.some(group=>group.id===project.group_id))},
  ].filter(bucket=>bucket.items.length>0)

  const mobileProjection=mobileWorkspaceProjection(activeLayout,focusedViewId,activeId)
  const activateMobileTab=(leaf:PaneLeaf)=>{
    setFocusedViewId(leaf.id)
    if(leaf.kind==='terminal')setActiveId(leaf.id)
    const current=layoutValues.current[projectId]||activeLayout
    const pane=stackForView(current,leaf.id)
    if(pane&&pane.active_child_id!==leaf.id)void updateLayout(projectId,activateStackChild(current,pane.id,leaf.id))
  }
  const focusAfterMobileClose=(leaf:PaneLeaf)=>{
    const next=adjacentMobileTab(mobileProjection.tabs,leaf.id)
    setFocusedViewId(next?.id||null)
    if(next?.kind==='terminal')setActiveId(next.id)
  }
  const closeMobileTab=(leaf:PaneLeaf,session?:Session)=>{
    if(leaf.kind==='terminal'){
      if(!session||session.pending)return
      if(confirmKillId===leaf.id)focusAfterMobileClose(leaf)
      requestKill(session);return
    }
    focusAfterMobileClose(leaf)
    if(leaf.kind==='note'){void removeWorkspaceNote(projectId,leaf.id);return}
    const current=layoutValues.current[projectId]||activeLayout
    void updateLayout(projectId,removeLeaf(current,leaf.kind,leaf.id))
  }
  const mobileTab=(leaf:PaneLeaf):ComponentChildren=>{
    const selected=leaf.id===mobileProjection.selected?.id
    const session=leaf.kind==='terminal'?sessions.find(item=>item.id===leaf.id):undefined
    const preview=leaf.kind==='preview'?previews[leaf.id]:undefined
    const label=leaf.kind==='terminal'?session?.name||leaf.id:leaf.kind==='preview'?preview?.url||leaf.id:leaf.kind==='history'?'History':noteTabLabel(leaf.id)
    const visibleLabel=leaf.kind==='preview'?(preview?`:${preview.port}`:leaf.id):label
    const glyph=leaf.kind==='terminal'?<span class={`state-dot ${session?.state||'running'}`}/>:<span class="preview-tab-glyph" aria-hidden="true">{leaf.kind==='preview'?'◱':leaf.kind==='history'?'◷':'◇'}</span>
    const confirming=leaf.kind==='terminal'&&confirmKillId===leaf.id
    return <div key={`${leaf.kind}:${leaf.id}`} class="stack-tab-shell mobile-unified-tab">
      <button role="tab" aria-label={`${label} ${leaf.kind} tab`} title={label} aria-selected={selected} class={`tab-main ${selected?'active':''} ${session?.state||''}`} onClick={()=>activateMobileTab(leaf)} onContextMenu={event=>{event.preventDefault();event.stopPropagation();activateMobileTab(leaf);if(session&&!session.pending)openSessionMenu(session,event.clientX,event.clientY,'mobile');else if(leaf.kind!=='terminal')openTabMenu(leaf,label,event.clientX,event.clientY,'mobile')}}>{glyph}{visibleLabel}</button>
      <button class={`tab-close ${confirming?'confirming':''}`} disabled={leaf.kind==='terminal'&&(!session||!!session.pending)} aria-label={`${confirming?'Confirm close':'Close'} ${label}`} title={confirming?'Confirm kill terminal':`Close ${label}`} onClick={event=>{event.stopPropagation();closeMobileTab(leaf,session)}}>{confirming?'✓':'×'}</button>
    </div>
  }
  const mobileUnifiedWorkspace=<section class="pane-stack mobile-unified-workspace">
    <div class="stack-tabs mobile-unified-tabs" role="tablist" aria-label="All Project tabs">
      {mobileProjection.tabs.map(mobileTab)}
      <button class="stack-add" aria-label="New terminal tab" title="New terminal tab" disabled={!activeProject} onClick={()=>{if(!activeProject)return;const target=mobileProjection.selected&&stackForView(activeLayout,mobileProjection.selected.id)?mobileProjection.selected.id:null;void (target?spawnTerminal(activeProject.id,'stack',undefined,target):spawnTerminal(activeProject.id))}}>+</button>
    </div>
    <div class="stack-active mobile-unified-active">{mobileProjection.selected?renderPaneNode(mobileProjection.selected,'mobile',true):<div class="empty-stage"><div class="hero-terminal" aria-hidden="true">&gt;_</div><h1>Your Project workspace.</h1><p>Open a terminal, Project note, Files, or a preview to begin.</p></div>}</div>
  </section>

  return <div class="app-shell">
    <div class="sr-only" role="status" aria-live="polite" aria-atomic="true">{attention ? `${attention} agent${attention === 1 ? '' : 's'} awaiting attention` : 'No agents awaiting attention'}</div>
    <div class="mobile-toolbar">
      <button class="nav-toggle mobile-nav-toggle" onClick={() => setSidebarOpen(value => !value)}>:nav</button>
      <strong class="mobile-project-name" title={activeProject?.name||'No Project selected'}>{activeProject?.name||'No Project'}</strong>
      <button class="mobile-run-trigger" disabled={!activeProject} onClick={event=>activeProject&&openRunMenu(activeProject,event.currentTarget)}>▶ Run</button>
      <AccountSwitcher compact onManage={()=>openSettings('Accounts')}/>
    </div>

    <ContinuityBanner />
    {broadcast && <div class="broadcast-banner"><strong>Broadcast input is on</strong><span>Keystrokes mirror to sessions in the broadcast set.</span><button onClick={() => setBroadcast(false)}>Stop broadcasting</button></div>}

    <div class={`workspace ${sidebarCollapsed?'sidebar-collapsed':''}`} style={{'--sidebar-width':`${sidebarWidth}px`} as JSX.CSSProperties}>
      <header class="app-topbar">
        <div class="app-identity"><strong>swe_mux</strong><button class="sidebar-collapse" aria-label={sidebarCollapsed?'Expand sidebar':'Collapse sidebar'} title={sidebarCollapsed?'Expand sidebar':'Collapse sidebar'} onClick={toggleSidebar}>{sidebarCollapsed?'»':'«'}</button><span class="daemon-ok" title="daemon::connected" aria-label="daemon connected"><i aria-hidden="true" /></span>{activeProject&&<button class="project-run-header" title={`Run in ${activeProject.name}`} onClick={event=>openRunMenu(activeProject,event.currentTarget)}>▶ Run</button>}</div>
      </header>
      <aside class={`sidebar ${sidebarOpen ? 'open' : ''}`} onContextMenu={event=>{const target=event.target as Element;if(target.closest('.sidebar-heading,.project-row,.session-row,.sidebar-note-row,.sidebar-footer'))return;event.preventDefault();setContextMenu(null);setProjectMenu(null);setNoteMenu(null);setMainMenuOpen(false);setSidebarMenu({x:event.clientX,y:event.clientY})}}>
        <div class="project-tree">
          {visibleProjects.length===0&&<button class="empty-project-cta" onClick={openProjectsManager}><strong>{projects.length?'No Projects shown':'Create your first Project'}</strong><small>{projects.length?'Open Projects to show or add an active Project.':'Open Projects to add a canonical folder.'}</small></button>}
          {projectBuckets.map(bucket=>{const peerIds=bucket.items.map(item=>item.id);return <section class="sidebar-project-bucket" key={bucket.id}><header><span>{bucket.name}</span>{bucket.id!=='ungrouped'&&<><button title="Rename group" onClick={()=>{const group=projectGroups.find(item=>item.id===bucket.id);if(group)setGroupEdit({id:group.id,name:group.name})}}>✎</button><button title="Remove group (projects become ungrouped)" onClick={()=>{const group=projectGroups.find(item=>item.id===bucket.id);if(group)void deleteGroup(group)}}>×</button></>}</header>{bucket.items.map(project => {
            const children = sessions
              .filter(session => session.project_id === project.id)
              .sort((a,b)=>a.created_at-b.created_at||a.id.localeCompare(b.id))
            const projectLayout=resolveLayout(layoutMap[project.id],project.layout)
            const projectPaneIds=terminalIds(projectLayout)
            const unpanedChildren=children.filter(session=>!projectPaneIds.includes(session.id))
            const dropClass=dragProject?.overId===project.id&&dragProject.side?`project-drop-target drop-${dragProject.side}`:''
            return <section key={project.id} data-reorder-id={project.id} style={{order:projectPreviewIds.indexOf(project.id)}} class={`project-group ${project.id === projectId ? 'active' : ''} ${dropClass}`}>
              <div class={`project-row draggable-project ${dragProject?.id===project.id?'dragging':''}`} title="Drag to reorder Project" onPointerDown={event=>{beginLongPress(event,(x,y)=>setProjectMenu({project,x,y}));beginProjectPointerDrag(event,project,peerIds)}} onPointerUp={cancelLongPress} onPointerCancel={cancelLongPress} onPointerMove={cancelLongPress} onContextMenu={event => { event.preventDefault(); setProjectMenu({ project, x: event.clientX, y: event.clientY }) }} onClick={()=>{if(suppressDragClickRef.current===`project:${project.id}`){suppressDragClickRef.current=null;return}setProjectId(project.id)}}>
                <span class="project-chevron" aria-hidden="true">{project.id === projectId ? '◆' : '◇'}</span><strong>{project.name}</strong><button class="project-row-run" title={`Run in ${project.name}`} aria-label={`Run in ${project.name}`} onPointerDown={event=>event.stopPropagation()} onClick={event=>{event.stopPropagation();openRunMenu(project,event.currentTarget)}}>▶</button>
              </div>
              <div class="project-note-list">{sidebarNoteRow(noteResourceId('note',project.id),project.id)}{sidebarNoteRow(noteResourceId('files',project.id),project.id)}</div>
              <div class="session-list">
                {sidebarNode(projectLayout.root)}
                {unpanedChildren.map(session=>sessionRow(session))}
              </div>
            </section>
          })}</section>})}
        </div>
        <div class="sidebar-status">
          <AccountSwitcher onManage={()=>openSettings('Accounts')}/>
          <ResourceUsageSummary snapshot={processFleet} sessions={sessions} projects={projects} onRefresh={()=>void loadProcesses()} onOpenFleet={()=>openProcessViewer()}/>
        </div>
        <div class="sidebar-footer"><button class="menu-trigger" onClick={() => setMainMenuOpen(value => !value)}><span>:</span> menu</button><button class="project-trigger" onClick={openProjectsManager}><span>◇</span> projects</button></div>
      </aside>
      <div class="sidebar-resizer" role="separator" tabindex={0} aria-label="Resize sidebar" aria-orientation="vertical" aria-valuemin={190} aria-valuemax={480} aria-valuenow={Math.round(sidebarWidth)} title="Drag to resize · arrow keys adjust · double-click to reset" onPointerDown={beginSidebarResize} onDblClick={()=>persistSidebarWidth(254)} onKeyDown={event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();persistSidebarWidth(event.key==='Home'?190:event.key==='End'?480:sidebarWidth+(event.key==='ArrowLeft'?-10:10))}} />

      <main class="main-stage" onContextMenu={event => { if (!activeLayout.root) { event.preventDefault(); setEmptyMenu({ x: event.clientX, y: event.clientY }) } }}>
        <div class="project-workspace unified-workspace">
          <div class="terminal-workspace">
            {mobileWorkspace?mobileUnifiedWorkspace:(activeLayout.root||focusedOutsideLayout) ? <div class="pane-tree">{renderPaneNode(zoomedId ? stackForView(activeLayout,zoomedId)||activeLayout.root! : focusedOutsideLayout&&activeId ? paneStack([terminalLeaf(activeId)],activeId) : activeLayout.root!)}</div> : <div class="pane-tree"><section class="pane-stack empty-workspace-pane">
              <div class="stack-tabs" role="tablist" aria-label="Workspace tabs"><button class="stack-add" aria-label="New terminal tab" title="New terminal tab" disabled={!activeProject} onClick={()=>activeProject&&void spawnTerminal(activeProject.id)}>+</button></div>
              <div class="stack-active empty-stage"><div class="hero-terminal" aria-hidden="true">&gt;_</div><h1>Your Project workspace.</h1><p>Open a terminal, Project note, Files, or a preview to begin.</p></div>
            </section></div>}
          </div>
        </div>
      </main>
    </div>

    {launcherOpen && <div class="quick-launcher" role="dialog" aria-modal="true" aria-label="New terminal custom">
      <div class="quick-heading"><span>NEW TERMINAL CUSTOM::{projects.find(project => project.id === launcherProject)?.name?.toUpperCase()}{launcherSplit?'::SPLIT':''}</span><button onClick={() => setLauncherOpen(false)}>×</button></div>
      <form onSubmit={event => { event.preventDefault(); void spawnTerminal(launcherProject, launcherSplit, launcherProfile) }}>
        <label>Shell profile<select value={launcherProfile} onChange={event=>setLauncherProfile(event.currentTarget.value)}>{profiles.map(profile=><option value={profile.id}>{profile.marker} · {profile.label}</option>)}</select><small>{profiles.find(profile=>profile.id===launcherProfile)?.capabilities.join(' · ')}</small></label>
        <label>Project root<input value={projects.find(project=>project.id===launcherProject)?.root||''} readOnly /></label>
        <button class="primary" type="submit">Open {profiles.find(item=>item.id===launcherProfile)?.label || 'terminal'}</button>
      </form>
    </div>}

    {runMenu&&<ProjectRunMenu project={runMenu.project} anchor={{x:runMenu.x,y:runMenu.y}} onClose={()=>setRunMenu(null)} onLaunch={backend=>{const target=runMenu.project.id;setRunMenu(null);void spawnTerminal(target,false,undefined,undefined,'after',backend)}} onCustom={()=>{const target=runMenu.project.id;setRunMenu(null);openLauncher(target)}} onSessions={items=>void attachActionSessions(runMenu.project.id,items)} onError={setError}/>}

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
      {contextMenu.source==='sidebar'&&<button onClick={() => runNamedCommand('session.open')}>Open in focused pane</button>}
      {['exited', 'crashed'].includes(contextMenu.session.state) && isAgent(contextMenu.session) && <button onClick={() => runNamedCommand('session.resume')}>Resume as new…</button>}
      <button onClick={() => runNamedCommand('session.copyId')}>Copy session ID</button>
      <button onClick={() => runNamedCommand('session.copyCwd')}>Copy working directory</button>
      <button onClick={() => runNamedCommand('session.note')}>Open session note</button>
      <button onClick={()=>{setContextMenu(null);setPromptLibraryOpen(true)}}>Insert prompt template…</button>
      {contextMenu.source!=='mobile'&&directionRow('Open in split:',option=>{const leaf=terminalLeaf(contextMenu.session.id);if(contextMenu.source==='tab'||contextMenu.source==='pane')void splitExistingLeaf(leaf,contextMenu.session.project_id,option.direction,option.position);else{const current=resolveLayout(layoutMap[contextMenu.session.project_id],projects.find(project=>project.id===contextMenu.session.project_id)?.layout);const target=leaves(current).find(item=>item.id!==contextMenu.session.id)?.id||null;void openInSplit(contextMenu.session,option.direction,option.position,target)}},()=>contextMenu.source==='sidebar'||(stackForView(activeLayout,contextMenu.session.id)?.children.length||0)>1)}
      {contextMenu.source!=='mobile'&&directionRow('New terminal in split:',option=>{setContextMenu(null);void spawnTerminal(contextMenu.session.project_id,option.direction,undefined,contextMenu.session.id,option.position)})}
      {(contextMenu.source==='tab'||contextMenu.source==='pane')&&directionRow('Move tab:',option=>void moveTabDirection(terminalLeaf(contextMenu.session.id),contextMenu.session.project_id,option.id),direction=>!!paneNeighborIds(activeLayout,contextMenu.session.id)[direction])}
      {contextMenu.source==='sidebar'&&<button disabled={!activeId||activeId===contextMenu.session.id} onClick={()=>runNamedCommand('session.groupStack')}>Stack with focused terminal</button>}
      <button onClick={() => runNamedCommand('processes.open')}>Processes and previews…</button>
      <button onClick={()=>runNamedCommand('pane.stackNew')}>New terminal as tab</button>
      {contextMenu.source!=='mobile'&&activeStack&&activeStack.children.length>1&&<button onClick={()=>runNamedCommand('stack.dissolve')}>Dissolve tab stack into splits</button>}
      {contextMenu.source!=='mobile'&&<button onClick={() => runNamedCommand('session.customSplit')}>New terminal custom in split…</button>}
      {voiceStatus?.enabled&&isAgent(contextMenu.session)&&<>
        <div class="context-subtitle">READ ALOUD</div>
        {(['off','on_demand','auto'] as VoiceMode[]).map(mode=><button key={mode} onClick={()=>{void setVoiceMode(contextMenu.session,mode);setContextMenu(null)}}>{effectiveVoiceMode(contextMenu.session)===mode?'✓ ':''}{mode==='off'?'Off':mode==='on_demand'?'On demand':'Auto on reply'}</button>)}
        <button onClick={()=>{const target=contextMenu.session;setContextMenu(null);void speakLastReply(target)}}>Speak last reply now</button>
      </>}
      <div class="context-rule" />
      <button onClick={() => runNamedCommand('session.broadcastMembership')}>{contextMenu.session.broadcast ? 'Remove from broadcast' : 'Add to broadcast'}</button>
      <button class="danger" onClick={() => runNamedCommand('session.killImmediate')}>{isEndedSession(contextMenu.session) ? 'Remove from sidebar' : 'Kill session'}</button>
    </div>}

    {projectMenu && <div class="context-menu" role="menu" aria-label={`Project actions for ${projectMenu.project.name}`} style={{ left: clampContextMenuLeft(projectMenu.x, innerWidth), top: Math.max(4, Math.min(projectMenu.y, innerHeight - 310)) }}>
      <div class="context-title"><strong>{projectMenu.project.name}</strong></div>
      <button onClick={() => runNamedCommand('project.newTerminal')}>New terminal</button>
      <button onClick={() => runNamedCommand('project.newTerminalCustom')}>New terminal custom…</button>
      <button onClick={()=>{openProjectFiles(projectMenu.project);setProjectMenu(null)}}>Browse files…</button>
      <button onClick={() => runNamedCommand('project.reveal')}>Reveal in Explorer</button>
      <label class="context-select">Group<select value={projectMenu.project.group_id||''} onChange={event=>{const target=projectMenu.project;const group_id=event.currentTarget.value||null;void api<Project>('PATCH',`/api/projects/${target.id}`,{group_id}).then(updated=>setProjects(items=>items.map(item=>item.id===updated.id?updated:item)));setProjectMenu(null)}}><option value="">Ungrouped</option>{projectGroups.map(group=><option value={group.id}>{group.name}</option>)}</select></label>
      <button onClick={() => runNamedCommand('project.rename')}>Rename project</button>
      <button disabled={!commands.find(item=>item.id==='project.moveUp')?.available} onClick={()=>runNamedCommand('project.moveUp')}>Move Project up</button>
      <button disabled={!commands.find(item=>item.id==='project.moveDown')?.available} onClick={()=>runNamedCommand('project.moveDown')}>Move Project down</button>
      <button onClick={() => runNamedCommand('project.settings')}>Project defaults in Settings…</button>
      {confirmProjectDeleteId !== projectMenu.project.id && <button class="danger" disabled={sessions.some(session=>session.project_id===projectMenu.project.id)} onClick={() => runNamedCommand('project.delete')}>Delete project…</button>}
      {confirmProjectDeleteId === projectMenu.project.id && <>
        <div class="context-subtitle">DELETE PROJECT REGISTRATION</div>
        <button class="danger" onClick={() => { const target = projectMenu.project; setProjectMenu(null); void deleteProject(target) }}>Confirm delete</button>
        <button onClick={() => setConfirmProjectDeleteId(null)}>Cancel</button>
      </>}
    </div>}

    {sidebarMenu&&<div class="context-menu" role="menu" aria-label="Sidebar actions" style={{left:clampContextMenuLeft(sidebarMenu.x,innerWidth),top:Math.max(4,Math.min(sidebarMenu.y,innerHeight-190))}}>
      <div class="context-title"><strong>PROJECTS</strong></div>
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('project.create')}}>Manage projects…</button>
      <button onClick={()=>{setSidebarMenu(null);setGroupEdit({name:''})}}>Create group</button>
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('processes.all')}}>All processes and previews…</button>
      <div class="context-rule" />
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('settings.open')}}>All Settings…</button>
    </div>}

    {noteMenu&&<div class="context-menu" role="menu" aria-label="Resource view actions" style={{left:clampContextMenuLeft(noteMenu.x,innerWidth),top:Math.max(4,Math.min(noteMenu.y,innerHeight-220))}}>
      <div class="context-title"><strong>{noteTabLabel(noteMenu.resourceId)}</strong></div>
      <button onClick={()=>void placeNoteResourceInFocusedPane(noteMenu.resourceId,noteMenu.projectId)}>{mobileWorkspace?'Open tab':'Open in focused pane'}</button>
      {!mobileWorkspace&&directionRow('Open in split:',option=>void splitNoteResource(noteMenu.resourceId,noteMenu.projectId,option.direction,option.position))}
      {workspaceNoteIds(noteMenu.projectId).includes(noteMenu.resourceId)&&<><div class="context-rule"/><button onClick={()=>{const target=noteMenu;setNoteMenu(null);void removeWorkspaceNote(target.projectId,target.resourceId)}}>Close resource tab</button></>}
    </div>}

    {tabMenu&&<div class="context-menu tab-context-menu" role="menu" aria-label={`Tab actions for ${tabMenu.label}`} style={{left:clampContextMenuLeft(tabMenu.x,innerWidth),top:Math.max(4,Math.min(tabMenu.y,innerHeight-300))}}>
      <div class="context-title"><strong>{tabMenu.label}</strong></div>
      {tabMenu.source==='tab'&&directionRow('Open in split:',option=>void splitExistingLeaf(tabMenu.leaf,tabMenu.projectId,option.direction,option.position),()=>{const current=resolveLayout(layoutMap[tabMenu.projectId],projects.find(project=>project.id===tabMenu.projectId)?.layout);return (stackForView(current,tabMenu.leaf.id)?.children.length||0)>1})}
      {tabMenu.source==='tab'&&directionRow('New terminal in split:',option=>{const target=tabMenu;setTabMenu(null);void spawnTerminal(target.projectId,option.direction,undefined,target.leaf.id,option.position)})}
      {tabMenu.source==='tab'&&directionRow('Move tab:',option=>void moveTabDirection(tabMenu.leaf,tabMenu.projectId,option.id),direction=>{const current=resolveLayout(layoutMap[tabMenu.projectId],projects.find(project=>project.id===tabMenu.projectId)?.layout);return !!paneNeighborIds(current,tabMenu.leaf.id)[direction]})}
      {tabMenu.source==='mobile'&&<button onClick={()=>{const target=tabMenu;setTabMenu(null);void spawnTerminal(target.projectId,'stack',undefined,target.leaf.id)}}>New terminal as tab</button>}
      <div class="context-rule"/><button onClick={()=>{const target=tabMenu;setTabMenu(null);const current=resolveLayout(layoutMap[target.projectId],projects.find(project=>project.id===target.projectId)?.layout);void updateLayout(target.projectId,removeLeaf(current,target.leaf.kind,target.leaf.id))}}>Close tab</button>
    </div>}

    {emptyMenu && <div class="context-menu" role="menu" style={{ left: clampContextMenuLeft(emptyMenu.x, innerWidth), top: Math.min(emptyMenu.y, innerHeight - 280) }}>
      <div class="context-title"><strong>EMPTY PANE</strong></div>
      <button role="menuitem" onClick={() => { setEmptyMenu(null); void spawnTerminal() }}>New terminal</button>
      <button role="menuitem" onClick={() => { setEmptyMenu(null); openLauncher() }}>New terminal custom…</button>
      {unpanned.length > 0 && <div class="context-subtitle">ATTACH LIVE SESSION</div>}
      {unpanned.map(session => <button role="menuitem" onClick={() => runNamedCommand(`session.attach(${session.id})`)}><span class={`state-dot ${session.state}`} />{sessionName(session)}</button>)}
    </div>}

    {mainMenuOpen && <div class="context-menu main-menu" role="menu" aria-label="swe-mux menu">
      <div class="context-title"><strong>swe-mux menu</strong></div>
      <div class="context-subtitle">PROJECT</div>
      <button onClick={() => { setMainMenuOpen(false); runNamedCommand('session.spawnShell') }}>New terminal in current project</button>
      <button onClick={() => { setMainMenuOpen(false); runNamedCommand('session.quickLaunch') }}>New terminal custom…</button>
      <button onClick={() => { setMainMenuOpen(false); runNamedCommand('history.open') }}>Session history</button>
      <button onClick={() => runNamedCommand('project.create')}>Projects…</button>
      <button onClick={() => runNamedCommand('processes.all')}>Process fleet…</button>
      <button onClick={()=>runNamedCommand('prompts.open')}>Prompt library…</button>
      <button onClick={() => runNamedCommand('notifications.open')}>Notifications{notificationUnread?` [${notificationUnread} new]`:''}</button>
      <button onClick={() => { setMainMenuOpen(false); runNamedCommand('broadcast.toggle') }}>{broadcast ? 'Stop broadcast input' : 'Start broadcast input'}</button>
      <div class="context-subtitle">CONFIGURATION</div>
      <button disabled={!activeProject} onClick={() => runNamedCommand('settings.project')}>Project settings…</button>
      <button onClick={() => runNamedCommand('usage.open')}>Usage analytics…</button>
      <button onClick={() => runNamedCommand('hooks.open')}>Automation…</button>
      <button onClick={() => runNamedCommand('settings.open')}>All Settings…</button>
      <div class="context-subtitle">SHORTCUTS</div>
      <button onClick={() => { setMainMenuOpen(false); runNamedCommand('palette.open') }}>Command palette <span class="menu-hint">ctrl alt p</span></button>
    </div>}

    {sidebarOpen && <button class="sidebar-scrim" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />}

    {renameTarget && <div class="modal-layer" onMouseDown={event => event.target === event.currentTarget && setRenameTarget(null)}>
      <form class="modal rename-modal" onSubmit={event => { event.preventDefault(); void submitRename() }}>
        <div class="modal-heading"><div><span>RENAME::{renameTarget.kind.toUpperCase()}</span><h2>{renameTarget.kind === 'session' ? sessionName(renameTarget.session) : renameTarget.project.name}</h2></div><button type="button" aria-label="Close rename" onClick={() => setRenameTarget(null)}>×</button></div>
        <label>name<input value={renameValue} onInput={event => setRenameValue(event.currentTarget.value)} autofocus /></label>
        <div class="modal-footer"><span>enter::save · esc::cancel</span><button type="button" onClick={() => setRenameTarget(null)}>Cancel</button><button class="primary" type="submit" disabled={!renameValue.trim()}>Rename</button></div>
      </form>
    </div>}

    {projectsManagerOpen&&<ProjectsManager projects={projects} groups={projectGroups} sessions={sessions} onClose={()=>setProjectsManagerOpen(false)} onAdd={()=>void createProject()} onAddGroup={()=>setGroupEdit({name:''})} onOpen={project=>{setProjectId(project.id);setProjectsManagerOpen(false)}} onSettings={project=>{setProjectsManagerOpen(false);openSettings('Current project',project.root)}} onNote={project=>{setProjectsManagerOpen(false);openProjectNotes(project)}} onFiles={project=>{setProjectsManagerOpen(false);openProjectFiles(project)}} onPatch={patchManagedProject} onDelete={deleteProject}/>}

    {projectCreateOpen&&<div class="modal-layer project-registry-dialog-layer" onMouseDown={event=>event.target===event.currentTarget&&setProjectCreateOpen(false)}><form class="modal" onSubmit={event=>{event.preventDefault();void submitProject()}}><div class="modal-heading"><div><span>PROJECT::CREATE</span><h2>Add a project</h2></div><button type="button" onClick={()=>setProjectCreateOpen(false)}>×</button></div><label>Name<input value={projectCreate.name} onInput={event=>setProjectCreate(value=>({...value,name:event.currentTarget.value}))} autofocus /></label><label>Folder<div class="project-folder-field"><input value={projectCreate.root} onInput={event=>setProjectCreate(value=>({...value,root:event.currentTarget.value}))} placeholder="D:\\projects\\horizon" /><button type="button" onClick={()=>setFolderPickerOpen(true)}>Browse…</button></div></label><label>Group<select value={projectCreate.group_id} onChange={event=>setProjectCreate(value=>({...value,group_id:event.currentTarget.value}))}><option value="">Ungrouped</option>{projectGroups.map(group=><option value={group.id}>{group.name}</option>)}</select></label><p class="modal-note">Creating the project initializes .swe-mux in this folder. Every session starts at this exact root.</p><div class="modal-footer"><button type="button" onClick={()=>setProjectCreateOpen(false)}>Cancel</button><button class="primary" type="submit" disabled={!projectCreate.name.trim()||!projectCreate.root.trim()}>Create project</button></div></form></div>}
    {folderPickerOpen&&<DirectoryPicker initialPath={projectCreate.root} onCancel={()=>setFolderPickerOpen(false)} onSelect={root=>{setProjectCreate(value=>({...value,root,name:folderNameFromPath(root)}));setFolderPickerOpen(false)}} />}

    {groupEdit&&<div class="modal-layer project-registry-dialog-layer" onMouseDown={event=>event.target===event.currentTarget&&setGroupEdit(null)}><form class="modal rename-modal" onSubmit={event=>{event.preventDefault();void submitGroup()}}><div class="modal-heading"><div><span>GROUP::{groupEdit.id?'RENAME':'CREATE'}</span><h2>Sidebar group</h2></div><button type="button" onClick={()=>setGroupEdit(null)}>×</button></div><label>Name<input value={groupEdit.name} onInput={event=>setGroupEdit(current=>current?{...current,name:event.currentTarget.value}:current)} autofocus /></label><p class="modal-note">Groups only organize the sidebar. They never affect sessions, panes, or project data.</p><div class="modal-footer"><button type="button" onClick={()=>setGroupEdit(null)}>Cancel</button><button class="primary" type="submit" disabled={!groupEdit.name.trim()}>Save group</button></div></form></div>}

    {historyOpen && <div class="history-layer" role="dialog" aria-modal="true" aria-label="Agent session history">
      <div class="history-header"><div><span>SESSION ARCHIVE</span><h2>History</h2></div><button onClick={() => setHistoryOpen(false)}>×</button></div>
      <div class="history-body">
        <aside><div class="history-search"><input placeholder="Search agent history…" value={historyQuery} onInput={event => { const query = event.currentTarget.value; setHistoryQuery(query); void loadHistory({ query }) }} />
          <select aria-label="Filter history backend" value={historyBackend} onChange={event => { const backend = event.currentTarget.value; setHistoryBackend(backend); void loadHistory({ backend }) }}><option value="">Claude + Codex</option><option value="claude">Claude</option><option value="codex">Codex</option></select>
          <select aria-label="Filter history state" value={historyState} onChange={event => { const state=event.currentTarget.value;setHistoryState(state);void loadHistory({state}) }}><option value="">All states</option><option value="idle">Completed</option><option value="exited">Exited</option><option value="crashed">Crashed</option></select>
          <select aria-label="Filter history project" value={historyProject} onChange={event => { const project=event.currentTarget.value;setHistoryProject(project);void loadHistory({project}) }}><option value="">All projects</option>{projects.map(project=><option value={project.id}>{project.name}</option>)}</select>
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
              {!collapsed && entries.map(entry => <article class={`history-row ${transcript?.entry.id === entry.id ? 'active' : ''}`}><button onClick={() => void viewHistory(entry)}><strong>[{entry.backend}] {historyName(entry)}</strong><span>{new Date(entry.spawned_at * 1000).toLocaleString()}</span><small>{entry.final_state || entry.exit_reason || 'indexed'}{entry.external ? ' · external' : ''}{entry.compaction_count?` · compacted ${entry.compaction_count}×`:''}</small></button><button class={confirmHistoryDelete === entry.id ? 'danger confirming' : 'danger'} aria-label={`Delete history index entry ${historyName(entry)}`} onClick={() => void deleteHistory(entry)}>{confirmHistoryDelete === entry.id ? '✓' : '×'}</button></article>)}
            </section>
          })}
          {historyLoading && <div class="history-inline-state">Loading agent history…</div>}
          {historyNext && !historyLoading && <button class="history-load-more" onClick={() => void loadHistory({ append: true })}>Load more</button>}
        </aside>
        <main>{transcript ? <><div class="transcript-heading"><button class="history-back" onClick={()=>setTranscript(null)}>← Back</button><div><h3>[{transcript.entry.backend}] {historyName(transcript.entry)}</h3><span>{transcript.entry.project_label || 'Ungrouped'} · {transcript.entry.cwd}</span><small>{transcript.entry.exit_reason || transcript.entry.final_state || 'indexed'} · {transcript.entry.model || 'model unavailable'} · {transcript.entry.external ? 'external' : 'mux session'}</small><small>{transcript.entry.context_window ? `context final ${Math.round((transcript.entry.final_context_pct || 0) * 100)}% · peak ${Math.round((transcript.entry.peak_context_pct || 0) * 100)}% · ${transcript.entry.measurement_source || 'native observation'}` : 'context unavailable'} · tokens in {transcript.entry.tokens_in || 0} / out {transcript.entry.tokens_out || 0}</small>{transcript.entry.compaction_count?<small>explicit compactions {transcript.entry.compaction_count} · last {transcript.entry.last_compaction_at?new Date(transcript.entry.last_compaction_at*1000).toLocaleString():'unknown'} · {transcript.entry.compaction_capability||'native evidence'} · confidence {transcript.entry.compaction_confidence||'unknown'}</small>:<small>compaction count unavailable — token drops are not treated as compaction evidence</small>}</div><button class="primary" onClick={() => void resumeHistoryEntry(transcript.entry)}>Resume as new</button></div>
          <div class="transcript-actions">{transcript.entry.project_id&&<button onClick={()=>{const project=projects.find(item=>item.id===transcript.entry.project_id);if(project)openProjectNotes(project)}}>Project note</button>}<button onClick={()=>void openHandoff(transcript.entry)}>Export handoff</button><button class="primary" onClick={()=>void previewSecondOpinion(transcript.entry)}>Review with {transcript.entry.backend==='claude'?'Codex':'Claude'}</button></div>{lineage.length>0&&<section class="transcript-lineage"><h4>Work lineage</h4>{lineage.map(edge=><article><strong>{edge.relation}</strong><span>{edge.parent_run_id} → {edge.child_run_id}</span><small>{new Date(edge.created_at*1000).toLocaleString()}</small></article>)}</section>}{transcript.annotations.length>0&&<section class="transcript-annotations"><h4>Run notes</h4>{transcript.annotations.map(item=><details><summary>{item.tag} · {item.content}</summary><small>{new Date(item.created_at*1000).toLocaleString()} · {item.provenance} · model::{item.resolved_model||'deterministic'} · confidence::{item.confidence??'—'} · cost::{annotationMoney.format(item.cost_usd||0)}</small></details>)}</section>}<div class="messages">{transcript.messages.length ? transcript.messages.map(message => <article class={message.role}><header>{message.role}</header>{message.content.map(block => block.type === 'text' ? <p>{block.text}</p> : <pre>{block.type === 'tool_use' ? `${block.name}\n${JSON.stringify(block.input, null, 2)}` : block.type}</pre>)}</article>) : <div class="no-transcript">No native transcript is available for this session.</div>}</div></> : <div class="history-placeholder"><span>◷</span><strong>Select a session</strong><p>Read its native transcript without resuming it.</p></div>}</main>
      </div>
    </div>}

    {reviewState&&<div class="modal-layer control-plane-modal-layer" role="dialog" aria-modal="true" aria-label="Cross-vendor second opinion" onMouseDown={event=>event.target===event.currentTarget&&setReviewState(null)}><section class="modal control-plane-modal"><div class="modal-heading"><div><span>CROSS-VENDOR REVIEW</span><h2>{reviewState.preview.source_backend} → {reviewState.preview.backend}</h2></div><button aria-label="Close review" onClick={()=>setReviewState(null)}>×</button></div><div class="control-plane-modal-body"><p>This is user-initiated. The generated prompt is shown in full and no rule or observer can start this session.</p><label>Target project<select value={reviewState.project} onChange={event=>setReviewState(current=>current?{...current,project:event.currentTarget.value}:current)}>{projects.map(project=><option value={project.id}>{project.name}</option>)}</select></label><label>Additional review instructions<textarea value={reviewState.instructions} onInput={event=>setReviewState(current=>current?{...current,instructions:event.currentTarget.value,dirty:true}:current)} placeholder="Optional constraints or review focus" /></label><label>Reviewed prompt<textarea class="review-prompt" readOnly value={reviewState.preview.prompt}/></label>{reviewState.dirty&&<p class="modal-warning">Instructions changed. Refresh the prompt before spawning.</p>}{reviewState.error&&<p class="modal-warning" role="alert">{reviewState.error}</p>}</div><div class="modal-footer"><span>{reviewState.loading?'working…':reviewState.dirty?'preview stale':'prompt reviewed'}</span><button onClick={()=>setReviewState(null)}>Cancel</button><button onClick={()=>void refreshSecondOpinion()} disabled={reviewState.loading}>Refresh preview</button><button class="primary" disabled={reviewState.loading||reviewState.dirty} onClick={()=>void confirmSecondOpinion()}>Spawn {reviewState.preview.backend} review</button></div></section></div>}

    {handoffState&&<div class="modal-layer control-plane-modal-layer" role="dialog" aria-modal="true" aria-label="Handoff export" onMouseDown={event=>event.target===event.currentTarget&&setHandoffState(null)}><section class="modal control-plane-modal"><div class="modal-heading"><div><span>HANDOFF::EXPORT</span><h2>{historyName(handoffState.entry)}</h2></div><button aria-label="Close handoff" onClick={()=>setHandoffState(null)}>×</button></div><div class="control-plane-modal-body"><p>{handoffState.message}</p><textarea class="handoff-export" readOnly value={handoffState.markdown}/></div><div class="modal-footer"><span>read-only annotation export</span><button onClick={()=>{const blob=new Blob([handoffState.markdown],{type:'text/markdown'});const url=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=url;anchor.download=`handoff-${handoffState.entry.id}.md`;anchor.click();URL.revokeObjectURL(url)}}>Download</button><button class="primary" onClick={()=>void navigator.clipboard.writeText(handoffState.markdown).then(()=>setHandoffState(current=>current?{...current,message:'Copied to clipboard.'}:current)).catch(()=>setHandoffState(current=>current?{...current,message:'Clipboard blocked. Select the text and copy it manually.'}:current))}>Copy</button></div></section></div>}

    {settingsOpen && <Settings cwd={settingsCwd||activeProject?.root} initialSection={settingsSection} onOpenUsage={()=>{setSettingsOpen(false);setUsageOpen(true)}} onOpenAutomation={()=>{setSettingsOpen(false);setAutomationOpen(true)}} onClose={() => { setSettingsOpen(false);setSettingsCwd(null); void refresh(); void loadProfiles(); void api<Record<string,unknown>>('GET','/api/config').then(config=>setMobileInput(mobileInputSettings(config))) }} />}

    {promptLibraryOpen&&<PromptLibrary project={activeProject} backend={active?.backend} onClose={()=>setPromptLibraryOpen(false)} onInsert={text=>window.dispatchEvent(new CustomEvent('mux:terminal-action',{detail:{sessionId:activeId,action:'insertText',text}}))}/>}

    {usageOpen&&<UsageDashboard onClose={()=>setUsageOpen(false)} onConfigure={()=>{setUsageOpen(false);openSettings('Usage analytics')}}/>}
    {automationOpen&&<AutomationDashboard onClose={()=>setAutomationOpen(false)} onConfigure={()=>{setAutomationOpen(false);openSettings('Automation')}} onOpenSession={sessionId=>{const session=sessions.find(item=>item.id===sessionId);if(!session){setError('The automation session is no longer live.');return}setAutomationOpen(false);void selectSession(session)}}/>}

    {processViewerOpen && <ProcessPanel initialSessionId={processSession?.id||null} sessions={sessions} projects={projects} onClose={() => {setProcessViewerOpen(false);setProcessSession(null)}} onAttached={(preview, project) => {
      setPreviews(current => ({...current, [preview.id]: preview}))
      setProjects(items => items.map(item => item.id === project.id ? project : item))
      setLayoutMap(current => ({...current, [project.id]: parseLayout(project.layout)}))
    }} />}

    {notificationsOpen&&<Notifications data={notificationData} onClose={()=>setNotificationsOpen(false)} onOpenSession={sessionId=>{const session=sessions.find(item=>item.id===sessionId);if(!session){setError('The notification session is no longer live.');return}setNotificationsOpen(false);void selectSession(session)}} />}

    {notificationToast&&<button class="notification-toast" aria-live="assertive" onClick={()=>{setNotificationToast(null);openNotifications()}}><strong>{notificationToast.session_name||'daemon'}</strong><span>{notificationToast.type.replaceAll('_',' ')}</span><small>open notifications</small></button>}

    {error && <div class="toast" onClick={() => setError('')}>{error}<span>×</span></div>}
  </div>
}
