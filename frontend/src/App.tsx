import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { ComponentChildren, JSX } from 'preact'
import { api, openWebSocket } from './api'
import { HANDSHAKE_TIMEOUT_MS, retryDelay, watchLiveness } from './liveness'
import { TerminalPane } from './TerminalPane'
import { recordPaneVisits, warmPaneIds } from './warmPanes'
import { windowsPtyCompatibility, type TerminalRendererPreference, type WindowsPtyCompatibility } from './terminalRenderer'
import { ProjectResource } from './ProjectResource'
import { SendToAgentPicker, type SendToAgentRequest, type SendToAgentResult, type SendToAgentTarget } from './SendToAgentPicker'
import { pastePayload } from './noteSelection'
import { QueuePane, type QueueScope } from './QueuePane'
import { editQueueMessage, enqueueMessage, fetchQueueSummary, sendQueueMessage, type QueueTargetSummary } from './queueApi'
import { ContinuityBanner } from './ContinuityBanner'
import { DirectoryPicker } from './DirectoryPicker'
import { folderNameFromPath } from './pathNames'
import { agentTargetName } from './agentTargets'
import {
  defaultInitScriptSelection, emptyProjectCreateDraft, projectCreateFolder, projectCreateReady,
  projectCreateRoot, suggestFolderName, toggleInitScript,
  type InitScript, type ProjectCreateDraft,
} from './projectCreate'
import { ProcessPanel, type FleetSnapshot, type Preview, type ProcessItem } from './ProcessPanel'
import { ResourceUsageSummary } from './ResourceUsage'
import { ProjectsManager, type ProjectPatch, type ProjectsManagerTab } from './ProjectsManager'
import { MenuGroup } from './MenuGroup'
import { detectedServers, type DetectedServer } from './sessionProcesses'
import { PreviewPane } from './PreviewPane'
import { Observations } from './Observations'
import type { NotificationData, UiNotification } from './Notifications'
import { UsageDashboard } from './UsageDashboard'
import { HistoryBrowser } from './HistoryBrowser'
import { AccountSwitcher, providerGlyph, type ProviderName } from './ProviderAccounts'
import { PromptLibrary } from './PromptLibrary'
import { PROMPT_RAIL_EVENT } from './promptRail'
import { UtilityDrawer } from './UtilityDrawer'
import {
  DRAWER_TABS, DRAWER_TAB_KEY, DRAWER_WIDTH_KEY, clampDrawerWidth, drawerTab, parseDrawerTab, storedDrawerWidth,
  type DrawerTabId,
} from './drawerTabs'
import {
  isDefaultDrawerTabOrder, normalizeDrawerTabOrder, orderedDrawerTabs as orderedTabsFor,
  sameDrawerTabOrder, DEFAULT_DRAWER_TAB_ORDER,
} from './drawerTabOrder'
import { DRAWER_TAB_ICONS } from './railIcons'
import { CLIPBOARD_CHANGED_EVENT, clearClipboardHistory, configureClipboardCapture } from './clipboardHistory'
import { insertIntoFocusedSurface } from './insertTarget'
import type { SessionNoteSummary } from './NotesTab'
import { ProjectRunMenu } from './ProjectRunMenu'
import { AutomationDashboard } from './AutomationDashboard'
import { VoicePlayer } from './VoicePlayer'
import { ConversationControl } from './ConversationControl'
import { autoplayEnabled, enqueueAutoplay, playClip, setAutoplayEnabled, stopAllPlayback, stopSessionPlayback, unlockPlayback } from './voice'
import { handleSessionSound, type NormalizedMuxEvent } from './sessionSounds'
import { currentProfile, loadDrawerTabOrder, loadSettings, refreshSettings, saveDrawerTabOrder } from './deviceSettings'
import { initPush } from './push'
import { watchDevicePresence } from './devicePresence'
import type { Project, ProjectGroup, Session, ShellProfile, VoiceClip, VoiceMode, VoiceStatus } from './types'
import { keyChord } from './keys'
import { Settings } from './Settings'
import { GuidedTutorial, type TutorialStepId } from './GuidedTutorial'
import { completeTutorial, emitTutorialAction, resetTutorial, shouldStartTutorial } from './tutorial'
import { applyTheme, configureCustomTheme, type CustomTheme, type ThemeName } from './theme'
import { applyNoteEditorConfig } from './noteEditorSettings'
import { applyUiScale, watchUiScaleProfile } from './uiScale'
import { bindingFor, displayChord, runCommand, searchCommands, type Command } from './commands'
import { copyPreparedText } from './terminalClipboard'
import { absoluteProjectPath, FILE_COPY_MAX_LINES, truncateForClipboard } from './fileClipboard'
import { clampContextMenuLeft, fitMenuInViewport } from './menuPosition'
import { defaultMobileInputSettings, mobileInputSettings, type MobileInputSettings } from './mobileInput'
import { adjacentMobileTab, mobileWorkspaceProjection } from './mobileWorkspace'
import { dismissSoftKeyboard, softKeyboardHolder } from './mobileKeyboard'
import { MOBILE_TAB_ORDER_KEY, moveMobileTab, parseMobileTabOrder, pruneMobileTabOrder, serializeMobileTabOrder, type MobileTabOrder } from './mobileTabOrder'
import { classifyGesture, defaultMobileGestureSettings, mobileGestureSettings, resolveGestureCommand, swipeAwayCloseEnabled, type MobileGestureSettings } from './mobileGestures'
import { focusMemoryWith, parseFocusMemory, parseViewPreference, rememberedView, resolveInitialFocus, viewUrl } from './viewState'
import { reorderForHover, reorderTargetFromContainer, type DropSide, type ReorderAxis } from './dragReorder'
import { claimPointerDrag, markPointerDragClaims, pointerDragOwnsPointer } from './pointerDragClaim'
import { horizontalWheelDelta } from './wheelScroll'
import {
  COLLAPSED_PROJECTS_KEY, canHideProject, describeOpenWork, loadCollapsedProjects,
  projectInitials, projectOpenWork, serializeCollapsedProjects, toggleCollapsed,
} from './sidebarProjects'
import {
  PROJECT_SORT_OPTIONS, SECTION_SORT_OPTIONS, SIDEBAR_ORDER_KEY, UNGROUPED_BUCKET_ID,
  bucketSortMode, isBucketCollapsed, loadSidebarOrder, mergeVisibleOrder, placeUngrouped,
  projectActivity, projectSortLabel, pruneSidebarOrder, sectionSortLabel, serializeSidebarOrder,
  setBucketSortMode, sortBuckets, sortProjects, toggleBucketCollapsed,
} from './projectSort'
import { reconcileSeen, isUnread, projectRailStatus, projectSetRailStatus, type ProjectRailActivity, type SeenMap } from './sessionAttention'
import { activityBadges, sessionDotClass, sessionStatus, stateDotClass } from './sessionStatus'
import {
  browserUuid, emptyLayout, leaves, noteResourceId, paneStack, parseLayout, parseNoteResourceId, resourceLeaf,
  reconcilePreviews, reconcileTerminals, removeLeaf, replaceTerminal, setSplitRatio,
  activateContainingStack, activateStackChild, addLeafToStack, addToStack, dissolveStack, groupTerminalsInStack, moveLeafToSplit, moveLeafToStack, openAnchorId, openTab, paneNeighborIds, paneStacks, queueLeafId, queueLeafSessionId, reorderStack, resolveLayout, spawnAnchorId, splitTerminal, splitView, stackForView, stackTerminal, terminalIds, terminalLeaf, visibleTerminalIds, type PaneLayout,
  type PaneDirection, type PaneLeaf, type PaneLeafKind, type PaneNode, type SplitDirection,
} from './layout'

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

/** Let a plain wheel scroll a tab strip that only overflows sideways.
 *
 * Shift+wheel is the browser's only native way in, which is not discoverable and
 * needs a second hand. `preventDefault` is deliberate: without it an overflowing
 * strip consumes the wheel *and* the page keeps whatever scroll chaining it would
 * have done, so the same notch moves two things.
 */
function scrollStripByWheel(event:JSX.TargetedWheelEvent<HTMLDivElement>):void {
  const strip=event.currentTarget
  const delta=horizontalWheelDelta(event,strip)
  if(!delta)return
  event.preventDefault()
  strip.scrollLeft+=delta
}

// One naming rule for every surface that shows a session: sidebar rows, workspace
// tabs, menus, drag labels, the palette. Kept as a single delegation rather than a
// re-implementation because the copies drifted — the workspace tab strip read
// `session.name` directly and so was the one place a generated title never appeared.
const sessionName=(session:Session):string=>agentTargetName(session)

// Compact standing-activity glyphs for dense surfaces (sidebar rows, tab
// strips): the dot's color never changes — green keeps meaning "ready" — so
// an armed loop, cron schedule, background tasks, or live subagents render as
// dimmed glyphs beside it, with the full text in the status line and tooltip.
const activityGlyphs=(session:Session|undefined)=>{
  if(!session||session.pending)return null
  const badges=activityBadges(session)
  if(!badges.length)return null
  return <span class="activity-badges" role="img" aria-label={badges.map(badge=>badge.label).join(', ')}>
    {badges.map((badge,index)=><span key={index} class="activity-badge" title={badge.title}>{badge.glyph}{badge.count&&badge.count>1?<span class="activity-count">{badge.count}</span>:null}</span>)}
  </span>
}

function workingCwd(session:Session):string {
  return session.runtime_cwd||session.spawn_cwd||session.cwd
}


