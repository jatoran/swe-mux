import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { ComponentChildren, JSX } from 'preact'
import { api, openWebSocket, type ApiError } from './api'
import {
  allBackendNames, deliversHarnessPrompts, harnessDisplayName, hasHarnessTranscript, installHarnessRegistry, isAgentBackend,
  isObservedHarness, setHarnessEnablement, type HarnessRegistryPayload,
} from './harnessRegistry'
import { HANDSHAKE_TIMEOUT_MS, retryDelay, watchLiveness } from './liveness'
import { TerminalPane } from './TerminalPane'
import { recordPaneVisits, warmPaneBudget, warmPaneIds } from './warmPanes'
import { windowsPtyCompatibility, type TerminalRendererPreference, type WindowsPtyCompatibility } from './terminalRenderer'
import { ProjectResource } from './ProjectResource'
import { SendToAgentPicker, type SendToAgentRequest, type SendToAgentResult, type SendToAgentTarget } from './SendToAgentPicker'
import { pastePayload } from './noteSelection'
import { QueuePane } from './QueuePane'
import { editQueueMessage, enqueueMessage, fetchAutoStatus, fetchQueueSummary, sendQueueMessage, setAutoPaused, type QueueAutoStatus, type QueueTargetSummary } from './queueApi'
import { FleetQueue } from './FleetQueue'
import { ContinuityBanner } from './ContinuityBanner'
import { DirectoryPicker } from './DirectoryPicker'
import { folderNameFromPath } from './pathNames'
import { agentTargetName } from './agentTargets'
import {
  defaultInitScriptSelection, emptyProjectCreateDraft, projectCreateFolder, projectCreateReady,
  projectCreateRoot, suggestFolderName, toggleInitScript,
  type InitScript, type ProjectCreateDraft,
} from './projectCreate'
import { ProcessPanel, type FleetSnapshot, type Preview } from './ProcessPanel'
import { ResourceUsageSummary } from './ResourceUsage'
import { ProjectsManager, type ProjectPatch, type ProjectsManagerTab } from './ProjectsManager'
import { MenuGroup } from './MenuGroup'
import { PreviewPane } from './PreviewPane'
import type { NotificationData, UiNotification } from './Notifications'
import { alertPreferences, setAlertPreferencesFor } from './alertPrefs'
import { UsageDashboard } from './UsageDashboardView'
import { NetworkUsageModal } from './NetworkUsageModal'
import { StorageUsageModal } from './StorageUsageModal'
import { HistoryBrowser } from './HistoryBrowser'
import { AccountSwitcher, providerGlyph } from './ProviderAccounts'
import { PromptLibrary } from './PromptLibrary'
import { PROMPT_RAIL_EVENT } from './promptRail'
import { UtilityDrawer } from './UtilityDrawer'
import { OverflowRail } from './RailScroller'
import {
  DRAWER_COLLAPSE_WIDTH, DRAWER_PROJECT_STATE_KEY, DRAWER_REOPEN_WIDTH,
  DRAWER_DEFAULT_WIDTH, DRAWER_MIN_WIDTH, DRAWER_TABS, DRAWER_TAB_KEY, DRAWER_WIDTH_KEY,
  clampDrawerWidth, drawerMaximumWidth,
  drawerTab, storedDrawerWidth, type DrawerTabId,
} from './drawerTabs'
import {
  DRAWER_LAYOUT_KEY, DRAWER_PROJECT_PRESENTATIONS_KEY, activateDrawerTab,
  defaultDrawerLayout, drawerProjectPresentationFor, drawerStackForTab, drawerStacks, drawerTabs,
  isDefaultDrawerLayout,
  migrateDrawerProjectPresentations, moveDrawerTabDirection, moveDrawerTabToSplit,
  moveDrawerTabToStack, normalizeDrawerLayout, normalizeDrawerProjectPresentation,
  parseDrawerLayout, pruneDrawerProjectPresentations, reconcileDrawerProjectPresentations,
  resetDrawerLayout, serializeDrawerLayout, serializeDrawerProjectPresentations,
  setDrawerProjectPresentation, setDrawerSplitRatio, updateDrawerProjectPresentation,
  type DrawerEdge, type DrawerLayout, type DrawerProjectPresentation,
  type DrawerProjectPresentationMap,
} from './drawerLayout'
import {
  SIDEBAR_COLLAPSED_WIDTH, SIDEBAR_COLLAPSE_WIDTH, SIDEBAR_DEFAULT_WIDTH, SIDEBAR_MAX_WIDTH,
  SIDEBAR_MIN_WIDTH, SIDEBAR_REOPEN_WIDTH, SIDEBAR_RESIZER_WIDTH, clampSidebarWidth,
  dragCollapsedAtWidth, navigationSidebarCommandState,
} from './sidebarResize'
import { normalizeDrawerTabOrder } from './drawerTabOrder'
import {
  DRAWER_NOTE_KEY, claimDrawerNote, drawerNoteFor, isDrawerOwned, parseDrawerNotes,
  pruneDrawerNotes, serializeDrawerNotes, type DrawerNoteMap,
} from './drawerNotes'
import {
  presentationWithTransientDrawerTab, transientDrawerTabForProject, type TransientDrawerTab,
} from './drawerTransient'
import type { WatchScope } from './processWatch'
import { DRAWER_TAB_ICONS, NavPanelIcon, SidePanelIcon } from './railIcons'
import {
  CLIPBOARD_CHANGED_EVENT, clearClipboardHistory, configureClipboardCapture,
} from './clipboardHistory'
import { InteractionHud, showInteractionHud } from './InteractionHud'
import { currentInsertTarget, insertIntoFocusedSurface, noteTerminalFocus, subscribeInsertTarget } from './insertTarget'
import type { InsertTarget } from './insertTarget'
import type { NotePlacement } from './NotesTab'
import { ProjectRunMenu } from './ProjectRunMenu'
import { AutomationDashboard } from './AutomationDashboard'
import { VoicePlayer } from './VoicePlayer'
import { ConversationSurface, ConversationToggle, useConversation } from './ConversationControl'
import { resolveConversationTarget } from './conversationTarget'
import type { VoiceSessionCandidate } from './conversationTarget'
import { autoplayEnabled, enqueueAutoplay, enqueueRequestedStreamClip, playClip, setAutoplayEnabled, stopAllPlayback, stopSessionPlayback, unlockPlayback } from './voice'
import { handleSessionSound, type NormalizedMuxEvent } from './sessionSounds'
import { mergeSessionSnapshot, reconcileSessionSnapshots } from './sessionSnapshots'
import {
  KILL_TOMBSTONE_TTL_MS, applyKillTombstones, expiredKillIds, killRemovedTheSession,
  nextActiveAfterKill, type KillTombstones,
} from './sessionKills'
import { currentProfile, loadDrawerTabOrder, loadRailConfig, loadSettings, refreshSettings } from './deviceSettings'
import { initPush } from './push'
import { watchDevicePresence } from './devicePresence'
import type { Project, ProjectGroup, Session, LaunchProfile, VoiceClip, VoiceContent, VoiceMode, VoiceStatus } from './types'
import { keyChord } from './keys'
import { Settings } from './Settings'
import { HarnessSetup } from './HarnessSetup'
import { ActionEditorModal } from './ActionEditorModal'
import { GuidedTutorial, type TutorialStepId } from './GuidedTutorial'
import { completeTutorial, emitTutorialAction, resetTutorial, shouldStartTutorial } from './tutorial'
import { applyTheme, configureCustomTheme, type CustomTheme, type ThemeName } from './theme'
import { TRANSCRIPT_CHANGED_EVENT, TURN_ENDED_EVENT } from './transcriptView'
import { eventRequiresFleetRefresh } from './eventRefresh'
import { loadedUiBuildId, uiUpdateReloadReady, uiUpdateRequired } from './uiBuild'
import { applyNoteEditorConfig } from './noteEditorSettings'
import {
  DEFAULT_UI_SCALE, applyUiScale, createUiScaleWheelIntent, uiScaleConfigKey,
  uiScaleForIntent, uiScaleKeyboardIntent, watchUiScaleProfile, type UiScale,
} from './uiScale'
import { DEFAULT_CLAUDE_MAX_COLUMNS, claudeMaxColumnsFrom } from './terminalViewport'
import { bindingFor, displayChord, runCommand, searchCommands, type Command, type VoiceCommandResult } from './commands'
import { resolveRailVoiceEntries, type RailVoiceEntry } from './railVoice.ts'
import { requestTerminalAction } from './terminalActions.ts'
import { normalizeSpokenText, numberedCandidates, resolveVoiceIntent, selectNumberedCandidate, type VoiceIntentCandidate } from './voiceIntents'
import { buildFleetReadModel, fleetRundown, fleetRundownDetail, type FleetSession } from './fleetStatus'
import {
  parseVoiceQuery, projectListPage, sessionListPage, spokenSessionStatus, voiceHelpPage,
  voiceSessionFilterMatches, type VoiceQuery, type VoiceSessionFilter, type VoiceScope,
} from './voiceQueries'
import { adjacentVoiceSession, buildVoiceNavigationIndex, projectAtVoiceNumber, sessionAtVoiceNumber } from './voiceNavigation'
import { sessionLaunchVoicePhrases } from './voiceLaunch'
import {
  clearSpokenListContext, loadSpokenListContext, saveSpokenListContext,
  SPOKEN_LIST_TTL_MS, type SpokenListContext,
} from './spokenListContext'
import { copyPreparedText } from './terminalClipboard'
import { absoluteProjectPath, FILE_COPY_MAX_LINES, truncateForClipboard } from './fileClipboard'
import { clampContextMenuLeft, fitMenuInViewport } from './menuPosition'
import { defaultMobileInputSettings, mobileInputSettings, type MobileInputSettings } from './mobileInput'
import { adjacentMobileTab, mobileWorkspaceProjection } from './mobileWorkspace'
import { SOFT_KEYBOARD_EVENT, dismissSoftKeyboard, rememberSoftKeyboardInset, softKeyboardHolder, softKeyboardInset } from './mobileKeyboard'
import { MOBILE_TERMINAL_DRAFT_EVENT, mobileTerminalDraftStore } from './mobileTerminalDraft'
import { classifyGesture, defaultMobileGestureSettings, gestureOverlayDepth, mobileGestureSettings, overlayBackEnabled, pathOwnsHorizontalScroll, resolveGestureCommand, swipeAwayCloseEnabled, type MobileGestureSettings } from './mobileGestures'
import { dismissStack } from './dismissStack.ts'
import { useDismissLevel } from './modalFocus'
import { installSystemBack } from './systemBack.ts'
import { focusMemoryWith, parseFocusMemory, parseViewPreference, reconcileFocusView, rememberedView, resolveInitialFocus, viewUrl } from './viewState'
import {
  DROP_LIST_MARGIN, edgeAutoScrollDelta, listDropTargetForPoint, MOBILE_HOLD_DRAG, MOBILE_HOLD_MOVE_DRAG, POINTER_MOVE_DRAG, pointerDragMoveDecision,
  reorderForHover, reorderTargetFromContainer, type DropSide, type ListDropTarget, type PointerDragActivation, type ReorderAxis, type ReorderTarget,
} from './dragReorder'
import { claimPointerDrag, markPointerDragClaims, pointerDragOwnsPointer } from './pointerDragClaim'
import { relativeStackTab } from './workspaceTabs'
import {
  COLLAPSED_PROJECTS_KEY, canHideProject, describeOpenWork, loadCollapsedProjects,
  projectInitials, projectOpenWork, serializeCollapsedProjects, setAllCollapsed, toggleCollapsed,
} from './sidebarProjects'
import {
  PROJECT_SORT_OPTIONS, SECTION_SORT_OPTIONS, SIDEBAR_ORDER_KEY,
  isBucketCollapsed, loadSidebarOrder, mergeVisibleOrder,
  projectRecency, projectSortLabel, pruneSidebarOrder, sectionSortLabel, serializeSidebarOrder,
  setAllBucketsCollapsed, setProjectSortMode, sortBuckets, sortProjects,
  toggleBucketCollapsed,
} from './projectSort'
import { PROJECT_RECENCY_EVENT, type ProjectRecencyEventDetail, type ProjectUseReason } from './projectRecency'
import { placePendingTerminal, selectPendingTerminal, type PendingSpawnPlacement } from './pendingSession'
import { pendingAcks, pruneAcks, isUnread, projectRailStatus, projectSetRailStatus, type AckMap, type ProjectRailActivity } from './sessionAttention'
import { isHumanPresent, watchHumanPresence } from './humanPresence'
import { activityBadges, sessionStatus } from './sessionStatus'
import { StateIndicator } from './StateIndicator'
import { SessionRowBody } from './SessionRowBody'
import type { DotShape, StandingRender } from './sessionRowConfig'
import {
  applySessionDotSize, useObservedWidth, useRowClock, useSessionRowConfig, watchSessionDotProfile,
} from './sessionRowPrefs'
import { serverNow } from './serverClock.ts'
import {
  buildSessionRowTokens, deriveRowContext, identityRowTokens, sessionContextArc,
  sessionStandingMark, shedForWidth,
} from './sessionRowFields'
import {
  browserUuid, emptyLayout, leaves, noteResourceId, paneStack, parseLayout, parseNoteResourceId, resourceLeaf, worktreeFileResourceId,
  reconcilePreviews, reconcileTerminals, removeLeaf, replaceTerminal, setSplitRatio,
  activateContainingStack, activateStackChild, addLeafToStack, dissolveStack, groupTerminalsInStack, moveLeafToSplit, moveLeafToStack, moveTerminalBeside, openAnchorId, openTab, paneNeighborIds, paneStacks, queueLeafId, queueLeafSessionId, reorderStack, resolveLayout, spawnAnchorId, splitTerminal, splitView, stackForView, stackTerminal, terminalIds, terminalLeaf, visibleTerminalIds, type PaneLayout,
  type PaneDirection, type PaneLeaf, type PaneLeafKind, type PaneNode, type SplitDirection,
} from './layout'

// `/events` is authoritative for live changes. These are only visible-tab recovery
// backstops, so keeping them sub-minute re-sent whole fleet payloads without improving
// convergence. Process watch stays fresher but uses the reduced summary representation.
const FLEET_SAFETY_REFRESH_MS=60_000
const KEYBINDING_SAFETY_REFRESH_MS=60_000
const PROCESS_SUMMARY_REFRESH_MS=10_000
// How long an agent's finished turn must sit on screen, with a human at the
// window, before the row counts as read. Long enough that flicking through
// panes does not silently clear a fleet; short enough that reading a reply and
// moving on does.
const READ_ACK_DWELL_MS=1_200

const paneDirectionOptions:Array<{id:PaneDirection;glyph:string;direction:SplitDirection;position:'before'|'after'}>=[
  {id:'left',glyph:'←',direction:'horizontal',position:'before'},
  {id:'right',glyph:'→',direction:'horizontal',position:'after'},
  {id:'up',glyph:'↑',direction:'vertical',position:'before'},
  {id:'down',glyph:'↓',direction:'vertical',position:'after'},
]

const isAgent = (session: Session) => isAgentBackend(session.backend)

function isEndedSession(session: Session) {
  return session.state === 'exited' || session.state === 'crashed'
}