const projectRailActivityLabel:Record<ProjectRailActivity,string>={
  attention:'awaiting attention',working:'working',waiting:'ready for input',running:'sessions running',inactive:'no live sessions',
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
type ReviewPreview={source_run_id:string;source_backend:string;backend:'claude'|'codex';cwd:string;worktree_context:string;prompt:string;relation:'review';preview_token:string}
type ReviewState={entry:HistoryEntry;instructions:string;project:string;preview:ReviewPreview;dirty:boolean;loading:boolean;error:string}
type HandoffState={entry:HistoryEntry;markdown:string;message:string}
type ContextState = { session: Session; x: number; y: number; source: 'sidebar'|'tab'|'pane'|'mobile' } | null
type ProjectContext = { project: Project; x: number; y: number } | null
type SidebarContext = { x:number;y:number } | null
type NoteContext = { resourceId:string;projectId:string;x:number;y:number } | null
type TabContext = { leaf:PaneLeaf;label:string;projectId:string;x:number;y:number;source:'tab'|'mobile' } | null
type RenameTarget = { kind: 'session'; session: Session } | { kind: 'project'; project: Project }
type NoteTarget={projectId:string;terminalSessionId:string|null;kind:'note'|'session-note'|'file';ownerLabel:string}
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
  // True while a session-preserving daemon restart is in flight; the page
  // reloads itself once the successor daemon answers /api/health.
  const [daemonReloading, setDaemonReloading] = useState(false)
  const [redeploying, setRedeploying] = useState(false)
  const [redeployConfirmOpen, setRedeployConfirmOpen] = useState(false)
  // '' browses every Project; a Project id prefilters the archive to it.
  const [historyScope,setHistoryScope]=useState('')
  const [historyOpen,setHistoryOpen]=useState(false)
  const [processScope,setProcessScope]=useState<string|null>(null)
  // Which Project's templates join the global ones. Unlike the other surfaces
  // this is additive rather than restrictive, so the app menu still passes the
  // active Project: opening "unscoped" would remove templates, not filters.
  const [promptScope,setPromptScope]=useState<Project|null>(null)
  const [reviewState,setReviewState]=useState<ReviewState|null>(null)
  const [handoffState,setHandoffState]=useState<HandoffState|null>(null)
  // A note/markdown selection waiting for a target. The message is captured when the dialog
  // opens, so editing the document underneath cannot change what is about to be sent.
  const [sendToAgent,setSendToAgent]=useState<SendToAgentRequest|null>(null)
  // Per-target prompt-queue aggregates (pending counts for pane chips), keyed by
  // target session id and refreshed off `queue_updated` events.
  const [queueSummary,setQueueSummary]=useState<Record<string,QueueTargetSummary>>({})
  const queueSummaryTimer=useRef<number|undefined>(undefined)
  const loadQueueSummary=async()=>{
    try{
      const result=await fetchQueueSummary()
      setQueueSummary(Object.fromEntries(result.targets.map(target=>[target.target_session_id,target])))
    }catch{/* the daemon is briefly away; the next event retries */}
  }
  // Fleet-wide pending count, for the drawer tab's badge: "is anything waiting anywhere",
  // which is the question you have while looking at some other session.
  const queuePendingTotal=useMemo(()=>Object.values(queueSummary).reduce((total,target)=>total+target.pending,0),[queueSummary])
  const refreshQueueSummary=()=>{
    if(queueSummaryTimer.current)return
    queueSummaryTimer.current=window.setTimeout(()=>{queueSummaryTimer.current=undefined;void loadQueueSummary()},300)
  }
  useEffect(()=>{
    void loadQueueSummary()
    const reload=()=>void loadQueueSummary()
    window.addEventListener('mux:events-connected',reload)
    return()=>window.removeEventListener('mux:events-connected',reload)
  },[])
  const [contextMenu, setContextMenu] = useState<ContextState>(null)
  const [projectMenu, setProjectMenu] = useState<ProjectContext>(null)
  const [sidebarMenu,setSidebarMenu]=useState<SidebarContext>(null)
  // Per-agent read/unread marks for sidebar rows; see sessionAttention.ts.
  const [seenActivity,setSeenActivity]=useState<SeenMap>({})
  const [noteMenu,setNoteMenu]=useState<NoteContext>(null)
  const [tabMenu,setTabMenu]=useState<TabContext>(null)
  const [emptyMenu, setEmptyMenu] = useState<{x:number;y:number} | null>(null)
  const [zoomedId, setZoomedId] = useState<string | null>(null)
  const [keybindings, setKeybindings] = useState<Record<string, string>>({ 'ctrl+alt+t': 'session.spawnShell', 'ctrl+alt+p': 'palette.open' })
  const [confirmKillId, setConfirmKillId] = useState<string | null>(null)
  const [confirmProjectDeleteId, setConfirmProjectDeleteId] = useState<string | null>(null)
  const [confirmHideId, setConfirmHideId] = useState<string | null>(null)
  const [collapsedProjects, setCollapsedProjects] = useState<Set<string>>(() => loadCollapsedProjects(localStorage.getItem(COLLAPSED_PROJECTS_KEY)))
  const [mainMenuOpen, setMainMenuOpen] = useState(false)
  // Raw setters; every caller uses the mobile-exclusive wrappers defined below.
  const [sidebarOpen, setSidebarOpenState] = useState(false)
  const [sidebarCollapsed,setSidebarCollapsed]=useState(()=>localStorage.getItem('mux.sidebar.collapsed.v1')==='true')
  const [sidebarWidth,setSidebarWidth]=useState(()=>{
    const stored=Number(localStorage.getItem('mux.sidebar.width.v1'))
    return Number.isFinite(stored)&&stored>=190&&stored<=480?stored:254
  })
  const [renameTarget, setRenameTarget] = useState<RenameTarget | null>(null)
  const renameInput = useRef<HTMLInputElement>(null)
  const [renameValue, setRenameValue] = useState('')
  const [projectCreate,setProjectCreate]=useState<ProjectCreateDraft>(emptyProjectCreateDraft())
  const [projectCreateOpen,setProjectCreateOpen]=useState(false)
  // User-authored setup commands, read fresh when the dialog opens. They live in the
  // daemon config (Settings → General), never in a repository.
  const [initScripts,setInitScripts]=useState<InitScript[]>([])
  const [projectsManagerOpen,setProjectsManagerOpen]=useState(false)
  // Which Project the registry should land on, and whether on its record or its
  // settings. Projects is the only per-Project editor, so every "project settings"
  // entry point is a preselection of it rather than a second surface.
  const [projectsManagerFocus,setProjectsManagerFocus]=useState<{projectId:string;tab:ProjectsManagerTab}|null>(null)
  // null closed; a string scopes the browser to one project, '' shows every project.
  // The Notes drawer tab lists the active Project by default; the app menu's unscoped
  // entry point flips it to every Project. Device-local UI state, not a modal.
  const [notesAllProjects,setNotesAllProjects]=useState(false)
  const [folderPickerOpen,setFolderPickerOpen]=useState(false)
  const [groupEdit,setGroupEdit]=useState<{id?:string;name:string}|null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsSection, setSettingsSection] = useState('General')
  // Which MenuGroup is expanded in the app menu; null collapses every group.
  const [menuGroup,setMenuGroup]=useState<string|null>(null)
  const [tutorialOpen,setTutorialOpen]=useState(()=>shouldStartTutorial())
  const [processSession, setProcessSession] = useState<Session | null>(null)
  const [processViewerOpen,setProcessViewerOpen]=useState(false)
  const [sessionProcesses,setSessionProcesses]=useState<Record<string,ProcessItem[]>>({})
  const [processFleet,setProcessFleet]=useState<FleetSnapshot|null>(null)
  const [previews, setPreviews] = useState<Record<string, Preview>>({})
  const [notificationData, setNotificationData] = useState<NotificationData>({notifications:[],deliveries:[]})
  const [notificationUnread, setNotificationUnread] = useState(0)
  const [notificationToast, setNotificationToast] = useState<UiNotification | null>(null)
  const [usageOpen, setUsageOpen] = useState(false)
  const [automationOpen,setAutomationOpen]=useState(false)
  const [projectGroups,setProjectGroups]=useState<ProjectGroup[]>([])
  const dragSessionTargetRef=useRef<{sessionId?:string;stackId?:string;projectId:string}|null>(null)
  type ProjectDrag={id:string;bucketId:string;previewIds:string[];overId:string|null;side:DropSide|null}
  type BucketDrag={id:string;previewIds:string[]}
  type PaneDropZone='tabs'|'left'|'right'|'top'|'bottom'
  type StackTabDrag={stackId:string;childId:string;kind:PaneLeafKind;targetStackId:string;zone:PaneDropZone;previewIds:string[];overId:string|null;side:DropSide|null}
  const [dragProject,setDragProjectState]=useState<ProjectDrag|null>(null)
  const dragProjectRef=useRef<ProjectDrag|null>(null)
  // Ref-only: the ghost and the drop indicator are the drag's feedback, so nothing
  // about a bucket drag needs to re-render the tree it is reordering.
  const dragBucketRef=useRef<BucketDrag|null>(null)
  // Device-local sidebar ordering: the per-bucket sort mode, and the slot the
  // ungrouped bucket takes among the Groups. Group order itself is server-side.
  const [sidebarOrder,setSidebarOrderState]=useState(()=>loadSidebarOrder(localStorage.getItem(SIDEBAR_ORDER_KEY)))
  const setSidebarOrder=(next:ReturnType<typeof loadSidebarOrder>)=>{
    setSidebarOrderState(next)
    localStorage.setItem(SIDEBAR_ORDER_KEY,serializeSidebarOrder(next))
  }
  const [sortMenu,setSortMenu]=useState<{bucketId:string;bucketName:string;x:number;y:number}|null>(null)
  const [dragStackTab,setDragStackTabState]=useState<StackTabDrag|null>(null)
  const dragStackTabRef=useRef<StackTabDrag|null>(null)
  const suppressDragClickRef=useRef<string|null>(null)
  const pointerDropIndicatorRef=useRef<HTMLElement|null>(null)
  const setDragProject=(next:ProjectDrag|null)=>{dragProjectRef.current=next;setDragProjectState(next)}
  const setDragStackTab=(next:StackTabDrag|null)=>{dragStackTabRef.current=next;setDragStackTabState(next)}
  const previewDragStackTab=(next:StackTabDrag)=>{dragStackTabRef.current=next}
  const [promptLibraryOpen,setPromptLibraryOpen]=useState(false)
  // The inbox is per-Project, so it carries its Project rather than following the
  // active one — it opens from a Project's own context menu.
  const [observationsProject,setObservationsProject]=useState<Project|null>(null)
  // The Queue drawer tab's "you were opened deliberately" signal: a counter (so clicking
  // the same chip twice focuses the composer twice) plus the scope to land on. Switching
  // to the tab by hand leaves the panel wherever it was.
  const [queueOpen,setQueueOpen]=useState<{token:number;scope:QueueScope}>({token:0,scope:'session'})
  // The utility drawer: open state, which tab, and (desktop) the docked column's
  // width. All three are device-local UI preferences, like sidebar width, so the
  // drawer reopens where you left it on this device.
  const [clipboardOpen,setClipboardOpenState]=useState(false)
  const [drawerTabId,setDrawerTabId]=useState<DrawerTabId>(()=>parseDrawerTab(localStorage.getItem(DRAWER_TAB_KEY)))
  // A command-rail prompt button whose template has {{placeholders}} has nothing to
  // inject yet, so it hands the template to the Prompts tab to be filled in.
  const [promptPreselect,setPromptPreselect]=useState<{key:string}|undefined>()
  const [drawerWidth,setDrawerWidth]=useState(()=>storedDrawerWidth(localStorage.getItem(DRAWER_WIDTH_KEY)))
  // The drawer's tab arrangement. Unlike width and last-used tab (localStorage, genuinely
  // per-device) this is server-persisted, so a phone inherits what a desktop arranged.
  const [drawerOrder,setDrawerOrder]=useState<DrawerTabId[]>(()=>normalizeDrawerTabOrder(loadDrawerTabOrder()))
  const [dragDrawerTab,setDragDrawerTab]=useState<DrawerTabId|null>(null)
  const dragDrawerOrderRef=useRef<DrawerTabId[]|null>(null)
  const drawerOrderRef=useRef(drawerOrder)
  drawerOrderRef.current=drawerOrder
  const orderedDrawerTabs=useMemo(()=>orderedTabsFor(drawerOrder),[drawerOrder])
  const [clipboardEnabled,setClipboardEnabled]=useState(true)
  const [xtermScrollback, setXtermScrollback] = useState(10000)
  const [terminalRenderer, setTerminalRenderer] = useState<TerminalRendererPreference>('auto')
  const [windowsPty, setWindowsPty] = useState<WindowsPtyCompatibility | undefined>(undefined)
  const [mobileInput, setMobileInput] = useState<MobileInputSettings>(defaultMobileInputSettings)
  const [mobileGestures, setMobileGestures] = useState<MobileGestureSettings>(defaultMobileGestureSettings)
  const [swipeAwayClose, setSwipeAwayClose] = useState(true)
  const [mobileWorkspace,setMobileWorkspace]=useState(()=>window.matchMedia('(max-width:760px)').matches)
  // On a phone the navigation sidebar and the clipboard panel are both full-height
  // drawers over the workspace, entering from opposite edges. Two open at once leave
  // no workspace between them and bury one under the other's scrim, so opening either
  // closes the other. On desktop the sidebar is an in-flow column that the right-edge
  // panel never covers, so both stay open there.
  // Opening either also lowers the soft keyboard, for the same reason: it is
  // held up by a field now behind the scrim, and it covers up to half of the
  // panel that just opened (see mobileKeyboard.ts). Both rules live in the
  // setters rather than at each call site so every entry point — gesture,
  // command, nav toggle, tutorial — inherits them.
  type OpenState=boolean|((value:boolean)=>boolean)
  const setSidebarOpen=(next:OpenState)=>{
    const open=typeof next==='function'?next(sidebarOpen):next
    setSidebarOpenState(open)
    if(open&&mobileWorkspace){setClipboardOpenState(false);dismissSoftKeyboard()}
  }
  const setClipboardOpen=(next:OpenState)=>{
    const open=typeof next==='function'?next(clipboardOpen):next
    setClipboardOpenState(open)
    if(open&&mobileWorkspace){setSidebarOpenState(false);dismissSoftKeyboard()}
  }
  /** Open the drawer on a specific tab (or toggle that tab shut if it is already showing). */
  const showDrawerTab=(tab:DrawerTabId)=>{
    localStorage.setItem(DRAWER_TAB_KEY,tab)
    setDrawerTabId(tab)
    setClipboardOpen(!(clipboardOpen&&drawerTabId===tab))
    // Reaching Notes from the rail, the tab strip, or `drawer.notes` says nothing about scope,
    // so it means "this Project" — the drawer sits beside that Project's workspace. Only the
    // app menu's deliberately unscoped `notes.browse` widens it, and it goes through
    // `openNotesBrowser`, which sets the scope after this and is not on this path.
    if(tab==='notes')setNotesAllProjects(false)
    setMainMenuOpen(false);setProjectMenu(null);setContextMenu(null)
  }
  /** Same, but never toggling shut. A menu row or chip that names a surface ("Browse
   *  files…", "Session notes…") has already said "show me this"; closing the drawer on
   *  it is perverse, and worse when the click also switched Project — the panel would
   *  vanish instead of retargeting. */
  const openDrawerTab=(tab:DrawerTabId)=>{
    localStorage.setItem(DRAWER_TAB_KEY,tab)
    setDrawerTabId(tab)
    setClipboardOpen(true)
    setMainMenuOpen(false);setProjectMenu(null);setContextMenu(null)
  }
  const persistDrawerWidth=(value:number)=>{
    const next=clampDrawerWidth(value)
    setDrawerWidth(next);localStorage.setItem(DRAWER_WIDTH_KEY,String(Math.round(next)))
  }
  // Optimistic like every other settings write: `saveDrawerTabOrder` updates the shared cache
  // before its PUT, so the adopt listener below reads back what we just set rather than
  // fighting it. A failed PUT surfaces rather than silently reverting the strip under a cursor.
  const commitDrawerTabOrder=async(nextIds:string[])=>{
    const normalized=normalizeDrawerTabOrder(nextIds)
    if(sameDrawerTabOrder(normalized,drawerOrderRef.current))return
    setDrawerOrder(normalized)
    try{await saveDrawerTabOrder(normalized)}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }
  const resetDrawerTabOrder=()=>{setMainMenuOpen(false);void commitDrawerTabOrder(DEFAULT_DRAWER_TAB_ORDER)}
  // Adopt the persisted order when the settings cache first loads and whenever another device
  // edits it. Both arrive as the same event, and both are just "the stored order changed".
  useEffect(()=>{
    const adopt=()=>{
      const next=normalizeDrawerTabOrder(loadDrawerTabOrder())
      setDrawerOrder(current=>sameDrawerTabOrder(next,current)?current:next)
    }
    window.addEventListener('mux:settings-changed',adopt)
    return()=>window.removeEventListener('mux:settings-changed',adopt)
  },[])
  /** Drag a drawer tab to rearrange it, from either the strip (horizontal) or the rail
   *  (vertical). One handler for both: they render one order, so they reorder one list.
   *  Follows the app's pointer-drag contract — no native DnD, refs and one DOM attribute
   *  during the move, commit on pointer-up. */
  const beginDrawerTabDrag=(event:JSX.TargetedPointerEvent<HTMLElement>,id:DrawerTabId)=>{
    const container=event.currentTarget.closest<HTMLElement>('.drawer-tabs,.utility-rail')
    const axis:ReorderAxis=container?.classList.contains('utility-rail')?'vertical':'horizontal'
    beginPointerDrag(event,drawerTab(id).label,`drawer-tab:${id}`,
      ()=>{cancelLongPress();dragDrawerOrderRef.current=drawerOrderRef.current;setDragDrawerTab(id)},
      pointer=>{
        const current=dragDrawerOrderRef.current
        if(!current||!container){showPointerDropIndicator(null);return}
        const target=reorderTargetFromContainer(container,id,axis,axis==='horizontal'?pointer.clientX:pointer.clientY)
        if(!target){showPointerDropIndicator(null);return}
        dragDrawerOrderRef.current=reorderForHover(current,id,target.id,target.side) as DrawerTabId[]
        const element=Array.from(container.querySelectorAll<HTMLElement>(':scope > [data-reorder-id]')).find(item=>item.dataset.reorderId===target.id)||null
        showPointerDropIndicator(element,`insert-${target.side}`)
      },
      ()=>{const next=dragDrawerOrderRef.current;dragDrawerOrderRef.current=null;setDragDrawerTab(null);if(next)void commitDrawerTabOrder(next)},
      ()=>{dragDrawerOrderRef.current=null;setDragDrawerTab(null)},
    )
  }
  // Mirrors the sidebar resizer, mirrored: dragging left widens the dock. Every
  // width change reflows the pane tree and refits its terminals, which is why the
  // width is persisted and the drawer is never opened automatically.
  const beginDrawerResize=(event:PointerEvent)=>{
    event.preventDefault()
    const startX=event.clientX,startWidth=drawerWidth
    document.body.classList.add('sidebar-resizing')
    const move=(pointer:PointerEvent)=>setDrawerWidth(clampDrawerWidth(startWidth-(pointer.clientX-startX)))
    // pointercancel too: on touch, a cancelled drag fires only that, and without
    // it the pointermove listener and the `sidebar-resizing` body class both
    // survive until some unrelated pointerup happens elsewhere.
    const stop=(pointer:PointerEvent)=>{
      persistDrawerWidth(startWidth-(pointer.clientX-startX))
      document.body.classList.remove('sidebar-resizing')
      window.removeEventListener('pointermove',move)
      window.removeEventListener('pointerup',stop)
      window.removeEventListener('pointercancel',stop)
    }
    window.addEventListener('pointermove',move)
    window.addEventListener('pointerup',stop,{once:true})
    window.addEventListener('pointercancel',stop,{once:true})
  }
  // Transient touch feedback (which tab a swipe landed on, what a held Run
  // started). Purely visual, so it never enters layout or Project state.
  const [mobileHud,setMobileHud]=useState('')
  const mobileHudTimer=useRef<number|null>(null)
  // Device-local rail permutation, per project. Never sent to the daemon: it
  // reorders the mobile projection only, so it cannot alter the desktop layout.
  const [mobileTabOrder,setMobileTabOrder]=useState<MobileTabOrder>(()=>parseMobileTabOrder(localStorage.getItem(MOBILE_TAB_ORDER_KEY)))
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
    // Held from the moment the drag becomes real until it unwinds, so the mobile gesture
    // recognizer stops reading this finger: a tab dragged along a strip is the same motion
    // as a swipe, and only the drag knows which it is. Claimed at activation rather than at
    // pointer-down so a swipe that merely *starts* on a draggable tab still works.
    let releaseDragClaim:(()=>void)|null=null
    const cleanup=()=>{
      releaseDragClaim?.();releaseDragClaim=null
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
        active=true;releaseDragClaim=claimPointerDrag();suppressDragClickRef.current=identity;document.body.classList.add('workspace-pointer-dragging')
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
  const relaunching = useRef(false)
  const longPressTimer = useRef<number | null>(null)
  const runHeldRef = useRef(false)
  // When the Run menu's scrim dismissed it, so the trigger's own click can tell
  // "reopen" from "the closing half of a toggle tap".
  const runMenuClosedAt = useRef(0)
  // Set when an outside pointer-down closed a context menu, so the menu's focus
  // teardown knows not to reclaim focus from whatever that pointer landed on.
  const menuDismissedByPointer = useRef(false)
  const notificationIds = useRef<Set<string>>(new Set())
  const paletteInput = useRef<HTMLInputElement>(null)
  const refreshInFlight = useRef<Promise<void> | null>(null)
  const refreshQueued = useRef(false)
  const sessionsRef=useRef<Session[]>([])
  const projectsRef=useRef<Project[]>([])
  const layoutRevisions = useRef<Record<string,number>>({})
  const layoutWriteChains = useRef<Record<string,Promise<boolean>>>({})
  const layoutWriteGeneration = useRef<Record<string,number>>({})
  // Highest event sequence this tab has seen; sent as ?after_seq on reconnect so
  // the daemon replays what was actually missed instead of the oldest retained page.
  const lastEventSeq = useRef(0)
  const requestedView = useRef(parseViewPreference(location.search))
  const focusMemory = useRef(parseFocusMemory(localStorage.getItem('mux.focus.v1')))
  const [focusHydrated,setFocusHydrated]=useState(false)
  sessionsRef.current=sessions
  projectsRef.current=projects
  // Clipboard capture runs from module-level hooks installed at boot, so it reads
  // the focused session / device / on-off state through refs rather than props.
  const clipboardContextRef=useRef({activeId:null as string|null,projectId:'',enabled:true})
  clipboardContextRef.current={activeId,projectId,enabled:clipboardEnabled}

  const cancelLongPress = () => {
    if (longPressTimer.current !== null) window.clearTimeout(longPressTimer.current)
    longPressTimer.current = null
  }

  const showMobileHud = (text: string) => {
    setMobileHud(text)
    if (mobileHudTimer.current !== null) window.clearTimeout(mobileHudTimer.current)
    mobileHudTimer.current = window.setTimeout(() => { setMobileHud(''); mobileHudTimer.current = null }, 1100)
  }
  useEffect(() => () => { if (mobileHudTimer.current !== null) window.clearTimeout(mobileHudTimer.current) }, [])

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
      window.removeEventListener('pointermove',move)
      window.removeEventListener('pointerup',stop)
      window.removeEventListener('pointercancel',stop)
    }
    window.addEventListener('pointermove',move)
    window.addEventListener('pointerup',stop,{once:true})
    window.addEventListener('pointercancel',stop,{once:true})
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
      // A project with a layout PATCH in flight is deliberately skipped below, so
      // its server revision must not advance either: adopting it here is what let
      // a second drag base itself on the clobbered layout and then win the write,
      // silently reverting the first move for every client.
      for(const project of nextProjects){
        if(layoutWriteChains.current[project.id]!==undefined)continue
        layoutRevisions.current[project.id]=project.layout_revision
      }
      setPreviews(Object.fromEntries(nextPreviews.items.map(item => [item.id, item])))
      setProjectGroups(nextGroups)
      setLayoutMap(current => {
        const next = { ...current }
        const live = new Set(nextSessions.filter(session => !['exited', 'crashed'].includes(session.state)).map(session => session.id))
        const livePreviews = new Set(nextPreviews.items.map(item => item.id))
        for (const project of nextProjects) {
          // This GET may have been snapshotted before an in-flight layout PATCH
          // committed. Overwriting optimistic state with it snaps a just-dropped
          // tab back; the PATCH's own generation-guarded path reconciles instead.
          if(layoutWriteChains.current[project.id]!==undefined)continue
          // History graduated from a per-project pane tab to a global overlay;
          // drop any persisted history leaf so old layouts don't dangle.
          let base=parseLayout(project.layout)
          for(const leaf of leaves(base,'history'))base=removeLeaf(base,'history',leaf.id)
          next[project.id] = reconcilePreviews(reconcileTerminals(base, live), livePreviews)
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

  type AppConfig = {
    theme:ThemeName
    custom_theme:CustomTheme
    xterm_scrollback_lines:number
    terminal_renderer:TerminalRendererPreference
  }&Record<string,unknown>

  // One place that turns a daemon config into browser state. The boot path, the
  // Settings-close path, and the configuration_changed handler each applied a
  // *different subset*, so a renderer or scroll-sensitivity change made on
  // another device silently never reached this tab. `includeTheme` is the only
  // difference: theme is applied once at boot and by Settings itself.
  const applyConfig = (config:AppConfig, includeTheme:boolean) => {
    if (includeTheme) { configureCustomTheme(config.custom_theme); applyTheme(config.theme) }
    applyNoteEditorConfig(config)
    applyUiScale(config)
    setXtermScrollback(config.xterm_scrollback_lines)
    setTerminalRenderer(config.terminal_renderer)
    // Value-compared for the same reason as mobileInput below: this feeds
    // TerminalPane's mount effect, so a fresh object identity on an unchanged
    // machine descriptor would dispose and rebuild every live terminal.
    const nextWindowsPty = windowsPtyCompatibility(config.pty_windows)
    setWindowsPty(current =>
      JSON.stringify(current) === JSON.stringify(nextWindowsPty) ? current : nextWindowsPty)
    // Value-compared, not replaced: a fresh object identity defeats TerminalPane's
    // memo and remounts every terminal (socket torn down, xterm disposed, buffer
    // replayed) on an unchanged setting.
    const nextMobileInput = mobileInputSettings(config)
    setMobileInput(current =>
      JSON.stringify(current) === JSON.stringify(nextMobileInput) ? current : nextMobileInput)
    setMobileGestures(mobileGestureSettings(config))
    setSwipeAwayClose(swipeAwayCloseEnabled(config))
    setClipboardEnabled(config.clipboard_history_enabled!==false)
  }

  const loadConfig = (includeTheme:boolean) =>
    api<AppConfig>('GET','/api/config')
      .then(config=>applyConfig(config,includeTheme))
      .catch(()=>{})

  // Read aloud turned off in Settings (here or on another device — `configuration_changed`
  // refetches this status everywhere) silences whatever is mid-clip rather than letting it
  // run out.
  useEffect(() => { if (voiceStatus && !voiceStatus.enabled) stopAllPlayback() }, [voiceStatus?.enabled])

  useEffect(() => {
    void refresh()
    void loadConfig(true)
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
    // Backstop for every `void api(...)` call site. Kill, create, and delete are
    // all fire-and-forget: a rejected DELETE left the session in place with no
    // toast and no console-surfaced message, indistinguishable from a dead
    // button. Catching the rejection centrally means one missed try/catch cannot
    // silently swallow a failure again.
    const onUnhandled = (event: PromiseRejectionEvent) => {
      const reason = event.reason
      setError(reason instanceof Error ? reason.message : String(reason))
    }
    window.addEventListener('unhandledrejection', onUnhandled)
    return () => {
      clearInterval(timer); clearInterval(keyTimer)
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('unhandledrejection', onUnhandled)
    }
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

  // Chrome scale is stored per device class, so crossing the breakpoint changes
  // which stored value applies — not just the layout.
  useEffect(() => watchUiScaleProfile(), [])

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

  useEffect(() => { void loadSettings(); void initPush() }, [])

  // Point the boot-installed clipboard capture at live app state. Runs once: the
  // getters read refs, so they never go stale and never re-install the hooks.
  useEffect(() => {
    configureClipboardCapture({
      device: () => currentProfile(),
      sessionId: () => clipboardContextRef.current.activeId,
      projectId: () => clipboardContextRef.current.projectId || null,
      enabled: () => clipboardContextRef.current.enabled,
    })
  }, [])

  useEffect(() => {
    let socket: WebSocket | null = null
    let retry: number | undefined
    let refreshTimer: number | undefined
    // Attempt bookkeeping for the liveness watcher (see liveness.ts): a handshake started
    // while a dormant PWA wakes can hang without ever failing, and the backoff timer that
    // should retry it may have been frozen along with the page.
    let attemptStartedAt: number | null = null
    let nextAttemptAt: number | null = null
    let handshakeTimer: number | undefined
    let attempt = 0
    // Presence rides this socket: the daemon uses it to decide whether a lock-screen
    // push is worth sending, and a dead socket is a device nobody is looking at.
    const presence = watchDevicePresence(frame => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(frame))
    })
    const queueRefresh = () => {
      if (refreshTimer !== undefined) return
      refreshTimer = window.setTimeout(() => {
        refreshTimer = undefined
        void refresh()
      }, 100)
    }
    const clearHandshakeWatchdog = () => {
      if (handshakeTimer === undefined) return
      window.clearTimeout(handshakeTimer)
      handshakeTimer = undefined
    }
    const scheduleRetry = () => {
      if (retry !== undefined) return
      const delay = retryDelay(attempt)
      attempt += 1
      nextAttemptAt = Date.now() + delay
      retry = window.setTimeout(() => { retry = undefined; nextAttemptAt = null; connect() }, delay)
    }
    const connect = () => {
      if(retry){clearTimeout(retry);retry=undefined}
      nextAttemptAt = null
      clearHandshakeWatchdog()
      attemptStartedAt = Date.now()
      let next: WebSocket
      // Constructing a socket can throw outright (no route, blocked scheme). That must feed
      // the retry path rather than escape into the caller's timer.
      // Reconnects resume from the last event actually seen. Without the cursor the daemon
      // cannot know what this client missed, so catch-up delivers history it already has
      // and none of the gap.
      const resume = lastEventSeq.current > 0 ? `?after_seq=${lastEventSeq.current}` : ''
      try { next = openWebSocket(`/events${resume}`) } catch { socket = null; scheduleRetry(); return }
      socket = next
      handshakeTimer = window.setTimeout(() => {
        handshakeTimer = undefined
        if (socket !== next || next.readyState === WebSocket.OPEN) return
        next.onclose = null; next.onerror = null; next.onmessage = null
        try { next.close() } catch { /* already tearing down */ }
        socket = null
        scheduleRetry()
      }, HANDSHAKE_TIMEOUT_MS)
      next.onopen = () => {
        if (socket !== next) return
        clearHandshakeWatchdog()
        attempt = 0
        // A new socket is a new connection id on the daemon, with no presence of its
        // own. Until it has one this device looks absent, which is the safe direction
        // (a redundant push, never a missing one) but only briefly.
        presence.report()
        window.dispatchEvent(new CustomEvent('mux:events-connected'))
      }
      next.onerror = () => { if (socket === next) next.close() }
      next.onmessage = message => {
        if (socket !== next) return
        queueRefresh()
        try {
          const event = JSON.parse(String(message.data))
          // The daemon says the gap was wider than the replay window: nothing in the
          // stream can reconstruct it, so fall back to a full REST refresh.
          if (event.type === 'events_gap') { queueRefresh(); return }
          if (typeof event.seq === 'number' && event.seq > lastEventSeq.current) lastEventSeq.current = event.seq
          // Catch-up events (marked replay by the daemon) are a historical resync sent on
          // every (re)connect. They must drive state refresh but never re-fire live-only
          // effects like notification sounds or voice autoplay, or reopening the app would
          // replay old audio.
          const isReplay = event.replay === true
          const soundEvent=event as NormalizedMuxEvent
          const eventSession=sessionsRef.current.find(item=>item.id===soundEvent.session_id)
          const eventProject=projectsRef.current.find(item=>item.id===(eventSession?.project_id||String(soundEvent.payload?.project_id||'')))
          if (!isReplay) handleSessionSound(soundEvent,eventProject?.effective_options?.notification_sounds_enabled!==false)
          if (['notification','notification_created'].includes(event.type)) void loadNotifications(true)
          if (event.type === 'voice_clip_ready' || event.type === 'voice_clip_failed') {
            const clipId = String(event.payload?.clip_id || '')
            window.dispatchEvent(new CustomEvent('mux:voice-clip', { detail: {
              sessionId: event.session_id, clipId,
              status: event.type === 'voice_clip_ready' ? 'ready' : 'failed',
              trigger: event.payload?.trigger,
              streamId: event.payload?.stream_id,
            } }))
            // The pane's mode is re-checked here as well as on the daemon: a clip
            // generated just before the user hit "off" would otherwise land and start
            // speaking after the switch was thrown.
            const autoAllowed = eventSession ? eventSession.voice_mode !== 'off' : true
            if (!isReplay && event.type === 'voice_clip_ready' && event.payload?.trigger === 'auto' && clipId && autoAllowed && autoplayEnabled()) enqueueAutoplay(clipId,String(event.payload?.stream_id||'')||null,event.session_id||null)
          }
          if (event.type === 'settings_changed') refreshSettings()
          // Another device (or another tab) changed the ring; an open picker refetches.
          if (event.type === 'clipboard_changed') window.dispatchEvent(new CustomEvent(CLIPBOARD_CHANGED_EVENT))
          if (event.type === 'configuration_changed') {
            void api<VoiceStatus>('GET','/api/voice').then(setVoiceStatus).catch(()=>{})
            // A change made from another device (or by editing the config file)
            // has to reach this tab's copy of *every* config-derived setting, not
            // the subset this handler happened to list.
            void loadConfig(false)
          }
          if(event.type==='project_files_changed')window.dispatchEvent(new CustomEvent('mux:project-files-changed',{detail:{projectId:event.payload?.project_id,paths:event.payload?.paths||[]}}))
          if(event.type==='agent_context_changed')window.dispatchEvent(new CustomEvent('mux:agent-context-changed',{detail:{projectId:event.payload?.project_id}}))
          // Queue tabs and pane chips live-update off these; payloads carry ids/counts only.
          if(event.type==='queue_updated'||event.type==='queue_delivery'){window.dispatchEvent(new CustomEvent('mux:queue-changed',{detail:{sessionId:event.session_id}}));refreshQueueSummary()}
          // The drawer's Git tab refetches its worktree list off this. Branch/dirty/upstream
          // already ride the session snapshots, so `git_changed` needs no payload here.
          if(event.type==='worktree_created'||event.type==='worktree_removed'||event.type==='git_changed')window.dispatchEvent(new CustomEvent('mux:git-changed'))
          if(event.type==='project_note_changed'||event.type==='session_note_changed')window.dispatchEvent(new CustomEvent('mux:note-changed',{detail:{projectId:String(event.payload?.project_id||''),kind:event.type==='session_note_changed'?'session-note':'note',noteId:event.type==='session_note_changed'?String(event.payload?.note_id||''):null,revision:String(event.payload?.revision||'')}}))
        } catch { /* malformed events are ignored */ }
      }
      next.onclose = () => { if (socket !== next) return; socket = null; scheduleRetry() }
    }
    const reconnect = () => {
      clearHandshakeWatchdog()
      if(socket){socket.onclose=null;socket.onerror=null;socket.onmessage=null;socket.close();socket=null}
      attempt = 0
      connect()
      // The socket only carries changes, so a stream that was dead for a while leaves the
      // REST-backed state stale too; refresh alongside the fresh attach.
      queueRefresh()
    }
    connect()
    const stopLivenessWatch = watchLiveness({
      phase: () => socket ? (socket.readyState === WebSocket.OPEN ? 'open' : socket.readyState === WebSocket.CONNECTING ? 'connecting' : 'closed') : 'closed',
      attemptStartedAt: () => attemptStartedAt,
      nextAttemptAt: () => nextAttemptAt,
      reconnect,
    })
    return () => { stopLivenessWatch(); presence.stop(); clearHandshakeWatchdog(); if (retry) clearTimeout(retry); if(refreshTimer)clearTimeout(refreshTimer);if(socket){socket.onclose=null;socket.close()} }
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
  const orderedGroups=[...projectGroups].sort((a,b)=>a.position-b.position||a.name.localeCompare(b.name)||a.id.localeCompare(b.id))
  // Every bucket the sidebar could show, in display order, empty ones included: a
  // drag only ever permutes the rendered subset, and folding that back into the full
  // list is what keeps an empty Group where its owner left it.
  const bucketIds=placeUngrouped(orderedGroups.map(group=>group.id),sidebarOrder.ungroupedIndex)
  const activityStamps=projectActivity(projects,sessions)
  const allBuckets=bucketIds.map(bucketId=>{
    const group=orderedGroups.find(item=>item.id===bucketId)
    const items=bucketId===UNGROUPED_BUCKET_ID
      ? visibleProjects.filter(project=>!project.group_id||!projectGroups.some(item=>item.id===project.group_id))
      : visibleProjects.filter(project=>project.group_id===bucketId)
    // `visibleProjects` is already in manual order, which sortProjects treats as the
    // tie-break, so every mode falls back to what the user arranged by hand.
    return {id:bucketId,name:group?group.name:'Projects',items:sortProjects(items,bucketSortMode(sidebarOrder,bucketId),activityStamps)}
  })
  // Sections sort by the same contract their contents do: manual order in, stable
  // sort out, so the arrangement underneath a sort is never lost.
  const displayBuckets=sortBuckets(allBuckets,sidebarOrder.sectionSort,activityStamps)
  const displayBucketIds=displayBuckets.map(bucket=>bucket.id)
  const projectBuckets=displayBuckets.filter(bucket=>bucket.items.length>0)
  // Sidebar reading order across every bucket. The collapsed rail, the numbered
  // Project commands, and the drag baseline all follow what is on screen rather
  // than the stored positions, or a sorted sidebar would disagree with itself.
  // A folded section still contributes its Projects: collapsing hides rows, it
  // does not remove the Projects from the rail or the numbered shortcuts.
  const displayProjects=projectBuckets.flatMap(bucket=>bucket.items)
  const displayProjectIds=mergeVisibleOrder(orderedProjects.map(project=>project.id),displayProjects.map(project=>project.id))
  // A deleted Group would otherwise leave its sort mode behind forever, and the
  // stored blob is what a recreated bucket id would silently inherit.
  useEffect(()=>{
    const pruned=pruneSidebarOrder(sidebarOrder,bucketIds)
    if(pruned!==sidebarOrder)setSidebarOrder(pruned)
  },[projectGroups])
  const activeLayout = layoutMap[projectId] || emptyLayout()
  const paneIds = terminalIds(activeLayout).filter(id => sessions.some(session => session.id === id && !['exited', 'crashed'].includes(session.state)))
  const workspacePanes=paneStacks(activeLayout)
  const paneViewIds=workspacePanes.map(pane=>pane.active_child_id)
  const focusedTabId=leaves(activeLayout).find(leaf=>leaf.id===(focusedViewId||activeId))?.id||null
  const activeStack=focusedTabId?stackForView(activeLayout,focusedTabId):null
  const unpanned = sessions.filter(session => session.project_id === projectId && !['exited', 'crashed'].includes(session.state) && !paneIds.includes(session.id))
  const focusedOutsideLayout=!!active&&!['exited','crashed'].includes(active.state)&&active.project_id===projectId&&!paneIds.includes(active.id)

  // Sessions on screen right now (visible pane of the displayed project) count as
  // "read": their sidebar rows stay muted even while their agent keeps working.
  const visibleSessionIds=visibleTerminalIds(activeLayout)
  const visibleSessionKey=visibleSessionIds.join('\n')
  useEffect(()=>{
    setSeenActivity(prev=>reconcileSeen(prev,sessions,visibleSessionIds))
  },[sessions,visibleSessionKey])

  // Terminals stay mounted for a few switches after you leave them, so coming back
  // costs no replay (`warmPanes.ts`). Recency is recorded from the layout rather than
  // from focus: what matters is which pane a stack was last *showing*, which is also
  // what survives a project switch and a workspace restore.
  const [warmHistory,setWarmHistory]=useState<string[]>([])
  useEffect(()=>{
    setWarmHistory(history=>recordPaneVisits(history,visibleSessionIds))
  },[visibleSessionKey])
  const layoutTerminalIds=terminalIds(activeLayout)
  const layoutTerminalKey=layoutTerminalIds.join(' ')
  // Budgeted across the whole workspace, not per stack: three hidden terminals is
  // three sockets and three scrollbacks however they are arranged on screen.
  const warmTerminalIds=useMemo(
    ()=>warmPaneIds(warmHistory,visibleSessionIds,layoutTerminalIds),
    [warmHistory,visibleSessionKey,layoutTerminalKey],
  )

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
    // Restore the last focused view (which may be a note or file) when it still
    // exists in the project's layout; otherwise fall back to the resolved session.
    const layout=layoutMap[selected.projectId]||parseLayout(projects.find(project=>project.id===selected.projectId)?.layout)
    const remembered=rememberedView(focusMemory.current,selected.projectId)
    setFocusedViewId(remembered&&leaves(layout).some(leaf=>leaf.id===remembered)?remembered:selected.sessionId)
    setFocusHydrated(true)
  },[focusHydrated,sessions,projects,layoutMap])

  useEffect(() => {
    if(!focusHydrated)return
    const session=sessions.find(item=>item.id===activeId&&item.project_id===projectId&&!isEndedSession(item))
    // Persist the focused view (note/file/terminal) alongside the active session so a
    // later return to this project reopens exactly what was last looked at here.
    const focusView=leaves(activeLayout).some(leaf=>leaf.id===focusedViewId)?focusedViewId:null
    focusMemory.current=focusMemoryWith(focusMemory.current,projectId,session?.id||null,focusView)
    localStorage.setItem('mux.focus.v1',JSON.stringify(focusMemory.current))
    const next=viewUrl(location.href,projectId,session?.id||null)
    if(`${location.pathname}${location.search}${location.hash}`!==next)window.history.replaceState(window.history.state,'',next)
  },[focusHydrated,projectId,activeId,focusedViewId,sessions,layoutMap])

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
    // Follow the active terminal with focus only when the current focus is stale (its
    // view is gone from this layout). A deliberately focused note/file that still exists
    // stays focused — otherwise switching projects would yank focus onto a live session.
    if(nextId&&!leaves(activeLayout).some(leaf=>leaf.id===focusedViewId))setFocusedViewId(nextId)
    if (nextId && terminalIds(activeLayout).includes(nextId)) {
      setLayoutMap(current => ({
        ...current,
        [projectId]: activateContainingStack(current[projectId] ?? activeLayout, nextId),
      }))
    }
  }, [focusHydrated,sessions, projectId, activeId, focusedViewId, zoomedId, layoutMap])
  // Settings is global-only. Anything scoped to one Project lives in the Projects
  // registry (`openProjectsManager`), which is the single per-Project editor.
  const openSettings = (section='General') => { setSettingsSection(section); setSettingsOpen(true); setMainMenuOpen(false); setProjectMenu(null) }
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
  // The Notes tab lists notes from disk, so an owner may be long gone. Route through
  // the same initialize-then-open path: initialization is idempotent and keeps a
  // note whose file was removed underneath the listing from opening onto nothing.
  const openBrowsedSessionNote=(targetProject:string,noteId:string)=>{
    void initializeAndOpenSessionNote({
      projectId:targetProject,
      terminalSessionId:sessions.some(item=>item.id===noteId&&!isEndedSession(item))?noteId:null,
      kind:'session-note',
      ownerLabel:noteId,
    })
  }
  // Files is a drawer tab, not a pane tab: it is a navigator that opens documents into
  // the workspace, so it costs a panel rather than a permanent tab. Its view follows the
  // active Project, which is why every entry point selects that Project first.
  const openProjectFiles=(project:Project)=>{setProjectId(project.id);openDrawerTab('files')}
  const openNotesBrowser=(scope:Project|null)=>{
    if(scope)setProjectId(scope.id)
    setNotesAllProjects(!scope)
    openDrawerTab('notes')
  }
  const openProjectFile=(project:Project,path:string,targetViewId?:string)=>void showResourceForTarget({projectId:project.id,terminalSessionId:null,kind:'file',ownerLabel:path},targetViewId)
  // Notifications are a drawer tab, not a modal: checking what fired should not be
  // a full-screen interruption. Opening the tab is what marks them read.
  const openNotifications = () => { showDrawerTab('notifications');setNotificationUnread(0);void loadNotifications() }

  // The sort button stops its own pointer-down so the header drag never starts under
  // it, which also keeps that event from reaching the document's dismiss handler —
  // so opening this menu has to close whatever else was open itself.
  const openSortMenu=(bucketId:string,bucketName:string,x:number,y:number)=>{
    setContextMenu(null);setProjectMenu(null);setSidebarMenu(null);setNoteMenu(null);setTabMenu(null);setEmptyMenu(null);setMainMenuOpen(false)
    setSortMenu({bucketId,bucketName,x,y})
  }
  const bucketIdFor=(project:Project)=>
    project.group_id&&projectGroups.some(group=>group.id===project.group_id)?project.group_id:UNGROUPED_BUCKET_ID
  /** Write a manual Project order. Placing a Project by hand is the statement that
   *  this bucket is hand-arranged, so it drops the bucket's sort back to Manual and
   *  freezes whatever was on screen into positions — otherwise the next render would
   *  re-sort the move away and the drag would look broken. */
  const commitProjectOrder=async(nextIds:string[],bucketId:string)=>{
    if(nextIds.join('\0')===displayProjectIds.join('\0'))return
    setSidebarOrder(setBucketSortMode(sidebarOrder,bucketId,'custom'))
    // The daemon validates against its own position order, not the sorted view.
    const expected=orderedProjects.map(project=>project.id)
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
    const bucketId=bucketIdFor(project)
    const peers=displayProjects.filter(item=>bucketIdFor(item)===bucketId)
    const index=peers.findIndex(item=>item.id===project.id)
    const other=peers[index+direction]
    if(!other)return
    const ids=[...displayProjectIds]
    const from=ids.indexOf(project.id),to=ids.indexOf(other.id)
    ;[ids[from],ids[to]]=[ids[to],ids[from]]
    setProjectMenu(null)
    void commitProjectOrder(ids,bucketId)
  }
  const beginProjectPointerDrag=(event:JSX.TargetedPointerEvent<HTMLElement>,project:Project,bucketId:string,peerIds:string[])=>{
    const bucket=event.currentTarget.closest<HTMLElement>('.sidebar-project-bucket')
    const initial:ProjectDrag={id:project.id,bucketId,previewIds:displayProjectIds,overId:null,side:null}
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
      ()=>{const current=dragProjectRef.current;setDragProject(null);if(current)void commitProjectOrder(current.previewIds,current.bucketId)},
      ()=>setDragProject(null),
    )
  }
  /** Reorder the sidebar's sections. Group order is shared (it lives on the Group
   *  record); the ungrouped remainder has no record, so its slot is remembered per
   *  device instead of being pinned to the end for everyone. */
  const commitBucketOrder=async(nextIds:string[])=>{
    if(nextIds.join('\0')===displayBucketIds.join('\0'))return
    const ungroupedIndex=nextIds.indexOf(UNGROUPED_BUCKET_ID)
    const nextGroupIds=nextIds.filter(id=>id!==UNGROUPED_BUCKET_ID)
    // Placing a section by hand is the statement that the sections are hand-arranged,
    // so it drops the section sort back to Manual and freezes what was on screen —
    // the same rule a Project drag follows one level down.
    setSidebarOrder({...sidebarOrder,sectionSort:'custom',ungroupedIndex:ungroupedIndex<0?null:ungroupedIndex})
    const expected=orderedGroups.map(group=>group.id)
    if(nextGroupIds.join('\0')===expected.join('\0'))return
    const positions=new Map(nextGroupIds.map((id,index)=>[id,index]))
    setProjectGroups(items=>items.map(item=>({...item,position:positions.get(item.id)??item.position})))
    try{
      setProjectGroups(await api<ProjectGroup[]>('PUT','/api/project-groups/order',{group_ids:nextGroupIds,expected_order:expected}))
    }catch(cause){
      await refresh()
      setError(cause instanceof Error?cause.message:String(cause))
    }
  }
  const beginBucketPointerDrag=(event:JSX.TargetedPointerEvent<HTMLElement>,bucketId:string,label:string)=>{
    const tree=event.currentTarget.closest<HTMLElement>('.project-tree')
    // Only rendered buckets can be a drop target, so an empty Group never becomes
    // one; folding the permutation back through the full list keeps it in place.
    const rendered=projectBuckets.map(bucket=>bucket.id)
    // Ref-only while the pointer is down, exactly like the Project drag: the ghost
    // and the insertion line are the feedback, and re-rendering the tree mid-drag
    // would move the very element holding the pointer capture.
    beginPointerDrag(event,label,`bucket:${bucketId}`,
      ()=>{cancelLongPress();dragBucketRef.current={id:bucketId,previewIds:displayBucketIds}},
      pointer=>{
        const current=dragBucketRef.current
        if(!current||!tree){showPointerDropIndicator(null);return}
        const target=reorderTargetFromContainer(tree,current.id,'vertical',pointer.clientY)
        if(!target){showPointerDropIndicator(null);return}
        const visible=reorderForHover(rendered,current.id,target.id,target.side)
        dragBucketRef.current={...current,previewIds:mergeVisibleOrder(displayBucketIds,visible)}
        const targetElement=Array.from(tree.querySelectorAll<HTMLElement>(':scope > [data-reorder-id]')).find(item=>item.dataset.reorderId===target.id)||null
        showPointerDropIndicator(targetElement,`insert-${target.side}`)
      },
      ()=>{const current=dragBucketRef.current;dragBucketRef.current=null;if(current)void commitBucketOrder(current.previewIds)},
      ()=>{dragBucketRef.current=null},
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

  // A MenuGroup left expanded would reopen with the menu next time; both hosts are
  // dismissed from a dozen places, so collapse from their state rather than each one.
  // They share the openId because opening either menu closes the other, so a group
  // belonging to the one that just closed can never still be showing.
  useEffect(() => { if (!mainMenuOpen) setMenuGroup(null) }, [mainMenuOpen])
  useEffect(() => { if (!sortMenu) setMenuGroup(null) }, [sortMenu])

  useEffect(() => {
    if (!contextMenu && !projectMenu && !sidebarMenu && !sortMenu && !noteMenu && !tabMenu && !emptyMenu && !mainMenuOpen && !renameTarget) return
    const dismissEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      setContextMenu(null)
      setProjectMenu(null)
      setSidebarMenu(null)
      setSortMenu(null)
      setNoteMenu(null)
      setTabMenu(null)
      setEmptyMenu(null)
      setMainMenuOpen(false)
      setRenameTarget(null)
    }
    window.addEventListener('keydown', dismissEscape, true)
    return () => window.removeEventListener('keydown', dismissEscape, true)
  }, [contextMenu, projectMenu, sidebarMenu, sortMenu, noteMenu, tabMenu, emptyMenu, mainMenuOpen, renameTarget])

  useEffect(() => {
    if (!contextMenu && !projectMenu && !sidebarMenu && !sortMenu && !noteMenu && !tabMenu && !emptyMenu && !mainMenuOpen) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    menuDismissedByPointer.current = false
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
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('keydown', navigate, true)
      // Return focus only on the keyboard path (Escape, or activating an item),
      // where landing back where you were is what you want. Preact runs this
      // cleanup after the dismissing click has fully settled, so reclaiming focus
      // unconditionally yanked it off whatever the user had just clicked and sent
      // their next keystrokes to the menu's old trigger instead.
      const active = document.activeElement
      const claimed = menuDismissedByPointer.current || (!!active && active !== document.body)
      menuDismissedByPointer.current = false
      if (!claimed) previous?.focus()
    }
  }, [contextMenu, projectMenu, sidebarMenu, sortMenu, noteMenu, tabMenu, emptyMenu, mainMenuOpen])

  useEffect(() => {
    if (!confirmKillId) return
    const timer = window.setTimeout(() => {
      setConfirmKillId(current => current === confirmKillId ? null : current)
    }, 2000)
    return () => window.clearTimeout(timer)
  }, [confirmKillId])

  // The command rail's End session button lives inside a memoized pane that
  // deliberately ignores callback props, so it cannot read this state directly.
  // Broadcasting the armed id (arming and disarming alike) keeps its label in step
  // with the confirm window here instead of duplicating the timer over there.
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('mux:kill-armed', { detail: confirmKillId }))
  }, [confirmKillId])

  // `options.argv` seeds an agent with a first prompt through the CLI's own argv, the same way
  // the cross-vendor review spawn does. That is deliberately not an inject-then-Enter dance: a
  // freshly spawned TUI is not ready for input for seconds, and anything written before it is
  // would be swallowed.
  const spawnTerminal = async (targetProject = projectId, split: false | SplitDirection | 'stack' = false, profileId?: string, targetSessionId?: string, position:'before'|'after'='after', backend:'shell'|'claude'|'codex'='shell', options?:{argv?:string[];seedText?:string}) => {
    if (spawning.current) return
    const target=projectsRef.current.find(item=>item.id===targetProject)
    if(!target){setError('Project is not available yet.');return}
    spawning.current = true
    const startupOrigin=performance.now()
    const pendingId=`pending-${browserUuid()}`
    const currentLayout=layoutValues.current[targetProject]||layoutMap[targetProject]||parseLayout(target.layout)
    const focused=targetSessionId??(targetProject===projectId?openAnchorId(currentLayout,focusedViewId||activeId):spawnAnchorId(currentLayout))
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
    // Every launch focuses the new tab, so every launch must also clear what is covering it.
    // On a phone the sidebar is a drawer over the whole workspace, and launching from a
    // Project row left it up: the tab really had been focused, it was just invisible behind
    // the drawer. Closing here (not at the Run menu's call site) covers every entry point —
    // sidebar row, toolbar Run, palette, keybinding, custom launcher — and it runs with the
    // optimistic state so the pending terminal is on screen immediately. No-op on desktop,
    // where `sidebarOpen` drives only the mobile drawer (desktop collapse is `sidebarCollapsed`).
    setSidebarOpen(false)
    try {
      const next = await api<Session>('POST', '/api/sessions', {
        backend, project_id: targetProject,
        profile_id: backend==='shell' ? profileId || undefined : undefined,
        ...(options?.argv?.length ? { argv: options.argv } : {}),
        // A first prompt as text: the daemon inlines short bodies into argv and stages long
        // ones into the workspace with a reader prompt, so there is no client-side ceiling.
        ...(options?.seedText ? { seed_text: options.seedText } : {}),
      })
      startupOrigins.current[next.id]=startupOrigin
      const browserTiming={api_response:performance.now()-startupOrigin}
      clientStartupTimingValues.current[next.id]=browserTiming
      setClientStartupTimings(current=>({...current,[next.id]:browserTiming}))
      if (profileId) { localStorage.setItem('mux.lastProfile',profileId); setLauncherProfile(profileId) }
      // Remembered so holding mobile Run repeats the last launch without the menu.
      localStorage.setItem('mux.lastBackend',backend)
      placement.resolvedId=next.id
      setSessions(items => [...items.filter(item=>item.id!==pendingId&&item.id!==next.id),next])
      setActiveId(next.id)
      setFocusedViewId(next.id)
      const latestLayout=layoutValues.current[targetProject]||optimisticLayout
      const withPending=terminalIds(latestLayout).includes(pendingId)?latestLayout:placePendingTerminal(latestLayout,pendingId,placement)
      const nextLayout=replaceTerminal(withPending,pendingId,next.id)
      await updateLayout(targetProject, nextLayout)
      emitTutorialAction({action:'session-launched',backend})
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

  // Backend of the most recent launch, for the held-Run repeat. Anything other
  // than a known backend (absent, stale, hand-edited) falls back to a shell.
  const lastLaunchBackend=():'shell'|'claude'|'codex'=>{
    const stored=localStorage.getItem('mux.lastBackend')
    return stored==='claude'||stored==='codex'?stored:'shell'
  }

  const openProjectMenuAt=(project:Project,x:number,y:number)=>{
    setContextMenu(null);setNoteMenu(null);setTabMenu(null);setSidebarMenu(null);setRunMenu(null);setMainMenuOpen(false)
    setProjectMenu({project,x,y})
  }

  const openRunMenu=(project:Project,element:HTMLElement)=>{
    const rect=element.getBoundingClientRect()
    setRunMenu({project,x:Math.max(6,Math.min(rect.left,window.innerWidth-306)),y:Math.min(rect.bottom+4,window.innerHeight-50)})
    setProjectMenu(null);setMainMenuOpen(false)
  }

  // Toggle for the Run triggers that always target the active Project (mobile
  // toolbar, desktop header, collapsed rail): a second click collapses what the
  // first opened. The menu's scrim sits above all of them, so on touch a second
  // tap dismisses through the scrim and the click then lands on the trigger the
  // scrim was covering — a click right after a dismissal is that toggle closing,
  // not a fresh open. Sidebar project rows keep the plain open: clicking another
  // Project's ▶ while a menu is up should switch to it, never just close.
  const toggleRunMenu=(project:Project,element:HTMLElement)=>{
    if(runMenu?.project.id===project.id||Date.now()-runMenuClosedAt.current<350){setRunMenu(null);return}
    openRunMenu(project,element)
  }

  const attachActionSessions=async(targetProject:string,nextSessions:Session[])=>{
    if(!nextSessions.length)return
    const target=projectsRef.current.find(item=>item.id===targetProject)
    if(!target)return
    let nextLayout=layoutValues.current[targetProject]||layoutMap[targetProject]||parseLayout(target.layout)
    let targetId=openAnchorId(nextLayout,targetProject===projectId?(focusedViewId||activeId):null)
    for(const session of nextSessions){nextLayout=openTab(nextLayout,targetId,terminalLeaf(session.id));targetId=session.id}
    layoutValues.current[targetProject]=nextLayout
    setSessions(items=>[...items.filter(item=>!nextSessions.some(next=>next.id===item.id)),...nextSessions])
    setLayoutMap(current=>({...current,[targetProject]:nextLayout}))
    setProjectId(targetProject);setActiveId(nextSessions.at(-1)!.id);setFocusedViewId(nextSessions.at(-1)!.id);setSidebarOpen(false)
    await updateLayout(targetProject,nextLayout)
  }

  const createProject = async () => {
    setProjectCreate(emptyProjectCreateDraft())
    setInitScripts([])
    setProjectCreateOpen(true)
    try{
      const config=await api<{project_init_scripts?:InitScript[]}>('GET','/api/config')
      const scripts=config.project_init_scripts||[]
      setInitScripts(scripts)
      setProjectCreate(value=>({...value,scripts:defaultInitScriptSelection(scripts)}))
    }catch{/* the dialog still registers a Project without its optional setup commands */}
  }

  const openProjectsManager=(focus?:{project:Project;tab?:ProjectsManagerTab})=>{
    setProjectsManagerFocus(focus?{projectId:focus.project.id,tab:focus.tab||'details'}:null)
    setProjectsManagerOpen(true);setMainMenuOpen(false);setSidebarMenu(null);setProjectMenu(null)
  }

  const closeTutorial=()=>{
    completeTutorial()
    setTutorialOpen(false)
  }
  const startTutorial=()=>{
    resetTutorial()
    setTutorialOpen(true)
  }
  const navigateTutorial=(step:TutorialStepId)=>{
    if(step!=='feature-menu')setMainMenuOpen(false)
    if(step!=='run-choice')setRunMenu(null)
    setContextMenu(null);setProjectMenu(null);setSidebarMenu(null);setTabMenu(null);setNoteMenu(null)
    if(step==='welcome'||step==='projects'){
      setSettingsOpen(false);setProjectsManagerOpen(false);setProjectCreateOpen(false);setFolderPickerOpen(false)
      if(step==='projects'&&mobileWorkspace)setSidebarOpen(true)
      return
    }
    if(step==='project-add'||step==='project-open'){
      setSettingsOpen(false);setProjectCreateOpen(false);setFolderPickerOpen(false);setProjectsManagerOpen(true);return
    }
    if(step==='project-create'){
      setSettingsOpen(false);setProjectsManagerOpen(true);return
    }
    if(step==='accounts'){
      setProjectCreateOpen(false);setFolderPickerOpen(false);setProjectsManagerOpen(false);openSettings('Accounts');return
    }
    if(['run','run-choice','workspace','new-tab','tabs','splits','resources','features','feature-menu','ready'].includes(step)){
      setSettingsOpen(false);setProjectsManagerOpen(false);setProjectCreateOpen(false);setFolderPickerOpen(false)
      const first=projectsRef.current[0]
      if(first&&!projectsRef.current.some(project=>project.id===projectId))setProjectId(first.id)
      if(mobileWorkspace&&(step==='resources'||step==='features'||step==='feature-menu'))setSidebarOpen(true)
    }
  }

  const submitProject=async()=>{
    const next=await api<Project>('POST','/api/projects',{
      name:projectCreate.name,
      root:projectCreateRoot(projectCreate),
      group_id:projectCreate.group_id||null,
      create_missing:projectCreate.mode==='new',
    })
    setProjects(items=>[...items,next]);setProjectId(next.id);setProjectCreateOpen(false);setFolderPickerOpen(false)
    emitTutorialAction({action:'project-created'})
    // The registration is already durable, so a setup command that fails to launch is
    // reported without unwinding the Project the user just made.
    const scripts=projectCreate.scripts.filter(id=>initScripts.some(script=>script.id===id))
    if(!scripts.length)return
    try{
      const result=await api<{errors:{script:string;error:string}[]}>(
        'POST',`/api/projects/${next.id}/init-scripts/run`,{script_ids:scripts})
      if(result.errors.length)setError(result.errors.map(item=>`${item.script}: ${item.error}`).join(' · '))
      await refresh()
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
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

  // On open, place the caret in the name field with the current name selected so typing
  // replaces it. On touch, focus() also raises the on-screen keyboard. rAF waits for the
  // modal to paint so the focus lands (and Android shows Gboard) reliably.
  useEffect(() => {
    if (!renameTarget) return
    const frame = requestAnimationFrame(() => {
      const input = renameInput.current
      if (!input) return
      input.focus()
      input.select()
    })
    return () => cancelAnimationFrame(frame)
  }, [renameTarget])

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

  const patchManagedProject=async(project:Project,changes:ProjectPatch)=>{
    const updated=await api<Project>('PATCH',`/api/projects/${project.id}`,changes)
    setProjects(items=>items.map(item=>item.id===updated.id?updated:item))
    if(changes.sidebar_visible===false&&projectId===project.id){
      const fallback=projects.find(item=>item.id!==project.id&&item.sidebar_visible!==false)
      if(fallback){setProjectId(fallback.id);setFocusedViewId(leaves(layoutMap[fallback.id]||parseLayout(fallback.layout))[0]?.id||null)}
    }
    return updated
  }

  const toggleProjectCollapsed=(id:string)=>setCollapsedProjects(current=>{
    const next=toggleCollapsed(current,id)
    localStorage.setItem(COLLAPSED_PROJECTS_KEY,serializeCollapsedProjects(next))
    return next
  })

  // Hiding a project only removes it from the sidebar; its record, notes, and
  // layout stay in the registry. We refuse while live work is attached so a
  // running terminal or preview can't be stranded off-screen.
  const openWorkFor=(project:Project)=>projectOpenWork(sessions,project.id,leaves(layoutMap[project.id]||parseLayout(project.layout),'preview').map(leaf=>leaf.id))
  const hideProject=async(project:Project)=>{
    await patchManagedProject(project,{sidebar_visible:false})
    setCollapsedProjects(current=>{
      if(!current.has(project.id))return current
      const next=toggleCollapsed(current,project.id)
      localStorage.setItem(COLLAPSED_PROJECTS_KEY,serializeCollapsedProjects(next))
      return next
    })
  }
  const closeWorkAndHideProject=async(project:Project)=>{
    const {liveSessions}=openWorkFor(project)
    await Promise.all(liveSessions.map(session=>api('DELETE',`/api/sessions/${session.id}`)))
    let layout=layoutMap[project.id]||parseLayout(project.layout)
    for(const session of liveSessions)layout=removeLeaf(layout,'terminal',session.id)
    for(const leaf of leaves(layout,'preview'))layout=removeLeaf(layout,'preview',leaf.id)
    await updateLayout(project.id,layout)
    await hideProject(project)
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

  // Relaunch a task-launched terminal in place: the daemon spawns a fresh copy of the
  // exact retained argv and retires the old session, and we swap the new id into the old
  // one's layout leaf so the tab, split, and focus stay put. Only offered for sessions the
  // daemon marked relaunchable, so agent/plain-shell lifecycles are never touched.
  const relaunchSession = async (session: Session) => {
    if (relaunching.current) return
    relaunching.current = true
    try {
      const { session: next } = await api<{ session: Session; replaced: string }>('POST', `/api/sessions/${session.id}/relaunch`, {})
      startupOrigins.current[next.id] = performance.now()
      const currentLayout = resolveLayout(
        layoutMap[session.project_id],
        projects.find(project => project.id === session.project_id)?.layout,
      )
      const nextLayout = terminalIds(currentLayout).includes(session.id)
        ? activateContainingStack(replaceTerminal(currentLayout, session.id, next.id), next.id)
        : currentLayout
      setSessions(items => [...items.filter(item => item.id !== session.id && item.id !== next.id), next])
      delete startupOrigins.current[session.id]
      delete clientStartupTimingValues.current[session.id]
      setClientStartupTimings(current => { const copy = { ...current }; delete copy[session.id]; return copy })
      setProjectId(session.project_id)
      if (activeId === session.id) setActiveId(next.id)
      if (focusedViewId === session.id) setFocusedViewId(next.id)
      if (zoomedId === session.id) setZoomedId(next.id)
      await updateLayout(session.project_id, nextLayout)
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      relaunching.current = false
    }
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
    const previous=layoutWriteChains.current[targetProject]||Promise.resolve(true)
    const operation=previous.catch(()=>false).then(async()=>{
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
        return true
      } catch (cause) {
        await refresh()
        const message = cause instanceof Error ? cause.message : String(cause)
        setError(message.includes('stale layout revision') ? 'Layout changed in another client; reloaded the current layout.' : message)
        return false
      }
    })
    layoutWriteChains.current[targetProject]=operation
    const persisted=await operation
    if(layoutWriteChains.current[targetProject]===operation)delete layoutWriteChains.current[targetProject]
    return persisted
  }

  const showResourceForTarget = async (target:NoteTarget,targetViewId?:string) => {
    const resourceId=noteIdForTarget(target)
    const targetProject=projects.some(project=>project.id===target.projectId)?target.projectId:(activeProject?.id||projects[0]?.id)
    if(!resourceId||!targetProject){setError('A live Project is required to open this resource.');return}
    const current=resolveLayout(layoutMap[targetProject],projects.find(project=>project.id===targetProject)?.layout)
    // An explicit target (drag/drop) is honored exactly. Everything else lands in the pane
    // you were last in: the focused view when it is still in this layout, then the owning
    // terminal, then whatever the layout has.
    const preferredAnchor=(targetProject===projectId&&focusedViewId&&stackForView(current,focusedViewId)?focusedViewId:null)||target.terminalSessionId||terminalIds(current)[0]||leaves(current)[0]?.id||null
    const focused=targetViewId||openAnchorId(current,preferredAnchor)
    setProjectId(targetProject);if(target.terminalSessionId)setActiveId(target.terminalSessionId);setFocusedViewId(resourceId)
    setContextMenu(null);setProjectMenu(null);setNoteMenu(null);setMainMenuOpen(false);setEmptyMenu(null)
    // Every resource opens the same way: a tab in the anchor's pane. Session notes used to
    // split a pane off to sit beside their terminal, which spent workspace geometry on a
    // guess — splitting is an explicit action (the tab menu, a drag), not something an
    // ordinary open should do on your behalf.
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

  const openProcessViewer=(session:Session|null=null,scope:string|null=null)=>{
    setProcessSession(session);setProcessScope(scope);setProcessViewerOpen(true)
    setContextMenu(null);setSidebarMenu(null);setMainMenuOpen(false);setProjectMenu(null)
  }

  const removeWorkspaceNote = async (targetProject:string,resourceId:string) => {
    if(focusedViewId===resourceId)setFocusedViewId(activeId)
    const current=resolveLayout(layoutMap[targetProject],projects.find(project=>project.id===targetProject)?.layout)
    await updateLayout(targetProject,removeLeaf(current,'note',resourceId))
  }

  // Selecting a project (its row/rail button, not a specific resource) restores the
  // view that was last focused there — a note, file, or terminal — rather than letting
  // the mobile projection default to whichever tab happens to sort first. Falls back to
  // a plain project switch when nothing valid is remembered.
  const selectProject = (id:string) => {
    setProjectId(id)
    const current = resolveLayout(layoutMap[id],projects.find(item=>item.id===id)?.layout)
    const remembered = rememberedView(focusMemory.current,id)
    if(!remembered||!leaves(current).some(leaf=>leaf.id===remembered))return
    setFocusedViewId(remembered)
    if(terminalIds(current).includes(remembered))setActiveId(remembered)
    const pane=stackForView(current,remembered)
    if(pane&&pane.active_child_id!==remembered)void updateLayout(id,activateStackChild(current,pane.id,remembered))
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

  /** Show one session's prompt queue, which lives in the drawer's Queue tab.
   *
   *  Focuses the target first: the tab is session-scoped and follows focus, so a chip
   *  clicked on an unfocused pane would otherwise open the queue of a different agent
   *  than the one the click named. */
  const openQueueForSession = async (sessionId: string) => {
    const session = sessionsRef.current.find(item => item.id === sessionId)
    if (session) await selectSession(session)
    openDrawerTab('queue')
    setQueueOpen(current => ({ token: current.token + 1, scope: 'session' }))
  }

  /** Pop one target's queue out into a workspace tab: the wide-review escape hatch, and
   *  what a persisted layout holding a `queue:` leaf resolves to. Nothing creates one
   *  implicitly any more — a queue tab per session inspected was the reason the queue
   *  moved into the drawer. */
  const openQueueTab = async (sessionId: string) => {
    const session = sessionsRef.current.find(item => item.id === sessionId)
    const targetProject = session?.project_id || projectId
    if (!targetProject) return
    const current = resolveLayout(layoutMap[targetProject], projects.find(project => project.id === targetProject)?.layout)
    const resourceId = queueLeafId(sessionId)
    const focused = openAnchorId(current, sessionId)
    setProjectId(targetProject)
    setFocusedViewId(resourceId)
    setContextMenu(null); setTabMenu(null); setMainMenuOpen(false)
    await updateLayout(targetProject, openTab(current, focused, resourceLeaf('queue', resourceId)))
  }

  /**
   * Deliver a note/markdown/file selection — Phase 4 shape. A new session is seeded through
   * `seed_text` (the daemon inlines short bodies into argv and stages long ones into the
   * workspace, so there is no client-side length ceiling). A live-session send is a queue
   * operation: the message is staged armed, then delivered with the queue's own
   * "send next now" — one audited path with the daemon re-checking identity, revision, and
   * readiness at send time. With `submit` off the text only fills the target's composer,
   * which is not a delivery and deliberately stays a plain input write.
   */
  const deliverToAgent = async (target: SendToAgentTarget, message: string): Promise<SendToAgentResult> => {
    if (target.kind === 'new') {
      setSendToAgent(null)
      // spawnTerminal reports its own failures through the toast and unwinds the layout.
      await spawnTerminal(target.projectId,'horizontal',undefined,undefined,'after',target.backend,{seedText:message})
      return { status: 'done' }
    }
    const sid = target.session.id
    try {
      if (!target.submit) {
        await api('POST',`/api/sessions/${sid}/input`,{data:pastePayload(message)})
        await selectSession(target.session)
        return { status: 'done' }
      }
      let messageId = target.confirmQueued?.messageId || ''
      let revision = target.confirmQueued?.revision || 0
      if (target.confirmQueued?.bodyChanged && messageId) {
        const edited = await editQueueMessage(messageId, revision, message)
        revision = edited.revision
      }
      if (!messageId) {
        const created = await enqueueMessage(sid, message, { armed: true })
        messageId = created.id
        revision = created.revision
      }
      const outcome = await sendQueueMessage(messageId, revision, {
        confirm: !!target.confirmQueued,
        idempotencyKey: browserUuid(),
      })
      switch (outcome.status) {
        case 'sent':
          await selectSession(target.session)
          return { status: 'done' }
        case 'queued_behind':
          // Strict order: the message waits in the one audited place. Show it.
          await openQueueForSession(sid)
          return { status: 'done' }
        case 'blocked':
          return { status: 'blocked', messageId, revision, reasons: outcome.reasons, protected: outcome.protected }
        case 'not_due':
          // A scheduled item reached this path (retarget/confirm of an
          // existing message): it is queued, just not yet due.
          await openQueueForSession(sid)
          return { status: 'done' }
        case 'stranded':
        case 'expired':
        case 'revision_conflict':
        case 'error':
          return { status: 'error', error: 'error' in outcome ? outcome.error : 'The message changed underneath this dialog; check the Queue panel.' }
      }
    } catch (cause) {
      return { status: 'error', error: cause instanceof Error ? cause.message : String(cause) }
    }
  }

  const openInSplit = async (session: Session, direction: SplitDirection = 'horizontal', position:'before'|'after'='after', targetId=activeId) => {
    setProjectId(session.project_id)
    setActiveId(session.id)
    setFocusedViewId(session.id)
    await updateLayout(session.project_id, splitTerminal(layoutMap[session.project_id] || emptyLayout(), targetId, session.id, direction,position))
    setContextMenu(null)
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

  // History indexes every project's native transcripts, so it is a global
  // overlay rather than a per-project pane tab. Scope belongs to the menu that
  // opened it: the app menu browses everything, a Project row pre-filters to
  // that Project (the browser's own picker can still widen back to all).
  const showHistory = (scope:Project|null=null) => {
    setHistoryScope(scope?scope.id:'')
    setHistoryOpen(true)
    setMainMenuOpen(false);setProjectMenu(null)
  }

  const openHandoff = async (entry:HistoryEntry) => {
    try {
      const result=await api<{markdown:string}>('GET',`/api/history/${entry.id}/handoff`)
      setHandoffState({entry,markdown:result.markdown,message:'Review this export before using it as agent context.'})
    } catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }

  const previewSecondOpinion = async (entry:HistoryEntry,instructions='',targetProject=entry.project_id||projectId) => {
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
      setReviewState(null);await refresh();setProjectId(result.session.project_id);setActiveId(result.session.id)
    }catch(cause){setReviewState(current=>current?{...current,loading:false,error:cause instanceof Error?cause.message:String(cause)}:current)}
  }

  const resumeHistoryEntry = async (entry: HistoryEntry) => {
    try {
      const targetProject = entry.project_id || projectId
      const resumed = await api<Session>('POST', `/api/history/${entry.id}/resume`, { project_id: targetProject, target_session_id: targetProject === projectId ? activeId : undefined })
      setSessions(items => [...items, resumed]); setProjectId(resumed.project_id); setActiveId(resumed.id); setFocusedViewId(resumed.id)
      setHistoryOpen(false)
      await refresh()
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
  }

  // Fork an agent conversation into a sibling pane. The daemon injects Claude's
  // native /branch (or a Codex resume child-thread), attaches the new pane, and
  // returns the new session; refresh() re-syncs the server-updated layout.
  const branchSession = async (session: Session) => {
    try {
      const result = await api<{ session: Session; source: string }>('POST', `/api/sessions/${session.id}/branch`, { target_session_id: session.id, direction: 'after' })
      setSessions(items => [...items, result.session]); setActiveId(result.session.id); setFocusedViewId(result.session.id)
      await refresh()
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
  }

  // Resume targets the history entry of the conversation the pane was last on,
  // which is its run rather than its session: a pane that rolled its
  // conversation (/clear) or inherited one (a previous resume) owns a row keyed
  // by the run id, and asking for the session id there finds nothing.
  const resumeSession = async (session: Session) => {
    try {
      const resumed = await api<Session>('POST', `/api/history/${session.agent_run_id || session.id}/resume`, { project_id: session.project_id })
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
  const setVoiceMode = (session: Session, mode: VoiceMode) => {
    // Cut this pane's audio on the click, not when the PATCH lands and not when the
    // current clip happens to end: "off" has to be audible immediately.
    if (mode === 'off') stopSessionPlayback(session.id)
    return api<Session>('PATCH', `/api/sessions/${session.id}`, { voice_mode: mode }).then(updateSession).catch(cause => setError(cause instanceof Error ? cause.message : String(cause)))
  }
  const cycleVoiceMode = (session: Session) => {
    const order: VoiceMode[] = ['off', 'on_demand', 'auto']
    void setVoiceMode(session, order[(order.indexOf(effectiveVoiceMode(session)) + 1) % order.length])
  }
  const voiceModeLabel = (mode: VoiceMode) => mode === 'on_demand' ? 'on demand' : mode === 'auto' ? 'auto on reply' : 'off'
  const speakLastReply = async (session: Session) => {
    unlockPlayback()
    try {
      const clip = await api<VoiceClip>('POST', `/api/sessions/${session.id}/voice/generate`)
      if (clip?.id) void playClip(clip.id, session.id).catch(() => {})
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
  }

  const commandSession = contextMenu?.session || active
  const commandProject = projectMenu?.project || activeProject
  // Cycle the mobile unified tab strip. Recomputes the projection from live layout
  // state so it works when invoked from a gesture, outside the render-time `mobileProjection`.
  // Short label for a projected mobile tab; also what the swipe HUD announces.
  const queueTabLabel = (resourceId: string): string => {
    const targetSessionId = queueLeafSessionId(resourceId)
    const owner = targetSessionId ? sessions.find(item => item.id === targetSessionId) : undefined
    return owner ? `Queue · ${sessionName(owner)}` : 'Queue'
  }

  const mobileTabLabel = (leaf: PaneLeaf): string => {
    if (leaf.kind === 'terminal') { const session = sessions.find(item => item.id === leaf.id); return session ? sessionName(session) : leaf.id }
    if (leaf.kind === 'preview') { const preview = previews[leaf.id]; return preview ? `:${preview.port}` : leaf.id }
    if (leaf.kind === 'history') return 'History'
    if (leaf.kind === 'queue') return queueTabLabel(leaf.id)
    return noteTabLabel(leaf.id)
  }

  const navigateMobileTab = (offset: number) => {
    const layout = layoutValues.current[projectId] || activeLayout
    const projection = mobileWorkspaceProjection(layout, focusedViewId, activeId, mobileTabOrder[projectId])
    const tabs = projection.tabs
    if (tabs.length < 2) return
    const index = projection.selected ? tabs.findIndex(tab => tab.id === projection.selected!.id) : -1
    const next = tabs[((index < 0 ? 0 : index) + offset + tabs.length) % tabs.length]
    if (!next) return
    showMobileHud(mobileTabLabel(next))
    setFocusedViewId(next.id)
    if (next.kind === 'terminal') setActiveId(next.id)
    const pane = stackForView(layout, next.id)
    if (pane && pane.active_child_id !== next.id) void updateLayout(projectId, activateStackChild(layout, pane.id, next.id))
  }
  async function reloadDaemon() {
    setMainMenuOpen(false)
    setPaletteOpen(false)
    // Direct fetch (not api()): the 409 body carries a human-readable
    // `message` explaining why a restart would kill sessions.
    let accepted = false
    try {
      const response = await fetch('/api/daemon/restart', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      })
      if (response.status === 202) accepted = true
      else {
        const detail = await response.json().catch(() => ({}))
        setError(detail.message || detail.error || 'Daemon reload failed.')
      }
    } catch {
      setError('Daemon reload request failed.')
    }
    if (!accepted) return
    setDaemonReloading(true)
    // Let the old daemon actually exit before treating a healthy response as
    // the successor; the first ~second could still be the predecessor.
    await new Promise(resolve => setTimeout(resolve, 1500))
    const deadline = Date.now() + 90_000
    while (Date.now() < deadline) {
      try {
        const health = await fetch('/api/health', { cache: 'no-store' })
        if (health.ok) { location.reload(); return }
      } catch { /* daemon still restarting */ }
      await new Promise(resolve => setTimeout(resolve, 750))
    }
    setDaemonReloading(false)
    setError('The daemon did not come back within 90s. Check daemon-relaunch.log in the data directory.')
  }

  function redeployApp() {
    // In-app confirmation (native dialogs are banned by the phase-3 contract).
    setMainMenuOpen(false)
    setPaletteOpen(false)
    setSidebarMenu(null)
    setRedeployConfirmOpen(true)
  }

  async function startRedeploy() {
    setRedeployConfirmOpen(false)
    // Direct fetch (not api()): 409 bodies carry a human-readable `message`
    // (no source checkout, uv missing, supervisor detached, already running).
    let accepted = false
    try {
      const response = await fetch('/api/daemon/redeploy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      })
      if (response.status === 202) accepted = true
      else {
        const detail = await response.json().catch(() => ({}))
        setError(detail.message || detail.error || 'Redeploy request failed.')
      }
    } catch {
      setError('Redeploy request failed.')
    }
    if (!accepted) return
    setRedeploying(true)
    // Phase 1: the build runs while this daemon still serves. Watch the
    // status endpoint — the lock clearing without the daemon ever going down
    // means the build failed and the old app is untouched. Phase 2: the
    // daemon drops (stop/swap/relaunch); poll health until the successor (or
    // the rolled-back previous build) answers, then reload the page.
    const deadline = Date.now() + 8 * 60_000
    let sawDown = false
    while (Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 2000))
      let healthy = false
      try {
        const health = await fetch('/api/health', { cache: 'no-store' })
        healthy = health.ok
      } catch { /* daemon down: stop/swap/relaunch in progress */ }
      if (!healthy) { sawDown = true; continue }
      if (sawDown) { location.reload(); return }
      try {
        const status = await fetch('/api/daemon/redeploy', { cache: 'no-store' })
        if (status.ok) {
          const detail = await status.json()
          if (detail.running === false) {
            setRedeploying(false)
            const tail = Array.isArray(detail.log_tail) ? detail.log_tail.slice(-3).join(' · ') : ''
            setError(`Redeploy failed before the app was stopped; the current app is untouched. ${tail || 'Check redeploy.log in the data directory.'}`)
            return
          }
        }
      } catch { /* transient; keep waiting */ }
    }
    setRedeploying(false)
    setError('Redeploy did not complete within 8 minutes. Check redeploy.log in the data directory.')
  }

  const commands: Command[] = [
    { id: 'palette.open', label: 'Open command palette', category: 'view', available: true, run: () => setPaletteOpen(true) },
    // Session-preserving daemon restart (PTY supervisor); refused server-side
    // when a restart would kill sessions. Reload UI is the browser-half of a
    // frontend update: fetch the freshly built assets, keep everything else.
    { id: 'daemon.reload', label: 'Reload daemon (keep sessions)', category: 'view', available: true, run: () => void reloadDaemon() },
    // Full frozen-app redeploy: staged rebuild from source, swap, relaunch;
    // sessions survive and a failed build leaves the current app running.
    { id: 'app.redeploy', label: 'Rebuild + redeploy app (keep sessions)', category: 'view', available: true, run: () => redeployApp() },
    { id: 'ui.reload', label: 'Reload UI', category: 'view', available: true, run: () => location.reload() },
    { id: 'mobileTab.next', label: 'Focus next tab (mobile)', category: 'pane', available: mobileWorkspace, disabledReason: 'Available on the mobile workspace', run: () => navigateMobileTab(1) },
    { id: 'mobileTab.previous', label: 'Focus previous tab (mobile)', category: 'pane', available: mobileWorkspace, disabledReason: 'Available on the mobile workspace', run: () => navigateMobileTab(-1) },
    { id: 'sidebar.open', label: 'Open navigation sidebar', category: 'view', available: true, run: () => setSidebarOpen(true) },
    { id: 'sidebar.close', label: 'Close navigation sidebar', category: 'view', available: true, run: () => setSidebarOpen(false) },
    { id: 'sidebar.toggle', label: 'Toggle navigation sidebar', category: 'view', available: true, run: () => setSidebarOpen(value => !value) },
    { id:'prompts.open',label:'Open prompt library',category:'input',available:true,run:()=>{setPromptScope(null);setPromptLibraryOpen(true);setMainMenuOpen(false)} },
    { id:'prompts.openProject',label:'Open prompt library for selected project',category:'input',available:!!commandProject,disabledReason:'No project selected',run:()=>{setPromptScope(commandProject||null);setPromptLibraryOpen(true);setMainMenuOpen(false);setProjectMenu(null)} },
    { id:'observations.open',label:'Open selected project’s observation inbox',category:'input',available:!!commandProject,disabledReason:'No project selected',run:()=>{setObservationsProject(commandProject||null);setMainMenuOpen(false);setProjectMenu(null)} },
    // The mailbox is a scope of the Queue panel, not a modal of its own: "what is queued
    // for this agent" and "what is queued anywhere" are one store, and were two surfaces
    // with two different action sets over it.
    { id:'mailbox.open',label:'Open mailbox (queued messages, auto-delivery)',category:'input',available:true,run:()=>{openDrawerTab('queue');setQueueOpen(current=>({token:current.token+1,scope:'inbox'}));setMainMenuOpen(false);setProjectMenu(null)} },
    { id:'queue.open',label:'Open the prompt queue for the focused session',category:'input',available:!!active&&isAgent(active),disabledReason:'Focus a Claude or Codex session',run:()=>{if(active)void openQueueForSession(active.id)} },
    { id: 'session.spawnShell', label: 'New terminal in current project', category: 'session', available: !!activeProject, disabledReason:'Create or select a project first', run: () => void spawnTerminal() },
    { id: 'session.quickLaunch', label: 'New terminal custom…', category: 'session', available: !!activeProject, disabledReason:'Create or select a project first', run: () => openLauncher() },
    // `project.create` predates this and opens the registry; adding a Project is
    // the common intent, so it gets its own direct entry.
    { id: 'project.add', label: 'Add project', category: 'project', available: true, run: () => { setSidebarMenu(null);setMainMenuOpen(false);setProjectMenu(null);void createProject() } },
    { id: 'project.create', label: 'Manage projects', category: 'project', available: true, run: () => openProjectsManager() },
    { id: 'history.open', label: 'Browse session history', category: 'view', available: true, run: () => void showHistory() },
    { id: 'history.openProject', label: 'Browse selected project’s session history', category: 'view', available: !!commandProject, disabledReason: 'No project selected', run: () => void showHistory(commandProject||null) },
    { id: 'project.files', label: 'Browse current project files', category: 'view', available: !!activeProject, disabledReason: 'No project selected', run: () => activeProject&&openProjectFiles(activeProject) },
    { id: 'settings.open', label: 'Open Settings', category: 'view', available: true, run: () => openSettings() },
    { id: 'usage.open', label: 'Open usage analytics', category: 'view', available: true, run: () => {setUsageOpen(true);setMainMenuOpen(false)} },
    { id: 'hooks.open', label: 'Open Automation', category: 'view', available: true, run: () => {setAutomationOpen(true);setMainMenuOpen(false)} },
    { id: 'notifications.open', label: `Open notifications${notificationUnread?` (${notificationUnread} new)`:''}`, category: 'view', available: true, run: openNotifications },
    { id: 'notes.open', label: 'Open current project note', category: 'view', available: !!activeProject, disabledReason: 'No project selected', run: () => activeProject&&openProjectNotes(activeProject) },
    { id: 'session.note', label: 'Open selected session note', category: 'view', available: !!commandSession, disabledReason: 'No session selected', run: () => commandSession&&openSessionNotes(commandSession) },
    { id: 'notes.browse', label: 'Browse session notes', category: 'view', available: true, run: () => openNotesBrowser(null) },
    { id: 'notes.browseProject', label: 'Browse this project’s session notes', category: 'view', available: !!activeProject, disabledReason: 'No project selected', run: () => activeProject&&openNotesBrowser(activeProject) },
    { id: 'project.note', label: 'Open selected project note', category: 'view', available: !!commandProject, disabledReason: 'No project selected', run: () => commandProject&&openProjectNotes(commandProject) },
    { id: 'processes.open', label: 'Inspect selected session processes and previews', category: 'view', available: !!commandSession, disabledReason: 'No session selected', run: () => {if(commandSession)openProcessViewer(commandSession)} },
    { id: 'processes.all', label: 'Open unified process viewer', category: 'view', available: true, run: () => openProcessViewer() },
    { id: 'processes.project', label: 'Inspect selected project’s processes', category: 'view', available: !!commandProject, disabledReason: 'No project selected', run: () => openProcessViewer(null,commandProject?.id||null) },
    { id: 'terminal.find', label: 'Find in focused terminal', category: 'terminal', available: !!active, disabledReason: 'No focused terminal', run: () => window.dispatchEvent(new CustomEvent('mux:terminal-find', { detail: activeId })) },
    // Which note is focused is not App state — it is whatever Continuity editor reported
    // focus last — so this cannot be answered by an `available` flag computed at render.
    // The resource holding that editor claims the event by cancelling it, and an unclaimed
    // event is what "no note is focused" looks like.
    { id: 'note.find', label: 'Find in focused note', category: 'view', available: true, run: () => {
      const claim = new CustomEvent('mux:note-find', { cancelable: true })
      window.dispatchEvent(claim)
      if (!claim.defaultPrevented) setError('No focused note to search. Click into a note first.')
    } },
    // A plain "put the keyboard away" with no sticky mode behind it. On touch this is
    // the only way out of a note editor's keyboard: the read/select toggle below is a
    // terminal mode, and a note has no rail button of its own.
    { id: 'keyboard.dismiss', label: 'Hide the on-screen keyboard', category: 'view', available: true, run: () => dismissSoftKeyboard() },
    // The ⌨ read/select toggle used to exist only as a rail button, so it could
    // not be bound to a gesture or reached from the palette — on touch it is one
    // of the most-used controls, so it is a first-class command.
    //
    // It also carries the default two-finger-swipe-down binding, i.e. the gesture a
    // touch user reaches for to push the keyboard away wherever they are. So it is
    // available with no terminal focused, and when a field outside the terminal's own
    // live input is what is holding the keyboard up, it lowers that instead of
    // toggling read/select mode on a terminal the mobile workspace is not even
    // showing. With nothing holding the keyboard up it stays a plain toggle, which is
    // what turns read mode back off.
    { id: 'terminal.keyboardToggle', label: 'Hide the on-screen keyboard (read/select mode in a focused terminal)', category: 'terminal', available: true, run: () => {
      const holder = softKeyboardHolder()
      if (holder && !holder.classList.contains('mobile-terminal-live-input')) { holder.blur(); return }
      if (activeId) window.dispatchEvent(new CustomEvent('mux:terminal-action', { detail: { sessionId: activeId, action: 'toggleKeyboard' } }))
    } },
    // The utility drawer is a slide-in panel, so every entry point is a toggle:
    // the gesture that pulls it in pushes it back out, and a tab command run while
    // that tab is showing closes it.
    { id: 'drawer.toggle', label: clipboardOpen ? 'Close side panel' : `Open side panel (${DRAWER_TABS.find(tab=>tab.id===drawerTabId)?.label||'clipboard'})`, category: 'view', available: true, run: () => { setClipboardOpen(value => !value); setMainMenuOpen(false); setContextMenu(null) } },
    ...DRAWER_TABS.map((tab): Command => ({
      id: `drawer.${tab.id}`, label: `Side panel: ${tab.label}`, category: tab.id === 'notifications' ? 'view' : 'clipboard',
      available: true, run: () => showDrawerTab(tab.id),
    })),
    // Tab order is persistent state a drag can scramble, so it needs a way back that is not
    // "drag five tabs into place from memory".
    { id: 'drawer.resetTabs', label: 'Reset side panel tab order', category: 'view', available: !isDefaultDrawerTabOrder(drawerOrder), disabledReason: 'Side panel tabs are already in their default order', run: resetDrawerTabOrder },
    { id: 'clipboard.open', label: 'Open clipboard history', category: 'clipboard', available: true, run: () => showDrawerTab('clipboard') },
    { id: 'clipboard.clear', label: 'Clear unpinned clipboard history', category: 'clipboard', available: true, run: () => void clearClipboardHistory().then(removed => { window.dispatchEvent(new CustomEvent(CLIPBOARD_CHANGED_EVENT)); setError(`Cleared ${removed} clipboard entr${removed===1?'y':'ies'}.`) }).catch(cause => setError(cause instanceof Error?cause.message:String(cause))) },
    ...(['copy', 'paste', 'selectAll', 'clear'] as const).map((action): Command => ({
      id: `terminal.${action}`, label: `${action === 'selectAll' ? 'Select all' : action[0].toUpperCase() + action.slice(1)} in focused terminal`,
      category: 'clipboard', available: !!active, disabledReason: 'No focused terminal',
      run: () => window.dispatchEvent(new CustomEvent('mux:terminal-action', { detail: { sessionId: activeId, action } })),
    })),
    { id: 'session.kill', label: active && isEndedSession(active) ? 'Remove focused session from sidebar' : 'Kill focused session', category: 'session', available: !!active, disabledReason: 'No focused session', run: () => active && requestKill(active) },
    { id: 'session.killImmediate', label: commandSession && isEndedSession(commandSession) ? 'Remove selected session from sidebar' : 'Kill selected session immediately', category: 'session', available: !!commandSession, disabledReason: 'No session selected', run: () => commandSession && void killNow(commandSession) },
    { id: 'session.relaunch', label: 'Relaunch focused task terminal', category: 'session', available: !!active && !!active.relaunchable, disabledReason: 'Relaunch is available for task-launched terminals', run: () => active && void relaunchSession(active) },
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
    // "First"/"last" against the sorted view, not the stored positions: with a sort
    // active they disagree, and the enabled state has to describe what is on screen.
    { id:'project.moveUp',label:'Move selected Project up',category:'project',available:!!commandProject&&displayProjects.filter(item=>bucketIdFor(item)===bucketIdFor(commandProject))[0]?.id!==commandProject.id,disabledReason:'Project is already first in its Group',run:()=>commandProject&&moveProjectRelative(commandProject,-1) },
    { id:'project.moveDown',label:'Move selected Project down',category:'project',available:!!commandProject&&displayProjects.filter(item=>bucketIdFor(item)===bucketIdFor(commandProject)).at(-1)?.id!==commandProject.id,disabledReason:'Project is already last in its Group',run:()=>commandProject&&moveProjectRelative(commandProject,1) },
    { id: 'project.settings', label: 'Open selected project settings', category: 'project', available: !!commandProject, disabledReason: 'No project selected', run: () => commandProject && openProjectsManager({ project: commandProject, tab: 'settings' }) },
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
    ...displayProjects.slice(0, 9).map((project, index): Command => ({
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
        setPaletteOpen(false); setLauncherOpen(false); setContextMenu(null); setProjectMenu(null);setSidebarMenu(null);setSortMenu(null);setNoteMenu(null);setTabMenu(null); setEmptyMenu(null); setMainMenuOpen(false); setSidebarOpen(false); setRenameTarget(null); setProcessSession(null);setProcessViewerOpen(false); setSettingsOpen(false); setProjectsManagerOpen(false); setReviewState(null);setHandoffState(null);setClipboardOpen(false)
      }
    }
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target
      // A `[data-menu-toggle]` trigger owns its own open/close: dismissing here
      // would close on pointer-down and let the trigger's click reopen, which is
      // indistinguishable from "the menu never closes".
      if (target instanceof Element && target.closest('.context-menu,.menu-trigger,[data-menu-toggle],.bucket-sort')) return
      // This pointer, not the menu, decides where focus goes next.
      menuDismissedByPointer.current = true
      setContextMenu(null)
      setProjectMenu(null)
      setSidebarMenu(null)
      setSortMenu(null)
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

  // A rail prompt button with {{placeholders}} opens the Prompts tab on that template.
  // This deliberately opens rather than toggling (`showDrawerTab`): the click already
  // said "I want this template", so closing the drawer on it would be perverse.
  useEffect(() => {
    const onPromptTemplate = (event: Event) => {
      const detail = (event as CustomEvent<{ key?: string }>).detail
      if (!detail?.key) return
      setPromptPreselect({ key: detail.key })
      localStorage.setItem(DRAWER_TAB_KEY, 'prompts')
      setDrawerTabId('prompts')
      setClipboardOpen(true)
    }
    window.addEventListener(PROMPT_RAIL_EVENT, onPromptTemplate)
    return () => window.removeEventListener(PROMPT_RAIL_EVENT, onPromptTemplate)
  })

  // Mobile touch gestures. Handled at the shell level so the terminal's own pointer
  // pipeline (scroll, long-press selection, tap-to-focus) is untouched: terminals
  // ignore horizontal drags and second fingers, so we only claim what they discard.
  // Gestures dispatch through the shared `mux:command` bus, keeping this effect
  // decoupled from the per-render `commands` array.
  // Panel-open state rides in a ref so toggling a panel doesn't re-register the
  // touch listeners (and can't drop a gesture already in flight).
  const overlayPanels = useRef({ sidebarOpen: false, drawerOpen: false })
  overlayPanels.current = { sidebarOpen, drawerOpen: clipboardOpen }
  useEffect(() => {
    if (!mobileWorkspace) return
    let state: { startX:number; startY:number; lastX:number; lastY:number; maxPointers:number; start:number; axis:'?'|'h'|'v'; claims:ReturnType<typeof markPointerDragClaims> } | null = null
    const centroid = (touches: TouchList) => {
      let x = 0, y = 0
      for (let i = 0; i < touches.length; i++) { x += touches[i].clientX; y += touches[i].clientY }
      return { x: x / touches.length, y: y / touches.length }
    }
    // Horizontal swipe nav must yield to elements that own horizontal scrolling — the
    // command rail, tab strips, etc. — or their native scroll gets hijacked into a tab
    // switch. Named strips plus a generic overflow-x scan cover current and future ones.
    const startsInHorizontalScroller = (element: Element | null) => {
      for (let node = element; node && node !== document.body; node = node.parentElement) {
        if (node.matches('.terminal-action-rail, .stack-tabs, .voice-strip')) return true
        const overflowX = getComputedStyle(node).overflowX
        if ((overflowX === 'auto' || overflowX === 'scroll') && node.scrollWidth > node.clientWidth + 1) return true
      }
      return false
    }
    const onStart = (event: TouchEvent) => {
      const target = event.target
      // Act over the workspace, the sidebar, or its scrim (so a swipe over the dimmed
      // area toggles the open sidebar shut). Taps inside modals/menus/palette match none
      // of these, so open overlays stay immune to gesture hijacking.
      // The utility drawer and its scrim are included so the leftward two-finger
      // swipe that pulls the drawer in can also push it back out from over it.
      if (!(target instanceof Element) || !target.closest('.mobile-unified-workspace, .sidebar, .sidebar-scrim, .utility-drawer, .utility-drawer-scrim') || startsInHorizontalScroller(target)) { state = null; detachMove(); return }
      // A drag that has claimed the pointer owns it outright (`pointerDragClaim.ts`); a
      // second finger landing mid-drag does not get to start a gesture behind it.
      if (pointerDragOwnsPointer()) { state = null; detachMove(); return }
      const point = centroid(event.touches)
      if (!state) state = { startX: point.x, startY: point.y, lastX: point.x, lastY: point.y, maxPointers: event.touches.length, start: Date.now(), axis: '?', claims: markPointerDragClaims() }
      else state.maxPointers = Math.max(state.maxPointers, event.touches.length)
      // Two fingers is never text entry, so lower the keyboard the moment the second
      // one lands rather than waiting for the command at touchend. An editor focuses
      // its input on every pointerdown (that is how a tap places the caret), so a
      // two-finger swipe starting over a note raises the keyboard on the way in — and
      // a swipe later is far too late to hide that it happened. Blurring in the same
      // frame the focus landed is what keeps it from ever animating up.
      if (event.touches.length >= 2) dismissSoftKeyboard()
      attachMove()
    }
    const onMove = (event: TouchEvent) => {
      if (!state) return
      // A drag activates after 5 px, i.e. part-way through a sequence this handler is
      // already tracking. Forfeit that sequence the moment it does: the travel measured
      // so far belongs to the drag, and letting it accumulate would classify at touch-end.
      if (pointerDragOwnsPointer(state.claims)) { state = null; detachMove(); return }
      const point = centroid(event.touches)
      state.lastX = point.x; state.lastY = point.y
      state.maxPointers = Math.max(state.maxPointers, event.touches.length)
      const dx = point.x - state.startX, dy = point.y - state.startY
      if (state.axis === '?' && (Math.abs(dx) > 10 || Math.abs(dy) > 10)) state.axis = Math.abs(dx) >= Math.abs(dy) ? 'h' : 'v'
      // Suppress native pinch/scroll only for gestures we own: any two-finger move, or
      // a single-finger horizontal swipe. Vertical single-finger stays with the terminal.
      if ((state.maxPointers >= 2 || (state.maxPointers === 1 && state.axis === 'h')) && event.cancelable) event.preventDefault()
    }
    // Only the move listener has to be non-passive (it preventDefaults the gestures we own),
    // and a non-passive touchmove registered on the window makes Chrome route *every* touch
    // through the main thread before it may scroll — on a busy pane that is enough to eat the
    // first drag on a horizontal scroller like the command rail. So it is attached only once
    // a touchstart claims the gesture (a listener added during touchstart dispatch still gets
    // cancelable moves) and dropped as soon as the sequence ends, which leaves drags inside
    // scrollers on the compositor fast path with no handler to wait for.
    let moveAttached = false
    const attachMove = () => { if (moveAttached) return; moveAttached = true; window.addEventListener('touchmove', onMove, { passive: false }) }
    const detachMove = () => { if (!moveAttached) return; moveAttached = false; window.removeEventListener('touchmove', onMove) }
    const onEnd = (event: TouchEvent) => {
      if (event.touches.length === 0) detachMove()
      if (!state) return
      if (event.touches.length > 0) return // wait until every finger lifts
      // Belt to the move handler's braces, and the one check that cannot be skipped:
      // `pointerup` precedes `touchend`, so a drag that just released its claim is still
      // the owner of everything this sequence measured.
      if (pointerDragOwnsPointer(state.claims)) { state = null; return }
      const slot = classifyGesture({ pointerCount: state.maxPointers, dx: state.lastX - state.startX, dy: state.lastY - state.startY, durationMs: Date.now() - state.start })
      state = null
      if (!slot) return
      const command = resolveGestureCommand(slot, mobileGestures, overlayPanels.current, swipeAwayClose)
      // A short tick on recognition: without it a swipe that lands on an empty
      // command, or a tab change the eye misses, reads as "nothing happened".
      if (command) { navigator.vibrate?.(12); window.dispatchEvent(new CustomEvent('mux:command', { detail: command })) }
    }
    // start/end never preventDefault, so they stay passive and cost the scroller nothing.
    window.addEventListener('touchstart', onStart, { passive: true })
    window.addEventListener('touchend', onEnd, { passive: true })
    window.addEventListener('touchcancel', onEnd, { passive: true })
    return () => {
      window.removeEventListener('touchstart', onStart)
      detachMove()
      window.removeEventListener('touchend', onEnd)
      window.removeEventListener('touchcancel', onEnd)
    }
  }, [mobileWorkspace, mobileGestures, swipeAwayClose])

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
    let moved = false
    const moveDivider = (pointer: PointerEvent) => {
      const ratio = direction === 'horizontal'
        ? (pointer.clientX - rect.left) / rect.width
        : (pointer.clientY - rect.top) / rect.height
      moved = true
      latest = setSplitRatio(activeLayout, path, ratio)
      setLayoutMap(current => ({ ...current, [projectId]: latest }))
    }
    // pointercancel, not just pointerup: a touch drag interrupted by palm
    // rejection or an OS gesture fires only cancel, which left the global
    // pointermove listener alive and then wrote a layout PATCH from stale
    // pre-drag state at whatever unrelated pointerup came next.
    const stopResize = () => {
      window.removeEventListener('pointermove', moveDivider)
      window.removeEventListener('pointerup', stopResize)
      window.removeEventListener('pointercancel', stopResize)
      if (moved) void updateLayout(projectId, latest)
    }
    window.addEventListener('pointermove', moveDivider)
    window.addEventListener('pointerup', stopResize, { once: true })
    window.addEventListener('pointercancel', stopResize, { once: true })
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
      ()=>{dragStackTabRef.current=initial;emitTutorialAction({action:'tab-drag-started'})},
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
          void updateLayout(projectId,moveLeafToSplit(latest,current.kind,current.childId,current.targetStackId,direction,position)).then(persisted=>persisted&&emitTutorialAction({action:'tab-dropped',zone:current.zone}));return
        }
        const moved=current.stackId===current.targetStackId?latest:moveLeafToStack(latest,current.kind,current.childId,current.targetStackId)
        void updateLayout(projectId,reorderStack(moved,current.targetStackId,current.previewIds)).then(persisted=>persisted&&emitTutorialAction({action:'tab-dropped',zone:'tabs'}))
      },
      ()=>{setDragStackTab(null);emitTutorialAction({action:'tab-drag-cancelled'})},
    )
  }

  // Drag a file row out of the Files tree as a brand-new tab. Unlike a workspace-tab drag this
  // leaf does not exist yet, so the drop creates it (openTab/split) rather than moving it; if it
  // is already open elsewhere it is moved instead. Reuses the pane hit-test and drop indicators.
  const beginFileTabDrag=(event:JSX.TargetedPointerEvent<HTMLElement>,path:string)=>{
    const childId=noteResourceId('file',path)
    const fileLeaf=resourceLeaf('note',childId)
    let drop:{targetStackId:string;zone:PaneDropZone;previewIds:string[]}|null=null
    beginPointerDrag(event,path.split('/').pop()||path,`file:${childId}`,
      ()=>{drop=null},
      pointer=>{
        const hit=document.elementFromPoint(pointer.clientX,pointer.clientY) as HTMLElement|null
        const paneElement=hit?.closest<HTMLElement>('.pane-stack[data-pane-stack-id]')
        const targetStackId=paneElement?.dataset.paneStackId
        if(!paneElement||!targetStackId){drop=null;showPointerDropIndicator(null);return}
        const latest=layoutValues.current[projectId]||activeLayout
        const targetPane=paneStacks(latest).find(pane=>pane.id===targetStackId)
        if(!targetPane){drop=null;showPointerDropIndicator(null);return}
        const tabStrip=paneElement.querySelector<HTMLElement>(':scope > .stack-tabs')
        const tabBox=tabStrip?.getBoundingClientRect()
        if(tabStrip&&tabBox&&pointer.clientY>=tabBox.top&&pointer.clientY<=tabBox.bottom){
          const target=reorderTargetFromContainer(tabStrip,childId,'horizontal',pointer.clientX)
          const base=[...targetPane.children.map(child=>child.id),childId]
          const previewIds=target?reorderForHover(base,childId,target.id,target.side):base
          drop={targetStackId,zone:'tabs',previewIds}
          const targetElement=target?Array.from(tabStrip.querySelectorAll<HTMLElement>(':scope > [data-reorder-id]')).find(item=>item.dataset.reorderId===target.id)||null:null
          showPointerDropIndicator(targetElement||tabStrip,targetElement?`insert-${target?.side}`:'tab-bar')
          return
        }
        const box=paneElement.getBoundingClientRect(),x=(pointer.clientX-box.left)/box.width,y=(pointer.clientY-box.top)/box.height
        const edges:[PaneDropZone,number][]=[['left',x],['right',1-x],['top',y],['bottom',1-y]]
        const nearest=edges.sort((a,b)=>a[1]-b[1])[0]
        const zone:PaneDropZone=nearest[1]<.2?nearest[0]:'tabs'
        drop={targetStackId,zone,previewIds:[...targetPane.children.map(child=>child.id),childId]}
        showPointerDropIndicator(zone==='tabs'?(tabStrip||paneElement):paneElement,zone==='tabs'?'tab-bar':`split-${zone}`)
      },
      ()=>{
        const current=drop
        if(!current)return
        const latest=layoutValues.current[projectId]||activeLayout
        const targetPane=paneStacks(latest).find(pane=>pane.id===current.targetStackId)
        if(!targetPane)return
        const exists=leaves(latest).some(leaf=>leaf.id===childId)
        let next:PaneLayout
        if(current.zone!=='tabs'){
          const direction=current.zone==='left'||current.zone==='right'?'horizontal':'vertical'
          const position=current.zone==='left'||current.zone==='top'?'before':'after'
          next=exists?moveLeafToSplit(latest,'note',childId,current.targetStackId,direction,position):splitView(latest,targetPane.active_child_id,fileLeaf,direction,position)
        }else{
          const base=exists?moveLeafToStack(latest,'note',childId,current.targetStackId):addLeafToStack(latest,current.targetStackId,fileLeaf)
          next=reorderStack(base,current.targetStackId,current.previewIds)
        }
        setFocusedViewId(childId)
        void updateLayout(projectId,next)
      },
      ()=>{showPointerDropIndicator(null)},
    )
  }

  const renderPaneNode = (node: PaneNode|PaneLeaf, path = '', insideStack = false, paneVisible = true): ComponentChildren => {
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
      return <section data-pane-stack-id={node.id} data-tutorial="workspace-pane" class={`pane-stack ${focusedPane?'focused-pane':''} ${paneDropClass}`} onPointerDown={()=>setFocusedViewId(activeChild.id)}><div data-tutorial="tab-strip" class="stack-tabs" role="tablist" aria-label="Workspace tabs" onWheel={scrollStripByWheel}>
        {node.children.map(child=>{
          const activate=()=>{if(suppressDragClickRef.current===`tab:${child.id}`){suppressDragClickRef.current=null;return}setFocusedViewId(child.id);if(child.kind==='terminal')setActiveId(child.id);if(child.id!==activeChild.id)void updateLayout(projectId,activateStackChild(activeLayout,node.id,child.id))}
          const dragClass=dragStackTab?.overId===child.id&&dragStackTab.side?`drag-over drop-${dragStackTab.side}`:''
          const dragStyle={order:previewIds.indexOf(child.id)}
          if(child.kind==='preview'){
            const preview=previews[child.id]
            const label=preview?.url||child.id
            return <div key={child.id} data-reorder-id={child.id} data-tutorial="tab-drag-source" style={dragStyle} class={`stack-tab-shell draggable-tab ${dragStackTab?.childId===child.id?'dragging':''} ${dragClass}`} onPointerDown={event=>beginWorkspaceTabDrag(event,{stackId:node.id,childId:child.id,kind:child.kind,targetStackId:node.id,zone:'tabs',previewIds:node.children.map(item=>item.id),overId:null,side:null},label)}><button role="tab" aria-label={`${label} preview tab`} title={label} aria-selected={child.id===activeChild.id} class={`tab-main preview-tab ${child.id===activeChild.id?'active':''}`} onClick={activate} onContextMenu={event=>{event.preventDefault();event.stopPropagation();activate();openTabMenu(child,label,event.clientX,event.clientY)}}><span class="preview-tab-glyph" aria-hidden="true">◱</span>{preview?`:${preview.port}`:child.id}</button>{closeTab(child,label)}</div>
          }
          if(child.kind==='note'){
            const label=noteTabLabel(child.id)
            return <div key={child.id} data-reorder-id={child.id} data-tutorial="tab-drag-source" style={dragStyle} class={`stack-tab-shell draggable-tab resource-tab ${dragStackTab?.childId===child.id?'dragging':''} ${dragClass}`} onPointerDown={event=>beginWorkspaceTabDrag(event,{stackId:node.id,childId:child.id,kind:child.kind,targetStackId:node.id,zone:'tabs',previewIds:node.children.map(item=>item.id),overId:null,side:null},label)}><button role="tab" aria-label={`${label} resource tab`} title={label} aria-selected={child.id===activeChild.id} class={`tab-main ${child.id===activeChild.id?'active':''}`} onClick={activate} onContextMenu={event=>{event.preventDefault();event.stopPropagation();activate();openTabMenu(child,label,event.clientX,event.clientY)}}><span class="preview-tab-glyph" aria-hidden="true">◇</span>{label}</button>{closeTab(child,label)}</div>
          }
          if(child.kind==='history'){
            const label='History'
            return <div key={child.id} data-reorder-id={child.id} data-tutorial="tab-drag-source" style={dragStyle} class={`stack-tab-shell draggable-tab resource-tab ${dragStackTab?.childId===child.id?'dragging':''} ${dragClass}`} onPointerDown={event=>beginWorkspaceTabDrag(event,{stackId:node.id,childId:child.id,kind:child.kind,targetStackId:node.id,zone:'tabs',previewIds:node.children.map(item=>item.id),overId:null,side:null},label)}><button role="tab" aria-label="History tab" title="Search session history" aria-selected={child.id===activeChild.id} class={`tab-main ${child.id===activeChild.id?'active':''}`} onClick={activate} onContextMenu={event=>{event.preventDefault();event.stopPropagation();activate();openTabMenu(child,label,event.clientX,event.clientY)}}><span class="preview-tab-glyph" aria-hidden="true">◷</span>{label}</button>{closeTab(child,label)}</div>
          }
          if(child.kind==='queue'){
            const label=queueTabLabel(child.id)
            return <div key={child.id} data-reorder-id={child.id} data-tutorial="tab-drag-source" style={dragStyle} class={`stack-tab-shell draggable-tab resource-tab ${dragStackTab?.childId===child.id?'dragging':''} ${dragClass}`} onPointerDown={event=>beginWorkspaceTabDrag(event,{stackId:node.id,childId:child.id,kind:child.kind,targetStackId:node.id,zone:'tabs',previewIds:node.children.map(item=>item.id),overId:null,side:null},label)}><button role="tab" aria-label={`${label} queue tab`} title={label} aria-selected={child.id===activeChild.id} class={`tab-main ${child.id===activeChild.id?'active':''}`} onClick={activate} onContextMenu={event=>{event.preventDefault();event.stopPropagation();activate();openTabMenu(child,label,event.clientX,event.clientY)}}><span class="preview-tab-glyph" aria-hidden="true">⇥</span>{label}</button>{closeTab(child,label)}</div>
          }
          const session=sessions.find(item=>item.id===child.id)
          // sessionName, not session.name: the generated title is the whole point of
          // titling, and a tab strip showing `claude-15036b` while the sidebar shows
          // the real name is the surface where you actually need to tell panes apart.
          const label=session?sessionName(session):child.id
          return <div key={child.id} data-reorder-id={child.id} data-tutorial="tab-drag-source" style={dragStyle} class={`stack-tab-shell draggable-tab ${session?.pending?'pending-terminal-tab':''} ${dragStackTab?.childId===child.id?'dragging':''} ${dragClass}`} onPointerDown={event=>{if(!session?.pending)beginWorkspaceTabDrag(event,{stackId:node.id,childId:child.id,kind:child.kind,targetStackId:node.id,zone:'tabs',previewIds:node.children.map(item=>item.id),overId:null,side:null},label)}}><button role="tab" aria-label={`${label} session tab`} aria-selected={child.id===activeChild.id} class={`tab-main ${child.id===activeChild.id?'active':''} ${session?.state||''}`} onClick={activate} onContextMenu={event=>{event.preventDefault();event.stopPropagation();activate();if(session&&!session.pending)openSessionMenu(session,event.clientX,event.clientY,'tab')}}><span class={stateDotClass(session?.state)}/>{activityGlyphs(session)}{label}</button>{closeTab(child,label,session)}</div>
        })}
      </div><div class="stack-active">{node.children
        .filter(child=>child.id===activeChild.id||(child.kind==='terminal'&&warmTerminalIds.includes(child.id)))
        .map(child=>renderPaneNode(child,`${path}t`,true,child.id===activeChild.id))}</div></section>
    }
    if(node.kind==='note'){
      const identity=parseNoteResourceId(node.id)
      if(!identity||!activeProject)return <section class="workspace-leaf-placeholder note-unavailable"><strong>resource unavailable</strong><span>{node.id}</span><button onClick={()=>void removeWorkspaceNote(projectId,node.id)}>close tab</button></section>
      return <ProjectResource key={`${activeProject.id}:${node.id}`} project={activeProject} resource={identity} onOpenFile={path=>{if(suppressDragClickRef.current===`file:${noteResourceId('file',path)}`){suppressDragClickRef.current=null;return}openProjectFile(activeProject,path)}} onFileDragStart={(path,event)=>beginFileTabDrag(event,path)} onSendToAgent={setSendToAgent}/>
    }
    if(node.kind==='history')return <section class="workspace-leaf-placeholder"><strong>History moved</strong><span>Session history is now a full-screen overlay.</span><button onClick={()=>{setHistoryOpen(true);void updateLayout(projectId,removeLeaf(layoutValues.current[projectId]||emptyLayout(),'history',node.id))}}>Open History</button></section>
    if(node.kind==='queue'){
      const targetSessionId=queueLeafSessionId(node.id)
      if(!targetSessionId)return <section class="workspace-leaf-placeholder"><strong>queue unavailable</strong><span>{node.id}</span></section>
      // The pop-out rendering: target pinned to the leaf rather than following focus, and
      // no pop-out button of its own. Everything else is the same panel the drawer shows.
      return <QueuePane key={node.id} sessionId={targetSessionId} sessions={sessions} onSelectSession={sid=>{const owner=sessions.find(item=>item.id===sid);if(owner)void selectSession(owner)}} onFocusTarget={sid=>void openQueueForSession(sid)}/>
    }
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
    const agentVoice=isAgent(session)
    const voiceMode=voiceStatus?.enabled&&agentVoice?effectiveVoiceMode(session):'off'
    const voiceAvailable=!!voiceStatus?.enabled&&agentVoice
    const conversationAvailable=!!voiceStatus?.stt_enabled&&agentVoice
    const voiceStripVisible=voiceAvailable&&voiceMode!=='off'
    // Read-aloud and conversation controls live in the pane header beside note/proc, and the
    // playback strip (seek, clip nav, generate) expands as a row directly beneath it. They
    // used to lead the bottom command rail, but that rail is a horizontal scroller the user
    // pages through to reach terminal keys, so the voice chips were both in the way there and
    // easy to lose off-screen. Grouped in the header they have a fixed home; the group is its
    // own scroller so a long chip set can never push the pane tools out of the bar.
    const paneVoice=agentVoice&&voiceStatus?<>
      {voiceAvailable&&<button class={`voice-chip ${voiceMode}`} aria-label={`Read aloud mode for ${sessionName(session)}: ${voiceModeLabel(voiceMode)}. Click to change.`} title={`Read aloud: ${voiceModeLabel(voiceMode)} · click to cycle off → on demand → auto`} onClick={()=>cycleVoiceMode(session)}>tts:{voiceMode==='on_demand'?'tap':voiceMode}</button>}
      {!voiceAvailable&&<button class="voice-chip mobile-voice-action" aria-label="Set up read aloud" title="Read aloud is disabled · open Voice settings" onClick={()=>openSettings('Voice')}>tts:setup</button>}
      {conversationAvailable&&<ConversationControl session={session} status={voiceStatus} onSession={updateSession}/>}
      {!conversationAvailable&&<button class="conversation-chip mobile-voice-action" aria-label="Set up hands-free conversation" title="Microphone conversation is disabled · open Voice settings" onClick={()=>openSettings('Voice')}>talk:setup</button>}
      {/* speak / verbatim-summary / autoplay used to be repeated here for touch, because the
          playback strip was buried at the bottom of the pane. The strip is now the row
          immediately below this bar, so those chips would sit a few pixels from the controls
          they duplicate — they render only when the strip renders. The strip owns them. */}
      {(voiceAvailable||conversationAvailable)&&<button class="mobile-voice-action voice-settings" title="Open all Voice settings" onClick={()=>openSettings('Voice')}>audio…</button>}
    </>:null
    const voiceStripNode=voiceStripVisible&&voiceStatus?<VoicePlayer session={session} status={voiceStatus} mode={voiceMode as 'on_demand'|'auto'} onSession={updateSession} />:null
    // `key` matters here in a way it does not for a single-child stack: a stack now
    // renders its active pane *and* its warm siblings, so without a stable identity a
    // reorder would rebuild terminals rather than move them.
    const terminalPane=<section key={id} class={`terminal-pane ${activeId === id ? 'focused' : ''} ${paneVisible ? '' : 'pane-warm'}`} aria-hidden={paneVisible?undefined:'true'} onPointerDown={() => {setActiveId(id);setFocusedViewId(id)}}>
      <div class="pane-bar" onContextMenu={openPaneMenu} onDblClick={() => setZoomedId(current => current === id ? null : id)}>
        {/* A stale transcript is the one fault that looks like a healthy session: the state
            below is being read off a conversation this PTY may no longer be running (an
            unfollowable /clear or /new). Marked visibly, not just in the tooltip, because the
            whole failure mode is that nothing looks wrong. */}
        <div><span class={`pane-state ${session.state}${session.observation_stale_since?' observation-stale':''}`} title={[session.observation_stale_since&&'observation stale: the followed transcript may no longer be this session’s conversation',session.parser_diagnostic,session.delivery_readiness&&`delivery::${session.delivery_readiness.state} (${session.delivery_readiness.reason}) · authorized::no`].filter(Boolean).join('\n')}>{sessionStatus(session)}{session.observation_stale_since?' · stale':''}</span></div>
        <div class={`pane-path ${cwdIsLive?'live':'last-known'}`} title={cwdIsLive?`live cwd · ${displayedCwd}`:`last known (spawn) cwd · ${displayedCwd}`}>{cwdIsLive?'':<span>last-known::</span>}{displayedCwd}</div>
        <div class="pane-voice">{paneVoice}</div>
        <div class="pane-tools"><button class={`pane-tool-label note-chip ${noteChipState(session)}`} aria-label={noteChipLabel(session)} title={noteChipTitle(session)} onClick={()=>openSessionNotes(session)}>note{noteChipState(session)==='empty'?'':'•'}</button>{isAgent(session)&&<button class={`pane-tool-label queue-chip${(queueSummary[session.id]?.pending||0)>0?' has-pending':''}`} aria-label={`Open the prompt queue for ${sessionName(session)}`} title={`Prompt queue · ${queueSummary[session.id]?.pending||0} pending`} onClick={()=>void openQueueForSession(session.id)}>queue{(queueSummary[session.id]?.pending||0)>0?`:${queueSummary[session.id].pending}`:''}</button>}<button class="pane-tool-label" aria-label={`Inspect processes for ${sessionName(session)}`} title="Processes and previews" onClick={() => {setActiveId(session.id);openProcessViewer(session)}}>proc</button><button aria-label={`More actions for ${sessionName(session)}`} title="Session actions" onClick={event=>{const rect=event.currentTarget.getBoundingClientRect();openPaneMenu({clientX:rect.right,clientY:rect.bottom,stopPropagation:()=>event.stopPropagation()})}}>⋯</button></div>
      </div>
      {voiceStripNode}
      <TerminalPane session={session} onState={updateSession} startupOrigin={startupOrigins.current[session.id]} onStartupTiming={(milestone,elapsedMs)=>recordClientStartupTiming(session.id,milestone,elapsedMs)} broadcast={broadcast} keybindings={keybindings} scrollback={xtermScrollback} rendererPreference={terminalRenderer} windowsPty={windowsPty} mobileInput={mobileInput} visible={paneVisible} onConfigureRail={()=>openSettings('Command rail')} onBranch={()=>void branchSession(session)} />
    </section>
    if(insideStack)return terminalPane
    return <section data-tutorial="workspace-pane" class="pane-stack singleton-stack"><div data-tutorial="tab-strip" class="stack-tabs" role="tablist" aria-label="Terminal tabs">
      <div data-tutorial="tab-drag-source" class="stack-tab-shell"><button role="tab" aria-label={`${sessionName(session)} session tab`} aria-selected="true" class={`tab-main active ${session.state}`} onClick={()=>setActiveId(id)} onContextMenu={event=>{event.preventDefault();event.stopPropagation();setActiveId(id);openSessionMenu(session,event.clientX,event.clientY,'tab')}}><span class={stateDotClass(session.state)}/>{activityGlyphs(session)}{sessionName(session)}</button><button class={`tab-close ${confirmKillId===id?'confirming':''}`} aria-label={`${confirmKillId===id?'Confirm close':'Close'} terminal: ${sessionName(session)}`} title={confirmKillId===id?'Confirm kill terminal':'Close and kill terminal'} onClick={event=>{event.stopPropagation();requestKill(session)}}>{confirmKillId===id?'✓':'×'}</button></div>
    </div><div class="stack-active">{terminalPane}</div></section>
  }

  const workspaceNoteIds=(targetProject:string)=>leaves(resolveLayout(layoutMap[targetProject],projects.find(item=>item.id===targetProject)?.layout),'note').map(leaf=>leaf.id)

  // Resource tabs cover notes, the Files browser, and opened files; only the last has a path
  // worth copying, so the menu's copy group is gated on this resolving.
  const fileMenuTarget=(menu:{resourceId:string;projectId:string})=>{
    const identity=parseNoteResourceId(menu.resourceId)
    if(identity?.kind!=='file')return null
    const root=projects.find(item=>item.id===menu.projectId)?.root||''
    return { relative:identity.id, absolute:absoluteProjectPath(root,identity.id) }
  }
  // The tab menu has no recovery panel of its own, so a refused write says where the payload
  // still is (the Files tree offers the manual copy) rather than failing silently.
  const copyFileClipboard=async(menu:{resourceId:string;projectId:string},form:'absolute'|'relative'|'contents')=>{
    const target=fileMenuTarget(menu)
    if(!target)return
    setNoteMenu(null)
    try{
      let payload=form==='absolute'?target.absolute:target.relative
      if(form==='contents'){
        const file=await api<{status:string;text?:string}>('GET',`/api/projects/${menu.projectId}/file?path=${encodeURIComponent(target.relative)}`)
        if(file.status==='too-large'){setError(`${target.relative} is above the 2 MiB read limit and cannot be copied.`);return}
        if(file.status==='binary'||file.text===undefined){setError(`${target.relative} is not text, so there is nothing to copy.`);return}
        payload=truncateForClipboard(file.text,target.relative).text
      }
      if(!await copyPreparedText(payload)){
        setError('Clipboard write was blocked by the browser. Right-click the file in the Files tab to copy it manually.')
      }
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }
  // The chip reports whether this terminal has anything written down, which is
  // most of its value: `open` when its note is the focused tab, `written` when
  // the note holds text, `empty` otherwise.
  const noteChipState=(session:Session):'open'|'written'|'empty'=>{
    const resourceId=noteResourceId('session-note',session.note_id||session.id)
    const layout=resolveLayout(layoutMap[session.project_id],projects.find(item=>item.id===session.project_id)?.layout)
    if(stackForView(layout,resourceId)?.active_child_id===resourceId)return 'open'
    return session.note_exists?'written':'empty'
  }
  const noteChipTitle=(session:Session)=>({
    open:'Session note · open · click to focus it',
    written:'Session note · has content · click to open it beside this terminal',
    empty:'Session note · empty · click to start one beside this terminal',
  })[noteChipState(session)]
  const noteChipLabel=(session:Session)=>`${noteChipState(session)==='empty'?'Start':'Open'} session note for ${sessionName(session)}`
  const sidebarNoteRow=(resourceId:string,targetProject:string)=>{
    const identity=parseNoteResourceId(resourceId)
    if(!identity)return null
    const noteLayout=resolveLayout(layoutMap[targetProject],projects.find(item=>item.id===targetProject)?.layout)
    const workspaceOpen=workspaceNoteIds(targetProject).includes(resourceId)
    const selected=targetProject===projectId&&stackForView(noteLayout,resourceId)?.active_child_id===resourceId
    const label=identity.kind==='note'?'Project note':identity.kind==='session-note'?'Session note':identity.id.split('/').pop()||'File'
    return <button data-tutorial={identity.kind==='note'?'project-note':undefined} class={`sidebar-note-row ${selected?'active':''} ${workspaceOpen?'open':''}`} title={`${label} · opens in the focused pane`} onContextMenu={event=>{event.preventDefault();event.stopPropagation();openNoteContext(resourceId,targetProject,event.clientX,event.clientY)}} onClick={event=>{event.stopPropagation();showNoteResource(resourceId,targetProject);setSidebarOpen(false)}}>
      <span class="note-branch" aria-hidden="true">└</span><span class="note-copy"><strong>{label}</strong></span>
    </button>
  }
  // Note and Files share one nested row (single guideline, divider between them) so each
  // project costs one line instead of two. They now open different kinds of surface: the
  // note is a pane tab (a document you edit), Files is the drawer's navigator tab. Each
  // chip therefore reads its "active" state from where its surface actually lives.
  const sidebarProjectResourceRow=(targetProject:string)=>{
    const noteLayout=resolveLayout(layoutMap[targetProject],projects.find(item=>item.id===targetProject)?.layout)
    const noteId=noteResourceId('note',targetProject)
    const noteOpen=workspaceNoteIds(targetProject).includes(noteId)
    const noteSelected=targetProject===projectId&&stackForView(noteLayout,noteId)?.active_child_id===noteId
    const filesShowing=clipboardOpen&&drawerTabId==='files'&&targetProject===projectId
    return <div class="sidebar-note-row note-files-row">
      <span class="note-branch" aria-hidden="true">└</span>
      <span class="note-resource-group">
        <button data-tutorial="project-note" class={`note-resource-chip ${noteSelected?'active':''} ${noteOpen?'open':''}`} title="Project note · opens in the focused pane" onContextMenu={event=>{event.preventDefault();event.stopPropagation();openNoteContext(noteId,targetProject,event.clientX,event.clientY)}} onClick={event=>{event.stopPropagation();showNoteResource(noteId,targetProject);setSidebarOpen(false)}}><strong>Note</strong></button>
        <span class="note-resource-divider" aria-hidden="true"></span>
        <button class={`note-resource-chip ${filesShowing?'active open':''}`} title="Files · browse this project in the side panel" onClick={event=>{event.stopPropagation();const project=projects.find(item=>item.id===targetProject);if(project)openProjectFiles(project);setSidebarOpen(false)}}><strong>Files</strong></button>
      </span>
    </div>
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
  const sessionRow=(session:Session)=>{
    const spawnedPreviews=Object.values(previews).filter(item=>item.session_id===session.id)
    // Only servers earn a sidebar row. A session's other children are bookkeeping
    // noise and stay in the process inspector. Ones already open as a preview are
    // rendered by that row instead.
    const spawnedServers=detectedServers(sessionProcesses[session.id]||[])
      .filter(server=>!spawnedPreviews.some(preview=>preview.port===server.port))
    const sessionNoteId=noteResourceId('session-note',session.note_id||session.id)
    const showSessionNote=!!session.note_exists||workspaceNoteIds(session.project_id).includes(sessionNoteId)
    // Sidebar attention tier for agent rows. The focused row keeps its own
    // `.active` treatment; a row visible in another split pane reads as
    // "viewing" (on screen, not focused); an off-screen row with unseen output
    // is "unread"; an off-screen, already-seen row is "read" and recedes.
    const agent=isAgent(session)
    const attention=!agent||activeId===session.id?''
      :visibleSessionIds.includes(session.id)?'viewing'
      :isUnread(session,seenActivity)?'unread':'read'
    return <div class="session-entry"><button data-sidebar-session-id={session.id} data-sidebar-project-id={session.project_id} class={`session-row ${activeId === session.id ? 'active' : ''} ${agent?'agent':''} ${attention} ${session.state} ${session.pending?'pending-terminal-row':''}`} onPointerDown={event=>{if(!session.pending){beginLongPress(event,(x,y)=>openSessionMenu(session,x,y,'sidebar'));beginSessionPointerDrag(event,session)}}} onPointerUp={cancelLongPress} onPointerCancel={cancelLongPress} onPointerMove={cancelLongPress} onContextMenu={event => { event.preventDefault();if(!session.pending)openSessionMenu(session,event.clientX,event.clientY,'sidebar') }} onClick={() => {if(suppressDragClickRef.current===`session:${session.id}`){suppressDragClickRef.current=null;return}void selectSession(session)}}>
      <span class={sessionDotClass(session)} />
      <span class="session-copy"><strong>{isAgent(session) && <span class={`agent-prefix ${session.backend}`} title={session.backend}>{providerGlyph(session.backend as ProviderName)}</span>}{sessionName(session)}{session.broadcast&&<span class="broadcast-flag" title="In the broadcast set — keystrokes mirror here while broadcast input is on">⇶</span>}{activityGlyphs(session)}</strong><small class={isAgent(session) ? `agent-status ${session.state}` : ''}>{sessionStatus(session)}</small></span>
      {!session.pending&&<span class="row-actions" onPointerDown={event=>event.stopPropagation()} onClick={event => event.stopPropagation()}><button class={confirmKillId === session.id ? 'confirming' : ''} title={confirmKillId === session.id ? (isEndedSession(session) ? 'Confirm remove' : 'Confirm kill') : (isEndedSession(session) ? 'Remove from sidebar' : 'Kill')} onClick={() => runNamedCommand(`session.requestKill(${session.id})`)}>{confirmKillId === session.id ? '✓' : '×'}</button></span>}
    </button>{showSessionNote&&sidebarNoteRow(sessionNoteId,session.project_id)}{spawnedPreviews.map(preview=>sidebarPreviewRow(preview,session))}{spawnedServers.map(server=>sidebarServerRow(server,session))}</div>
  }
  const sidebarNode=(node:PaneNode|PaneLeaf|null|undefined):ComponentChildren=>{
    if(!node)return null
    if(node.type==='leaf'){
      if(node.kind!=='terminal')return null
      const session=sessions.find(item=>item.id===node.id)
      return session?sessionRow(session):null
    }
    const nodeLayout:PaneLayout={...emptyLayout(),root:node}
    const ids=terminalIds(nodeLayout)
    const branches=(node.type==='stack'?node.children:[node.first,node.second]).filter(child=>child.type==='leaf'?child.kind==='terminal':terminalIds({...emptyLayout(),root:child}).length>0)
    if(branches.length===0)return null
    if(branches.length===1)return sidebarNode(branches[0])
    const label=node.type==='stack'?'Sessions sharing one tabbed pane':`${node.direction} split branches`
    const owner=sessions.find(item=>ids.includes(item.id))
    return <section data-sidebar-stack-id={node.type==='stack'?node.id:undefined} data-sidebar-project-id={node.type==='stack'?owner?.project_id:undefined} class={`layout-cluster ${node.type} ${node.type==='split'?node.direction:''}`} role="group" aria-label={label}>
      {branches.map((child,index)=><div class={`layout-branch ${index===0?'first':''} ${index===branches.length-1?'last':''}`} key={child.id}>{sidebarNode(child)}</div>)}
    </section>
  }

  const noteTabLabel=(resourceId:string)=>{
    const identity=parseNoteResourceId(resourceId)
    if(identity?.kind==='note')return 'Project note'
    if(identity?.kind==='session-note'){
      const owner=sessions.find(session=>(session.note_id||session.id)===identity.id)
      return owner?`Note · ${sessionName(owner)}`:'Session note'
    }
    return identity?.id.split('/').pop()||'File'
  }
  const projectPreviewIds=dragProject?.previewIds||displayProjectIds

  const mobileProjection=mobileWorkspaceProjection(activeLayout,focusedViewId,activeId,mobileTabOrder[projectId])
  // Reordering stores the full displayed order for this project, so the saved
  // permutation self-heals: ids the save predated are already merged in at their
  // layout-relative position by the projection before it is written back.
  const moveMobileTabSlot=(leafId:string,direction:'left'|'right')=>{
    if(!projectId)return
    const next=moveMobileTab(mobileProjection.tabs.map(tab=>tab.id),leafId,direction)
    if(!next)return
    // The active project is always retained, so a write that lands before the
    // project list has loaded cannot prune away the order it just saved.
    const updated=pruneMobileTabOrder({...mobileTabOrder,[projectId]:next},[projectId,...projects.map(project=>project.id)])
    setMobileTabOrder(updated)
    localStorage.setItem(MOBILE_TAB_ORDER_KEY,serializeMobileTabOrder(updated))
    navigator.vibrate?.(10)
  }
  // Left/right only: the mobile rail is flat, so this permutes the rail rather
  // than moving a leaf between panes the way the desktop 'Move tab' row does.
  const mobileMoveRow=(leafId:string)=>{
    const ids=mobileProjection.tabs.map(tab=>tab.id)
    const index=ids.indexOf(leafId)
    return <div class="context-direction-row"><span>Move tab:</span><div>
      <button aria-label="Move tab left" title="Move this tab one slot left (this device only)" disabled={index<=0} onClick={()=>moveMobileTabSlot(leafId,'left')}>◀</button>
      <button aria-label="Move tab right" title="Move this tab one slot right (this device only)" disabled={index<0||index>=ids.length-1} onClick={()=>moveMobileTabSlot(leafId,'right')}>▶</button>
    </div></div>
  }
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
    const label=leaf.kind==='terminal'?(session?sessionName(session):leaf.id):leaf.kind==='preview'?preview?.url||leaf.id:leaf.kind==='history'?'History':leaf.kind==='queue'?queueTabLabel(leaf.id):noteTabLabel(leaf.id)
    const visibleLabel=mobileTabLabel(leaf)
    const glyph=leaf.kind==='terminal'?<><span class={sessionDotClass(session)}/>{activityGlyphs(session)}</>:<span class="preview-tab-glyph" aria-hidden="true">{leaf.kind==='preview'?'◱':leaf.kind==='history'?'◷':leaf.kind==='queue'?'⇥':'◇'}</span>
    // Mobile tabs carry no close button: it ate label width and was a mis-tap
    // hazard next to tab activation. Closing/killing lives in the long-press
    // menu (session menu for terminals, tab menu for resources), which is also
    // where the confirm step already is.
    const openMobileTabMenu=(x:number,y:number)=>{
      activateMobileTab(leaf)
      if(session&&!session.pending)openSessionMenu(session,x,y,'mobile')
      else if(leaf.kind!=='terminal')openTabMenu(leaf,label,x,y,'mobile')
    }
    return <div key={`${leaf.kind}:${leaf.id}`} class="stack-tab-shell mobile-unified-tab">
      <button role="tab" aria-label={`${label} ${leaf.kind} tab`} title={label} aria-selected={selected} class={`tab-main ${selected?'active':''} ${session?.state||''}`} onClick={()=>activateMobileTab(leaf)} onPointerDown={event=>beginLongPress(event,openMobileTabMenu)} onPointerUp={cancelLongPress} onPointerCancel={cancelLongPress} onPointerMove={cancelLongPress} onContextMenu={event=>{event.preventDefault();event.stopPropagation();cancelLongPress();openMobileTabMenu(event.clientX,event.clientY)}}>{glyph}{visibleLabel}</button>
    </div>
  }
  // With no new-tab button left in the rail, an empty projection would render a
  // bare strip; drop the row entirely and let the empty stage own the section.
  const mobileUnifiedWorkspace=<section data-tutorial="workspace-pane" class={`pane-stack mobile-unified-workspace ${mobileProjection.tabs.length?'':'no-tabs'}`}>
    {mobileProjection.tabs.length>0&&<div data-tutorial="tab-strip" class="stack-tabs mobile-unified-tabs" role="tablist" aria-label="All Project tabs" onWheel={scrollStripByWheel}>
      {mobileProjection.tabs.map(mobileTab)}
    </div>}
    <div class="stack-active mobile-unified-active">{mobileProjection.selected?renderPaneNode(mobileProjection.selected,'mobile',true):<div class="empty-stage"><div class="hero-terminal" aria-hidden="true">&gt;_</div><h1>Your Project workspace.</h1><p>Run a terminal, or open the Project note, a file, or a preview to begin. Files and notes live in the side panel.</p></div>}</div>
  </section>

  return <div class="app-shell">
    <div class="sr-only" role="status" aria-live="polite" aria-atomic="true">{attention ? `${attention} agent${attention === 1 ? '' : 's'} awaiting attention` : 'No agents awaiting attention'}</div>
    <div class="mobile-toolbar">
      {/* A glyph, not `:nav`: at half width no word survives, and pinning a font size to make
          one fit would ignore the user's UI-scale setting, which this button is subject to via
          an `!important` rule. One character stays legible at every scale. */}
      <button class="nav-toggle mobile-nav-toggle" aria-label="Open navigation sidebar" title="Navigation" onClick={() => setSidebarOpen(value => !value)}>≡</button>
      {/* Quota sits beside nav, at the start of the bar: it is glanced at constantly and the
          far edge is where a thumb reaching for Run lands. Run moves to that far edge for the
          same reason — it is the destructive-ish action here (it spawns), so it wants the
          corner, not the middle. */}
      <AccountSwitcher variant="compact" onManage={()=>openSettings('Accounts')}/>
      {/* The toolbar title is the Project menu's trigger. Single tap opens it on
          touch: a long-press was the only way in, and holding a text node is what
          raised the selection UI. Long-press/right-click still work for parity.
          `data-menu-toggle` keeps the document dismiss handler off this button,
          or it would close the menu on pointer-down and the click would reopen
          it, so a second tap could never collapse what the first opened. */}
      <button class="mobile-project-name" type="button" data-menu-toggle aria-haspopup="menu" aria-expanded={!!projectMenu} disabled={!activeProject} title={activeProject?`${activeProject.name} — Project actions`:'No Project selected'} onClick={event=>{if(!activeProject)return;if(projectMenu){setProjectMenu(null);return}const rect=event.currentTarget.getBoundingClientRect();openProjectMenuAt(activeProject,rect.left,rect.bottom+4)}} onContextMenu={event=>{if(!activeProject)return;event.preventDefault();if(projectMenu){setProjectMenu(null);return}openProjectMenuAt(activeProject,event.clientX,event.clientY)}}>{activeProject?.name||'No Project'}</button>
      {/* Tap opens the launcher; hold repeats the last launch straight away,
          which is the common case once a Project settles on one backend. The
          long-press fires while the finger is down, so the click it is followed
          by must be swallowed or the menu would open on top of the new tab. */}
      <button data-tutorial="run" class="mobile-run-trigger" disabled={!activeProject} title={activeProject?`Run in ${activeProject.name} — hold to start ${lastLaunchBackend()} directly`:'No Project selected'}
        onPointerDown={event=>{runHeldRef.current=false;beginLongPress(event,()=>{
          if(!activeProject)return
          runHeldRef.current=true
          const backend=lastLaunchBackend()
          showMobileHud(`starting ${backend}…`)
          void spawnTerminal(activeProject.id,false,undefined,undefined,'after',backend)
        })}}
        onPointerUp={cancelLongPress} onPointerCancel={cancelLongPress} onPointerMove={cancelLongPress}
        onClick={event=>{
          if(runHeldRef.current){runHeldRef.current=false;return}
          if(activeProject)toggleRunMenu(activeProject,event.currentTarget)
        }}>▶ Run</button>
    </div>
    {mobileHud&&<div class="mobile-hud" role="status" aria-live="polite">{mobileHud}</div>}

    <ContinuityBanner />
    {broadcast && <div class="broadcast-banner"><strong>Broadcast input is on</strong><span>Keystrokes mirror to sessions in the broadcast set.</span><button onClick={() => setBroadcast(false)}>Stop broadcasting</button></div>}

    <div class={`workspace ${sidebarCollapsed?'sidebar-collapsed':''} ${clipboardOpen&&!mobileWorkspace?'drawer-open':''}`} style={{'--sidebar-width':`${sidebarWidth}px`,'--drawer-width':`${drawerWidth}px`} as JSX.CSSProperties}>
      <header class="app-topbar">
        <div class="app-identity"><strong>swe_mux</strong><button class="sidebar-collapse" aria-label={sidebarCollapsed?'Expand sidebar':'Collapse sidebar'} title={sidebarCollapsed?'Expand sidebar':'Collapse sidebar'} onClick={toggleSidebar}>{sidebarCollapsed?'»':'«'}</button><span class="daemon-ok" title="daemon::connected" aria-label="daemon connected"><i aria-hidden="true" /></span>{activeProject&&<button data-tutorial="run" class="project-run-header" aria-haspopup="menu" aria-expanded={runMenu?.project.id===activeProject.id} title={`Run in ${activeProject.name}`} onClick={event=>toggleRunMenu(activeProject,event.currentTarget)}>▶ Run</button>}</div>
      </header>
      <aside class={`sidebar ${sidebarOpen ? 'open' : ''}`} onContextMenu={event=>{const target=event.target as Element;if(target.closest('.sidebar-heading,.project-row,.session-row,.sidebar-note-row,.sidebar-footer'))return;event.preventDefault();setContextMenu(null);setProjectMenu(null);setNoteMenu(null);setSortMenu(null);setMainMenuOpen(false);setSidebarMenu({x:event.clientX,y:event.clientY})}}>
        <div class="project-tree">
          {visibleProjects.length===0&&<button data-tutorial="empty-project" class="empty-project-cta" onClick={()=>openProjectsManager()}><strong>{projects.length?'No Projects shown':'Create your first Project'}</strong><small>{projects.length?'Open Projects to show or add an active Project.':'Open Projects to add a canonical folder.'}</small></button>}
          {projectBuckets.map(bucket=>{
            const peerIds=bucket.items.map(item=>item.id)
            const sortMode=bucketSortMode(sidebarOrder,bucket.id)
            const bucketCollapsed=isBucketCollapsed(sidebarOrder,bucket.id)
            // Folding a section hides whichever Project holds the waiting agent, so
            // the header has to answer for all of them: a count for how much is live,
            // and the strongest state as a dot, because a bare count cannot say that
            // something in here is waiting on you.
            const bucketStatus=bucketCollapsed?projectSetRailStatus(sessions,peerIds,seenActivity):null
            return <section class={`sidebar-project-bucket ${bucketCollapsed?'collapsed':''}`} key={bucket.id} data-reorder-id={bucket.id}>
            {/* The header is the section's drag handle and its collapse toggle: press
                and move to reorder, press and release to fold. The drag suppresses the
                click it ends with, which is the same disambiguation a Project row uses.
                Its buttons stop the pointer so pressing one never starts either. */}
            <header title={`${bucket.name} — click to ${bucketCollapsed?'expand':'collapse'}, drag to reorder`} onPointerDown={event=>beginBucketPointerDrag(event,bucket.id,bucket.name)} onClick={()=>{if(suppressDragClickRef.current===`bucket:${bucket.id}`){suppressDragClickRef.current=null;return}setSidebarOrder(toggleBucketCollapsed(sidebarOrder,bucket.id))}}>
              <span class="bucket-chevron" aria-hidden="true">{bucketCollapsed?'▸':'▾'}</span><span>{bucket.name}</span>
              {bucketStatus&&bucketStatus.liveCount>0&&<span class={`bucket-collapsed-badge activity-${bucketStatus.activity} ${bucketStatus.unread?'unread':''}`} title={`${bucketStatus.liveCount} live session${bucketStatus.liveCount===1?'':'s'} · ${projectRailActivityLabel[bucketStatus.activity]}${bucketStatus.unread?' · unread output':''}`}><i aria-hidden="true"/>{bucketStatus.liveCount}</span>}
              <button class={`bucket-sort ${sortMode==='custom'?'':'active'}`} aria-haspopup="menu" aria-expanded={sortMenu?.bucketId===bucket.id} aria-label={`Sort projects in ${bucket.name}`} title={`Sort projects in ${bucket.name} — ${projectSortLabel(sortMode)}`} onPointerDown={event=>event.stopPropagation()} onClick={event=>{event.stopPropagation();if(sortMenu?.bucketId===bucket.id){setSortMenu(null);return}const rect=event.currentTarget.getBoundingClientRect();openSortMenu(bucket.id,bucket.name,rect.left,rect.bottom+4)}}>⇅</button>
              {bucket.id!==UNGROUPED_BUCKET_ID&&<><button title="Rename group" onPointerDown={event=>event.stopPropagation()} onClick={()=>{const group=projectGroups.find(item=>item.id===bucket.id);if(group)setGroupEdit({id:group.id,name:group.name})}}>✎</button><button title="Remove group (projects become ungrouped)" onPointerDown={event=>event.stopPropagation()} onClick={()=>{const group=projectGroups.find(item=>item.id===bucket.id);if(group)void deleteGroup(group)}}>×</button></>}</header>{!bucketCollapsed&&bucket.items.map(project => {
            const children = sessions
              .filter(session => session.project_id === project.id)
              .sort((a,b)=>a.created_at-b.created_at||a.id.localeCompare(b.id))
            const projectLayout=resolveLayout(layoutMap[project.id],project.layout)
            const projectPaneIds=terminalIds(projectLayout)
            const unpanedChildren=children.filter(session=>!projectPaneIds.includes(session.id))
            const dropClass=dragProject?.overId===project.id&&dragProject.side?`project-drop-target drop-${dragProject.side}`:''
            const collapsed=collapsedProjects.has(project.id)
            const liveCount=children.filter(session=>!session.pending&&!['exited','crashed'].includes(session.state)).length
            return <section key={project.id} data-reorder-id={project.id} style={{order:projectPreviewIds.indexOf(project.id)}} class={`project-group ${project.id === projectId ? 'active' : ''} ${collapsed?'collapsed':''} ${dropClass}`}>
              <div class={`project-row draggable-project ${dragProject?.id===project.id?'dragging':''}`} title="Drag to reorder Project" onPointerDown={event=>{beginLongPress(event,(x,y)=>setProjectMenu({project,x,y}));beginProjectPointerDrag(event,project,bucket.id,peerIds)}} onPointerUp={cancelLongPress} onPointerCancel={cancelLongPress} onPointerMove={cancelLongPress} onContextMenu={event => { event.preventDefault(); setProjectMenu({ project, x: event.clientX, y: event.clientY }) }} onClick={()=>{if(suppressDragClickRef.current===`project:${project.id}`){suppressDragClickRef.current=null;return}selectProject(project.id)}}>
                <button class="project-chevron project-collapse-toggle" aria-expanded={!collapsed} aria-label={`${collapsed?'Expand':'Collapse'} ${project.name}`} title={collapsed?'Expand project':'Collapse project'} onPointerDown={event=>event.stopPropagation()} onClick={event=>{event.stopPropagation();toggleProjectCollapsed(project.id)}}>{collapsed?'▸':'▾'}</button><strong class="project-name-cell"><span class="project-name-text">{project.name}</span>{collapsed&&liveCount>0&&<span class="project-collapsed-badge" title={`${liveCount} active session${liveCount===1?'':'s'}`}>{liveCount}</span>}</strong><button data-tutorial="project-run" class="project-row-run" title={`Run in ${project.name}`} aria-label={`Run in ${project.name}`} onPointerDown={event=>event.stopPropagation()} onClick={event=>{event.stopPropagation();openRunMenu(project,event.currentTarget)}}>▶</button>
              </div>
              {!collapsed&&<div data-tutorial="project-resources" class="project-note-list">{sidebarProjectResourceRow(project.id)}</div>}
              {!collapsed&&<div class="session-list">
                {sidebarNode(projectLayout.root)}
                {unpanedChildren.map(session=>sessionRow(session))}
              </div>}
            </section>
          })}</section>})}
        </div>
        <div class="sidebar-status">
          <AccountSwitcher onManage={()=>openSettings('Accounts')}/>
          <ResourceUsageSummary snapshot={processFleet} sessions={sessions} projects={projects} onRefresh={()=>void loadProcesses()} onOpenFleet={()=>openProcessViewer()}/>
        </div>
        <div class="sidebar-footer"><button data-tutorial="menu" class="menu-trigger" onClick={() => setMainMenuOpen(value => !value)}><span>:</span> menu</button><button data-tutorial="projects" class="project-trigger" onClick={()=>openProjectsManager()}><span>◇</span> projects</button></div>
      </aside>
      {/* The collapsed strip keeps the sidebar's own controls reachable rather
          than forcing an expand round-trip for menu, projects, or status. */}
      {sidebarCollapsed&&<nav class="sidebar-rail" aria-label="Sidebar shortcuts">
        <div class="rail-projects" aria-label="Projects">
          {displayProjects.map(project=>{const status=projectRailStatus(sessions,project.id,seenActivity);const selected=project.id===projectId;const readLabel=status.agentCount?(status.unread?' · unread output':' · read'):'';const countLabel=status.liveCount?` · ${status.liveCount} live session${status.liveCount===1?'':'s'}`:'';return <button
            key={project.id}
            data-sidebar-project-id={project.id}
            class={`rail-project activity-${status.activity} ${status.unread?'unread':'read'} ${selected?'active':''}`}
            aria-label={`Open ${project.name} · ${projectRailActivityLabel[status.activity]}${readLabel}`}
            aria-current={selected?'page':undefined}
            title={`${project.name} · ${projectRailActivityLabel[status.activity]}${readLabel}${countLabel}`}
            onContextMenu={event=>{event.preventDefault();setProjectMenu({project,x:event.clientX,y:event.clientY})}}
            onClick={()=>selectProject(project.id)}
          ><span>{projectInitials(project.name)}</span><i aria-hidden="true" /></button>})}
        </div>
        {/* Status above, actions at the very bottom, mirroring the expanded
            sidebar where menu and projects are the last rows. */}
        <div class="rail-status">
          <ResourceUsageSummary compact snapshot={processFleet} sessions={sessions} projects={projects} onRefresh={()=>void loadProcesses()} onOpenFleet={()=>openProcessViewer()}/>
          <AccountSwitcher variant="rail" placement="up" onManage={()=>openSettings('Accounts')}/>
        </div>
        {/* Run stays reachable while the sidebar is collapsed: the top-bar Run
            has no room in the 40px rail column, and tab strips no longer carry
            a new-tab button. */}
        <button data-tutorial="run" class="rail-button rail-run" aria-haspopup="menu" aria-expanded={!!activeProject&&runMenu?.project.id===activeProject.id} aria-label={activeProject?`Run in ${activeProject.name}`:'Run'} title={activeProject?`Run in ${activeProject.name}`:'Run'} disabled={!activeProject} onClick={event=>activeProject&&toggleRunMenu(activeProject,event.currentTarget)}>▶</button>
        <button class="rail-button" aria-label="Open swe-mux menu" title="Menu" onClick={()=>setMainMenuOpen(value=>!value)}>:</button>
        <button class="rail-button" aria-label="Manage projects" title="Projects" onClick={()=>openProjectsManager()}>◇</button>
      </nav>}
      <div class="sidebar-resizer" role="separator" tabindex={0} aria-label="Resize sidebar" aria-orientation="vertical" aria-valuemin={190} aria-valuemax={480} aria-valuenow={Math.round(sidebarWidth)} title="Drag to resize · arrow keys adjust · double-click to reset" onPointerDown={beginSidebarResize} onDblClick={()=>persistSidebarWidth(254)} onKeyDown={event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();persistSidebarWidth(event.key==='Home'?190:event.key==='End'?480:sidebarWidth+(event.key==='ArrowLeft'?-10:10))}} />

      {/* The utility drawer is a workspace grid child so the desktop rendering can
          be an in-flow column: the pane tree shrinks rather than being covered.
          Mobile takes the same element out of flow (position:fixed) and adds a
          scrim, which is why both renderings share one component. */}
      {clipboardOpen&&<UtilityDrawer
        tab={drawerTabId}
        // The drag ghost's pointer-up also fires a click on the tab it started from, which
        // would switch to the tab the user was only moving.
        onTab={tab=>{
          if(suppressDragClickRef.current===`drawer-tab:${tab}`){suppressDragClickRef.current=null;return}
          showDrawerTab(tab)
        }}
        onClose={()=>setClipboardOpen(false)}
        mobile={mobileWorkspace}
        session={active||null}
        project={activeProject}
        backend={active?.backend}
        notifications={notificationData}
        onNotificationsChanged={()=>void loadNotifications()}
        unread={notificationUnread}
        onOpenSession={sessionId=>{const session=sessions.find(item=>item.id===sessionId);if(!session){setError('That session is no longer live.');return}void selectSession(session)}}
        onOpenSettings={section=>{if(mobileWorkspace)setClipboardOpen(false);openSettings(section)}}
        onManagePrompts={()=>{if(mobileWorkspace)setClipboardOpen(false);setPromptLibraryOpen(true)}}
        onOpenFile={path=>{
          // The drag ghost's pointer-up also fires a click on the row it started from.
          if(suppressDragClickRef.current===`file:${noteResourceId('file',path)}`){suppressDragClickRef.current=null;return}
          if(activeProject)openProjectFile(activeProject,path)
        }}
        // Desktop only: the drawer is an in-flow column there, so a file row can be dragged
        // onto a visible pane. On mobile it is an overlay with nothing to drop onto.
        onFileDragStart={mobileWorkspace?undefined:(path,event)=>beginFileTabDrag(event,path)}
        onSendToAgent={request=>{if(mobileWorkspace)setClipboardOpen(false);setSendToAgent(request)}}
        queueOpenRequest={queueOpen.token?queueOpen:undefined}
        onQueueOpenAsTab={sessionId=>void openQueueTab(sessionId)}
        queuePending={queuePendingTotal}
        notesAllProjects={notesAllProjects}
        onNotesAllProjects={setNotesAllProjects}
        focusedNote={active?.note_exists?{projectId:active.project_id,noteId:active.note_id||active.id,label:sessionName(active)}:null}
        onOpenProjectNote={targetProject=>{const project=projects.find(item=>item.id===targetProject);if(project)openProjectNotes(project)}}
        onOpenSessionNote={openBrowsedSessionNote}
        tabs={orderedDrawerTabs}
        onTabDragStart={beginDrawerTabDrag}
        draggingTab={dragDrawerTab}
        promptPreselect={promptPreselect}
        onResize={beginDrawerResize}
        onInsert={text=>{
          const target=insertIntoFocusedSurface(text,activeId)
          if(target==='none')setError('Focus a terminal or note before inserting text.')
          return target
        }}
        // A prompt template is written for an agent to read: routing one into whichever
        // note or file pane happened to be focused last edits that document instead.
        onInsertPrompt={text=>{
          const target=insertIntoFocusedSurface(text,activeId,{terminalsOnly:true})
          if(target==='none')setError('Focus an agent session before inserting a prompt.')
          return target
        }}
        sessions={sessions}
        onSendPrompt={deliverToAgent}
      />}
      {/* Desktop only: the always-visible strip that makes these surfaces
          discoverable without a menu or a chord. Mobile reaches the same tabs
          through the drawer's own tab strip after a two-finger swipe. */}
      {!mobileWorkspace&&<nav class="utility-rail" aria-label="Side panel">
        {orderedDrawerTabs.map(tab=>{
          const Icon=DRAWER_TAB_ICONS[tab.id]
          return <button
            key={tab.id}
            data-reorder-id={tab.id}
            class={`${clipboardOpen&&drawerTabId===tab.id?'active':''} ${dragDrawerTab===tab.id?'dragging':''}`}
            aria-pressed={clipboardOpen&&drawerTabId===tab.id}
            aria-label={tab.title}
            title={`${tab.title} · drag to rearrange`}
            onPointerDown={event=>beginDrawerTabDrag(event,tab.id)}
            onClick={()=>{
              if(suppressDragClickRef.current===`drawer-tab:${tab.id}`){suppressDragClickRef.current=null;return}
              showDrawerTab(tab.id)
            }}
          ><Icon/>{tab.id==='notifications'&&notificationUnread>0&&<i class="drawer-badge">{notificationUnread>99?'99+':notificationUnread}</i>}{tab.id==='queue'&&queuePendingTotal>0&&<i class="drawer-badge queue-badge">{queuePendingTotal>99?'99+':queuePendingTotal}</i>}</button>
        })}
      </nav>}

      <main data-tutorial="workspace" class="main-stage" onContextMenu={event => { if (!activeLayout.root) { event.preventDefault(); setEmptyMenu({ x: event.clientX, y: event.clientY }) } }}>
        <div class="project-workspace unified-workspace">
          <div class="terminal-workspace">
            {mobileWorkspace?mobileUnifiedWorkspace:(activeLayout.root||focusedOutsideLayout) ? <div class="pane-tree">{renderPaneNode(zoomedId ? stackForView(activeLayout,zoomedId)||activeLayout.root! : focusedOutsideLayout&&activeId ? paneStack([terminalLeaf(activeId)],activeId) : activeLayout.root!)}</div> : <div class="pane-tree"><section data-tutorial="workspace-pane" class="pane-stack empty-workspace-pane">
              <div class="stack-active empty-stage"><div class="hero-terminal" aria-hidden="true">&gt;_</div><h1>Your Project workspace.</h1><p>Run a terminal, or open the Project note, a file, or a preview to begin. Files and notes live in the side panel.</p></div>
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

    {runMenu&&<ProjectRunMenu project={runMenu.project} anchor={{x:runMenu.x,y:runMenu.y}} onClose={()=>{runMenuClosedAt.current=Date.now();setRunMenu(null)}} onLaunch={backend=>{const target=runMenu.project.id;setRunMenu(null);void spawnTerminal(target,false,undefined,undefined,'after',backend)}} onCustom={()=>{const target=runMenu.project.id;setRunMenu(null);openLauncher(target)}} onSessions={items=>void attachActionSessions(runMenu.project.id,items)} onError={setError}/>}

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

    {contextMenu && <div ref={el=>fitMenuInViewport(el)} class="context-menu" role="menu" aria-label={`Session actions for ${sessionName(contextMenu.session)}`} style={{ left: clampContextMenuLeft(contextMenu.x, innerWidth), top: Math.max(4, Math.min(contextMenu.y, innerHeight - 520)) }}>
      <div class="context-title"><span class={sessionDotClass(contextMenu.session)} /><strong>{sessionName(contextMenu.session)}</strong></div>
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
      {/* No session menu carries pane geometry — not the sidebar row, not the tab, not
          the pane's own ⋯. Split / stack / dissolve answer "how is the workspace laid
          out", which is not the question any of these menus is opened to answer, and
          five direction rows pushed Rename and Kill past the fold in all three. The
          layout routes are drag (direct manipulation) and the command palette, where
          session.openSplit*, pane.split*, session.groupStack, stack.dissolve and
          session.customSplit stay searchable and bindable. `Move tab` is the exception:
          it reorders the strip you are looking at rather than reshaping the tree. */}
      {(contextMenu.source==='tab'||contextMenu.source==='pane')&&directionRow('Move tab:',option=>void moveTabDirection(terminalLeaf(contextMenu.session.id),contextMenu.session.project_id,option.id),direction=>!!paneNeighborIds(activeLayout,contextMenu.session.id)[direction])}
      {contextMenu.source==='mobile'&&mobileMoveRow(contextMenu.session.id)}
      <button onClick={() => runNamedCommand('processes.open')}>Processes and previews…</button>
      <button onClick={()=>runNamedCommand('pane.stackNew')}>New terminal as tab</button>
      {voiceStatus?.enabled&&isAgent(contextMenu.session)&&<>
        <div class="context-subtitle">READ ALOUD</div>
        {(['off','on_demand','auto'] as VoiceMode[]).map(mode=><button key={mode} onClick={()=>{void setVoiceMode(contextMenu.session,mode);setContextMenu(null)}}>{effectiveVoiceMode(contextMenu.session)===mode?'✓ ':''}{mode==='off'?'Off':mode==='on_demand'?'On demand':'Auto on reply'}</button>)}
        <button onClick={()=>{const target=contextMenu.session;setContextMenu(null);void speakLastReply(target)}}>Speak last reply now</button>
      </>}
      <div class="context-rule" />
      <button onClick={() => runNamedCommand('session.broadcastMembership')}>{contextMenu.session.broadcast ? 'Remove from broadcast' : 'Add to broadcast'}</button>
      <button class="danger" onClick={() => runNamedCommand('session.killImmediate')}>{isEndedSession(contextMenu.session) ? 'Remove from sidebar' : 'Kill session'}</button>
    </div>}

    {projectMenu && <div ref={el=>fitMenuInViewport(el)} class="context-menu" role="menu" aria-label={`Project actions for ${projectMenu.project.name}`} style={{ left: clampContextMenuLeft(projectMenu.x, innerWidth), top: Math.max(4, Math.min(projectMenu.y, innerHeight - 470)) }}>
      <div class="context-title"><strong>{projectMenu.project.name}</strong></div>
      {/* Starting work belongs to the Run button (sidebar header, every Project row,
          and the mobile rail), which offers the same backends plus Project tasks —
          duplicating it here left two doors to one action. */}
      {/* The same surfaces the app menu opens globally, prefiltered to this Project. */}
      <div class="context-subtitle">BROWSE THIS PROJECT</div>
      <button onClick={() => runNamedCommand('history.openProject')}>Session history…</button>
      <button onClick={()=>{const target=projectMenu.project;setProjectMenu(null);openNotesBrowser(target)}}>Session notes…</button>
      <button onClick={() => runNamedCommand('processes.project')}>Processes…</button>
      <button onClick={() => runNamedCommand('prompts.openProject')}>Prompt library…</button>
      <button onClick={() => runNamedCommand('observations.open')}>Observation inbox…</button>
      <button onClick={() => runNamedCommand('mailbox.open')}>Mailbox…</button>
      <button onClick={()=>{openProjectFiles(projectMenu.project);setProjectMenu(null)}}>Browse files…</button>
      <div class="context-subtitle">PROJECT</div>
      <button onClick={() => runNamedCommand('project.reveal')}>Reveal in Explorer</button>
      <button onClick={()=>{const target=projectMenu.project;setProjectMenu(null);toggleProjectCollapsed(target.id)}}>{collapsedProjects.has(projectMenu.project.id)?'Expand in sidebar':'Collapse in sidebar'}</button>
      {confirmHideId!==projectMenu.project.id&&<button onClick={()=>{const target=projectMenu.project;if(canHideProject(openWorkFor(target))){setProjectMenu(null);void hideProject(target)}else setConfirmHideId(target.id)}}>Hide from sidebar</button>}
      {confirmHideId===projectMenu.project.id&&<>
        <div class="context-subtitle">CLOSE OPEN WORK TO HIDE</div>
        <div class="context-note">{describeOpenWork(openWorkFor(projectMenu.project))||'No live work'} still attached. Hiding would strand it off-screen.</div>
        <button class="danger" onClick={()=>{const target=projectMenu.project;setProjectMenu(null);setConfirmHideId(null);void closeWorkAndHideProject(target)}}>Close it &amp; hide</button>
        <button onClick={()=>setConfirmHideId(null)}>Cancel</button>
      </>}
      <label class="context-select">Group<select value={projectMenu.project.group_id||''} onChange={event=>{const target=projectMenu.project;const group_id=event.currentTarget.value||null;void api<Project>('PATCH',`/api/projects/${target.id}`,{group_id}).then(updated=>setProjects(items=>items.map(item=>item.id===updated.id?updated:item)));setProjectMenu(null)}}><option value="">Ungrouped</option>{projectGroups.map(group=><option value={group.id}>{group.name}</option>)}</select></label>
      <button onClick={() => runNamedCommand('project.rename')}>Rename project</button>
      <button disabled={!commands.find(item=>item.id==='project.moveUp')?.available} onClick={()=>runNamedCommand('project.moveUp')}>Move Project up</button>
      <button disabled={!commands.find(item=>item.id==='project.moveDown')?.available} onClick={()=>runNamedCommand('project.moveDown')}>Move Project down</button>
      <button onClick={() => runNamedCommand('project.settings')}>Project settings…</button>
      {confirmProjectDeleteId !== projectMenu.project.id && <button class="danger" disabled={sessions.some(session=>session.project_id===projectMenu.project.id)} onClick={() => runNamedCommand('project.delete')}>Delete project…</button>}
      {confirmProjectDeleteId === projectMenu.project.id && <>
        <div class="context-subtitle">DELETE PROJECT REGISTRATION</div>
        <button class="danger" onClick={() => { const target = projectMenu.project; setProjectMenu(null); void deleteProject(target) }}>Confirm delete</button>
        <button onClick={() => setConfirmProjectDeleteId(null)}>Cancel</button>
      </>}
    </div>}

    {sidebarMenu&&<div ref={el=>fitMenuInViewport(el)} class="context-menu" role="menu" aria-label="Sidebar actions" style={{left:clampContextMenuLeft(sidebarMenu.x,innerWidth),top:Math.max(4,Math.min(sidebarMenu.y,innerHeight-300))}}>
      <div class="context-title"><strong>PROJECTS</strong></div>
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('project.add')}}>Add project…</button>
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('project.create')}}>Manage projects…</button>
      <button onClick={()=>{setSidebarMenu(null);setGroupEdit({name:''})}}>Create group</button>
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('history.open')}}>Session history</button>
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('notes.browse')}}>Session notes…</button>
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('processes.all')}}>Process fleet…</button>
      <div class="context-rule" />
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('settings.open')}}>All Settings…</button>
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('daemon.reload')}}>Reload daemon (keep sessions)</button>
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('app.redeploy')}}>Rebuild + redeploy app (keep sessions)</button>
    </div>}

    {/* Per bucket, not global: Groups are how unlike things get separated, so a
        hand-arranged shortlist and an alphabetical pile can coexist. */}
    {sortMenu&&<div ref={el=>fitMenuInViewport(el)} class="context-menu" role="menu" aria-label={`Sort projects in ${sortMenu.bucketName}`} style={{left:clampContextMenuLeft(sortMenu.x,innerWidth),top:Math.max(4,Math.min(sortMenu.y,innerHeight-330))}}>
      <div class="context-title"><strong>SORT · {sortMenu.bucketName}</strong></div>
      {PROJECT_SORT_OPTIONS.map(option=>{
        const active=bucketSortMode(sidebarOrder,sortMenu.bucketId)===option.id
        return <button key={option.id} title={option.hint} aria-checked={active} role="menuitemradio" onClick={()=>{setSidebarOrder(setBucketSortMode(sidebarOrder,sortMenu.bucketId,option.id));setSortMenu(null)}}>{active?'✓ ':''}{option.label}</button>
      })}
      <div class="context-rule"/>
      {/* One level up, from the same control: a header's ⇅ already means "how is
          this list ordered", and the sidebar has no section-level header to hang a
          separate control on. Behind a MenuGroup so the common case stays flat, and
          carrying its current mode in the label since the section order has no
          always-visible indicator of its own. */}
      <MenuGroup id="sections" label={`Sort Groups · ${sectionSortLabel(sidebarOrder.sectionSort)}`} openId={menuGroup} onOpenChange={setMenuGroup} hint="Order the Groups and PROJECTS themselves">
        {SECTION_SORT_OPTIONS.map(option=>{
          const active=sidebarOrder.sectionSort===option.id
          return <button key={option.id} title={option.hint} aria-checked={active} role="menuitemradio" onClick={()=>{setSidebarOrder({...sidebarOrder,sectionSort:option.id});setSortMenu(null)}}>{active?'✓ ':''}{option.label}</button>
        })}
      </MenuGroup>
      {/* "a header" rather than "a Group header": the PROJECTS remainder drags and
          sorts with the Groups but is not one, and naming only Groups would read as
          excluding it. */}
      <div class="context-note">Dragging a Project or a header into place puts that level back on Manual order.</div>
    </div>}

    {noteMenu&&<div ref={el=>fitMenuInViewport(el)} class="context-menu" role="menu" aria-label="Resource view actions" style={{left:clampContextMenuLeft(noteMenu.x,innerWidth),top:Math.max(4,Math.min(noteMenu.y,innerHeight-220))}}>
      <div class="context-title"><strong>{noteTabLabel(noteMenu.resourceId)}</strong></div>
      <button onClick={()=>void placeNoteResourceInFocusedPane(noteMenu.resourceId,noteMenu.projectId)}>{mobileWorkspace?'Open tab':'Open in focused pane'}</button>
      {!mobileWorkspace&&directionRow('Open in split:',option=>void splitNoteResource(noteMenu.resourceId,noteMenu.projectId,option.direction,option.position))}
      {/* Same copy actions the Files tree offers, so a file already open as a tab does not
          have to be found again in the browser just to get its path. */}
      {fileMenuTarget(noteMenu)&&<><div class="context-rule"/>
        <button title={fileMenuTarget(noteMenu)!.absolute} onClick={()=>void copyFileClipboard(noteMenu,'absolute')}>Copy full path</button>
        <button title={fileMenuTarget(noteMenu)!.relative} onClick={()=>void copyFileClipboard(noteMenu,'relative')}>Copy path from project root</button>
        <button title={`Copy the file's text, capped at ${FILE_COPY_MAX_LINES.toLocaleString()} lines`} onClick={()=>void copyFileClipboard(noteMenu,'contents')}>Copy file contents</button>
      </>}
      {workspaceNoteIds(noteMenu.projectId).includes(noteMenu.resourceId)&&<><div class="context-rule"/><button onClick={()=>{const target=noteMenu;setNoteMenu(null);void removeWorkspaceNote(target.projectId,target.resourceId)}}>Close resource tab</button></>}
    </div>}

    {tabMenu&&<div ref={el=>fitMenuInViewport(el)} class="context-menu tab-context-menu" role="menu" aria-label={`Tab actions for ${tabMenu.label}`} style={{left:clampContextMenuLeft(tabMenu.x,innerWidth),top:Math.max(4,Math.min(tabMenu.y,innerHeight-300))}}>
      <div class="context-title"><strong>{tabMenu.label}</strong></div>
      {/* Same rule as the session menu above: no context menu reshapes the pane tree.
          Splitting a resource tab out is a drag; the keyboard route is the palette. */}
      {tabMenu.source==='tab'&&directionRow('Move tab:',option=>void moveTabDirection(tabMenu.leaf,tabMenu.projectId,option.id),direction=>{const current=resolveLayout(layoutMap[tabMenu.projectId],projects.find(project=>project.id===tabMenu.projectId)?.layout);return !!paneNeighborIds(current,tabMenu.leaf.id)[direction]})}
      {tabMenu.source==='mobile'&&mobileMoveRow(tabMenu.leaf.id)}
      {tabMenu.source==='mobile'&&<button onClick={()=>{const target=tabMenu;setTabMenu(null);void spawnTerminal(target.projectId,'stack',undefined,target.leaf.id)}}>New terminal as tab</button>}
      <div class="context-rule"/><button onClick={()=>{
        const target=tabMenu;setTabMenu(null)
        // Mobile has no per-tab close button, so this is the only close path
        // there; route it through closeMobileTab to keep neighbour focus.
        if(target.source==='mobile'){closeMobileTab(target.leaf);return}
        const current=resolveLayout(layoutMap[target.projectId],projects.find(project=>project.id===target.projectId)?.layout)
        void updateLayout(target.projectId,removeLeaf(current,target.leaf.kind,target.leaf.id))
      }}>Close tab</button>
    </div>}

    {emptyMenu && <div ref={el=>fitMenuInViewport(el)} class="context-menu" role="menu" style={{ left: clampContextMenuLeft(emptyMenu.x, innerWidth), top: Math.min(emptyMenu.y, innerHeight - 280) }}>
      <div class="context-title"><strong>EMPTY PANE</strong></div>
      <button role="menuitem" onClick={() => { setEmptyMenu(null); void spawnTerminal() }}>New terminal</button>
      <button role="menuitem" onClick={() => { setEmptyMenu(null); openLauncher() }}>New terminal custom…</button>
      {unpanned.length > 0 && <div class="context-subtitle">ATTACH LIVE SESSION</div>}
      {unpanned.map(session => <button role="menuitem" onClick={() => runNamedCommand(`session.attach(${session.id})`)}><span class={stateDotClass(session.state)} />{sessionName(session)}</button>)}
    </div>}

    {mainMenuOpen && <div data-tutorial="main-menu" class="context-menu main-menu" role="menu" aria-label="swe-mux menu">
      <div class="context-title"><strong>swe-mux menu</strong></div>
      {/* The lead block needs no heading: these are the app's general-purpose
          surfaces, opened across everything. Right-clicking a Project row opens the
          Project-scoped ones prefiltered to it. Anything that acts on one Project
          lives there, not here. */}
      <button onClick={() => runNamedCommand('history.open')}>Session history</button>
      <button onClick={() => runNamedCommand('notes.browse')}>Session notes…</button>
      <button onClick={() => runNamedCommand('processes.all')}>Process fleet…</button>
      <button onClick={()=>runNamedCommand('prompts.open')}>Prompt library…</button>
      <button onClick={()=>runNamedCommand('clipboard.open')}>Clipboard history…</button>
      <button onClick={() => runNamedCommand('usage.open')}>Usage analytics…</button>
      <button onClick={() => runNamedCommand('notifications.open')}>Notifications{notificationUnread?` [${notificationUnread} new]`:''}</button>
      <div class="context-subtitle">CONFIGURATION</div>
      {/* Adding a Project lives in the registry and the empty-sidebar menu; this
          menu keeps only the surfaces that act across the whole app. */}
      <button onClick={() => runNamedCommand('project.create')}>Manage projects…</button>
      <button onClick={() => runNamedCommand('hooks.open')}>Automation…</button>
      <MenuGroup id="maintenance" label="Maintenance" openId={menuGroup} onOpenChange={setMenuGroup} hint="Reload and rebuild without reaping live sessions">
        <button onClick={() => runNamedCommand('daemon.reload')}>Reload daemon (keep sessions)</button>
        <button onClick={() => runNamedCommand('app.redeploy')}>Rebuild + redeploy app (keep sessions)</button>
        <button onClick={() => runNamedCommand('ui.reload')}>Reload UI</button>
      </MenuGroup>
      <div class="context-rule"/>
      {/* Broadcast is an app-wide input mode, not a Project action: membership is
          per-session, set from a session's own context menu. */}
      <button onClick={() => { setMainMenuOpen(false); runNamedCommand('broadcast.toggle') }}>{broadcast ? 'Stop broadcasting input' : 'Start broadcasting input'}</button>
      <button onClick={() => { setMainMenuOpen(false); runNamedCommand('palette.open') }}>Command palette <span class="menu-hint">ctrl alt p</span></button>
      <div class="context-rule"/>
      <button onClick={() => runNamedCommand('settings.open')}>All Settings…</button>
    </div>}

    {sidebarOpen && <button class="sidebar-scrim" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />}

    {renameTarget && <div class="modal-layer" onMouseDown={event => event.target === event.currentTarget && setRenameTarget(null)}>
      <form class="modal rename-modal" onSubmit={event => { event.preventDefault(); void submitRename() }}>
        <div class="modal-heading"><div><span>RENAME::{renameTarget.kind.toUpperCase()}</span><h2>{renameTarget.kind === 'session' ? sessionName(renameTarget.session) : renameTarget.project.name}</h2></div><button type="button" aria-label="Close rename" onClick={() => setRenameTarget(null)}>×</button></div>
        <label>name<input ref={renameInput} value={renameValue} onInput={event => setRenameValue(event.currentTarget.value)} autofocus /></label>
        <div class="modal-footer"><span>enter::save · esc::cancel</span><button type="button" onClick={() => setRenameTarget(null)}>Cancel</button><button class="primary" type="submit" disabled={!renameValue.trim()}>Rename</button></div>
      </form>
    </div>}

    {daemonReloading&&<div class="modal-layer daemon-reload-layer" role="alertdialog" aria-modal="true" aria-label="Daemon reloading"><div class="modal daemon-reload-modal"><h2>Reloading daemon…</h2><p>Live sessions are preserved by the PTY supervisor. This page reloads automatically once the daemon is back.</p></div></div>}
    {redeploying&&<div class="modal-layer daemon-reload-layer" role="alertdialog" aria-modal="true" aria-label="App redeploying"><div class="modal daemon-reload-modal"><h2>Rebuilding + redeploying app…</h2><p>The new bundle builds while the current app keeps running, then the app restarts around your live sessions. This takes a few minutes; the page reloads automatically. A failed build leaves the current app untouched.</p></div></div>}
    {redeployConfirmOpen&&<div class="modal-layer daemon-reload-layer" role="alertdialog" aria-modal="true" aria-label="Confirm redeploy" onClick={()=>setRedeployConfirmOpen(false)}><div class="modal daemon-reload-modal" onClick={event=>event.stopPropagation()}><h2>Rebuild + redeploy app?</h2><p>Rebuilds the frozen desktop app from source and restarts it around your live sessions (a few minutes). A failed build leaves the current app running.</p><div class="modal-actions"><button onClick={()=>void startRedeploy()}>Rebuild + redeploy</button><button onClick={()=>setRedeployConfirmOpen(false)}>Cancel</button></div></div></div>}

    {historyOpen&&<HistoryBrowser projects={orderedProjects} initialProjectId={historyScope} onClose={()=>setHistoryOpen(false)} onResume={resumeHistoryEntry} onSessionNote={openHistorySessionNote} onSecondOpinion={previewSecondOpinion} onHandoff={openHandoff}/>}

    {projectsManagerOpen&&<ProjectsManager projects={projects} groups={projectGroups} sessions={sessions} profiles={profiles} initialProjectId={projectsManagerFocus?.projectId} initialTab={projectsManagerFocus?.tab} onClose={()=>{setProjectsManagerOpen(false);setProjectsManagerFocus(null)}} onAdd={()=>void createProject()} onAddGroup={()=>setGroupEdit({name:''})} onOpen={project=>{setProjectId(project.id);setProjectsManagerOpen(false)}} onNote={project=>{setProjectsManagerOpen(false);openProjectNotes(project)}} onFiles={project=>{setProjectsManagerOpen(false);openProjectFiles(project)}} onPatch={patchManagedProject} onDelete={deleteProject}/>}

    {projectCreateOpen&&<div class="modal-layer project-registry-dialog-layer" onMouseDown={event=>event.target===event.currentTarget&&setProjectCreateOpen(false)}>
      <form data-tutorial="project-form" class="modal" onSubmit={event=>{event.preventDefault();void submitProject()}}>
        <div class="modal-heading"><div><span>PROJECT::CREATE</span><h2>Add a project</h2></div><button type="button" onClick={()=>setProjectCreateOpen(false)}>×</button></div>
        {/* Registering a folder that exists and making a new one are the same
            registration with a different first step, so they are two modes of one
            form rather than two dialogs that would each need their own setup list. */}
        <div class="project-create-mode" role="tablist" aria-label="How to add this project">
          <button type="button" role="tab" aria-selected={projectCreate.mode==='existing'} class={projectCreate.mode==='existing'?'active':''} onClick={()=>setProjectCreate(value=>({...value,mode:'existing'}))}>Existing folder</button>
          <button type="button" role="tab" aria-selected={projectCreate.mode==='new'} class={projectCreate.mode==='new'?'active':''} onClick={()=>setProjectCreate(value=>({...value,mode:'new'}))}>Create new folder</button>
        </div>
        <label>Name<input value={projectCreate.name} onInput={event=>setProjectCreate(value=>({...value,name:event.currentTarget.value}))} autofocus /></label>
        {projectCreate.mode==='existing'
          ?<label>Folder<div class="project-folder-field"><input value={projectCreate.root} onInput={event=>setProjectCreate(value=>({...value,root:event.currentTarget.value}))} placeholder="D:\\projects\\horizon" /><button type="button" onClick={()=>setFolderPickerOpen(true)}>Browse…</button></div></label>
          :<>
            <label>Parent folder<div class="project-folder-field"><input value={projectCreate.parent} onInput={event=>setProjectCreate(value=>({...value,parent:event.currentTarget.value}))} placeholder="D:\\projects" /><button type="button" onClick={()=>setFolderPickerOpen(true)}>Browse…</button></div></label>
            <label>New folder name<input value={projectCreateFolder(projectCreate)} onInput={event=>setProjectCreate(value=>({...value,folder:event.currentTarget.value,folderTouched:true}))} placeholder={suggestFolderName(projectCreate.name)||'horizon'} /></label>
          </>}
        <label>Group<select value={projectCreate.group_id} onChange={event=>setProjectCreate(value=>({...value,group_id:event.currentTarget.value}))}><option value="">Ungrouped</option>{projectGroups.map(group=><option value={group.id}>{group.name}</option>)}</select></label>
        {!!initScripts.length&&<details class="project-init-scripts">
          <summary>Setup commands · {projectCreate.scripts.length} selected</summary>
          {initScripts.map(script=><label class="check" key={script.id}>
            <input type="checkbox" checked={projectCreate.scripts.includes(script.id)} onChange={event=>setProjectCreate(value=>({...value,scripts:toggleInitScript(value.scripts,script.id,event.currentTarget.checked)}))} />
            <span><strong>{script.label}</strong><code>{script.command}</code></span>
          </label>)}
          <p class="modal-note">Each selected command opens its own terminal in the new Project, started in this order. They are your own commands from Settings → General, never anything read out of the folder.</p>
        </details>}
        <p class="modal-note">{projectCreate.mode==='new'?'The parent folder must already exist; only the new folder is created. ':''}Creating the project initializes .swe-mux in <code>{projectCreateRoot(projectCreate)||'the chosen folder'}</code>. Every session starts at this exact root.</p>
        <div class="modal-footer"><button type="button" onClick={()=>setProjectCreateOpen(false)}>Cancel</button><button class="primary" type="submit" disabled={!projectCreateReady(projectCreate)}>Create project</button></div>
      </form>
    </div>}
    {folderPickerOpen&&<DirectoryPicker initialPath={projectCreate.mode==='new'?projectCreate.parent:projectCreate.root} onCancel={()=>setFolderPickerOpen(false)} onSelect={root=>{setProjectCreate(value=>value.mode==='new'?{...value,parent:root}:{...value,root,name:folderNameFromPath(root)});setFolderPickerOpen(false)}} />}

    {groupEdit&&<div class="modal-layer project-registry-dialog-layer" onMouseDown={event=>event.target===event.currentTarget&&setGroupEdit(null)}><form class="modal rename-modal" onSubmit={event=>{event.preventDefault();void submitGroup()}}><div class="modal-heading"><div><span>GROUP::{groupEdit.id?'RENAME':'CREATE'}</span><h2>Sidebar group</h2></div><button type="button" onClick={()=>setGroupEdit(null)}>×</button></div><label>Name<input value={groupEdit.name} onInput={event=>setGroupEdit(current=>current?{...current,name:event.currentTarget.value}:current)} autofocus /></label><p class="modal-note">Groups only organize the sidebar. They never affect sessions, panes, or project data.</p><div class="modal-footer"><button type="button" onClick={()=>setGroupEdit(null)}>Cancel</button><button class="primary" type="submit" disabled={!groupEdit.name.trim()}>Save group</button></div></form></div>}

    {reviewState&&<div class="modal-layer control-plane-modal-layer" role="dialog" aria-modal="true" aria-label="Cross-vendor second opinion" onMouseDown={event=>event.target===event.currentTarget&&setReviewState(null)}><section class="modal control-plane-modal"><div class="modal-heading"><div><span>CROSS-VENDOR REVIEW</span><h2>{reviewState.preview.source_backend} → {reviewState.preview.backend}</h2></div><button aria-label="Close review" onClick={()=>setReviewState(null)}>×</button></div><div class="control-plane-modal-body"><p>This is user-initiated. The generated prompt is shown in full and no rule or observer can start this session.</p><label>Target project<select value={reviewState.project} onChange={event=>setReviewState(current=>current?{...current,project:event.currentTarget.value}:current)}>{projects.map(project=><option value={project.id}>{project.name}</option>)}</select></label><label>Additional review instructions<textarea value={reviewState.instructions} onInput={event=>setReviewState(current=>current?{...current,instructions:event.currentTarget.value,dirty:true}:current)} placeholder="Optional constraints or review focus" /></label><label>Reviewed prompt<textarea class="review-prompt" readOnly value={reviewState.preview.prompt}/></label>{reviewState.dirty&&<p class="modal-warning">Instructions changed. Refresh the prompt before spawning.</p>}{reviewState.error&&<p class="modal-warning" role="alert">{reviewState.error}</p>}</div><div class="modal-footer"><span>{reviewState.loading?'working…':reviewState.dirty?'preview stale':'prompt reviewed'}</span><button onClick={()=>setReviewState(null)}>Cancel</button><button onClick={()=>void refreshSecondOpinion()} disabled={reviewState.loading}>Refresh preview</button><button class="primary" disabled={reviewState.loading||reviewState.dirty} onClick={()=>void confirmSecondOpinion()}>Spawn {reviewState.preview.backend} review</button></div></section></div>}

    {handoffState&&<div class="modal-layer control-plane-modal-layer" role="dialog" aria-modal="true" aria-label="Handoff export" onMouseDown={event=>event.target===event.currentTarget&&setHandoffState(null)}><section class="modal control-plane-modal"><div class="modal-heading"><div><span>HANDOFF::EXPORT</span><h2>{historyName(handoffState.entry)}</h2></div><button aria-label="Close handoff" onClick={()=>setHandoffState(null)}>×</button></div><div class="control-plane-modal-body"><p>{handoffState.message}</p><textarea class="handoff-export" readOnly value={handoffState.markdown}/></div><div class="modal-footer"><span>read-only annotation export</span><button onClick={()=>{const blob=new Blob([handoffState.markdown],{type:'text/markdown'});const url=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=url;anchor.download=`handoff-${handoffState.entry.id}.md`;anchor.click();URL.revokeObjectURL(url)}}>Download</button><button class="primary" onClick={()=>void navigator.clipboard.writeText(handoffState.markdown).then(()=>setHandoffState(current=>current?{...current,message:'Copied to clipboard.'}:current)).catch(()=>setHandoffState(current=>current?{...current,message:'Clipboard blocked. Select the text and copy it manually.'}:current))}>Copy</button></div></section></div>}

    {sendToAgent&&<SendToAgentPicker request={sendToAgent} projects={orderedProjects} sessions={sessions} onClose={()=>setSendToAgent(null)} onSend={deliverToAgent}/>}

    {settingsOpen && <Settings initialSection={settingsSection} onStartTutorial={startTutorial} onOpenUsage={()=>{setSettingsOpen(false);setUsageOpen(true)}} onOpenAutomation={()=>{setSettingsOpen(false);setAutomationOpen(true)}} onClose={() => { setSettingsOpen(false); void refresh(); void loadProfiles(); void loadConfig(false) }} />}

    {promptLibraryOpen&&<PromptLibrary project={promptScope||activeProject} backend={active?.backend} onClose={()=>setPromptLibraryOpen(false)} onInsert={text=>window.dispatchEvent(new CustomEvent('mux:terminal-action',{detail:{sessionId:activeId,action:'insertText',text}}))}/>}

    {observationsProject&&<Observations project={observationsProject} onClose={()=>setObservationsProject(null)} onInsertBatch={activeId?text=>window.dispatchEvent(new CustomEvent('mux:terminal-action',{detail:{sessionId:activeId,action:'insertText',text}})):undefined}/>}

    {usageOpen&&<UsageDashboard onClose={()=>setUsageOpen(false)} onConfigure={()=>{setUsageOpen(false);openSettings('Usage analytics')}}/>}
    {automationOpen&&<AutomationDashboard onClose={()=>setAutomationOpen(false)} onConfigure={()=>{setAutomationOpen(false);openSettings('Automation')}} onOpenSession={sessionId=>{const session=sessions.find(item=>item.id===sessionId);if(!session){setError('The automation session is no longer live.');return}setAutomationOpen(false);void selectSession(session)}}/>}

    {processViewerOpen && <ProcessPanel initialSessionId={processSession?.id||null} initialProjectId={processScope} sessions={sessions} projects={projects} onClose={() => {setProcessViewerOpen(false);setProcessSession(null)}} onAttached={(preview, project) => {
      setPreviews(current => ({...current, [preview.id]: preview}))
      setProjects(items => items.map(item => item.id === project.id ? project : item))
      setLayoutMap(current => ({...current, [project.id]: parseLayout(project.layout)}))
    }} />}

    {notificationToast&&<button class="notification-toast" aria-live="assertive" onClick={()=>{setNotificationToast(null);openNotifications()}}><strong>{notificationToast.session_name||'daemon'}</strong><span>{notificationToast.type.replaceAll('_',' ')}</span><small>open notifications</small></button>}

    {tutorialOpen&&<GuidedTutorial hasProject={projects.length>0} onNavigate={navigateTutorial} onExit={closeTutorial} onComplete={closeTutorial}/>}

    {error && <div class="toast" onClick={() => setError('')}>{error}<span>×</span></div>}
  </div>
}