function railVoiceConfirmation(entry: RailVoiceEntry): string {
  const name=entry.phrases[0]||entry.item.label
  if(entry.request.action==='pasteText')return 'Pasted into the focused session. Still listening.'
  if(entry.request.action==='sendKey')return `Pressed ${name}. Still listening.`
  return `${entry.request.submit?'Ran':'Inserted'} ${name}. Still listening.`
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
//
// `standing` is the row configuration's rendering choice, honoured on every
// surface rather than in the sidebar alone: moving the fact onto the indicator
// and leaving the tab strip printing glyphs would be the same fact twice, in the
// two places most likely to be on screen together.
const activityGlyphs=(session:Session|undefined,standing:StandingRender)=>{
  if(!session||session.pending||standing!=='row')return null
  const badges=activityBadges(session)
  if(!badges.length)return null
  return <span class="activity-badges" role="img" aria-label={badges.map(badge=>badge.label).join(', ')}>
    {badges.map((badge,index)=><span key={index} class="activity-badge" title={badge.title}>{badge.glyph}{badge.count&&badge.count>1?<span class="activity-count">{badge.count}</span>:null}</span>)}
  </span>
}

// What a session tab holds, before which one it is. Every other tab kind already carries a
// glyph in the strip (preview, note, history, queue), so a session tab showing only a state dot
// was the one kind you had to read the title to identify. This is the sidebar's provider mark,
// from the same source as the account switcher's, so a strip reads the way a row does: state,
// kind, title. A shell keeps the prompt mark rather than nothing - "no glyph" is one more thing
// to know about a strip that is otherwise total.
const sessionGlyph=(session:Session|undefined)=>{
  if(!session)return null
  if(!isAgent(session))return <span class="tab-session-glyph shell" title="shell">❯</span>
  return <span class={`tab-session-glyph agent-prefix ${session.backend}`} title={harnessDisplayName(session.backend)}>{providerGlyph(session.backend)}</span>
}

// The one state indicator every surface draws. Shape (and any gauge wrapped
// around it) comes from the sidebar row configuration, so a hexagon in the
// sidebar is a hexagon in the tab strip and the context menu too.
const sessionStateDot=(
  session:Session|undefined,
  shape:DotShape,
  gauge?:{pct:number;peak:number}|null,
  standing?:{label:string}|null,
)=>{
  if(!session||(isAgent(session)&&!isObservedHarness(session.backend)))return null
  return <StateIndicator session={session} shape={shape} gauge={gauge} standing={standing}/>
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
  tokens_in?: number; tokens_out?: number; tokens_cache_read?:number;tokens_cache_write?:number;cost_usd?:number
  model?: string; measurement_source?: string
  compaction_count?:number;last_compaction_at?:number;compaction_capability?:string;compaction_confidence?:string
  auto_named?:number;generated_title?:string
}
type ReviewPreview={source_run_id:string;source_backend:string;backend:string;cwd:string;worktree_context:string;prompt:string;relation:'review';preview_token:string}
type ReviewState={entry:HistoryEntry;instructions:string;project:string;preview:ReviewPreview;dirty:boolean;loading:boolean;error:string}
type HandoffState={entry:HistoryEntry;markdown:string;message:string}
type ContextState = { session: Session; x: number; y: number; source: 'sidebar'|'tab'|'pane'|'mobile' } | null
type ProjectContext = { project: Project; x: number; y: number } | null
type SidebarContext = { x:number;y:number } | null
type NoteContext = { resourceId:string;projectId:string;x:number;y:number } | null
type TabContext = { leaf:PaneLeaf;label:string;projectId:string;x:number;y:number;source:'tab'|'mobile' } | null
type RenameTarget = { kind: 'session'; session: Session } | { kind: 'project'; project: Project }
type NoteTarget={projectId:string;kind:'note'|'global-note'|'file'|'worktree-file';resourceId:string;worktree?:string}
type StartupMilestone = 'pane_mounted' | 'socket_open' | 'replay_ready'
type ClientStartupTiming = Partial<Record<'api_response' | StartupMilestone, number>>
type RunMenuState={project:Project;x:number;y:number}
type WorktreeSetupResult={status:'not_configured'|'succeeded'|'failed'|'timed_out'|'error';error?:string;exit_code?:number|null}
type WorktreeSpawnResult={status:'not_requested'|'spawned'|'error';session_id?:string;session?:Session;error?:string;setup?:WorktreeSetupResult}
type PendingSpawn={projectId:string;placement:PendingSpawnPlacement|null;resolvedId?:string}

function pendingTerminal(id:string,project:Project,backend:string='shell',options?:{cwd?:string;name?:string;label?:string;detail?:string}):Session {
  // Daemon clock: this placeholder is rendered by the same sidebar row that ages
  // real sessions, so stamping it locally would make it the one row whose age is
  // measured between two different clocks.
  const now=serverNow()
  const cwd=options?.cwd||project.root
  return {
    id,name:options?.name||`starting ${backend==='shell'?'terminal':backend}…`,project_id:project.id,backend,native_session_id:id,
    cwd,exe:'',args:[],pid:-1,created_at:now,state:'starting',tokens_in:0,
    process_job_assignment:'pending',tokens_out:0,tokens_cache_read:0,tokens_cache_write:0,cost_usd:0,context_window:0,context_pct:0,last_activity_ts:now,
    git:{dirty:0,ahead:0,behind:0},pinned_attention:false,broadcast:false,context_peak_pct:0,
    compaction_count:0,runtime_cwd:cwd,runtime_cwd_live:false,runtime_cwd_source:'spawn',
    runtime_cwd_dropped:0,pending:true,pending_label:options?.label,pending_detail:options?.detail,
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
  const [, setHarnessRegistryRevision] = useState(0)
  const [projectId, setProjectId] = useState('')
  const [activeId, setActiveId] = useState<string | null>(null)
  const [focusedViewId,setFocusedViewId]=useState<string|null>(null)
  const [focusedInsertTarget,setFocusedInsertTarget]=useState<InsertTarget|null>(()=>currentInsertTarget())
  useEffect(()=>subscribeInsertTarget(setFocusedInsertTarget),[])
  // A view we have asked to focus that this project's layout does not hold yet. Set by
  // `requestFocusView` and consumed by the reconciliation effect; see
  // `reconcileFocusView` for why the intent has to outlive the refresh.
  const pendingFocusId=useRef<string|null>(null)
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
  const loadedBuildId = useRef(loadedUiBuildId())
  const [uiUpdateAvailable, setUiUpdateAvailable] = useState(false)
  const [redeployConfirmOpen, setRedeployConfirmOpen] = useState(false)
  // '' browses every Project; a Project id prefilters the archive to it.
  const [historyScope,setHistoryScope]=useState('')
  const [historyOpen,setHistoryOpen]=useState(false)
  const [processScope,setProcessScope]=useState<string|null>(null)
  // The drawer tab's scope: '' is every Project, anything else is that Project. Kept here (not
  // in the tab) so switching tabs and coming back does not silently reset what you were
  // watching. Project-scoped by default, like every other Project-scoped tab — a session-scoped
  // processes view would churn its whole body on each focus change and read empty most of the
  // time, since most sessions are just their agent CLI and a conhost.
  const [processWatchScope,setProcessWatchScope]=useState<WatchScope>('')
  // Which Project's templates join the global ones. Unlike the other surfaces
  // this is additive rather than restrictive, so the app menu still passes the
  // active Project: opening "unscoped" would remove templates, not filters.
  const [promptScope,setPromptScope]=useState<Project|null>(null)
  const [promptTargetId,setPromptTargetId]=useState<string|null>(null)
  const [reviewState,setReviewState]=useState<ReviewState|null>(null)
  const [handoffState,setHandoffState]=useState<HandoffState|null>(null)
  // A note/markdown selection waiting for a target. The message is captured when the dialog
  // opens, so editing the document underneath cannot change what is about to be sent.
  const [sendToAgent,setSendToAgent]=useState<SendToAgentRequest|null>(null)
  // Per-target prompt-queue aggregates (pending counts for pane chips), keyed by
  // target session id and refreshed off `queue_updated` events.
  const [queueSummary,setQueueSummary]=useState<Record<string,QueueTargetSummary>>({})
  const [mobileDraftRevision,setMobileDraftRevision]=useState(0)
  useEffect(()=>{
    const changed=()=>setMobileDraftRevision(value=>value+1)
    window.addEventListener(MOBILE_TERMINAL_DRAFT_EVENT,changed)
    return()=>window.removeEventListener(MOBILE_TERMINAL_DRAFT_EVENT,changed)
  },[])
  const mobileDraftIndicator=(sessionId:string)=>mobileTerminalDraftStore.has(sessionId)
    ?<span class="terminal-draft-indicator" title="Unsent mobile draft" aria-label="unsent draft"/>
    :null
  const queueSummaryTimer=useRef<number|undefined>(undefined)
  // The install-wide auto-delivery flag, held here so `autodelivery.pause` can name the
  // act it is about to perform. The emergency stop has to be reachable without opening a
  // surface first, which means the command list needs to know the current state.
  const [autoStatus,setAutoStatus]=useState<QueueAutoStatus|null>(null)
  const loadQueueSummary=async()=>{
    try{
      const [result,policy]=await Promise.all([fetchQueueSummary(),fetchAutoStatus()])
      setQueueSummary(Object.fromEntries(result.targets.map(target=>[target.target_session_id,target])))
      setAutoStatus(policy)
    }catch{/* the daemon is briefly away; the next event retries */}
  }
  // Fleet-wide pending count: "is anything waiting anywhere", which is the question you
  // have while looking at some other session. It labels the way into the fleet queue.
  const queuePendingTotal=useMemo(()=>Object.values(queueSummary).reduce((total,target)=>total+target.pending,0),[queueSummary])
  // Sidebar row appearance. The clock is shared and quantized, so ageing a
  // working row costs one timer for the whole list rather than one per row; the
  // derived context answers "differs from the project default" once per snapshot
  // instead of once per row.
  const rowConfig=useSessionRowConfig()
  const rowNow=useRowClock()
  const rowQueueDepth=useMemo(
    ()=>Object.fromEntries(Object.entries(queueSummary).map(([id,target])=>[id,target.pending])),
    [queueSummary],
  )
  // Width-driven token shedding is measured, not queried in CSS: hiding a token
  // with `display:none` leaves the separator JSX already emitted beside it.
  const sidebarRef=useRef<HTMLElement>(null)
  const rowWidth=useObservedWidth(sidebarRef)
  const rowShed=shedForWidth(rowWidth)
  // Device-local drafts are unioned into the row context rather than read at the
  // row: the daemon's ledger sees text typed from any client but not text staged
  // in this browser's own draft composer, which never reaches the PTY. Neither
  // source is a superset of the other.
  const localDrafts=useMemo(()=>mobileTerminalDraftStore.stamps(),[mobileDraftRevision])
  const rowContext=useMemo(
    ()=>deriveRowContext(sessions,rowQueueDepth,rowNow,rowShed,localDrafts),
    [sessions,rowQueueDepth,rowNow,rowShed,localDrafts],
  )
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
  // Acknowledgements in flight for sidebar rows. The durable mark lives on the
  // session record; this is only the optimistic overlay. See sessionAttention.ts.
  const [ackedTurns,setAckedTurns]=useState<AckMap>({})
  const [noteMenu,setNoteMenu]=useState<NoteContext>(null)
  const [tabMenu,setTabMenu]=useState<TabContext>(null)
  const [emptyMenu, setEmptyMenu] = useState<{x:number;y:number} | null>(null)
  const [drawerDisplayMenu,setDrawerDisplayMenu]=useState<{x:number;y:number;surface:'tabs'|'rail'}|null>(null)
  const [zoomedId, setZoomedId] = useState<string | null>(null)
  const [keybindings, setKeybindings] = useState<Record<string, string>>({ 'ctrl+alt+t': 'session.spawnShell', 'ctrl+alt+p': 'palette.open' })
  const [confirmKillId, setConfirmKillId] = useState<string | null>(null)
  const [confirmHideId, setConfirmHideId] = useState<string | null>(null)
  const [collapsedProjects, setCollapsedProjects] = useState<Set<string>>(() => loadCollapsedProjects(localStorage.getItem(COLLAPSED_PROJECTS_KEY)))
  const [mainMenuOpen, setMainMenuOpen] = useState(false)
  // Raw setters; every caller uses the mobile-exclusive wrappers defined below.
  const [sidebarOpen, setSidebarOpenState] = useState(false)
  const [sidebarCollapsed,setSidebarCollapsed]=useState(()=>localStorage.getItem('mux.sidebar.collapsed.v1')==='true')
  const [sidebarWidth,setSidebarWidth]=useState(()=>{
    const stored=Number(localStorage.getItem('mux.sidebar.width.v1'))
    return Number.isFinite(stored)&&stored>=SIDEBAR_MIN_WIDTH&&stored<=SIDEBAR_MAX_WIDTH?stored:SIDEBAR_DEFAULT_WIDTH
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
  const [noteTitles,setNoteTitles]=useState<Record<string,string>>({})
  // Which Notes sub-tab each Project last selected. Device-local, because the active drawer
  // editor also claims that note instead of its pane while the drawer is open. The remembered
  // selection survives closing the drawer and switching Projects.
  // The shared layout is never mutated merely by selecting a drawer tab. See `drawerNotes.ts`
  // for why one editor per note per browser is a correctness rule and not a preference.
  const [drawerNotes,setDrawerNotes]=useState<DrawerNoteMap>(()=>parseDrawerNotes(localStorage.getItem(DRAWER_NOTE_KEY)))
  const [drawerNoteClaimRequest,setDrawerNoteClaimRequest]=useState<{token:number;projectId:string;resourceId:string}|null>(null)
  const drawerNoteClaimSequence=useRef(0)
  useEffect(()=>{
    if(!drawerNoteClaimRequest)return
    const token=drawerNoteClaimRequest.token
    const timeout=window.setTimeout(()=>setDrawerNoteClaimRequest(current=>current?.token===token?null:current),5000)
    return()=>window.clearTimeout(timeout)
  },[drawerNoteClaimRequest?.token])
  const [folderPickerOpen,setFolderPickerOpen]=useState(false)
  const [groupEdit,setGroupEdit]=useState<{id?:string;name:string}|null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [harnessSetupNeeded, setHarnessSetupNeeded] = useState(false)
  const [actionEditorOpen, setActionEditorOpen] = useState(false)
  // The section a caller asked Settings to land on, or undefined for "wherever the
  // user left off" - Settings remembers its own last tab, so an unqualified open must
  // stay unqualified rather than assert General.
  const [settingsSection, setSettingsSection] = useState<string|undefined>(undefined)
  // Which MenuGroup is expanded in the app menu; null collapses every group.
  const [menuGroup,setMenuGroup]=useState<string|null>(null)
  const [tutorialOpen,setTutorialOpen]=useState(()=>shouldStartTutorial())
  const [processSession, setProcessSession] = useState<Session | null>(null)
  const [processViewerOpen,setProcessViewerOpen]=useState(false)
  const [processFleet,setProcessFleet]=useState<FleetSnapshot|null>(null)
  const [previews, setPreviews] = useState<Record<string, Preview>>({})
  const [notificationData, setNotificationData] = useState<NotificationData>({notifications:[],deliveries:[]})
  const [notificationUnread, setNotificationUnread] = useState(0)
  const [notificationToast, setNotificationToast] = useState<UiNotification | null>(null)
  // Quick sidebar bell mirrors this device profile's shared alert master switch.
  // Sound and push channel choices stay intact while the master is muted. Kept in sync
  // through the `mux:settings-changed` event the device-settings cache emits.
  const [alertsEnabled, setAlertsEnabled] = useState(() => alertPreferences().enabled)
  const [railVoiceRevision,setRailVoiceRevision]=useState(0)
  const [usageOpen, setUsageOpen] = useState(false)
  const [networkUsageOpen,setNetworkUsageOpen]=useState(false)
  const [storageUsageOpen,setStorageUsageOpen]=useState(false)
  // The fleet queue overlay, and the Project it opens filtered to (the Project menu scopes
  // it to its own row; everywhere else opens it unfiltered). `null` is closed.
  const [fleetQueue, setFleetQueue] = useState<{ projectId: string } | null>(null)
  const [automationOpen,setAutomationOpen]=useState(false)
  const [projectGroups,setProjectGroups]=useState<ProjectGroup[]>([])
  // False until the first `/api/project-groups` response lands. Nothing may prune
  // device-local sidebar state against the empty mount-time arrays: they mean "not
  // fetched yet", not "the user deleted everything".
  const [registryLoaded,setRegistryLoaded]=useState(false)
  const dragSessionTargetRef=useRef<ListDropTarget|null>(null)
  type ProjectDrag={id:string;previewIds:string[];overId:string|null;side:DropSide|null}
  type BucketDrag={id:string;previewIds:string[]}
  type PaneDropZone='tabs'|'left'|'right'|'top'|'bottom'
  type StackTabDrag={stackId:string;childId:string;kind:PaneLeafKind;targetStackId:string;zone:PaneDropZone;previewIds:string[];overId:string|null;side:DropSide|null}
  const [dragProject,setDragProjectState]=useState<ProjectDrag|null>(null)
  const dragProjectRef=useRef<ProjectDrag|null>(null)
  // Ref-only: the ghost and the drop indicator are the drag's feedback, so nothing
  // about a bucket drag needs to re-render the tree it is reordering.
  const dragBucketRef=useRef<BucketDrag|null>(null)
  // Device-local sidebar sorting and Group fold state. Manual Group order itself
  // is server-side; ungrouped Projects always render at the root before Groups.
  const [sidebarOrder,setSidebarOrderState]=useState(()=>loadSidebarOrder(localStorage.getItem(SIDEBAR_ORDER_KEY)))
  const setSidebarOrder=(next:ReturnType<typeof loadSidebarOrder>)=>{
    setSidebarOrderState(next)
    localStorage.setItem(SIDEBAR_ORDER_KEY,serializeSidebarOrder(next))
  }
  const applyProjectUse=(targetProject:string,lastUsedAt:number)=>{
    if(!targetProject||!Number.isFinite(lastUsedAt)||lastUsedAt<=0)return
    setProjects(items=>items.map(item=>item.id===targetProject
      ?{...item,last_used_at:Math.max(item.last_used_at||0,lastUsedAt)}
      :item))
  }
  const markProjectRecent=(targetProject:string,reason:ProjectUseReason='session_started')=>{
    void api<{project_id:string;last_used_at:number}>('POST',`/api/projects/${encodeURIComponent(targetProject)}/used`,{reason})
      .then(result=>applyProjectUse(result.project_id,result.last_used_at))
      .catch(()=>{})
  }
  const [sortMenu,setSortMenu]=useState<{x:number;y:number}|null>(null)
  const [dragStackTab,setDragStackTabState]=useState<StackTabDrag|null>(null)
  const dragStackTabRef=useRef<StackTabDrag|null>(null)
  const suppressDragClickRef=useRef<string|null>(null)
  const pointerDropIndicatorRef=useRef<HTMLElement|null>(null)
  const activePointerDragCancelRef=useRef<(()=>void)|null>(null)
  const setDragProject=(next:ProjectDrag|null)=>{dragProjectRef.current=next;setDragProjectState(next)}
  const setDragStackTab=(next:StackTabDrag|null)=>{dragStackTabRef.current=next;setDragStackTabState(next)}
  const previewDragStackTab=(next:StackTabDrag)=>{dragStackTabRef.current=next}
  const [promptLibraryOpen,setPromptLibraryOpen]=useState(false)
  // The inbox is per-Project, so it carries its Project rather than following the
  // active one — it opens from a Project's own context menu.
  // The Queue drawer tab's deliberate-open counter focuses the composer even when the
  // same chip is clicked twice.
  const [queueOpenToken,setQueueOpenToken]=useState(0)
  // The utility workspace has one device-local split tree shared by every Project. Selection
  // and desktop expansion remain device-local per Project. Mobile visibility is transient.
  const [mobileWorkspace,setMobileWorkspace]=useState(()=>window.matchMedia('(max-width:760px)').matches)
  const [viewportWidth,setViewportWidth]=useState(()=>window.innerWidth)
  const [mobileDrawerOpen,setMobileDrawerOpen]=useState(false)
  useEffect(()=>{ activePointerDragCancelRef.current?.() },[projectId,mobileWorkspace])
  useEffect(()=>()=>activePointerDragCancelRef.current?.(),[])
  // A desktop resize previews collapse without writing per-Project persistence on every
  // threshold crossing. Null means the Project's durable presentation owns visibility.
  const [drawerResizeOpen,setDrawerResizeOpen]=useState<boolean|null>(null)
  const legacyDrawerTab=useRef(localStorage.getItem(DRAWER_TAB_KEY))
  const drawerMigrationPending=useRef(localStorage.getItem(DRAWER_PROJECT_PRESENTATIONS_KEY)===null)
  const [drawerLegacySettingsReady,setDrawerLegacySettingsReady]=useState(
    ()=>localStorage.getItem(DRAWER_LAYOUT_KEY)!==null,
  )
  const [drawerLayout,setDrawerLayoutState]=useState<DrawerLayout>(()=>parseDrawerLayout(
    localStorage.getItem(DRAWER_LAYOUT_KEY),normalizeDrawerTabOrder(loadDrawerTabOrder())))
  const drawerLayoutRef=useRef(drawerLayout)
  drawerLayoutRef.current=drawerLayout
  const [drawerProjectPresentations,setDrawerProjectPresentations]=useState<DrawerProjectPresentationMap>(()=>
    migrateDrawerProjectPresentations(
      localStorage.getItem(DRAWER_PROJECT_PRESENTATIONS_KEY),
      localStorage.getItem(DRAWER_PROJECT_STATE_KEY),drawerLayout,
      legacyDrawerTab.current,projectId,
    ))
  const [unscopedDrawerPresentation,setUnscopedDrawerPresentation]=useState<DrawerProjectPresentation>(()=>
    normalizeDrawerProjectPresentation(null,drawerLayout))
  const activeDrawerPresentation=projectId
    ?drawerProjectPresentationFor(drawerProjectPresentations,projectId,drawerLayout)
    :normalizeDrawerProjectPresentation(unscopedDrawerPresentation,drawerLayout)
  const [transientDrawer,setTransientDrawer]=useState<TransientDrawerTab|null>(null)
  const transientDrawerTab=transientDrawerTabForProject(transientDrawer,projectId)
  const renderedDrawerPresentation=presentationWithTransientDrawerTab(
    activeDrawerPresentation,drawerLayout,transientDrawerTab)
  const drawerTabId=activeDrawerPresentation.focused_tab
  const clipboardOpen=mobileWorkspace?mobileDrawerOpen:(drawerResizeOpen??activeDrawerPresentation.desktop_expanded)
  // An Action rail prompt button whose template has {{placeholders}} has nothing to
  // inject yet, so it hands the template to the Prompt templates section in Actions.
  const [promptPreselect,setPromptPreselect]=useState<{key:string}|undefined>()
  const [drawerTabDisplay,setDrawerTabDisplay]=useState<'icon'|'title'>('icon')
  const [utilityRailDisplay,setUtilityRailDisplay]=useState<'icon'|'title'>('icon')
  const utilityRailWidth=utilityRailDisplay==='title'?112:40
  const [drawerWidth,setDrawerWidth]=useState(()=>storedDrawerWidth(localStorage.getItem(DRAWER_WIDTH_KEY)))
  const leftChromeWidth=sidebarCollapsed?SIDEBAR_COLLAPSED_WIDTH:sidebarWidth+SIDEBAR_RESIZER_WIDTH
  const drawerWidthLimit=drawerMaximumWidth(viewportWidth,leftChromeWidth,utilityRailWidth)
  const renderedDrawerWidth=clampDrawerWidth(drawerWidth,drawerWidthLimit)
  const [dragDrawerTab,setDragDrawerTab]=useState<DrawerTabId|null>(null)
  const dragDrawerBaseRef=useRef<DrawerLayout|null>(null)
  const dragDrawerLayoutRef=useRef<DrawerLayout|null>(null)
  const dragDrawerTargetRef=useRef<{stackId:string;kind:'join'|'split';edge?:DrawerEdge}|null>(null)
  const [drawerAnnouncement,setDrawerAnnouncement]=useState('')
  const drawerLauncherTabs=useMemo(()=>drawerTabs(drawerLayout).map(drawerTab),[drawerLayout])
  const [clipboardEnabled,setClipboardEnabled]=useState(true)
  // A momentary drawer belongs only to the Project that opened it. Clear the state
  // after any Project switch so returning later cannot revive a stale Actions peek.
  useEffect(()=>setTransientDrawer(null),[projectId])
  const [xtermScrollback, setXtermScrollback] = useState(10000)
  const [terminalRenderer, setTerminalRenderer] = useState<TerminalRendererPreference>('auto')
  const [claudeMaxColumns, setClaudeMaxColumns] = useState<number>(DEFAULT_CLAUDE_MAX_COLUMNS)
  // Chrome scale as a number. Every other surface reads it as a CSS custom property, but
  // xterm owns its own font and derives the cell grid from it, so the terminal has to be
  // handed the value rather than inheriting it.
  const [uiScale, setUiScale] = useState<UiScale>(DEFAULT_UI_SCALE)
  const uiScaleRef = useRef<UiScale>(DEFAULT_UI_SCALE)
  const uiScaleConfigRef = useRef<Record<string, unknown> | null>(null)
  const uiScalePersistTimer = useRef<number | null>(null)
  const uiScalePersistGeneration = useRef(0)
  uiScaleRef.current = uiScale
  const [windowsPty, setWindowsPty] = useState<WindowsPtyCompatibility | undefined>(undefined)
  const [mobileInput, setMobileInput] = useState<MobileInputSettings>(defaultMobileInputSettings)
  const [mobileGestures, setMobileGestures] = useState<MobileGestureSettings>(defaultMobileGestureSettings)
  const [swipeAwayClose, setSwipeAwayClose] = useState(true)
  const [overlayBack, setOverlayBack] = useState(true)
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
    if(open&&mobileWorkspace){setMobileDrawerOpen(false);dismissSoftKeyboard()}
  }
  const storeDrawerValue=(key:string,value:string)=>{
    try{localStorage.setItem(key,value)}
    catch(cause){setError(`Side panel layout is active for this session but could not be saved: ${cause instanceof Error?cause.message:String(cause)}`)}
  }
  const updateDrawerPresentation=(
    targetProject:string,
    update:(current:DrawerProjectPresentation)=>DrawerProjectPresentation,
  )=>{
    if(!targetProject){
      setUnscopedDrawerPresentation(current=>normalizeDrawerProjectPresentation(update(
        normalizeDrawerProjectPresentation(current,drawerLayoutRef.current)),drawerLayoutRef.current))
      return
    }
    setDrawerProjectPresentations(current=>{
      const layout=drawerLayoutRef.current
      const presentation=drawerProjectPresentationFor(current,targetProject,layout)
      const updated=setDrawerProjectPresentation(current,targetProject,update(presentation),layout)
      if(updated!==current)storeDrawerValue(DRAWER_PROJECT_PRESENTATIONS_KEY,serializeDrawerProjectPresentations(updated,layout))
      return updated
    })
  }
  const setClipboardOpen=(next:OpenState,targetProject=projectId)=>{
    if(mobileWorkspace){
      const open=typeof next==='function'?next(mobileDrawerOpen):next
      if(!open){activePointerDragCancelRef.current?.();setTransientDrawer(null)}
      setMobileDrawerOpen(open)
      if(open){setSidebarOpenState(false);dismissSoftKeyboard()}
      return
    }
    if(!targetProject){
      const current=normalizeDrawerProjectPresentation(unscopedDrawerPresentation,drawerLayoutRef.current)
      const open=typeof next==='function'?next(current.desktop_expanded):next
      if(!open){activePointerDragCancelRef.current?.();setTransientDrawer(null)}
      updateDrawerPresentation('',current=>updateDrawerProjectPresentation(current,drawerLayoutRef.current,{
        desktop_expanded:open,
      }))
      return
    }
    const current=drawerProjectPresentationFor(drawerProjectPresentations,targetProject,drawerLayoutRef.current)
    const open=typeof next==='function'?next(current.desktop_expanded):next
    if(!open){activePointerDragCancelRef.current?.();setTransientDrawer(null)}
    updateDrawerPresentation(targetProject,presentation=>updateDrawerProjectPresentation(presentation,drawerLayoutRef.current,{
      desktop_expanded:open,
    }))
  }
  const selectDrawerTab=(tab:DrawerTabId,targetProject=projectId)=>{
    setTransientDrawer(null)
    updateDrawerPresentation(targetProject,current=>activateDrawerTab(current,drawerLayoutRef.current,tab))
  }
  /** Open the drawer on a specific tab (or toggle that tab shut if it is already showing). */
  const showDrawerTab=(tab:DrawerTabId,targetProject=projectId)=>{
    const presentation=targetProject
      ?drawerProjectPresentationFor(drawerProjectPresentations,targetProject,drawerLayoutRef.current)
      :normalizeDrawerProjectPresentation(unscopedDrawerPresentation,drawerLayoutRef.current)
    const stack=drawerStackForTab(drawerLayoutRef.current,tab)
    const visible=Boolean(stack&&presentation.selected_tabs[stack.id]===tab)
    const open=mobileWorkspace?mobileDrawerOpen:presentation.desktop_expanded
    selectDrawerTab(tab,targetProject)
    setClipboardOpen(!(open&&visible&&presentation.focused_tab===tab),targetProject)
    // Reaching Notes from the rail, the tab strip, or `drawer.notes` says nothing about scope,
    // so it means "this Project" — the drawer sits beside that Project's workspace. Only the
    // app menu's deliberately unscoped `notes.browse` widens it, and it goes through
    // `openNotesBrowser`, which sets the scope after this and is not on this path.
    if(tab==='notes')setNotesAllProjects(false)
    setMainMenuOpen(false);setProjectMenu(null);setContextMenu(null)
  }
  /** Same, but never toggling shut. A menu row or chip that names a surface ("Browse
   *  files…", "Notes…") has already said "show me this"; closing the drawer on
   *  it is perverse, and worse when the click also switched Project — the panel would
   *  vanish instead of retargeting. */
  const openDrawerTab=(tab:DrawerTabId,targetProject=projectId)=>{
    selectDrawerTab(tab,targetProject)
    setClipboardOpen(true,targetProject)
    setMainMenuOpen(false);setProjectMenu(null);setContextMenu(null)
  }
  /** Open Actions as a momentary tool without changing this Project's saved tab. */
  const peekActions=()=>{
    setTransientDrawer({projectId,tab:'actions'})
    setClipboardOpen(true)
    setMainMenuOpen(false);setProjectMenu(null);setContextMenu(null)
  }
  const commitDrawerLayout=(candidate:DrawerLayout,focusedTab?:DrawerTabId,targetProject=projectId)=>{
    const normalized=normalizeDrawerLayout(candidate)
    if(serializeDrawerLayout(normalized)===serializeDrawerLayout(drawerLayoutRef.current)){
      if(focusedTab)selectDrawerTab(focusedTab,targetProject)
      return
    }
    drawerLayoutRef.current=normalized
    setDrawerLayoutState(normalized)
    storeDrawerValue(DRAWER_LAYOUT_KEY,serializeDrawerLayout(normalized))
    setDrawerProjectPresentations(current=>{
      let updated=reconcileDrawerProjectPresentations(current,normalized)
      if(focusedTab&&targetProject){
        const active=drawerProjectPresentationFor(updated,targetProject,normalized)
        updated=setDrawerProjectPresentation(updated,targetProject,activateDrawerTab(active,normalized,focusedTab),normalized)
      }
      storeDrawerValue(DRAWER_PROJECT_PRESENTATIONS_KEY,serializeDrawerProjectPresentations(updated,normalized))
      return updated
    })
    setUnscopedDrawerPresentation(current=>{
      const normalizedPresentation=normalizeDrawerProjectPresentation(current,normalized,current.focused_tab)
      return focusedTab&&!targetProject?activateDrawerTab(normalizedPresentation,normalized,focusedTab):normalizedPresentation
    })
  }
  const resetDrawerArrangement=()=>{
    setMainMenuOpen(false)
    commitDrawerLayout(resetDrawerLayout(),drawerTabId)
    setDrawerAnnouncement('Side panel layout reset')
  }
  // Serialize both new stores before removing either legacy key. An interrupted migration can
  // therefore retry without losing the former selected tab or flat order.
  useEffect(()=>{
    if(!drawerMigrationPending.current||!drawerLegacySettingsReady)return
    if(legacyDrawerTab.current&&!projectId)return
    setDrawerProjectPresentations(current=>{
      let updated=current
      if(projectId&&legacyDrawerTab.current&&!current[projectId]){
        const base=drawerProjectPresentationFor(current,projectId,drawerLayoutRef.current)
        // Mirrors `migratedTabId` in drawerLayout: this legacy `mux.drawer.tab.v1`
        // seed bypasses that helper, so the same forward-maps apply here — the
        // retired `commands`/`prompts` (→ actions) and Phase 7.10's `timeline` (→ insight).
        const legacyRaw=legacyDrawerTab.current
        const legacyValue=legacyRaw==='commands'||legacyRaw==='prompts'?'actions':legacyRaw==='timeline'?'insight':legacyRaw
        const legacy=DRAWER_TABS.some(tab=>tab.id===legacyValue)?legacyValue as DrawerTabId:null
        if(legacy)updated=setDrawerProjectPresentation(current,projectId,activateDrawerTab(base,drawerLayoutRef.current,legacy),drawerLayoutRef.current)
      }
      try{
        localStorage.setItem(DRAWER_LAYOUT_KEY,serializeDrawerLayout(drawerLayoutRef.current))
        localStorage.setItem(DRAWER_PROJECT_PRESENTATIONS_KEY,serializeDrawerProjectPresentations(updated,drawerLayoutRef.current))
        localStorage.removeItem(DRAWER_PROJECT_STATE_KEY)
        localStorage.removeItem(DRAWER_TAB_KEY)
        drawerMigrationPending.current=false
        legacyDrawerTab.current=null
      }catch(cause){setError(`Side panel migration could not be saved: ${cause instanceof Error?cause.message:String(cause)}`)}
      return updated
    })
  },[projectId,drawerLegacySettingsReady])
  useEffect(()=>{localStorage.setItem(DRAWER_NOTE_KEY,serializeDrawerNotes(drawerNotes))},[drawerNotes])
  /** A pane placement of the selected drawer note closes the drawer, ending its temporary
   * editor ownership without erasing the remembered Notes sub-tab. */
  const releaseIfDrawerHolds=(targetProject:string,resourceId:string)=>{
    if(drawerNoteFor(drawerNotes,targetProject)===resourceId)setClipboardOpen(false)
  }
  // A deleted Project must not keep a slot in device-local storage forever. Guarded on a
  // non-empty list because `projects` is empty until the first load answers, and pruning
  // against that would drop every claim on boot.
  useEffect(()=>{
    if(!projects.length)return
    setDrawerNotes(current=>pruneDrawerNotes(current,projects.map(project=>project.id)))
    setDrawerProjectPresentations(current=>{
      const updated=pruneDrawerProjectPresentations(current,projects.map(project=>project.id))
      if(updated!==current)storeDrawerValue(DRAWER_PROJECT_PRESENTATIONS_KEY,serializeDrawerProjectPresentations(updated,drawerLayoutRef.current))
      return updated
    })
  },[projects])
  const persistDrawerWidth=(value:number,maximum=Number.POSITIVE_INFINITY)=>{
    const next=clampDrawerWidth(value,maximum)
    setDrawerWidth(next);storeDrawerValue(DRAWER_WIDTH_KEY,String(Math.round(next)))
  }
  /** Drag within a pane rail, join another pane, or split on one of its four body edges. */
  const beginDrawerTabDrag=(event:JSX.TargetedPointerEvent<HTMLElement>,id:DrawerTabId)=>{
    beginPointerDrag(event,drawerTab(id).label,`drawer-tab:${id}`,
      ()=>{
        cancelLongPress()
        dragDrawerBaseRef.current=drawerLayoutRef.current
        dragDrawerLayoutRef.current=drawerLayoutRef.current
        dragDrawerTargetRef.current=null
        setDragDrawerTab(id)
      },
      pointer=>{
        const base=dragDrawerBaseRef.current
        const hit=document.elementFromPoint(pointer.clientX,pointer.clientY) as HTMLElement|null
        const pane=hit?.closest<HTMLElement>('.drawer-pane[data-drawer-stack-id]')||null
        if(!base||!pane){dragDrawerLayoutRef.current=null;dragDrawerTargetRef.current=null;showPointerDropIndicator(null);return}
        const stackId=pane.dataset.drawerStackId||''
        const targetStack=drawerStacks(base).find(stack=>stack.id===stackId)
        if(!targetStack){dragDrawerLayoutRef.current=null;dragDrawerTargetRef.current=null;showPointerDropIndicator(null);return}
        const rail=hit?.closest<HTMLElement>('.drawer-tabs[data-drawer-stack-id]')
          ||hit?.closest<HTMLElement>('.drawer-tabs-rail')?.querySelector<HTMLElement>('.drawer-tabs[data-drawer-stack-id]')
          ||null
        if(rail){
          const buttons=Array.from(rail.querySelectorAll<HTMLElement>(':scope > button[data-reorder-id]')).filter(button=>button.dataset.reorderId!==id)
          let index=buttons.length
          for(let position=0;position<buttons.length;position+=1){
            const bounds=buttons[position].getBoundingClientRect()
            if(pointer.clientX<bounds.left+bounds.width/2){index=position;break}
          }
          const indicator=buttons[Math.min(index,Math.max(0,buttons.length-1))]||rail
          const side=index>=buttons.length?'after':'before'
          dragDrawerLayoutRef.current=moveDrawerTabToStack(base,id,stackId,index)
          dragDrawerTargetRef.current={stackId,kind:'join'}
          showPointerDropIndicator(indicator,`insert-${side}`)
          return
        }
        const bounds=pane.getBoundingClientRect()
        const x=(pointer.clientX-bounds.left)/Math.max(1,bounds.width)
        const y=(pointer.clientY-bounds.top)/Math.max(1,bounds.height)
        let edge:DrawerEdge|null=null
        const nearest=Math.min(x,1-x,y,1-y)
        if(nearest<=0.24)edge=nearest===x?'left':nearest===1-x?'right':nearest===y?'top':'bottom'
        dragDrawerLayoutRef.current=edge
          ?moveDrawerTabToSplit(base,id,stackId,edge)
          :moveDrawerTabToStack(base,id,stackId,targetStack.tabs.length)
        dragDrawerTargetRef.current={stackId,kind:edge?'split':'join',edge:edge||undefined}
        showPointerDropIndicator(pane,edge?`split-${edge}`:'join')
      },
      ()=>{
        const next=dragDrawerLayoutRef.current,target=dragDrawerTargetRef.current
        dragDrawerBaseRef.current=null;dragDrawerLayoutRef.current=null;dragDrawerTargetRef.current=null;setDragDrawerTab(null)
        if(next){commitDrawerLayout(next,id);setDrawerAnnouncement(`${drawerTab(id).label} ${target?.kind==='split'?`split ${target.edge}`:'moved'}`)}
      },
      ()=>{dragDrawerBaseRef.current=null;dragDrawerLayoutRef.current=null;dragDrawerTargetRef.current=null;setDragDrawerTab(null)},
    )
  }
  // Mobile flattens the drawer tree to one projected rail (see UtilityDrawer's `mobileStack`),
  // so a reorder there lands on whichever real stack holds the target tab, at the aimed slot.
  const commitMobileDrawerOrder=(id:DrawerTabId,target:ReorderTarget)=>{
    const base=drawerLayoutRef.current
    const targetStack=drawerStackForTab(base,target.id as DrawerTabId)
    if(!targetStack)return
    const without=targetStack.tabs.filter(tab=>tab!==id)
    const at=without.indexOf(target.id as DrawerTabId)
    if(at<0)return
    const next=moveDrawerTabToStack(base,id,targetStack.id,at+(target.side==='after'?1:0))
    if(next!==base)commitDrawerLayout(next,id)
  }
  const beginMobileDrawerTabDrag=(event:JSX.TargetedPointerEvent<HTMLElement>,id:DrawerTabId)=>{
    const rail=event.currentTarget.closest<HTMLElement>('.drawer-tabs')
    let target:ReorderTarget|null=null,latestPointer:{clientX:number;clientY:number}|null=null,scrollFrame:number|null=null
    const preview=(pointer:{clientX:number;clientY:number})=>{
      if(!rail){target=null;showPointerDropIndicator(null);return}
      const next=reorderTargetFromContainer(rail,id,'horizontal',pointer.clientX)
      target=next
      const element=next?Array.from(rail.querySelectorAll<HTMLElement>(':scope > [data-reorder-id]')).find(item=>item.dataset.reorderId===next.id)||null:null
      showPointerDropIndicator(element,next?`insert-${next.side}`:undefined)
    }
    const stopAutoScroll=()=>{latestPointer=null;if(scrollFrame!==null)window.cancelAnimationFrame(scrollFrame);scrollFrame=null}
    const autoScroll=()=>{
      scrollFrame=null
      if(!rail||!latestPointer)return
      const box=rail.getBoundingClientRect()
      const delta=edgeAutoScrollDelta(latestPointer.clientX,box.left,box.right)
      if(delta!==0){const before=rail.scrollLeft;rail.scrollLeft+=delta;if(rail.scrollLeft!==before)preview(latestPointer)}
      scrollFrame=window.requestAnimationFrame(autoScroll)
    }
    beginPointerDrag(event,drawerTab(id).label,`drawer-mtab:${id}`,
      ()=>{cancelLongPress();setDrawerDisplayMenu(null);if(mobileWorkspace)navigator.vibrate?.(15)},
      pointer=>{latestPointer={clientX:pointer.clientX,clientY:pointer.clientY};preview(pointer);if(scrollFrame===null)scrollFrame=window.requestAnimationFrame(autoScroll)},
      ()=>{stopAutoScroll();const chosen=target;target=null;showPointerDropIndicator(null);if(chosen&&chosen.id!==id)commitMobileDrawerOrder(id,chosen)},
      ()=>{stopAutoScroll();target=null;showPointerDropIndicator(null)},
      MOBILE_HOLD_MOVE_DRAG,
    )
  }
  // Mirrors the sidebar resizer: dragging left widens the dock, while crossing its collapse
  // threshold closes it. The transient override keeps that reversible within the same drag
  // without writing each threshold crossing to the active Project's stored presentation.
  const beginDrawerResize=(event:PointerEvent)=>{
    event.preventDefault()
    const startX=event.clientX,startWidth=renderedDrawerWidth,storedWidth=drawerWidth
    let dragOpen=true,lastRawWidth=startWidth
    const maximum=()=>drawerMaximumWidth(
      window.innerWidth,
      sidebarCollapsed?SIDEBAR_COLLAPSED_WIDTH:sidebarWidth+SIDEBAR_RESIZER_WIDTH,
      utilityRailWidth,
    )
    const preview=(rawWidth:number)=>{
      lastRawWidth=rawWidth
      dragOpen=!dragCollapsedAtWidth(rawWidth,!dragOpen,DRAWER_COLLAPSE_WIDTH,DRAWER_REOPEN_WIDTH)
      setDrawerResizeOpen(dragOpen)
      if(dragOpen)setDrawerWidth(clampDrawerWidth(rawWidth,maximum()))
    }
    document.body.classList.add('sidebar-resizing')
    const move=(pointer:PointerEvent)=>preview(startWidth-(pointer.clientX-startX))
    // pointercancel too: on touch, a cancelled drag fires only that, and without
    // it the pointermove listener and the `sidebar-resizing` body class both
    // survive until some unrelated pointerup happens elsewhere.
    const stop=(pointer:PointerEvent)=>{
      if(pointer.type!=='pointercancel')preview(startWidth-(pointer.clientX-startX))
      if(dragOpen)persistDrawerWidth(lastRawWidth,maximum())
      else setDrawerWidth(storedWidth)
      setDrawerResizeOpen(null)
      setClipboardOpen(dragOpen)
      document.body.classList.remove('sidebar-resizing')
      window.removeEventListener('pointermove',move)
      window.removeEventListener('pointerup',stop)
      window.removeEventListener('pointercancel',stop)
    }
    window.addEventListener('pointermove',move)
    window.addEventListener('pointerup',stop,{once:true})
    window.addEventListener('pointercancel',stop,{once:true})
  }
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus | null>(null)
  const [profiles, setProfiles] = useState<LaunchProfile[]>([])
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

  /** The insertion preview for the sidebar's list drags: an outline of the dragged row, at the
   *  gap it would land in, carrying its name. A line between two rows says where the pointer is;
   *  this says what the list will look like, which is the question being asked while a row is in
   *  the air. It is a body-level element positioned over the list rather than a placeholder
   *  spliced into it, because the sidebar re-renders on every fleet event and a foreign node
   *  inside a Preact-managed parent does not survive that diff. */
  const dropSlotRef=useRef<HTMLDivElement|null>(null)
  const showDropSlot=(slot:{left:number;width:number;gap:number;height:number;label:string}|null)=>{
    if(!slot){dropSlotRef.current?.remove();dropSlotRef.current=null;return}
    let element=dropSlotRef.current
    if(!element){
      element=document.createElement('div')
      element.className='mux-drop-slot'
      document.body.appendChild(element)
      dropSlotRef.current=element
    }
    if(element.textContent!==slot.label)element.textContent=slot.label
    element.style.width=`${Math.round(slot.width)}px`
    element.style.height=`${Math.round(slot.height)}px`
    // Centred on the gap rather than resting below it: a row cannot occupy a zero-height seam,
    // and straddling it reads as "this pushes in here" without claiming either neighbour.
    element.style.transform=`translate3d(${Math.round(slot.left)}px,${Math.round(slot.gap-slot.height/2)}px,0)`
  }
  /** The slot for landing beside `row`, from the geometry of the row itself: it is indented
   *  exactly as far as its own list nesting, which is what makes the preview legible inside a
   *  pane cluster. `height` is the dragged row's, since that is what lands there. */
  const dropSlotForRow=(row:HTMLElement,side:DropSide,height:number,label:string)=>{
    const box=row.getBoundingClientRect()
    showDropSlot({left:box.left,width:box.width,gap:side==='before'?box.top:box.bottom,height,label})
  }

  const beginPointerDrag=(
    event:JSX.TargetedPointerEvent<HTMLElement>,label:string,identity:string,
    onStart:()=>void,onMove:(event:PointerEvent)=>void,onDrop:()=>void,onCancel:()=>void,
    activation:PointerDragActivation=POINTER_MOVE_DRAG,
    // Mobile `hold` lift model: the row lifts on a stationary hold (below), and if it is then
    // released WITHOUT being dragged, this opens the context menu on that release. The menu
    // never opens while the finger is down — on a vertical list it lands on the drag path and
    // blocks the very drag the hold became (the "hold too long and it won't drag" bug) — and
    // activating on stillness (not on a move) keeps the swipe recognizer from racing the drag.
    onInertRelease?:()=>void,
  )=>{
    if(event.button!==0||!event.isPrimary)return
    const source=event.currentTarget
    const pointerId=event.pointerId,startX=event.clientX,startY=event.clientY
    let active=false,done=false,ghost:HTMLDivElement|null=null,activationTimer:number|null=null
    // Set once a lifted drag has actually travelled: it decides release-in-place (menu) from a
    // real reorder, and it is measured from where the drag lifted, not from touch-down.
    let moved=false,activateX=startX,activateY=startY
    // `hold-move` only: set once the hold has settled, so the next move past slop drags
    // rather than scrolls. `hold` and `movement` never read it.
    let armed=false
    let latestX=startX,latestY=startY
    // Held from the moment the drag becomes real until it unwinds, so the mobile gesture
    // recognizer stops reading this finger: a tab dragged along a strip is the same motion
    // as a swipe, and only the drag knows which it is. Claimed at activation rather than at
    // pointer-down so a swipe that merely *starts* on a draggable tab still works.
    let releaseDragClaim:(()=>void)|null=null
    // Touch only, and the reason the hold-to-drag gesture works at all. `preventDefault` on a
    // *pointer* move does not stop a touch from scrolling — only `touch-action` and a cancelled
    // `touchmove` do — so without this the sidebar scrolled under the finger and the scroll
    // cancelled the pointer, which is exactly the shape of "the drag does nothing on a phone".
    // `touch-action:none` on the row is not the fix either: it would cost the sidebar its
    // scroll, since a row is most of what there is to put a finger on. Registered at
    // pointer-down (which precedes `touchstart`) so the sequence is main-thread from the start
    // and its moves stay cancelable; it only cancels once the drag is real, leaving an ordinary
    // scroll that merely began on a row untouched.
    const blockTouchScroll=(touch:TouchEvent)=>{
      if(!touch.cancelable)return
      if(active){touch.preventDefault();return}
      // Before the drag is real, a hold still has to keep the browser from starting a
      // scroll off the finger's micro-jitter: once a scroll latches, it ignores every
      // later `preventDefault` and cancels the pointer, so the drag would do nothing
      // unless yanked — the exact "I feel the buzz but it won't drag unless I go fast"
      // failure. Cancel touchmoves inside the hold slop always. For `hold-move`, once the
      // hold has armed, keep cancelling PAST the slop too: that first past-slop move is
      // the drag itself starting, and if the browser latches a scroll on it before the
      // pointer handler captures, the drag dies on `pointercancel` — the residual sidebar
      // failure after the within-slop fix. Only a `hold-move` that has not armed yet, or a
      // plain `hold`, treats past-slop as the scroll it releases to.
      if(activation.mode==='movement'||touch.touches.length!==1)return
      const point=touch.touches[0]
      const within=Math.hypot(point.clientX-startX,point.clientY-startY)<=activation.slop
      if(within||(activation.mode==='hold-move'&&armed))touch.preventDefault()
    }
    if(event.pointerType==='touch')window.addEventListener('touchmove',blockTouchScroll,{passive:false})
    const cleanup=()=>{
      if(activePointerDragCancelRef.current===cancel)activePointerDragCancelRef.current=null
      releaseDragClaim?.();releaseDragClaim=null
      window.removeEventListener('pointermove',move)
      window.removeEventListener('pointerup',up)
      window.removeEventListener('pointercancel',cancelPointer)
      window.removeEventListener('touchmove',blockTouchScroll)
      window.removeEventListener('blur',cancel)
      window.removeEventListener('keydown',key,true)
      source.removeEventListener('lostpointercapture',lostCapture)
      if(source.hasPointerCapture(pointerId))source.releasePointerCapture(pointerId)
      if(activationTimer!==null)window.clearTimeout(activationTimer)
      activationTimer=null
      document.body.classList.remove('workspace-pointer-dragging')
      source.classList.remove('dragging')
      showPointerDropIndicator(null)
      showDropSlot(null)
      ghost?.remove()
    }
    const finish=(commit:boolean)=>{
      if(done)return
      done=true
      // A lifted-but-never-dragged release opens the menu (release-in-place) instead of
      // committing a reorder. Only the mobile `hold` lift model does this — a movement-mode
      // (desktop) drag has to move to exist, so a small one is a reorder, never a menu.
      const inertMenu=active&&commit&&!moved&&!!onInertRelease&&activation.mode==='hold'
      cleanup()
      if(!active)return
      window.setTimeout(()=>{if(suppressDragClickRef.current===identity)suppressDragClickRef.current=null},0)
      // Unwind the drag's own state (auto-scroll frame, drop preview) without committing, then
      // open the menu. `onCancel` is the no-commit teardown every caller already provides.
      if(inertMenu){onCancel();onInertRelease?.();return}
      if(commit)onDrop();else onCancel()
    }
    const activate=(clientX:number,clientY:number)=>{
      if(active||done)return
      active=true
      activateX=clientX;activateY=clientY
      if(activationTimer!==null)window.clearTimeout(activationTimer)
      activationTimer=null
      releaseDragClaim=claimPointerDrag();suppressDragClickRef.current=identity
      document.body.classList.add('workspace-pointer-dragging');source.classList.add('dragging')
      try{source.setPointerCapture(pointerId)}catch{finish(false);return}
      ghost=document.createElement('div');ghost.className='mux-pointer-drag-ghost';ghost.textContent=label;document.body.appendChild(ghost)
      ghost.style.transform=`translate3d(${clientX+14}px,${clientY+12}px,0)`
      onStart()
    }
    const move=(pointer:PointerEvent)=>{
      if(pointer.pointerId!==pointerId)return
      latestX=pointer.clientX;latestY=pointer.clientY
      if(!active){
        const distance=Math.hypot(pointer.clientX-startX,pointer.clientY-startY)
        if(activation.mode==='hold-move'){
          // Inside slop: still the hold settling. Past slop before the hold armed: a
          // scroll this drag never owned. Past slop once armed: the drag begins.
          if(distance<=activation.slop)return
          if(!armed){finish(false);return}
          activate(pointer.clientX,pointer.clientY)
        }else{
          const decision=pointerDragMoveDecision(activation,distance)
          if(decision==='wait')return
          if(decision==='cancel'){finish(false);return}
          activate(pointer.clientX,pointer.clientY)
        }
      }
      if(!moved&&Math.hypot(pointer.clientX-activateX,pointer.clientY-activateY)>8)moved=true
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
    activePointerDragCancelRef.current=cancel
    // `hold` lifts the row on a stationary hold (the mobile reorder model): one buzz, no move
    // to time, and because it claims the pointer before any movement exists, the swipe
    // recognizer never gets a gesture to misread. `hold-move` instead only arms and waits for a
    // move (kept for the drawer, whose touch-action:none tabs never scroll under the hold).
    if(activation.mode==='hold')activationTimer=window.setTimeout(()=>activate(latestX,latestY),activation.delayMs)
    else if(activation.mode==='hold-move')activationTimer=window.setTimeout(()=>{armed=true;activationTimer=null},activation.delayMs)
  }
  const startupOrigins=useRef<Record<string,number>>({})
  const pendingSpawns=useRef<Record<string,PendingSpawn>>({})
  // Sessions this client has already taken off screen while their DELETE finishes.
  // See sessionKills.ts for why the fleet and layout reconcilers both have to honour it.
  const pendingKills=useRef<KillTombstones>({})
  const spawning = useRef(false)
  const relaunching = useRef(false)
  const longPressTimer = useRef<number | null>(null)
  const longPressOrigin = useRef<{pointerId:number;x:number;y:number}|null>(null)
  const runHeldRef = useRef(false)
  const mobileTabHeldRef = useRef(false)
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
  // Highest durable event sequence this tab has covered. Control frames can advance
  // it without transferring audit-only payloads that browser state does not consume.
  const lastEventSeq = useRef(0)
  const requestedView = useRef(parseViewPreference(location.search))
  const focusMemory = useRef(parseFocusMemory(localStorage.getItem('mux.focus.v1')))
  const [focusHydrated,setFocusHydrated]=useState(false)
  sessionsRef.current=sessions
  projectsRef.current=projects
  useEffect(()=>{
    const onProjectRecency=(event:Event)=>{
      const detail=(event as CustomEvent<ProjectRecencyEventDetail>).detail
      const session=sessionsRef.current.find(item=>item.id===detail?.sessionId)
      if(session)markProjectRecent(session.project_id,detail.reason)
    }
    window.addEventListener(PROJECT_RECENCY_EVENT,onProjectRecency)
    return()=>window.removeEventListener(PROJECT_RECENCY_EVENT,onProjectRecency)
  },[])
  // Clipboard capture runs from module-level hooks installed at boot, so it reads
  // the focused session / device / on-off state through refs rather than props.
  const clipboardContextRef=useRef({activeId:null as string|null,projectId:'',enabled:true})
  clipboardContextRef.current={activeId,projectId,enabled:clipboardEnabled}

  const cancelLongPress = () => {
    if (longPressTimer.current !== null) window.clearTimeout(longPressTimer.current)
    longPressTimer.current = null
    longPressOrigin.current = null
  }

  const moveLongPress = (event:JSX.TargetedPointerEvent<HTMLElement>) => {
    const origin=longPressOrigin.current
    if(!origin||origin.pointerId!==event.pointerId)return
    if(Math.hypot(event.clientX-origin.x,event.clientY-origin.y)>8)cancelLongPress()
  }

  const setDesktopSidebarCollapsed=(next:boolean)=>{
    localStorage.setItem('mux.sidebar.collapsed.v1',String(next))
    setSidebarCollapsed(next)
  }
  const toggleSidebar=()=>setSidebarCollapsed(value=>{
    const next=!value
    localStorage.setItem('mux.sidebar.collapsed.v1',String(next))
    return next
  })
  const setNavigationSidebarOpen=(open:boolean)=>{
    const state=navigationSidebarCommandState(mobileWorkspace,open)
    if(state.mobileOpen!==null)setSidebarOpen(state.mobileOpen)
    if(state.desktopCollapsed!==null)setDesktopSidebarCollapsed(state.desktopCollapsed)
  }
  const persistSidebarWidth=(value:number)=>{
    const next=clampSidebarWidth(value)
    setSidebarWidth(next);localStorage.setItem('mux.sidebar.width.v1',String(Math.round(next)))
  }

  const beginSidebarResize=(event:JSX.TargetedPointerEvent<HTMLDivElement>)=>{
    if(sidebarCollapsed)return
    event.preventDefault()
    const startX=event.clientX,startWidth=sidebarWidth
    let dragCollapsed=false,lastRawWidth=startWidth
    const preview=(rawWidth:number)=>{
      lastRawWidth=rawWidth
      dragCollapsed=dragCollapsedAtWidth(rawWidth,dragCollapsed,SIDEBAR_COLLAPSE_WIDTH,SIDEBAR_REOPEN_WIDTH)
      setSidebarCollapsed(dragCollapsed)
      if(!dragCollapsed)setSidebarWidth(clampSidebarWidth(rawWidth))
    }
    document.body.classList.add('sidebar-resizing')
    const move=(pointer:PointerEvent)=>preview(startWidth+pointer.clientX-startX)
    const stop=(pointer:PointerEvent)=>{
      if(pointer.type!=='pointercancel')preview(startWidth+pointer.clientX-startX)
      if(dragCollapsed)setSidebarWidth(startWidth);else persistSidebarWidth(lastRawWidth)
      localStorage.setItem('mux.sidebar.collapsed.v1',String(dragCollapsed))
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
    longPressOrigin.current={pointerId:event.pointerId,x:clientX,y:clientY}
    longPressTimer.current=window.setTimeout(()=>{navigator.vibrate?.(20);open(clientX,clientY);longPressTimer.current=null},550)
  }

  // Every enabled launch profile, shells and agents together. Surfaces that mean
  // "a terminal" filter on `backend` themselves rather than being handed a
  // pre-filtered list, because the Run menu needs the agent ones from the same load.
  const loadProfiles = () => api<{default_profile_id:string;profiles:LaunchProfile[];detected:LaunchProfile[]}>('GET','/api/profiles').then(result => { const combined=[...result.profiles,...result.detected.filter(profile=>!result.profiles.some(item=>item.id===profile.id))];setProfiles(combined.filter(profile=>profile.enabled));setDefaultProfile(result.default_profile_id);setLauncherProfile(current=>current||result.default_profile_id) })

  const loadNotifications = async (announce=false) => {
    const next=await api<NotificationData>('GET','/api/notifications')
    const fresh=next.notifications.filter(item=>!notificationIds.current.has(`legacy:${item.delivery_id}`))
    const freshAutomation=(next.automation||[]).filter(item=>!notificationIds.current.has(`automation:${item.id}`))
    notificationIds.current=new Set([...next.notifications.map(item=>`legacy:${item.delivery_id}`),...(next.automation||[]).map(item=>`automation:${item.id}`)])
    setNotificationData(next)
    if(announce&&(fresh.length||freshAutomation.length)){setNotificationUnread(count=>count+fresh.length+freshAutomation.length);const latest=fresh[fresh.length-1];const observer=freshAutomation[freshAutomation.length-1];setNotificationToast(observer?{ts:observer.created_at,channel:'ui',delivery_id:observer.id,session_id:observer.session_id,session_name:'automation',type:observer.kind}:latest)}
  }

  // Safety net under the kill request's own deadline. The request always settles and
  // clears its tombstone, but a phone that freezes the tab mid-flight can lose the
  // continuation entirely (see liveness.ts), and a tombstone with nothing behind it
  // hides a session that is still running - the one failure worse than a slow close.
  const expireStaleKills = () => {
    const expired = expiredKillIds(pendingKills.current, Date.now())
    if (!expired.length) return
    for (const sessionId of expired) delete pendingKills.current[sessionId]
    setError('A session close never reported back; restoring whatever the daemon still has.')
  }

  const refresh = (): Promise<void> => {
    if (refreshInFlight.current) {
      refreshQueued.current = true
      return refreshInFlight.current
    }
    const operation = (async () => {
      try {
      const [nextSessions, nextProjects, nextPreviews, nextGroups, nextHarnesses] = await Promise.all([
        api<Session[]>('GET', '/api/sessions'), api<Project[]>('GET', '/api/projects'),
        api<{items:Preview[]}>('GET', '/api/previews'),
        api<ProjectGroup[]>('GET','/api/project-groups'),
        api<HarnessRegistryPayload>('GET','/api/harnesses'),
      ])
      installHarnessRegistry(nextHarnesses)
      setHarnessRegistryRevision(current=>current+1)
      // The daemon still reports a session being killed as live for the whole
      // teardown window, so every consumer of this GET has to see the fleet the
      // operator sees - the row, the layout leaf, and the live set they are reconciled against.
      expireStaleKills()
      const visibleSessions=applyKillTombstones(nextSessions,pendingKills.current)
      setSessions(current => {
        const optimistic=current.filter(session=>session.pending&&pendingSpawns.current[session.id]&&!pendingSpawns.current[session.id].resolvedId)
        return reconcileSessionSnapshots(current,visibleSessions,optimistic)
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
      setRegistryLoaded(true)
      setLayoutMap(current => {
        const next = { ...current }
        const live = new Set(visibleSessions.filter(session => !['exited', 'crashed'].includes(session.state)).map(session => session.id))
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
          for(const [pendingId,pending] of Object.entries(pendingSpawns.current)){
            if(pending.projectId!==project.id||!pending.placement)continue
            next[project.id]=placePendingTerminal(next[project.id],pending.resolvedId||pendingId,pending.placement,false)
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
    drawer_tab_display?:'icon'|'title'
    utility_rail_display?:'icon'|'title'
  }&Record<string,unknown>

  // Scale previews have to update both authorities in the browser: the root custom
  // property used by chrome and the numeric prop xterm receives. Settings used to call
  // `applyUiScale` by itself, which made its live preview stop at the terminal boundary.
  const previewUiScaleConfig = (config:Record<string,unknown>):UiScale => {
    uiScaleConfigRef.current=config
    const next=applyUiScale(config)
    uiScaleRef.current=next
    setUiScale(next)
    return next
  }

  const previewActiveUiScale = (scale:UiScale):void => {
    const config={
      ...(uiScaleConfigRef.current||{}),
      [uiScaleConfigKey(currentProfile())]:scale,
    }
    previewUiScaleConfig(config)
  }

  // One place that turns a daemon config into browser state. The boot path, the
  // Settings-close path, and the configuration_changed handler each applied a
  // *different subset*, so a renderer or scroll-sensitivity change made on
  // another device silently never reached this tab. `includeTheme` is the only
  // difference: theme is applied once at boot and by Settings itself.
  const applyConfig = (config:AppConfig, includeTheme:boolean) => {
    if (includeTheme) { configureCustomTheme(config.custom_theme); applyTheme(config.theme) }
    // Explicit harness enablement choices, so the launcher accessors filter on the
    // user's list. Detection (the descriptor `installed` flag) fills the rest.
    setHarnessEnablement(config.harness_enabled as Record<string,boolean>|undefined)
    // First-run harness panel, gated daemon-side so a choice made on one device does
    // not reappear on another. False (or a daemon predating the flag) shows it once.
    setHarnessSetupNeeded(config.harness_setup_complete===false)
    applyNoteEditorConfig(config)
    previewUiScaleConfig(config)
    setXtermScrollback(config.xterm_scrollback_lines)
    setTerminalRenderer(config.terminal_renderer)
    setClaudeMaxColumns(claudeMaxColumnsFrom(config))
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
    setOverlayBack(overlayBackEnabled(config))
    setClipboardEnabled(config.clipboard_history_enabled!==false)
    setDrawerTabDisplay(config.drawer_tab_display==='title'?'title':'icon')
    setUtilityRailDisplay(config.utility_rail_display==='title'?'title':'icon')
  }

  const loadConfig = (includeTheme:boolean) =>
    api<AppConfig>('GET','/api/config')
      .then(config=>applyConfig(config,includeTheme))
      .catch(()=>{})

  const scheduleUiScalePersist = (scale:UiScale):void => {
    const field=uiScaleConfigKey(currentProfile())
    const generation=++uiScalePersistGeneration.current
    if(uiScalePersistTimer.current!==null)window.clearTimeout(uiScalePersistTimer.current)
    uiScalePersistTimer.current=window.setTimeout(()=>{
      uiScalePersistTimer.current=null
      void api<AppConfig>('PATCH','/api/config',{[field]:scale}).then(config=>{
        if(generation===uiScalePersistGeneration.current)applyConfig(config,false)
      }).catch(cause=>{
        if(generation!==uiScalePersistGeneration.current)return
        setError(`UI scale could not be saved: ${cause instanceof Error?cause.message:String(cause)}`)
        void loadConfig(false)
      })
    },300)
  }

  const persistDrawerDisplay=async(surface:'tabs'|'rail',next:'icon'|'title')=>{
    const previous=surface==='tabs'?drawerTabDisplay:utilityRailDisplay
    setDrawerDisplayMenu(null)
    if(surface==='tabs')setDrawerTabDisplay(next)
    else setUtilityRailDisplay(next)
    try{
      const field=surface==='tabs'?'drawer_tab_display':'utility_rail_display'
      const config=await api<AppConfig>('PATCH','/api/config',{[field]:next})
      applyConfig(config,false)
    }catch(cause){
      if(surface==='tabs')setDrawerTabDisplay(previous)
      else setUtilityRailDisplay(previous)
      setError(cause instanceof Error?cause.message:String(cause))
    }
  }

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
    const timer = setInterval(tick, FLEET_SAFETY_REFRESH_MS)
    const keyTimer = setInterval(keyTick, KEYBINDING_SAFETY_REFRESH_MS)
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

  // Resource summaries and the Processes drawer reuse the daemon's cached sample,
  // so this poll adds no process enumeration. Preview classification happens in
  // the daemon; this raw fleet sample is never navigation state by itself.
  const loadProcesses = async () => {
    try {
      const snapshot = await api<FleetSnapshot>('GET','/api/processes?summary=1')
      setProcessFleet(snapshot)
    } catch { setProcessFleet(null) }
  }

  useEffect(() => {
    void loadProcesses()
    const tick = () => { if (!document.hidden) void loadProcesses() }
    const timer = setInterval(tick, PROCESS_SUMMARY_REFRESH_MS)
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
  useEffect(() => watchUiScaleProfile(scale=>{
    uiScaleRef.current=scale
    setUiScale(scale)
  }), [])

  // The state indicator's size is stored per device class for the same reason,
  // and is published as a custom property because the sidebar's gutter column,
  // stack thread, and row height are all derived from it rather than from the
  // glyph. Applied here rather than in the row component: it is one root-level
  // value, and every surface that draws an indicator must agree on it.
  useEffect(()=>{applySessionDotSize(rowConfig)},[rowConfig])
  useEffect(()=>watchSessionDotProfile(),[])

  // Browser-style UI scaling is captured before xterm, editors, command bindings, or
  // Chromium's page zoom see it. Plain wheel/key input and every non-exact modifier
  // combination continue down their ordinary paths. Settings keeps the result in its
  // draft; everywhere else the final step is persisted after a short gesture debounce.
  useEffect(() => {
    const wheelIntent=createUiScaleWheelIntent()
    const consume=(event:KeyboardEvent|WheelEvent)=>{
      event.preventDefault()
      event.stopImmediatePropagation()
    }
    const applyIntent=(intent:ReturnType<typeof uiScaleKeyboardIntent>)=>{
      if(!intent)return
      const current=uiScaleRef.current
      const next=uiScaleForIntent(current,intent)
      previewActiveUiScale(next)
      const limit=next===current&&intent!=='reset'?(intent==='increase'?' (maximum)':' (minimum)'):''
      showInteractionHud(`UI scale ${Math.round(next*100)}%${limit}`)
      if(next!==current&&!settingsOpen)scheduleUiScalePersist(next)
    }
    const onKey=(event:KeyboardEvent)=>{
      const intent=uiScaleKeyboardIntent(event)
      if(!intent)return
      consume(event)
      applyIntent(intent)
    }
    const onWheel=(event:WheelEvent)=>{
      if(!event.ctrlKey||event.altKey||event.metaKey||event.shiftKey)return
      consume(event)
      applyIntent(wheelIntent(event))
    }
    window.addEventListener('keydown',onKey,true)
    window.addEventListener('wheel',onWheel,{capture:true,passive:false})
    return()=>{
      window.removeEventListener('keydown',onKey,true)
      window.removeEventListener('wheel',onWheel,true)
    }
  },[settingsOpen])

  // Opening Settings turns an outstanding shortcut preview into ordinary draft state.
  // The panel will either save it with the rest of the draft or restore the saved config.
  useEffect(()=>{
    if(!settingsOpen||uiScalePersistTimer.current===null)return
    window.clearTimeout(uiScalePersistTimer.current)
    uiScalePersistTimer.current=null
    uiScalePersistGeneration.current+=1
  },[settingsOpen])

  useEffect(()=>()=>{
    if(uiScalePersistTimer.current!==null)window.clearTimeout(uiScalePersistTimer.current)
  },[])

  useEffect(() => {
    const viewport = window.visualViewport
    let lastInset = -1
    // The shell is the *layout* viewport, which `interactive-widget=resizes-visual` keeps at
    // full height while the keyboard is up. Sizing it from `visualViewport` instead is what
    // used to shrink every terminal when the keyboard opened — and shrinking an
    // alternate-screen PTY discards the rows that no longer fit, permanently. The keyboard
    // is now an inset the layout is slid up by, never a smaller layout.
    const updateAppHeight = () => {
      const layout = Math.round(window.innerHeight)
      const inset = softKeyboardInset(layout, Math.round(viewport?.height ?? layout))
      const root = document.documentElement
      root.style.setProperty('--app-height', `${layout}px`)
      root.style.setProperty('--keyboard-inset', `${inset}px`)
      // A class as well as the length, so the slide can be scoped to the keyboard being up.
      // A `translateY(0)` still makes an element a containing block for its `position:fixed`
      // descendants, which would silently re-anchor the drawer and sidebar overlays.
      root.classList.toggle('soft-keyboard-open', inset > 0)
      // Panes need this as state, not only as a length: a terminal shows a peek-at-the-top
      // control while the keyboard covers part of it. Published on change only, because the
      // keyboard fires resizes throughout its open animation.
      if (inset !== lastInset) {
        lastInset = inset
        // Remembered across sessions and reloads, because a pane that reserves the
        // keyboard's height has to know it *before* the keyboard opens — the first time it
        // asks, on a device that has never shown one, is exactly when no measurement exists.
        if (inset > 0) rememberSoftKeyboardInset(inset)
        window.dispatchEvent(new CustomEvent(SOFT_KEYBOARD_EVENT, { detail: inset }))
      }
      setViewportWidth(window.innerWidth)
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
      document.documentElement.style.removeProperty('--keyboard-inset')
      document.documentElement.classList.remove('soft-keyboard-open')
    }
  }, [])

  useEffect(() => {
    void loadSettings().then(()=>{
      // The former flat order lives in the asynchronous device-settings cache. Do not
      // persist the new default before that cache has had one chance to seed the layout.
      // A user interaction that creates the new key while this request is in flight wins.
      if(localStorage.getItem(DRAWER_LAYOUT_KEY)===null){
        const migrated=defaultDrawerLayout(normalizeDrawerTabOrder(loadDrawerTabOrder()))
        drawerLayoutRef.current=migrated
        setDrawerLayoutState(migrated)
        setDrawerProjectPresentations(current=>reconcileDrawerProjectPresentations(current,migrated))
        setUnscopedDrawerPresentation(current=>normalizeDrawerProjectPresentation(current,migrated,current.focused_tab))
      }
      setDrawerLegacySettingsReady(true)
    })
    void initPush()
  }, [])

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
      // Reconnects resume from the last durable sequence covered. A new tab has no
      // cursor and receives a watermark after its ordinary REST bootstrap.
      const hadCursor = lastEventSeq.current > 0
      const resume = hadCursor ? `?after_seq=${lastEventSeq.current}` : ''
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
        window.dispatchEvent(new CustomEvent('mux:events-connected',{detail:{resumed:hadCursor}}))
        if (hadCursor) {
          // Catch-up events are bounded and may omit state-independent audit hooks.
          // Refresh each global cache once instead of once per replayed event.
          refreshSettings()
          void loadConfig(false)
          void loadNotifications(true)
        }
      }
      next.onerror = () => { if (socket === next) next.close() }
      next.onmessage = message => {
        if (socket !== next) return
        try {
          const event = JSON.parse(String(message.data))
          if (event.type === 'events_hello') {
            setUiUpdateAvailable(uiUpdateRequired(loadedBuildId.current, event.ui_build_id))
            return
          }
          if (event.type === 'events_ready' || event.type === 'events_cursor' || event.type === 'events_gap') {
            const sequence = Number(event.sequence)
            if (Number.isSafeInteger(sequence) && sequence >= 0 && sequence > lastEventSeq.current) lastEventSeq.current = sequence
            // A cold watermark closes the subscribe/snapshot race. A wide reconnect
            // gap likewise needs one authoritative snapshot. Cursor-only frames mean
            // the skipped records were audit hooks and require no state refresh.
            if (event.type !== 'events_cursor') queueRefresh()
            return
          }
          if (eventRequiresFleetRefresh(event.type)) queueRefresh()
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
          // The drawer's transcript reader refreshes on this rather than on a timer.
          // Replayed turns are re-broadcast on purpose: a reconnect is exactly when the
          // reader's copy is stalest, and a reread is cheap and idempotent.
          if (event.type === 'turn_ended') window.dispatchEvent(new CustomEvent(TURN_ENDED_EVENT, { detail: { sessionId: event.session_id } }))
          if (event.type === 'transcript_message') window.dispatchEvent(new CustomEvent(TRANSCRIPT_CHANGED_EVENT, { detail: { sessionId: event.session_id } }))
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
            if(!isReplay&&event.type==='voice_clip_ready'&&event.payload?.trigger!=='auto'&&clipId&&event.payload?.stream_id){
              enqueueRequestedStreamClip(clipId,String(event.payload.stream_id),Number(event.payload.segment_index||0),Number(event.payload.segment_count||1))
            }
          }
          if (!isReplay && event.type === 'settings_changed') refreshSettings()
          // Another device (or another tab) changed the ring; an open picker refetches.
          if (event.type === 'clipboard_changed') window.dispatchEvent(new CustomEvent(CLIPBOARD_CHANGED_EVENT))
          if (!isReplay && event.type === 'configuration_changed') {
            void api<VoiceStatus>('GET','/api/voice').then(setVoiceStatus).catch(()=>{})
            // A change made from another device (or by editing the config file)
            // has to reach this tab's copy of *every* config-derived setting, not
            // the subset this handler happened to list.
            void loadConfig(false)
          }
          if(event.type==='project_files_changed')window.dispatchEvent(new CustomEvent('mux:project-files-changed',{detail:{projectId:event.payload?.project_id,paths:event.payload?.paths||[]}}))
          if(event.type==='project_used')applyProjectUse(String(event.payload?.project_id||''),Number(event.payload?.last_used_at||0))
          if(event.type==='agent_context_changed')window.dispatchEvent(new CustomEvent('mux:agent-context-changed',{detail:{projectId:event.payload?.project_id}}))
          // Queue tabs and pane chips live-update off these; payloads carry ids/counts only.
          if(event.type==='queue_updated'||event.type==='queue_delivery'){window.dispatchEvent(new CustomEvent('mux:queue-changed',{detail:{sessionId:event.session_id}}));refreshQueueSummary()}
          if(event.type==='spawn_request_drafted'||event.type==='spawn_request_decided')window.dispatchEvent(new CustomEvent('mux:queue-changed',{detail:{projectId:event.payload?.project_id}}))
          // The drawer's Git tab refetches its worktree list off this. Branch/dirty/upstream
          // already ride the session snapshots, so `git_changed` needs no payload here.
          if(event.type==='worktree_created'||event.type==='worktree_removed'||event.type==='git_changed'||event.type==='git_provenance_changed')window.dispatchEvent(new CustomEvent('mux:git-changed'))
          if(!isReplay&&event.type==='note_changed')window.dispatchEvent(new CustomEvent('mux:note-changed',{detail:{scope:event.payload?.scope==='global'?'global':'project',projectId:String(event.payload?.project_id||''),kind:event.payload?.scope==='global'?'global-note':'note',noteId:String(event.payload?.note_id||''),revision:String(event.payload?.revision||'')}}))
        } catch {
          // A malformed event cannot be classified safely. Keep the REST snapshot as
          // the recovery path, while well-formed telemetry events avoid that cost.
          queueRefresh()
        }
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

  useEffect(() => {
    if (!uiUpdateAvailable) return
    const reloadWhenHidden = () => {
      if (uiUpdateReloadReady(true, document.visibilityState)) location.reload()
    }
    reloadWhenHidden()
    document.addEventListener('visibilitychange', reloadWhenHidden)
    return () => document.removeEventListener('visibilitychange', reloadWhenHidden)
  }, [uiUpdateAvailable])

  useEffect(()=>{if(!notificationToast)return;const timer=window.setTimeout(()=>setNotificationToast(null),5000);return()=>clearTimeout(timer)},[notificationToast])

  // Keep the sidebar bell in step with the device-settings cache: a local toggle, a
  // remote edit replayed over the /events socket, or a device-class switch all land here.
  useEffect(()=>{
    const sync=()=>{
      setAlertsEnabled(alertPreferences().enabled)
      setRailVoiceRevision(value=>value+1)
    }
    window.addEventListener('mux:settings-changed',sync)
    return ()=>window.removeEventListener('mux:settings-changed',sync)
  },[])

  useEffect(()=>{
    const query=window.matchMedia('(max-width:760px)')
    // Responsive transitions never turn a remembered desktop column into an unsolicited
    // mobile overlay, and a formerly open overlay does not reappear after another transition.
    const changed=()=>{setMobileWorkspace(query.matches);setMobileDrawerOpen(false)}
    changed();query.addEventListener('change',changed)
    return()=>query.removeEventListener('change',changed)
  },[])

  const active = sessions.find(session => session.id === activeId)
  const attention = sessions.filter(session => session.state === 'awaiting').length
  const activeProject = projects.find(project => project.id === projectId)
  const orderedProjects = [...projects].sort((a,b)=>a.position-b.position||a.name.localeCompare(b.name)||a.id.localeCompare(b.id))
  const visibleProjects = orderedProjects.filter(project => project.sidebar_visible !== false)
  const orderedGroups=[...projectGroups].sort((a,b)=>a.position-b.position||a.name.localeCompare(b.name)||a.id.localeCompare(b.id))
  const recentProjectRanks=projectRecency(projects)
  const ungroupedProjects=sortProjects(
    visibleProjects.filter(project=>!project.group_id||!projectGroups.some(item=>item.id===project.group_id)),
    sidebarOrder.projectSort,
    recentProjectRanks,
  )
  const ungroupedProjectIds=ungroupedProjects.map(project=>project.id)
  // Every Group in manual order, including empty Groups. A drag only permutes the
  // rendered subset; folding that back into the full list keeps empty Groups where
  // their owner left them.
  const allBuckets=orderedGroups.map(group=>{
    const items=visibleProjects.filter(project=>project.group_id===group.id)
    // `visibleProjects` is already in manual order, which sortProjects treats as the
    // tie-break, so every mode falls back to what the user arranged by hand.
    return {id:group.id,name:group.name,items:sortProjects(items,sidebarOrder.projectSort,recentProjectRanks)}
  })
  // Groups sort by the same contract their contents do: manual order in, stable
  // sort out, so the arrangement underneath a sort is never lost.
  const displayBuckets=sortBuckets(allBuckets,sidebarOrder.sectionSort,recentProjectRanks)
  const displayBucketIds=displayBuckets.map(bucket=>bucket.id)
  const projectBuckets=displayBuckets.filter(bucket=>bucket.items.length>0)
  // Sidebar reading order starts with root Projects, then proceeds through Groups.
  // The collapsed rail, the numbered
  // Project commands, and the drag baseline all follow what is on screen rather
  // than the stored positions, or a sorted sidebar would disagree with itself.
  // A folded Group still contributes its Projects: collapsing hides rows, it
  // does not remove the Projects from the rail or the numbered shortcuts.
  const displayProjects=[...ungroupedProjects,...projectBuckets.flatMap(bucket=>bucket.items)]
  const displayProjectIds=mergeVisibleOrder(orderedProjects.map(project=>project.id),displayProjects.map(project=>project.id))
  // Which way the toolbar's fold control points. "Everything on screen is folded"
  // rather than "anything is", so the button only offers Expand once there is
  // genuinely nothing left to collapse — a half-folded tree still reads as untidy,
  // and one more click finishes the job instead of undoing it.
  const allFolded=!!displayProjects.length
    &&displayProjects.every(project=>collapsedProjects.has(project.id))
    &&projectBuckets.every(bucket=>isBucketCollapsed(sidebarOrder,bucket.id))
  // A deleted Group would otherwise leave its folded flag behind forever, and the
  // stored blob is what a recreated bucket id would silently inherit. Gated on
  // `registryLoaded`: this effect also runs on mount, where the empty group list is
  // an unfetched snapshot rather than an empty registry, and pruning against it
  // unfolded every Group on every page load.
  useEffect(()=>{
    const pruned=pruneSidebarOrder(
      sidebarOrder,
      registryLoaded?orderedGroups.map(group=>group.id):null,
    )
    if(pruned!==sidebarOrder)setSidebarOrder(pruned)
  },[projectGroups,registryLoaded])
  const activeLayout = layoutMap[projectId] || emptyLayout()
  const paneIds = terminalIds(activeLayout).filter(id => sessions.some(session => session.id === id && !['exited', 'crashed'].includes(session.state)))
  const workspacePanes=paneStacks(activeLayout)
  const paneViewIds=workspacePanes.map(pane=>pane.active_child_id)
  const focusedTabId=leaves(activeLayout).find(leaf=>leaf.id===(focusedViewId||activeId))?.id||null
  const focusedTerminalSession=focusedTabId?sessions.find(session=>session.id===focusedTabId)||null:null
  const railVoiceEntries=useMemo(()=>focusedTerminalSession?resolveRailVoiceEntries(
    loadRailConfig(focusedTerminalSession.project_id),
    {device:currentProfile(),backend:focusedTerminalSession.backend},
  ):[],[
    focusedTerminalSession?.id,focusedTerminalSession?.project_id,focusedTerminalSession?.backend,
    mobileWorkspace,railVoiceRevision,
  ])
  const activeStack=focusedTabId?stackForView(activeLayout,focusedTabId):null
  const unpanned = sessions.filter(session => session.project_id === projectId && !['exited', 'crashed'].includes(session.state) && !paneIds.includes(session.id))
  const focusedOutsideLayout=!!active&&!['exited','crashed'].includes(active.state)&&active.project_id===projectId&&!paneIds.includes(active.id)
  const focusedAgentSession=focusedViewId
    ?sessions.find(session=>session.id===focusedViewId&&session.project_id===projectId&&isAgent(session)&&!session.pending&&!isEndedSession(session))||null
    :active&&active.project_id===projectId&&isAgent(active)&&!active.pending&&!isEndedSession(active)?active:null
  useEffect(()=>{
    if(focusedAgentSession)noteTerminalFocus(focusedAgentSession.id)
  },[focusedAgentSession?.id])
  const liveVoiceSessionIds=useRef<Set<string>>(new Set())
  liveVoiceSessionIds.current=new Set(sessions.filter(session=>isAgent(session)&&!session.pending&&!isEndedSession(session)).map(session=>session.id))
  const liveVoiceSessionRuns=useRef<Map<string,string|null>>(new Map())
  liveVoiceSessionRuns.current=new Map(sessions.map(session=>[session.id,session.agent_run_id||null]))
  const liveVoiceSessionSettings=useRef<Map<string,{mode:Session['voice_mode'];content:Session['voice_content']}>>(new Map())
  liveVoiceSessionSettings.current=new Map(sessions.map(session=>[session.id,{mode:session.voice_mode,content:session.voice_content}]))
  const voiceSessionCandidates=useMemo<VoiceSessionCandidate[]>(()=>sessions
    .filter(session=>session.project_id===projectId&&isAgent(session)&&!session.pending&&!isEndedSession(session))
    .map(session=>({
      id:session.id,
      label:`Agent · ${sessionName(session)}`,
      available:()=>liveVoiceSessionIds.current.has(session.id),
      agentRunId:()=>liveVoiceSessionRuns.current.get(session.id)||null,
      voiceMode:()=>liveVoiceSessionSettings.current.get(session.id)?.mode||null,
      voiceContent:()=>liveVoiceSessionSettings.current.get(session.id)?.content||null,
    })),[sessions,projectId])
  const conversationTarget=useMemo(()=>resolveConversationTarget(
    focusedInsertTarget,
    voiceSessionCandidates,
    focusedAgentSession?.id||null,
  ),[focusedInsertTarget,voiceSessionCandidates,focusedAgentSession?.id])
  const updateSession = (next: Session) => setSessions(items => items.map(item => item.id === next.id ? mergeSessionSnapshot(item,next) : item))
  const commandRegistryRef=useRef<Command[]>([])
  const pendingVoiceCandidates=useRef<VoiceIntentCandidate[]>([])
  const spokenListContext=useRef<SpokenListContext|null>(null)
  const voiceQueryHandler=useRef<(query:VoiceQuery)=>Promise<VoiceCommandResult>>(async()=>({detail:'Voice queries are still loading.'}))
  const [approvalConfirmation,setApprovalConfirmation]=useState<{sessionId:string;confirmationId:string;operation:string}|null>(null)
  const handleVoiceIntent=async(spoken:string)=>{
    const selected=selectNumberedCandidate(pendingVoiceCandidates.current,spoken)
    const resolution=selected
      ?{match:selected,candidates:[selected],confidence:selected.confidence}
      :resolveVoiceIntent(commandRegistryRef.current,spoken)
    if(!resolution.match){
      pendingVoiceCandidates.current=resolution.candidates
      if(resolution.candidates.length){
        const list=numberedCandidates(resolution.candidates)
        return {detail:`More than one command matches. ${list}`,speech:`I found more than one. ${list} Choose option 1 or option 2 after this finishes.`}
      }
      const detail=`No voice command matched “${spoken}”. Say “${voiceStatus?.wake_words?.[0]||'Mux'}, list voice commands” for help.`
      return {detail,speech:detail}
    }
    pendingVoiceCandidates.current=[]
    const {command,text}=resolution.match
    if(command.voice?.execute)return await command.voice.execute(text)
    const ran=runCommand(commandRegistryRef.current,command.id)
    if(ran!=='ran')return{detail:command.disabledReason||`${command.label} is unavailable.`}
    return {detail:`${command.label}. Still listening.`}
  }
  // Capture is a workspace flag. Focus only changes this commit target; it never
  // restarts the microphone or clears the draft, and pinning freezes the target.
  const conversation = useConversation(voiceStatus, updateSession, conversationTarget, handleVoiceIntent)

  // Sessions on screen right now (visible pane of the displayed project). Being
  // on screen is half of what marks a row read; a human at the window is the
  // other half (humanPresence.ts).
  const visibleSessionIds=visibleTerminalIds(activeLayout)
  const conversationPaneCandidate=focusedViewId||activeId
  const conversationPaneId=conversation.phase!=='off'&&conversationPaneCandidate&&visibleSessionIds.includes(conversationPaneCandidate)
    ?conversationPaneCandidate
    :null
  const visibleSessionKey=visibleSessionIds.join('\n')
  const [humanPresent,setHumanPresent]=useState(isHumanPresent)
  useEffect(()=>watchHumanPresence(setHumanPresent),[])
  // Drop overlay entries the daemon has confirmed, so the map stays the size of
  // what is genuinely in flight rather than growing for the life of the tab.
  useEffect(()=>{setAckedTurns(prev=>pruneAcks(prev,sessions))},[sessions])
  // Acknowledge the completed turns of every on-screen agent after a dwell. The
  // dependency is the pending turns themselves, not the session list: a busy
  // fleet re-renders constantly, and keying the timer on that would restart it
  // forever and never acknowledge anything. `turn_seq` only moves when an agent
  // settles, so this key is stable for exactly as long as the dwell needs.
  const pending=humanPresent?pendingAcks(sessions,visibleSessionIds,ackedTurns):[]
  const pendingKey=pending.map(([id,seq])=>`${id}:${seq}`).join('\n')
  useEffect(()=>{
    if(!pendingKey)return
    const timer=window.setTimeout(()=>{
      for(const [id,seq] of pending){
        // Optimistic first: the row must clear now, not a round-trip later. A
        // failed POST is left alone rather than rolled back - the next snapshot
        // carries the daemon's own answer, and re-lighting a row the user just
        // looked at is worse than acknowledging it slightly early.
        setAckedTurns(current=>current[id]>=seq?current:{...current,[id]:seq})
        void api('POST',`/api/sessions/${id}/read`,{turn_seq:seq}).catch(()=>{})
      }
    },READ_ACK_DWELL_MS)
    return()=>window.clearTimeout(timer)
  },[pendingKey])

  // Terminals stay mounted for a few switches after you leave them, so coming back
  // costs no replay (`warmPanes.ts`). Recency is recorded from the layout rather than
  // from focus: what matters is which pane a stack was last *showing*, which is also
  // what survives a project switch and a workspace restore.
  const [warmHistory,setWarmHistory]=useState<string[]>([])
  useEffect(()=>{
    setWarmHistory(history=>recordPaneVisits(history,visibleSessionIds))
  },[visibleSessionKey])
  const layoutTerminalIds=terminalIds(activeLayout)
  const layoutTerminalKey=layoutTerminalIds.join('\u0000')
  // Budgeted across the whole workspace, not per stack. Mobile keeps no hidden
  // terminals because their live output is paid over the network while offscreen.
  const warmTerminalBudget=warmPaneBudget(mobileWorkspace?'mobile':'desktop')
  const warmTerminalIds=useMemo(
    ()=>warmPaneIds(warmHistory,visibleSessionIds,layoutTerminalIds,warmTerminalBudget),
    [warmHistory,visibleSessionKey,layoutTerminalKey,warmTerminalBudget],
  )

  useEffect(()=>{
    const {focus,keepRequest}=reconcileFocusView({
      requested:pendingFocusId.current,
      focused:focusedViewId,
      hasRoot:!!activeLayout.root,
      holdsRequested:!!pendingFocusId.current&&!!stackForView(activeLayout,pendingFocusId.current),
      holdsFocused:!!focusedViewId&&!!stackForView(activeLayout,focusedViewId),
      firstPaneActive:paneStacks(activeLayout)[0]?.active_child_id||null,
    })
    if(!keepRequest)pendingFocusId.current=null
    if(focus!==focusedViewId)setFocusedViewId(focus)
  },[projectId,activeLayout,focusedViewId])

  /** Focus a view now, and again the moment the layout that holds it arrives.
   *
   *  For panes the daemon creates on our behalf, where the response names the leaf but
   *  the layout carrying it is a refresh behind. A plain `setFocusedViewId` is undone by
   *  the reconciliation above before that refresh lands. */
  const requestFocusView=(id:string)=>{pendingFocusId.current=id;setFocusedViewId(id)}

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
  const openSettings = (section?:string) => { setSettingsSection(section); setSettingsOpen(true); setMainMenuOpen(false); setProjectMenu(null) }
  const openActionEditor = () => { setActionEditorOpen(true); setMainMenuOpen(false); setProjectMenu(null); setContextMenu(null) }
  const noteIdForTarget=(target:NoteTarget)=>target.kind==='worktree-file'
    ? target.worktree?worktreeFileResourceId(target.worktree,target.resourceId):''
    : noteResourceId(target.kind,target.resourceId)
  const noteTargetForResource=(resourceId:string,targetProject=projectId):NoteTarget|null=>{
    const identity=parseNoteResourceId(resourceId)
    if(!identity)return null
    return {projectId:targetProject,kind:identity.kind,resourceId:identity.id,worktree:identity.kind==='worktree-file'?identity.worktree:undefined}
  }
  const openBrowsedNote=(targetProject:string,noteId:string,place:NotePlacement='tab')=>{
    const target:NoteTarget={projectId:targetProject,kind:'note',resourceId:noteId}
    if(place==='drawer')openTargetInDrawer(target);else void showResourceForTarget(target)
  }
  const openScratchpad=(place:NotePlacement='drawer')=>{
    const targetProject=projectId||activeProject?.id||projects[0]?.id
    if(!targetProject){setError('Create a Project before opening the Scratchpad in a workspace.');return}
    const target:NoteTarget={projectId:targetProject,kind:'global-note',resourceId:'scratchpad'}
    if(place==='drawer')openTargetInDrawer(target);else void showResourceForTarget(target)
  }
  // Files is a drawer tab, not a pane tab: it is a navigator that opens documents into
  // the workspace, so it costs a panel rather than a permanent tab. Its view follows the
  // active Project, which is why every entry point selects that Project first.
  const openProjectFiles=(project:Project)=>{setProjectId(project.id);openDrawerTab('files',project.id)}
  const openNotesBrowser=(scope:Project|null)=>{
    if(scope)setProjectId(scope.id)
    setNotesAllProjects(!scope)
    openDrawerTab('notes',scope?.id||projectId)
  }
  const openProjectFile=(project:Project,path:string,targetViewId?:string)=>void showResourceForTarget({projectId:project.id,kind:'file',resourceId:path},targetViewId)
  const openWorktreeFile=(project:Project,worktree:string,path:string,targetViewId?:string)=>void showResourceForTarget({projectId:project.id,kind:'worktree-file',resourceId:path,worktree},targetViewId)
  // Notifications are a drawer tab, not a modal: checking what fired should not be
  // a full-screen interruption. Opening the tab is what marks them read.
  const openNotifications = () => { showDrawerTab('notifications');setNotificationUnread(0);void loadNotifications() }
  // Flip every interruptive alert for this device profile without discarding its
  // channel or per-event choices.
  const toggleAlerts = () => {
    const profile = currentProfile()
    const prefs = alertPreferences()
    setAlertPreferencesFor(profile, { ...prefs, enabled: !prefs.enabled })
  }

  // The sort button stops its own pointer-down so the header drag never starts under
  // it, which also keeps that event from reaching the document's dismiss handler —
  // so opening this menu has to close whatever else was open itself.
  const openSortMenu=(x:number,y:number)=>{
    setContextMenu(null);setProjectMenu(null);setSidebarMenu(null);setNoteMenu(null);setTabMenu(null);setEmptyMenu(null);setDrawerDisplayMenu(null);setMainMenuOpen(false)
    setSortMenu({x,y})
  }
  const openDrawerDisplayMenu=(x:number,y:number,surface:'tabs'|'rail')=>{
    setContextMenu(null);setProjectMenu(null);setSidebarMenu(null);setSortMenu(null);setNoteMenu(null);setTabMenu(null);setEmptyMenu(null);setMainMenuOpen(false)
    setDrawerDisplayMenu({x,y,surface})
  }
  const groupIdFor=(project:Project)=>
    project.group_id&&projectGroups.some(group=>group.id===project.group_id)?project.group_id:null
  /** Write a manual Project order. Placing a Project by hand is the statement that
   *  Projects are hand-arranged, so it drops the sort back to Manual and freezes
   *  whatever was on screen into positions — otherwise the next render would re-sort
   *  the move away and the drag would look broken. It took the drag's bucket when the
   *  mode was per section; one global mode needs no such argument. */
  const commitProjectOrder=async(nextIds:string[])=>{
    if(nextIds.join('\0')===displayProjectIds.join('\0'))return
    setSidebarOrder(setProjectSortMode(sidebarOrder,'custom'))
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
    const groupId=groupIdFor(project)
    const peers=displayProjects.filter(item=>groupIdFor(item)===groupId)
    const index=peers.findIndex(item=>item.id===project.id)
    const other=peers[index+direction]
    if(!other)return
    const ids=[...displayProjectIds]
    const from=ids.indexOf(project.id),to=ids.indexOf(other.id)
    ;[ids[from],ids[to]]=[ids[to],ids[from]]
    setProjectMenu(null)
    void commitProjectOrder(ids)
  }
  const beginProjectPointerDrag=(event:JSX.TargetedPointerEvent<HTMLElement>,project:Project,peerIds:string[])=>{
    const projectList=event.currentTarget.closest<HTMLElement>('.sidebar-project-list')
    const tree=event.currentTarget.closest<HTMLElement>('.project-tree')
    const rowHeight=event.currentTarget.getBoundingClientRect().height
    const initial:ProjectDrag={id:project.id,previewIds:displayProjectIds,overId:null,side:null}
    let latestPointer:{clientX:number;clientY:number}|null=null,scrollFrame:number|null=null
    const preview=(pointer:{clientX:number;clientY:number})=>{
      const current=dragProjectRef.current
      if(!current||!projectList||!peerIds.includes(current.id)){showDropSlot(null);return}
      const target=reorderTargetFromContainer(projectList,current.id,'vertical',pointer.clientY)
      if(!target){showDropSlot(null);return}
      const previewIds=reorderForHover(current.previewIds,current.id,target.id,target.side)
      dragProjectRef.current={...current,previewIds,overId:target.id,side:target.side}
      // The section is the drop target, but the row is what lands: a Project with sessions
      // showing is a tall section, and outlining all of it would promise a move of the whole
      // block into a gap that only ever receives one row.
      const targetSection=Array.from(projectList.querySelectorAll<HTMLElement>(':scope > [data-reorder-id]')).find(item=>item.dataset.reorderId===target.id)||null
      if(!targetSection){showDropSlot(null);return}
      dropSlotForRow(targetSection,target.side,rowHeight,project.name)
    }
    const stopAutoScroll=()=>{
      latestPointer=null
      if(scrollFrame!==null)window.cancelAnimationFrame(scrollFrame)
      scrollFrame=null
    }
    const autoScroll=()=>{
      scrollFrame=null
      if(!tree||!latestPointer)return
      const box=tree.getBoundingClientRect()
      const delta=edgeAutoScrollDelta(latestPointer.clientY,box.top,box.bottom)
      if(delta===0)return
      const before=tree.scrollTop
      tree.scrollTop+=delta
      if(tree.scrollTop===before)return
      preview(latestPointer)
      scrollFrame=window.requestAnimationFrame(autoScroll)
    }
    beginPointerDrag(event,project.name,`project:${project.id}`,
      ()=>{
        cancelLongPress();setProjectMenu(null);setContextMenu(null);setRunMenu(null)
        if(mobileWorkspace)navigator.vibrate?.(15)
        dragProjectRef.current=initial
      },
      pointer=>{
        latestPointer={clientX:pointer.clientX,clientY:pointer.clientY}
        preview(pointer)
        if(scrollFrame===null)scrollFrame=window.requestAnimationFrame(autoScroll)
      },
      ()=>{stopAutoScroll();const current=dragProjectRef.current;setDragProject(null);if(current)void commitProjectOrder(current.previewIds)},
      ()=>{stopAutoScroll();setDragProject(null)},
      mobileWorkspace?MOBILE_HOLD_DRAG:POINTER_MOVE_DRAG,
      ()=>openProjectMenuAt(project,event.clientX,event.clientY),
    )
  }
  /** Reorder the sidebar's Groups. Root Projects stay before them and Group order
   *  is shared because it lives on each Group record. */
  const commitBucketOrder=async(nextIds:string[])=>{
    if(nextIds.join('\0')===displayBucketIds.join('\0'))return
    const nextGroupIds=nextIds
    // Placing a Group by hand is the statement that Groups are hand-arranged,
    // so it drops the section sort back to Manual and freezes what was on screen —
    // the same rule a Project drag follows one level down.
    setSidebarOrder({...sidebarOrder,sectionSort:'custom'})
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
  /** Reorder a session within its own Project, or group it with a peer into one tabbed pane.
   *
   *  The gesture is confined to the Project the session already belongs to: that Project's own
   *  `.session-list` is the only container consulted, so no pointer position over another
   *  Project resolves to anything at all. A session cannot change Project by being dragged —
   *  that would reassign a running PTY's owner, which is not a decision a two-inch gesture over
   *  a tree should make — and the drag used to say so with a red "invalid" outline and an error
   *  toast on drop, which is a worse way to say "this was never going to work" than having no
   *  target there in the first place.
   *
   *  The sidebar's session list is the pane tree read depth-first, so landing between two rows
   *  is a real position and `moveTerminalBeside` honours it exactly. Before this, every drop was
   *  `groupTerminalsInStack`, which appends: a row aimed at the top of the list arrived at the
   *  bottom of a pane, and the list looked like it ignored the gesture. */
  const beginSessionPointerDrag=(event:JSX.TargetedPointerEvent<HTMLElement>,session:Session)=>{
    const list=event.currentTarget.closest<HTMLElement>('.project-group')?.querySelector<HTMLElement>(':scope > .session-list')||null
    const tree=event.currentTarget.closest<HTMLElement>('.project-tree')
    const rowHeight=event.currentTarget.getBoundingClientRect().height
    const label=sessionName(session)
    const projectLayout=()=>layoutValues.current[session.project_id]||layoutMap[session.project_id]||parseLayout(projects.find(item=>item.id===session.project_id)?.layout)
    // Sessions already sharing this one's pane cannot be joined to it, so their rows are
    // insertion targets over their whole height: without this, the middle of a sibling tab's row
    // previewed a group that would have appended the dragged tab to the end of the pane they are
    // both already in — a move nothing on screen asked for.
    const paneSiblings=new Set(stackForView(projectLayout(),session.id)?.children.map(child=>child.id)||[])
    let latestPointer:{clientX:number;clientY:number}|null=null,scrollFrame:number|null=null
    // Rows a drop may land on: every session row this Project renders, minus those with no
    // position to hold — a pending terminal is a client-only leaf about to be replaced, and an
    // unpaned session is not in the pane tree the list order is read from.
    const targetRows=()=>list?Array.from(list.querySelectorAll<HTMLElement>('[data-sidebar-session-id]')).filter(row=>row.dataset.sidebarReorder!=='off'):[]
    const clearTarget=()=>{dragSessionTargetRef.current=null;showPointerDropIndicator(null);showDropSlot(null)}
    const preview=(pointer:{clientX:number;clientY:number})=>{
      const bounds=list?.getBoundingClientRect()
      // Leaving the list is not a drop somewhere else, so it is a drop nowhere. Without this the
      // last slot computed inside the list stays armed and commits on release, reordering a
      // Project the pointer had already left — the closest this gesture could come to the
      // cross-Project move it refuses to perform.
      if(!bounds||pointer.clientX<bounds.left-DROP_LIST_MARGIN||pointer.clientX>bounds.right+DROP_LIST_MARGIN
        ||pointer.clientY<bounds.top-DROP_LIST_MARGIN||pointer.clientY>bounds.bottom+DROP_LIST_MARGIN){clearTarget();return}
      const rows=targetRows()
      // On mobile the drop is reorder-only: grouping two sessions into one tabbed pane lives
      // in the row's middle band, which needs a ~12px-edge hit to reorder instead — unaimable
      // with a fingertip, so nearly every drop grouped and the list "wouldn't rearrange". A
      // fingertip gets the whole row as a before/after insertion; grouping stays a desktop
      // gesture (and the long-press menu's "combine" path).
      const groupable=mobileWorkspace?()=>false:(id:string)=>!paneSiblings.has(id)
      const target=listDropTargetForPoint(rows.map(row=>{
        const box=row.getBoundingClientRect()
        return {id:row.dataset.sidebarSessionId||'',start:box.top,end:box.bottom}
      }),session.id,pointer.clientY,groupable)
      const element=target?rows.find(row=>row.dataset.sidebarSessionId===target.id)||null:null
      if(!target||!element){clearTarget();return}
      dragSessionTargetRef.current=target
      if(target.kind==='group'){showDropSlot(null);showPointerDropIndicator(element,'group-session');return}
      showPointerDropIndicator(null)
      dropSlotForRow(element,target.side,rowHeight,label)
    }
    const stopAutoScroll=()=>{
      latestPointer=null
      if(scrollFrame!==null)window.cancelAnimationFrame(scrollFrame)
      scrollFrame=null
    }
    const autoScroll=()=>{
      scrollFrame=null
      if(!tree||!latestPointer)return
      const box=tree.getBoundingClientRect()
      const delta=edgeAutoScrollDelta(latestPointer.clientY,box.top,box.bottom)
      if(delta===0)return
      const before=tree.scrollTop
      tree.scrollTop+=delta
      if(tree.scrollTop===before)return
      preview(latestPointer)
      scrollFrame=window.requestAnimationFrame(autoScroll)
    }
    beginPointerDrag(event,label,`session:${session.id}`,
      ()=>{cancelLongPress();setContextMenu(null);setProjectMenu(null);if(mobileWorkspace)navigator.vibrate?.(15);dragSessionTargetRef.current=null},
      pointer=>{
        latestPointer={clientX:pointer.clientX,clientY:pointer.clientY}
        preview(pointer)
        if(scrollFrame===null)scrollFrame=window.requestAnimationFrame(autoScroll)
      },
      ()=>{
        stopAutoScroll()
        const target=dragSessionTargetRef.current;dragSessionTargetRef.current=null
        if(!target)return
        const current=projectLayout()
        const next=target.kind==='group'
          ?groupTerminalsInStack(current,target.id,session.id)
          :moveTerminalBeside(current,session.id,target.id,target.side)
        if(next!==current)void updateLayout(session.project_id,next)
      },
      ()=>{stopAutoScroll();dragSessionTargetRef.current=null},
      mobileWorkspace?MOBILE_HOLD_DRAG:POINTER_MOVE_DRAG,
      session.pending?undefined:()=>openSessionMenu(session,event.clientX,event.clientY,'sidebar'),
    )
  }

  // A MenuGroup left expanded would reopen with the menu next time; both hosts are
  // dismissed from a dozen places, so collapse from their state rather than each one.
  // They share the openId because opening either menu closes the other, so a group
  // belonging to the one that just closed can never still be showing.
  useEffect(() => { if (!mainMenuOpen) setMenuGroup(null) }, [mainMenuOpen])
  useEffect(() => { if (!sortMenu) setMenuGroup(null) }, [sortMenu])

  // The menu- and modal-closing Escape handlers that used to live here (one closing nine
  // menus at once, one closing eighteen things at once) are gone: each surface registers a
  // dismiss level, and the single Escape branch in the `mux:command` effect pops one.

  useEffect(() => {
    if (!contextMenu && !projectMenu && !sidebarMenu && !sortMenu && !noteMenu && !tabMenu && !emptyMenu && !drawerDisplayMenu && !mainMenuOpen) return
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
  }, [contextMenu, projectMenu, sidebarMenu, sortMenu, noteMenu, tabMenu, emptyMenu, drawerDisplayMenu, mainMenuOpen])

  useEffect(() => {
    if (!confirmKillId) return
    const timer = window.setTimeout(() => {
      setConfirmKillId(current => current === confirmKillId ? null : current)
    }, 2000)
    return () => window.clearTimeout(timer)
  }, [confirmKillId])

  // The Action rail's End session button lives inside a memoized pane that
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
  const spawnTerminal = async (targetProject = projectId, split: false | SplitDirection | 'stack' = false, profileId?: string, targetSessionId?: string, position:'before'|'after'='after', backend:string='shell', options?:{argv?:string[];seedText?:string}) => {
    if (spawning.current) return false
    const target=projectsRef.current.find(item=>item.id===targetProject)
    if(!target){setError('Project is not available yet.');return false}
    spawning.current = true
    const startupOrigin=performance.now()
    const pendingId=`pending-${browserUuid()}`
    const currentLayout=layoutValues.current[targetProject]||layoutMap[targetProject]||parseLayout(target.layout)
    const focused=targetSessionId??(targetProject===projectId?openAnchorId(currentLayout,focusedViewId||activeId):spawnAnchorId(currentLayout))
    const placement:PendingSpawnPlacement={split,targetId:focused,position}
    pendingSpawns.current[pendingId]={projectId:targetProject,placement}
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
        // A launch profile now exists for agent harnesses too, so this is no longer
        // gated on `shell`. The daemon refuses a profile whose own backend does not
        // match the requested one, which is the check the gate used to stand in for.
        profile_id: profileId || undefined,
        ...(options?.argv?.length ? { argv: options.argv } : {}),
        // A first prompt as text: the daemon inlines short bodies into argv and stages long
        // ones into the workspace with a reader prompt, so there is no client-side ceiling.
        ...(options?.seedText ? { seed_text: options.seedText } : {}),
      })
      markProjectRecent(targetProject)
      startupOrigins.current[next.id]=startupOrigin
      const browserTiming={api_response:performance.now()-startupOrigin}
      clientStartupTimingValues.current[next.id]=browserTiming
      setClientStartupTimings(current=>({...current,[next.id]:browserTiming}))
      if (profileId) { localStorage.setItem('mux.lastProfile',profileId); setLauncherProfile(profileId) }
      // Remembered so holding mobile Run repeats the last launch without the menu.
      localStorage.setItem('mux.lastBackend',backend)
      pendingSpawns.current[pendingId].resolvedId=next.id
      setSessions(items => [...items.filter(item=>item.id!==pendingId&&item.id!==next.id),mergeSessionSnapshot(items.find(item=>item.id===next.id),next)])
      setActiveId(next.id)
      setFocusedViewId(next.id)
      const latestLayout=layoutValues.current[targetProject]||optimisticLayout
      const withPending=terminalIds(latestLayout).includes(pendingId)?latestLayout:placePendingTerminal(latestLayout,pendingId,placement)
      const nextLayout=replaceTerminal(withPending,pendingId,next.id)
      await updateLayout(targetProject, nextLayout)
      emitTutorialAction({action:'session-launched',backend})
      // Protect against an event refresh that began with the pre-spawn layout.
      window.setTimeout(()=>{delete pendingSpawns.current[pendingId]},500)
      return true
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
      return false
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
  const lastLaunchBackend=():string=>{
    const stored=localStorage.getItem('mux.lastBackend')
    return stored&&isAgentBackend(stored)?stored:'shell'
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

  const startWorktreeSession=async(targetProject:string,path:string,backend:string)=>{
    const target=projectsRef.current.find(item=>item.id===targetProject)
    if(!target){setError(`Worktree created at ${path}, but its Project is no longer available.`);return}
    const startupOrigin=performance.now()
    const pendingId=`pending-${browserUuid()}`
    const currentLayout=layoutValues.current[targetProject]||layoutMap[targetProject]||parseLayout(target.layout)
    pendingSpawns.current[pendingId]={projectId:targetProject,placement:null}
    setSessions(items=>[...items,pendingTerminal(pendingId,target,backend,{
      cwd:path,
      name:`setting up ${backend==='shell'?'shell':backend}…`,
      label:'Setting up worktree…',
      detail:`Running the repository setup before starting ${backend==='shell'?'the shell':backend}…`,
    })])
    setProjectId(targetProject)
    setActiveId(pendingId)
    setFocusedViewId(pendingId)
    setSidebarOpen(false)
    try{
      const result=await api<WorktreeSpawnResult>('POST','/api/git/worktrees/session',{
        path,spawn:{project_id:targetProject,backend},
      },{timeoutMs:35*60*1000})
      if(result.status!=='spawned'||!result.session_id){
        const setupFailed=result.setup&&['failed','timed_out','error'].includes(result.setup.status)
        const setupDetail=setupFailed?` Setup also failed (${result.setup?.error||result.setup?.exit_code||result.setup?.status}); the tree is not bootstrapped.`:''
        throw new Error(`the session failed: ${result.error||'unknown error'}.${setupDetail}`)
      }
      const next=result.session||await api<Session>('GET',`/api/sessions/${encodeURIComponent(result.session_id)}`)
      markProjectRecent(targetProject)
      startupOrigins.current[next.id]=startupOrigin
      const browserTiming={api_response:performance.now()-startupOrigin}
      clientStartupTimingValues.current[next.id]=browserTiming
      setClientStartupTimings(current=>({...current,[next.id]:browserTiming}))
      localStorage.setItem('mux.lastBackend',backend)
      pendingSpawns.current[pendingId].resolvedId=next.id
      setSessions(items=>[
        ...items.filter(item=>item.id!==pendingId&&item.id!==next.id),
        mergeSessionSnapshot(items.find(item=>item.id===next.id),next),
      ])
      setActiveId(current=>current===pendingId?next.id:current)
      setFocusedViewId(current=>current===pendingId?next.id:current)
      emitTutorialAction({action:'session-launched',backend})
      if(result.setup&&['failed','timed_out','error'].includes(result.setup.status)){
        const detail=result.setup.error||(result.setup.exit_code!=null?`exit code ${result.setup.exit_code}`:result.setup.status)
        setError(`Worktree session started, but setup failed (${detail}). The tree is not bootstrapped; setup output is in the session scrollback.`)
      }
      window.setTimeout(()=>{delete pendingSpawns.current[pendingId]},500)
    }catch(cause){
      delete pendingSpawns.current[pendingId]
      setSessions(items=>items.filter(item=>item.id!==pendingId))
      const fallback=visibleTerminalIds(layoutValues.current[targetProject]||currentLayout)[0]||terminalIds(currentLayout)[0]||null
      setActiveId(current=>current===pendingId?fallback:current)
      setFocusedViewId(current=>current===pendingId?fallback:current)
      setError(`Worktree created at ${path}, but ${cause instanceof Error?cause.message:String(cause)}`)
    }
  }

  const attachActionSessions=async(targetProject:string,nextSessions:Session[])=>{
    if(!nextSessions.length)return
    const target=projectsRef.current.find(item=>item.id===targetProject)
    if(!target)return
    let nextLayout=layoutValues.current[targetProject]||layoutMap[targetProject]||parseLayout(target.layout)
    let targetId=openAnchorId(nextLayout,targetProject===projectId?(focusedViewId||activeId):null)
    for(const session of nextSessions){nextLayout=openTab(nextLayout,targetId,terminalLeaf(session.id));targetId=session.id}
    layoutValues.current[targetProject]=nextLayout
    setSessions(items=>[
      ...items.filter(item=>!nextSessions.some(next=>next.id===item.id)),
      ...nextSessions.map(next=>mergeSessionSnapshot(items.find(item=>item.id===next.id),next)),
    ])
    setLayoutMap(current=>({...current,[targetProject]:nextLayout}))
    setProjectId(targetProject);setActiveId(nextSessions.at(-1)!.id);setFocusedViewId(nextSessions.at(-1)!.id);setSidebarOpen(false)
    markProjectRecent(targetProject)
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
      if(result.errors.length<scripts.length)markProjectRecent(next.id)
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

  // No deleteGroup: the sidebar header's × was its only caller and is gone. A Group
  // is emptied rather than deleted now — reassign its Projects (Projects manager, or
  // the session menu's Group select) and it stops rendering, since a Group with no
  // Projects in it is not a sidebar section. DELETE /api/project-groups/{id} still
  // exists if this ever needs a home in the Projects registry.

  const openRename = (target: RenameTarget) => {
    setContextMenu(null)
    setProjectMenu(null)
    setRenameTarget(target)
    setRenameValue(target.kind === 'session' ? sessionName(target.session) : target.project.name)
  }

  const regenerateSessionTitle = async (session: Session) => {
    setContextMenu(null)
    try {
      await api('POST', `/api/sessions/${session.id}/title/regenerate`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  // Escape hatch for a standing-activity annotation the user can see is wrong.
  // Every source of one is evidence about work the daemon cannot observe
  // directly, so any of them can be left holding a claim that outlived its task;
  // without this the only exit is a 30-minute TTL. It retracts only - the state
  // dot, delivery, and awaiting are untouched - so the worst case is that a
  // genuinely running task re-announces itself on its next piece of evidence.
  const clearStandingActivity = async (session: Session) => {
    setContextMenu(null)
    try {
      await api('POST', `/api/sessions/${session.id}/standing-activity/clear`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  // One menu item, not two. "Mark as read" and "Mark as unread" are the same
  // decision, and listing both makes the reader work out which of the pair is
  // currently true before clicking - which is exactly what a label stating the
  // action already tells them.
  //
  // Optimistic on the same discipline as the dwell acknowledgement: the row has
  // to flip on the click, and the daemon's own snapshot follows over the socket.
  // `unread_pin` is what makes marking the pane you are looking at stick - the
  // dwell timer would otherwise re-read it a second later (sessionAttention.ts).
  const toggleSessionRead = async (session: Session) => {
    setContextMenu(null)
    const unread = isUnread(session, ackedTurns)
    const turns = Number(session.turn_seq || 0)
    updateSession({
      ...session,
      unread_pin: !unread,
      read_turn_seq: unread ? turns : Math.max(turns - 1, 0),
    })
    setAckedTurns(current => {
      if (!unread) {
        if (!(session.id in current)) return current
        const { [session.id]: _cleared, ...rest } = current
        return rest
      }
      return current[session.id] >= turns ? current : { ...current, [session.id]: turns }
    })
    try {
      await api('POST', `/api/sessions/${session.id}/read`, { read: unread })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
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

  const removeProject = async (project: Project, closeLive: boolean) => {
    if(closeLive){
      const liveSessions=sessions.filter(session=>session.project_id===project.id&&!isEndedSession(session))
      for(const session of liveSessions)pendingKills.current[session.id]={sessionId:session.id,projectId:project.id,startedAt:Date.now()}
      setSessions(items=>applyKillTombstones(items,pendingKills.current))
      try{
        await Promise.all(liveSessions.map(session=>deleteSessionOnce(session.id)))
      }finally{
        for(const session of liveSessions)delete pendingKills.current[session.id]
      }
      let layout=layoutValues.current[project.id]||parseLayout(project.layout)
      for(const session of liveSessions)layout=removeLeaf(layout,'terminal',session.id)
      for(const leaf of leaves(layout,'preview'))layout=removeLeaf(layout,'preview',leaf.id)
      await updateLayout(project.id,layout)
    }
    await api('DELETE', `/api/projects/${project.id}`)
    setProjects(items => items.filter(item => item.id !== project.id))
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

  /** Fold or unfold the whole tree - every Project row and every visible Group. One control
   *  for both levels because "tidy the sidebar" is one intent, and folding only the
   *  Projects would leave the Group headers claiming space for nothing.
   *  Only what is on screen is folded: a Project hidden from the sidebar has no row to
   *  collapse, and adding its id would silently pre-fold it if it were ever shown. */
  const setAllFolded=(folded:boolean)=>{
    const next=setAllCollapsed(displayProjects.map(project=>project.id),folded)
    setCollapsedProjects(next)
    localStorage.setItem(COLLAPSED_PROJECTS_KEY,serializeCollapsedProjects(next))
    setSidebarOrder(setAllBucketsCollapsed(sidebarOrder,projectBuckets.map(bucket=>bucket.id),folded))
  }

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
  /** DELETE a session, treating "the daemon no longer has it" as the outcome we wanted.
   *  A double-tap, a second client that got there first, and a session that exited on
   *  its own between the click and the request all land on 404, and none of them is a
   *  reason to put a row back that has nothing behind it. Deadlined so the caller's
   *  tombstone can never outlive the request that owns it. */
  const deleteSessionOnce = async (sessionId: string) => {
    try {
      await api('DELETE', `/api/sessions/${sessionId}`, undefined, { timeoutMs: KILL_TOMBSTONE_TTL_MS })
    } catch (cause) {
      if (!killRemovedTheSession((cause as ApiError).status)) throw cause
    }
  }

  // Bulk close stays synchronous on purpose: hiding is refused while live work is
  // attached, so the sessions have to be genuinely gone before `hideProject` can run.
  // The tombstones are still worth taking - they keep a refresh landing mid-close from
  // flickering the rows back into a project that is on its way off the sidebar.
  const closeWorkAndHideProject=async(project:Project)=>{
    const {liveSessions}=openWorkFor(project)
    for(const session of liveSessions)pendingKills.current[session.id]={sessionId:session.id,projectId:project.id,startedAt:Date.now()}
    setSessions(items=>applyKillTombstones(items,pendingKills.current))
    try{
      await Promise.all(liveSessions.map(session=>deleteSessionOnce(session.id)))
    }finally{
      for(const session of liveSessions)delete pendingKills.current[session.id]
    }
    let layout=layoutMap[project.id]||parseLayout(project.layout)
    for(const session of liveSessions)layout=removeLeaf(layout,'terminal',session.id)
    for(const leaf of leaves(layout,'preview'))layout=removeLeaf(layout,'preview',leaf.id)
    await updateLayout(project.id,layout)
    await hideProject(project)
  }

  /**
   * Close a session on screen now; let the daemon catch up underneath.
   *
   * The daemon's half is unavoidably slow: it types the backend's exit keys, waits
   * out an agent that may be mid-turn and never sees them, force-kills the tree, then
   * persists the run - and none of that is a reason to keep showing a tab the operator
   * has already closed. So the row, the leaf, and the focus move immediately and the
   * DELETE settles in the background, guarded by a tombstone (`sessionKills.ts`).
   *
   * The layout PATCH deliberately waits for the daemon to agree. The tombstone already
   * keeps the leaf out of every reconcile, so deferring the write costs nothing on
   * screen and means a failed kill has no persisted state to undo: the next refresh
   * simply finds the session still live and puts it back where it was.
   */
  const killNow = async (session: Session) => {
    if (pendingKills.current[session.id]) return
    setConfirmKillId(null)
    setContextMenu(null)
    const currentLayout = resolveLayout(
      layoutMap[session.project_id],
      projects.find(project => project.id === session.project_id)?.layout,
    )
    let nextLayout = removeLeaf(currentLayout, 'terminal', session.id)
    const nextActiveId = nextActiveAfterKill({
      layout: nextLayout, sessions, killedId: session.id,
      projectId: session.project_id, activeId,
    })
    if (nextActiveId && terminalIds(nextLayout).includes(nextActiveId)) {
      nextLayout = activateContainingStack(nextLayout, nextActiveId)
    }
    pendingKills.current[session.id] = {
      sessionId: session.id, projectId: session.project_id, startedAt: Date.now(),
    }
    setSessions(items => items.filter(item => item.id !== session.id))
    delete startupOrigins.current[session.id]
    delete clientStartupTimingValues.current[session.id]
    setClientStartupTimings(current=>{const next={...current};delete next[session.id];return next})
    if (activeId === session.id) setActiveId(nextActiveId)
    if (focusedViewId === session.id) setFocusedViewId(nextActiveId)
    if (zoomedId === session.id) setZoomedId(null)
    layoutValues.current[session.project_id] = nextLayout
    setLayoutMap(current => ({ ...current, [session.project_id]: nextLayout }))
    let removed = true
    try {
      await deleteSessionOnce(session.id)
    } catch (cause) {
      removed = false
      const reason = cause instanceof Error ? cause.message : String(cause)
      setError(`Could not close ${sessionName(session)}: ${reason}`)
    } finally {
      delete pendingKills.current[session.id]
    }
    // Re-derived rather than replayed: a drag or a tab open may have landed in the
    // window this kill was waiting out, and `removeLeaf` on an id that is already gone
    // is a no-op, so this persists the current layout minus the session either way.
    if (removed) {
      const settled = layoutValues.current[session.project_id] ?? nextLayout
      await updateLayout(session.project_id, removeLeaf(settled, 'terminal', session.id))
    }
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
      markProjectRecent(session.project_id)
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

  const showResourceForTarget = async (target:NoteTarget,targetViewId?:string,preserveDrawerSelection=false) => {
    const resourceId=noteIdForTarget(target)
    const targetProject=projects.some(project=>project.id===target.projectId)?target.projectId:(activeProject?.id||projects[0]?.id)
    if(!resourceId||!targetProject){setError('A live Project is required to open this resource.');return}
    const current=resolveLayout(layoutMap[targetProject],projects.find(project=>project.id===targetProject)?.layout)
    // An explicit target (drag/drop) is honored exactly. Everything else lands in the pane
    // you were last in: the focused view when it is still in this layout, then the owning
    // terminal, then whatever the layout has.
    const preferredAnchor=(targetProject===projectId&&focusedViewId&&stackForView(current,focusedViewId)?focusedViewId:null)||terminalIds(current)[0]||leaves(current)[0]?.id||null
    const focused=targetViewId||openAnchorId(current,preferredAnchor)
    setProjectId(targetProject);setFocusedViewId(resourceId)
    setContextMenu(null);setProjectMenu(null);setNoteMenu(null);setMainMenuOpen(false);setEmptyMenu(null)
    if(!preserveDrawerSelection)releaseIfDrawerHolds(targetProject,resourceId)
    // Every resource opens the same way: a tab in the anchor's pane. Notes previously
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

  // ---- Drawer-hosted notes -------------------------------------------------------------
  // The drawer and a pane are mutually exclusive hosts for one note, and moving between them
  // is an explicit act rather than something either surface does silently. `drawerNotes.ts`
  // holds why (one live editor per note per browser is a correctness rule: the save queue is
  // shared per note, so a second mounted editor clobbers the first with no conflict the
  // daemon can see) and why the claim is device-local rather than layout state.
  /** The note selected in this Project's persistent Notes sub-tab rail, or null. */
  const drawerNoteId=drawerNoteFor(drawerNotes,projectId)
  const openNoteInDrawer=(resourceId:string,targetProject:string)=>{
    setProjectId(targetProject)
    setDrawerNotes(current=>claimDrawerNote(current,targetProject,resourceId))
    setNoteMenu(null);setContextMenu(null);setProjectMenu(null);setMainMenuOpen(false)
    openDrawerTab('notes',targetProject)
  }
  const openTargetInDrawer=(target:NoteTarget)=>{
    const resourceId=noteIdForTarget(target)
    if(!resourceId){setError('This resource is no longer linked to a durable owner.');return}
    openNoteInDrawer(resourceId,target.projectId)
  }
  /** Put the selected note in a pane without forgetting the Notes sub-tab. The drawer closes,
   *  so editor ownership ends while the remembered selection remains available on reopen. */
  const popDrawerNoteToTab=(resourceId:string,targetProject:string)=>{
    const target=noteTargetForResource(resourceId,targetProject)
    if(!target){setError('This resource is no longer linked to a durable owner.');return}
    void showResourceForTarget(target,undefined,true)
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
    releaseIfDrawerHolds(targetProject,resourceId)
    await updateLayout(targetProject,next)
  }
  const splitNoteResource=async(resourceId:string,targetProject:string,direction:SplitDirection,position:'before'|'after'='after')=>{
    const current=resolveLayout(layoutMap[targetProject],projects.find(project=>project.id===targetProject)?.layout)
    const owner=stackForView(current,resourceId)
    const target=targetProject===projectId&&focusedViewId&&focusedViewId!==resourceId&&stackForView(current,focusedViewId)
      ?focusedViewId
      :owner?.children.find(child=>child.id!==resourceId)?.id||null
    setProjectId(targetProject);setFocusedViewId(resourceId);setNoteMenu(null)
    releaseIfDrawerHolds(targetProject,resourceId)
    await updateLayout(targetProject,splitView(current,target,resourceLeaf('note',resourceId),direction,position))
  }

  const openNoteContext=(resourceId:string,targetProject:string,x:number,y:number)=>{
    setContextMenu(null);setProjectMenu(null);setSidebarMenu(null);setMainMenuOpen(false)
    setNoteMenu({resourceId,projectId:targetProject,x,y})
  }

  /** Every target's queue at once, partitioned by who wrote each message. A modal rather
   *  than a drawer tab because nothing in it delivers: it needs no terminal beside it, the
   *  same reason the process fleet is a modal and the Processes tab is not. */
  const openFleetQueue=(scopeProjectId='')=>{
    setFleetQueue({projectId:scopeProjectId})
    setMainMenuOpen(false);setProjectMenu(null);setContextMenu(null);setSidebarMenu(null)
  }

  /** The install-wide emergency stop. Deliberately callable with nothing open — the whole
   *  point of a brake is that reaching it costs one gesture. */
  const toggleAutoPaused=async()=>{
    setMainMenuOpen(false)
    try{setAutoStatus(await setAutoPaused(!autoStatus?.paused))}
    catch(cause){setError(`Auto-delivery could not be ${autoStatus?.paused?'resumed':'paused'}: ${cause instanceof Error?cause.message:String(cause)}`)}
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
    setSidebarOpen(false)
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
      const next=selectPendingTerminal(current,session.id)
      if(next!==current){
        layoutValues.current[session.project_id]=next
        setLayoutMap(layouts=>({...layouts,[session.project_id]:next}))
      }
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
    openDrawerTab('queue',session?.project_id||projectId)
    setQueueOpenToken(current => current + 1)
  }

  /** Read one session's conversation in the drawer without replacing its terminal.
   *
   *  Like Queue, Transcript follows the focused session, so the pane chip must focus
   *  its own session before selecting the drawer tab. */
  const openTranscriptForSession = async (sessionId: string) => {
    const session = sessionsRef.current.find(item => item.id === sessionId)
    if (session) await selectSession(session)
    openDrawerTab('transcript',session?.project_id||projectId)
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
      // Keep the dialog and its captured selection alive until the spawn is actually accepted.
      // spawnTerminal also reports the detailed failure through the app toast.
      const started=await spawnTerminal(target.projectId,'horizontal',undefined,undefined,'after',target.backend,{seedText:message})
      return started?{status:'done'}:{status:'error',error:'The new session could not be started.'}
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
          markProjectRecent(target.session.project_id)
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
      markProjectRecent(result.session.project_id)
      setReviewState(null);await refresh();setProjectId(result.session.project_id);setActiveId(result.session.id);requestFocusView(result.session.id)
    }catch(cause){setReviewState(current=>current?{...current,loading:false,error:cause instanceof Error?cause.message:String(cause)}:current)}
  }

  const resumeHistoryEntry = async (entry: HistoryEntry) => {
    try {
      const targetProject = entry.project_id || projectId
      const resumed = await api<Session>('POST', `/api/history/${entry.id}/resume`, { project_id: targetProject, target_session_id: targetProject === projectId ? activeId : undefined })
      markProjectRecent(resumed.project_id)
      // `requestFocusView`, not `setFocusedViewId`: the daemon attached the pane and set
      // it active in its own layout, but this client sees that layout only after the
      // refresh below. Plain focus would be reconciled away in the gap and the resumed
      // conversation would open behind whatever tab the History browser was opened from.
      setSessions(items => [...items, resumed]); setProjectId(resumed.project_id); setActiveId(resumed.id); requestFocusView(resumed.id)
      setHistoryOpen(false)
      // The workspace is behind a full-screen overlay and, on a phone, possibly a drawer
      // too. Focusing a pane nobody can see is not focusing it. Mobile-only for the side
      // panel: on desktop that is a docked column beside the workspace, not over it.
      setSidebarOpen(false); if(mobileWorkspace)setClipboardOpen(false)
      await refresh()
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
  }

  // Fork an agent conversation into a sibling pane. The daemon injects Claude's
  // native /branch (or a Codex resume child-thread), attaches the new pane, and
  // returns the new session; refresh() re-syncs the server-updated layout.
  const branchSession = async (session: Session) => {
    try {
      const result = await api<{ session: Session; source: string }>('POST', `/api/sessions/${session.id}/branch`, { target_session_id: session.id, direction: 'after' })
      markProjectRecent(result.session.project_id)
      setSessions(items => [...items, result.session]); setActiveId(result.session.id); requestFocusView(result.session.id)
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
      markProjectRecent(resumed.project_id)
      setSessions(items => [...items, resumed])
      setActiveId(resumed.id)
      // The replacement takes the original's place in the layout, so focus has to move
      // with it: the leaf it was on is about to stop existing.
      requestFocusView(resumed.id)
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
    const projection = mobileWorkspaceProjection(layout, focusedViewId, activeId)
    const tabs = projection.tabs
    if (tabs.length < 2) return
    const index = projection.selected ? tabs.findIndex(tab => tab.id === projection.selected!.id) : -1
    const next = tabs[((index < 0 ? 0 : index) + offset + tabs.length) % tabs.length]
    if (!next) return
    showInteractionHud(mobileTabLabel(next))
    setFocusedViewId(next.id)
    if (next.kind === 'terminal') setActiveId(next.id)
    const pane = stackForView(layout, next.id)
    if (pane && pane.active_child_id !== next.id) void updateLayout(projectId, activateStackChild(layout, pane.id, next.id))
  }

  const navigateWorkspaceTab = (offset: number) => {
    if (mobileWorkspace) { navigateMobileTab(offset); return }
    const layout = layoutValues.current[projectId] || activeLayout
    const currentId = focusedViewId || activeId
    const pane = currentId ? stackForView(layout, currentId) : null
    // The layout ref advances synchronously before its PATCH. Reading the pane's
    // active child lets key repeat continue from the optimistic tab even before
    // Preact has rendered the new focusedViewId closure.
    const next = relativeStackTab(pane, pane?.active_child_id || currentId, offset)
    if (!pane || !next) return
    setFocusedViewId(next.id)
    if (next.kind === 'terminal') setActiveId(next.id)
    if (pane.active_child_id !== next.id) void updateLayout(projectId, activateStackChild(layout, pane.id, next.id))
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

  const focusedDrawerStack=drawerStackForTab(drawerLayout,drawerTabId)
  const navigateDrawerTab=(offset:number)=>{
    if(!focusedDrawerStack)return
    const index=focusedDrawerStack.tabs.indexOf(drawerTabId)
    const next=focusedDrawerStack.tabs[(index+offset+focusedDrawerStack.tabs.length)%focusedDrawerStack.tabs.length]
    selectDrawerTab(next)
  }
  const drawerDirectionLayout=(edge:DrawerEdge)=>moveDrawerTabDirection(drawerLayout,drawerTabId,edge)
  const moveFocusedDrawerTab=(edge:DrawerEdge)=>{
    const next=drawerDirectionLayout(edge)
    if(serializeDrawerLayout(next)===serializeDrawerLayout(drawerLayout))return
    commitDrawerLayout(next,drawerTabId)
    setDrawerAnnouncement(`${drawerTab(drawerTabId).label} moved ${edge}`)
  }
  const fleetVoiceModel=buildFleetReadModel(sessions,projects)
  const fleetItemById=new Map(fleetVoiceModel.sessions.map(item=>[item.session.id,item]))
  const voiceNavigationIndex=buildVoiceNavigationIndex(
    displayProjects,sessions,project=>resolveLayout(layoutMap[project.id],project.layout),
  )
  const relativeVoiceSession=(direction:-1|1):Session|null=>active
    ?adjacentVoiceSession(voiceNavigationIndex,projectId,active.id,direction)
    :null
  const voiceSessionAddress=(item:FleetSession)=>voiceNavigationIndex.sessionAddressById.get(item.session.id)||null
  const voiceProjectNumber=(project:{id?:string})=>project.id
    ?voiceNavigationIndex.projectNumberById.get(project.id)||null
    :null
  const voiceSessionPage=(items:FleetSession[],offset=0,limit=5,detailed=false,compound=false)=>
    sessionListPage(items,offset,limit,detailed,{addressFor:voiceSessionAddress,compound})
  const orderedFleetSessions=(project:Project|null):FleetSession[]=>{
    const ordered=project
      ?voiceNavigationIndex.sessionsByProject.get(project.id)||[]
      :displayProjects.flatMap(item=>voiceNavigationIndex.sessionsByProject.get(item.id)||[])
    return ordered.map(session=>fleetItemById.get(session.id)).filter((item):item is FleetSession=>!!item)
  }
  const orderedVoiceFleetModel={...fleetVoiceModel,sessions:orderedFleetSessions(null)}
  const rememberSpokenContext=(context:SpokenListContext)=>{
    spokenListContext.current=context
    saveSpokenListContext(context)
  }
  const freshSpokenContext=():SpokenListContext|null=>{
    const context=spokenListContext.current||loadSpokenListContext()
    if(!context||context.expiresAt<=Date.now()){
      spokenListContext.current=null
      clearSpokenListContext()
      return null
    }
    spokenListContext.current=context
    return context
  }
  const rememberSessionPage=(items:FleetSession[],pageFrom:number,page:{shownThrough:number;speech:string},compound:boolean)=>{
    rememberSpokenContext({
      kind:'sessions',
      ids:items.map(item=>item.session.id),
      compound,
      pageFrom,
      shownThrough:page.shownThrough,
      expiresAt:Date.now()+SPOKEN_LIST_TTL_MS,
      lastSpeech:page.speech,
    })
  }
  const rememberProjectPage=(items:Project[],pageFrom:number,page:{shownThrough:number;speech:string})=>{
    rememberSpokenContext({
      kind:'projects',ids:items.map(item=>item.id),pageFrom,shownThrough:page.shownThrough,
      expiresAt:Date.now()+SPOKEN_LIST_TTL_MS,lastSpeech:page.speech,
    })
  }
  type SessionResolution={item:FleetSession|null;expectedRun:string|null;candidates:FleetSession[];error:string}
  type ProjectResolution={item:Project|null;candidates:Project[];error:string}
  const resolveSessionReference=(reference:string,projectScope?:Project):SessionResolution=>{
    const normalized=normalizeSpokenText(reference)
    if(normalized==='current'||normalized==='focused'||normalized==='this'){
      const targetId=normalized==='focused'
        ?focusedAgentSession?.id
        :conversation.target?.kind==='session'?conversation.target.id:focusedAgentSession?.id
      const item=targetId?fleetItemById.get(targetId)||null:null
      return item?{item,expectedRun:item.session.agent_run_id||null,candidates:[],error:''}
        :{item:null,expectedRun:null,candidates:[],error:'Focus an agent session first.'}
    }
    const named=fleetVoiceModel.sessions.filter(item=>(!projectScope||item.session.project_id===projectScope.id)
      &&normalizeSpokenText(sessionName(item.session))===normalized)
    if(named.length===1)return{item:named[0],expectedRun:named[0].session.agent_run_id||null,candidates:[],error:''}
    if(named.length>1)return{item:null,expectedRun:null,candidates:named,error:'More than one session has that name.'}
    if(/^\d+$/.test(normalized)){
      const project=projectScope||activeProject
      if(!project)return{item:null,expectedRun:null,candidates:[],error:'Select a Project first, or say open Project 1 Session 1.'}
      const projectSessions=voiceNavigationIndex.sessionsByProject.get(project.id)||[]
      const session=sessionAtVoiceNumber(voiceNavigationIndex,project.id,Number(normalized))
      const item=session?fleetItemById.get(session.id)||null:null
      return item?{item,expectedRun:item.session.agent_run_id||null,candidates:[],error:''}
        :{item:null,expectedRun:null,candidates:[],error:`Project ${project.name} has ${projectSessions.length} session${projectSessions.length===1?'':'s'}. There is no Session ${normalized}.`}
    }
    return{item:null,expectedRun:null,candidates:[],error:`No session named ${reference} is available.`}
  }
  const resolveProjectReference=(reference:string):ProjectResolution=>{
    const normalized=normalizeSpokenText(reference)
    if(normalized==='current'||normalized==='this')return activeProject
      ?{item:activeProject,candidates:[],error:''}
      :{item:null,candidates:[],error:'No project is selected.'}
    const named=projects.filter(project=>normalizeSpokenText(project.name)===normalized)
    if(named.length===1)return{item:named[0],candidates:[],error:''}
    if(named.length>1)return{item:null,candidates:named,error:'More than one project has that name.'}
    if(/^\d+$/.test(normalized)){
      const item=projectAtVoiceNumber(voiceNavigationIndex,Number(normalized))
      return item?{item,candidates:[],error:''}
        :{item:null,candidates:[],error:`There is no Project ${normalized}. There are ${displayProjects.length} visible Projects.`}
    }
    return{item:null,candidates:[],error:`No project named ${reference} is available.`}
  }
  const sessionFilterLabel=(filter:VoiceSessionFilter)=>({
    live:'live',active:'active',working:'working',ready:'ready',needs_me:'needing you',approval:'awaiting approval',
    question:'awaiting your answer',rate_limited:'rate limited',stuck:'stuck',failed:'failed',
  })[filter]
  const resolveVoiceScope=(scope:VoiceScope):{project:Project|null;error:string}=>{
    if(scope.kind==='all')return{project:null,error:''}
    if(scope.kind==='current')return activeProject?{project:activeProject,error:''}:{project:null,error:'No project is selected.'}
    const result=resolveProjectReference(scope.reference)
    return result.item?{project:result.item,error:''}:{project:null,error:result.error}
  }
  const ambiguousSessions=(items:FleetSession[],message:string):VoiceCommandResult=>{
    const ordered=[...items].sort((left,right)=>{
      const a=voiceSessionAddress(left),b=voiceSessionAddress(right)
      return(a?.projectNumber??Number.MAX_SAFE_INTEGER)-(b?.projectNumber??Number.MAX_SAFE_INTEGER)
        ||(a?.sessionNumber??Number.MAX_SAFE_INTEGER)-(b?.sessionNumber??Number.MAX_SAFE_INTEGER)
    })
    const page=voiceSessionPage(ordered,0,5,false,true)
    rememberSessionPage(ordered,0,page,true)
    return{detail:`${message} ${page.detail}`,speech:`${message} ${page.speech}`}
  }
  const ambiguousProjects=(items:Project[],message:string):VoiceCommandResult=>{
    const ordered=[...items].sort((left,right)=>(voiceProjectNumber(left)??Number.MAX_SAFE_INTEGER)-(voiceProjectNumber(right)??Number.MAX_SAFE_INTEGER))
    const page=projectListPage(ordered,0,5,voiceProjectNumber)
    rememberProjectPage(ordered,0,page)
    return{detail:`${message} ${page.detail}`,speech:`${message} ${page.speech}`}
  }
  voiceQueryHandler.current=async(query:VoiceQuery):Promise<VoiceCommandResult>=>{
    if(query.kind==='help'){
      const page=voiceHelpPage(query.category,commandRegistryRef.current,voiceStatus?.commands||[])
      return{detail:page.detail,speech:page.speech,transcript:page.detail}
    }
    if(query.kind==='list_projects'){
      if(!displayProjects.length)return{detail:'No projects are registered.',speech:'No projects are registered.'}
      const page=projectListPage(displayProjects,0,5,voiceProjectNumber)
      rememberProjectPage(displayProjects,0,page)
      return{detail:page.detail,speech:page.speech}
    }
    if(query.kind==='repeat'){
      const context=freshSpokenContext()
      return context?{detail:context.lastSpeech,speech:context.lastSpeech}
        :{detail:'There is no recent spoken list to repeat.',speech:'There is no recent spoken list to repeat.'}
    }
    if(query.kind==='next'){
      const context=freshSpokenContext()
      if(!context)return{detail:'There is no recent spoken list. List sessions or projects first.',speech:'There is no recent spoken list. List sessions or projects first.'}
      if(context.kind==='projects'){
        const items=context.ids.map(id=>projects.find(project=>project.id===id)).filter((item):item is Project=>!!item)
        if(items.length!==context.ids.length)return{detail:'The project list changed. List projects again.',speech:'The project list changed. List projects again.'}
        const page=projectListPage(items,context.shownThrough,5,voiceProjectNumber)
        rememberProjectPage(items,context.shownThrough,page)
        return{detail:page.detail,speech:page.speech}
      }
      const items=context.ids.map(id=>fleetItemById.get(id)).filter((item):item is FleetSession=>!!item)
      if(items.length!==context.ids.length)return{detail:'The session list changed. List sessions again.',speech:'The session list changed. List sessions again.'}
      const page=voiceSessionPage(items,context.shownThrough,5,false,context.compound)
      rememberSessionPage(items,context.shownThrough,page,context.compound)
      return{detail:page.detail,speech:page.speech}
    }
    if(query.kind==='detail'){
      const context=freshSpokenContext()
      if(!context){
        const speech=fleetRundownDetail(orderedVoiceFleetModel,{
          addressFor:voiceSessionAddress,
          compound:true,
        })
        return{detail:speech,speech}
      }
      if(context.kind==='projects'){
        const lines=context.ids.slice(context.pageFrom,context.shownThrough).map((id,index)=>{
          const project=projects.find(item=>item.id===id)
          if(!project)return''
          const live=fleetVoiceModel.sessions.filter(item=>item.session.project_id===id&&!isEndedSession(item.session)).length
          const number=voiceProjectNumber(project)
          return `${number?`Project ${number}, `:'Project '}${project.name}, has ${live} live session${live===1?'':'s'}.`
        }).filter(Boolean)
        const speech=lines.length?lines.join(' '):'The project list changed. List projects again.'
        context.lastSpeech=speech;context.expiresAt=Date.now()+SPOKEN_LIST_TTL_MS;rememberSpokenContext(context)
        return{detail:speech,speech}
      }
      const items=context.ids.map(id=>fleetItemById.get(id)).filter((item):item is FleetSession=>!!item)
      if(items.length!==context.ids.length)return{detail:'The session list changed. List sessions again.',speech:'The session list changed. List sessions again.'}
      const page=voiceSessionPage(items,context.pageFrom,context.shownThrough-context.pageFrom,true,context.compound)
      context.lastSpeech=page.speech;context.expiresAt=Date.now()+SPOKEN_LIST_TTL_MS;rememberSpokenContext(context)
      return{detail:page.detail,speech:page.speech}
    }
    if(query.kind==='list_sessions'){
      const scope=resolveVoiceScope(query.scope)
      if(scope.error)return{detail:scope.error,speech:scope.error}
      const items=orderedFleetSessions(scope.project).filter(item=>voiceSessionFilterMatches(item,query.filter))
      if(!items.length){
        const speech=`No ${sessionFilterLabel(query.filter)} sessions${scope.project?` in ${scope.project.name}`:' overall'}.`
        return{detail:speech,speech}
      }
      const compound=!scope.project
      const page=voiceSessionPage(items,0,5,false,compound)
      rememberSessionPage(items,0,page,compound)
      return{detail:page.detail,speech:page.speech}
    }
    if(query.kind==='open'){
      if(query.entity==='project'){
        const result=resolveProjectReference(query.reference)
        if(result.candidates.length)return ambiguousProjects(result.candidates,result.error)
        if(!result.item)return{detail:result.error,speech:result.error}
        const ran=runCommand(commandRegistryRef.current,`project.focus:${result.item.id}`)
        const speech=ran==='ran'?`Opened project ${result.item.name}.`:`Project ${result.item.name} cannot be opened.`
        return{detail:speech,speech}
      }
      const projectResult=query.projectReference?resolveProjectReference(query.projectReference):null
      if(projectResult?.candidates.length)return ambiguousProjects(projectResult.candidates,projectResult.error)
      if(projectResult&&!projectResult.item)return{detail:projectResult.error,speech:projectResult.error}
      const result=resolveSessionReference(query.reference,projectResult?.item||undefined)
      if(result.candidates.length)return ambiguousSessions(result.candidates,result.error)
      if(!result.item)return{detail:result.error,speech:result.error}
      if(isEndedSession(result.item.session))return{detail:'That session has ended and cannot be opened.',speech:'That session has ended and cannot be opened.'}
      const ran=runCommand(commandRegistryRef.current,`session.focus:${result.item.session.id}`)
      const address=voiceSessionAddress(result.item)
      const speech=ran==='ran'
        ?`Opened ${address?`Project ${address.projectNumber}, Session ${address.sessionNumber}, `:''}${sessionName(result.item.session)} in ${result.item.projectName}.`
        :'That session cannot be opened.'
      return{detail:speech,speech}
    }
    if(query.kind==='read_reply'){
      const result=resolveSessionReference(query.reference)
      if(result.candidates.length)return ambiguousSessions(result.candidates,result.error)
      if(!result.item)return{detail:result.error,speech:result.error}
      const session=result.item.session
      if(!isAgent(session))return{detail:'Read reply requires an agent session.',speech:'Read reply requires an agent session.'}
      if(result.expectedRun!==null&&(session.agent_run_id||null)!==result.expectedRun){
        return{detail:'That numbered session started a new agent run. List sessions again before reading it.',speech:'That numbered session started a new agent run. List sessions again before reading it.'}
      }
      if(!voiceStatus?.enabled)return{detail:'Read aloud is off. Enable it in Settings, Voice.',speech:'Read aloud is off. Enable it in Settings, Voice.'}
      unlockPlayback()
      const body=query.mode==='current'?{}:{content_mode:query.mode as VoiceContent}
      const clip=await api<VoiceClip>('POST',`/api/sessions/${session.id}/voice/generate`,body)
      await playClip(clip.id,session.id)
      const mode=query.mode==='current'?(session.voice_content||voiceStatus.content):query.mode
      return{detail:`Reading ${sessionName(session)}'s last reply ${mode}.`,transcript:clip.text}
    }
    if(query.kind==='status'){
      if(query.entity==='session'){
        const result=resolveSessionReference(query.reference)
        if(result.candidates.length)return ambiguousSessions(result.candidates,result.error)
        if(!result.item)return{detail:result.error,speech:result.error}
        const speech=`${sessionName(result.item.session)} in ${result.item.projectName} is ${spokenSessionStatus(result.item,true)}.`
        return{detail:speech,speech}
      }
      const projectResult=query.entity==='project'?resolveProjectReference(query.reference):null
      if(projectResult?.candidates.length)return ambiguousProjects(projectResult.candidates,projectResult.error)
      if(projectResult&&!projectResult.item)return{detail:projectResult.error,speech:projectResult.error}
      const scope=query.entity==='fleet'?resolveVoiceScope(query.scope):{project:projectResult?.item||null,error:''}
      if(scope.error)return{detail:scope.error,speech:scope.error}
      const scopedSessions=orderedFleetSessions(scope.project).map(item=>item.session)
      const model=buildFleetReadModel(scopedSessions,projects)
      const speech=`${scope.project?`${scope.project.name}. `:''}${fleetRundown(model)}`
      const live=model.sessions.filter(item=>!isEndedSession(item.session))
      if(live.length){
        const compound=!scope.project
        const page=voiceSessionPage(live,0,5,false,compound)
        rememberSessionPage(live,0,{...page,speech},compound)
      }
      return{detail:speech,speech}
    }
    return{detail:'That voice query is not available.'}
  }
  const sessionVoiceAliases=(session:Session):string[]=>{
    if(session.awaiting_reason==='approval')return ['go to the one waiting for approval','show approvals','open approval']
    if(session.awaiting_reason==='question'||session.awaiting_reason==='elicitation')return ['go to the one waiting for an answer','show questions']
    if(session.awaiting_reason==='rate_limit')return ['go to the rate limited one']
    if(session.delivery_readiness?.state==='unknown'||((session.state==='working'||session.state==='running')&&serverNow()-session.last_activity_ts>300))return ['go to the stuck one']
    if(session.state==='working'||session.state==='running')return ['go to the working one']
    if(session.state==='idle')return ['go to the idle one']
    if(session.state==='crashed')return ['go to the crashed one']
    return []
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
    { id: 'tab.next', label: 'Focus next workspace tab', category: 'pane', available: mobileWorkspace ? leaves(activeLayout).length > 1 : !!activeStack && activeStack.children.length > 1, disabledReason: mobileWorkspace ? 'Only one tab is open in this project' : 'Only one tab is open in the focused pane', run: () => navigateWorkspaceTab(1) },
    { id: 'tab.previous', label: 'Focus previous workspace tab', category: 'pane', available: mobileWorkspace ? leaves(activeLayout).length > 1 : !!activeStack && activeStack.children.length > 1, disabledReason: mobileWorkspace ? 'Only one tab is open in this project' : 'Only one tab is open in the focused pane', run: () => navigateWorkspaceTab(-1) },
    { id: 'mobileTab.next', label: 'Focus next tab (mobile)', category: 'pane', available: mobileWorkspace, disabledReason: 'Available on the mobile workspace', run: () => navigateMobileTab(1) },
    { id: 'mobileTab.previous', label: 'Focus previous tab (mobile)', category: 'pane', available: mobileWorkspace, disabledReason: 'Available on the mobile workspace', run: () => navigateMobileTab(-1) },
    { id: 'sidebar.open', label: 'Open navigation sidebar', category: 'view', available: true, run: () => setNavigationSidebarOpen(true), voice:{
      phrases:['open navigation','show navigation','open navigation sidebar','show navigation sidebar','open left sidebar','show left sidebar'],
    } },
    // Unconditionally available, and deliberately not gated on `dismissStack.depth()`:
    // `available` is a render-time snapshot, but a drill-down level (History's transcript)
    // lives in its own component's state and opens without re-rendering App. A stale
    // `false` would make `runCommand` refuse the command and toast at exactly the moment
    // the user swiped back. `pop()` is already inert on an empty stack, which is the same
    // outcome without the lie. Subscribing App to the stack instead would re-render the
    // whole shell on every overlay open for a greyed-out palette row.
    { id: 'nav.back', label: 'Back (close one overlay level)', category: 'view', available: true, run: () => { dismissStack.pop() } },
    { id: 'sidebar.close', label: 'Close navigation sidebar', category: 'view', available: true, run: () => setNavigationSidebarOpen(false), voice:{
      phrases:['close navigation','hide navigation','close navigation sidebar','hide navigation sidebar','close left sidebar','hide left sidebar'],
    } },
    { id: 'sidebar.toggle', label: 'Toggle navigation sidebar', category: 'view', available: true, run: () => setSidebarOpen(value => !value) },
    { id:'prompts.open',label:'Open prompt library',category:'input',available:true,run:()=>{setPromptScope(null);setPromptTargetId(null);setPromptLibraryOpen(true);setMainMenuOpen(false)} },
    { id:'prompts.openProject',label:'Open prompt library for selected project',category:'input',available:!!commandProject,disabledReason:'No project selected',run:()=>{setPromptScope(commandProject||null);setPromptTargetId(null);setPromptLibraryOpen(true);setMainMenuOpen(false);setProjectMenu(null)} },
    { id:'queue.fleet',label:'Open fleet queue (every session’s queued messages)',category:'input',available:true,run:()=>openFleetQueue() },
    { id:'queue.fleetProject',label:'Open fleet queue for selected project',category:'input',available:!!commandProject,disabledReason:'No project selected',run:()=>commandProject&&openFleetQueue(commandProject.id) },
    // The emergency stop, reachable with nothing open. Its label names the act, not the
    // state, because a command list is read as a list of things you can do.
    { id:'autodelivery.pause',label:autoStatus?.paused?'Resume auto-delivery (install-wide)':'Pause all auto-delivery (install-wide)',category:'input',available:true,run:()=>void toggleAutoPaused() },
    { id:'queue.open',label:'Open the prompt queue for the focused session',category:'input',available:!!active&&deliversHarnessPrompts(active.backend),disabledReason:'Focus an agent session',run:()=>{if(active)void openQueueForSession(active.id)} },
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
    { id: 'actions.configure', label: 'Configure Actions', category: 'view', available: true, run: openActionEditor },
    { id: 'usage.open', label: 'Open usage analytics', category: 'view', available: true, run: () => {setUsageOpen(true);setMainMenuOpen(false)} },
    { id: 'networkUsage.open', label: 'Open bandwidth usage', category: 'view', available: true, run: () => {setNetworkUsageOpen(true);setMainMenuOpen(false)} },
    { id: 'storageUsage.open', label: 'Open storage usage', category: 'view', available: true, run: () => {setStorageUsageOpen(true);setMainMenuOpen(false)} },
    { id: 'hooks.open', label: 'Open Automation', category: 'view', available: true, run: () => {setAutomationOpen(true);setMainMenuOpen(false)} },
    { id: 'notifications.open', label: `Open notifications${notificationUnread?` (${notificationUnread} new)`:''}`, category: 'view', available: true, run: openNotifications },
    { id: 'notes.scratchpad', label: 'Open global Scratchpad', category: 'view', available: !!activeProject, disabledReason: 'No project workspace available', run: () => openScratchpad('drawer') },
    { id: 'notes.open', label: 'Open current project’s notes', category: 'view', available: !!activeProject, disabledReason: 'No project selected', run: () => activeProject&&openNotesBrowser(activeProject) },
    { id: 'notes.browse', label: 'Browse all notes', category: 'view', available: true, run: () => openNotesBrowser(null) },
    { id: 'notes.browseProject', label: 'Browse this project’s notes', category: 'view', available: !!activeProject, disabledReason: 'No project selected', run: () => activeProject&&openNotesBrowser(activeProject) },
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
    { id: 'note.outline', label: 'Jump to a heading in the focused note', category: 'view', available: true, run: () => {
      const claim = new CustomEvent('mux:note-outline', { cancelable: true })
      window.dispatchEvent(claim)
      if (!claim.defaultPrevented) setError('No focused note to outline. Click into a note first.')
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
    { id:'drawer.open',label:'Open side panel',category:'view',available:true,run:()=>{setClipboardOpen(true);setMainMenuOpen(false);setContextMenu(null)},voice:{
      phrases:['open side panel','show side panel','open right sidebar','show right sidebar','open utility sidebar','show utility sidebar'],
    }},
    { id:'drawer.close',label:'Close side panel',category:'view',available:true,run:()=>{setClipboardOpen(false);setMainMenuOpen(false);setContextMenu(null)},voice:{
      phrases:['close side panel','hide side panel','close right sidebar','hide right sidebar','close utility sidebar','hide utility sidebar'],
    }},
    { id:'drawer.peekActions',label:'Open Actions temporarily',category:'view',available:true,run:peekActions },
    ...DRAWER_TABS.map((tab): Command => ({
      id: `drawer.${tab.id}`, label: `Side panel: ${tab.label}`, category: tab.id === 'notifications' ? 'view' : 'clipboard',
      available: true, run: () => showDrawerTab(tab.id),
    })),
    ...DRAWER_TABS.map((tab): Command => ({
      id:`drawer.show:${tab.id}`,label:`Open ${tab.label}`,category:'view',available:true,
      run:()=>openDrawerTab(tab.id),voice:{
        phrases:[`open ${tab.label}`,`show ${tab.label}`,`go to ${tab.label}`],
        execute:()=>{
          openDrawerTab(tab.id)
          if(tab.id!=='notes')return{detail:`Opened ${tab.label}. Still listening.`}
          if(!drawerNoteId){
            setDrawerNoteClaimRequest(null)
            return{detail:'Opened Notes. Select a note before using Send or Append.'}
          }
          const token=++drawerNoteClaimSequence.current
          setDrawerNoteClaimRequest({token,projectId,resourceId:drawerNoteId})
          return{detail:'Opened Notes and targeted the current note. Still listening.'}
        },
      },
    })),
    // Tab order is persistent state a drag can scramble, so it needs a way back that is not
    // "drag five tabs into place from memory".
    { id: 'drawer.resetLayout', label: 'Reset side panel layout', category: 'view', available: !isDefaultDrawerLayout(drawerLayout), disabledReason: 'Side panel layout is already at its default', run: resetDrawerArrangement },
    { id: 'drawer.next', label: 'Side panel: focus next tab in pane', category: 'view', available: !!focusedDrawerStack&&focusedDrawerStack.tabs.length>1, disabledReason: 'The focused side panel pane has one tab', run: ()=>navigateDrawerTab(1) },
    { id: 'drawer.previous', label: 'Side panel: focus previous tab in pane', category: 'view', available: !!focusedDrawerStack&&focusedDrawerStack.tabs.length>1, disabledReason: 'The focused side panel pane has one tab', run: ()=>navigateDrawerTab(-1) },
    ...([['left','Left'],['right','Right'],['top','Up'],['bottom','Down']] as const).map(([edge,name]):Command=>({
      id:`drawer.move${name}`,
      label:`Side panel: move focused tab ${name.toLowerCase()}`,
      category:'view',
      available:serializeDrawerLayout(drawerDirectionLayout(edge))!==serializeDrawerLayout(drawerLayout),
      disabledReason:'The focused tab cannot move in that direction',
      run:()=>moveFocusedDrawerTab(edge),
    })),
    { id: 'clipboard.open', label: 'Open clipboard history', category: 'clipboard', available: true, run: () => showDrawerTab('clipboard') },
    { id: 'clipboard.clear', label: 'Clear unpinned clipboard history', category: 'clipboard', available: true, run: () => void clearClipboardHistory().then(removed => { window.dispatchEvent(new CustomEvent(CLIPBOARD_CHANGED_EVENT)); setError(`Cleared ${removed} clipboard entr${removed===1?'y':'ies'}.`) }).catch(cause => setError(cause instanceof Error?cause.message:String(cause))) },
    ...railVoiceEntries.map((entry):Command=>({
      id:`terminal.railVoice:${entry.item.id}`,
      label:`Focused session: ${entry.item.title||entry.item.label}`,
      category:entry.request.action==='pasteText'?'clipboard':'terminal',
      available:!!focusedTerminalSession&&!isEndedSession(focusedTerminalSession),
      disabledReason:'Focus a running session',
      run:()=>focusedTerminalSession&&requestTerminalAction(focusedTerminalSession.id,entry.request).catch(cause=>setError(cause instanceof Error?cause.message:String(cause))),
      voice:{
        phrases:entry.phrases,
        execute:async()=>{
          if(!focusedTerminalSession||isEndedSession(focusedTerminalSession))return{detail:'Focus a running session first.'}
          try{
            await requestTerminalAction(focusedTerminalSession.id,entry.request)
            return{detail:railVoiceConfirmation(entry)}
          }catch(cause){
            return{detail:cause instanceof Error?cause.message:String(cause)}
          }
        },
      },
    })),
    ...(['copy', 'paste', 'selectAll', 'clear'] as const).map((action): Command => ({
      id: `terminal.${action}`, label: `${action === 'selectAll' ? 'Select all' : action[0].toUpperCase() + action.slice(1)} in focused terminal`,
      category: 'clipboard', available: !!active, disabledReason: 'No focused terminal',
      run: () => window.dispatchEvent(new CustomEvent('mux:terminal-action', { detail: { sessionId: activeId, action } })),
      voice:action==='copy'?{
        phrases:['copy','copy selection'],
        execute:async()=>{
          if(!focusedTerminalSession)return{detail:'Focus a session first.'}
          try{
            await requestTerminalAction(focusedTerminalSession.id,{action:'copy'})
            return{detail:'Copied the terminal selection. Still listening.'}
          }catch(cause){
            return{detail:cause instanceof Error?cause.message:String(cause)}
          }
        },
      }:undefined,
    })),
    { id: 'session.kill', label: active && isEndedSession(active) ? 'Remove focused session from sidebar' : 'Kill focused session', category: 'session', available: !!active, disabledReason: 'No focused session', run: () => active && requestKill(active) },
    { id: 'session.killImmediate', label: commandSession && isEndedSession(commandSession) ? 'Remove selected session from sidebar' : 'Kill selected session immediately', category: 'session', available: !!commandSession, disabledReason: 'No session selected', run: () => commandSession && void killNow(commandSession) },
    { id: 'session.relaunch', label: 'Relaunch focused task terminal', category: 'session', available: !!active && !!active.relaunchable, disabledReason: 'Relaunch is available for task-launched terminals', run: () => active && void relaunchSession(active) },
    { id: 'session.pinAttention', label: active?.pinned_attention ? 'Unpin focused session attention' : 'Pin focused session attention', category: 'session', available: !!active && isAgent(active), disabledReason: 'Attention pinning requires a focused agent', run: () => active && void api<Session>('PATCH', `/api/sessions/${active.id}`, { pin: !active.pinned_attention }).then(updateSession) },
    { id: 'voice.toggleTalk', label: conversation.active||conversation.phase!=='off'?'Stop hands-free conversation':'Start hands-free conversation', category: 'voice', available: !!voiceStatus?.stt_enabled, disabledReason: 'Enable microphone conversation in Settings first', run: () => conversation.toggle() },
    { id: 'voice.toggleTargetPin', label: conversation.pinned?'Voice dictation: follow workspace focus':'Voice dictation: pin current target', category: 'voice', available: !!conversation.target, disabledReason: 'Focus an agent or text surface first', run: () => conversation.togglePin() },
    { id: 'voice.cycleMode', label: `Read aloud: cycle focused session mode${active && isAgent(active) ? ` (now ${voiceModeLabel(effectiveVoiceMode(active))})` : ''}`, category: 'voice', available: !!active && isAgent(active) && !!voiceStatus?.enabled, disabledReason: 'Read aloud requires a focused agent and TTS enabled in Settings', run: () => { if (active) cycleVoiceMode(active); setContextMenu(null) } },
    { id: 'voice.speak', label: 'Read aloud: speak focused session’s last reply', category: 'voice', available: !!active && isAgent(active) && !!voiceStatus?.enabled, disabledReason: 'Read aloud requires a focused agent and TTS enabled in Settings', run: () => { if (active) void speakLastReply(active); setContextMenu(null) } },
    { id: 'voice.autoplayDevice', label: `Read aloud: turn device autoplay ${autoplayEnabled() ? 'off' : 'on'}`, category: 'voice', available: !!voiceStatus?.enabled, disabledReason: 'Enable read aloud in Settings first', run: () => { setAutoplayEnabled(!autoplayEnabled()); setContextMenu(null) } },
    { id:'voice.fleetStatus',label:'Speak fleet status',category:'voice',available:true,run:()=>{},voice:{
      phrases:['fleet status','status report','what is running'],
      execute:text=>voiceQueryHandler.current(parseVoiceQuery(text||'fleet status')||{kind:'status',entity:'fleet',reference:'',scope:{kind:'all'}}),
    }},
    { id:'voice.fleetStatusDetail',label:'Speak detailed fleet status',category:'voice',available:true,run:()=>{},voice:{
      phrases:['detailed fleet status','full status report','status details'],
      execute:()=>{const speech=fleetRundownDetail(orderedVoiceFleetModel,{addressFor:voiceSessionAddress,compound:true});return{detail:speech,speech}},
    }},
    { id:'voice.query',label:'Ask a deterministic voice lookup',category:'voice',available:true,run:()=>{},voice:{
      phrases:['{text}'],
      execute:text=>{
        const query=parseVoiceQuery(text)
        if(!query){
          const detail=`No voice command matched “${text}”. Say “${voiceStatus?.wake_words?.[0]||'Mux'}, list voice commands” for help.`
          return{detail,speech:detail}
        }
        return voiceQueryHandler.current(query)
      },
    }},
    { id:'voice.approval.prepare',label:'Review focused approval',category:'voice',available:!!active&&active.state==='awaiting'&&active.awaiting_reason==='approval',disabledReason:'Focus a session waiting for approval first',run:()=>{},voice:{
      phrases:['approve','review approval','confirm tool use'],
      execute:async()=>{
        if(!active)throw new Error('Focus a session waiting for approval first.')
        const prepared=await api<{confirmation_id:string;operation:string}>('POST',`/api/sessions/${active.id}/voice/approval`,{action:'prepare'})
        setApprovalConfirmation({sessionId:active.id,confirmationId:prepared.confirmation_id,operation:prepared.operation})
        const speech=`Approve the currently highlighted choice for ${prepared.operation}? Say ${conversation.wake}, confirm approval.`
        return{detail:speech,speech}
      },
    }},
    { id:'voice.approval.confirm',label:'Confirm reviewed approval',category:'voice',available:!!approvalConfirmation,disabledReason:'Review one focused approval first',run:()=>{},voice:{
      phrases:['confirm approval','yes approve it'],
      execute:async()=>{
        const confirmation=approvalConfirmation
        if(!confirmation)throw new Error('Review one focused approval first.')
        let result:{operation:string}
        try{result=await api<{operation:string}>('POST',`/api/sessions/${confirmation.sessionId}/voice/approval`,{action:'confirm',confirmation_id:confirmation.confirmationId})}
        finally{setApprovalConfirmation(null)}
        return{detail:`Approved ${result.operation}. Still listening.`,speech:`Approved ${result.operation}.`}
      },
    }},
    { id:'voice.approval.cancel',label:'Cancel voice approval confirmation',category:'voice',available:!!approvalConfirmation,disabledReason:'No voice approval is pending',run:()=>{},voice:{
      phrases:['cancel approval','do not approve'],
      execute:async()=>{
        const confirmation=approvalConfirmation
        try{if(confirmation)await api('POST',`/api/sessions/${confirmation.sessionId}/voice/approval`,{action:'cancel'})}
        finally{setApprovalConfirmation(null)}
        return{detail:'Voice approval cancelled. The tool prompt is unchanged.',speech:'Approval cancelled.'}
      },
    }},
    { id: 'session.open', label: 'Open selected session in focused pane', category: 'session', available: !!commandSession && !['exited', 'crashed'].includes(commandSession.state), disabledReason: 'No live session selected', run: () => commandSession && void selectSession(commandSession) },
    { id:'session.nextInProject',label:'Go to next session in current Project',category:'session',available:!!activeProject&&!!active,disabledReason:'Focus a session in a Project first',run:()=>{const target=relativeVoiceSession(1);if(target)void selectSession(target)},voice:{
      phrases:['go to next session','next session','open next session'],
      execute:async()=>{
        const target=relativeVoiceSession(1)
        if(!target)return{detail:'This is the last session in the current Project.',speech:'This is the last session in the current Project.'}
        await selectSession(target)
        const number=voiceNavigationIndex.sessionAddressById.get(target.id)?.sessionNumber
        const detail=`Opened Session ${number||''}${number?' - ':''}${sessionName(target)}.`
        return{detail,speech:detail}
      },
    }},
    { id:'session.previousInProject',label:'Go to previous session in current Project',category:'session',available:!!activeProject&&!!active,disabledReason:'Focus a session in a Project first',run:()=>{const target=relativeVoiceSession(-1);if(target)void selectSession(target)},voice:{
      phrases:['go to previous session','previous session','open previous session','go to prior session'],
      execute:async()=>{
        const target=relativeVoiceSession(-1)
        if(!target)return{detail:'This is the first session in the current Project.',speech:'This is the first session in the current Project.'}
        await selectSession(target)
        const number=voiceNavigationIndex.sessionAddressById.get(target.id)?.sessionNumber
        const detail=`Opened Session ${number||''}${number?' - ':''}${sessionName(target)}.`
        return{detail,speech:detail}
      },
    }},
    { id: 'session.rename', label: 'Rename selected session', category: 'session', available: !!commandSession, disabledReason: 'No session selected', run: () => commandSession && openRename({ kind: 'session', session: commandSession }) },
    { id: 'session.regenerateTitle', label: 'Regenerate generated title', category: 'session', available: !!commandSession && isAgent(commandSession) && commandSession.auto_named !== false && !isEndedSession(commandSession), disabledReason: 'Select a live auto-named agent session', run: () => commandSession && void regenerateSessionTitle(commandSession) },
    { id: 'session.clearStandingActivity', label: 'Clear standing activity (subagents / background tasks)', category: 'session', available: !!commandSession && activityBadges(commandSession).length > 0, disabledReason: 'Select a session with a standing-activity badge', run: () => commandSession && void clearStandingActivity(commandSession) },
    { id: 'session.toggleRead', label: commandSession && isUnread(commandSession, ackedTurns) ? 'Mark selected session read' : 'Mark selected session unread', category: 'session', available: !!commandSession && isAgent(commandSession) && !isEndedSession(commandSession), disabledReason: 'Read state is tracked for live agent sessions', run: () => commandSession && void toggleSessionRead(commandSession) },
    { id: 'session.copyId', label: 'Copy selected session ID', category: 'clipboard', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) void navigator.clipboard.writeText(commandSession.id).catch(() => setError('Clipboard access was blocked.')) ; setContextMenu(null) } },
    { id: 'session.copyCwd', label: 'Copy selected working directory', category: 'clipboard', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) void navigator.clipboard.writeText(workingCwd(commandSession)).catch(() => setError('Clipboard access was blocked.')); setContextMenu(null) } },
    { id: 'session.openSplitHorizontal', label: 'Open selected session in split right', category: 'pane', available: !!commandSession && !['exited', 'crashed'].includes(commandSession.state), disabledReason: 'No live session selected', run: () => commandSession && void openInSplit(commandSession, 'horizontal') },
    { id: 'session.openSplitVertical', label: 'Open selected session in split below', category: 'pane', available: !!commandSession && !['exited', 'crashed'].includes(commandSession.state), disabledReason: 'No live session selected', run: () => commandSession && void openInSplit(commandSession, 'vertical') },
    { id: 'session.groupStack', label: 'Stack selected session with focused terminal', category: 'pane', available: !!commandSession&&!!activeId&&commandSession.id!==activeId&&commandSession.project_id===projectId, disabledReason: 'Choose two live sessions in the same project', run:()=>commandSession&&activeId&&void updateLayout(projectId,groupTerminalsInStack(activeLayout,activeId,commandSession.id)) },
    { id: 'session.reveal', label: 'Reveal selected working directory', category: 'session', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) void api('POST', '/api/reveal', { path: commandSession.cwd }); setContextMenu(null) } },
    { id: 'session.customSplit', label: 'New custom terminal in selected session split', category: 'pane', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) { setContextMenu(null); openLauncher(commandSession.project_id, 'horizontal') } } },
    { id: 'session.broadcastMembership', label: commandSession?.broadcast ? 'Remove selected session from broadcast' : 'Add selected session to broadcast', category: 'input', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) void api<Session>('POST', `/api/sessions/${commandSession.id}/broadcast-set`, { include: !commandSession.broadcast }).then(updated => { updateSession(updated); setContextMenu(null) }) } },
    { id: 'session.resume', label: 'Resume selected agent as new', category: 'history', available: !!commandSession && isAgent(commandSession) && ['exited', 'crashed'].includes(commandSession.state), disabledReason: 'Select an exited agent session', run: () => commandSession && void resumeSession(commandSession) },
    { id: 'project.newTerminal', label: 'New terminal in selected project', category: 'project', available: !!commandProject, disabledReason: 'No project selected', run: () => { if (commandProject) void spawnTerminal(commandProject.id); setProjectMenu(null) } },
    { id: 'project.newTerminalCustom', label: 'New custom terminal in selected project', category: 'project', available: !!commandProject, disabledReason: 'No project selected', run: () => { if (commandProject) openLauncher(commandProject.id); setProjectMenu(null) } },
    { id: 'project.reveal', label: 'Reveal selected project in Explorer', category: 'project', available: !!commandProject, disabledReason: 'No project selected', run: () => { if (commandProject) void api('POST', '/api/reveal', { path: commandProject.root }); setProjectMenu(null) } },
    { id: 'project.rename', label: 'Rename selected project', category: 'project', available: !!commandProject, disabledReason: 'No project selected', run: () => commandProject && openRename({ kind: 'project', project: commandProject }) },
    // "First"/"last" against the sorted view, not the stored positions: with a sort
    // active they disagree, and the enabled state has to describe what is on screen.
    { id:'project.moveUp',label:'Move selected Project up',category:'project',available:!!commandProject&&displayProjects.filter(item=>groupIdFor(item)===groupIdFor(commandProject))[0]?.id!==commandProject.id,disabledReason:'Project is already first here',run:()=>commandProject&&moveProjectRelative(commandProject,-1) },
    { id:'project.moveDown',label:'Move selected Project down',category:'project',available:!!commandProject&&displayProjects.filter(item=>groupIdFor(item)===groupIdFor(commandProject)).at(-1)?.id!==commandProject.id,disabledReason:'Project is already last here',run:()=>commandProject&&moveProjectRelative(commandProject,1) },
    { id: 'project.settings', label: 'Open selected project settings', category: 'project', available: !!commandProject, disabledReason: 'No project selected', run: () => commandProject && openProjectsManager({ project: commandProject, tab: 'settings' }) },
    { id: 'project.delete', label: 'Remove selected Project from swe-mux…', category: 'project', available: !!commandProject, disabledReason: 'No Project selected', run: () => commandProject&&openProjectsManager({project:commandProject,tab:'details'}) },
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
    // Added when `Move tab` left the context menus: drag covers it by pointer, but
    // without these there would be no keyboard route to move a tab between panes at
    // all, and nothing to bind a key to. One per direction, availability read from the
    // live split tree so a direction with no neighbour says why it is disabled.
    ...paneDirectionOptions.map((option): Command => {
      const leaf = focusedTabId ? leaves(activeLayout).find(item => item.id === focusedTabId) || null : null
      return {
        id: `pane.moveTab${option.id[0].toUpperCase()}${option.id.slice(1)}`,
        label: `Move focused tab ${option.id}`, category: 'pane',
        available: !!leaf && !!paneNeighborIds(activeLayout, leaf.id)[option.id],
        disabledReason: 'No pane in that direction',
        run: () => { if (leaf) void moveTabDirection(leaf, projectId, option.id) },
      }
    }),
    { id: 'pane.zoom', label: zoomedId ? 'Restore pane layout' : 'Zoom focused pane', category: 'pane', available: !!focusedTabId && workspacePanes.length > 1, disabledReason: 'Zoom requires multiple panes', run: () => setZoomedId(zoomedId ? null : focusedTabId) },
    { id: 'pane.next', label: 'Focus next pane', category: 'pane', available: workspacePanes.length > 1, disabledReason: 'Only one pane is open', run: () => focusRelativePane(1) },
    { id: 'pane.previous', label: 'Focus previous pane', category: 'pane', available: workspacePanes.length > 1, disabledReason: 'Only one pane is open', run: () => focusRelativePane(-1) },
    { id: 'broadcast.toggle', label: broadcast ? 'Stop broadcasting input' : 'Start broadcasting input', category: 'input', available: true, run: () => setBroadcast(value => !value) },
    ...displayProjects.slice(0, 9).map((project, index): Command => ({
      id: `project.activate(${index + 1})`, label: `Switch to project ${index + 1}: ${project.name}`,
      category: 'project', available: project.id !== projectId, disabledReason: 'Project is already active',
      run: () => { const layout=layoutMap[project.id]||emptyLayout();const first=leaves(layout)[0]||null;setProjectId(project.id);setFocusedViewId(first?.id||null);setActiveId(terminalIds(layout)[0]||null) },
    })),
    ...projects.map((project):Command=>({
      id:`project.focus:${project.id}`,label:`Focus project: ${project.name}`,category:'project',available:true,
      run:()=>selectProject(project.id),voice:{phrases:[`go to project ${project.name}`,`open project ${project.name}`,`switch to ${project.name}`]},
    })),
    ...sessions.filter(session=>!session.pending&&!isEndedSession(session)).map((session):Command=>({
      id:`session.focus:${session.id}`,label:`Focus session: ${sessionName(session)}`,category:'session',available:true,
      run:()=>void selectSession(session),voice:{phrases:[`go to session ${sessionName(session)}`,`open session ${sessionName(session)}`,`focus ${sessionName(session)}`,...sessionVoiceAliases(session)]},
    })),
    ...projects.flatMap(project=>allBackendNames().map((backend):Command=>({
      id:`session.spawn:${project.id}:${backend}`,label:`New ${harnessDisplayName(backend)} in ${project.name}`,category:'session',available:true,
      run:()=>void spawnTerminal(project.id,false,undefined,undefined,'after',backend),
      voice:{
        phrases:sessionLaunchVoicePhrases({
          backend,
          displayName:harnessDisplayName(backend),
          projectName:project.name,
          projectNumber:voiceProjectNumber(project),
          currentProject:project.id===projectId,
        }),
        execute:async text=>{
          const started=await spawnTerminal(project.id,false,undefined,undefined,'after',backend,{seedText:text||undefined})
          return{detail:started
            ?`Started ${harnessDisplayName(backend)} in ${project.name}${text?' with the spoken seed':''}. Still listening.`
            :'The session could not be started. Still listening.'}
        },
      },
    }))),
  ]
  commandRegistryRef.current=commands
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
      // One level, not everything on screen. This handler stays bubble-phase on window so
      // a surface that owns Escape for itself — the utility drawer's focus-scoped handler,
      // the shortcut recorder in Settings — still shields it by stopping propagation.
      if (event.key === 'Escape') dismissStack.pop()
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
      setDrawerDisplayMenu(null)
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

  // An Action rail prompt button with {{placeholders}} opens Prompt templates on that template.
  // This deliberately opens rather than toggling (`showDrawerTab`): the click already
  // said "I want this template", so closing the drawer on it would be perverse.
  useEffect(() => {
    const onPromptTemplate = (event: Event) => {
      const detail = (event as CustomEvent<{ key?: string }>).detail
      if (!detail?.key) return
      setPromptPreselect({ key: detail.key })
      // A variable prompt chosen inside a temporary Actions visit still needs the
      // same drawer to fill its fields. Keep the override and, crucially, do not
      // promote Actions into the Project's persistent selection.
      if (transientDrawerTab === 'actions') { setClipboardOpen(true); return }
      openDrawerTab('actions')
    }
    window.addEventListener(PROMPT_RAIL_EVENT, onPromptTemplate)
    return () => window.removeEventListener(PROMPT_RAIL_EVENT, onPromptTemplate)
  })

  // Everything the composition root itself can put on screen, as dismiss levels. These all
  // register at mount because the root is always mounted, so their stack position comes
  // from when each one opens rather than from the order they appear here.
  //
  // The slide-in panels are mobile-only levels on purpose. On desktop the sidebar and the
  // utility drawer are docked chrome, not overlays: `clipboardOpen` there is a persisted
  // expansion that is routinely true for the whole session, and registering it would keep
  // the stack permanently non-empty, arm the history sentinel forever, and make the
  // browser's Back button stop working. The drawer keeps its own element-scoped Escape
  // (`UtilityDrawer.tsx`) for the docked case, which is focus-scoped and shielded from
  // this handler by its `stopPropagation`.
  useDismissLevel(() => setSidebarOpen(false), mobileWorkspace && sidebarOpen, 'sidebar')
  useDismissLevel(() => setClipboardOpen(false), mobileWorkspace && clipboardOpen, 'utility-drawer')
  // Menus. Each is its own level so back closes the one that is open rather than all nine.
  useDismissLevel(() => setContextMenu(null), !!contextMenu, 'session-menu')
  useDismissLevel(() => setProjectMenu(null), !!projectMenu, 'project-menu')
  useDismissLevel(() => setSidebarMenu(null), !!sidebarMenu, 'sidebar-menu')
  useDismissLevel(() => setSortMenu(null), !!sortMenu, 'sort-menu')
  useDismissLevel(() => setNoteMenu(null), !!noteMenu, 'note-menu')
  useDismissLevel(() => setTabMenu(null), !!tabMenu, 'tab-menu')
  useDismissLevel(() => setEmptyMenu(null), !!emptyMenu, 'empty-menu')
  useDismissLevel(() => setDrawerDisplayMenu(null), !!drawerDisplayMenu, 'drawer-display-menu')
  useDismissLevel(() => setMainMenuOpen(false), mainMenuOpen, 'app-menu')
  // Root-owned dialogs and pickers.
  useDismissLevel(() => setPaletteOpen(false), paletteOpen, 'palette')
  useDismissLevel(() => setLauncherOpen(false), launcherOpen, 'quick-launcher')
  useDismissLevel(() => setRenameTarget(null), !!renameTarget, 'rename')
  useDismissLevel(() => setProjectCreateOpen(false), projectCreateOpen, 'project-create')
  // Opened from inside project create, so it opens later and correctly closes first.
  useDismissLevel(() => setFolderPickerOpen(false), folderPickerOpen, 'folder-picker')
  useDismissLevel(() => setGroupEdit(null), !!groupEdit, 'group-edit')
  useDismissLevel(() => setRedeployConfirmOpen(false), redeployConfirmOpen, 'redeploy-confirm')
  useDismissLevel(() => setReviewState(null), !!reviewState, 'second-opinion')
  useDismissLevel(() => setHandoffState(null), !!handoffState, 'handoff-export')

  // The platform back gesture closes one overlay level. Installed for every device, not
  // just the mobile workspace: a desktop browser's Back button and mouse-4 reach the same
  // handler, and a standalone PWA has no other route back at all.
  useEffect(() => installSystemBack(), [])
  // A dismissable level that refuses to be dismissed: back must not walk out of the app
  // while the daemon is mid-restart, and there is nothing behind these overlays to reach.
  useEffect(() => {
    if (!daemonReloading && !redeploying) return
    const id = dismissStack.register({ label: 'daemon-reload', blocking: true, dismiss: () => undefined })
    return () => dismissStack.unregister(id)
  }, [daemonReloading, redeploying])
  // Field diagnostics for "back did the wrong thing" reports: the live level names and the
  // recent transition ring, readable from a phone's remote console with no build change.
  useEffect(() => {
    ;(window as unknown as { __muxDismiss?: unknown }).__muxDismiss = {
      depth: () => dismissStack.depth(),
      top: () => dismissStack.topLabel(),
      trace: () => dismissStack.trace(),
    }
  }, [])

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
    const onStart = (event: TouchEvent) => {
      const target = event.target
      // Use the composed path rather than parentElement so a scroller inside an
      // open shadow root, including Continuity's command rail, keeps its drag.
      const path = event.composedPath().filter((node): node is Element => node instanceof Element)
      // Act over the workspace, the sidebar, or its scrim (so a swipe over the dimmed
      // area toggles the open sidebar shut). The utility drawer and its scrim are included
      // so the leftward two-finger swipe that pulls the drawer in can also push it back
      // out from over it.
      // The overlay wrappers are listed only so the back swipe can reach an open overlay.
      // `.modal-layer` is the most common one but not the only one: Settings, the
      // dashboards (usage, automation, fleet queue, observations, bandwidth), Processes,
      // the folder picker, and the palette each render their own, and listing only
      // `.modal-layer` left the swipe silently dead on most of the app's big surfaces.
      // Every class here belongs to a surface that registers a dismiss level. Adding one
      // that does not would let a swipe run its workspace binding behind the overlay,
      // which is the hijacking this filter exists to prevent — so the floating voice
      // overlay is deliberately absent, being chrome rather than a dismissable level.
      // Overlays stay immune to that hijacking by a stronger rule than exclusion:
      // `resolveGestureCommand` resolves every non-back slot to nothing whenever the
      // dismiss stack is non-empty.
      if (!(target instanceof Element) || !target.closest('.mobile-unified-workspace, .sidebar, .sidebar-scrim, .utility-drawer, .utility-drawer-scrim, .modal-layer, .settings-layer, .usage-layer, .process-layer, .folder-picker-layer, .palette-layer') || pathOwnsHorizontalScroll(path, node => getComputedStyle(node).overflowX)) { state = null; detachMove(); return }
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
    // first drag on a horizontal scroller like the Action rail. So it is attached only once
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
      const panels = overlayPanels.current
      const command = resolveGestureCommand(slot, mobileGestures, panels, swipeAwayClose, { depth: gestureOverlayDepth(dismissStack.depth(), panels), enabled: overlayBack })
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
  }, [mobileWorkspace, mobileGestures, swipeAwayClose, overlayBack])

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
    // Context targeting is not workspace activation. Pane-bar menus still focus
    // their own pane; sidebar, desktop-tab, and mobile-tab menus preserve the
    // active Project, active terminal, and focused view.
    if(source==='pane'){setActiveId(session.id);setFocusedViewId(session.id)}
    setTabMenu(null);setNoteMenu(null)
    setContextMenu({session,x,y,source})
  }

  const openTabMenu=(leaf:PaneLeaf,label:string,x:number,y:number,source:'tab'|'mobile'='tab')=>{
    setContextMenu(null);setNoteMenu(null);setProjectMenu(null);setSidebarMenu(null);setMainMenuOpen(false)
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
        const tabStrip=paneElement.querySelector<HTMLElement>(':scope > .stack-tabs-rail > .stack-tabs')
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
        const tabStrip=paneElement.querySelector<HTMLElement>(':scope > .stack-tabs-rail > .stack-tabs')
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
      return <section data-pane-stack-id={node.id} data-tutorial="workspace-pane" class={`pane-stack ${focusedPane?'focused-pane':''} ${paneDropClass}`} onPointerDown={event=>{if(event.button!==2)setFocusedViewId(activeChild.id)}}><OverflowRail className="stack-tabs" itemLabel="workspace tabs" wrapperClassName="stack-tabs-rail" activeKey={activeChild.id} focusKey={focusedPane?activeChild.id:undefined} stripProps={{'data-tutorial':'tab-strip',role:'tablist','aria-label':'Workspace tabs'}}>
        {node.children.map(child=>{
          const activate=()=>{if(suppressDragClickRef.current===`tab:${child.id}`){suppressDragClickRef.current=null;return}setFocusedViewId(child.id);if(child.kind==='terminal')setActiveId(child.id);if(child.id!==activeChild.id)void updateLayout(projectId,activateStackChild(activeLayout,node.id,child.id))}
          const dragClass=dragStackTab?.overId===child.id&&dragStackTab.side?`drag-over drop-${dragStackTab.side}`:''
          const dragStyle={order:previewIds.indexOf(child.id)}
          if(child.kind==='preview'){
            const preview=previews[child.id]
            const label=preview?.url||child.id
            return <div key={child.id} data-reorder-id={child.id} data-tutorial="tab-drag-source" style={dragStyle} class={`stack-tab-shell draggable-tab ${dragStackTab?.childId===child.id?'dragging':''} ${dragClass}`} onPointerDown={event=>beginWorkspaceTabDrag(event,{stackId:node.id,childId:child.id,kind:child.kind,targetStackId:node.id,zone:'tabs',previewIds:node.children.map(item=>item.id),overId:null,side:null},label)}><button role="tab" aria-label={`${label} preview tab`} title={label} aria-selected={child.id===activeChild.id} class={`tab-main preview-tab ${child.id===activeChild.id?'active':''}`} onClick={activate} onContextMenu={event=>{event.preventDefault();event.stopPropagation();openTabMenu(child,label,event.clientX,event.clientY)}}><span class="preview-tab-glyph" aria-hidden="true">◱</span>{preview?`:${preview.port}`:child.id}</button>{closeTab(child,label)}</div>
          }
          if(child.kind==='note'){
            const label=noteTabLabel(child.id)
            return <div key={child.id} data-reorder-id={child.id} data-tutorial="tab-drag-source" style={dragStyle} class={`stack-tab-shell draggable-tab resource-tab ${dragStackTab?.childId===child.id?'dragging':''} ${dragClass}`} onPointerDown={event=>beginWorkspaceTabDrag(event,{stackId:node.id,childId:child.id,kind:child.kind,targetStackId:node.id,zone:'tabs',previewIds:node.children.map(item=>item.id),overId:null,side:null},label)}><button role="tab" aria-label={`${label} resource tab`} title={label} aria-selected={child.id===activeChild.id} class={`tab-main ${child.id===activeChild.id?'active':''}`} onClick={activate} onContextMenu={event=>{event.preventDefault();event.stopPropagation();openTabMenu(child,label,event.clientX,event.clientY)}}><span class="preview-tab-glyph" aria-hidden="true">◇</span>{label}</button>{closeTab(child,label)}</div>
          }
          if(child.kind==='history'){
            const label='History'
            return <div key={child.id} data-reorder-id={child.id} data-tutorial="tab-drag-source" style={dragStyle} class={`stack-tab-shell draggable-tab resource-tab ${dragStackTab?.childId===child.id?'dragging':''} ${dragClass}`} onPointerDown={event=>beginWorkspaceTabDrag(event,{stackId:node.id,childId:child.id,kind:child.kind,targetStackId:node.id,zone:'tabs',previewIds:node.children.map(item=>item.id),overId:null,side:null},label)}><button role="tab" aria-label="History tab" title="Search session history" aria-selected={child.id===activeChild.id} class={`tab-main ${child.id===activeChild.id?'active':''}`} onClick={activate} onContextMenu={event=>{event.preventDefault();event.stopPropagation();openTabMenu(child,label,event.clientX,event.clientY)}}><span class="preview-tab-glyph" aria-hidden="true">◷</span>{label}</button>{closeTab(child,label)}</div>
          }
          if(child.kind==='queue'){
            const label=queueTabLabel(child.id)
            return <div key={child.id} data-reorder-id={child.id} data-tutorial="tab-drag-source" style={dragStyle} class={`stack-tab-shell draggable-tab resource-tab ${dragStackTab?.childId===child.id?'dragging':''} ${dragClass}`} onPointerDown={event=>beginWorkspaceTabDrag(event,{stackId:node.id,childId:child.id,kind:child.kind,targetStackId:node.id,zone:'tabs',previewIds:node.children.map(item=>item.id),overId:null,side:null},label)}><button role="tab" aria-label={`${label} queue tab`} title={label} aria-selected={child.id===activeChild.id} class={`tab-main ${child.id===activeChild.id?'active':''}`} onClick={activate} onContextMenu={event=>{event.preventDefault();event.stopPropagation();openTabMenu(child,label,event.clientX,event.clientY)}}><span class="preview-tab-glyph" aria-hidden="true">⇥</span>{label}</button>{closeTab(child,label)}</div>
          }
          const session=sessions.find(item=>item.id===child.id)
          // sessionName, not session.name: the generated title is the whole point of
          // titling, and a tab strip showing `claude-15036b` while the sidebar shows
          // the real name is the surface where you actually need to tell panes apart.
          const label=session?sessionName(session):child.id
          return <div key={child.id} data-reorder-id={child.id} data-tutorial="tab-drag-source" style={dragStyle} class={`stack-tab-shell draggable-tab ${session?.pending?'pending-terminal-tab':''} ${dragStackTab?.childId===child.id?'dragging':''} ${dragClass}`} onPointerDown={event=>{if(!session?.pending)beginWorkspaceTabDrag(event,{stackId:node.id,childId:child.id,kind:child.kind,targetStackId:node.id,zone:'tabs',previewIds:node.children.map(item=>item.id),overId:null,side:null},label)}}><button role="tab" aria-label={`${label} session tab`} aria-selected={child.id===activeChild.id} class={`tab-main ${child.id===activeChild.id?'active':''} ${session?.state||''}`} onClick={activate} onContextMenu={event=>{event.preventDefault();event.stopPropagation();if(session&&!session.pending)openSessionMenu(session,event.clientX,event.clientY,'tab')}}>{sessionStateDot(session,rowConfig.dotShape,null,sessionStandingMark(session,rowConfig))}{sessionGlyph(session)}{activityGlyphs(session,rowConfig.standing)}{mobileDraftIndicator(child.id)}{label}</button>{closeTab(child,label,session)}</div>
        })}
      </OverflowRail><div class="stack-active">{node.children
        .filter(child=>child.id===activeChild.id||(child.kind==='terminal'&&warmTerminalIds.includes(child.id)))
        .map(child=>renderPaneNode(child,`${path}t`,true,child.id===activeChild.id))}</div></section>
    }
    if(node.kind==='note'){
      const identity=parseNoteResourceId(node.id)
      if(!identity||!activeProject)return <section class="workspace-leaf-placeholder note-unavailable"><strong>resource unavailable</strong><span>{node.id}</span><button onClick={()=>void removeWorkspaceNote(projectId,node.id)}>close tab</button></section>
      // The tab keeps its place in the layout while the drawer holds this note — the layout is
      // shared across devices, so claiming a note here must not rearrange anyone else's panes.
      // What it does not keep is a second editor: two on one note share one save queue and the
      // later one silently overwrites the earlier. See `drawerNotes.ts`.
      if(isDrawerOwned(drawerNotes,activeProject.id,node.id,clipboardOpen))return <section class="workspace-leaf-placeholder note-in-drawer">
        <strong>Open in the panel</strong>
        <span>This note is being edited in the side panel. It stays in one place at a time so an edit cannot be lost to the other copy.</span>
        <button onClick={()=>popDrawerNoteToTab(node.id,activeProject.id)}>Move it back here</button>
      </section>
      return <ProjectResource key={`${activeProject.id}:${node.id}`} project={activeProject} resource={identity} onOpenFile={path=>{if(identity.kind==='worktree-file'){openWorktreeFile(activeProject,identity.worktree,path);return}if(suppressDragClickRef.current===`file:${noteResourceId('file',path)}`){suppressDragClickRef.current=null;return}openProjectFile(activeProject,path)}} onFileDragStart={identity.kind==='worktree-file'?undefined:(path,event)=>beginFileTabDrag(event,path)} onSendToAgent={setSendToAgent}/>
    }
    if(node.kind==='history')return <section class="workspace-leaf-placeholder"><strong>History moved</strong><span>Session history is now a full-screen overlay.</span><button onClick={()=>{setHistoryOpen(true);void updateLayout(projectId,removeLeaf(layoutValues.current[projectId]||emptyLayout(),'history',node.id))}}>Open History</button></section>
    if(node.kind==='queue'){
      const targetSessionId=queueLeafSessionId(node.id)
      if(!targetSessionId)return <section class="workspace-leaf-placeholder"><strong>queue unavailable</strong><span>{node.id}</span></section>
      // The pop-out rendering: target pinned to the leaf rather than following focus, and
      // no pop-out button of its own. Everything else is the same panel the drawer shows.
      return <QueuePane key={node.id} sessionId={targetSessionId} sessions={sessions} onSelectSession={sid=>{const owner=sessions.find(item=>item.id===sid);if(owner)void selectSession(owner)}}/>
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
    const agentSession=isAgent(session)
    if(session.pending)return <section class={`terminal-pane pending-terminal-pane ${activeId===id?'focused':''}`} onPointerDown={()=>{setActiveId(id);setFocusedViewId(id)}}>
      <div class={`pane-bar ${agentSession?'agent-pane-bar':''}`}><div><span class="pane-state starting">{session.pending_label||'starting terminal…'}</span></div>{!agentSession&&<div class="pane-path">{session.cwd}</div>}</div>
      <div class="pending-terminal-body" role="status" aria-live="polite"><span class="pending-terminal-spinner" aria-hidden="true"/><strong>{session.pending_label||'Starting terminal'}</strong><small>{session.pending_detail||'Resolving the project and opening the shell…'}</small></div>
    </section>
    const remoteBoundary=session.runtime_boundary==='remote'
    const boundaryUnknown=session.runtime_boundary==='unknown'
    const nonLocalBoundary=remoteBoundary||boundaryUnknown
    const displayedCwd=remoteBoundary
      ?`ssh://${session.remote_authority||'remote'}`
      :boundaryUnknown?'unavailable':session.runtime_cwd||session.spawn_cwd||session.cwd
    const cwdIsLive=session.runtime_cwd_live&&!nonLocalBoundary
    const openPaneMenu=(event:{clientX:number;clientY:number;preventDefault?:()=>void;stopPropagation?:()=>void})=>{event.preventDefault?.();event.stopPropagation?.();openSessionMenu(session,event.clientX,event.clientY,'pane')}
    const agentVoice=agentSession
    const voiceMode=voiceStatus?.enabled&&agentVoice?effectiveVoiceMode(session):'off'
    const voiceAvailable=!!voiceStatus?.enabled&&agentVoice
    const voiceStripVisible=voiceAvailable&&voiceMode!=='off'
    // Read-aloud stays session-scoped in the pane header. The workspace microphone and
    // dictation draft are rendered once at App level, so changing panes only retargets them.
    // The playback strip (seek, clip nav, generate) floats directly beneath the header. It
    // used to lead the bottom Action rail, but that rail is a horizontal scroller the user
    // pages through to reach terminal keys, so the voice chips were both in the way there and
    // easy to lose off-screen. Grouped in the header they have a fixed home; the group is its
    // own scroller so a long chip set can never push the pane tools out of the bar.
    const paneVoice=agentVoice&&voiceStatus?<>
      {voiceAvailable&&<button class={`voice-chip ${voiceMode}`} aria-label={`Read aloud mode for ${sessionName(session)}: ${voiceModeLabel(voiceMode)}. Click to change.`} title={`Read aloud: ${voiceModeLabel(voiceMode)} · click to cycle off → on demand → auto`} onClick={()=>cycleVoiceMode(session)}>tts:{voiceMode==='on_demand'?'tap':voiceMode}</button>}
      {!voiceAvailable&&<button class="voice-chip mobile-voice-action" aria-label="Set up read aloud" title="Read aloud is disabled · open Voice settings" onClick={()=>openSettings('Voice')}>tts:setup</button>}
      {/* speak / verbatim-summary / autoplay were repeated here for touch while the playback
          strip was buried at the bottom of the pane; the strip owns them now. The `audio…`
          settings chip went the same way once both floating surfaces grew their own gear —
          three routes to one Settings section is two too many. */}
    </>:null
    const openVoiceSettings=()=>openSettings('Voice')
    const voiceStripNode=voiceStripVisible&&voiceStatus?<VoicePlayer session={session} status={voiceStatus} mode={voiceMode as 'on_demand'|'auto'} commands={commands} onSession={updateSession} onOpenSettings={openVoiceSettings} />:null
    // The read-aloud strip hangs off a zero-height pane anchor. Dictation no longer
    // participates in pane layout at all.
    const conversationSurface=id===conversationPaneId
      ?<ConversationSurface conversation={conversation} commands={commands} configuredCommands={voiceStatus?.commands} onOpenSettings={openVoiceSettings} placement="pane"/>
      :null
    const voiceOverlayNode=voiceStripNode||conversationSurface
      ?<div class="voice-overlay-anchor"><div class="voice-overlay">{voiceStripNode}{conversationSurface}</div></div>
      :null
    // `key` matters here in a way it does not for a single-child stack: a stack now
    // renders its active pane *and* its warm siblings, so without a stable identity a
    // reorder would rebuild terminals rather than move them.
    const terminalPane=<section key={id} class={`terminal-pane ${activeId === id ? 'focused' : ''} ${paneVisible ? '' : 'pane-warm'}`} aria-hidden={paneVisible?undefined:'true'} onPointerDown={() => {setActiveId(id);setFocusedViewId(id)}}>
      <div class={`pane-bar ${agentSession?'agent-pane-bar':''}`} onContextMenu={openPaneMenu} onDblClick={() => setZoomedId(current => current === id ? null : id)}>
        {/* A stale transcript is the one fault that looks like a healthy session: the state
            below is being read off a conversation this PTY may no longer be running (an
            unfollowable /clear or /new). Marked visibly, not just in the tooltip, because the
            whole failure mode is that nothing looks wrong. */}
        <div><span class={`pane-state ${isObservedHarness(session.backend)?session.state:'unobserved'}${session.observation_stale_since?' observation-stale':''}`} title={[nonLocalBoundary&&'non-local terminal boundary; local cwd, Git, transcript, hooks, shim PATH repair, and agent promotion are unavailable',session.observation_stale_since&&'observation stale: the followed transcript may no longer be this session’s conversation',session.observation_diagnostic,session.parser_diagnostic,session.delivery_readiness&&`delivery::${session.delivery_readiness.state} (${session.delivery_readiness.reason}) · authorized::no`].filter(Boolean).join('\n')}>{sessionStatus(session)}{session.observation_stale_since?' · stale':''}</span></div>
        {!agentSession&&<div class={`pane-path ${remoteBoundary?'remote':boundaryUnknown?'boundary-unknown':cwdIsLive?'live':'last-known'}`} title={nonLocalBoundary?'non-local terminal boundary; local cwd, Git, transcript, hooks, shim PATH repair, and agent promotion are unavailable':cwdIsLive?`live cwd · ${displayedCwd}`:`last known (spawn) cwd · ${displayedCwd}`}>{remoteBoundary?<span>remote::</span>:boundaryUnknown?<span>boundary::unknown::</span>:cwdIsLive?'':<span>last-known::</span>}{displayedCwd}</div>}
        <div class="pane-voice">{paneVoice}</div>
        <div class="pane-tools">{deliversHarnessPrompts(session.backend)&&<button class={`pane-tool-label queue-chip${(queueSummary[session.id]?.pending||0)>0?' has-pending':''}`} aria-label={`Open the prompt queue for ${sessionName(session)}`} title={`Prompt queue · ${queueSummary[session.id]?.pending||0} pending`} onClick={()=>void openQueueForSession(session.id)}>queue{(queueSummary[session.id]?.pending||0)>0?`:${queueSummary[session.id].pending}`:''}</button>}{hasHarnessTranscript(session.backend)&&<button class="pane-tool-label transcript-chip" aria-label={`Open the transcript for ${sessionName(session)}`} title="Read transcript" onClick={()=>void openTranscriptForSession(session.id)}>transcript</button>}{/* No `proc` chip. It carries no state of its own while `queue` reports its pending count, and
            on a phone it cost 40px of a bar that also has to fit the session name and path. What it
            opened is now the drawer's Processes tab, which pins this session's row first, and the
            session context menu and palette still open the inspector directly. */}<button aria-label={`More actions for ${sessionName(session)}`} title="Session actions" onClick={event=>{const rect=event.currentTarget.getBoundingClientRect();openPaneMenu({clientX:rect.right,clientY:rect.bottom,stopPropagation:()=>event.stopPropagation()})}}>⋯</button></div>
      </div>
      {voiceOverlayNode}
      <TerminalPane session={session} onState={updateSession} startupOrigin={startupOrigins.current[session.id]} onStartupTiming={(milestone,elapsedMs)=>recordClientStartupTiming(session.id,milestone,elapsedMs)} broadcast={broadcast} keybindings={keybindings} scrollback={xtermScrollback} rendererPreference={terminalRenderer} windowsPty={windowsPty} mobileInput={mobileInput} uiScale={uiScale} visible={paneVisible} claudeMaxColumns={claudeMaxColumns} onConfigureRail={openActionEditor} onConfigureWidth={()=>openSettings('Terminals')} onBranch={()=>void branchSession(session)} />
    </section>
    if(insideStack)return terminalPane
    return <section data-tutorial="workspace-pane" class="pane-stack singleton-stack"><OverflowRail className="stack-tabs" itemLabel="terminal tabs" wrapperClassName="stack-tabs-rail" activeKey={id} stripProps={{'data-tutorial':'tab-strip',role:'tablist','aria-label':'Terminal tabs'}}>
      <div data-tutorial="tab-drag-source" class="stack-tab-shell"><button role="tab" aria-label={`${sessionName(session)} session tab`} aria-selected="true" class={`tab-main active ${session.state}`} onClick={()=>setActiveId(id)} onContextMenu={event=>{event.preventDefault();event.stopPropagation();openSessionMenu(session,event.clientX,event.clientY,'tab')}}>{sessionStateDot(session,rowConfig.dotShape,null,sessionStandingMark(session,rowConfig))}{sessionGlyph(session)}{activityGlyphs(session,rowConfig.standing)}{mobileDraftIndicator(id)}{sessionName(session)}</button><button class={`tab-close ${confirmKillId===id?'confirming':''}`} aria-label={`${confirmKillId===id?'Confirm close':'Close'} terminal: ${sessionName(session)}`} title={confirmKillId===id?'Confirm kill terminal':'Close and kill terminal'} onClick={event=>{event.stopPropagation();requestKill(session)}}>{confirmKillId===id?'✓':'×'}</button></div>
    </OverflowRail><div class="stack-active">{terminalPane}</div></section>
  }

  type FileMenuSource={resourceId:string;projectId:string}|{leaf:PaneLeaf;projectId:string}
  const workspaceNoteIds=(targetProject:string)=>leaves(
    resolveLayout(layoutMap[targetProject],projects.find(item=>item.id===targetProject)?.layout),
    'note',
  ).map(leaf=>leaf.id)
  // Resource menus cover notes and opened files; only the latter have filesystem actions.
  // Accept both resource-list and workspace-tab targets so the two menus share one resolver.
  const fileMenuTarget=(menu:FileMenuSource)=>{
    const resourceId='resourceId' in menu?menu.resourceId:menu.leaf.kind==='note'?menu.leaf.id:''
    const identity=parseNoteResourceId(resourceId)
    if(identity?.kind!=='file'&&identity?.kind!=='worktree-file')return null
    const root=identity.kind==='worktree-file'?identity.worktree:projects.find(item=>item.id===menu.projectId)?.root||''
    return { relative:identity.id, absolute:absoluteProjectPath(root,identity.id), worktree:identity.kind==='worktree-file'?identity.worktree:undefined }
  }
  // The tab menu has no recovery panel of its own, so a refused write says where the payload
  // still is (the Files tree offers the manual copy) rather than failing silently.
  const revealFileResource=async(menu:FileMenuSource)=>{
    const target=fileMenuTarget(menu)
    if(!target)return
    setNoteMenu(null);setTabMenu(null)
    try{
      await api('POST',`/api/projects/${menu.projectId}/reveal`,{
        path:target.relative,
        ...(target.worktree?{worktree:target.worktree}:{}),
      })
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }
  const copyFileClipboard=async(menu:FileMenuSource,form:'absolute'|'relative'|'contents')=>{
    const target=fileMenuTarget(menu)
    if(!target)return
    setNoteMenu(null);setTabMenu(null)
    try{
      let payload=form==='absolute'?target.absolute:target.relative
      if(form==='contents'){
        const query=new URLSearchParams({path:target.relative})
        if(target.worktree)query.set('worktree',target.worktree)
        const file=await api<{status:string;text?:string}>('GET',`/api/projects/${menu.projectId}/file?${query}`)
        if(file.status==='too-large'){setError(`${target.relative} is above the 2 MiB read limit and cannot be copied.`);return}
        if(file.status==='binary'||file.text===undefined){setError(`${target.relative} is not text, so there is nothing to copy.`);return}
        payload=truncateForClipboard(file.text,target.relative).text
      }
      if(!await copyPreparedText(payload)){
        setError('Clipboard write was blocked by the browser. Right-click the file in the Files tab to copy it manually.')
      }
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }
  // A listed Preview lives beside its owning session. Raw loopback listeners remain
  // in Processes unless browser classification or an explicit action promotes one.
  const sidebarPreviewRow=(preview:Preview,session:Session)=>{
    const layout=layoutMap[session.project_id]||parseLayout(projects.find(item=>item.id===session.project_id)?.layout)
    const previewStack=stackForView(layout,preview.id)
    const selected=previewStack?.active_child_id===preview.id
    return <button key={preview.id} class={`sidebar-note-row preview-row ${selected?'active':''}`} title={`${preview.url} · ${preview.source} preview spawned by this session`} onClick={event=>{event.stopPropagation();if(previewStack){setProjectId(session.project_id);setFocusedViewId(preview.id);void updateLayout(session.project_id,activateStackChild(layout,previewStack.id,preview.id))}else void openDetectedServer(preview,session);setSidebarOpen(false)}}>
      <span class="note-branch" aria-hidden="true">└</span><span class="note-copy"><strong>server :{preview.port}</strong></span>
    </button>
  }
  const openDetectedServer=async(server:{url:string},session:Session)=>{
    try{
      const result=await api<{preview:Preview;project:Project}>('POST','/api/previews',{session_id:session.id,url:server.url,attach:true})
      setPreviews(current=>({...current,[result.preview.id]:result.preview}))
      setProjects(items=>items.map(item=>item.id===result.project.id?result.project:item))
      setLayoutMap(current=>({...current,[result.project.id]:parseLayout(result.project.layout)}))
      setProjectId(session.project_id)
      setFocusedViewId(result.preview.id)
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }
  /** `placement` is whether this row's session sits in the Project's pane tree. An unpaned one
   *  is a live session the layout has no leaf for, listed after the tree in creation order — it
   *  holds no position, so it is not a slot another row can be dropped beside. */
  const sessionRow=(session:Session,placement:'paned'|'unpaned'='paned')=>{
    const spawnedPreviews=Object.values(previews).filter(item=>item.session_id===session.id&&item.listed!==false)
    // The phone renders identity only unless parity is enabled: its rows are
    // narrower than any of these tokens are useful in, and a row that truncates
    // its own title to make room for a branch name has traded down.
    // The flag strip survives the identity projection and is given the live row
    // context, so a phone still marks the session it is holding a draft for.
    const rowTokens=(item:Session)=>mobileWorkspace&&!rowConfig.mobileFields
      ? identityRowTokens(item,rowConfig,rowContext)
      : buildSessionRowTokens(item,rowConfig,rowContext)
    // Sidebar attention tier for agent rows. The focused row keeps its own
    // `.active` treatment; a row visible in another split pane reads as
    // "viewing" (on screen, not focused); an off-screen row with unseen output
    // is "unread"; an off-screen, already-seen row is "read" and recedes.
    const agent=isAgent(session)
    const attention=!agent||activeId===session.id?''
      :visibleSessionIds.includes(session.id)?'viewing'
      :isUnread(session,ackedTurns)?'unread':'read'
    return <div class="session-entry"><button data-sidebar-session-id={session.id} data-sidebar-project-id={session.project_id} data-sidebar-reorder={placement==='paned'&&!session.pending?undefined:'off'} class={`session-row ${activeId === session.id ? 'active' : ''} ${agent?'agent':''} ${attention} ${session.state} ${session.pending?'pending-terminal-row':''}`} onPointerDown={event=>{if(!session.pending)beginSessionPointerDrag(event,session)}} onContextMenu={event => { event.preventDefault();if(!session.pending&&!mobileWorkspace)openSessionMenu(session,event.clientX,event.clientY,'sidebar') }} onClick={() => {if(suppressDragClickRef.current===`session:${session.id}`){suppressDragClickRef.current=null;return}void selectSession(session)}}>
      {sessionStateDot(session,rowConfig.dotShape,sessionContextArc(session,rowConfig),sessionStandingMark(session,rowConfig))}
      <SessionRowBody session={session} tokens={rowTokens(session)} config={rowConfig}/>
      {!session.pending&&<span class="row-actions" onPointerDown={event=>event.stopPropagation()} onClick={event => event.stopPropagation()}><button class={confirmKillId === session.id ? 'confirming' : ''} title={confirmKillId === session.id ? (isEndedSession(session) ? 'Confirm remove' : 'Confirm kill') : (isEndedSession(session) ? 'Remove from sidebar' : 'Kill')} onClick={() => runNamedCommand(`session.requestKill(${session.id})`)}>{confirmKillId === session.id ? '✓' : '×'}</button></span>}
    </button>{spawnedPreviews.map(preview=>sidebarPreviewRow(preview,session))}</div>
  }
  const sidebarNode=(node:PaneNode|PaneLeaf|null|undefined):ComponentChildren=>{
    if(!node)return null
    if(node.type==='leaf'){
      if(node.kind!=='terminal')return null
      const session=sessions.find(item=>item.id===node.id)
      return session?sessionRow(session):null
    }
    const branches=(node.type==='stack'?node.children:[node.first,node.second]).filter(child=>child.type==='leaf'?child.kind==='terminal':terminalIds({...emptyLayout(),root:child}).length>0)
    if(branches.length===0)return null
    if(branches.length===1)return sidebarNode(branches[0])
    const label=node.type==='stack'?'Sessions sharing one tabbed pane':`${node.direction} split branches`
    // A stack used to be a drop target in its own right, hit-tested by id off this section. It
    // is not one any more: dropping on any *row* of a stack joins that stack, which is the same
    // outcome aimed at something the eye can actually see, and it leaves no dead strip of
    // section padding behaving differently from the rows inside it.
    return <section class={`layout-cluster ${node.type} ${node.type==='split'?node.direction:''}`} role="group" aria-label={label}>
      {branches.map((child,index)=><div class={`layout-branch ${index===0?'first':''} ${index===branches.length-1?'last':''}`} key={child.id}>{sidebarNode(child)}</div>)}
    </section>
  }

  const noteTabLabel=(resourceId:string)=>{
    const identity=parseNoteResourceId(resourceId)
    if(identity?.kind==='global-note')return 'Scratchpad'
    if(identity?.kind==='note')return noteTitles[`${projectId}:${identity.id}`]||'Note'
    return identity?.id.split('/').pop()||'File'
  }
  const projectPreviewIds=dragProject?.previewIds||displayProjectIds

  const mobileProjection=mobileWorkspaceProjection(activeLayout,focusedViewId,activeId)
  const activateMobileTab=(leaf:PaneLeaf)=>{
    setFocusedViewId(leaf.id)
    if(leaf.kind==='terminal')setActiveId(leaf.id)
    const current=layoutValues.current[projectId]||activeLayout
    const pane=stackForView(current,leaf.id)
    if(pane&&pane.active_child_id!==leaf.id)void updateLayout(projectId,activateStackChild(current,pane.id,leaf.id))
  }
  const focusAfterMobileClose=(leaf:PaneLeaf)=>{
    if(mobileProjection.selected?.id!==leaf.id)return
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
  // Land a mobile tab-strip reorder back on the real pane tree. The strip is a depth-first
  // flattening of every stack (see mobileWorkspace.ts), so a drop within one stack reorders
  // it, and a drop next to a tab from another stack moves the leaf into that stack at the
  // aimed position — the only two things a flat rail over a split tree can mean.
  const commitMobileTabOrder=(leaf:PaneLeaf,target:ReorderTarget)=>{
    const latest=layoutValues.current[projectId]||activeLayout
    const targetStack=stackForView(latest,target.id)
    if(!targetStack)return
    const source=stackForView(latest,leaf.id)
    const moved=source&&source.id===targetStack.id?latest:moveLeafToStack(latest,leaf.kind,leaf.id,targetStack.id)
    const stack=stackForView(moved,target.id)
    if(!stack)return
    const ids=stack.children.map(child=>child.id).filter(id=>id!==leaf.id)
    const at=ids.indexOf(target.id)
    if(at<0)return
    ids.splice(at+(target.side==='after'?1:0),0,leaf.id)
    const next=reorderStack(moved,targetStack.id,ids)
    if(next!==latest)void updateLayout(projectId,next)
  }
  const beginMobileTabDrag=(event:JSX.TargetedPointerEvent<HTMLElement>,leaf:PaneLeaf,label:string,openMenu:(x:number,y:number)=>void)=>{
    const strip=event.currentTarget.closest<HTMLElement>('.stack-tabs')
    let target:ReorderTarget|null=null,latestPointer:{clientX:number;clientY:number}|null=null,scrollFrame:number|null=null
    const preview=(pointer:{clientX:number;clientY:number})=>{
      if(!strip){target=null;showPointerDropIndicator(null);return}
      const next=reorderTargetFromContainer(strip,leaf.id,'horizontal',pointer.clientX)
      target=next
      const element=next?Array.from(strip.querySelectorAll<HTMLElement>(':scope > [data-reorder-id]')).find(item=>item.dataset.reorderId===next.id)||null:null
      showPointerDropIndicator(element,next?`insert-${next.side}`:undefined)
    }
    const stopAutoScroll=()=>{latestPointer=null;if(scrollFrame!==null)window.cancelAnimationFrame(scrollFrame);scrollFrame=null}
    const autoScroll=()=>{
      scrollFrame=null
      if(!strip||!latestPointer)return
      const box=strip.getBoundingClientRect()
      const delta=edgeAutoScrollDelta(latestPointer.clientX,box.left,box.right)
      if(delta!==0){const before=strip.scrollLeft;strip.scrollLeft+=delta;if(strip.scrollLeft!==before)preview(latestPointer)}
      scrollFrame=window.requestAnimationFrame(autoScroll)
    }
    beginPointerDrag(event,label,`mobiletab:${leaf.id}`,
      ()=>{mobileTabHeldRef.current=false;if(mobileWorkspace)navigator.vibrate?.(15)},
      pointer=>{latestPointer={clientX:pointer.clientX,clientY:pointer.clientY};preview(pointer);if(scrollFrame===null)scrollFrame=window.requestAnimationFrame(autoScroll)},
      ()=>{stopAutoScroll();const chosen=target;target=null;showPointerDropIndicator(null);if(chosen&&chosen.id!==leaf.id)commitMobileTabOrder(leaf,chosen)},
      ()=>{stopAutoScroll();target=null;showPointerDropIndicator(null)},
      mobileWorkspace?MOBILE_HOLD_DRAG:POINTER_MOVE_DRAG,
      ()=>openMenu(event.clientX,event.clientY),
    )
  }
  const mobileTab=(leaf:PaneLeaf):ComponentChildren=>{
    const selected=leaf.id===mobileProjection.selected?.id
    const session=leaf.kind==='terminal'?sessions.find(item=>item.id===leaf.id):undefined
    const preview=leaf.kind==='preview'?previews[leaf.id]:undefined
    const label=leaf.kind==='terminal'?(session?sessionName(session):leaf.id):leaf.kind==='preview'?preview?.url||leaf.id:leaf.kind==='history'?'History':leaf.kind==='queue'?queueTabLabel(leaf.id):noteTabLabel(leaf.id)
    const visibleLabel=mobileTabLabel(leaf)
    const glyph=leaf.kind==='terminal'?<>{sessionStateDot(session,rowConfig.dotShape,null,sessionStandingMark(session,rowConfig))}{sessionGlyph(session)}{activityGlyphs(session,rowConfig.standing)}{mobileDraftIndicator(leaf.id)}</>:<span class="preview-tab-glyph" aria-hidden="true">{leaf.kind==='preview'?'◱':leaf.kind==='history'?'◷':leaf.kind==='queue'?'⇥':'◇'}</span>
    // Mobile tabs carry no close button: it ate label width and was a mis-tap
    // hazard next to tab activation. Closing/killing lives in the long-press
    // menu (session menu for terminals, tab menu for resources), which is also
    // where the confirm step already is.
    const openMobileTabMenu=(x:number,y:number)=>{
      mobileTabHeldRef.current=true
      if(session&&!session.pending)openSessionMenu(session,x,y,'mobile')
      else if(leaf.kind!=='terminal')openTabMenu(leaf,label,x,y,'mobile')
    }
    return <div key={`${leaf.kind}:${leaf.id}`} data-reorder-id={leaf.id} class="stack-tab-shell mobile-unified-tab">
      <button role="tab" aria-label={`${label} ${leaf.kind} tab`} title={label} aria-selected={selected} class={`tab-main ${selected?'active':''} ${session?.state||''}`} onClick={()=>{if(suppressDragClickRef.current===`mobiletab:${leaf.id}`){suppressDragClickRef.current=null;return}if(mobileTabHeldRef.current){mobileTabHeldRef.current=false;return}activateMobileTab(leaf)}} onPointerDown={event=>{mobileTabHeldRef.current=false;beginMobileTabDrag(event,leaf,label,openMobileTabMenu)}} onContextMenu={event=>{event.preventDefault();event.stopPropagation()}}>{glyph}{visibleLabel}</button>
    </div>
  }
  // With no new-tab button left in the rail, an empty projection would render a
  // bare strip; drop the row entirely and let the empty stage own the section.
  const mobileUnifiedWorkspace=<section data-tutorial="workspace-pane" class={`pane-stack mobile-unified-workspace ${mobileProjection.tabs.length?'':'no-tabs'}`}>
    {mobileProjection.tabs.length>0&&<OverflowRail className="stack-tabs mobile-unified-tabs" itemLabel="Project tabs" wrapperClassName="stack-tabs-rail" activeKey={mobileProjection.selected?.id} stripProps={{'data-tutorial':'tab-strip',role:'tablist','aria-label':'All Project tabs'}}>
      {mobileProjection.tabs.map(mobileTab)}
    </OverflowRail>}
    <div class="stack-active mobile-unified-active">{mobileProjection.selected?renderPaneNode(mobileProjection.selected,'mobile',true):<div class="empty-stage"><div class="hero-terminal" aria-hidden="true">&gt;_</div><h1>Your Project workspace.</h1><p>Run a terminal, or open a note, a file, or a preview to begin. Files and notes live in the side panel.</p></div>}</div>
  </section>

  const sidebarProjectRow=(project:Project,peerIds:string[])=>{
    const children = sessions
      .filter(session => session.project_id === project.id)
      .sort((a,b)=>a.created_at-b.created_at||a.id.localeCompare(b.id))
    const projectLayout=resolveLayout(layoutMap[project.id],project.layout)
    const projectPaneIds=terminalIds(projectLayout)
    const unpanedChildren=children.filter(session=>!projectPaneIds.includes(session.id))
    const dropClass=dragProject?.overId===project.id&&dragProject.side?`project-drop-target drop-${dragProject.side}`:''
    const collapsed=collapsedProjects.has(project.id)
    const liveCount=children.filter(session=>!session.pending&&!['exited','crashed'].includes(session.state)).length
    const hasSessions=children.length>0
    return <section key={project.id} data-reorder-id={project.id} style={{order:projectPreviewIds.indexOf(project.id)}} class={`project-group ${project.id === projectId ? 'active' : ''} ${collapsed?'collapsed':''} ${dropClass}`}>
      <div class={`project-row draggable-project ${dragProject?.id===project.id?'dragging':''}`} title={mobileWorkspace?'Hold for actions, hold and drag to reorder':'Drag to reorder Project'} onPointerDown={event=>beginProjectPointerDrag(event,project,peerIds)} onContextMenu={event => { event.preventDefault();if(!mobileWorkspace)openProjectMenuAt(project,event.clientX,event.clientY) }} onClick={()=>{if(suppressDragClickRef.current===`project:${project.id}`){suppressDragClickRef.current=null;return}selectProject(project.id)}}>
        {hasSessions?<button class="project-chevron project-collapse-toggle" aria-expanded={!collapsed} aria-label={`${collapsed?'Expand':'Collapse'} ${project.name}`} title={collapsed?'Expand project':'Collapse project'} onPointerDown={event=>event.stopPropagation()} onClick={event=>{event.stopPropagation();toggleProjectCollapsed(project.id)}}>{collapsed?'▸':'▾'}</button>:<span class="project-chevron project-collapse-spacer" aria-hidden="true"/>}<strong class="project-name-cell"><span class="project-name-text">{project.name}</span>{collapsed&&liveCount>0&&<span class="project-collapsed-badge" title={`${liveCount} active session${liveCount===1?'':'s'}`}>{liveCount}</span>}</strong><button data-menu-toggle class="project-row-menu" title={`Project actions for ${project.name}`} aria-label={`Project actions for ${project.name}`} aria-haspopup="menu" aria-expanded={projectMenu?.project.id===project.id} onPointerDown={event=>event.stopPropagation()} onClick={event=>{event.stopPropagation();if(projectMenu?.project.id===project.id){setProjectMenu(null);return}const rect=event.currentTarget.getBoundingClientRect();openProjectMenuAt(project,rect.left,rect.bottom+4)}}>⋮</button><button data-tutorial="project-run" class="project-row-run" title={`Run in ${project.name}`} aria-label={`Run in ${project.name}`} onPointerDown={event=>event.stopPropagation()} onClick={event=>{event.stopPropagation();openRunMenu(project,event.currentTarget)}}>▶</button>
      </div>
      {!collapsed&&<div class="session-list">
        {sidebarNode(projectLayout.root)}
        {unpanedChildren.map(session=>sessionRow(session,'unpaned'))}
      </div>}
    </section>
  }

  return <div class="app-shell">
    <div class="sr-only" role="status" aria-live="polite" aria-atomic="true">{attention ? `${attention} agent${attention === 1 ? '' : 's'} awaiting attention` : 'No agents awaiting attention'}</div>
    <div class="mobile-toolbar">
      {/* A mark, not `:nav`: no word survives at this width, and pinning a font size to make
          one fit would ignore the user's UI-scale setting, which this button is subject to via
          an `!important` rule. It is `SidePanelIcon` mirrored, because the two edge toggles open
          mirror-image drawers and the bare `≡` said nothing about which panel it reached. */}
      <button class="nav-toggle mobile-nav-toggle" aria-label="Open navigation sidebar" title="Navigation" onClick={() => setSidebarOpen(value => !value)}><NavPanelIcon/></button>
      {/* Quota sits beside nav, at the start of the bar: it is glanced at constantly, and the
          two edges are where a thumb reaching for a toggle lands, so it takes neither. */}
      <AccountSwitcher variant="compact" onManage={()=>openSettings('Accounts')}/>
      {/* The toolbar title is the Project menu's trigger. Single tap opens it on
          touch: a long-press was the only way in, and holding a text node is what
          raised the selection UI. Long-press/right-click still work for parity.
          `data-menu-toggle` keeps the document dismiss handler off this button,
          or it would close the menu on pointer-down and the click would reopen
          it, so a second tap could never collapse what the first opened. */}
      <button class="mobile-project-name" type="button" data-menu-toggle aria-haspopup="menu" aria-expanded={!!projectMenu} disabled={!activeProject} title={activeProject?`${activeProject.name} — Project actions`:'No Project selected'} onClick={event=>{if(!activeProject)return;if(projectMenu){setProjectMenu(null);return}const rect=event.currentTarget.getBoundingClientRect();openProjectMenuAt(activeProject,rect.left,rect.bottom+4)}} onContextMenu={event=>{if(!activeProject)return;event.preventDefault();if(projectMenu){setProjectMenu(null);return}openProjectMenuAt(activeProject,event.clientX,event.clientY)}}>{activeProject?.name||'No Project'}</button>
      {voiceStatus&&<ConversationToggle conversation={conversation} configured={!!voiceStatus.stt_enabled} onOpenSettings={()=>openSettings('Voice')}/>}
      {/* Tap opens the launcher; hold repeats the last launch straight away,
          which is the common case once a Project settles on one backend. The
          long-press fires while the finger is down, so the click it is followed
          by must be swallowed or the menu would open on top of the new tab. */}
      <button data-tutorial="run" class="mobile-run-trigger" disabled={!activeProject} title={activeProject?`Run in ${activeProject.name} — hold to start ${lastLaunchBackend()} directly`:'No Project selected'}
        onPointerDown={event=>{runHeldRef.current=false;beginLongPress(event,()=>{
          if(!activeProject)return
          runHeldRef.current=true
          const backend=lastLaunchBackend()
          showInteractionHud(`starting ${backend}…`)
          void spawnTerminal(activeProject.id,false,undefined,undefined,'after',backend)
        })}}
        onPointerUp={cancelLongPress} onPointerCancel={cancelLongPress} onPointerMove={moveLongPress}
        onClick={event=>{
          if(runHeldRef.current){runHeldRef.current=false;return}
          if(activeProject)toggleRunMenu(activeProject,event.currentTarget)
        }}>▶ Run</button>
      {/* The side panel's only tap target on a phone: the desktop's always-visible rail is
          hidden here, so until now the drawer opened by two-finger swipe or the command
          palette alone — neither of which is discoverable. It takes the right corner and
          mirrors nav at the left because the two full-height drawers they open are mirror
          images, and an edge toggle sitting anywhere but its own edge reads as unrelated to
          the panel it opens. Run gives up the corner for it and is found by its label. Opens
          the last tab used, which is why the icon is the panel and not one tab's mark. */}
      <button class="mobile-drawer-toggle" aria-label={clipboardOpen?'Close side panel':`Open side panel (${DRAWER_TABS.find(tab=>tab.id===drawerTabId)?.label||'clipboard'})`} aria-expanded={clipboardOpen} title={clipboardOpen?'Close side panel':`Side panel — ${DRAWER_TABS.find(tab=>tab.id===drawerTabId)?.label||'clipboard'}`} onClick={()=>setClipboardOpen(value=>!value)}><SidePanelIcon/></button>
    </div>
    <InteractionHud />
    {voiceStatus&&!conversationPaneId&&<ConversationSurface conversation={conversation} commands={commands} configuredCommands={voiceStatus.commands} onOpenSettings={()=>openSettings('Voice')}/>}

    <ContinuityBanner />
    {uiUpdateAvailable && <div class="ui-update-banner" role="status" aria-live="polite">
      <strong>UI update ready</strong>
      <span>This device will reload when the page is hidden.</span>
      <button onClick={() => location.reload()}>Reload now</button>
    </div>}
    {broadcast && <div class="broadcast-banner"><strong>Broadcast input is on</strong><span>Keystrokes mirror to sessions in the broadcast set.</span><button onClick={() => setBroadcast(false)}>Stop broadcasting</button></div>}

    <div class={`workspace ${sidebarCollapsed?'sidebar-collapsed':''} ${clipboardOpen&&!mobileWorkspace?'drawer-open':''} ${drawerTabDisplay==='title'?'drawer-tabs-title':''}`} style={{'--sidebar-width':`${sidebarWidth}px`,'--drawer-width':`${renderedDrawerWidth}px`,'--utility-rail-width':`${utilityRailWidth}px`} as JSX.CSSProperties}>
      <header class="app-topbar">
        <div class="app-identity"><button class="sidebar-collapse" aria-label={sidebarCollapsed?'Expand sidebar':'Collapse sidebar'} title={sidebarCollapsed?'Expand sidebar':'Collapse sidebar'} onClick={toggleSidebar}>{sidebarCollapsed?'»':'«'}</button><span class="daemon-ok" title="daemon::connected" aria-label="daemon connected"><i aria-hidden="true" /></span><strong class="desktop-project-name" title={activeProject?.name||'No Project selected'}>{activeProject?.name||'No Project'}</strong>{voiceStatus&&<ConversationToggle conversation={conversation} configured={!!voiceStatus.stt_enabled} onOpenSettings={()=>openSettings('Voice')}/>} {activeProject&&<button data-tutorial="run" class="project-run-header" aria-haspopup="menu" aria-expanded={runMenu?.project.id===activeProject.id} title={`Run in ${activeProject.name}`} onClick={event=>toggleRunMenu(activeProject,event.currentTarget)}>▶ Run</button>}</div>
      </header>
      <aside ref={sidebarRef} class={`sidebar ${sidebarOpen ? 'open' : ''}`} onContextMenu={event=>{const target=event.target as Element;if(target.closest('.sidebar-heading,.project-row,.session-row,.sidebar-note-row,.sidebar-footer'))return;event.preventDefault();setContextMenu(null);setProjectMenu(null);setNoteMenu(null);setSortMenu(null);setMainMenuOpen(false);setSidebarMenu({x:event.clientX,y:event.clientY})}}>
        {/* PROJECTS names the whole navigation tree. Ungrouped Projects are root
            rows, while only explicit Groups receive their own headers. */}
        <div class="sidebar-tools sidebar-projects-header">
          <strong>PROJECTS</strong>
          <button class="sidebar-tool" disabled={!displayProjects.length} aria-label={allFolded?'Expand all Projects and Groups':'Collapse all Projects and Groups'} title={allFolded?'Expand all Projects and Groups':'Collapse all Projects and Groups'} onClick={()=>setAllFolded(!allFolded)}>{allFolded?'⊞':'⊟'}</button>
          <button class={`sidebar-tool sidebar-sort ${sidebarOrder.projectSort==='custom'&&sidebarOrder.sectionSort==='custom'?'':'active'}`} disabled={!displayProjects.length} aria-haspopup="menu" aria-expanded={!!sortMenu} aria-label="Sort Projects and Groups" title={`Sort - Projects: ${projectSortLabel(sidebarOrder.projectSort)} · Groups: ${sectionSortLabel(sidebarOrder.sectionSort)}`} onClick={event=>{event.stopPropagation();if(sortMenu){setSortMenu(null);return}const rect=event.currentTarget.getBoundingClientRect();openSortMenu(rect.right,rect.bottom+4)}}>⇅</button>
        </div>
        <div class="project-tree">
          {visibleProjects.length===0&&<button data-tutorial="empty-project" class="empty-project-cta" onClick={()=>openProjectsManager()}><strong>{projects.length?'No Projects shown':'Create your first Project'}</strong><small>{projects.length?'Open Projects to show or add an active Project.':'Open Projects to add a canonical folder.'}</small></button>}
          {!!ungroupedProjects.length&&<div class="sidebar-project-list sidebar-ungrouped-projects">
            {ungroupedProjects.map(project=>sidebarProjectRow(project,ungroupedProjectIds))}
          </div>}
          {projectBuckets.map(bucket=>{
            const peerIds=bucket.items.map(item=>item.id)
            const bucketCollapsed=isBucketCollapsed(sidebarOrder,bucket.id)
            // Folding a section hides whichever Project holds the waiting agent, so
            // the header has to answer for all of them: a count for how much is live,
            // and the strongest state as a dot, because a bare count cannot say that
            // something in here is waiting on you.
            const bucketStatus=bucketCollapsed?projectSetRailStatus(sessions,peerIds,ackedTurns):null
            return <section class={`sidebar-project-list sidebar-project-bucket ${bucketCollapsed?'collapsed':''}`} key={bucket.id} data-reorder-id={bucket.id}>
            {/* Desktop uses the header as both drag handle and collapse toggle. Mobile
                keeps only the tap-to-fold half because Project rows are its sole sidebar
                reorder target. The rename button stops either parent gesture. */}
            <header title={mobileWorkspace?`${bucket.name} - tap to ${bucketCollapsed?'expand':'collapse'}`:`${bucket.name} - click to ${bucketCollapsed?'expand':'collapse'}, drag to reorder`} onPointerDown={event=>{if(!mobileWorkspace)beginBucketPointerDrag(event,bucket.id,bucket.name)}} onClick={()=>{if(suppressDragClickRef.current===`bucket:${bucket.id}`){suppressDragClickRef.current=null;return}setSidebarOrder(toggleBucketCollapsed(sidebarOrder,bucket.id))}}>
              <span class="bucket-chevron" aria-hidden="true">{bucketCollapsed?'▸':'▾'}</span><span>{bucket.name}</span>
              {bucketStatus&&bucketStatus.liveCount>0&&<span class={`bucket-collapsed-badge activity-${bucketStatus.activity} ${bucketStatus.unread?'unread':''}`} title={`${bucketStatus.liveCount} live session${bucketStatus.liveCount===1?'':'s'} · ${projectRailActivityLabel[bucketStatus.activity]}${bucketStatus.unread?' · unread output':''}`}><i aria-hidden="true"/>{bucketStatus.liveCount}</span>}
              {/* Rename only. Sort lives in the PROJECTS header, and delete is gone
                  from the sidebar entirely — a header button one pixel from the fold
                  toggle should not be able to dissolve a Group. Emptying a Group still
                  removes it from the sidebar, since a Group with no Projects in it is
                  not rendered. */}
              <button class="bucket-rename" title="Rename group" aria-label={`Rename group ${bucket.name}`} onPointerDown={event=>event.stopPropagation()} onClick={event=>{event.stopPropagation();const group=projectGroups.find(item=>item.id===bucket.id);if(group)setGroupEdit({id:group.id,name:group.name})}}>✎</button></header>
              {!bucketCollapsed&&bucket.items.map(project=>sidebarProjectRow(project,peerIds))}
            </section>})}
        </div>
        <div class="sidebar-status">
          <AccountSwitcher onManage={()=>openSettings('Accounts')}/>
          <ResourceUsageSummary snapshot={processFleet} sessions={sessions} projects={projects} onRefresh={()=>void loadProcesses()} onOpenFleet={()=>openProcessViewer()}/>
        </div>
        <div class="sidebar-footer"><button data-tutorial="menu" class="menu-trigger" onClick={() => setMainMenuOpen(value => !value)}><span>:</span> menu</button><button type="button" class={`notify-trigger ${alertsEnabled?'':'off'}`} aria-pressed={alertsEnabled} title={alertsEnabled?'Alerts on - click to mute sounds and push':'Alerts muted - click to restore sounds and push'} aria-label={alertsEnabled?'Mute alerts':'Enable alerts'} onClick={toggleAlerts}><svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 2c-2.2 0-3.6 1.6-3.6 3.9 0 2.7-1.2 3.6-1.2 4.6h9.6c0-1-1.2-1.9-1.2-4.6C11.6 3.6 10.2 2 8 2Z"/><path d="M6.6 12.6a1.5 1.5 0 0 0 2.8 0"/>{!alertsEnabled&&<line x1="2.6" y1="2.6" x2="13.4" y2="13.4"/>}</svg></button>{/* Settings is one of the two things anyone reaches for from this footer, and it
            was three interactions deep (menu → All Settings…). It sits beside the bell
            because both are install-wide switches rather than navigation, and the menu
            entry stays: a cog next to a bell is discoverable, a named row is searchable. */}
          <button type="button" class="settings-trigger" title="Settings" aria-label="Open Settings" onClick={()=>openSettings()}><svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="8" cy="8" r="5.1"/><circle cx="8" cy="8" r="1.9"/><path d="M8 1.2v1.8M8 13v1.8M14.8 8H13M3 8H1.2M12.8 3.2l-1.3 1.3M4.5 11.5l-1.3 1.3M12.8 12.8l-1.3-1.3M4.5 4.5L3.2 3.2"/></svg></button><button data-tutorial="projects" class="project-trigger" onClick={()=>openProjectsManager()}><span>◇</span> projects</button></div>
      </aside>
      {/* The collapsed strip keeps the sidebar's own controls reachable rather
          than forcing an expand round-trip for menu, projects, or status. */}
      {sidebarCollapsed&&<nav class="sidebar-rail" aria-label="Sidebar shortcuts">
        <div class="rail-projects" aria-label="Projects">
          {displayProjects.map(project=>{const status=projectRailStatus(sessions,project.id,ackedTurns);const selected=project.id===projectId;const readLabel=status.agentCount?(status.unread?' · unread output':' · read'):'';const countLabel=status.liveCount?` · ${status.liveCount} live session${status.liveCount===1?'':'s'}`:'';return <button
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
      <div class="sidebar-resizer" role="separator" tabindex={0} aria-label="Resize sidebar" aria-orientation="vertical" aria-valuemin={SIDEBAR_MIN_WIDTH} aria-valuemax={SIDEBAR_MAX_WIDTH} aria-valuenow={Math.round(sidebarWidth)} title="Drag to resize or collapse · arrow keys adjust · double-click to reset" onPointerDown={beginSidebarResize} onDblClick={()=>persistSidebarWidth(SIDEBAR_DEFAULT_WIDTH)} onKeyDown={event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();persistSidebarWidth(event.key==='Home'?SIDEBAR_MIN_WIDTH:event.key==='End'?SIDEBAR_MAX_WIDTH:sidebarWidth+(event.key==='ArrowLeft'?-10:10))}} />

      {/* The utility drawer is a workspace grid child so the desktop rendering can
          be an in-flow column: the pane tree shrinks rather than being covered.
          Mobile takes the same element out of flow (position:fixed) and adds a
          scrim, which is why both renderings share one component. */}
      {clipboardOpen&&<UtilityDrawer
        layout={drawerLayout}
        presentation={renderedDrawerPresentation}
        transientTab={transientDrawerTab||undefined}
        onLayout={layout=>commitDrawerLayout(layout)}
        // The drag ghost's pointer-up also fires a click on the tab it started from, which
        // would switch to the tab the user was only moving.
        onTab={(tab,collapseIfSelected)=>{
          if(suppressDragClickRef.current===`drawer-tab:${tab}`){suppressDragClickRef.current=null;return}
          // Clicking the temporarily selected Actions tab is an explicit choice to
          // keep it, not the normal second-click request to collapse the drawer.
          if(collapseIfSelected&&!transientDrawerTab){setClipboardOpen(false);return}
          selectDrawerTab(tab)
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
        onConfigureActions={()=>{if(mobileWorkspace||transientDrawerTab)setClipboardOpen(false);openActionEditor()}}
        onManagePrompts={()=>{if(mobileWorkspace||transientDrawerTab)setClipboardOpen(false);setPromptScope(null);setPromptTargetId(null);setPromptLibraryOpen(true)}}
        onOpenFile={path=>{
          // The drag ghost's pointer-up also fires a click on the row it started from.
          if(suppressDragClickRef.current===`file:${noteResourceId('file',path)}`){suppressDragClickRef.current=null;return}
          if(activeProject)openProjectFile(activeProject,path)
        }}
        onOpenWorktreeFile={(worktree,path)=>{if(activeProject)openWorktreeFile(activeProject,worktree,path)}}
        onProjectUpdated={updated=>setProjects(items=>items.map(item=>item.id===updated.id?updated:item))}
        // Desktop only: the drawer is an in-flow column there, so a file row can be dragged
        // onto a visible pane. On mobile it is an overlay with nothing to drop onto.
        onFileDragStart={mobileWorkspace?undefined:(path,event)=>beginFileTabDrag(event,path)}
        onSendToAgent={request=>{if(mobileWorkspace)setClipboardOpen(false);setSendToAgent(request)}}
        queueOpenToken={queueOpenToken || undefined}
        onQueueOpenAsTab={sessionId=>void openQueueTab(sessionId)}
        processSnapshot={processFleet}
        projects={projects}
        // '' (all Projects) is the stored default; an unscoped tab means the Project the
        // drawer is sitting beside, so it resolves to the active one at render time and
        // follows a Project switch instead of pinning whichever was active when it opened.
        processScope={processWatchScope&&projects.some(project=>project.id===processWatchScope)?processWatchScope:(projectId||'')}
        onProcessScope={setProcessWatchScope}
        onRefreshProcesses={()=>void loadProcesses()}
        onOpenPreview={(sessionId,url)=>{
          const owner=sessions.find(item=>item.id===sessionId)
          if(owner)void openDetectedServer({url},owner)
        }}
        onOpenInspector={scope=>openProcessViewer(null,scope)}
        onOpenProjectSettings={id=>{const target=projects.find(item=>item.id===id);if(target)openProjectsManager({project:target,tab:'settings'})}}
        onOpenAutomationDashboard={()=>setAutomationOpen(true)}
        queuePending={queuePendingTotal}
        onOpenFleetQueue={()=>openFleetQueue()}
        notesAllProjects={notesAllProjects}
        onNotesAllProjects={setNotesAllProjects}
        onOpenNote={(targetProject,noteId,title,place)=>{
          setNoteTitles(current=>({...current,[`${targetProject}:${noteId}`]:title}))
          openBrowsedNote(targetProject,noteId,place)
        }}
        onOpenScratchpad={openScratchpad}
        drawerNoteId={drawerNoteId}
        noteTargetClaimToken={drawerNoteClaimRequest?.projectId===projectId&&drawerNoteClaimRequest.resourceId===drawerNoteId?drawerNoteClaimRequest.token:undefined}
        onNoteTargetClaimed={token=>setDrawerNoteClaimRequest(current=>current?.token===token?null:current)}
        onPopDrawerNoteToTab={resourceId=>popDrawerNoteToTab(resourceId,projectId)}
        tabDisplay={drawerTabDisplay}
        onTabDragStart={beginDrawerTabDrag}
        onProjectionTabReorder={beginMobileDrawerTabDrag}
        onTabDisplayMenu={(x,y)=>openDrawerDisplayMenu(x,y,'tabs')}
        draggingTab={dragDrawerTab}
        announcement={drawerAnnouncement}
        promptPreselect={promptPreselect}
        onResize={beginDrawerResize}
        width={renderedDrawerWidth}
        minimumWidth={DRAWER_MIN_WIDTH}
        maximumWidth={drawerWidthLimit}
        defaultWidth={DRAWER_DEFAULT_WIDTH}
        onWidth={width=>persistDrawerWidth(width,drawerWidthLimit)}
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
      {/* Desktop only, and only while the drawer is closed: this rail *is* what the collapsed
          drawer looks like, the same way `.sidebar-rail` is what the collapsed navigation
          sidebar looks like. It makes these surfaces discoverable without a menu or a chord;
          once the drawer is open its pane strips own tab selection, so keeping the rail beside
          them would only repeat the same icons and spend a column doing it. Mobile reaches the
          same tabs through the drawer's own tab strip after a two-finger swipe. */}
      {!mobileWorkspace&&!clipboardOpen&&<nav class={`utility-rail ${utilityRailDisplay==='title'?'title-mode':'icon-mode'}`} aria-label="Side panel">
        {drawerLauncherTabs.filter(tab=>tab.id!=='transcript'||hasHarnessTranscript(active?.backend)).map(tab=>{
          const Icon=DRAWER_TAB_ICONS[tab.id]
          // No selected state to draw: the rail is only rendered while the drawer is closed,
          // so no tab it lists is showing anywhere.
          return <button
            key={tab.id}
            data-tutorial={tab.id==='notes'?'project-notes':undefined}
            data-scope={tab.scope}
            aria-label={`${tab.title}${tab.scope==='session'?'. Session scoped.':''}`}
            title={`${tab.title}${tab.scope==='session'?' - session scoped':''}`}
            onContextMenu={event=>{
              event.preventDefault()
              event.stopPropagation()
              openDrawerDisplayMenu(event.clientX,event.clientY,'rail')
            }}
            onClick={()=>showDrawerTab(tab.id)}
          >{utilityRailDisplay==='title'?<span class="drawer-tab-title">{tab.label}</span>:<Icon/>}{tab.id==='notifications'&&notificationUnread>0&&<i class="drawer-badge">{notificationUnread>99?'99+':notificationUnread}</i>}</button>
        })}
      </nav>}

      <main data-tutorial="workspace" class="main-stage" onContextMenu={event => { if (!activeLayout.root) { event.preventDefault(); setEmptyMenu({ x: event.clientX, y: event.clientY }) } }}>
        <div class="project-workspace unified-workspace">
          <div class="terminal-workspace">
            {mobileWorkspace?mobileUnifiedWorkspace:(activeLayout.root||focusedOutsideLayout) ? <div class="pane-tree">{renderPaneNode(zoomedId ? stackForView(activeLayout,zoomedId)||activeLayout.root! : focusedOutsideLayout&&activeId ? paneStack([terminalLeaf(activeId)],activeId) : activeLayout.root!)}</div> : <div class="pane-tree"><section data-tutorial="workspace-pane" class="pane-stack empty-workspace-pane">
              <div class="stack-active empty-stage"><div class="hero-terminal" aria-hidden="true">&gt;_</div><h1>Your Project workspace.</h1><p>Run a terminal, or open a note, a file, or a preview to begin. Files and notes live in the side panel.</p></div>
            </section></div>}
          </div>
        </div>
      </main>
    </div>

    {launcherOpen && <div class="quick-launcher" role="dialog" aria-modal="true" aria-label="New terminal custom">
      <div class="quick-heading"><span>NEW TERMINAL CUSTOM::{projects.find(project => project.id === launcherProject)?.name?.toUpperCase()}{launcherSplit?'::SPLIT':''}</span><button onClick={() => setLauncherOpen(false)}>×</button></div>
      <form onSubmit={event => { event.preventDefault(); void spawnTerminal(launcherProject, launcherSplit, launcherProfile) }}>
        <label>Shell profile<select value={launcherProfile} onChange={event=>setLauncherProfile(event.currentTarget.value)}>{profiles.filter(profile=>profile.backend==='shell').map(profile=><option value={profile.id}>{profile.marker} · {profile.label}</option>)}</select><small>{profiles.find(profile=>profile.id===launcherProfile)?.capabilities.join(' · ')}</small></label>
        <label>Project root<input value={projects.find(project=>project.id===launcherProject)?.root||''} readOnly /></label>
        <button class="primary" type="submit">Open {profiles.find(item=>item.id===launcherProfile)?.label || 'terminal'}</button>
      </form>
    </div>}

    {runMenu&&<ProjectRunMenu project={runMenu.project} profiles={profiles} anchor={{x:runMenu.x,y:runMenu.y}} onClose={()=>{runMenuClosedAt.current=Date.now();setRunMenu(null)}} onLaunch={(backend,profileId)=>{const target=runMenu.project.id;setRunMenu(null);void spawnTerminal(target,false,profileId,undefined,'after',backend)}} onCustom={()=>{const target=runMenu.project.id;setRunMenu(null);openLauncher(target)}} onSessions={items=>void attachActionSessions(runMenu.project.id,items)} onWorktreeCreated={(path,backend)=>void startWorktreeSession(runMenu.project.id,path,backend)} onError={setError}/>}

    {paletteOpen && <div class="palette-layer" onMouseDown={event => event.target === event.currentTarget && setPaletteOpen(false)}>
      <div class="palette" role="dialog" aria-modal="true" aria-label="Command palette"><input ref={paletteInput} role="combobox" aria-controls="command-results" aria-expanded="true" aria-activedescendant={shownCommands[paletteIndex]?`command-${shownCommands[paletteIndex].id.replaceAll(/[^a-zA-Z0-9_-]/g,'-')}`:undefined} value={paletteQuery} onInput={event => setPaletteQuery(event.currentTarget.value)} onKeyDown={event => {
        // Stops here rather than also reaching the window handler, so one keypress is one pop.
        if (event.key === 'Escape') { event.stopPropagation(); dismissStack.pop() }
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

    {contextMenu && <div ref={el=>fitMenuInViewport(el)} class="context-menu" role="menu" aria-label={`Session actions for ${sessionName(contextMenu.session)}`} style={{ left: clampContextMenuLeft(contextMenu.x, innerWidth), top: Math.max(4, Math.min(contextMenu.y, innerHeight - 430)) }}>
      <div class="context-title">{sessionStateDot(contextMenu.session,rowConfig.dotShape,null,sessionStandingMark(contextMenu.session,rowConfig))}<strong>{sessionName(contextMenu.session)}</strong></div>
      <div class="context-session-info">
        <span title="Process ID of the session's root process">PID {contextMenu.session.pid}</span>
        {contextMenu.session.git.branch&&<span class="git-chip" title={`Git branch ${contextMenu.session.git.branch}${contextMenu.session.git.dirty?` · ${contextMenu.session.git.dirty} changed files`:' · clean'}`}>git:{contextMenu.session.git.branch}{contextMenu.session.git.dirty?` +${contextMenu.session.git.dirty}`:''}</span>}
        {(()=>{const startup=startupSummary(contextMenu.session);return startup&&<span class="startup-chip" title={startupTimingTitle(contextMenu.session,clientStartupTimings[contextMenu.session.id]||{})}>{startup.label}:{formatStartupMs(startup.value)}</span>})()}
      </div>
      <button onClick={() => runNamedCommand('session.rename')}>Rename</button>
      {isAgent(contextMenu.session)&&contextMenu.session.auto_named!==false&&!isEndedSession(contextMenu.session)&&<button onClick={() => runNamedCommand('session.regenerateTitle')}>Regenerate title</button>}
      {contextMenu.source==='sidebar'&&<button onClick={() => runNamedCommand('session.open')}>Open in focused pane</button>}
      {['exited', 'crashed'].includes(contextMenu.session.state) && isAgent(contextMenu.session) && <button onClick={() => runNamedCommand('session.resume')}>Resume as new…</button>}
      {activityBadges(contextMenu.session).length>0&&<button onClick={() => runNamedCommand('session.clearStandingActivity')}>Clear standing activity</button>}
      {isAgent(contextMenu.session)&&!isEndedSession(contextMenu.session)&&<button onClick={()=>runNamedCommand('session.toggleRead')}>{isUnread(contextMenu.session,ackedTurns)?'Mark as read':'Mark as unread'}</button>}
      <button onClick={() => runNamedCommand('session.copyId')}>Copy session ID</button>
      {/* Pane-only, deliberately. A session's own ⋯ header menu is where its
          full detail lives, and these are errands you run while working *in* a
          session rather than while pointing at one from a list. On a sidebar row
          or a tab title they were pure length: three rows of rarely-wanted
          plumbing between the two things those menus are actually opened for,
          Rename and Kill. Same actions, same commands, one surface. */}
      {contextMenu.source==='pane'&&<>
        <button onClick={() => runNamedCommand('session.copyCwd')}>Copy working directory</button>
        <button onClick={()=>{const target=contextMenu.session;setPromptScope(projects.find(project=>project.id===target.project_id)||null);setPromptTargetId(target.id);setContextMenu(null);setPromptLibraryOpen(true)}}>Insert prompt template…</button>
        <button onClick={() => runNamedCommand('processes.open')}>Processes and previews…</button>
      </>}
      {/* No context menu touches tab order or pane geometry on any platform — not split,
          stack, dissolve, or move. They answer "how is the workspace laid out", which is
          not the question a menu opened on a session or a tab is asked, and the direction
          rows pushed Rename and Kill past the fold on every source. Desktop layout is drag
          or the palette (session.openSplit*, pane.split*, pane.moveTab*,
          session.groupStack, stack.dissolve, session.customSplit). Mobile has neither, so
          its rail is simply the projection's order — see mobileWorkspace. The device-local
          permutation overlay that used to back the touch row went with it, deliberately:
          left in place it could not be written any more, but a phone that had already
          saved one would have stayed permanently pinned to it with no way out.
          `New terminal as tab` went the same way, on every source including the ⋯ menu:
          it spawns a *new* session, which is the Run button's whole job, and reading it
          off a menu opened on some other session made the pane it landed in a guess. */}
      {voiceStatus?.enabled&&isAgent(contextMenu.session)&&<MenuGroup id="session-voice" label={`Read aloud · ${voiceModeLabel(effectiveVoiceMode(contextMenu.session))}`} openId={menuGroup} onOpenChange={setMenuGroup} hint="Spoken replies for this session">
        {/* Four flat rows for a setting most sessions never change, sitting between
            the actions this menu exists for. Behind one row carrying its current
            mode, so the common case reads the state without opening anything. */}
        {(['off','on_demand','auto'] as VoiceMode[]).map(mode=><button key={mode} onClick={()=>{void setVoiceMode(contextMenu.session,mode);setContextMenu(null)}}>{effectiveVoiceMode(contextMenu.session)===mode?'✓ ':''}{mode==='off'?'Off':mode==='on_demand'?'On demand':'Auto on reply'}</button>)}
        <button onClick={()=>{const target=contextMenu.session;setContextMenu(null);void speakLastReply(target)}}>Speak last reply now</button>
      </MenuGroup>}
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
      <button onClick={()=>{const target=projectMenu.project;setProjectMenu(null);openNotesBrowser(target)}}>Notes…</button>
      <button onClick={() => runNamedCommand('processes.project')}>Processes…</button>
      <button onClick={() => runNamedCommand('prompts.openProject')}>Prompt library…</button>
      <button onClick={() => runNamedCommand('queue.fleetProject')}>Fleet queue…</button>
      <button onClick={()=>{openProjectFiles(projectMenu.project);setProjectMenu(null)}}>Browse files…</button>
      <div class="context-subtitle">PROJECT</div>
      <button onClick={() => runNamedCommand('project.reveal')}>Reveal in Explorer</button>
      <button onClick={()=>{const target=projectMenu.project;setProjectMenu(null);toggleProjectCollapsed(target.id)}}>{collapsedProjects.has(projectMenu.project.id)?'Expand in sidebar':'Collapse in sidebar'}</button>
      {confirmHideId!==projectMenu.project.id&&<button onClick={()=>{const target=projectMenu.project;if(canHideProject(openWorkFor(target))){setProjectMenu(null);void hideProject(target)}else setConfirmHideId(target.id)}}>Hide from sidebar</button>}
      {confirmHideId===projectMenu.project.id&&<>
        <div class="context-subtitle">CLOSE OPEN WORK TO HIDE</div>
        <div class="context-note">{describeOpenWork(openWorkFor(projectMenu.project))||'No live work'} still attached. Hiding would strand it off-screen.</div>
        <button class="danger" onClick={()=>{const target=projectMenu.project;setProjectMenu(null);setConfirmHideId(null);void closeWorkAndHideProject(target).catch(cause=>{setError(cause instanceof Error?cause.message:String(cause));void refresh()})}}>Close it &amp; hide</button>
        <button onClick={()=>setConfirmHideId(null)}>Cancel</button>
      </>}
      <label class="context-select">Group<select value={projectMenu.project.group_id||''} onChange={event=>{const target=projectMenu.project;const group_id=event.currentTarget.value||null;void api<Project>('PATCH',`/api/projects/${target.id}`,{group_id}).then(updated=>setProjects(items=>items.map(item=>item.id===updated.id?updated:item)));setProjectMenu(null)}}><option value="">Ungrouped</option>{projectGroups.map(group=><option value={group.id}>{group.name}</option>)}</select></label>
      <button onClick={() => runNamedCommand('project.rename')}>Rename project</button>
      <button disabled={!commands.find(item=>item.id==='project.moveUp')?.available} onClick={()=>runNamedCommand('project.moveUp')}>Move Project up</button>
      <button disabled={!commands.find(item=>item.id==='project.moveDown')?.available} onClick={()=>runNamedCommand('project.moveDown')}>Move Project down</button>
      <button onClick={() => runNamedCommand('project.settings')}>Project settings…</button>
      <button class="danger" onClick={() => runNamedCommand('project.delete')}>Remove from swe-mux…</button>
    </div>}

    {sidebarMenu&&<div ref={el=>fitMenuInViewport(el)} class="context-menu" role="menu" aria-label="Sidebar actions" style={{left:clampContextMenuLeft(sidebarMenu.x,innerWidth),top:Math.max(4,Math.min(sidebarMenu.y,innerHeight-300))}}>
      <div class="context-title"><strong>PROJECTS</strong></div>
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('project.add')}}>Add project…</button>
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('project.create')}}>Manage projects…</button>
      <button onClick={()=>{setSidebarMenu(null);setGroupEdit({name:''})}}>Create group</button>
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('history.open')}}>Session history</button>
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('notes.browse')}}>Notes…</button>
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('processes.all')}}>Process fleet…</button>
      <div class="context-rule" />
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('settings.open')}}>All Settings…</button>
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('daemon.reload')}}>Reload daemon (keep sessions)</button>
      <button onClick={()=>{setSidebarMenu(null);runNamedCommand('app.redeploy')}}>Rebuild + redeploy app (keep sessions)</button>
    </div>}

    {/* One sort for the whole sidebar. It was per section once, so a hand-arranged
        shortlist and an alphabetical pile could coexist; that put a ⇅ on every Group
        header for a preference that in practice was set the same everywhere, so the
        modes collapsed into one and the control moved to the PROJECTS header. */}
    {sortMenu&&<div ref={el=>fitMenuInViewport(el)} class="context-menu" role="menu" aria-label="Sort Projects and Groups" style={{left:clampContextMenuLeft(sortMenu.x,innerWidth),top:Math.max(4,Math.min(sortMenu.y,innerHeight-330))}}>
      <div class="context-title"><strong>SORT PROJECTS</strong></div>
      {PROJECT_SORT_OPTIONS.map(option=>{
        const active=sidebarOrder.projectSort===option.id
        return <button key={option.id} title={option.hint} aria-checked={active} role="menuitemradio" onClick={()=>{setSidebarOrder(setProjectSortMode(sidebarOrder,option.id));setSortMenu(null)}}>{active?'✓ ':''}{option.label}</button>
      })}
      <div class="context-rule"/>
      {/* One level up, from the same control: the header's ⇅ already means "how is
          this list ordered", and the sidebar has no global Group header to hang a
          separate control on. Behind a MenuGroup so the common case stays flat, and
          carrying its current mode in the label since the section order has no
          always-visible indicator of its own. */}
      <MenuGroup id="sections" label={`Sort Groups · ${sectionSortLabel(sidebarOrder.sectionSort)}`} openId={menuGroup} onOpenChange={setMenuGroup} hint="Order the named Groups">
        {SECTION_SORT_OPTIONS.map(option=>{
          const active=sidebarOrder.sectionSort===option.id
          return <button key={option.id} title={option.hint} aria-checked={active} role="menuitemradio" onClick={()=>{setSidebarOrder({...sidebarOrder,sectionSort:option.id});setSortMenu(null)}}>{active?'✓ ':''}{option.label}</button>
        })}
      </MenuGroup>
      <div class="context-note">Dragging a Project or Group into place puts that level back on Manual order.</div>
    </div>}

    {noteMenu&&<div ref={el=>fitMenuInViewport(el)} class="context-menu" role="menu" aria-label="Resource view actions" style={{left:clampContextMenuLeft(noteMenu.x,innerWidth),top:Math.max(4,Math.min(noteMenu.y,innerHeight-220))}}>
      <div class="context-title"><strong>{noteTabLabel(noteMenu.resourceId)}</strong></div>
      <button onClick={()=>void placeNoteResourceInFocusedPane(noteMenu.resourceId,noteMenu.projectId)}>{mobileWorkspace?'Open tab':'Open in focused pane'}</button>
      {!mobileWorkspace&&directionRow('Open in split:',option=>void splitNoteResource(noteMenu.resourceId,noteMenu.projectId,option.direction,option.position))}
      {/* Same copy actions the Files tree offers, so a file already open as a tab does not
          have to be found again in the browser just to get its path. */}
      {fileMenuTarget(noteMenu)&&<><div class="context-rule"/>
        <button title={fileMenuTarget(noteMenu)!.absolute} onClick={()=>void copyFileClipboard(noteMenu,'absolute')}>Copy full path</button>
        <button title={fileMenuTarget(noteMenu)!.relative} onClick={()=>void copyFileClipboard(noteMenu,'relative')}>Copy path from {fileMenuTarget(noteMenu)!.worktree?'worktree':'project'} root</button>
        <button title={`Copy the file's text, capped at ${FILE_COPY_MAX_LINES.toLocaleString()} lines`} onClick={()=>void copyFileClipboard(noteMenu,'contents')}>Copy file contents</button>
      </>}
      {workspaceNoteIds(noteMenu.projectId).includes(noteMenu.resourceId)&&<><div class="context-rule"/><button onClick={()=>{const target=noteMenu;setNoteMenu(null);void removeWorkspaceNote(target.projectId,target.resourceId)}}>Close resource tab</button></>}
    </div>}

    {tabMenu&&<div ref={el=>fitMenuInViewport(el)} class="context-menu tab-context-menu" role="menu" aria-label={`Tab actions for ${tabMenu.label}`} style={{left:clampContextMenuLeft(tabMenu.x,innerWidth),top:Math.max(4,Math.min(tabMenu.y,innerHeight-300))}}>
      <div class="context-title"><strong>{tabMenu.label}</strong></div>
      {/* Same rule as the session menu above, on every platform: no context menu moves,
          splits or reorders, and none of them spawns a session. Rearranging a resource
          tab is a drag; the keyboard route is the palette; new work is the Run button,
          which is on the mobile rail too. */}
      {fileMenuTarget(tabMenu)&&<>
        <button title={fileMenuTarget(tabMenu)!.absolute} onClick={()=>void revealFileResource(tabMenu)}>Open in default explorer</button>
        <button title={fileMenuTarget(tabMenu)!.absolute} onClick={()=>void copyFileClipboard(tabMenu,'absolute')}>Copy full path</button>
        <button title={fileMenuTarget(tabMenu)!.relative} onClick={()=>void copyFileClipboard(tabMenu,'relative')}>Copy path from {fileMenuTarget(tabMenu)!.worktree?'worktree':'project'} root</button>
      </>}
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
      {unpanned.map(session => <button role="menuitem" onClick={() => runNamedCommand(`session.attach(${session.id})`)}>{sessionStateDot(session,rowConfig.dotShape,null,sessionStandingMark(session,rowConfig))}{sessionName(session)}</button>)}
    </div>}

    {drawerDisplayMenu&&<div ref={el=>fitMenuInViewport(el)} class="context-menu drawer-display-menu" role="menu" aria-label={`${drawerDisplayMenu.surface==='tabs'?'Drawer tabs':'Right rail'} options`} style={{left:clampContextMenuLeft(drawerDisplayMenu.x,innerWidth),top:Math.max(4,Math.min(drawerDisplayMenu.y,innerHeight-140))}}>
      <div class="context-title"><strong>{drawerDisplayMenu.surface==='tabs'?'DRAWER TABS':'RIGHT RAIL'}</strong></div>
      {(()=>{const display=drawerDisplayMenu.surface==='tabs'?drawerTabDisplay:utilityRailDisplay;return <button role="menuitemcheckbox" aria-checked={display==='title'} onClick={()=>void persistDrawerDisplay(drawerDisplayMenu.surface,display==='icon'?'title':'icon')}>{display==='title'?'✓ ':''}Text labels</button>})()}
      <div class="context-rule" />
      <button role="menuitem" disabled={!clipboardOpen} onClick={()=>{setDrawerDisplayMenu(null);setClipboardOpen(false)}}>Collapse utility drawer</button>
    </div>}

    {mainMenuOpen && <div data-tutorial="main-menu" class="context-menu main-menu" role="menu" aria-label="swe-mux menu">
      <div class="context-title"><strong>swe-mux menu</strong></div>
      {/* The lead block needs no heading: these are the app's general-purpose
          surfaces, opened across everything. Right-clicking a Project row opens the
          Project-scoped ones prefiltered to it. Anything that acts on one Project
          lives there, not here. */}
      <button onClick={() => runNamedCommand('history.open')}>Session history</button>
      <button onClick={() => runNamedCommand('notes.browse')}>Notes…</button>
      <button onClick={() => runNamedCommand('processes.all')}>Process fleet…</button>
      <button onClick={() => runNamedCommand('queue.fleet')}>Fleet queue{queuePendingTotal?` [${queuePendingTotal} pending]`:''}</button>
      <button onClick={()=>runNamedCommand('prompts.open')}>Prompt library…</button>
      <button onClick={()=>runNamedCommand('clipboard.open')}>Clipboard history…</button>
      <button onClick={() => runNamedCommand('usage.open')}>Usage analytics…</button>
      <button onClick={() => runNamedCommand('networkUsage.open')}>Bandwidth usage…</button>
      <button onClick={() => runNamedCommand('storageUsage.open')}>Storage usage…</button>
      <button onClick={() => runNamedCommand('notifications.open')}>Notifications{notificationUnread?` [${notificationUnread} new]`:''}</button>
      <div class="context-subtitle">CONFIGURATION</div>
      {/* Adding a Project lives in the registry and the empty-sidebar menu; this
          menu keeps only the surfaces that act across the whole app. */}
      <button onClick={() => runNamedCommand('project.create')}>Manage projects…</button>
      <button onClick={() => runNamedCommand('actions.configure')}>Configure Actions…</button>
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

    {historyOpen&&<HistoryBrowser projects={orderedProjects} initialProjectId={historyScope} onClose={()=>setHistoryOpen(false)} onResume={resumeHistoryEntry} onSecondOpinion={previewSecondOpinion} onHandoff={openHandoff}/>}

    {projectsManagerOpen&&<ProjectsManager projects={projects} groups={projectGroups} sessions={sessions} profiles={profiles} initialProjectId={projectsManagerFocus?.projectId} initialTab={projectsManagerFocus?.tab} onClose={()=>{setProjectsManagerOpen(false);setProjectsManagerFocus(null)}} onAdd={()=>void createProject()} onAddGroup={()=>setGroupEdit({name:''})} onOpen={project=>{setProjectId(project.id);setProjectsManagerOpen(false)}} onNotes={project=>{setProjectsManagerOpen(false);openNotesBrowser(project)}} onFiles={project=>{setProjectsManagerOpen(false);openProjectFiles(project)}} onPatch={patchManagedProject} onRemove={removeProject}/>}

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

    {settingsOpen && <Settings activeUiScale={uiScale} onUiScalePreview={previewUiScaleConfig} initialSection={settingsSection} voiceCommands={commands} onStartTutorial={startTutorial} onOpenUsage={()=>{setSettingsOpen(false);setUsageOpen(true)}} onOpenAutomation={()=>{setSettingsOpen(false);setAutomationOpen(true)}} onClose={() => { setSettingsOpen(false); void refresh(); void loadProfiles(); void loadConfig(false) }} />}

    {harnessSetupNeeded && !settingsOpen && <HarnessSetup onDone={()=>{setHarnessSetupNeeded(false); void loadConfig(false); void refresh()}} onConfigureMore={()=>{setHarnessSetupNeeded(false); openSettings('Agents')}} />}

    {actionEditorOpen && <ActionEditorModal onClose={() => setActionEditorOpen(false)} />}

    {promptLibraryOpen&&<PromptLibrary project={promptScope||activeProject} backend={(sessions.find(session=>session.id===promptTargetId)||active)?.backend} onClose={()=>{setPromptLibraryOpen(false);setPromptTargetId(null)}} onInsert={text=>window.dispatchEvent(new CustomEvent('mux:terminal-action',{detail:{sessionId:promptTargetId||activeId,action:'insertText',text}}))}/>}

    {usageOpen&&<UsageDashboard onClose={()=>setUsageOpen(false)} onConfigure={()=>{setUsageOpen(false);openSettings('Usage analytics')}}/>}
    {networkUsageOpen&&<NetworkUsageModal onClose={()=>setNetworkUsageOpen(false)}/>}
    {storageUsageOpen&&<StorageUsageModal onClose={()=>setStorageUsageOpen(false)}/>}
    {fleetQueue&&<FleetQueue projects={projects} initialProjectId={fleetQueue.projectId} onOpenQueue={sessionId=>void openQueueForSession(sessionId)} onClose={()=>setFleetQueue(null)}/>}
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
