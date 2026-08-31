import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { ComponentChildren, JSX } from 'preact'
import { api, openWebSocket, type ApiError } from './api'
import {
  allBackendNames, branchesFromMessage, deliversHarnessPrompts, harnessDisplayName, hasHarnessTranscript, installHarnessRegistry, isAgentBackend,
  isObservedHarness, setHarnessEnablement,
} from './harnessRegistry'
import {
  CONFIGURATOR_LAUNCH_PATH, fetchConfiguratorOptions, launchBody, launchState, opensChooser,
  type ConfiguratorOptions,
} from './configurator.ts'
import { BranchPicker } from './BranchPicker'
import type { BranchRequest } from './branchPoints'
import { stageBranchSeed } from './branchSeed'
import { HANDSHAKE_TIMEOUT_MS, retryDelay, watchLiveness } from './liveness'
import { TerminalPane } from './TerminalPane'
import { EndedPaneBanner } from './EndedPaneBanner'
import {
  canRestartCold, coldSessionSummary, inactiveSessionSummary, isColdSession, isInactiveSession,
} from './coldSession.ts'
import { recordPaneVisits, warmPaneBudget, warmPaneIds } from './warmPanes'
import { windowsPtyCompatibility, type TerminalRendererPreference, type WindowsPtyCompatibility } from './terminalRenderer'
import { ProjectResource } from './ProjectResource'
import { SendToAgentPicker, type SendToAgentRequest, type SendToAgentResult, type SendToAgentTarget } from './SendToAgentPicker'
import { composerInsertion } from './composerInsertion'
import { QueuePane } from './QueuePane'
import { LazyChangeMap } from './LazyChangeMap'
import { editQueueMessage, enqueueMessage, fetchAutoStatus, fetchQueueSummary, sendQueueMessage, setAutoPaused, type QueueAutoStatus, type QueueTargetSummary } from './queueApi'
import { FleetQueue } from './FleetQueue'
import { ContinuityBanner } from './ContinuityBanner'
import { UpdateBanner } from './UpdateBanner'
import { DirectoryPicker } from './DirectoryPicker'
import { Dropdown } from './Dropdown'
import { folderNameFromPath } from './pathNames'
import { agentTargetName } from './agentTargets'
import { runDisplayName, sessionDisplayName } from './sessionNames'
import {
  defaultInitScriptSelection, emptyProjectCreateDraft, projectCreateFolder, projectCreateReady,
  projectCreateRoot, selectedStartingSets, suggestFolderName, toggleInitScript,
  type InitScript, type ProjectCreateDraft, type StartingSetCatalog,
} from './projectCreate'
import { isStaticPreview, previewLabel, type FleetSnapshot, type Preview } from './processFleet'
import { isPreviewableDocument } from './staticPreview'
import { ResourceUsageSummary } from './ResourceUsage'
import { ProjectsManager, type ProjectPatch } from './ProjectsManager'
import { MenuGroup } from './MenuGroup'
import { PreviewPane } from './PreviewPane'
import type { NotificationData, UiNotification } from './Notifications'
import { alertPreferences, setAlertPreferencesFor } from './alertPrefs'
import { ResourcesModal, type ResourceSegment } from './ResourcesModal'
import { UsageModal } from './UsageModal'
import type { UsageSegment } from './usageSegments'
import { HistoryBrowser } from './HistoryBrowser'
import { resumeDraft, type ScheduleDraft, type ScheduleTargetKind } from './schedules'
import { AccountSwitcher } from './ProviderAccounts'
import { harnessMark } from './harnessIcons'
import { PromptLibrary } from './PromptLibrary'
import { PROMPT_RAIL_EVENT } from './promptRail'
import { UtilityDrawer } from './UtilityDrawer'
import { INSTALL_CONFIG_CHANGED } from './installSwitches'
import { forgetProjectAutomations } from './projectAutomations'
import { OPEN_SETTING_EVENT, settingTarget, type OpenSettingDetail, type SettingTargetId } from './settingTargets'
import { OverflowRail } from './RailScroller'
import { PaneRunTrigger } from './PaneRunTrigger'
import { SCRATCHPAD_TAB_ID } from './noteTabs'
import {
  DRAWER_COLLAPSE_WIDTH, DRAWER_PROJECT_STATE_KEY, DRAWER_REOPEN_WIDTH,
  DRAWER_DEFAULT_WIDTH, DRAWER_MIN_WIDTH, DRAWER_TABS, DRAWER_TAB_KEY, DRAWER_WIDTH_KEY,
  clampDrawerWidth, drawerMaximumWidth,
  drawerTab, storedDrawerWidth, type DrawerTabId,
} from './drawerTabs'
import {
  DRAWER_LAYOUT_KEY, DRAWER_PROJECT_PRESENTATIONS_KEY, DRAWER_PROJECT_PRESENTATIONS_KEY_V2,
  activateDrawerTab,
  defaultDrawerLayout, drawerProjectPresentationFor, drawerStackForTab, drawerStacks, drawerTabs,
  isDefaultDrawerLayout,
  migrateDrawerProjectPresentations, migratedTabTarget, moveDrawerTabDirection, moveDrawerTabToSplit,
  moveDrawerTabToStack, normalizeDrawerLayout, normalizeDrawerProjectPresentation,
  parseDrawerLayout, pruneDrawerProjectPresentations, reconcileDrawerProjectPresentations,
  resetDrawerLayout, selectDrawerSegment, serializeDrawerLayout, serializeDrawerProjectPresentations,
  setDrawerProjectPresentation, setDrawerSplitRatio, updateDrawerProjectPresentation,
  type DrawerEdge, type DrawerLayout, type DrawerProjectPresentation,
  type DrawerProjectPresentationMap,
} from './drawerLayout'
import { DRAWER_SEGMENTS, RETIRED_DRAWER_SEGMENTS, resolveDrawerSegment } from './drawerSegments'
import {
  SIDEBAR_COLLAPSED_WIDTH, SIDEBAR_COLLAPSE_WIDTH, SIDEBAR_DEFAULT_WIDTH, SIDEBAR_MAX_WIDTH,
  SIDEBAR_MIN_WIDTH, SIDEBAR_REOPEN_WIDTH, SIDEBAR_RESIZER_WIDTH, clampSidebarWidth,
  dragCollapsedAtWidth, navigationSidebarCommandState,
} from './sidebarResize'
import { normalizeDrawerTabOrder } from './drawerTabOrder'
import {
  DRAWER_HIDDEN_KEY, canHideDrawerTab, defaultHiddenDrawerTabs, drawerTabVisible, parseHiddenDrawerTabs,
  type ExperienceTierChoice,
  serializeHiddenDrawerTabs, withDrawerTabHidden,
} from './drawerVisibility'
import {
  DRAWER_NOTE_KEY, claimDrawerNote, drawerNoteFor, isDrawerOwned, parseDrawerNotes,
  pruneDrawerNotes, serializeDrawerNotes, type DrawerNoteMap,
} from './drawerNotes'
import {
  presentationWithTransientDrawerTab, transientDrawerTabForProject, type TransientDrawerTab,
} from './drawerTransient'
import { resolveProjectScope, type ProjectScope } from './processFleet'
import { AlertsIcon, BroadcastIcon, CheckIcon, ClearIcon, ClipboardHistoryIcon, CloseIcon, CogIcon, CommandKeyIcon, CopyIcon, CopyPathIcon, DashboardIcon, DRAWER_TAB_ICONS, FilesIcon, GroupIcon, HelpIcon, HideIcon, HistoryIcon, MailIcon, NavPanelIcon, NotePencilIcon, PackageIcon, PlusIcon, PowerIcon, ProcessesIcon, PromptsIcon, QueueClockIcon, RefreshIcon, RenameIcon, ResumeIcon, RevealIcon, SearchIcon, ServerIcon, ShieldOffIcon, SidePanelIcon, SparkleIcon, SpeakerIcon, SpendIcon, TrashIcon, TrashSweepIcon, UnfoldLessIcon, UnfoldMoreIcon, WrenchIcon } from './railIcons'
import {
  CLIPBOARD_CHANGED_EVENT, clearClipboardHistory, configureClipboardCapture,
} from './clipboardHistory'
import { InteractionHud, showInteractionHud } from './InteractionHud'
import { RedeployChip } from './RedeployChip'
import {
  abandonRequest, applyProbe, confirmRedeploy, enterOutage, holderWarning, IDLE_REDEPLOY,
  interruptionSummary, loadRedeploy, markResultPending, outcomeIsFresh, outcomeNotice,
  REDEPLOY_POLL_MS, REDEPLOY_PROBE_TIMEOUT_MS, requestRedeploy, saveRedeploy, takeResultPending,
  waitsOnDaemon,
  type BundleHolder, type ProbeResult, type RedeployInterruptions, type RedeployState,
  type RedeployStatus,
} from './redeployProgress'
import { currentInsertTarget, insertIntoFocusedSurface, noteTerminalFocus, subscribeInsertTarget } from './insertTarget'
import type { InsertTarget } from './insertTarget'
import type { NotePlacement } from './NotesTab'
import { ProjectRunMenu } from './ProjectRunMenu'
import { AutomationDashboard, type AutomationView } from './AutomationDashboard'
import { useConversation, VoiceControl, VoiceDock } from './ConversationControl'
import {
  canCollapseVoiceDock, canExpandVoiceDock, effectiveVoicePanelMode, isVoicePanelMode,
  loadVoiceDock, reduceVoiceDock, saveVoiceDock, voiceAddressee, voiceBodyVariant,
  type VoiceDockEvent, type VoiceDockModel, type VoicePanelMode,
} from './voiceDock'
import { VoiceReadTab } from './VoiceReadTab'
import { resolveVoiceMode, voiceModeLabel } from './voiceMode'
import { AssistantPanel } from './AssistantPanel'
import {
  ASSISTANT_CLIENT_ID, assistantStatus, cancelAction, confirmAction, ensureDialog,
  latestOpenAction, NEW_CONVERSATION_PHRASES, NEW_CONVERSATION_REPLY,
  noteAssistantActionEvent, reportUiResult,
  sendTurn as sendAssistantTurnApi,
  spokenConfirmation, startNewDialog, type AssistantClientContext, type AssistantStatus,
} from './assistant'
import { resolveVoiceFuzzy } from './voiceFuzzy'
import { planUiCommand } from './uiCommand'
import { resolveConversationTarget } from './conversationTarget'
import type { VoiceSessionCandidate } from './conversationTarget'
import { autoplayEnabled, beginRequestedStream, cancelRequestedStream, closeRequestedStream, enqueueAutoplay, enqueueRequestedStreamClip, newVoiceStreamId, playAllHeldClips, playClipGroup, segmentPosition, setAutoplayEnabled, setPlaybackFocus, stopAllPlayback, stopSessionPlayback, unlockPlayback } from './voice'
import { speakOnce } from './assistantSpeech'
import { handleSessionSound, type NormalizedMuxEvent } from './sessionSounds'
import { mergeSessionSnapshot, reconcileSessionSnapshots } from './sessionSnapshots'
import {
  KILL_TOMBSTONE_TTL_MS, applyKillTombstones, clearableEndedSessions, expiredKillIds,
  killRemovedTheSession, nextActiveAfterKill, type KillTombstones,
} from './sessionKills'
import {
  forgetFocusedSession, recentFocusedSessions, recordFocusedSession, type SessionFocusHistory,
} from './sessionFocusHistory'
import {
  recordJoinFailure,
  type JoinAttempts,
} from './sessionJoin'
import {
  createFleetRefreshController, describeFleetFailures, fetchFleetSlices, type FleetRefreshController,
} from './fleetRefresh.ts'
import { planFleetLayouts, type PendingSpawn } from './fleetLayouts.ts'
import { createLayoutWriter } from './layoutWriter.ts'
import { currentProfile, hasSoftKeyboard, loadDrawerTabOrder, loadRailConfig, loadSettings, refreshSettings } from './deviceSettings'
import { initPush } from './push'
import { watchDevicePresence } from './devicePresence'
import type { ApprovalMode, DeliveryReadiness, Project, ProjectGroup, Session, LaunchProfile, VoiceClip, VoiceContent, VoiceMode, VoiceStatus } from './types'
import { isModifierOnly, keyChord } from './keys'
// Type-only: the component itself is a separate chunk, fetched below. Settings
// is 3,000 lines of form that the workspace cannot draw until it has parsed,
// and almost nobody opens it in the first seconds of a session.
import type { Settings as SettingsPanel } from './Settings'
import { HarnessSetup } from './HarnessSetup'
import { VoiceSetup } from './VoiceSetup'
import { QuestLog } from './QuestLog'
import { withQuestDismissed, type QuestId, type QuestSignals } from './questRegistry.ts'
import { ActionEditorModal } from './ActionEditorModal'
import { GuidedTutorial } from './GuidedTutorial'
import { completeTutorial, emitTutorialAction, firstRunSurface, mobileTutorialChrome, resetTutorial, shouldStartTutorial, type TutorialStepId } from './tutorial'
import { HelpModal } from './HelpModal'
import { HELP_TOPICS, helpCommandId, helpTopicForDrawer } from './helpTopics'
import { applyTheme, configureCustomTheme, type CustomTheme, type ThemeName } from './theme'
import { TRANSCRIPT_CHANGED_EVENT, TURN_ENDED_EVENT } from './transcriptView'
import { eventRequiresFleetRefresh } from './eventRefresh'
import { loadedUiBuildId, uiUpdateReloadReady, uiUpdateRequired } from './uiBuild'
import { applyNoteEditorConfig } from './noteEditorSettings'
import {
  DEFAULT_UI_SCALE, applyUiScale, createUiScaleWheelIntent, uiScaleConfigKey,
  uiScaleForIntent, uiScaleKeyboardIntent, watchUiScaleProfile, type UiScale,
} from './uiScale'
import { applyRailDensity, watchRailDensityProfile } from './railDensity'
import { DEFAULT_CLAUDE_MAX_COLUMNS, claudeMaxColumnsFrom } from './terminalViewport'
import { bindingFor, displayChord, paletteResults, paletteScope, PALETTE_PREFIXES, runCommand, type Command, type VoiceCommandResult } from './commands'
import { buildFleetCommands, displayOrderKey, type FleetCommandActions } from './fleetCommands.ts'
import { setKeybindingsStore } from './keybindingsStore.ts'
import type { ResolvedBindings, TrieOption } from './keymap.ts'
import {
  advance as advanceKeymap, cancel as cancelKeymap, installKeymap,
  options as keymapOptions, pendingChords as dispatchPending, terminalSelection,
} from './keymapDispatch.ts'
import { hostProfile, hostQuery } from './hostProfile.ts'
import { keyboardLockEnabled } from './KeyboardProbe.tsx'
import { WhichKey } from './WhichKey.tsx'
import { resolveRailVoiceEntries, type RailVoiceEntry } from './railVoice.ts'
import { insertIntoTerminal, insertionRefusal, requestTerminalAction } from './terminalActions.ts'
import { normalizeSpokenText, numberedCandidates, resolveVoiceIntent, selectNumberedCandidate, type VoiceIntentCandidate } from './voiceIntents'
import { buildFleetReadModel, fleetRundown, fleetRundownDetail, type FleetSession } from './fleetStatus'
import {
  parseVoiceQuery, projectListPage, sessionListPage, spokenSessionStatus, voiceHelpPage,
  voiceSessionFilterMatches, type VoiceQuery, type VoiceSessionFilter, type VoiceScope,
} from './voiceQueries'
import { adjacentVoiceSession, buildVoiceNavigationIndex, projectAtVoiceNumber, sessionAtVoiceNumber } from './voiceNavigation'
import {
  clearSpokenListContext, loadSpokenListContext, saveSpokenListContext,
  SPOKEN_LIST_TTL_MS, type SpokenListContext,
} from './spokenListContext'
import { copyPreparedText } from './terminalClipboard'
import { absoluteProjectPath, FILE_COPY_MAX_LINES, truncateForClipboard } from './fileClipboard'
import { clampContextMenuLeft, fitMenuInViewport } from './menuPosition'
import { defaultMobileInputSettings, mobileInputSettings, type MobileInputSettings } from './mobileInput'
import { adjacentMobileTab, mobileWorkspaceProjection } from './mobileWorkspace'
import { RESERVE_INTENT_WINDOW_MS, reservedKeyboardPx } from './keyboardReserve'
import { SOFT_KEYBOARD_EVENT, deepActiveElement, dismissSoftKeyboard, lastSoftKeyboardInset, raisesSoftKeyboard, rememberSoftKeyboardInset, softKeyboardHolder, softKeyboardInset, softKeyboardVisualOffset } from './mobileKeyboard'
import { MOBILE_TERMINAL_DRAFT_EVENT, mobileTerminalDraftStore } from './mobileTerminalDraft'
import { classifyGesture, classifyRailGesture, classifyRegionGesture, defaultMobileGestureSettings, gestureOverlayDepth, isHorizontalDirection, mobileGestureSettings, overlayBackEnabled, pathOwnsHorizontalScroll, pathShadowsGesture, regionForPath, resolveGestureCommand, surfaceGestureFor, surfaceGesturesEnabled, swipeAwayCloseEnabled, type GestureRegion, type MobileGestureSettings, type SurfaceGesture } from './mobileGestures'
import { SETTINGS_NAV_CLOSE, SETTINGS_NAV_TOGGLE } from './settingsTabs'
import { dismissStack } from './dismissStack.ts'
import { useDismissLevel } from './modalFocus'
import { installSystemBack } from './systemBack.ts'
import { composeBackTarget, viewBackEnabled, viewHistory, type ViewEntry, type ViewNavigator, type ViewPosition } from './viewHistory.ts'
import { focusMemoryWith, parseFocusMemory, parseViewPreference, reconcileFocusView, rememberedView, resolveActiveSession, resolveInitialFocus, viewUrl } from './viewState'
import {
  DROP_LIST_MARGIN, edgeAutoScrollDelta, listDropTargetForPoint, MOBILE_HOLD_DRAG, MOBILE_HOLD_MOVE_DRAG, POINTER_MOVE_DRAG, pointerDragMoveDecision,
  reorderForHover, reorderTargetFromContainer, type DropSide, type ListDropTarget, type PointerDragActivation, type ReorderAxis, type ReorderTarget,
} from './dragReorder'
import { claimPointerDrag, markPointerDragClaims, pointerDragOwnsPointer } from './pointerDragClaim'
import { relativeStackTab } from './workspaceTabs'
import {
  COLLAPSED_PROJECTS_KEY, canHideProject, describeOpenWork, loadCollapsedProjects,
  projectInitials, projectOpenWork, serializeCollapsedProjects, setAllCollapsed,
  sidebarProjectsView, toggleCollapsed,
} from './sidebarProjects'
import {
  NO_SEARCH_CURSOR, SIDEBAR_SEARCH_DEBOUNCE_MS, SIDEBAR_SEARCH_IDLE_TICK_MS,
  buildSidebarSearchIndex, clampSearchCursor, moveSearchCursor, sameSearchRow,
  sidebarSearchExpired, sidebarTreeFilter, type SidebarSearchCandidate,
} from './sidebarSearch'
import {
  PROJECT_SORT_OPTIONS, SIDEBAR_ORDER_KEY,
  isBucketCollapsed, loadSidebarOrder, mergeVisibleOrder,
  projectRecency, projectSortLabel, pruneSidebarOrder, serializeSidebarOrder,
  setAllBucketsCollapsed, setProjectSortMode, sidebarRootRows, sortProjects,
  sortRootEntries, toggleBucketCollapsed,
} from './projectSort'
import { PROJECT_RECENCY_EVENT, type ProjectRecencyEventDetail, type ProjectUseReason } from './projectRecency'
import { placePendingTerminal, selectPendingTerminal, type PendingSpawnPlacement } from './pendingSession'
import { pendingAcks, pruneAcks, trackPinVisits, isUnread, projectRailStatus, projectSetRailStatus, type AckMap, type PinVisits, type ProjectRailActivity } from './sessionAttention'
import { isHumanPresent, watchHumanPresence } from './humanPresence'
import { ApprovalChip } from './ApprovalChip'
import { effectiveApprovalMode } from './approvals'
import { activityBadges, sessionFaults, sessionStatus } from './sessionStatus'
import { StateIndicator } from './StateIndicator'
import { SessionRowLive } from './SessionRowLive'
import { isFieldPlaced, type DotShape, type StandingRender } from './sessionRowConfig'
import {
  applySessionDotSize, useRowBudget, useSessionRowConfig, watchSessionDotProfile,
} from './sessionRowPrefs'
import { serverNow } from './serverClock.ts'
import {
  deriveRowFleetFacts, sessionContextArc,
  sessionStandingMark, type ContextGauge,
} from './sessionRowFields'
import {
  browserUuid, emptyLayout, leaves, noteResourceId, paneStack, parseLayout, parseNoteResourceId, resourceLeaf, worktreeFileResourceId,
  removeLeaf, replaceTerminal, resizeTargetFor, setSplitRatio, swapPanes,
  activateContainingStack, activateStackChild, addLeafToStack, changeMapLeafId, changeMapLeafSessionId, dissolveStack, groupTerminalsInStack, moveLeafToSplit, moveLeafToStack, moveTerminalBeside, openAnchorId, openTab, paneNeighborIds, paneStacks, queueLeafId, queueLeafSessionId, reorderStack, resolveLayout, spawnAnchorId, splitTerminal, splitView, stackForView, stackTerminal, terminalIds, terminalLeaf, visibleTerminalIds, type PaneLayout,
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

/** `sessionStorage` when it is usable, else null. Accessing it throws outright
 *  under some privacy settings and in a sandboxed frame, and this backs the
 *  redeploy sentinel, which must never be the reason the app fails to boot. */
function sessionStorageOrNull(): Storage | null {
  try { return window.sessionStorage } catch { return null }
}

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

// The read-aloud mark, for the surfaces that are not the sidebar row.
//
// The tab strip picks its marks by hand rather than running the row's token engine, so
// this exists as a second renderer of one fact - and is gated on the same configured
// field, because a strip printing what the row beside it is configured not to print is
// the same fact twice from two different rulebooks. Nothing is drawn for `off`: a mark
// on every session says nothing, and read aloud is off for a fleet by default.
const voiceGlyph=(session:Session|undefined,mode:VoiceMode)=>{
  if(!session||session.pending||mode==='off')return null
  return <span class={`tab-voice-glyph ${mode}`} role="img"
    aria-label={`Read aloud: ${voiceModeLabel(mode)}`}
    title={mode==='auto'
      ?'Read aloud · every completed reply becomes audio automatically'
      :'Read aloud · on demand: audio is made when you ask for it'}><SpeakerIcon/></span>
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
  return <span class={`tab-session-glyph agent-prefix ${session.backend}`} title={harnessDisplayName(session.backend)}>{harnessMark(session.backend)}</span>
}

// The one state indicator every surface draws. Shape (and any gauge wrapped
// around it) comes from the sidebar row configuration, so a hexagon in the
// sidebar is a hexagon in the tab strip and the context menu too.
const sessionStateDot=(
  session:Session|undefined,
  shape:DotShape,
  gauge?:ContextGauge|null,
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
type HandoffState={entry:HistoryEntry;markdown:string;message:string}
type ContextState = { session: Session; x: number; y: number; source: 'sidebar'|'tab'|'pane'|'mobile' } | null
type ProjectContext = { project: Project; x: number; y: number } | null
type SidebarContext = { x:number;y:number } | null
type NoteContext = { resourceId:string;projectId:string;x:number;y:number } | null
/** Right-click on a *static* preview's sidebar row. Deliberately not offered on the
 *  session-owned preview rows: those follow their listener and are retired by it stopping,
 *  so there is nothing on them for a menu to do. */
type StaticPreviewContext = { previewId:string;projectId:string;label:string;x:number;y:number } | null
type TabContext = { leaf:PaneLeaf;label:string;projectId:string;x:number;y:number;source:'tab'|'mobile' } | null
type RenameTarget = { kind: 'session'; session: Session } | { kind: 'project'; project: Project }
/** What the create dialog needs from `GET /api/grants`: the named starting sets its
 *  checkboxes apply, and the provider verdict the model-backed one discloses. */
type GrantsCatalogue={
  project_starting_sets:StartingSetCatalog
  llm:{ready:boolean;reason:string}
  /** The registry with each entry's resolved install-wide ceiling, so the
   *  creation form can grey a set the daemon would refuse to grant. */
  automations?:{id:string;globally_allowed?:boolean}[]
}
type NoteTarget={projectId:string;kind:'note'|'global-note'|'file'|'worktree-file';resourceId:string;worktree?:string}
type StartupMilestone = 'pane_mounted' | 'socket_open' | 'replay_ready'
type ClientStartupTiming = Partial<Record<'api_response' | StartupMilestone, number>>
type RunMenuState={project:Project;x:number;y:number;trigger?:string}
type WorktreeSetupResult={status:'not_configured'|'succeeded'|'failed'|'timed_out'|'error';error?:string;exit_code?:number|null}
type WorktreeSpawnResult={status:'not_requested'|'spawned'|'error';session_id?:string;session?:Session;error?:string;setup?:WorktreeSetupResult}

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

function historyName(entry:HistoryEntry):string {
  return runDisplayName(entry)
}

export function App() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  // False until the first projects read settles. `projects` starts `[]`, which is
  // indistinguishable from "loaded, and this install genuinely has none" - and
  // the sidebar drew the second meaning for the first second of every load,
  // telling an operator with fourteen Projects to "Create your first Project".
  // The same rule the Agent Environment catalog carries: an empty list must say
  // which kind of empty it is.
  const [workspaceLoaded, setWorkspaceLoaded] = useState(false)
  // Bumped whenever a fleet refresh installs a harness registry: the accessors that
  // read it (`allBackendNames`, `harnessDisplayName`) are module functions over a
  // global, so this counter is what tells memoized work that their answers changed.
  const [harnessRegistryRevision, setHarnessRegistryRevision] = useState(0)
  // The current render's fleet-command handlers, and the never-changing facade the
  // memoized registry holds instead of them.
  const fleetCommandWork = useRef<FleetCommandActions>({
    activateProject: () => {}, focusProject: () => {}, focusSession: () => {}, spawnSession: async () => false,
  })
  const fleetCommandActions = useMemo<FleetCommandActions>(() => ({
    activateProject: projectId => fleetCommandWork.current.activateProject(projectId),
    focusProject: projectId => fleetCommandWork.current.focusProject(projectId),
    focusSession: session => fleetCommandWork.current.focusSession(session),
    spawnSession: (projectId, backend, seedText) => fleetCommandWork.current.spawnSession(projectId, backend, seedText),
  }), [])
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
  // Three-valued, not a boolean: see redeployProgress.ts. The multi-minute build
  // stage keeps the whole app usable and only shows the corner chip; the overlay
  // and the error suppression belong to the daemon-down stage alone. Restored
  // from sessionStorage so a reload - or a tab opened after the redeploy started
  // - comes up knowing, instead of rendering a broken app.
  const [redeploy, setRedeploy] = useState<RedeployState>(() => loadRedeploy(sessionStorageOrNull(), Date.now()))
  const redeployDown = redeploy.phase === 'down'
  // Every request is expected to fail while the daemon is away, so the toast
  // that reports them carries no information and buries the overlay that does.
  // Reload has the same window and the same excuse.
  const suppressTransientErrors = redeployDown || daemonReloading
  // Read through a ref: the refresh loop and the timers that drive it are
  // installed once, so a value captured from the render that created them would
  // still say "false" for the whole outage.
  const suppressErrorsRef = useRef(suppressTransientErrors)
  suppressErrorsRef.current = suppressTransientErrors
  const [redeployNotice, setRedeployNotice] = useState('')
  // What the confirm dialog reports will go dark. Advisory: nothing here can
  // refuse a redeploy, because a port being open is not a reason it would fail.
  const [redeployInterruptions, setRedeployInterruptions] = useState<RedeployInterruptions | null>(null)
  // Who would refuse the redeploy, scanned while the dialog is being read. Null
  // until the scan lands (it takes seconds), which is why it is a second request
  // rather than a field on the one that fills in the interruptions line.
  const [redeployHolders, setRedeployHolders] = useState<BundleHolder[] | null>(null)
  const loadedBuildId = useRef(loadedUiBuildId())
  const [uiUpdateAvailable, setUiUpdateAvailable] = useState(false)
  const [redeployConfirmOpen, setRedeployConfirmOpen] = useState(false)
  // '' browses every Project; a Project id prefilters the archive to it.
  const [historyScope,setHistoryScope]=useState('')
  //: One conversation to open History straight into, when a surface named a specific
  //: session rather than asking to browse.
  const [historyEntry,setHistoryEntry]=useState('')
  const [historyOpen,setHistoryOpen]=useState(false)
  const [processScope,setProcessScope]=useState<string|null>(null)
  // The drawer tab's scope: '' is every Project, anything else is that Project. Kept here (not
  // in the tab) so switching tabs and coming back does not silently reset what you were
  // watching. Project-scoped by default, like every other Project-scoped tab — a session-scoped
  // processes view would churn its whole body on each focus change and read empty most of the
  // time, since most sessions are just their agent CLI and a conhost.
  // `null` is "the tab has not been scoped", which resolves to the Project the drawer is
  // sitting beside; `''` is the user having asked for every Project. Collapsing the two made
  // `All projects` unselectable — it snapped straight back to the active Project.
  const [processProjectScope,setProcessProjectScope]=useState<ProjectScope|null>(null)
  // The Schedule tab's scope, kept here for the same reason: '' is every Project's
  // schedules ("what fires tonight"), anything else is one Project's.
  const [scheduleScope,setScheduleScope]=useState<string>('')
  // A resume schedule seeded from the conversation it reopens. It lives here rather
  // than in the Schedule tab because the two places that create one - a History row
  // and a pane's own menu - are the two places that know which conversation is meant,
  // and the tab has no way to find one.
  const [scheduleSeed,setScheduleSeed]=useState<ScheduleDraft|null>(null)
  // Which Project's templates join the global ones. Unlike the other surfaces
  // this is additive rather than restrictive, so the app menu still passes the
  // active Project: opening "unscoped" would remove templates, not filters.
  const [promptScope,setPromptScope]=useState<Project|null>(null)
  const [promptTargetId,setPromptTargetId]=useState<string|null>(null)
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
  // Sidebar row appearance. The derived facts answer "differs from the project
  // default" once per snapshot instead of once per row. The ageing clock is
  // deliberately NOT read here: it lives in `SessionRowLive`, so a five-second tick
  // re-renders the rows that age rather than the whole shell around them.
  const rowConfig=useSessionRowConfig()
  const rowQueueDepth=useMemo(
    ()=>Object.fromEntries(Object.entries(queueSummary).map(([id,target])=>[id,target.pending])),
    [queueSummary],
  )
  // The width ladder is measured, not queried in CSS: hiding a token with
  // `display:none` leaves the separator JSX already emitted beside it. What is
  // measured is the `.row-metric` probe below, a stand-in for one row's text
  // column — not this element, whose width overstates the room a row has by the
  // indicator gutter, the tree's padding, and the scrollbar.
  const sidebarRef=useRef<HTMLElement>(null)
  const rowMetricRef=useRef<HTMLDivElement>(null)
  const rowBudget=useRowBudget(rowMetricRef)
  // Device-local drafts are unioned into the row context rather than read at the
  // row: the daemon's ledger sees text typed from any client but not text staged
  // in this browser's own draft composer, which never reaches the PTY. Neither
  // source is a superset of the other.
  const localDrafts=useMemo(()=>mobileTerminalDraftStore.stamps(),[mobileDraftRevision])
  // Declared here rather than beside the other voice state: the sidebar row's token
  // context is built at the top of this component and reads the master switch, and a
  // value a hook depends on cannot be declared after it.
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus | null>(null)
  // Only the two facts a session's stored `voice_mode` resolves against, and never
  // the whole `VoiceStatus`: that object carries spend, cache size and engine
  // diagnostics that move on their own, and memoising the fleet's row tokens on it
  // would rebuild every row each time a clip was synthesized.
  const rowVoice=useMemo(
    ()=>({enabled:!!voiceStatus?.enabled,default_mode:voiceStatus?.default_mode||'off'}),
    [voiceStatus?.enabled,voiceStatus?.default_mode],
  )
  const rowFacts=useMemo(
    ()=>deriveRowFleetFacts(sessions,rowQueueDepth,rowBudget,localDrafts,rowVoice),
    [sessions,rowQueueDepth,rowBudget,localDrafts,rowVoice],
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
  // Which hand-set unread marks are still owned by the visit that set them, so a
  // return to the pane reads it rather than the mark outliving the reason for it.
  const [pinVisits,setPinVisits]=useState<PinVisits>({})
  const [noteMenu,setNoteMenu]=useState<NoteContext>(null)
  const [staticPreviewMenu,setStaticPreviewMenu]=useState<StaticPreviewContext>(null)
  const [tabMenu,setTabMenu]=useState<TabContext>(null)
  const [emptyMenu, setEmptyMenu] = useState<{x:number;y:number} | null>(null)
  const [drawerDisplayMenu,setDrawerDisplayMenu]=useState<{x:number;y:number;surface:'tabs'|'rail';tab?:DrawerTabId}|null>(null)
  const [zoomedId, setZoomedId] = useState<string | null>(null)
  // The resolved map for THIS host, as the daemon computed it. The seed is the two
  // chords worth having before the first fetch lands; it is deliberately not a copy
  // of the default preset, because a second copy of the keymap in the browser is
  // exactly the drift the daemon-side resolution exists to prevent.
  const [keymap, setKeymap] = useState<ResolvedBindings>({
    'ctrl+shift+p': [{ command: 'palette.open', when: '' }],
    'f1': [{ command: 'palette.open', when: '' }],
  })
  // What the which-key overlay draws. The sequence state itself lives in
  // `keymapDispatch`, because the terminal has to ask about it before this
  // component's handler ever runs.
  const [pendingChords, setPendingChords] = useState<string[]>([])
  const [pendingOptions, setPendingOptions] = useState<TrieOption[]>([])
  const host = hostProfile()
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
  // The grants catalogue, read when the create dialog opens: the named starting sets
  // its checkboxes apply, and the provider verdict the model-backed one discloses.
  const [grantsCatalogue,setGrantsCatalogue]=useState<GrantsCatalogue|null>(null)
  // A starting set with any member under the install-wide ceiling is offered
  // greyed rather than granted-and-refused: POST /api/grants answers such a
  // request with `automation_globally_disabled`, and the form knowing that
  // before the press is the whole point of the catalogue carrying the ceiling.
  const startingSetBlocked=(setName:keyof StartingSetCatalog):boolean=>{
    const set=grantsCatalogue?.project_starting_sets?.[setName]
    if(!set||!grantsCatalogue?.automations)return false
    const ceiling=new Map(grantsCatalogue.automations.map(item=>[item.id,item.globally_allowed!==false]))
    return set.automations.some(id=>ceiling.get(id)===false)
  }
  const [projectsManagerOpen,setProjectsManagerOpen]=useState(false)
  // Which Project the registry should land on, and whether on its record or its
  // settings. Projects is the only per-Project editor, so every "project settings"
  // entry point is a preselection of it rather than a second surface.
  const [projectsManagerFocus,setProjectsManagerFocus]=useState<{projectId:string;setting?:string}|null>(null)
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
  // `adoptProjectId` is set only when the dialog was opened from a Project's Group submenu:
  // the new Group takes that Project with it, which is the whole reason the row is there.
  const [groupEdit,setGroupEdit]=useState<{id?:string;name:string;adoptProjectId?:string}|null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  // Settings' own section drawer, the narrow-layout twin of the docked column. It lives
  // here rather than inside Settings because the gesture recognizer below is the shell's,
  // and it has to be able to work that drawer the way it works the workspace sidebar.
  const [settingsNavOpen, setSettingsNavOpen] = useState(false)
  const [harnessSetupNeeded, setHarnessSetupNeeded] = useState(false)
  const [experienceTierUnchosen, setExperienceTierUnchosen] = useState(false)
  const [voiceSetupOpen, setVoiceSetupOpen] = useState(false)
  const [questSignals, setQuestSignals] = useState<QuestSignals>({})
  // The sidebar's "add a provider account" invitation, which the status block shows
  // only while no provider has a credential on this host. Machine-side like the
  // quest dismissals above and for the same reason: it invites you to add a login
  // to the daemon host, so putting it away at the desk must also put it away on
  // the phone. Undefined until `/api/config` settles, which is what keeps the
  // invitation from flashing on an install that has already dismissed it.
  const [accountPromptDismissed, setAccountPromptDismissed] = useState<boolean | undefined>(undefined)
  const questAction = (id: QuestId): void => {
    if (id === 'voice') setVoiceSetupOpen(true)
    else if (id === 'worktrees') showDrawerTab('git')
    else openSettings('Remote')
  }
  // Optimistic and machine-side: the dismissal writes through so it never
  // resurfaces on another device, and a lost write costs one reappearance
  // rather than a phantom quest.
  const dismissQuest = (id: QuestId): void => {
    const next = withQuestDismissed(questSignals.quests_dismissed, id)
    setQuestSignals(current => ({ ...current, quests_dismissed: next }))
    void api('PATCH', '/api/config', { quests_dismissed: next }).catch(() => {})
  }
  // Optimistic and machine-side, exactly like the quest dismissal above. There is
  // deliberately no control to bring it back: it answers itself the moment a
  // credential exists, because the status block is derived rather than remembered.
  const dismissAccountPrompt = (): void => {
    setAccountPromptDismissed(true)
    void api('PATCH', '/api/config', { provider_accounts_prompt_dismissed: true }).catch(() => {})
  }
  // False until the first `/api/config` call settles, either way. Whether the first-run
  // harness panel is going to lead is a fact only the daemon holds, and it arrives after
  // the first paint - so the tour waits for it rather than painting a card that a dialog
  // is about to cover. See the sequencing note at the tutorial's render site.
  const [firstRunResolved, setFirstRunResolved] = useState(false)
  // The Settings panel, as a chunk rather than as part of the workspace bundle.
  //
  // Splitting it alone would trade a faster first paint for a slower first
  // *open*, which is a bad trade for a control people press deliberately. So it
  // is also prefetched once the workspace is up and the browser is idle: by the
  // time anyone reaches for Settings the chunk is almost always already there,
  // and the `settingsOpen` effect below is the backstop for when it is not.
  const [SettingsView, setSettingsView] = useState<typeof SettingsPanel | null>(null)
  const settingsChunk = useRef<Promise<void> | null>(null)
  const loadSettingsChunk = useCallback(() => {
    settingsChunk.current ??= import('./Settings')
      .then(module => { setSettingsView(() => module.Settings) })
      // Cleared so a failed fetch (an offline moment, a redeployed bundle) is
      // retried on the next press rather than wedging the panel shut forever.
      .catch(() => { settingsChunk.current = null })
    return settingsChunk.current
  }, [])
  useEffect(() => {
    if (!workspaceLoaded || SettingsView) return
    const idle = window.requestIdleCallback
    // Safari has no `requestIdleCallback`; a timeout is the same intent, just
    // without the browser's own notion of "idle".
    if (!idle) { const timer = window.setTimeout(() => void loadSettingsChunk(), 1500); return () => window.clearTimeout(timer) }
    const handle = idle(() => void loadSettingsChunk(), { timeout: 4000 })
    return () => window.cancelIdleCallback?.(handle)
  }, [workspaceLoaded, SettingsView, loadSettingsChunk])
  const [actionEditorOpen, setActionEditorOpen] = useState(false)
  // The section a caller asked Settings to land on, or undefined for "wherever the
  // user left off" - Settings remembers its own last tab, so an unqualified open must
  // stay unqualified rather than assert General.
  const [settingsSection, setSettingsSection] = useState<string|undefined>(undefined)
  // The exact control a deep link asked for (`settingTargets.ts`), and a counter that changes
  // on every request. The counter is what makes the same link work twice: the surface it
  // opens is often already open on the right tab, where nothing about the props would
  // otherwise differ, and "nothing happened" is exactly the failure these links exist to fix.
  const [settingsSetting, setSettingsSetting] = useState<string|undefined>(undefined)
  const [revealToken, setRevealToken] = useState(0)
  // Which MenuGroup is expanded in the app menu; null collapses every group.
  const [menuGroup,setMenuGroup]=useState<string|null>(null)
  const [tutorialOpen,setTutorialOpen]=useState(()=>shouldStartTutorial())
  // The help surface. `null` is closed; `''` opens the index; a topic id opens that topic.
  // One piece of state rather than an open flag beside a selection, so "open help" and
  // "open help about the scan timeline" cannot disagree about whether it is up.
  const [helpTopicOpen,setHelpTopicOpen]=useState<string|null>(null)
  const [processSession, setProcessSession] = useState<Session | null>(null)
  const [processFleet,setProcessFleet]=useState<FleetSnapshot|null>(null)
  const [previews, setPreviews] = useState<Record<string, Preview>>({})
  const [notificationData, setNotificationData] = useState<NotificationData>({notifications:[],deliveries:[]})
  const [notificationUnread, setNotificationUnread] = useState(0)
  const [notificationToast, setNotificationToast] = useState<UiNotification | null>(null)
  // Quick sidebar bell mirrors this device profile's shared alert master switch.
  // Sound and push channel choices stay intact while the master is muted. Kept in sync
  // through the `mux:settings-changed` event the device-settings cache emits.
  const [alertsEnabled, setAlertsEnabled] = useState(() => alertPreferences().enabled)
  // What the configurator launcher can offer. Null until the first answer (or
  // after a failed one), which `launchState` renders as an enabled button with a
  // neutral label rather than a disabled one - a control greyed out because a
  // status request is in flight reads as broken, and the daemon refuses cleanly
  // on the press anyway.
  const [configuratorOptions,setConfiguratorOptions]=useState<ConfiguratorOptions|null>(null)
  // Anchor for the harness chooser, opened by the modifier press rather than by
  // the plain one: a single press launching the default is the whole point of
  // the control.
  const [configuratorMenu,setConfiguratorMenu]=useState<{x:number;y:number}|null>(null)
  // A failed read leaves `null` rather than a stale answer: `launchState` treats
  // "not known" as pressable, so the button keeps working through an outage and
  // the daemon is what refuses if it genuinely cannot launch.
  const loadConfiguratorOptions=()=>void fetchConfiguratorOptions()
    .then(setConfiguratorOptions).catch(()=>setConfiguratorOptions(null))
  const configuratorLaunch=useMemo(()=>launchState(configuratorOptions),[configuratorOptions])
  const [railVoiceRevision,setRailVoiceRevision]=useState(0)
  // Processes, bandwidth, storage, and fleet activity are one dialog (`ResourcesModal`).
  // `null` is closed; the value is the segment it opens on, so every entry point that
  // named a resource still lands on that resource.
  const [resourcesOpen,setResourcesOpen]=useState<ResourceSegment|null>(null)
  // Spend is its own dialog (`UsageModal`), not a segment of the one above. Same
  // null-is-closed shape, because the same deep-link commands point into it.
  const [usageOpen,setUsageOpen]=useState<UsageSegment|null>(null)
  // The fleet queue overlay, and the Project it opens filtered to (the Project menu scopes
  // it to its own row; everywhere else opens it unfiltered). `null` is closed.
  const [fleetQueue, setFleetQueue] = useState<{ projectId: string } | null>(null)
  const [automationOpen,setAutomationOpen]=useState<{view:AutomationView;projectId?:string;setting?:string;revealToken:number}|null>(null)
  const openAutomation=(view:AutomationView,projectId?:string,setting?:string)=>setAutomationOpen({view,projectId,setting,revealToken:Date.now()})
  const [projectGroups,setProjectGroups]=useState<ProjectGroup[]>([])
  // False until the first `/api/project-groups` response lands. Nothing may prune
  // device-local sidebar state against the empty mount-time arrays: they mean "not
  // fetched yet", not "the user deleted everything".
  const [registryLoaded,setRegistryLoaded]=useState(false)
  const dragSessionTargetRef=useRef<ListDropTarget|null>(null)
  // `groupId` is the Group the drop would move the Project into (null = the ungrouped
  // root), which is why a Project drag is not purely a reorder: the same gesture can
  // cross a section boundary, and the drop has to carry where it landed as well as when.
  type ProjectDrag={id:string;previewIds:string[];groupId:string|null;overId:string|null;side:DropSide|null}
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
  // The sidebar's typed filter. `sidebarSearchInput` is what the keyboard is holding
  // and `sidebarSearchQuery` is what the list is ranked by; they differ for one debounce
  // interval, which is what keeps a fast typist from re-ranking on every keystroke.
  // `sidebarSearchTouchedAt` is a ref rather than state on purpose: the idle timer is
  // fed by pointer movement over the results, and doing that through state would
  // re-render the sidebar on every mouse move.
  const [sidebarSearchOpen,setSidebarSearchOpen]=useState(false)
  const [sidebarSearchInput,setSidebarSearchInput]=useState('')
  const [sidebarSearchQuery,setSidebarSearchQuery]=useState('')
  const [sidebarSearchCursor,setSidebarSearchCursor]=useState(0)
  const sidebarSearchRef=useRef<HTMLInputElement|null>(null)
  const sidebarSearchTouchedAt=useRef(0)
  const [sortMenu,setSortMenu]=useState<{x:number;y:number}|null>(null)
  // Held as a Group id rather than the record, so a rename landing while the menu is open
  // redraws it with the new name instead of the one it was opened on.
  const [groupMenu,setGroupMenu]=useState<{groupId:string;x:number;y:number}|null>(null)
  // Two-step delete: the menu asks, and only the second click sends. A Group's Projects
  // survive it, but the Group itself does not, so it does not go on a bare menu row.
  const [confirmGroupDeleteId,setConfirmGroupDeleteId]=useState<string|null>(null)
  const [dragStackTab,setDragStackTabState]=useState<StackTabDrag|null>(null)
  const dragStackTabRef=useRef<StackTabDrag|null>(null)
  const suppressDragClickRef=useRef<string|null>(null)
  const pointerDropIndicatorRef=useRef<HTMLElement|null>(null)
  const activePointerDragCancelRef=useRef<(()=>void)|null>(null)
  const setDragProject=(next:ProjectDrag|null)=>{dragProjectRef.current=next;setDragProjectState(next)}
  const setDragStackTab=(next:StackTabDrag|null)=>{dragStackTabRef.current=next;setDragStackTabState(next)}
  const previewDragStackTab=(next:StackTabDrag)=>{dragStackTabRef.current=next}
  const [promptLibraryOpen,setPromptLibraryOpen]=useState(false)
  // Whether this opening of the library lands on a blank template. It is a property
  // of the *opening*, not of the panel, so it is cleared on close rather than read
  // once — otherwise a later "Prompt library" would still arrive in create mode.
  const [promptLibraryCreating,setPromptLibraryCreating]=useState(false)
  // The session whose branch point is being chosen. Held by id rather than by object
  // so a session update under the dialog cannot leave it rendering a stale snapshot,
  // and so the dialog closes by itself if that session disappears.
  const [branchPickerId,setBranchPickerId]=useState<string|null>(null)
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
      localStorage.getItem(DRAWER_PROJECT_PRESENTATIONS_KEY_V2),
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
  // Which tabs the user has put away. Global and device-local, exactly like the
  // arrangement above it: visibility is arrangement, and the layout it filters is
  // read synchronously at boot so no tab is drawn and then taken away again.
  const [hiddenDrawerTabs,setHiddenDrawerTabs]=useState<DrawerTabId[]>(
    ()=>parseHiddenDrawerTabs(localStorage.getItem(DRAWER_HIDDEN_KEY)))
  const drawerLauncherTabs=useMemo(()=>drawerTabs(drawerLayout).map(drawerTab),[drawerLayout])
  const [clipboardEnabled,setClipboardEnabled]=useState(true)
  const [scratchpadEnabled,setScratchpadEnabled]=useState(true)
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
  const [surfaceGestures, setSurfaceGestures] = useState(true)
  const [viewBack, setViewBack] = useState(true)
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
  const setDrawerTabHidden=(tab:DrawerTabId,hidden:boolean)=>{
    setHiddenDrawerTabs(current=>{
      const next=withDrawerTabHidden(current,tab,hidden)
      storeDrawerValue(DRAWER_HIDDEN_KEY,serializeHiddenDrawerTabs(next))
      return next
    })
    setDrawerAnnouncement(`${drawerTab(tab).label} ${hidden?'hidden':'shown'}`)
  }
  const showAllDrawerTabs=()=>{
    setHiddenDrawerTabs(()=>{
      storeDrawerValue(DRAWER_HIDDEN_KEY,serializeHiddenDrawerTabs([]))
      return []
    })
    setDrawerAnnouncement('All side panels shown')
  }
  const selectDrawerTab=(tab:DrawerTabId,targetProject=projectId,segment?:string)=>{
    // A hidden tab reached by name — the palette, a voice command, a menu row that says
    // "Notes…" — is peeked for as long as it stays selected rather than unhidden. The
    // request was to see it now, not to change what the rail carries from here on.
    setTransientDrawer(hiddenDrawerTabs.includes(tab)?{projectId:targetProject,tab}:null)
    updateDrawerPresentation(targetProject,current=>activateDrawerTab(current,drawerLayoutRef.current,tab,segment))
  }
  /** Remember a segment choice made inside the drawer, without moving the selection. */
  const selectDrawerTabSegment=(tab:DrawerTabId,segment:string,targetProject=projectId)=>{
    updateDrawerPresentation(targetProject,current=>selectDrawerSegment(current,drawerLayoutRef.current,tab,segment))
  }
  /**
   * Ask the drawer to scroll a *section* into view and flash it.
   *
   * Sections are co-visible regions rather than modes, so "go to Clipboard" cannot be a
   * selection — it is an arrival, and the token is what distinguishes a second request for
   * the same section from no request at all.
   */
  const [drawerSectionReveal,setDrawerSectionReveal]=useState<{tab:DrawerTabId;section:string;token:number}|null>(null)
  const drawerSectionSequence=useRef(0)
  const revealDrawerSection=(tab:DrawerTabId,section:string,targetProject=projectId)=>{
    openDrawerTab(tab,targetProject)
    setDrawerSectionReveal({tab,section,token:++drawerSectionSequence.current})
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
  const openDrawerTab=(tab:DrawerTabId,targetProject=projectId,segment?:string)=>{
    selectDrawerTab(tab,targetProject,segment)
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
        // The retirement table lives in exactly one place (`migratedTabTarget`). This
        // legacy `mux.drawer.tab.v1` seed used to re-spell it inline and drifted every
        // time a tab was folded into another, so it now calls the same helper — segment
        // and all, which is what puts an old `changemap` seed on Activity → Changes.
        const legacy=migratedTabTarget(legacyDrawerTab.current)
        if(legacy)updated=setDrawerProjectPresentation(current,projectId,activateDrawerTab(base,drawerLayoutRef.current,legacy.tab,legacy.segment),drawerLayoutRef.current)
      }
      try{
        localStorage.setItem(DRAWER_LAYOUT_KEY,serializeDrawerLayout(drawerLayoutRef.current))
        localStorage.setItem(DRAWER_PROJECT_PRESENTATIONS_KEY,serializeDrawerProjectPresentations(updated,drawerLayoutRef.current))
        localStorage.removeItem(DRAWER_PROJECT_STATE_KEY)
        localStorage.removeItem(DRAWER_TAB_KEY)
        // v2 is dropped last, for the same reason the other two are: the v3 value is
        // already written above, so an interruption anywhere before this line leaves the
        // older record intact and the migration simply runs again.
        localStorage.removeItem(DRAWER_PROJECT_PRESENTATIONS_KEY_V2)
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
  const [profiles, setProfiles] = useState<LaunchProfile[]>([])
  const [defaultProfile, setDefaultProfile] = useState('default')
  const [launcherProfile, setLauncherProfile] = useState(localStorage.getItem('mux.lastProfile') || '')
  // The ref alone, deliberately. These milestones exist to be POSTed to the daemon's
  // startup diagnostics; nothing renders them any more (the session menu's boot chip was
  // the only reader), so holding them in state re-rendered the whole workspace once per
  // milestone for a value nobody displayed.
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

  // How far a lifted `hold` must travel from where it opened its menu before the gesture is a
  // reorder rather than a hand settling on the menu. Small enough to feel immediate, past the
  // idle jitter of a finger resting on a phone.
  const DRAG_BEGIN=12
  const beginPointerDrag=(
    event:JSX.TargetedPointerEvent<HTMLElement>,label:string,identity:string,
    onStart:()=>void,onMove:(event:PointerEvent)=>void,onDrop:()=>void,onCancel:()=>void,
    activation:PointerDragActivation=POINTER_MOVE_DRAG,
  )=>{
    if(event.button!==0||!event.isPrimary)return
    const source=event.currentTarget
    const pointerId=event.pointerId,startX=event.clientX,startY=event.clientY
    let active=false,done=false,ghost:HTMLDivElement|null=null,activationTimer:number|null=null
    // `dragging` turns true only once a lifted `hold` has actually travelled past DRAG_BEGIN
    // (or immediately, for a movement-mode desktop drag): before that a `hold` has opened its
    // menu and is still "menu territory", so releasing keeps the menu instead of reordering.
    let dragging=false,activateX=startX,activateY=startY
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
    // Android long-press fires a native `contextmenu` ~500ms into a stationary touch and
    // cancels the pointer with it, killing a hold that lingered before dragging. The rows
    // gate their own onContextMenu to desktop, so nothing else wants this event while a
    // touch drag candidate is down.
    const suppressContextMenu=(menu:Event)=>menu.preventDefault()
    if(event.pointerType==='touch')window.addEventListener('contextmenu',suppressContextMenu,true)
    const cleanup=()=>{
      if(activePointerDragCancelRef.current===cancel)activePointerDragCancelRef.current=null
      releaseDragClaim?.();releaseDragClaim=null
      window.removeEventListener('pointermove',move)
      window.removeEventListener('pointerup',up)
      window.removeEventListener('pointercancel',cancelPointer)
      window.removeEventListener('touchmove',blockTouchScroll)
      window.removeEventListener('contextmenu',suppressContextMenu,true)
      window.removeEventListener('blur',blurCancel)
      window.removeEventListener('keydown',key,true)
      document.body.removeEventListener('lostpointercapture',lostCapture)
      if(document.body.hasPointerCapture(pointerId))document.body.releasePointerCapture(pointerId)
      if(activationTimer!==null)window.clearTimeout(activationTimer)
      activationTimer=null
      document.body.classList.remove('workspace-pointer-dragging')
      source.classList.remove('dragging')
      showPointerDropIndicator(null)
      showDropSlot(null)
      ghost?.remove()
    }
    const beginDragVisual=(clientX:number,clientY:number)=>{
      if(dragging)return
      dragging=true
      document.body.classList.add('workspace-pointer-dragging');source.classList.add('dragging')
      ghost=document.createElement('div');ghost.className='mux-pointer-drag-ghost';ghost.textContent=label;document.body.appendChild(ghost)
      ghost.style.transform=`translate3d(${clientX+14}px,${clientY+12}px,0)`
    }
    const finish=(commit:boolean)=>{
      if(done)return
      done=true
      const wasDragging=dragging
      cleanup()
      if(!active)return
      window.setTimeout(()=>{if(suppressDragClickRef.current===identity)suppressDragClickRef.current=null},0)
      // Dragged: commit the reorder (or cancel). Activated but never dragged: a `hold` opened
      // its menu at activation and that menu stays up — just tear the drag down, no reorder.
      if(wasDragging){if(commit)onDrop();else onCancel();return}
      onCancel()
    }
    const activate=(clientX:number,clientY:number)=>{
      if(active||done)return
      active=true
      activateX=clientX;activateY=clientY
      if(activationTimer!==null)window.clearTimeout(activationTimer)
      activationTimer=null
      releaseDragClaim=claimPointerDrag();suppressDragClickRef.current=identity
      // Capture on the body, never on the row: a `hold` opens its menu from onStart, and
      // that render can move the row node — a captured element that leaves the document
      // (even for the instant of an insertBefore) drops the capture, and the resulting
      // `lostpointercapture` killed the gesture one frame after it activated. The body
      // never moves (RailEditor learned the same lesson with its chip preview). Capture is
      // best effort — the real event routing is the window listeners keyed by pointerId —
      // but holding it keeps the stream off whatever lands under the finger, menu included.
      try{document.body.setPointerCapture(pointerId)}catch{/* window listeners still track the pointer */}
      // A `hold` opens its menu here (via onStart) and shows no ghost yet — the drag begins
      // only once the finger travels past DRAG_BEGIN, which dismisses that menu. Every other
      // mode is a drag from the first instant it activates.
      onStart()
      if(activation.mode!=='hold')beginDragVisual(clientX,clientY)
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
      if(!dragging){
        // `hold` only reaches here (others begin the visual at activate). The lifted menu holds
        // until the finger clearly travels, so a hand that merely settles keeps the menu.
        if(Math.hypot(pointer.clientX-activateX,pointer.clientY-activateY)<=DRAG_BEGIN){pointer.preventDefault();return}
        beginDragVisual(pointer.clientX,pointer.clientY)
      }
      pointer.preventDefault()
      if(ghost)ghost.style.transform=`translate3d(${pointer.clientX+14}px,${pointer.clientY+12}px,0)`
      onMove(pointer)
    }
    const up=(pointer:PointerEvent)=>{if(pointer.pointerId===pointerId)finish(true)}
    const cancelPointer=(pointer:PointerEvent)=>{if(pointer.pointerId===pointerId)finish(false)}
    // `lostpointercapture` BUBBLES, and on touch the row holds implicit capture from
    // pointerdown — so the very transfer to the body fires it at the row and it arrives
    // here one frame after activation, which read as a cancel and killed every mobile
    // hold. Only the body losing ITS capture is fatal; the row losing the capture we just
    // took from it is the transfer working.
    const lostCapture=(pointer:PointerEvent)=>{
      if(pointer.pointerId!==pointerId||pointer.target!==document.body)return
      finish(false)
    }
    const blurCancel=()=>finish(false)
    const cancel=()=>finish(false)
    const key=(keyboard:KeyboardEvent)=>{if(keyboard.key==='Escape'){keyboard.preventDefault();finish(false)}}
    window.addEventListener('pointermove',move,{passive:false})
    window.addEventListener('pointerup',up)
    window.addEventListener('pointercancel',cancelPointer)
    window.addEventListener('blur',blurCancel)
    window.addEventListener('keydown',key,true)
    document.body.addEventListener('lostpointercapture',lostCapture)
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
  // Daemon-started sessions whose automatic join the server refused, so a Project that cannot
  // take another leaf stops being asked (`sessionJoin.ts`).
  const joinAttempts=useRef<JoinAttempts>({})
  // The pane a joining session should prefer, kept fresh every render because `refresh` runs from
  // intervals and sockets whose closures are older than the current focus.
  const joinAnchor=useRef<{projectId:string;viewId:string|null}>({projectId:'',viewId:null})
  const spawning = useRef(false)
  const relaunching = useRef(false)
  const longPressTimer = useRef<number | null>(null)
  const longPressOrigin = useRef<{pointerId:number;x:number;y:number}|null>(null)
  const runHeldRef = useRef(false)
  // A Group header's hold opened its menu, so the click the hold ends with must not
  // also fold the Group underneath it.
  const groupHeldRef = useRef(false)
  const mobileTabHeldRef = useRef(false)
  // When the Run menu's scrim dismissed it, so the trigger's own click can tell
  // "reopen" from "the closing half of a toggle tap".
  const runMenuClosedAt = useRef(0)
  // Set when an outside pointer-down closed a context menu, so the menu's focus
  // teardown knows not to reclaim focus from whatever that pointer landed on.
  const menuDismissedByPointer = useRef(false)
  const notificationIds = useRef<Set<string>>(new Set())
  const paletteInput = useRef<HTMLInputElement>(null)
  // The refresh cycle body, re-pointed every render so the controller - created
  // once - always runs the current one. The controller owns the dedupe, the
  // stall abort and the queued follow-up; see `fleetRefresh.ts`.
  const runFleetRefreshRef = useRef<(signal: AbortSignal) => Promise<void>>(async () => {})
  const refreshController = useMemo<FleetRefreshController>(() => createFleetRefreshController(
    signal => runFleetRefreshRef.current(signal),
    {
      onStall: stallMs => {
        if (!suppressErrorsRef.current) setError(`The fleet refresh did not finish within ${Math.round(stallMs/1000)}s; the next one starts fresh.`)
      },
    },
  ), [])
  useEffect(() => () => refreshController.reset(), [refreshController])
  const sessionsRef=useRef<Session[]>([])
  const projectsRef=useRef<Project[]>([])
  // Project layout is optimistic durable state; `layoutWriter.ts` owns the write chain,
  // the generation guard and the server revisions. What stays here is the browser's view
  // of a layout (`layoutValues` and `layoutMap`), which every surface reads.
  const layoutWriter = useMemo(() => createLayoutWriter({
    patch: (projectId, layout, revision) =>
      api<Project>('PATCH', `/api/projects/${projectId}`, { layout, layout_revision: revision }),
    showLayout: (projectId, layout) => {
      layoutValues.current[projectId]=layout
      setLayoutMap(current => ({ ...current, [projectId]: layout }))
    },
    adoptProject: project => setProjects(items => items.map(item => item.id === project.id ? project : item)),
    serverRevision: projectId => projectsRef.current.find(project => project.id === projectId)?.layout_revision,
    refresh: () => refreshController.refresh(),
    onError: setError,
  }), [refreshController])
  // Highest durable event sequence this tab has covered. Control frames can advance
  // it without transferring audit-only payloads that browser state does not consume.
  const lastEventSeq = useRef(0)
  const requestedView = useRef(parseViewPreference(location.search))
  const focusMemory = useRef(parseFocusMemory(localStorage.getItem('mux.focus.v1')))
  const [focusHydrated,setFocusHydrated]=useState(false)
  sessionsRef.current=sessions
  projectsRef.current=projects
  joinAnchor.current={projectId,viewId:focusedViewId||activeId}
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

  // One refresh cycle: read the five fleet slices under their deadlines, then apply
  // whichever of them arrived. Slice-by-slice rather than all-or-nothing, because the
  // five are independent registries and a single transient 500 used to discard the
  // other four for that cycle. `fleetRefresh.ts` owns the deadlines and the dedupe.
  const runFleetRefresh = async (signal: AbortSignal): Promise<void> => {
    const { slices, failures } = await fetchFleetSlices({ signal })
    // The controller abandoned this cycle; applying a snapshot a newer cycle has
    // already superseded would move the UI backwards.
    if (signal.aborted) return
    const { sessions: nextSessions, projects: nextProjects, previews: nextPreviews, groups: nextGroups, harnesses: nextHarnesses } = slices
    if (nextHarnesses) {
      installHarnessRegistry(nextHarnesses)
      setHarnessRegistryRevision(current=>current+1)
    }
    // The daemon still reports a session being killed as live for the whole
    // teardown window, so every consumer of this GET has to see the fleet the
    // operator sees - the row, the layout leaf, and the live set they are reconciled against.
    let visibleSessions: Session[] | null = null
    if (nextSessions) {
      expireStaleKills()
      const visible = applyKillTombstones(nextSessions, pendingKills.current)
      visibleSessions = visible
      setSessions(current => {
        const optimistic=current.filter(session=>session.pending&&pendingSpawns.current[session.id]&&!pendingSpawns.current[session.id].resolvedId)
        return reconcileSessionSnapshots(current,visible,optimistic)
      })
    }
    if (nextProjects) {
      setProjects(nextProjects)
      setWorkspaceLoaded(true)
      layoutWriter.adoptRevisions(nextProjects)
    }
    if (nextPreviews) setPreviews(Object.fromEntries(nextPreviews.items.map(item => [item.id, item])))
    // Gated on the Group read itself rather than on the whole cycle: the only thing
    // this flag guards is pruning sidebar fold state against the Group registry, and
    // that registry either loaded or it did not.
    if (nextGroups) { setProjectGroups(nextGroups); setRegistryLoaded(true) }
    // The layout pass reconciles panes against sessions, Projects and previews at
    // once, so it runs only when all three arrived. A cycle that lost one of them
    // leaves every layout untouched and the next full cycle reconciles.
    if (visibleSessions && nextProjects && nextPreviews) {
      const plan = planFleetLayouts({
        sessions: visibleSessions,
        projects: nextProjects,
        previewIds: new Set(nextPreviews.items.map(item => item.id)),
        pendingSpawns: pendingSpawns.current,
        joinAttempts: joinAttempts.current,
        joinAnchor: joinAnchor.current,
        hasPendingLayoutWrite: layoutWriter.hasPendingWrite,
        isEnded: isEndedSession,
      })
      joinAttempts.current = plan.joinAttempts
      setLayoutMap(current => {
        const next = { ...current, ...plan.layouts }
        layoutValues.current=next
        return next
      })
      // Persisted after the render that shows them, and quietly: a join nobody asked for must
      // not report a conflict the operator did not cause. A second device joining the same
      // session first simply wins - the loser's refresh finds the leaf already there and
      // proposes nothing, so the two converge instead of fighting.
      for(const join of plan.joins){
        void updateLayout(join.projectId,join.layout,{quiet:true}).then(persisted=>{
          if(!persisted)joinAttempts.current=recordJoinFailure(joinAttempts.current,join.ids)
        })
      }
    }
    // While the daemon is deliberately away, every request failing is the
    // expected state, not news. The reconnect that follows each dropped
    // events socket schedules one of these, so without the gate the overlay
    // explaining the outage sits under a stream of toasts reporting it.
    if (!failures.length) setError('')
    else if (!suppressErrorsRef.current) setError(describeFleetFailures(failures))
  }
  // An error escaping the application half is a defect rather than an outage, but it
  // must still reach the operator: the controller only sees that the cycle concluded.
  runFleetRefreshRef.current = async signal => {
    try { await runFleetRefresh(signal) }
    catch (cause) { if (!suppressErrorsRef.current) setError(cause instanceof Error ? cause.message : String(cause)) }
  }

  const refresh = refreshController.refresh

  type AppConfig = {
    theme:ThemeName
    custom_theme:CustomTheme
    xterm_scrollback_lines:number
    terminal_renderer:TerminalRendererPreference
    drawer_tab_display?:'icon'|'title'
    utility_rail_display?:'icon'|'title'
    note_scratchpad_enabled?:boolean
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
    setExperienceTierUnchosen(config.experience_tier==='')
    // Density follows the tier, but only until the user chooses: a device with
    // no stored visibility set re-derives its default from the tier on every
    // config arrival (so applying a tier in Settings takes effect live), while
    // a stored choice - including the empty set - is never overwritten.
    if(localStorage.getItem(DRAWER_HIDDEN_KEY)===null)
      setHiddenDrawerTabs(defaultHiddenDrawerTabs((config.experience_tier??'') as ExperienceTierChoice))
    setQuestSignals({
      tts_enabled: config.tts_enabled === true,
      stt_enabled: config.stt_enabled === true,
      quests_dismissed: Array.isArray(config.quests_dismissed) ? config.quests_dismissed as string[] : [],
    })
    setAccountPromptDismissed(config.provider_accounts_prompt_dismissed === true)
    applyNoteEditorConfig(config)
    previewUiScaleConfig(config)
    applyRailDensity(config)
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
    setSurfaceGestures(surfaceGesturesEnabled(config))
    setViewBack(viewBackEnabled(config))
    setClipboardEnabled(config.clipboard_history_enabled!==false)
    setScratchpadEnabled(config.note_scratchpad_enabled!==false)
    setDrawerTabDisplay(config.drawer_tab_display==='title'?'title':'icon')
    setUtilityRailDisplay(config.utility_rail_display==='title'?'title':'icon')
  }

  /**
   * Re-read the voice status, keeping the last good one when the daemon blips.
   *
   * This used to be a single fetch at mount with `.catch(()=>setVoiceStatus(null))`.
   * A page that lost that fetch once never asked again, `stt_available` stayed
   * unreadable for the life of that page, and Talk answered every press with
   * `talk:error` and "Daemon transcription is unavailable" *without contacting
   * the daemon*, so nothing appeared in any log while the daemon's own
   * `/api/voice` went on reporting a perfectly healthy engine.
   *
   * The desktop shell is the client that pays for it, and that is the diagnosis
   * for its reported dead microphone: its page is opened once and lives for days
   * across daemon restarts and redeploys, where a browser tab gets reloaded and
   * silently repairs itself.
   *
   * So: never latch a failure, and re-ask whenever the event stream says the
   * daemon is back.
   */
  const loadVoiceStatus = ():Promise<VoiceStatus|null> =>
    api<VoiceStatus>('GET','/api/voice')
      .then(status=>{setVoiceStatus(status);return status})
      // Deliberately keeps the previous value: a stale `stt_available` is a far
      // better basis for the next press than "unavailable", and the reconnect
      // path below re-asks anyway. The answer is *returned* as well as stored so
      // a caller that needs it now does not have to wait for a render.
      .catch(()=>null)

  const loadConfig = (includeTheme:boolean) =>
    api<AppConfig>('GET','/api/config')
      .then(config=>applyConfig(config,includeTheme))
      .catch(()=>{})
      // Settled, not succeeded: an unreachable daemon must not suppress the tour forever.
      .finally(()=>setFirstRunResolved(true))

  const persistScratchpadEnabled=async(next:boolean):Promise<void>=>{
    try{
      const config=await api<AppConfig>('PATCH','/api/config',{note_scratchpad_enabled:next})
      applyConfig(config,false)
    }catch(cause){
      setError(`Scratchpad visibility could not be saved: ${cause instanceof Error?cause.message:String(cause)}`)
      throw cause
    }
  }

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
    void loadVoiceStatus()
    void loadNotifications()
    // The host descriptor rides the request: the daemon resolves rules for the
    // keyboard that is actually asking, so a desktop-only chord is live in the
    // desktop app and simply absent in a browser tab rather than dead in both.
    const loadKeys = () => void api<{ resolved: ResolvedBindings }>('GET', `/api/keybindings?${hostQuery()}`).then(result => {
      const next = result.resolved || {}
      setKeybindingsStore(next, hostProfile().platform)
      setKeymap(current => JSON.stringify(current) === JSON.stringify(next) ? current : next)
    })
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

  // Once at boot. Nothing polls it: the two things that move the answer are a
  // configuration change (handled on the event) and installing a CLI, which is
  // not something to poll a subprocess probe for every few seconds.
  useEffect(() => { loadConfiguratorOptions() }, [])

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

  // Rail density is stored per device class for the same reason chrome scale is, and so
  // crossing the breakpoint has to re-resolve which stored step applies.
  useEffect(()=>watchRailDensityProfile(),[])

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
    const root = document.documentElement
    let lastInset = -1
    let pendingTimer: number | null = null
    // Retire a predicted reservation. Called both when a real keyboard shows up (the
    // measurement supersedes the guess) and when the guess turns out to be wrong - a focus
    // that never raises a keyboard, or one the operator moves away from. Holding a prediction
    // past either is how `keyboardReserve.ts` once left a pane rendering on half a screen
    // with nothing covering the other half.
    const clearPending = () => {
      if (pendingTimer !== null) { window.clearTimeout(pendingTimer); pendingTimer = null }
      root.style.removeProperty('--keyboard-pending')
      root.classList.remove('soft-keyboard-pending')
    }
    // The shell is the *layout* viewport, which `interactive-widget=resizes-visual` keeps at
    // full height while the keyboard is up. Sizing it from `visualViewport` instead is what
    // used to shrink every terminal when the keyboard opened — and shrinking an
    // alternate-screen PTY discards the rows that no longer fit, permanently. The keyboard
    // is now an inset the layout is slid up by, never a smaller layout.
    const updateAppHeight = () => {
      const layout = Math.round(window.innerHeight)
      const inset = softKeyboardInset(layout, Math.round(viewport?.height ?? layout))
      root.style.setProperty('--app-height', `${layout}px`)
      root.style.setProperty('--keyboard-inset', `${inset}px`)
      // Where the browser has scrolled the visual viewport to, which is a separate fact from
      // how tall the keyboard is and is what `--keyboard-cover` subtracts. See
      // `softKeyboardVisualOffset` and the `--keyboard-cover` block in `style.css`.
      root.style.setProperty('--visual-offset', `${softKeyboardVisualOffset(viewport?.offsetTop ?? 0, inset)}px`)
      // A class as well as the length, so the slide can be scoped to the keyboard being up.
      // A `translateY(0)` still makes an element a containing block for its `position:fixed`
      // descendants, which would silently re-anchor the drawer and sidebar overlays.
      root.classList.toggle('soft-keyboard-open', inset > 0)
      // A measured keyboard outranks a predicted one, and dropping the prediction here rather
      // than leaving it shadowed by the class is what makes the *close* correct too: dismissing
      // the keyboard with the field still focused returns the inset to zero, and a prediction
      // still armed would take over again and hold half the screen back with nothing on it.
      if (inset > 0) clearPending()
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
    // Give the keyboard its space before it arrives, rather than compensating once it has.
    //
    // The browser scrolls the visual viewport because the field it just focused is under the
    // keys at the moment they appear. A panel that has already shortened puts that field above
    // them, so there is nothing to scroll and `--visual-offset` stays zero - which is why this
    // is the fix and the offset arithmetic is the backstop for when the prediction is wrong,
    // absent (no keyboard measured on this device yet), or beaten by a rotation.
    //
    // Focus is the earliest honest signal a keyboard is coming, and the *only* one: Android
    // announces nothing else. `RESERVE_INTENT_WINDOW_MS` is how long that signal is trusted,
    // shared with the terminal's own reservation because it is the same bet on the same
    // animation. Nothing arms on a device that types with a real keyboard.
    const armPending = () => {
      if (!hasSoftKeyboard() || root.classList.contains('soft-keyboard-open')) return
      if (!raisesSoftKeyboard(deepActiveElement(document))) return
      const predicted = reservedKeyboardPx(lastSoftKeyboardInset(), Math.round(window.innerHeight))
      if (predicted <= 0) return
      if (pendingTimer !== null) window.clearTimeout(pendingTimer)
      root.style.setProperty('--keyboard-pending', `${predicted}px`)
      root.classList.add('soft-keyboard-pending')
      pendingTimer = window.setTimeout(clearPending, RESERVE_INTENT_WINDOW_MS)
    }
    // `focusout` runs before the incoming element takes focus, so this reads "nothing is
    // focused" even for a move between two fields - and that is fine, because the `focusin`
    // that follows re-arms in the same task and nothing is painted in between.
    const onFocusOut = () => { if (!raisesSoftKeyboard(deepActiveElement(document))) clearPending() }
    updateAppHeight()
    window.addEventListener('resize', updateAppHeight)
    window.addEventListener('focusin', armPending)
    window.addEventListener('focusout', onFocusOut)
    viewport?.addEventListener('resize', updateAppHeight)
    viewport?.addEventListener('scroll', updateAppHeight)
    return () => {
      window.removeEventListener('resize', updateAppHeight)
      window.removeEventListener('focusin', armPending)
      window.removeEventListener('focusout', onFocusOut)
      viewport?.removeEventListener('resize', updateAppHeight)
      viewport?.removeEventListener('scroll', updateAppHeight)
      clearPending()
      root.style.removeProperty('--app-height')
      root.style.removeProperty('--keyboard-inset')
      root.style.removeProperty('--visual-offset')
      root.classList.remove('soft-keyboard-open')
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
        // Unconditional, unlike the resume-only refreshes below: this is the one
        // cache whose absence is silent and permanent. `configuration_changed`
        // only fires when someone edits a setting, so a page whose first
        // `/api/voice` was lost has no other way back, and the socket opening
        // is the app's own evidence that the daemon is answering again.
        void loadVoiceStatus()
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
          // A transient frame carrying one session's complete new readiness. Patched
          // onto the row directly rather than merged as a session snapshot: it is not
          // one, it has no revision or generation to order against, and routing it
          // through `mergeSessionSnapshot` would have it compete with the record
          // authorities over fields it does not carry.
          if (event.type === 'delivery_readiness_changed' && event.session_id) {
            const readiness = event.payload?.readiness as DeliveryReadiness | undefined
            if (readiness) {
              const targetId = String(event.session_id)
              setSessions(items => items.map(item =>
                item.id === targetId ? { ...item, delivery_readiness: readiness } : item))
            }
          }
          if (event.type === 'turn_ended') window.dispatchEvent(new CustomEvent(TURN_ENDED_EVENT, { detail: { sessionId: event.session_id } }))
          if (event.type === 'transcript_message') window.dispatchEvent(new CustomEvent(TRANSCRIPT_CHANGED_EVENT, { detail: { sessionId: event.session_id } }))
          // The daemon replaced a finished reply's segments with the single clip
          // they were always one of. Nothing plays here; the lists refetch so the
          // reply stops being addressed by ids that are on their way out.
          if (!isReplay && event.type === 'voice_clip_joined') {
            window.dispatchEvent(new CustomEvent('mux:voice-clip', { detail: {
              sessionId: event.session_id,
              clipId: String(event.payload?.clip_id || ''),
              status: 'ready',
              streamId: event.payload?.stream_id,
              joined: true,
            } }))
          }
          if (event.type === 'voice_clip_ready' || event.type === 'voice_clip_failed') {
            const clipId = String(event.payload?.clip_id || '')
            window.dispatchEvent(new CustomEvent('mux:voice-clip', { detail: {
              sessionId: event.session_id, clipId,
              status: event.type === 'voice_clip_ready' ? 'ready' : 'failed',
              trigger: event.payload?.trigger,
              streamId: event.payload?.stream_id,
            } }))
            // The pane's participation is re-checked here as well as on the daemon: a
            // clip generated just before the user hit "off" would otherwise land and
            // start speaking after the switch was thrown. Whether it *plays* or is
            // *held* is `enqueueAutoplay`'s call, so the device toggle and the focus
            // rule stay in one place rather than being half-decided here.
            const autoAllowed = eventSession ? eventSession.voice_mode !== 'off' : true
            if (!isReplay && event.type === 'voice_clip_ready' && event.payload?.trigger === 'auto' && clipId && autoAllowed) enqueueAutoplay(clipId,String(event.payload?.stream_id||'')||null,event.session_id||null)
            if(!isReplay&&event.type==='voice_clip_ready'&&event.payload?.trigger!=='auto'&&clipId&&event.payload?.stream_id){
              // `segmentPosition`, never an inline coercion: `count === 0` means
              // "this stream is still open", and `||1` read that as "the last of
              // one" and hung up on the reply after its first sentence.
              const {index,count}=segmentPosition(event.payload)
              enqueueRequestedStreamClip(clipId,String(event.payload.stream_id),index,count)
            }
          }
          // The producer finished an open stream. Clips already queued still
          // play; only the claim that lets late segments join it is released,
          // which is what keeps an abandoned turn from appending itself to
          // whatever is speaking now.
          if(!isReplay&&event.type==='voice_stream_closed'&&event.payload?.stream_id){
            closeRequestedStream(String(event.payload.stream_id))
          }
          // The redeploy broadcasts. Never on replay: these are durable events,
          // so a reconnect weeks later would otherwise resurrect a finished
          // redeploy's overlay off the event history. The live copy is what
          // matters, and the wait loop's own health probes are the authority on
          // when it ends regardless.
          if (!isReplay && event.type === 'daemon_redeploy_started') enterRedeploy()
          if (!isReplay && event.type === 'daemon_redeploy_stopping') {
            // Sent by the daemon from its own shutdown handler, while it is still
            // alive: the one authoritative "the outage starts now". Health probes
            // would get there too, two strikes later; this skips the guessing.
            // `confirmRedeploy` first, so a client whose own accept is still in
            // flight (or which never heard the start) is in `building` before
            // the outage is entered from it.
            setRedeploy(current => enterOutage(confirmRedeploy(current, Date.now())))
          }
          if (!isReplay && event.type === 'settings_changed') refreshSettings()
          // Another device (or another tab) changed the ring; an open picker refetches.
          if (event.type === 'clipboard_changed') window.dispatchEvent(new CustomEvent(CLIPBOARD_CHANGED_EVENT))
          if (!isReplay && event.type === 'configuration_changed') {
            void loadVoiceStatus()
            void assistantStatus().then(setAssistantInfo).catch(()=>{})
            // A change made from another device (or by editing the config file)
            // has to reach this tab's copy of *every* config-derived setting, not
            // the subset this handler happened to list.
            void loadConfig(false)
            // The drawer's gate notices read the install switches from their own cached
            // copy (`installSwitches.ts`), which has to be dropped here too — otherwise
            // a switch turned on elsewhere leaves this tab's gate standing over a
            // surface that now works.
            window.dispatchEvent(new CustomEvent(INSTALL_CONFIG_CHANGED))
            // `default_harness` and the per-harness enablement map both live in
            // the config, so the launcher's resolved default can move here.
            void loadConfiguratorOptions()
          }
          if(event.type==='project_files_changed')window.dispatchEvent(new CustomEvent('mux:project-files-changed',{detail:{projectId:event.payload?.project_id,paths:event.payload?.paths||[]}}))
          if(event.type==='project_used')applyProjectUse(String(event.payload?.project_id||''),Number(event.payload?.last_used_at||0))
          if(event.type==='agent_context_changed')window.dispatchEvent(new CustomEvent('mux:agent-context-changed',{detail:{projectId:event.payload?.project_id}}))
          // A Project's opt-in table changed. Every surface that goes inert without one
          // (`projectAutomations.ts`) drops its cached answer on this, so turning an
          // automation on in the registry clears that surface's gate notice immediately
          // instead of on its next remount.
          if(event.type==='project_configuration_changed')window.dispatchEvent(new CustomEvent('mux:project-automations-changed',{detail:{projectId:String(event.payload?.project_id||'')}}))
          // Queue tabs and pane chips live-update off these; payloads carry ids/counts only.
          if(event.type==='queue_updated'||event.type==='queue_delivery'){window.dispatchEvent(new CustomEvent('mux:queue-changed',{detail:{sessionId:event.session_id}}));refreshQueueSummary()}
          if(event.type==='spawn_request_drafted'||event.type==='spawn_request_decided')window.dispatchEvent(new CustomEvent('mux:queue-changed',{detail:{projectId:event.payload?.project_id}}))
          // The drawer's Git tab refetches its worktree list off this. Branch/dirty/upstream
          // already ride the session snapshots, so `git_changed` carries no state here —
          // only *which Project* moved, which is the one thing the tab needs to decide
          // whether the refetch is about the repository it is drawing. `git_changed` is
          // raised by every session's five-second dirty tick, so an unfiltered listener
          // was re-reading one Project's whole worktree map on another Project's poll.
          if(event.type==='worktree_created'||event.type==='worktree_removed'||event.type==='git_changed'||event.type==='git_provenance_changed')window.dispatchEvent(new CustomEvent('mux:git-changed',{detail:{projectId:String(event.payload?.project_id||'')}}))
          // The daemon serves its last worktree reading immediately and revalidates
          // behind it; this is that revalidation reporting that it disagreed. Its own
          // event because it is not an observation of a repository moving - it is the
          // reading layer superseding an answer it already handed out - and because
          // only the Git tab has anything to do with it.
          if(event.type==='git_overview_changed')window.dispatchEvent(new CustomEvent('mux:git-overview-changed',{detail:{projectId:String(event.payload?.project_id||'')}}))
          // Its own event rather than folding into `mux:git-changed`: a land step
          // changes the queue on a five-second cadence, and re-reading the whole
          // worktree overview and provenance ledger each time is not free.
          if(event.type==='land_changed'||event.type==='land_verify_approved')window.dispatchEvent(new CustomEvent('mux:land-changed'))
          if(!isReplay&&event.type==='note_changed')window.dispatchEvent(new CustomEvent('mux:note-changed',{detail:{scope:event.payload?.scope==='global'?'global':'project',projectId:String(event.payload?.project_id||''),kind:event.payload?.scope==='global'?'global-note':'note',noteId:String(event.payload?.note_id||''),revision:String(event.payload?.revision||'')}}))
          // Assistant dialog events fan out to the panel and the UI-action
          // executor; replay is forwarded flagged so views can rebuild state
          // without re-firing side effects (speech, earcons, UI commands).
          if(typeof event.type==='string'&&event.type.startsWith('assistant_'))window.dispatchEvent(new CustomEvent('mux:assistant-event',{detail:{type:event.type,payload:event.payload||{},replay:isReplay}}))
          if(event.type==='voice_model_progress')window.dispatchEvent(new CustomEvent('mux:voice-model',{detail:event.payload||{}}))
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
  // One decision, in one place, so "not loaded yet" can never be drawn as
  // "you have no Projects" again (`sidebarProjects.sidebarProjectsView`).
  const projectsView = sidebarProjectsView({loaded:workspaceLoaded,registered:projects.length,visible:visibleProjects.length})
  const orderedGroups=[...projectGroups].sort((a,b)=>a.position-b.position||a.name.localeCompare(b.name)||a.id.localeCompare(b.id))
  const recentProjectRanks=projectRecency(projects)
  const ungroupedProjects=sortProjects(
    visibleProjects.filter(project=>!project.group_id||!projectGroups.some(item=>item.id===project.group_id)),
    sidebarOrder.projectSort,
    recentProjectRanks,
  )
  // Every Group in manual order.
  const allBuckets=orderedGroups.map(group=>{
    const items=visibleProjects.filter(project=>project.group_id===group.id)
    // `visibleProjects` is already in manual order, which sortProjects treats as the
    // tie-break, so every mode falls back to what the user arranged by hand.
    return {id:group.id,name:group.name,items:sortProjects(items,sidebarOrder.projectSort,recentProjectRanks)}
  })
  // Groups sort by the same contract their contents do: manual order in, stable
  // sort out, so the arrangement underneath a sort is never lost — and by the same mode,
  // which places them among the root Projects rather than in a block below all of them.
  //
  // Every Group here reaches the screen, one holding nothing included. Empty Groups used
  // to be filtered out between this and the render, which made "create a Group" look like
  // it had failed and left the only way to fill it — dragging a Project in — pointing at
  // a section that was not on screen. An empty Group renders as its header plus a drop hint.
  const rootEntries=sortRootEntries(ungroupedProjects,allBuckets,sidebarOrder.projectSort,recentProjectRanks)
  // What the tree renders: each Group's section, and the runs of root Projects between them.
  const rootRows=sidebarRootRows(rootEntries)
  const displayBuckets=rootEntries.flatMap(entry=>entry.kind==='group'?[entry.bucket]:[])
  const displayBucketIds=displayBuckets.map(bucket=>bucket.id)
  // Sidebar reading order, top to bottom, with each Group's Projects where the Group sits.
  // The collapsed rail, the numbered
  // Project commands, and the drag baseline all follow what is on screen rather
  // than the stored positions, or a sorted sidebar would disagree with itself.
  // A folded Group still contributes its Projects: collapsing hides rows, it
  // does not remove the Projects from the rail or the numbered shortcuts.
  const displayProjects=rootEntries.flatMap(entry=>entry.kind==='group'?entry.bucket.items:[entry.project])
  const displayProjectIds=mergeVisibleOrder(orderedProjects.map(project=>project.id),displayProjects.map(project=>project.id))
  /** The Project `step` places away from the active one in sidebar reading order, or null
   *  at either end. Drawn from `displayProjects` so it agrees with the numbered shortcuts
   *  and the collapsed rail rather than with the stored positions. */
  const adjacentProject=(step:1|-1):Project|null=>{
    const index=displayProjects.findIndex(project=>project.id===projectId)
    if(index<0)return null
    return displayProjects[index+step]||null
  }
  // Which rows the typed filter still leaves on screen, or null while nothing is typed
  // — which means *not filtering*, and is why opening the filter changes nothing until
  // a character lands. Computed here rather than beside the tree it edits because the
  // drag handlers close over it: a drop computes its insertion index from the rows that
  // are drawn, so reordering a partial tree would move a Project somewhere nobody aimed
  // at. Rebuilt every render rather than memoized — the index is a linear pass over
  // Projects and sessions, and both change under it constantly, so a session that just
  // went `awaiting` has to stay findable by what its row is drawing.
  const sidebarFilter=sidebarSearchOpen
    ? sidebarTreeFilter(
      buildSidebarSearchIndex(displayProjects,sessions),
      displayBuckets.map(bucket=>({id:bucket.id,name:bucket.name,projectIds:bucket.items.map(item=>item.id)})),
      sidebarSearchQuery,
    )
    : null
  /** Whether a session row survives the filter. Also the predicate the pane-tree walk
   *  prunes by, so a split branch whose only terminal was filtered out stops being a
   *  branch instead of drawing an empty one. */
  const sessionPassesFilter=(id:string)=>!sidebarFilter||sidebarFilter.sessions.has(id)
  // A filtered tree is missing rows, so every sidebar drag is inert while one is up.
  const sidebarReorderable=!sidebarFilter
  // Which way the toolbar's fold control points. "Everything on screen is folded"
  // rather than "anything is", so the button only offers Expand once there is
  // genuinely nothing left to collapse — a half-folded tree still reads as untidy,
  // and one more click finishes the job instead of undoing it.
  const allFolded=!!displayProjects.length
    &&displayProjects.every(project=>collapsedProjects.has(project.id))
    &&displayBuckets.every(bucket=>isBucketCollapsed(sidebarOrder,bucket.id))
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
  // The one report that drives focus-driven playback. Read aloud plays the session
  // being watched and holds the rest, so this has to be the same "focused agent"
  // every other pane-scoped surface means — and `null` (a note, a shell, nothing)
  // holds everything, which is why a held clip is surfaced rather than dropped.
  useEffect(()=>{setPlaybackFocus(focusedAgentSession?.id||null)},[focusedAgentSession?.id])
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
  // ---- Mux assistant (Phase 10.6): tier 3 behind the grammar, plus the chat view ----
  const [assistantInfo,setAssistantInfo]=useState<AssistantStatus|null>(null)
  // Chat is the default addressee: the assistant lane is the one people reach
  // for, while talk (the free deterministic dictation draft) stays one tab away
  // as the degradation path for budget exhaustion, outages, or verbatim
  // dictation. Device-local and persisted, so a deliberate switch sticks.
  const [voicePanelMode,setVoicePanelModeState]=useState<VoicePanelMode>(()=>{
    try{const saved=localStorage.getItem('mux.voice.panelMode');return isVoicePanelMode(saved)?saved:'chat'}catch{return 'chat'}
  })
  const setVoicePanelMode=(mode:VoicePanelMode)=>{
    setVoicePanelModeState(mode)
    try{localStorage.setItem('mux.voice.panelMode',mode)}catch{/* private mode */}
  }
  /**
   * How much of the voice dock is on screen (`voiceDock.ts`) — a third axis, kept apart
   * from the microphone and from the addressee above. Collapsing to the top-bar chip is
   * presentation only: capture keeps running, the dialog keeps streaming, and the
   * assistant view below stays mounted at every state.
   */
  const [voiceDock,setVoiceDockModel]=useState<VoiceDockModel>(loadVoiceDock)
  const voiceDockRef=useRef(voiceDock)
  // Reduced against the ref rather than inside the state updater: two dispatches in one
  // tick (a card opening as capture stops, say) must compose, and persisting is a side
  // effect that does not belong inside a state function.
  const dispatchVoiceDock=(event:VoiceDockEvent)=>{
    const next=reduceVoiceDock(voiceDockRef.current,event)
    if(next===voiceDockRef.current)return
    voiceDockRef.current=next
    setVoiceDockModel(next)
    saveVoiceDock(next)
  }
  /** A reply that landed while the dock was collapsed; the chip carries the mark. */
  const [assistantUnseen,setAssistantUnseen]=useState(false)
  const [assistantPendingActions,setAssistantPendingActions]=useState(0)
  useEffect(()=>{if(voiceDock.state!=='chip')setAssistantUnseen(false)},[voiceDock.state])
  useEffect(()=>{void assistantStatus().then(setAssistantInfo).catch(()=>setAssistantInfo(null))},[])
  const assistantContextRef=useRef<AssistantClientContext>({})
  assistantContextRef.current={
    focused_session_id:activeId||null,
    active_project_id:activeProject?.id||null,
    client_id:ASSISTANT_CLIENT_ID,
    // Sent whole rather than clipped at 400. The daemon folds the per-Project
    // and per-session families into templated lines before they reach the
    // prompt (`summarize_command_labels`), so the wire payload is the only cost
    // of sending everything - and a clip here was a truncation of real
    // capabilities that nothing reported: a 23-Project workspace already offered
    // 315 labels, and the ones past the cut simply stopped existing as far as
    // the assistant knew. The remaining bound is a runaway guard, not a budget.
    commands:commandRegistryRef.current.filter(item=>item.available).slice(0,2000).map(item=>({id:item.id,label:item.label})),
  }
  const assistantClientContext=()=>assistantContextRef.current
  // The dispatch executor below mounts once, so it reaches the launcher through
  // a ref that every render re-points at the fresh closure.
  const spawnTerminalRef=useRef<((targetProject?:string,split?:false|SplitDirection|'stack',profileId?:string,targetSessionId?:string,position?:'before'|'after',backend?:string,options?:{argv?:string[];seedText?:string;stageText?:string;model?:string})=>Promise<Session|false>)|null>(null)
  // The deterministic spoken verdict on a card is not a model turn, so it gets
  // its own one-shot stream rather than joining the turn's (assistantSpeech.ts).
  const speakAssistantReply=async(text:string)=>{
    if(!voiceStatus?.enabled)throw new Error('Read aloud is off.')
    await speakOnce(text)
  }
  /**
   * Route one utterance/typed line to the assistant; false when it is disabled,
   * otherwise a short status line for the Talk history. A bare confirm/cancel
   * over an open confirmation card resolves deterministically — a human act the
   * model must never be able to perform by talking about it.
   */
  const sendAssistantTurn=async(text:string):Promise<string|false>=>{
    if(!assistantInfo?.enabled)return false
    const verdict=spokenConfirmation(text)
    const open=verdict?latestOpenAction():null
    if(verdict&&open){
      const spokenOutcome=verdict==='confirm'
        ?`Confirmed: ${open.restatement}.`
        :`Cancelled: ${open.restatement}.`
      try{
        if(verdict==='confirm')await confirmAction(open.id)
        else await cancelAction(open.id)
      }catch(cause){
        return `That action could not be ${verdict==='confirm'?'confirmed':'cancelled'}: ${cause instanceof Error?cause.message:String(cause)}`
      }
      if(voiceStatus?.enabled&&conversation.phase!=='off')void speakAssistantReply(spokenOutcome).catch(()=>{})
      return spokenOutcome
    }
    const dialogId=await ensureDialog()
    // A spoken question routed to the assistant never seizes the workspace back: a dock
    // the operator collapsed to the chip stays collapsed, the reply is spoken, and the
    // chip carries the unread mark. An already-open dock does switch to the chat body,
    // because otherwise the answer to what was just asked lands behind the talk tab.
    if(voiceDockRef.current.state!=='chip')setVoicePanelMode('chat')
    // Speaking over a running turn used to be refused, and the refusal had
    // nowhere to put the words — so they were simply lost. It is queued now,
    // and saying which of the two happened is the difference between "it
    // ignored me" and "it heard me".
    const accepted=await sendAssistantTurnApi(dialogId,text,assistantClientContext())
    const quoted=`“${text.slice(0,80)}${text.length>80?'…':''}”`
    return accepted.queued?`Queued for the assistant: ${quoted}`:`Asked the assistant: ${quoted}`
  }
  // The daemon dispatches client-executed actions (UI commands, composer
  // typing, pane-placed spawns) to the device the dialog turn came from; these
  // executors run them and report the outcome so the assistant's tool call can
  // complete. Actions stamped with another tab's client id are ignored here —
  // executing an untargeted broadcast would type into every mounted copy of a
  // pane and spawn one session per open workspace.
  useEffect(()=>{
    const handler=(raw:Event)=>{
      const event=(raw as CustomEvent).detail as {type:string;payload:Record<string,unknown>;replay?:boolean}
      if(event.type!=='assistant_action')return
      const payload=event.payload||{}
      // Every action event feeds the open-card tracker the deterministic
      // spoken confirm/cancel path reads; replay never revives an old card.
      noteAssistantActionEvent(payload as never,event.replay===true)
      if(event.replay)return
      if(String(payload.status)!=='dispatched')return
      const kind=String(payload.kind)
      const actionArguments=(payload.arguments||{}) as Record<string,unknown>
      const actionClient=String(actionArguments.client_id||'')
      if(actionClient&&actionClient!==ASSISTANT_CLIENT_ID)return
      const actionId=String(payload.id||'')
      if(!actionId)return
      if(kind==='type_into_session'||kind==='submit_session_composer'){
        // `target_session_id`: `session_id` is a first-class MuxEvent field the
        // bus lifts out of the payload, so it cannot ride here.
        const sessionTarget=String(payload.target_session_id||'')
        if(!sessionTarget)return
        void (async()=>{
          try{
            if(kind==='type_into_session'){
              // The mounted pane types it with no carriage return: staged in
              // the composer, visible, editable — and never delivered by us.
              await insertIntoTerminal(sessionTarget,String(actionArguments.text||''),false)
              await reportUiResult(actionId,{ok:true,detail:'typed into the composer without sending'}).catch(()=>{})
            }else{
              // The same Enter the mobile Send control and voice submit use.
              await requestTerminalAction(sessionTarget,{action:'sendKey',text:'\r'})
              await reportUiResult(actionId,{ok:true,detail:'pressed Enter on the composer'}).catch(()=>{})
            }
          }catch(cause){
            await reportUiResult(actionId,{ok:false,detail:cause instanceof Error?cause.message.slice(0,380):'the terminal action failed'}).catch(()=>{})
          }
        })()
        return
      }
      if(kind==='spawn_session'){
        const projectTarget=String(payload.project_id||'')
        // The daemon resolves the backend through its full default chain; the
        // frontend never names a harness (test_harness_name_literals).
        const spawnBackend=String(payload.backend||'')
        void (async()=>{
          try{
            // The device's own launch path, so the new session opens as a tab
            // in the currently active pane with the optimistic leaf and focus
            // every other launch entry point gets.
            const spawned=projectTarget&&spawnBackend?await spawnTerminalRef.current?.(projectTarget,false,undefined,undefined,'after',spawnBackend,payload.seed_text||payload.stage_text||payload.model?{seedText:payload.seed_text?String(payload.seed_text):undefined,stageText:payload.stage_text?String(payload.stage_text):undefined,model:payload.model?String(payload.model):undefined}:undefined):false
            await reportUiResult(actionId,spawned
              ?{ok:true,detail:`spawned ${typeof spawned==='object'?spawned.name:'a session'} into the active pane`}
              :{ok:false,detail:'the device could not start the session'}).catch(()=>{})
          }catch(cause){
            await reportUiResult(actionId,{ok:false,detail:cause instanceof Error?cause.message.slice(0,380):'the spawn failed'}).catch(()=>{})
          }
        })()
        return
      }
      if(kind!=='run_ui_command')return
      const commandText=String(actionArguments.command||'')
      if(!commandText)return
      void (async()=>{
        const registry=commandRegistryRef.current
        const plan=planUiCommand(registry,commandText)
        if(plan.kind==='none'){
          await reportUiResult(actionId,{ok:false,detail:'no command matched',candidates:plan.candidates}).catch(()=>{})
          return
        }
        try{
          if(plan.kind==='query'){
            // The closed grammar executes and reports in its own words —
            // including entity-resolution failures with candidates — which is
            // exactly what the assistant should hear back.
            const outcome=await voiceQueryHandler.current(plan.query)
            await reportUiResult(actionId,{ok:true,detail:outcome.detail.slice(0,380)}).catch(()=>{})
            return
          }
          if(plan.command.voice?.execute){
            const outcome=await plan.command.voice.execute(plan.captured)
            await reportUiResult(actionId,{ok:true,detail:outcome.detail.slice(0,380)}).catch(()=>{})
            return
          }
          const ran=runCommand(registry,plan.command.id)
          await reportUiResult(actionId,{
            ok:ran==='ran',
            detail:ran==='ran'?`ran ${plan.command.label}`:(plan.command.disabledReason||`${plan.command.label} is unavailable`),
          }).catch(()=>{})
        }catch(cause){
          await reportUiResult(actionId,{ok:false,detail:cause instanceof Error?cause.message.slice(0,380):'the command failed'}).catch(()=>{})
        }
      })()
    }
    window.addEventListener('mux:assistant-event',handler)
    return()=>window.removeEventListener('mux:assistant-event',handler)
  },[])
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
  // Chat mode makes the assistant the microphone's addressee; the callback is
  // read per utterance so a mode flip applies to the very next thing said.
  const conversation = useConversation(
    voiceStatus, updateSession, conversationTarget, handleVoiceIntent, sendAssistantTurn,
    ()=>voicePanelMode==='chat'&&!!assistantInfo?.enabled,
    loadVoiceStatus,
  )
  const talkActive=conversation.phase!=='off'
  // Who plain speech reaches right now. Named for the addressee rather than the "mode" it
  // is stored as, because `effectiveVoiceMode` in this file is already the read-aloud
  // mode of one session, which is an unrelated thing.
  const voiceBody=effectiveVoicePanelMode(voicePanelMode,talkActive)
  // The panel's modes in the order its tablist draws them, minus the one that tablist
  // disables: `dictation` needs a live capture to show anything, so stepping onto it with
  // Talk off would land on an empty body the tab itself refuses to open.
  const voicePanelModeOrder:VoicePanelMode[]=talkActive?['dictation','chat','read']:['chat','read']
  const stepVoicePanelMode=(step:1|-1)=>{
    const order=voicePanelModeOrder
    if(!order.length)return
    const at=order.indexOf(voicePanelMode)
    setVoicePanelMode(order[((at<0?0:at)+step+order.length)%order.length])
  }
  // Capture start/stop is a dock *event*, not a dock setter: only the reducer decides
  // whether it may open anything, and it may only ever open the dictation draft, which
  // has no other surface. Any other body leaves a collapsed dock collapsed.
  const captureAddressee=voiceAddressee(voicePanelMode,talkActive)==='assistant'
    ?'assistant' as const
    :'dictation' as const
  const captureAddresseeRef=useRef(captureAddressee);captureAddresseeRef.current=captureAddressee
  useEffect(()=>{
    dispatchVoiceDock({kind:'capture',active:talkActive,addressee:captureAddresseeRef.current})
  },[talkActive])
  // Mounted exactly once, here, for the life of the app. It is handed to the dock, which
  // hides it rather than dropping it: `AssistantPanel` holds the per-device set of cards
  // it has already announced, and a remount reads as a device that has never seen them
  // and speaks an open card's line a second time.
  const assistantView=<AssistantPanel
    enabled={!!assistantInfo?.enabled}
    clientContext={assistantClientContext}
    speechEnabled={!!voiceStatus?.enabled}
    voiceActive={talkActive}
    pendingSpeech={conversation.pendingText}
    pendingSpeechNote={conversation.pendingNote}
    variant={voiceBodyVariant(voiceDock.state,voiceBody,'chat')}
    onOpenActions={count=>{
      setAssistantPendingActions(count)
      // A countdown nobody can see is a decision made by timeout. One-way, and only as
      // far as the peek row, so it never grabs the workspace back on its own.
      if(count>0)dispatchVoiceDock({kind:'floor',state:'peek'})
    }}
    onReply={()=>{if(voiceDockRef.current.state==='chip')setAssistantUnseen(true)}}
  />

  // Sessions on screen right now (visible pane of the displayed project). Being
  // on screen is half of what marks a row read; a human at the window is the
  // other half (humanPresence.ts).
  const visibleSessionIds=visibleTerminalIds(activeLayout)
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
  // A hand-set unread mark outranks the dwell only for the visit it was made in
  // (sessionAttention.ts). Tracked here rather than on the record because which
  // panes are on screen is this client's own state, and it is what separates
  // "I am marking the pane I am looking at" from "I am back to read it".
  useEffect(()=>{
    setPinVisits(prev=>trackPinVisits(prev,sessions,visibleSessionIds))
  },[sessions,visibleSessionKey])
  const pending=humanPresent?pendingAcks(sessions,visibleSessionIds,ackedTurns,pinVisits):[]
  const pendingKey=pending.map(({id,turnSeq,explicit})=>`${id}:${turnSeq}:${explicit?'x':''}`).join('\n')
  useEffect(()=>{
    if(!pendingKey)return
    const timer=window.setTimeout(()=>{
      for(const {id,turnSeq,explicit} of pending){
        // Optimistic first: the row must clear now, not a round-trip later. A
        // failed POST is left alone rather than rolled back - the next snapshot
        // carries the daemon's own answer, and re-lighting a row the user just
        // looked at is worse than acknowledging it slightly early.
        //
        // A released pin is the exception and stays pessimistic: the pin is what
        // `isUnread` reads first, so clearing it locally and having the write
        // fail would leave the row reading as caught up against a daemon that
        // still holds the mark. It clears on the daemon's own snapshot instead,
        // one round trip after a dwell that already took seconds.
        if(!explicit)setAckedTurns(current=>current[id]>=turnSeq?current:{...current,[id]:turnSeq})
        void api('POST',`/api/sessions/${id}/read`,explicit?{read:true}:{turn_seq:turnSeq}).catch(()=>{})
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
    const session=sessions.find(item=>item.id===activeId&&item.project_id===projectId)
    // Persist the focused view (note/file/terminal) alongside the active session so a
    // later return to this project reopens exactly what was last looked at here.
    const focusView=leaves(activeLayout).some(leaf=>leaf.id===focusedViewId)?focusedViewId:null
    focusMemory.current=focusMemoryWith(focusMemory.current,projectId,session?.id||null,focusView)
    localStorage.setItem('mux.focus.v1',JSON.stringify(focusMemory.current))
    const next=viewUrl(location.href,projectId,session?.id||null)
    if(`${location.pathname}${location.search}${location.hash}`!==next)window.history.replaceState(window.history.state,'',next)
  },[focusHydrated,projectId,activeId,focusedViewId,sessions,layoutMap])

  // Feed the most-recently-focused stack a close consults (`sessionFocusHistory.ts`).
  //
  // Observed from the *settled* active session for the same reason the navigation rung
  // below is: `setActiveId` has a couple of dozen call sites - spawn, resume, branch,
  // a tab click, a closing pane handing focus on - and recording at each of them would
  // rot the first time a new flow forgot to. A ref rather than state because nothing
  // renders from it; it is only ever read at close time.
  const sessionFocusHistory=useRef<SessionFocusHistory>({})
  useEffect(()=>{
    if(!focusHydrated||!activeId||!projectId)return
    // Only a session that is really there: an id still settling, or one already
    // tombstoned by a kill, would otherwise be handed back as "where you were".
    if(!sessions.some(item=>item.id===activeId&&item.project_id===projectId&&!isEndedSession(item)))return
    sessionFocusHistory.current=recordFocusedSession(sessionFocusHistory.current,projectId,activeId)
  },[focusHydrated,projectId,activeId,sessions])

  // Feed the navigation rung of back (`viewHistory.ts`).
  //
  // Recorded from the *committed* (Project, view) pair rather than at the two dozen
  // places that call `setFocusedViewId` - spawn, resume, branch, project selection, a
  // closing pane handing focus to its neighbour - because only the settled value is what
  // the user is actually looking at, and per-call-site recording would rot the first time
  // a new flow forgot to do it. `reconcileFocusView` above is what settles that value, so
  // this effect observes what it agreed on rather than a request still in flight.
  const lastViewPosition=useRef<ViewPosition|null>(null)
  useEffect(()=>{
    if(!focusHydrated)return
    const next:ViewPosition={projectId,viewId:focusedViewId}
    const previous=lastViewPosition.current
    lastViewPosition.current=next
    // Hydration is an arrival rather than a move: there is nothing behind it to return to.
    if(previous)viewHistory.record(previous,next)
  },[focusHydrated,projectId,focusedViewId])

  useEffect(() => {
    if(!focusHydrated)return
    if (zoomedId && !leaves(activeLayout).some(leaf => leaf.id === zoomedId)) setZoomedId(null)
    const nextId = resolveActiveSession(
      sessions, projectId, activeId, visibleTerminalIds(activeLayout), terminalIds(activeLayout),
    )
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
  // Opening always lands on the content, never on the narrow layout's section drawer: a
  // caller that named a section has already navigated, and one that did not is returning
  // to the tab it left off on.
  const openSettings = (section?:string,setting?:string) => {
    setSettingsSection(section); setSettingsSetting(setting); setRevealToken(token=>token+1)
    setSettingsNavOpen(false)
    setSettingsOpen(true); setMainMenuOpen(false); setProjectMenu(null)
  }
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
    if(!scratchpadEnabled){setError('Enable the global Scratchpad under Settings → Notes first.');return}
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
    setContextMenu(null);setProjectMenu(null);setSidebarMenu(null);setNoteMenu(null);setStaticPreviewMenu(null);setTabMenu(null);setEmptyMenu(null);setDrawerDisplayMenu(null);setGroupMenu(null);setMainMenuOpen(false)
    setSortMenu({x,y})
  }
  const openGroupMenu=(groupId:string,x:number,y:number)=>{
    setContextMenu(null);setProjectMenu(null);setSidebarMenu(null);setNoteMenu(null);setStaticPreviewMenu(null);setTabMenu(null);setEmptyMenu(null);setDrawerDisplayMenu(null);setSortMenu(null);setRunMenu(null);setMainMenuOpen(false)
    // Each opening starts from "not asked yet", so a confirm armed on one Group cannot
    // be inherited by the next menu the user opens.
    setConfirmGroupDeleteId(null)
    setGroupMenu({groupId,x,y})
  }
  const openDrawerDisplayMenu=(x:number,y:number,surface:'tabs'|'rail',tab?:DrawerTabId)=>{
    setContextMenu(null);setProjectMenu(null);setSidebarMenu(null);setSortMenu(null);setNoteMenu(null);setStaticPreviewMenu(null);setTabMenu(null);setEmptyMenu(null);setMainMenuOpen(false)
    setDrawerDisplayMenu({x,y,surface,tab})
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
  /** Which list in the tree the pointer is dropping into — the ungrouped root or one
   *  Group's section, each tagged with `data-group-id`. Landing inside a list's own box
   *  wins outright; otherwise the nearest list within `DROP_LIST_MARGIN` claims it, which
   *  is what makes the seams between sections droppable rather than dead. Past that
   *  margin nothing does, so a pointer parked over the sidebar's footer commits nothing. */
  const projectListAt=(tree:HTMLElement,y:number):HTMLElement|null=>{
    let nearest:{element:HTMLElement;distance:number}|null=null
    for(const list of Array.from(tree.querySelectorAll<HTMLElement>(':scope > [data-group-id]'))){
      const box=list.getBoundingClientRect()
      if(y>=box.top&&y<box.bottom)return list
      const distance=y<box.top?box.top-y:y-box.bottom
      if(distance<=DROP_LIST_MARGIN&&(!nearest||distance<nearest.distance))nearest={element:list,distance}
    }
    return nearest?.element||null
  }
  /** A Project drag lands two changes at once: which Group holds the Project, and where
   *  it sits in the one global position order. The Group is a PATCH on the record, the
   *  order is the ordering endpoint, and both are sent — in that order, because the
   *  reorder is validated against the positions it was planned from and a Group write
   *  changes none of them. A drag that never left its own Group sends only the reorder,
   *  and one that only changed Group sends only the PATCH (`commitProjectOrder` returns
   *  early on an unchanged order, before it would demote the sort to Manual). */
  const commitProjectDrop=async(project:Project,drop:ProjectDrag)=>{
    if(drop.groupId!==groupIdFor(project)){
      setProjects(items=>items.map(item=>item.id===project.id?{...item,group_id:drop.groupId}:item))
      try{
        const updated=await api<Project>('PATCH',`/api/projects/${project.id}`,{group_id:drop.groupId})
        setProjects(items=>items.map(item=>item.id===updated.id?updated:item))
      }catch(cause){
        await refresh()
        setError(cause instanceof Error?cause.message:String(cause))
        return
      }
    }
    await commitProjectOrder(drop.previewIds)
  }
  const beginProjectPointerDrag=(event:JSX.TargetedPointerEvent<HTMLElement>,project:Project)=>{
    // A filtered tree is missing rows, so the insertion index a drop computes from
    // what is drawn would not be the position the user aimed at.
    if(!sidebarReorderable)return

    const tree=event.currentTarget.closest<HTMLElement>('.project-tree')
    const rowHeight=event.currentTarget.getBoundingClientRect().height
    const originGroupId=groupIdFor(project)
    const initial:ProjectDrag={id:project.id,previewIds:displayProjectIds,groupId:originGroupId,overId:null,side:null}
    let latestPointer:{clientX:number;clientY:number}|null=null,scrollFrame:number|null=null
    // A pointer over no list is not "hovering the last thing it hovered": with a Group
    // change riding on the same gesture, a release out there has to be a no-op, so the
    // miss resets the plan to the baseline rather than leaving the last one armed.
    const clearTarget=()=>{
      showDropSlot(null);showPointerDropIndicator(null)
      const current=dragProjectRef.current
      if(current)dragProjectRef.current={...current,previewIds:displayProjectIds,groupId:originGroupId,overId:null,side:null}
    }
    const preview=(pointer:{clientX:number;clientY:number})=>{
      const current=dragProjectRef.current
      if(!current||!tree){clearTarget();return}
      const list=projectListAt(tree,pointer.clientY)
      if(!list){clearTarget();return}
      const groupId=list.dataset.groupId||null
      const target=reorderTargetFromContainer(list,current.id,'vertical',pointer.clientY)
      if(!target){
        // An empty or folded list has no sibling row to sit beside, so the Group itself
        // is the whole target and the Project keeps its slot in the global order.
        dragProjectRef.current={...current,groupId,overId:null,side:null}
        showDropSlot(null)
        showPointerDropIndicator(list,'drop-into')
        return
      }
      const previewIds=reorderForHover(current.previewIds,current.id,target.id,target.side)
      dragProjectRef.current={...current,previewIds,groupId,overId:target.id,side:target.side}
      // The section is the drop target, but the row is what lands: a Project with sessions
      // showing is a tall section, and outlining all of it would promise a move of the whole
      // block into a gap that only ever receives one row.
      const targetSection=Array.from(list.querySelectorAll<HTMLElement>(':scope > [data-reorder-id]')).find(item=>item.dataset.reorderId===target.id)||null
      if(!targetSection){clearTarget();return}
      showPointerDropIndicator(null)
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
      // Mobile: the hold opens the Project menu here; a drag past DRAG_BEGIN dismisses it in onMove.
      ()=>{
        cancelLongPress();setContextMenu(null);setRunMenu(null)
        if(mobileWorkspace)openProjectMenuAt(project,event.clientX,event.clientY);else setProjectMenu(null)
        if(mobileWorkspace)navigator.vibrate?.(15)
        dragProjectRef.current=initial
      },
      pointer=>{
        setProjectMenu(null)
        latestPointer={clientX:pointer.clientX,clientY:pointer.clientY}
        preview(pointer)
        if(scrollFrame===null)scrollFrame=window.requestAnimationFrame(autoScroll)
      },
      ()=>{stopAutoScroll();const current=dragProjectRef.current;setDragProject(null);if(current)void commitProjectDrop(project,current)},
      ()=>{stopAutoScroll();setDragProject(null)},
      mobileWorkspace?MOBILE_HOLD_DRAG:POINTER_MOVE_DRAG,
    )
  }
  /** Reorder the sidebar's Groups. Group order lives on each Group record, so it is
   *  shared across devices, and it is a separate order from the Project positions — which
   *  is why Manual is the two-tier tree: there is no one key to interleave the two by. */
  const commitBucketOrder=async(nextIds:string[])=>{
    if(nextIds.join('\0')===displayBucketIds.join('\0'))return
    const nextGroupIds=nextIds
    // Placing a Group by hand is the statement that the sidebar is hand-arranged, so it
    // drops the one sort back to Manual and freezes what was on screen — the same rule a
    // Project drag follows. From a sorted tree that also re-splits the root into two tiers,
    // since that is what Manual means; the arrangement the drag produced survives it,
    // because each list keeps the relative order the drop had put it in.
    setSidebarOrder(setProjectSortMode(sidebarOrder,'custom'))
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
    // A filtered tree is missing rows, so the insertion index a drop computes from
    // what is drawn would not be the position the user aimed at.
    if(!sidebarReorderable)return

    const tree=event.currentTarget.closest<HTMLElement>('.project-tree')
    // Every Group renders, empty ones included, so `rendered` is the whole list and
    // the merge below is now the identity. It stays because the merge is what keeps
    // this correct if anything is ever filtered out of the tree again.
    const rendered=displayBuckets.map(bucket=>bucket.id)
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
    // A filtered tree is missing rows, so the insertion index a drop computes from
    // what is drawn would not be the position the user aimed at.
    if(!sidebarReorderable)return

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
      // Mobile: the hold opens the row's menu here; a drag past DRAG_BEGIN dismisses it in onMove.
      ()=>{cancelLongPress();setProjectMenu(null);if(mobileWorkspace&&!session.pending)openSessionMenu(session,event.clientX,event.clientY,'sidebar');if(mobileWorkspace)navigator.vibrate?.(15);dragSessionTargetRef.current=null},
      pointer=>{
        setContextMenu(null)
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
    if (!contextMenu && !projectMenu && !sidebarMenu && !sortMenu && !noteMenu && !staticPreviewMenu && !tabMenu && !emptyMenu && !drawerDisplayMenu && !mainMenuOpen) return
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
  }, [contextMenu, projectMenu, sidebarMenu, sortMenu, noteMenu, staticPreviewMenu, tabMenu, emptyMenu, drawerDisplayMenu, mainMenuOpen])

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
  // `options.configurator` swaps the daemon route for the one that mints a
  // configurator session, and nothing else: the optimistic pane, the focus, and
  // the layout write below are the same for every launch, and a second placement
  // path is exactly the thing that drifts. The prompt, the harness resolution,
  // and the session's own marker are all the daemon's, which is why the body
  // carries a Project and at most a harness name.
  const spawnTerminal = async (targetProject = projectId, split: false | SplitDirection | 'stack' = false, profileId?: string, targetSessionId?: string, position:'before'|'after'='after', backend:string='shell', options?:{argv?:string[];seedText?:string;stageText?:string;model?:string;configurator?:{harness?:string}}) => {
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
      const [spawnPath, spawnPayload] = options?.configurator
        ? [CONFIGURATOR_LAUNCH_PATH, launchBody(targetProject, options.configurator.harness)]
        : ['/api/sessions', {
            backend, project_id: targetProject,
            // A launch profile now exists for agent harnesses too, so this is no longer
            // gated on `shell`. The daemon refuses a profile whose own backend does not
            // match the requested one, which is the check the gate used to stand in for.
            profile_id: profileId || undefined,
            ...(options?.argv?.length ? { argv: options.argv } : {}),
            // A first prompt as text the agent RUNS: the daemon inlines short bodies into argv
            // and stages long ones into the workspace with a reader prompt, so there is no
            // client-side ceiling.
            ...(options?.seedText ? { seed_text: options.seedText } : {}),
            // Text left waiting in the composer, unsent: the daemon waits for readiness and
            // writes a bracketed paste with no Enter, so no pane involvement is needed.
            ...(options?.stageText ? { stage_text: options.stageText } : {}),
            // A model name in the harness's own spelling, never argv: the daemon owns the
            // per-harness mapping and the refusal, so the browser stays free of both.
            ...(options?.model ? { model: options.model } : {}),
          }] as const
      const next = await api<Session>('POST', spawnPath, spawnPayload)
      markProjectRecent(targetProject)
      startupOrigins.current[next.id]=startupOrigin
      const browserTiming={api_response:performance.now()-startupOrigin}
      clientStartupTimingValues.current[next.id]=browserTiming
      if (profileId) { localStorage.setItem('mux.lastProfile',profileId); setLauncherProfile(profileId) }
      // Remembered so holding mobile Run repeats the last launch without the menu.
      // Not for a configurator launch: the operator picked a conversation about
      // swe-mux, not a harness preference, and recording it here would make the
      // next held-Run open the wrong thing.
      if (!options?.configurator) localStorage.setItem('mux.lastBackend',backend)
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
      return next
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
  spawnTerminalRef.current=spawnTerminal

  // The configurator launch, from every entry point. `harness` empty asks the
  // daemon to resolve one, which is what a plain press does; the chooser passes a
  // name. The Project is the active one, and the daemon substitutes a sensible
  // one (its own source checkout, when there is one) if this tab has none - so a
  // launch from a surface with no Project selected still works rather than
  // refusing on a detail the operator did not choose.
  const launchConfigurator = async (harness = '') => {
    setConfiguratorMenu(null)
    const target = projectId || projectsRef.current[0]?.id || ''
    if (!target) { setError('Add a Project before launching the configurator.'); return }
    // The backend argument only decorates the optimistic pending pane, so the
    // resolved default is the best guess available before the daemon answers; the
    // real record replaces it moments later.
    const optimistic = harness || configuratorOptions?.default_harness || 'shell'
    await spawnTerminal(target, false, undefined, undefined, 'after', optimistic, { configurator: { harness } })
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
    setContextMenu(null);setNoteMenu(null);setStaticPreviewMenu(null);setTabMenu(null);setSidebarMenu(null);setRunMenu(null);setGroupMenu(null);setMainMenuOpen(false)
    setProjectMenu({project,x,y})
  }

  const openRunMenu=(project:Project,element:HTMLElement,trigger?:string)=>{
    const rect=element.getBoundingClientRect()
    setRunMenu({project,x:Math.max(6,Math.min(rect.left,window.innerWidth-306)),y:Math.min(rect.bottom+4,window.innerHeight-50),trigger})
    setProjectMenu(null);setMainMenuOpen(false)
  }

  // Toggle for the Run triggers that always target the active Project (mobile
  // toolbar, desktop header, collapsed rail): a second click collapses what the
  // first opened. The menu's scrim sits above all of them, so on touch a second
  // tap dismisses through the scrim and the click then lands on the trigger the
  // scrim was covering — a click right after a dismissal is that toggle closing,
  // not a fresh open. Sidebar project rows keep the plain open: clicking another
  // Project's ▶ while a menu is up should switch to it, never just close.
  const toggleRunMenu=(project:Project,element:HTMLElement,trigger='project-run')=>{
    if((runMenu?.project.id===project.id&&runMenu.trigger===trigger)||Date.now()-runMenuClosedAt.current<350){setRunMenu(null);return}
    openRunMenu(project,element,trigger)
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
    setGrantsCatalogue(null)
    setProjectCreateOpen(true)
    try{
      const config=await api<{project_init_scripts?:InitScript[]}>('GET','/api/config')
      const scripts=config.project_init_scripts||[]
      setInitScripts(scripts)
      setProjectCreate(value=>({...value,scripts:defaultInitScriptSelection(scripts)}))
    }catch{/* the dialog still registers a Project without its optional setup commands */}
    try{
      setGrantsCatalogue(await api<GrantsCatalogue>('GET','/api/grants'))
    }catch{/* the checkboxes still render; submit refetches the catalogue it needs */}
  }

  const openProjectsManager=(focus?:{project:Project;setting?:string})=>{
    setProjectsManagerFocus(focus?{projectId:focus.project.id,setting:focus.setting}:null)
    setRevealToken(token=>token+1)
    setProjectsManagerOpen(true);setMainMenuOpen(false);setSidebarMenu(null);setProjectMenu(null)
  }

  /**
   * Route a deep link from a gated surface to the switch that ungates it.
   *
   * Every "this is off — turn it on" control in the app arrives here, because opening one of
   * the two overlays that own switches means closing whichever other one is up, and only
   * this component knows which that is. The overlay then scrolls to the control and flashes
   * it (`settingReveal.ts`); this function's whole job is choosing the overlay and handing it
   * the id.
   *
   * A Project target with no Project is the one refusal: naming the switch and then opening a
   * registry on some other Project's row would be worse than saying so.
   */
  const openSettingTarget=(id:SettingTargetId,requestedProject?:string)=>{
    const target=settingTarget(id)
    setContextMenu(null);setProjectMenu(null);setSidebarMenu(null);setMainMenuOpen(false)
    if(mobileWorkspace)setClipboardOpen(false)
    if(target.surface==='automation'){
      const owner=id.startsWith('project.')?projects.find(project=>project.id===(requestedProject||projectId)):undefined
      if(id.startsWith('project.')&&!owner){setError('Select a Project first - that policy belongs to one Project.');return}
      setSettingsOpen(false);setResourcesOpen(null);setProjectsManagerOpen(false)
      // Policy is the one editing view now: install switches, the matrix, and
      // the limits drawer all live on it, and the reveal walks to the mark.
      openAutomation('policy',owner?.id,target.setting)
      return
    }
    if(target.surface==='project'){
      const owner=projects.find(project=>project.id===(requestedProject||projectId))
      if(!owner){setError('Select a Project first — that switch belongs to one Project.');return}
      setSettingsOpen(false);setResourcesOpen(null);setAutomationOpen(null)
      openProjectsManager({project:owner,setting:target.setting})
      return
    }
    setResourcesOpen(null);setAutomationOpen(null);setProjectsManagerOpen(false)
    openSettings(target.section,target.setting)
  }

  useEffect(()=>{
    const onOpenSetting=(event:Event)=>{
      const detail=(event as CustomEvent<OpenSettingDetail>).detail
      if(detail?.target)openSettingTarget(detail.target,detail.projectId)
    }
    window.addEventListener(OPEN_SETTING_EVENT,onOpenSetting)
    return()=>window.removeEventListener(OPEN_SETTING_EVENT,onOpenSetting)
  })

  // Which of the two first-run surfaces may be on screen. One decision, one owner.
  const firstRun=firstRunSurface({tutorialArmed:tutorialOpen,configResolved:firstRunResolved,harnessSetupNeeded,settingsOpen})
  const closeTutorial=()=>{
    completeTutorial()
    setTutorialOpen(false)
  }
  const startTutorial=()=>{
    // Help is where the tour is offered from, and the tour coaches over the live app
    // rather than over a modal, so the modal has to go before the walk starts.
    setHelpTopicOpen(null)
    resetTutorial()
    setTutorialOpen(true)
  }
  /** Open help on one topic, or on the index with `''`. */
  const openHelp=(topic:string)=>{setMainMenuOpen(false);setPaletteOpen(false);setHelpTopicOpen(topic)}
  const navigateTutorial=(step:TutorialStepId)=>{
    if(step!=='feature-menu')setMainMenuOpen(false)
    if(step!=='run-choice')setRunMenu(null)
    setContextMenu(null);setProjectMenu(null);setSidebarMenu(null);setTabMenu(null);setNoteMenu(null)
    // One table (`mobileTutorialChrome`) decides which collapsed panel a step's anchor is
    // behind on a phone, so a step cannot be added to the walk and forgotten here - which
    // is how `resources` came to open the navigation sidebar, a panel that has never
    // carried its Notes anchor, and stranded the tour at step 10 of 14 with only Exit.
    // The two panels are mutually exclusive on a phone rather than merely both openable:
    // the side panel is an overlay at z-60 over a z-59 scrim and the navigation sidebar is
    // at z-46, so a sidebar anchor under an open side panel is spotlit and unclickable.
    // A step naming one therefore shuts the other; a step naming neither leaves both as
    // the user left them, so the Notes panel they were just asked to open survives the
    // explanatory step that follows.
    const revealMobileChrome=(id:TutorialStepId)=>{
      if(!mobileWorkspace)return
      const chrome=mobileTutorialChrome(id)
      if(chrome==='sidebar'){setClipboardOpen(false);setSidebarOpen(true)}
      // Never `showDrawerTab('notes')`: the step asks the user to open Notes, and
      // pre-selecting it would answer its own question.
      if(chrome==='side-panel'){setSidebarOpen(false);setClipboardOpen(true)}
    }
    if(step==='welcome'||step==='projects'){
      setSettingsOpen(false);setProjectsManagerOpen(false);setProjectCreateOpen(false);setFolderPickerOpen(false)
      revealMobileChrome(step)
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
    if(['run','run-choice','workspace','new-tab','tabs','splits','resources','gates','features','feature-menu','configurator','ready'].includes(step)){
      setSettingsOpen(false);setProjectsManagerOpen(false);setProjectCreateOpen(false);setFolderPickerOpen(false)
      const first=projectsRef.current[0]
      if(first&&!projectsRef.current.some(project=>project.id===projectId))setProjectId(first.id)
      revealMobileChrome(step)
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
    // Land in the new Project's workspace, not on the registry that happens to be behind
    // this dialog (operator decision 2026-08-22). The sidebar's `+` opens Manage projects
    // as a backdrop for the create form, so submitting used to reveal the settings editor
    // for a Project nobody has looked at yet - a configuration screen offered before the
    // thing being configured has been seen. The registry is one click away from the
    // sidebar for anyone who actually wants it, and the tutorial's own `project-open` step
    // reopens it by name. On a phone the sidebar closes too, or the Project is selected
    // behind a panel covering the workspace it just switched to.
    setProjectsManagerOpen(false);setProjectsManagerFocus(null);setSidebarOpen(false)
    emitTutorialAction({action:'project-created'})
    // The ticked starting sets, through the ordinary grant path so they leave the same
    // audit record as a gate press. One POST for the union rather than one per
    // checkbox: the daemon computes the dependency closure and writes the Project file
    // once, so there is one revision and no half-applied state. After the registration
    // and never before it: a Project that exists with nothing opted in is a normal
    // state, and one that failed to register has nothing to opt in. `restored` is
    // skipped because that Project already has whatever table it was registered with.
    const wantsStartingSets=projectCreate.automations||projectCreate.llm||projectCreate.autonomy
    if(wantsStartingSets&&!(next as Project&{restored?:boolean}).restored){
      try{
        const catalogue=grantsCatalogue??await api<GrantsCatalogue>('GET','/api/grants')
        // A set the ceiling blocks was greyed on the form; strip it here too so
        // a stale draft flag cannot turn one grant refusal into losing all three.
        const selection=selectedStartingSets({
          ...projectCreate,
          automations:projectCreate.automations&&!startingSetBlocked('recommended'),
          llm:projectCreate.llm&&!startingSetBlocked('llm'),
          autonomy:projectCreate.autonomy&&!startingSetBlocked('autonomy'),
        },catalogue.project_starting_sets)
        if(selection.automations.length||Object.keys(selection.values).length){
          await api('POST','/api/grants',{
            project_id:next.id,
            automations:selection.automations,
            values:selection.values,
          })
          forgetProjectAutomations(next.id)
        }
      }catch(cause){
        // Reported, never unwound: the Project is registered and usable, and every one
        // of these switches is reachable from the surface that needs it.
        setError(`The Project was created; its starting features were not turned on (${cause instanceof Error?cause.message:String(cause)}). Turn them on from the Projects registry or any Activity tab.`)
      }
    }
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

  /** Move one Project into a Group (or back to the root list), from the Project menu.
   *
   *  Closes the menu first: the submenu it is invoked from renders the Project's *current*
   *  Group in its header, so leaving it open would show the old answer until the PATCH
   *  landed and then silently change under the pointer. */
  const assignProjectGroup=async(project:Project,group_id:string|null)=>{
    setProjectMenu(null)
    try{
      const updated=await api<Project>('PATCH',`/api/projects/${project.id}`,{group_id})
      setProjects(items=>items.map(item=>item.id===updated.id?updated:item))
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
      // Opened from a Project's Group submenu: the Project goes in. A failure to move is
      // reported and leaves the (successfully created) Group in place rather than trying
      // to unwind it — the sidebar then shows exactly what the daemon holds.
      const adopt=groupEdit.adoptProjectId?projects.find(item=>item.id===groupEdit.adoptProjectId):undefined
      if(adopt)await assignProjectGroup(adopt,created.id)
    }
    setGroupEdit(null)
  }

  /** Dissolve a Group, returning its Projects to the root list.
   *
   *  Its only caller is the Group menu's second, confirming click. The header carried a `×`
   *  for this once, a pixel from the fold toggle, and losing it left no delete path at all:
   *  emptying a Group by reassigning every Project one at a time still left the empty Group
   *  on screen, since empty Groups render.
   *
   *  Deleting a Group is a registry write and nothing more — the daemon ungroups its
   *  Projects and renumbers the remaining Groups, and no folder, session, layout, or
   *  history is touched. The optimistic update mirrors exactly that, and a failure re-reads
   *  the registry rather than trying to reconstruct what the server did or did not do. Fold
   *  state for the id is dropped by the prune effect, which this state change triggers. */
  const deleteGroup=async(group:ProjectGroup)=>{
    setGroupMenu(null);setConfirmGroupDeleteId(null)
    setProjectGroups(items=>items.filter(item=>item.id!==group.id))
    setProjects(items=>items.map(item=>item.group_id===group.id?{...item,group_id:null}:item))
    try{
      await api<{ok:boolean}>('DELETE',`/api/project-groups/${group.id}`)
    }catch(cause){
      await refresh()
      setError(cause instanceof Error?cause.message:String(cause))
    }
  }

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

  // Setting the mode from the palette or the context menu, for the same reason
  // the strip exists in the pane: a control whose only home is one disclosed
  // line is one you have to be looking at the right pane to reach. Revoking to
  // `wait` is deliberately always offered and never refused - taking authority
  // back must not depend on the install switch, the Project ceiling, or the
  // conversation still being the one the grant was made against.
  const setApprovalMode = async (session: Session, mode: ApprovalMode) => {
    setContextMenu(null)
    try {
      await api('PUT', `/api/sessions/${session.id}/approvals`, { mode, set_by: 'palette' })
      window.dispatchEvent(
        new CustomEvent('mux:approvals-changed', { detail: { sessionId: session.id } }),
      )
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  // The one-shot. Answers the request the focused session is showing, once, and
  // is not a mode: the server re-checks the agent run, the screen classification,
  // and the prompt fingerprint before it writes anything.
  const approvePendingRequest = async (session: Session) => {
    setContextMenu(null)
    try {
      const result = await api<{ operation: string }>(
        'POST', `/api/sessions/${session.id}/approvals/approve-once`,
      )
      return result.operation
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      return null
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
  // It sticks for that visit and no longer: leaving the pane releases the pin,
  // so coming back to read the marked session clears it like any other pane.
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
    setSidebarOrder(setAllBucketsCollapsed(sidebarOrder,displayBuckets.map(bucket=>bucket.id),folded))
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
    // Read from the layout *before* the leaf came out: a stack that held only this
    // session no longer exists in `nextLayout`, and one that survives has already
    // forgotten which of its tabs the kill was about.
    const paneIds = stackForView(currentLayout, session.id)?.children.map(child => child.id) || []
    const nextActiveId = nextActiveAfterKill({
      layout: nextLayout, sessions, killedId: session.id,
      projectId: session.project_id, activeId,
      recent: recentFocusedSessions(sessionFocusHistory.current, session.project_id),
      paneIds,
    })
    // Dropped before the focus is handed on, so the recording effect below cannot see
    // the killed id at the head of the stack and re-offer it to the next close.
    sessionFocusHistory.current = forgetFocusedSession(sessionFocusHistory.current, session.id)
    if (nextActiveId && terminalIds(nextLayout).includes(nextActiveId)) {
      nextLayout = activateContainingStack(nextLayout, nextActiveId)
    }
    pendingKills.current[session.id] = {
      sessionId: session.id, projectId: session.project_id, startedAt: Date.now(),
    }
    setSessions(items => items.filter(item => item.id !== session.id))
    delete startupOrigins.current[session.id]
    delete clientStartupTimingValues.current[session.id]
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

  /**
   * Clear every ended row in one Project off the sidebar at once.
   *
   * The single-row remove already skips confirmation, because an ended session has no
   * process to interrupt and no turn left to lose - and that argument does not weaken
   * when there are nine of them. What does not survive repetition is `killNow` in a
   * loop: it writes the layout and re-reads the whole fleet once per session, so nine
   * dead rows would be nine layout PATCHes racing each other's revision and nine
   * refreshes, with the sidebar re-sorting under the pointer between each. So the sweep
   * does what one kill does, once - one optimistic removal, one layout write, one
   * refresh - with the DELETEs going out together underneath.
   */
  const clearEndedSessions = async (targetProjectId: string) => {
    const ended = clearableEndedSessions(sessions, targetProjectId, pendingKills.current)
    if (ended.length === 0) return
    setConfirmKillId(null)
    setContextMenu(null)
    const endedIds = new Set(ended.map(session => session.id))
    const currentLayout = resolveLayout(
      layoutMap[targetProjectId],
      projects.find(project => project.id === targetProjectId)?.layout,
    )
    let nextLayout = currentLayout
    for (const session of ended) nextLayout = removeLeaf(nextLayout, 'terminal', session.id)
    // Focus only has to move when the sweep takes the session holding it, and one
    // `nextActiveAfterKill` answers for the whole batch rather than one call per
    // session: it already refuses every exited or crashed id as a landing spot, and the
    // layout it reads has all of their leaves out, so no member of the batch can be
    // chosen as the successor to another.
    const focusedOut = activeId && endedIds.has(activeId) ? activeId : null
    const paneIds = focusedOut
      ? stackForView(currentLayout, focusedOut)?.children.map(child => child.id) || []
      : []
    const nextActiveId = focusedOut
      ? nextActiveAfterKill({
        layout: nextLayout, sessions, killedId: focusedOut,
        projectId: targetProjectId, activeId,
        recent: recentFocusedSessions(sessionFocusHistory.current, targetProjectId),
        paneIds,
      })
      : activeId
    for (const session of ended) {
      sessionFocusHistory.current = forgetFocusedSession(sessionFocusHistory.current, session.id)
      pendingKills.current[session.id] = {
        sessionId: session.id, projectId: targetProjectId, startedAt: Date.now(),
      }
    }
    setSessions(items => items.filter(item => !endedIds.has(item.id)))
    for (const session of ended) {
      delete startupOrigins.current[session.id]
      delete clientStartupTimingValues.current[session.id]
    }
    if (activeId && endedIds.has(activeId)) setActiveId(nextActiveId)
    if (focusedViewId && endedIds.has(focusedViewId)) setFocusedViewId(nextActiveId)
    if (zoomedId && endedIds.has(zoomedId)) setZoomedId(null)
    layoutValues.current[targetProjectId] = nextLayout
    setLayoutMap(current => ({ ...current, [targetProjectId]: nextLayout }))
    const removed: Session[] = []
    const failures: string[] = []
    try {
      await Promise.all(ended.map(async session => {
        try {
          await deleteSessionOnce(session.id)
          removed.push(session)
        } catch (cause) {
          const reason = cause instanceof Error ? cause.message : String(cause)
          failures.push(`${sessionName(session)}: ${reason}`)
        }
      }))
    } finally {
      for (const session of ended) delete pendingKills.current[session.id]
    }
    // The single kill's rule, applied per session: persist the leaf removal only for the
    // ones the daemon agreed are gone. A failed DELETE keeps its leaf in the written
    // layout, so the refresh below finds that session still there and puts its row back
    // where it was rather than into a pane the layout no longer holds it in.
    if (removed.length > 0) {
      let settled = layoutValues.current[targetProjectId] ?? nextLayout
      for (const session of removed) settled = removeLeaf(settled, 'terminal', session.id)
      await updateLayout(targetProjectId, settled)
    }
    // One toast for the batch, naming the first failure. A sweep of nine that loses one
    // is still eight rows cleared, and nine stacked errors would bury which one it was.
    if (failures.length > 0) {
      setError(failures.length === ended.length
        ? `Could not remove the ended sessions: ${failures[0]}`
        : `Removed ${removed.length} of ${ended.length} ended sessions - ${failures[0]}`)
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

  const standDownSession = async (session: Session) => {
    try {
      const inactive = await api<Session>('POST', `/api/sessions/${session.id}/stand-down`, {})
      updateSession(inactive)
      setContextMenu(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  const requestKill = (session: Session) => {
    // Confirmation guards against destroying work, and an ended session has
    // none left to destroy: there is no process to interrupt and no turn to
    // lose, only a record the operator is finished reading. Making them click
    // twice to clear a dead row is friction with nothing behind it.
    if (confirmKillId === session.id || isEndedSession(session)) void killNow(session)
    else setConfirmKillId(session.id)
  }

  // `quiet` suppresses only the failure toast, never the reload behind it. It exists for writes
  // the operator did not make - the automatic join in `refresh` - where a lost revision race is
  // the mechanism working rather than news: the refresh that follows re-derives from whatever
  // the winner persisted.
  const updateLayout = layoutWriter.write

  // Disabling Scratchpad removes its view from every Project layout but never deletes the
  // global note file. Re-enabling restores the Notes-rail entry with its previous content.
  useEffect(()=>{
    if(scratchpadEnabled)return
    for(const project of projects){
      const current=resolveLayout(layoutMap[project.id],project.layout)
      if(!leaves(current,'note').some(leaf=>leaf.id===SCRATCHPAD_TAB_ID))continue
      void updateLayout(project.id,removeLeaf(current,'note',SCRATCHPAD_TAB_ID))
    }
  },[scratchpadEnabled,projects,layoutMap])

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
    setProcessSession(session);setProcessScope(scope);setResourcesOpen('processes')
    setContextMenu(null);setSidebarMenu(null);setMainMenuOpen(false);setProjectMenu(null)
  }
  /** Open the Resources dialog on one of its other segments. */
  const openResources=(segment:ResourceSegment)=>{
    setResourcesOpen(segment)
    setContextMenu(null);setSidebarMenu(null);setMainMenuOpen(false);setProjectMenu(null)
  }
  /** Open the Usage dialog on one of its segments. Separate from `openResources` because
   *  they are separate dialogs: spend is a retrospective question asked of a ledger, and
   *  the meters beside Processes go stale in seconds. */
  const openUsage=(segment:UsageSegment)=>{
    setUsageOpen(segment)
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

  /** Anything that counts as still using the filter, which restarts its idle clock:
   *  a keystroke, an arrow, the pointer crossing the results, activating a row. */
  const touchSidebarSearch=()=>{sidebarSearchTouchedAt.current=Date.now()}
  const openSidebarSearch=()=>{
    touchSidebarSearch()
    setSidebarSearchCursor(NO_SEARCH_CURSOR)
    setSidebarSearchOpen(true)
  }
  /** Close and clear in one act. The filter holds nothing worth restoring: reopening
   *  it onto a stale query would filter the tree by something typed minutes ago. */
  const closeSidebarSearch=()=>{
    setSidebarSearchOpen(false)
    setSidebarSearchInput('')
    setSidebarSearchQuery('')
    setSidebarSearchCursor(NO_SEARCH_CURSOR)
  }
  /** The palette/keybinding entry point, which has to bring the sidebar with it: the
   *  filter is chrome inside a column that is a hidden overlay on a phone and can be
   *  collapsed to a rail on the desktop, and focusing an input nobody can see is the
   *  worst of both. */
  const toggleSidebarSearch=()=>{
    if(sidebarSearchOpen){closeSidebarSearch();return}
    setNavigationSidebarOpen(true)
    openSidebarSearch()
  }

  /** Show one session's prompt queue, which lives in the drawer's Queue tab.
   *
   *  Focuses the target first: the tab is session-scoped and follows focus, so a chip
   *  clicked on an unfocused pane would otherwise open the queue of a different agent
   *  than the one the click named.
   *
   *  `compose` separates the two reasons the Queue tab ever opens, which were one call
   *  before. A person pressing the queue chip or the palette command is *about to write*,
   *  so the composer earns focus. A send that came back `queued_behind` or `not_due` opens
   *  the same tab to say "your message is in there" — a reveal, not an invitation, and
   *  focusing the composer for it put a caret (and, on a phone, the whole soft keyboard)
   *  over the list the user was being shown. */
  const openQueueForSession = async (sessionId: string, compose = true) => {
    const session = sessionsRef.current.find(item => item.id === sessionId)
    if (session) await selectSession(session)
    openDrawerTab('queue',session?.project_id||projectId)
    if (compose) setQueueOpenToken(current => current + 1)
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

  /** Pop one session's change map out into a workspace tab.
   *
   *  Mirrors `openQueueTab`, and for a sharper reason than the queue had: the map is a
   *  force-directed graph, and the drawer column is the narrowest surface in the app.
   *  Nothing creates one implicitly — the drawer tab stays the primary home. */
  const openChangeMapTab = async (sessionId: string) => {
    const session = sessionsRef.current.find(item => item.id === sessionId)
    const targetProject = session?.project_id || projectId
    if (!targetProject) return
    const current = resolveLayout(layoutMap[targetProject], projects.find(project => project.id === targetProject)?.layout)
    const resourceId = changeMapLeafId(sessionId)
    const focused = openAnchorId(current, sessionId)
    setProjectId(targetProject)
    setFocusedViewId(resourceId)
    setContextMenu(null); setTabMenu(null); setMainMenuOpen(false)
    await updateLayout(targetProject, openTab(current, focused, resourceLeaf('changemap', resourceId)))
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
        // Filling a composer is an insert, not a delivery, so it does not pass the
        // queue's readiness gate — and therefore has to refuse a dialog itself.
        const refusal=insertionRefusal(target.session)
        if(refusal)return{status:'error',error:refusal}
        await api('POST',`/api/sessions/${sid}/input`,{data:composerInsertion(message,target.session.backend)})
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
          // Strict order: the message waits in the one audited place. Show it — the
          // message is already written, so this reveals the queue rather than asking
          // for another one.
          await openQueueForSession(sid, false)
          return { status: 'done' }
        case 'blocked':
          return { status: 'blocked', messageId, revision, reasons: outcome.reasons, protected: outcome.protected }
        case 'not_due':
          // A scheduled item reached this path (retarget/confirm of an
          // existing message): it is queued, just not yet due. Same reveal.
          await openQueueForSession(sid, false)
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
    setHistoryEntry('')
    setHistoryOpen(true)
    setMainMenuOpen(false);setProjectMenu(null)
  }

  /** Open History on one conversation, which is where a session that has ended lives.
   *
   *  Unscoped on purpose: the row being opened is named by its id, and pre-filtering to a
   *  Project would hide it whenever the conversation belongs to another one - the Git tab
   *  can name a session from a worktree that is no longer in this Project's scope. */
  const showHistoryEntry = (historyId:string) => {
    setHistoryScope('')
    setHistoryEntry(historyId)
    setHistoryOpen(true)
    setMainMenuOpen(false);setProjectMenu(null)
  }

  const openHandoff = async (entry:HistoryEntry) => {
    try {
      const result=await api<{markdown:string}>('GET',`/api/history/${entry.id}/handoff`)
      setHandoffState({entry,markdown:result.markdown,message:'Review this export before using it as agent context.'})
    } catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
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

  // "Resume later…": author a schedule that reopens this conversation, seeded from the
  // conversation itself. The Schedule tab owns everything about *when*; it cannot find
  // a conversation, which is why the seed is made here, where one is in hand.
  //
  // The two entry points seed different kinds deliberately. A History row is a
  // conversation somebody is reading, so it pins that one. A live pane is work in
  // progress whose conversation will keep moving - a rollover, a resume - so it follows
  // the work rather than freezing today's row.
  const scheduleResume = (
    seed: { run_id: string; label: string; backend: string; kind: ScheduleTargetKind },
    targetProject: string,
  ) => {
    const owner = targetProject || projectId
    if (!owner) { setError('A schedule belongs to a Project, and this conversation has none.'); return }
    setScheduleScope(owner)
    setScheduleSeed(resumeDraft(seed))
    setHistoryOpen(false)
    setContextMenu(null)
    openDrawerTab('schedule', owner)
    // Same reason the History resume closes these: the drawer is behind a full-screen
    // overlay and, on a phone, a side panel too.
    setSidebarOpen(false)
  }

  const scheduleResumeFromHistory = (entry: HistoryEntry) => scheduleResume(
    { run_id: entry.id, label: historyName(entry), backend: entry.backend, kind: 'run' },
    entry.project_id || projectId,
  )

  // A pane's own run, not its session id: a pane that rolled its conversation or
  // inherited one owns a History row keyed by the run, and a session id finds nothing.
  const scheduleResumeFromSession = (session: Session) => scheduleResume(
    {
      run_id: session.agent_run_id || session.id,
      label: sessionDisplayName(session),
      backend: session.backend,
      kind: 'latest_of_session',
    },
    session.project_id,
  )

  // Fork a conversation into a sibling pane. The daemon writes the forked
  // conversation itself (`transcript_fork`) or asks the CLI for a child thread,
  // attaches the new pane, and returns the new session; refresh() re-syncs the
  // server-updated layout. The source pane is never touched either way.
  //
  // A `before` cut hands back the prompt it excluded. It is staged rather than typed:
  // the pane it belongs to is still spawning, and it claims the text once its replay
  // finishes (`branchSeed.ts`).
  const runBranch = async (session: Session, request: BranchRequest = {}): Promise<string> => {
    try {
      const result = await api<{ session: Session; source: string; seed_text: string | null }>('POST', `/api/sessions/${session.id}/branch`, { target_session_id: session.id, direction: 'after', ...request })
      stageBranchSeed(result.session.id, result.seed_text)
      markProjectRecent(result.session.project_id)
      setSessions(items => [...items, result.session]); setActiveId(result.session.id); requestFocusView(result.session.id)
      await refresh()
      return ''
    } catch (cause) { return cause instanceof Error ? cause.message : String(cause) }
  }

  // A harness that honours a chosen point gets the picker; one that can only fork from
  // where the conversation stands now branches on the click, because a picker there
  // would offer a choice the daemon then refuses.
  const branchSession = async (session: Session) => {
    if (branchesFromMessage(session.backend)) { setBranchPickerId(session.id); return }
    const failure = await runBranch(session)
    if (failure) setError(failure)
  }

  // A retained pane resumes in place. The session route delegates agent work to the
  // shared History resume authority, proves the replacement, swaps the layout identity,
  // then removes the dead row. History's own Resume stays additive because it may name
  // a conversation with no retained pane to replace.
  const resumeSession = async (session: Session) => {
    try {
      const { session: resumed } = await api<{session: Session; replaced: string}>(
        'POST', `/api/sessions/${session.id}/resume`, {},
      )
      markProjectRecent(resumed.project_id)
      startupOrigins.current[resumed.id] = performance.now()
      setSessions(items => [...items.filter(item => item.id !== session.id && item.id !== resumed.id), resumed])
      setProjectId(resumed.project_id)
      setActiveId(resumed.id)
      requestFocusView(resumed.id)
      if (zoomedId === session.id) setZoomedId(resumed.id)
      setContextMenu(null)
      setSidebarOpen(false)
      await refresh()
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
  }

  // Delegated rather than reimplemented: the sidebar row and the tab strip resolve
  // the same question for every session at once (`voiceMode.ts`), and a second copy
  // here is how the pane and the list end up disagreeing about which sessions speak.
  const effectiveVoiceMode = (session: Session): VoiceMode => resolveVoiceMode(session, voiceStatus)
  /**
   * The mode a tab strip should mark, or `off` when it must not mark one.
   *
   * The agent check is not decoration: with a global default of `auto`, a shell would
   * resolve to `auto` too and wear a speaker it can never use — read aloud reads an agent
   * transcript. The `voice` row field applies the same rule, and both must, because they
   * draw the same mark for the same session on one screen.
   */
  const tabVoiceMode = (session: Session | undefined): VoiceMode =>
    session && isAgent(session) && isFieldPlaced(rowConfig, 'voice') ? effectiveVoiceMode(session) : 'off'
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
  const effectiveVoiceContent = (session: Session): VoiceContent =>
    session.voice_content === 'summary' || session.voice_content === 'verbatim'
      ? session.voice_content
      : (voiceStatus?.content || 'verbatim')
  const setVoiceContent = (session: Session, content: VoiceContent) =>
    api<Session>('PATCH', `/api/sessions/${session.id}`, { voice_content: content })
      .then(updateSession)
      .catch(cause => setError(cause instanceof Error ? cause.message : String(cause)))
  // Read aloud's own panel, mounted once by App for the same reason as the assistant
  // view: it holds the clip list it has fetched and its subscription to clip events, so
  // a remount on every tab switch would refetch the whole list to render the same rows.
  // The session it reports on is the same focused agent `setPlaybackFocus` uses, so what
  // the panel offers to change is what this device would actually speak.
  const readView = <VoiceReadTab
    variant={voiceBodyVariant(voiceDock.state, voiceBody, 'read')}
    status={voiceStatus}
    session={focusedAgentSession}
    mode={focusedAgentSession ? effectiveVoiceMode(focusedAgentSession) : 'off'}
    content={focusedAgentSession ? effectiveVoiceContent(focusedAgentSession) : (voiceStatus?.content || 'verbatim')}
    onMode={mode => { if (focusedAgentSession) void setVoiceMode(focusedAgentSession, mode) }}
    onContent={content => { if (focusedAgentSession) void setVoiceContent(focusedAgentSession, content) }}
    onOpenSettings={() => openSettings('Voice')}
    onStatusChanged={() => { void loadVoiceStatus() }}
  />
  const speakLastReply = async (session: Session) => {
    unlockPlayback()
    // Claimed before the request, so the segments of a reply too long for one clip
    // are accepted as they are synthesized instead of being dropped on arrival.
    const streamId = newVoiceStreamId()
    beginRequestedStream(streamId, session.id, 'agent')
    try {
      const clip = await api<VoiceClip>(
        'POST', `/api/sessions/${session.id}/voice/generate`, { stream_id: streamId })
      if (clip?.id) void playClipGroup(clip, session.id).catch(() => {})
      else cancelRequestedStream(streamId)
    } catch (cause) {
      cancelRequestedStream(streamId)
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  const commandSession = contextMenu?.session || active
  const commandProject = projectMenu?.project || activeProject
  // Which Project a "clear the ended rows" sweep acts on, and how many rows that is.
  // The selected session's Project rather than the workspace's: the sidebar draws every
  // Project's rows at once, so the menu can be opened on a row belonging to a Project that
  // is not the one on screen, and sweeping that other Project would clear rows the operator
  // was not pointing at. Falls back to the workspace's Project for the palette, which has
  // no row under a pointer to read.
  const clearEndedTarget = commandSession?.project_id || projectId
  const clearEndedCount = clearableEndedSessions(sessions, clearEndedTarget, pendingKills.current).length
  // The focused tab, when it is a file tab holding a previewable document. Derived rather
  // than tracked: the file identity is already encoded in the leaf id, and a second copy of
  // "which file is focused" is one more thing that can disagree with the layout.
  const focusedPreviewableFile = (() => {
    const identity = focusedViewId ? parseNoteResourceId(focusedViewId) : null
    if (!identity || (identity.kind !== 'file' && identity.kind !== 'worktree-file')) return null
    if (!isPreviewableDocument(identity.id)) return null
    return { path: identity.id, worktree: identity.kind === 'worktree-file' ? identity.worktree : undefined }
  })()
  // Cycle the mobile unified tab strip. Recomputes the projection from live layout
  // state so it works when invoked from a gesture, outside the render-time `mobileProjection`.
  // Short label for a projected mobile tab; also what the swipe HUD announces.
  const queueTabLabel = (resourceId: string): string => {
    const targetSessionId = queueLeafSessionId(resourceId)
    const owner = targetSessionId ? sessions.find(item => item.id === targetSessionId) : undefined
    return owner ? `Queue · ${sessionName(owner)}` : 'Queue'
  }

  const changeMapTabLabel = (resourceId: string): string => {
    const targetSessionId = changeMapLeafSessionId(resourceId)
    const owner = targetSessionId ? sessions.find(item => item.id === targetSessionId) : undefined
    return owner ? `Map · ${sessionName(owner)}` : 'Change Map'
  }

  const mobileTabLabel = (leaf: PaneLeaf): string => {
    if (leaf.kind === 'terminal') { const session = sessions.find(item => item.id === leaf.id); return session ? sessionName(session) : leaf.id }
    if (leaf.kind === 'preview') { const preview = previews[leaf.id]; return preview ? previewLabel(preview) : leaf.id }
    if (leaf.kind === 'history') return 'History'
    if (leaf.kind === 'queue') return queueTabLabel(leaf.id)
    if (leaf.kind === 'changemap') return changeMapTabLabel(leaf.id)
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

  // The workspace half of the navigation rung of back. Assigned every render and read
  // through the ref, because the back target below is built once - it lives on a window
  // listener for the whole session - while everything a traversal needs to answer (the
  // live layouts, which Projects still exist, whether this is the mobile layout) moves
  // underneath it.
  const backNavigator = useRef<ViewNavigator>({ enabled: () => false, alive: () => false, go: () => undefined })
  const projectLayoutFor = (id:string):PaneLayout|null => {
    const project = projects.find(item => item.id === id)
    return project ? resolveLayout(layoutValues.current[id] || layoutMap[id], project.layout) : null
  }
  backNavigator.current = {
    // Mobile only. On the desktop the tabs are on screen and one click away, and holding
    // the history sentinel armed permanently would stop the browser's own Back button
    // from ever leaving the site - the same reason the docked sidebar and drawer are not
    // dismiss levels there.
    enabled: () => mobileWorkspace && viewBack,
    alive: (entry:ViewEntry) => {
      const layout = projectLayoutFor(entry.projectId)
      return !!layout && leaves(layout).some(leaf => leaf.id === entry.viewId)
    },
    go: (entry:ViewEntry) => {
      const project = projects.find(item => item.id === entry.projectId)
      const layout = projectLayoutFor(entry.projectId)
      const leaf = layout ? leaves(layout).find(item => item.id === entry.viewId) : null
      if (!project || !layout || !leaf) return
      // Same acknowledgement a swipe gets, and for the same reason: a tab change the eye
      // misses is indistinguishable from "back did nothing", and the user presses again
      // and leaves the app. Named across Projects when the traversal crosses one.
      const label = mobileTabLabel(leaf)
      showInteractionHud(entry.projectId === projectId ? label : `${project.name} · ${label}`)
      // No panel to close on the way: a slide-in panel is a dismiss level, so back would
      // have spent this press on it before ever reaching the ring.
      if (entry.projectId !== projectId) setProjectId(entry.projectId)
      setFocusedViewId(leaf.id)
      if (leaf.kind === 'terminal') setActiveId(leaf.id)
      const pane = stackForView(layout, leaf.id)
      if (pane && pane.active_child_id !== leaf.id) void updateLayout(entry.projectId, activateStackChild(layout, pane.id, leaf.id))
    },
  }
  // Built once so `nav.back`, the back swipe, and the platform gesture are literally the
  // same step. Escape deliberately does not go through it: with nothing open, Escape
  // belongs to the terminal, and stepping tabs from inside an agent's TUI is exactly the
  // side effect the flat Escape handlers were replaced to stop.
  const backTarget = useMemo(() => composeBackTarget(dismissStack, viewHistory, {
    enabled: () => backNavigator.current.enabled(),
    alive: entry => backNavigator.current.alive(entry),
    go: entry => backNavigator.current.go(entry),
  }), [])
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
    // Fetched as the dialog opens rather than passed in: this is the one moment
    // the answer can change what the user does, and it is cheap (the daemon reads
    // its in-memory registry). A failure leaves the dialog saying nothing extra,
    // which is the old behaviour.
    setRedeployInterruptions(null)
    setRedeployHolders(null)
    void (async () => {
      try {
        const response = await fetch('/api/daemon/redeploy', { cache: 'no-store' })
        if (!response.ok) return
        const status = await response.json() as RedeployStatus
        setRedeployInterruptions(status.interrupted ?? null)
      } catch { /* advisory only */ }
    })()
    // A second request, because this one runs a seconds-long scan of every
    // process on the host and the line above must not wait behind it. It is
    // worth starting here twice over: it names a blocker *before* the operator
    // commits, instead of refusing after they have, and the accept then joins
    // this same scan rather than starting its own.
    void (async () => {
      try {
        const response = await fetch('/api/daemon/redeploy?holders=1', { cache: 'no-store' })
        if (!response.ok) return
        const status = await response.json() as RedeployStatus
        setRedeployHolders(status.bundle_holders ?? null)
      } catch { /* advisory only; the accept runs the same gate */ }
    })()
  }

  async function startRedeploy() {
    setRedeployConfirmOpen(false)
    // The chip goes up at the press, not at the 202. The daemon's preflight
    // walks the whole process table looking for a bundle holder (measured
    // 2.7-7.8s on the primary host), and reporting only afterwards left the
    // button reading as dead for that entire window. Nothing probes during
    // 'requested' - the lock a status read reports on is claimed by the very
    // request being awaited.
    const requestedAt = Date.now()
    setRedeploy(current => current.phase === 'idle' ? requestRedeploy(requestedAt) : current)
    // Direct fetch (not api()): 409 bodies carry a human-readable `message`
    // (no source checkout, uv missing, supervisor detached, already running).
    let accepted = false
    let alreadyRunning = false
    try {
      const response = await fetch('/api/daemon/redeploy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      })
      if (response.status === 202) accepted = true
      else {
        const detail = await response.json().catch(() => ({}))
        // Refused because one is already in flight: the chip is right, it just
        // belongs to somebody else's redeploy. Track that one rather than
        // taking the indicator away from a real outage that is coming.
        alreadyRunning = detail.error === 'redeploy_in_progress'
        setError(detail.message || detail.error || 'Redeploy request failed.')
      }
    } catch {
      setError('Redeploy request failed.')
    }
    // The daemon broadcasts the start to every client, this one included, so
    // the promotion here only covers the case where the broadcast is lost.
    // Entering 'building' rather than blocking is the point: the build takes
    // minutes during which this daemon keeps serving normally.
    if (accepted || alreadyRunning) enterRedeploy()
    else setRedeploy(current => abandonRequest(current, requestedAt))
  }

  // Entering is idempotent so the local start, the daemon's broadcast, and the
  // boot-time sentinel can all call it without racing each other into a second
  // wait loop or resetting the elapsed clock mid-redeploy.
  const enterRedeploy = () => setRedeploy(current => confirmRedeploy(current, Date.now()))

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
      const ran=runCommand(commandRegistryRef.current,`session.focus:${result.item.session.id}`)
      const address=voiceSessionAddress(result.item)
      // An ended session opens now — read-only — so the confirmation says what
      // was opened rather than refusing. Saying so out loud matters more here
      // than on screen: the pane's own banner is not something a hands-free
      // operator is looking at, and typing at a dead pane is the mistake.
      const ended=isEndedSession(result.item.session)
        ?isColdSession(result.item.session)?' It was recovered after a crash and is read-only.':' It has ended and is read-only.'
        :''
      const speech=ran==='ran'
        ?`Opened ${address?`Project ${address.projectNumber}, Session ${address.sessionNumber}, `:''}${sessionName(result.item.session)} in ${result.item.projectName}.${ended}`
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
      const streamId=newVoiceStreamId()
      beginRequestedStream(streamId,session.id,'agent')
      const body=query.mode==='current'?{stream_id:streamId}:{content_mode:query.mode as VoiceContent,stream_id:streamId}
      const clip=await api<VoiceClip>('POST',`/api/sessions/${session.id}/voice/generate`,body)
        .catch(cause=>{cancelRequestedStream(streamId);throw cause})
      await playClipGroup(clip,session.id)
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
  // Where a fleet command's work actually happens. Re-pointed every render and
  // reached through the stable facade below, so the memoized registry never runs a
  // handler built against a fleet snapshot the operator has already moved past.
  fleetCommandWork.current = {
    activateProject: projectId => {
      const layout=layoutMap[projectId]||emptyLayout()
      const first=leaves(layout)[0]||null
      setProjectId(projectId);setFocusedViewId(first?.id||null);setActiveId(terminalIds(layout)[0]||null)
    },
    focusProject: projectId => selectProject(projectId),
    focusSession: session => void selectSession(session),
    spawnSession: (projectId,backend,seedText) =>
      spawnTerminal(projectId,false,undefined,undefined,'after',backend,seedText===undefined?undefined:{seedText}),
  }
  // The fleet half of the registry: one command per numbered Project slot, per
  // Project, per live session, and per Project-and-harness launch pair, each with its
  // spoken phrases. Rebuilt when the fleet changes rather than on every render.
  // `displayProjects` and the harness registry are derived values with no stable
  // identity, so the dependencies are what *determines* them: the Project and session
  // records, the sidebar order as a value, and the registry revision counter.
  const fleetCommands = useMemo(
    () => buildFleetCommands({
      displayProjects, projects, sessions, activeProjectId: projectId,
      backends: allBackendNames(), harnessDisplayName, sessionName, isEnded: isEndedSession,
      actions: fleetCommandActions,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- see the note above
    [projects, sessions, projectId, displayOrderKey(displayProjects), harnessRegistryRevision, fleetCommandActions],
  )

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
    // Unconditionally available, and deliberately not gated on `backTarget.depth()`:
    // `available` is a render-time snapshot, but a drill-down level (History's transcript)
    // lives in its own component's state and opens without re-rendering App. A stale
    // `false` would make `runCommand` refuse the command and toast at exactly the moment
    // the user swiped back. `pop()` is already inert with nothing to go back to, which is
    // the same outcome without the lie. Subscribing App to the target instead would
    // re-render the whole shell on every overlay open for a greyed-out palette row.
    { id: 'nav.back', label: 'Back (close one overlay level, then step back a tab)', category: 'view', available: true, run: () => { backTarget.pop() } },
    { id: 'sidebar.close', label: 'Close navigation sidebar', category: 'view', available: true, run: () => setNavigationSidebarOpen(false), voice:{
      phrases:['close navigation','hide navigation','close navigation sidebar','hide navigation sidebar','close left sidebar','hide left sidebar'],
    } },
    { id: 'sidebar.toggle', label: 'Toggle navigation sidebar', category: 'view', available: true, run: () => setSidebarOpen(value => !value) },
    // Step through Projects in the order the sidebar draws them — `displayProjects`, the
    // same list the numbered `project.activate(N)` shortcuts and the collapsed rail follow,
    // so a sorted or grouped sidebar never disagrees with what "next" means. No wrap: the
    // ends of a list are information, and wrapping makes a repeated swipe a loop with no
    // way to tell you have arrived. Carries the top bar's horizontal swipe.
    { id: 'project.next', label: 'Focus the next Project (sidebar order)', category: 'project', available: adjacentProject(1) !== null, disabledReason: 'The last Project in the sidebar is already selected', run: () => { const next = adjacentProject(1); if (next) selectProject(next.id) }, voice:{
      phrases:['next project','go to the next project'],
    } },
    { id: 'project.previous', label: 'Focus the previous Project (sidebar order)', category: 'project', available: adjacentProject(-1) !== null, disabledReason: 'The first Project in the sidebar is already selected', run: () => { const previous = adjacentProject(-1); if (previous) selectProject(previous.id) }, voice:{
      phrases:['previous project','go to the previous project'],
    } },
    // The app menu, as a command rather than only a button. Its two triggers live in the
    // sidebar (the footer's `: menu`, and the collapsed rail's `:`), so on a phone the
    // menu was reachable only by pulling the sidebar in first — yet the menu itself is a
    // viewport-anchored overlay that never needed it. This is the door that skips that
    // trip, and being a registered command is what lets a gesture, a chord and the
    // palette all reach it. Toggling matches both buttons; note that on the gesture path
    // the touch's own `pointerdown` has already dismissed an open menu, so a swipe always
    // ends with it open.
    { id: 'menu.toggle', label: 'Toggle the swe-mux menu', category: 'view', available: true, run: () => setMainMenuOpen(value => !value), voice:{
      phrases:['open the menu','show the menu','open swe mux menu','open app menu'],
    } },
    // Brings the sidebar with it, because the filter is chrome inside a column that is
    // hidden on a phone and collapsible on the desktop.
    { id: 'sidebar.search', label: 'Filter Projects and sessions', category: 'view', available: true, run: () => toggleSidebarSearch(), voice:{
      phrases:['filter projects','filter sessions','search projects','search sessions','search the sidebar'],
    } },
    // Settings' section drawer, which only exists in the narrow layout. Real commands
    // rather than a special case inside the recognizer, because that is the only channel
    // a resolved gesture has — and it makes the drawer reachable from the palette too.
    { id: SETTINGS_NAV_TOGGLE, label: 'Toggle Settings sections', category: 'view', available: settingsOpen && mobileWorkspace, disabledReason: 'Open Settings on a narrow layout first', run: () => setSettingsNavOpen(value => !value) },
    { id: SETTINGS_NAV_CLOSE, label: 'Close Settings sections', category: 'view', available: settingsOpen && mobileWorkspace, disabledReason: 'Open Settings on a narrow layout first', run: () => setSettingsNavOpen(false) },
    { id:'prompts.open',label:'Open prompt library',category:'input',available:true,run:()=>{setPromptScope(null);setPromptTargetId(null);setPromptLibraryCreating(false);setPromptLibraryOpen(true);setMainMenuOpen(false)} },
    { id:'prompts.openProject',label:'Open prompt library for selected project',category:'input',available:!!commandProject,disabledReason:'No project selected',run:()=>{setPromptScope(commandProject||null);setPromptTargetId(null);setPromptLibraryCreating(false);setPromptLibraryOpen(true);setMainMenuOpen(false);setProjectMenu(null)} },
    // Scoped to the focused session's Project, because a template written from a
    // pane is nearly always about the thing that pane is doing.
    { id:'prompts.new',label:'New prompt template',category:'input',available:true,run:()=>{setPromptScope(projects.find(project=>project.id===active?.project_id)||null);setPromptTargetId(activeId);setPromptLibraryCreating(true);setPromptLibraryOpen(true);setMainMenuOpen(false)} },
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
    // The palette is where someone looks who does not yet know the footer button
    // exists, which is exactly the person this launches something for. The
    // disabled reason is the same sentence the button's tooltip carries, so the
    // two entry points never explain the same refusal differently.
    { id: 'configurator.open', label: 'Ask an agent about swe-mux (settings, diagnostics, how it works)', category: 'view', available: configuratorLaunch.enabled, disabledReason: configuratorLaunch.reason, run: () => void launchConfigurator() },
    // Help, as a command rather than only as a control. Before this it was reachable from
    // nowhere: there was no `help.*` command, no docs link, and the tour was behind one
    // section of one Settings tab. A recovery path nobody can find is not a recovery path.
    //
    // The spoken aliases deliberately avoid the bare word "help", which `voiceQueries.ts`
    // already owns for the *voice command catalog*. Two surfaces answering one phrase is
    // worse than either of them, and the catalog is the older meaning.
    { id: 'help.open', label: 'Open help (how swe-mux works)', category: 'view', available: true, run: () => openHelp(''), voice:{
      phrases:['open help','show help','open the help panel','how does this work','explain this app'],
    } },
    // The tour's own entry. `resetTutorial` first, because the walk is armed by the absence
    // of the completion mark - running it without clearing that leaves the next reload
    // offering it again, and completing it re-marks it either way.
    { id: 'tutorial.start', label: 'Take the guided tour', category: 'view', available: true, run: () => startTutorial(), voice:{
      phrases:['take the tour','start the tour','run the tutorial','show me around','start the guided tour'],
    } },
    // The guided voice setup gets its own command for the same reason the tour
    // did: a setup surface reachable only from inside Settings is invisible to
    // the person who needs it most.
    { id: 'voice.setup', label: 'Set up voice (guided)', category: 'voice', available: true, run: () => setVoiceSetupOpen(true), voice:{
      phrases:['set up voice','voice setup','guided voice setup','configure voice'],
    } },
    // One per topic, so a surface is reachable by its own name from the palette and by
    // voice - the same rule `drawerSegments.ts` enforces for a folded-in segment.
    ...HELP_TOPICS.map(topic => ({
      id: helpCommandId(topic.id),
      label: `Help: ${topic.title}`,
      category: 'view' as const,
      available: true,
      run: () => openHelp(topic.id),
      voice: { phrases: [`help with ${topic.title.toLowerCase()}`, `explain ${topic.title.toLowerCase()}`] },
    })),
    { id: 'actions.configure', label: 'Configure Actions', category: 'view', available: true, run: openActionEditor },
    // Two dialogs. The ids are unchanged so keybindings and menu rows that already name a
    // surface keep working and keep landing on it — `usage.open` most of all, which has
    // been called "Open usage analytics" the whole time while opening a segment of the
    // dialog about processes and disk, and now opens the dialog it is named for.
    { id: 'resources.open', label: 'Open resources', category: 'view', available: true, run: () => openResources('processes') },
    { id: 'usage.open', label: 'Open usage analytics', category: 'view', available: true, run: () => openUsage('overview') },
    // Quota is the one reading here that is ever urgent, so it gets a command of its own
    // rather than being two clicks inside the one above.
    { id: 'usage.quota', label: 'Open provider quota windows', category: 'view', available: true, run: () => openUsage('quota') },
    { id: 'fleetActivity.open', label: 'Open fleet activity telemetry', category: 'view', available: true, run: () => openResources('fleet') },
    { id: 'networkUsage.open', label: 'Open bandwidth usage', category: 'view', available: true, run: () => openResources('network') },
    { id: 'storageUsage.open', label: 'Open storage usage', category: 'view', available: true, run: () => openResources('storage') },
    { id: 'hooks.open', label: 'Open Automation', category: 'view', available: true, run: () => {openAutomation('policy',activeProject?.id);setMainMenuOpen(false)} },
    { id: 'notifications.open', label: `Open notifications${notificationUnread?` (${notificationUnread} new)`:''}`, category: 'view', available: true, run: openNotifications },
    { id: 'notes.scratchpad', label: 'Open global Scratchpad', category: 'view', available: scratchpadEnabled&&!!activeProject, disabledReason:scratchpadEnabled?'No project workspace available':'Global Scratchpad is disabled under Settings → Notes', run: () => openScratchpad('drawer') },
    { id: 'notes.open', label: 'Open current project’s notes', category: 'view', available: !!activeProject, disabledReason: 'No project selected', run: () => activeProject&&openNotesBrowser(activeProject) },
    { id: 'notes.browse', label: 'Browse all notes', category: 'view', available: true, run: () => openNotesBrowser(null) },
    { id: 'notes.browseProject', label: 'Browse this project’s notes', category: 'view', available: !!activeProject, disabledReason: 'No project selected', run: () => activeProject&&openNotesBrowser(activeProject) },
    { id: 'processes.open', label: 'Inspect selected session processes and previews', category: 'view', available: !!commandSession, disabledReason: 'No session selected', run: () => {if(commandSession)openProcessViewer(commandSession)} },
    { id: 'processes.all', label: 'Open unified process viewer', category: 'view', available: true, run: () => openProcessViewer() },
    { id: 'preview.file', label: 'Preview the focused HTML file in a pane', category: 'view', available: !!focusedPreviewableFile, disabledReason: 'The focused tab is not an HTML file', run: () => {
      if(!focusedPreviewableFile||!activeProject)return
      void openStaticPreview(activeProject,focusedPreviewableFile.path,focusedPreviewableFile.worktree,focusedViewId||undefined)
    } },
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
    // The persistent overlay half of the outline: the same claim protocol, toggling the
    // pinned faint list rather than opening the modal. Ctrl+click on the outline button does
    // this too; this command exists for the palette and for a mobile gesture binding.
    { id: 'note.outlinePeek', label: 'Toggle the pinned heading outline overlay', category: 'view', available: true, run: () => {
      const claim = new CustomEvent('mux:note-outline-peek', { cancelable: true })
      window.dispatchEvent(claim)
      if (!claim.defaultPrevented) setError('No focused note to pin an outline for. Click into a note first.')
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
    // One command per *segment and section*, not only per tab.
    //
    // This is what makes consolidating tabs non-destructive. Folding Clipboard into
    // Actions and Change Map into Activity would otherwise have deleted "open Clipboard"
    // and "open Change Map" as palette entries and as voice phrases, so the merge would
    // have cost a click at the rail *and* a whole navigation path for anyone who works by
    // name. Generated from the registry for the same reason the tab commands are: a
    // segment added without a command is a segment nobody can ask for.
    //
    // A segment is selected; a section is revealed. Both open the drawer first, because
    // both were asked for by name.
    ...DRAWER_SEGMENTS.map((segment): Command => ({
      id:`drawer.${segment.tab}.${segment.id}`,
      label:`Open ${segment.label}`,
      category:'view',
      available:true,
      run:()=>{
        if(segment.kind==='section')revealDrawerSection(segment.tab,segment.id)
        else openDrawerTab(segment.tab,projectId,segment.id)
      },
      voice:{phrases:[`open ${segment.label}`,`show ${segment.label}`,`go to ${segment.label}`]},
    })),
    // A retired segment keeps its command and its phrases, pointed at whatever absorbed
    // it. Deleting the entry would be the exact regression the registry exists to
    // prevent, only in reverse: "open Land" is a navigation path someone learned, and it
    // must keep answering after Land became a strip at the head of the worktree map.
    ...RETIRED_DRAWER_SEGMENTS.map((segment): Command => ({
      id:`drawer.${segment.tab}.${segment.id}`,
      label:`Open ${segment.label}`,
      category:'view',
      available:true,
      run:()=>openDrawerTab(segment.tab,projectId,segment.landsOn),
      voice:{phrases:[`open ${segment.label}`,`show ${segment.label}`,`go to ${segment.label}`]},
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
    // Kept under its own id as well as the generated `drawer.actions.clipboard`: this is
    // the id keybindings and the Clipboard Action button already bind to, and its label is
    // the phrase people search the palette for.
    { id: 'clipboard.open', label: 'Open clipboard history', category: 'clipboard', available: true, run: () => revealDrawerSection('actions','clipboard') },
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
    { id: 'session.kill', label: active && isEndedSession(active) ? 'Remove focused session from sidebar' : 'Kill and remove focused session', category: 'session', available: !!active, disabledReason: 'No focused session', run: () => active && requestKill(active) },
    { id: 'session.killImmediate', label: commandSession && isEndedSession(commandSession) ? 'Remove selected session from sidebar' : 'Kill and remove selected session immediately', category: 'session', available: !!commandSession, disabledReason: 'No session selected', run: () => commandSession && void killNow(commandSession) },
    { id: 'session.standDown', label: 'Stand down selected session', category: 'session', available: !!commandSession && !isEndedSession(commandSession), disabledReason: 'Select a live session', run: () => commandSession && void standDownSession(commandSession) },
    { id: 'session.resumeInactive', label: commandSession?.backend === 'shell' ? 'Restart inactive terminal' : 'Resume inactive session', category: 'session', available: !!commandSession && isInactiveSession(commandSession), disabledReason: 'Select an inactive session', run: () => commandSession && void resumeSession(commandSession) },
    { id: 'session.clearEnded', label: `Remove all ended sessions from sidebar${clearEndedCount > 1 ? ` (${clearEndedCount})` : ''}`, category: 'session', available: clearEndedCount > 0, disabledReason: 'No ended sessions in this Project', run: () => void clearEndedSessions(clearEndedTarget) },
    { id: 'session.relaunch', label: 'Relaunch focused task terminal', category: 'session', available: !!active && !!active.relaunchable, disabledReason: 'Relaunch is available for task-launched terminals', run: () => active && void relaunchSession(active) },
    // Same endpoint, different framing: for a recovered shell this is not
    // "run that task again" but "give me this terminal back", which is the only
    // way back for a session with no conversation to resume.
    { id: 'session.restartCold', label: 'Restart recovered terminal', category: 'session', available: !!commandSession && canRestartCold(commandSession), disabledReason: 'Select a recovered terminal session', run: () => commandSession && void relaunchSession(commandSession) },
    { id: 'session.pinAttention', label: active?.pinned_attention ? 'Unpin focused session attention' : 'Pin focused session attention', category: 'session', available: !!active && isAgent(active), disabledReason: 'Attention pinning requires a focused agent', run: () => active && void api<Session>('PATCH', `/api/sessions/${active.id}`, { pin: !active.pinned_attention }).then(updateSession) },
    { id: 'voice.toggleTalk', label: conversation.active||conversation.phase!=='off'?'Stop hands-free conversation':'Start hands-free conversation', category: 'voice', available: !!voiceStatus?.stt_enabled, disabledReason: 'Enable microphone conversation in Settings first', run: () => conversation.toggle() },
    { id: 'voice.toggleTargetPin', label: conversation.pinned?'Voice dictation: follow workspace focus':'Voice dictation: pin current target', category: 'voice', available: !!conversation.target, disabledReason: 'Focus an agent or text surface first', run: () => conversation.togglePin() },
    { id: 'voice.cycleMode', label: `Read aloud: cycle focused session mode${active && isAgent(active) ? ` (now ${voiceModeLabel(effectiveVoiceMode(active))})` : ''}`, category: 'voice', available: !!active && isAgent(active) && !!voiceStatus?.enabled, disabledReason: 'Read aloud requires a focused agent and TTS enabled in Settings', run: () => { if (active) cycleVoiceMode(active); setContextMenu(null) } },
    { id: 'voice.speak', label: 'Read aloud: speak focused session’s last reply', category: 'voice', available: !!active && isAgent(active) && !!voiceStatus?.enabled, disabledReason: 'Read aloud requires a focused agent and TTS enabled in Settings', run: () => { if (active) void speakLastReply(active); setContextMenu(null) } },
    { id: 'voice.autoplayDevice', label: `Read aloud: turn device autoplay ${autoplayEnabled() ? 'off' : 'on'}`, category: 'voice', available: !!voiceStatus?.enabled, disabledReason: 'Enable read aloud in Settings first', run: () => { setAutoplayEnabled(!autoplayEnabled()); setContextMenu(null) } },
    // The keyboard route to held clips. The pane's own strip is the surface that
    // *announces* a held clip, but a session whose pane is behind another tab has
    // no visible strip, so the backlog needs one route that does not depend on the
    // pane being drawn. Deliberately not labelled with a count: reading one would
    // subscribe this component to every `timeupdate` the audio element fires.
    { id: 'voice.playHeld', label: 'Read aloud: play clips held while you were elsewhere', category: 'voice', available: !!voiceStatus?.enabled, disabledReason: 'Enable read aloud in Settings first', run: () => { unlockPlayback(); playAllHeldClips(); setContextMenu(null) } },
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
      execute:async text=>{
        const query=parseVoiceQuery(text)
        if(!query){
          // Tier 2: a conservative fuzzy pass over the same grammar absorbs
          // STT noise before an utterance costs a model call.
          const fuzzy=resolveVoiceFuzzy(commandRegistryRef.current,text)
          if(fuzzy){
            if(fuzzy.command.voice?.execute)return await fuzzy.command.voice.execute('')
            const ran=runCommand(commandRegistryRef.current,fuzzy.command.id)
            if(ran==='ran')return{detail:`${fuzzy.command.label} (heard as “${text}”). Still listening.`}
          }
          // Tier 3: the assistant. An unmatched wake-word utterance becomes a
          // conversation turn instead of a refusal; the reply arrives in the
          // chat view and, in voice mode, through application speech.
          const asked=await sendAssistantTurn(text).catch(()=>false as const)
          if(asked!==false){
            return{detail:asked,transcript:asked}
          }
          const detail=`No voice command matched “${text}”. Say “${voiceStatus?.wake_words?.[0]||'Mux'}, list voice commands” for help, or enable the assistant in Settings.`
          return{detail,speech:detail}
        }
        return voiceQueryHandler.current(query)
      },
    }},
    // Keeps its id: it is reachable from saved keybindings and mobile gesture slots. What
    // changed underneath is that it moves the dock between the chip and whatever was last
    // expanded, rather than flipping a separate "assistant is open" flag — and that it
    // never touches capture. Asking for the assistant by name does set the addressee,
    // because "open the assistant" and "talk to the dictation draft" cannot both be true.
    { id:'assistant.toggle',label:voiceDock.state==='chip'?'Open the voice panel':'Collapse the voice panel to the top bar',category:'voice',available:true,run:()=>{
      if(voiceDockRef.current.state==='chip')setVoicePanelMode('chat')
      dispatchVoiceDock({kind:'toggle'})
    },voice:{phrases:['assistant','open assistant','open the assistant','chat','close assistant','close the assistant']}},
    { id:'voice.dockExpand',label:'Expand the voice panel',category:'voice',available:canExpandVoiceDock(voiceDock.state),disabledReason:'The voice panel is already at full size',run:()=>dispatchVoiceDock({kind:'expand'}),voice:{phrases:['expand the voice panel','expand voice panel','expand the panel']}},
    { id:'voice.dockCollapse',label:'Collapse the voice panel',category:'voice',available:canCollapseVoiceDock(voiceDock.state),disabledReason:'The voice panel is already in the top bar',run:()=>dispatchVoiceDock({kind:'collapse'}),voice:{phrases:['collapse the voice panel','collapse voice panel','collapse the panel']}},
    // Step across the panel's three bodies in the order its own tablist draws them, and
    // skip `dictation` while Talk is off for the same reason that tab is disabled: there
    // is no draft to show. Wrapping is right here where it is wrong for Projects — three
    // tabs one swipe apart are a ring, not a list with ends worth reporting. These carry
    // the dock's horizontal swipe.
    { id:'voice.panelModeNext',label:'Voice panel: next mode',category:'voice',available:voicePanelModeOrder.length>1,disabledReason:'Only one voice panel mode is available',run:()=>stepVoicePanelMode(1)},
    { id:'voice.panelModePrevious',label:'Voice panel: previous mode',category:'voice',available:voicePanelModeOrder.length>1,disabledReason:'Only one voice panel mode is available',run:()=>stepVoicePanelMode(-1)},
    // Clearing context is the one assistant act that runs on the word with no
    // confirmation card, because nothing is destroyed: the prior dialog is
    // unremembered, not deleted, and the panel keeps it readable. The spoken
    // reply therefore has to carry both halves - "context cleared" on its own
    // describes the same act as a deletion the operator cannot see.
    // Opening the panel is the dock's floor event here: raise to full without
    // ever collapsing, the dock-era spelling of the old setAssistantOpen(true).
    { id:'assistant.newConversation',label:'Start a new assistant conversation',category:'voice',
      // The refusal names the switch's real home from the target registry, so it cannot
      // drift the way the hardcoded "Settings → Assistant" it replaces did - that tab has
      // never existed. `settingTargets.test.ts` fails when the section stops resolving.
      available:!!assistantInfo?.enabled,disabledReason:`Turn the assistant on first: ${settingTarget('assistant.enable').where}`,
      run:()=>{setVoicePanelMode('chat');dispatchVoiceDock({kind:'floor',state:'full'});void startNewDialog().catch(()=>{})},voice:{
      phrases:NEW_CONVERSATION_PHRASES,
      execute:async()=>{
        await startNewDialog()
        // Show what was just done. The reply claims the old conversation is
        // still in the panel, so the panel is what the operator must land on.
        setVoicePanelMode('chat');dispatchVoiceDock({kind:'floor',state:'full'})
        return{detail:NEW_CONVERSATION_REPLY,speech:NEW_CONVERSATION_REPLY}
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
    // Ended and recovered sessions open too. The pane is read-only, and reading
    // what a session printed before it died is the entire reason its row is
    // still there — refusing to open it left the row as a label with nothing
    // behind it.
    { id: 'session.open', label: 'Open selected session in focused pane', category: 'session', available: !!commandSession, disabledReason: 'No session selected', run: () => commandSession && void selectSession(commandSession) },
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
    { id: 'session.approveOnce', label: 'Approve the request this session is showing', category: 'session', available: !!commandSession && commandSession.state === 'awaiting' && commandSession.awaiting_reason === 'approval', disabledReason: 'Select a session waiting for an approval', run: () => commandSession && void approvePendingRequest(commandSession) },
    { id: 'session.approvals.wait', label: 'Approvals: wait for me (default)', category: 'session', available: !!commandSession && effectiveApprovalMode(commandSession, Date.now() / 1000) !== 'wait', disabledReason: 'This session already routes every approval to you', run: () => commandSession && void setApprovalMode(commandSession, 'wait') },
    { id: 'session.approvals.allowlisted', label: 'Approvals: auto-approve allowlisted requests', category: 'session', available: !!commandSession && isAgent(commandSession) && !isEndedSession(commandSession), disabledReason: 'Select a live agent session', run: () => commandSession && void setApprovalMode(commandSession, 'allowlisted') },
    { id: 'session.approvals.allowAll', label: 'Approvals: auto-approve everything but the floor', category: 'session', available: !!commandSession && isAgent(commandSession) && !isEndedSession(commandSession), disabledReason: 'Select a live agent session', run: () => commandSession && void setApprovalMode(commandSession, 'allow_all') },
    { id: 'session.toggleRead', label: commandSession && isUnread(commandSession, ackedTurns) ? 'Mark selected session read' : 'Mark selected session unread', category: 'session', available: !!commandSession && isAgent(commandSession) && !isEndedSession(commandSession), disabledReason: 'Read state is tracked for live agent sessions', run: () => commandSession && void toggleSessionRead(commandSession) },
    { id: 'session.copyId', label: 'Copy selected session ID', category: 'clipboard', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) void navigator.clipboard.writeText(commandSession.id).catch(() => setError('Clipboard access was blocked.')) ; setContextMenu(null) } },
    { id: 'session.copyCwd', label: 'Copy selected working directory', category: 'clipboard', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) void navigator.clipboard.writeText(workingCwd(commandSession)).catch(() => setError('Clipboard access was blocked.')); setContextMenu(null) } },
    { id: 'session.openSplitHorizontal', label: 'Open selected session in split right', category: 'pane', available: !!commandSession, disabledReason: 'No session selected', run: () => commandSession && void openInSplit(commandSession, 'horizontal') },
    { id: 'session.openSplitVertical', label: 'Open selected session in split below', category: 'pane', available: !!commandSession, disabledReason: 'No session selected', run: () => commandSession && void openInSplit(commandSession, 'vertical') },
    { id: 'session.groupStack', label: 'Stack selected session with focused terminal', category: 'pane', available: !!commandSession&&!!activeId&&commandSession.id!==activeId&&commandSession.project_id===projectId, disabledReason: 'Choose two live sessions in the same project', run:()=>commandSession&&activeId&&void updateLayout(projectId,groupTerminalsInStack(activeLayout,activeId,commandSession.id)) },
    { id: 'session.reveal', label: 'Reveal selected working directory', category: 'session', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) void api('POST', '/api/reveal', { path: commandSession.cwd }); setContextMenu(null) } },
    { id: 'session.customSplit', label: 'New custom terminal in selected session split', category: 'pane', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) { setContextMenu(null); openLauncher(commandSession.project_id, 'horizontal') } } },
    { id: 'session.broadcastMembership', label: commandSession?.broadcast ? 'Remove selected session from broadcast' : 'Add selected session to broadcast', category: 'input', available: !!commandSession, disabledReason: 'No session selected', run: () => { if (commandSession) void api<Session>('POST', `/api/sessions/${commandSession.id}/broadcast-set`, { include: !commandSession.broadcast }).then(updated => { updateSession(updated); setContextMenu(null) }) } },
    { id: 'session.resume', label: 'Resume selected agent', category: 'history', available: !!commandSession && isAgent(commandSession) && !isInactiveSession(commandSession) && ['exited', 'crashed'].includes(commandSession.state), disabledReason: 'Select an exited agent session', run: () => commandSession && void resumeSession(commandSession) },
    // Offered on a live pane too, and that is the point: "pick this up on Tuesday" is
    // asked about work in progress far more often than about something already ended.
    { id: 'session.resumeLater', label: 'Resume selected agent later…', category: 'history', available: !!commandSession && isAgent(commandSession), disabledReason: 'Select an agent session', run: () => commandSession && scheduleResumeFromSession(commandSession) },
    { id: 'project.newTerminal', label: 'New terminal in selected project', category: 'project', available: !!commandProject, disabledReason: 'No project selected', run: () => { if (commandProject) void spawnTerminal(commandProject.id); setProjectMenu(null) } },
    { id: 'project.newTerminalCustom', label: 'New custom terminal in selected project', category: 'project', available: !!commandProject, disabledReason: 'No project selected', run: () => { if (commandProject) openLauncher(commandProject.id); setProjectMenu(null) } },
    { id: 'project.reveal', label: 'Reveal selected project in Explorer', category: 'project', available: !!commandProject, disabledReason: 'No project selected', run: () => { if (commandProject) void api('POST', '/api/reveal', { path: commandProject.root }); setProjectMenu(null) } },
    { id: 'project.rename', label: 'Rename selected project', category: 'project', available: !!commandProject, disabledReason: 'No project selected', run: () => commandProject && openRename({ kind: 'project', project: commandProject }) },
    // "First"/"last" against the sorted view, not the stored positions: with a sort
    // active they disagree, and the enabled state has to describe what is on screen.
    { id:'project.moveUp',label:'Move selected Project up',category:'project',available:!!commandProject&&displayProjects.filter(item=>groupIdFor(item)===groupIdFor(commandProject))[0]?.id!==commandProject.id,disabledReason:'Project is already first here',run:()=>commandProject&&moveProjectRelative(commandProject,-1) },
    { id:'project.moveDown',label:'Move selected Project down',category:'project',available:!!commandProject&&displayProjects.filter(item=>groupIdFor(item)===groupIdFor(commandProject)).at(-1)?.id!==commandProject.id,disabledReason:'Project is already last here',run:()=>commandProject&&moveProjectRelative(commandProject,1) },
    { id: 'project.settings', label: 'Open selected project settings', category: 'project', available: !!commandProject, disabledReason: 'No project selected', run: () => commandProject && openProjectsManager({ project: commandProject }) },
    { id: 'project.delete', label: 'Remove selected Project from swe-mux…', category: 'project', available: !!commandProject, disabledReason: 'No Project selected', run: () => commandProject&&openProjectsManager({project:commandProject}) },
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
    // Directional pane movement: focus, swap and resize, one command per direction.
    // `pane.next/previous` alone is unusable past two panes, and it is the first
    // vocabulary anyone arriving from tmux or vim reaches for. The split tree already
    // answers all three questions (`paneNeighborIds`, `swapPanes`, `resizeTargetFor`),
    // so availability is read from it rather than guessed - a direction with no
    // neighbour says why it is disabled instead of silently doing nothing.
    ...paneDirectionOptions.flatMap((option): Command[] => {
      const suffix = `${option.id[0].toUpperCase()}${option.id.slice(1)}`
      const neighbour = focusedTabId ? paneNeighborIds(activeLayout, focusedTabId)[option.id] : undefined
      const resize = focusedTabId ? resizeTargetFor(activeLayout, focusedTabId, option.id) : null
      return [
        {
          id: `pane.focus${suffix}`, label: `Focus the pane ${option.id === 'up' ? 'above' : option.id === 'down' ? 'below' : `to the ${option.id}`}`,
          category: 'pane', available: !!neighbour, disabledReason: 'No pane in that direction',
          run: () => { if (neighbour) focusPaneStack(neighbour) },
        },
        {
          id: `pane.swap${suffix}`, label: `Swap the focused pane with the one ${option.id === 'up' ? 'above' : option.id === 'down' ? 'below' : `to its ${option.id}`}`,
          category: 'pane', available: !!neighbour && !!activeStack, disabledReason: 'No pane in that direction',
          run: () => {
            const target = neighbour ? stackForView(activeLayout, neighbour) : null
            if (activeStack && target) void updateLayout(projectId, swapPanes(activeLayout, activeStack.id, target.id))
          },
        },
        {
          id: `pane.resize${suffix}`, label: `Move the pane divider ${option.id}`,
          category: 'pane', available: !!resize, disabledReason: 'No divider on that axis',
          run: () => { if (resize) void updateLayout(projectId, setSplitRatio(activeLayout, resize.path, resize.ratio)) },
        },
      ]
    }),
    // Close the focused pane without deciding what to do about the session in it:
    // a terminal leaf hands off to the ordinary confirm-kill, everything else is a
    // view and simply goes. There was no keyboard route to either before this.
    { id: 'pane.close', label: 'Close the focused pane', category: 'pane', available: !!focusedTabId, disabledReason: 'No focused pane', run: () => {
      const leaf = focusedTabId ? leaves(activeLayout).find(item => item.id === focusedTabId) : null
      if (!leaf) return
      const session = leaf.kind === 'terminal' ? sessions.find(item => item.id === leaf.id) : null
      if (session) { requestKill(session); return }
      void updateLayout(projectId, removeLeaf(activeLayout, leaf.kind, leaf.id))
    } },
    // Registered here since 2026-08-30. It had a button in the pane menu and an entry
    // in the bindable-command list, and no implementation anywhere - so the button did
    // nothing and a chord bound to it reported "unknown command".
    { id: 'pane.detach', label: 'Detach the focused tab into its own pane', category: 'pane', available: !!activeStack && activeStack.children.length > 1 && !!focusedTabId, disabledReason: 'The focused pane has only one tab', run: () => {
      const leaf = focusedTabId ? leaves(activeLayout).find(item => item.id === focusedTabId) : null
      if (!leaf || !activeStack) return
      const detached = splitView(removeLeaf(activeLayout, leaf.kind, leaf.id), activeStack.id, leaf, 'horizontal')
      void updateLayout(projectId, detached)
    } },
    { id: 'pane.swapNext', label: 'Swap the focused pane with the next one', category: 'pane', available: workspacePanes.length > 1 && !!activeStack, disabledReason: 'Only one pane is open', run: () => {
      if (!activeStack) return
      const order = workspacePanes.map(pane => pane.id)
      const next = order[(order.indexOf(activeStack.id) + 1) % order.length]
      if (next && next !== activeStack.id) void updateLayout(projectId, swapPanes(activeLayout, activeStack.id, next))
    } },
    // Reordering a tab inside its own pane, which drag covers by pointer and nothing
    // covered by keyboard. Same shape as `drawer.moveLeft`, one level down.
    ...([['stack.tabLeft', -1, 'left'], ['stack.tabRight', 1, 'right']] as const).map(([id, offset, word]): Command => ({
      id, label: `Move the focused tab ${word} within its pane`, category: 'pane',
      available: !!activeStack && activeStack.children.length > 1 && !!focusedTabId,
      disabledReason: 'The focused pane has only one tab',
      run: () => {
        if (!activeStack || !focusedTabId) return
        const order = activeStack.children.map(child => child.id)
        const index = order.indexOf(focusedTabId)
        const target = index + offset
        if (index < 0 || target < 0 || target >= order.length) return
        const reordered = [...order]
        reordered.splice(target, 0, ...reordered.splice(index, 1))
        void updateLayout(projectId, reorderStack(activeLayout, activeStack.id, reordered))
      },
    })),
    // Numbered tabs, mirroring the numbered Projects. The focused pane's tab strip is
    // the list, so `tab.activate(3)` means "the third tab of the pane I am in".
    ...Array.from({ length: 9 }, (_, index): Command => ({
      id: `tab.activate(${index + 1})`, label: `Focus workspace tab ${index + 1}`, category: 'pane',
      available: !!activeStack && activeStack.children.length > index,
      disabledReason: 'The focused pane has no such tab',
      run: () => {
        const child = activeStack?.children[index]
        if (!child || !activeStack) return
        setFocusedViewId(child.id)
        if (child.kind === 'terminal') setActiveId(child.id)
        if (activeStack.active_child_id !== child.id) void updateLayout(projectId, activateStackChild(activeLayout, activeStack.id, child.id))
      },
    })),
    // Focus regions: the hole that made "navigate the whole UI" impossible. The
    // sidebar could be *opened* without being focused and the drawer's tabs could be
    // stepped without focus ever entering it, so there was no keyboard route between
    // the parts of the screen at all - only within them.
    ...focusRegions().map(({ id, label, selector, before }): Command => ({
      id, label, category: id === 'focus.composer' ? 'input' : 'view', available: true,
      run: () => { before?.(); focusRegion(selector) },
    })),
    { id: 'focus.next', label: 'Focus the next UI region', category: 'view', available: true, run: () => cycleRegion(1) },
    { id: 'focus.previous', label: 'Focus the previous UI region', category: 'view', available: true, run: () => cycleRegion(-1) },
    // The palette's four scopes as commands, so a chord can land straight in the one
    // you want instead of opening the palette and typing its prefix.
    ...([['palette.commands', '>', 'commands'], ['palette.sessions', '@', 'a session'], ['palette.projects', '#', 'a Project'], ['palette.files', ':', 'a file']] as const).map(([id, prefix, what]): Command => ({
      id, label: `Palette: jump to ${what}`, category: 'view', available: true,
      run: () => { setPaletteQuery(prefix); setPaletteOpen(true) },
    })),
    { id: 'broadcast.toggle', label: broadcast ? 'Stop broadcasting input' : 'Start broadcasting input', category: 'input', available: true, run: () => setBroadcast(value => !value) },
    ...fleetCommands,
  ]
  commandRegistryRef.current=commands

  /**
   * The context a `when` clause is evaluated against.
   *
   * A closed set (`WHEN_FLAGS` in `keybindings.py`), computed fresh per keystroke
   * rather than memoized: every flag here is already a render-time value, and a
   * stale answer would fire the wrong binding rather than merely draw the wrong
   * thing. `agentFocused` and `hasSelection` are the two that are cheap here and
   * expensive anywhere else, which is why the evaluator lives in App at all.
   */
  const whenFlagsRef = useRef<Record<string, boolean>>({})
  whenFlagsRef.current = {
    terminalFocused: !!active && !settingsOpen && !paletteOpen,
    editorFocused: leaves(activeLayout).find(leaf => leaf.id === focusedViewId)?.kind === 'note',
    inputFocused: document.activeElement instanceof HTMLElement
      && /^(input|textarea)$/i.test(document.activeElement.tagName),
    overlayOpen: dismissStack.depth() > 0,
    paletteOpen,
    drawerFocused: !!document.activeElement?.closest?.('.utility-drawer'),
    sidebarFocused: sidebarOpen,
    settingsOpen,
    mobile: mobileWorkspace,
    desktop: !mobileWorkspace,
    zoomed: !!zoomedId,
    multiplePanes: workspacePanes.length > 1,
    multipleTabs: !!activeStack && activeStack.children.length > 1,
    hasSelection: terminalSelection(),
    agentFocused: !!active && isAgent(active),
  }
  // The trie is rebuilt only when the resolved map changes; the flag reader is a
  // ref, so the dispatcher always evaluates `when` against the current render
  // without the map being rebuilt on every state change.
  useEffect(() => {
    installKeymap(keymap, () => whenFlagsRef.current)
    setPendingChords([])
    setPendingOptions([])
  }, [keymap])

  /**
   * Keyboard Lock, off unless the user asked for it.
   *
   * In JavaScript-initiated fullscreen a Chromium tab can be handed the chords the
   * browser normally keeps - Ctrl+T, Ctrl+W, Escape - which is exactly the
   * remote-access case the API was specified for, and exactly what swe-mux in a
   * browser is. Never a default: it takes those keys away from the user's own
   * browser, and the only way out is Chrome's two-second Escape hold. It is armed
   * and released with fullscreen rather than held, so leaving fullscreen always
   * gives the keyboard back.
   */
  useEffect(() => {
    if (!keyboardLockEnabled() || !hostProfile().keyboardLockAvailable) return
    const keyboard = (navigator as Navigator & { keyboard?: { lock: () => Promise<void>; unlock: () => void } }).keyboard
    if (!keyboard) return
    const onFullscreen = () => {
      if (document.fullscreenElement) void keyboard.lock().catch(() => setError('The browser refused to capture its own shortcuts.'))
      else keyboard.unlock()
    }
    document.addEventListener('fullscreenchange', onFullscreen)
    onFullscreen()
    return () => { document.removeEventListener('fullscreenchange', onFullscreen); keyboard.unlock() }
  }, [settingsOpen])

  /**
   * The palette's four scopes.
   *
   * `@` sessions, `#` Projects, `:` files, `>` commands - VS Code's prefixes, and the
   * reason they exist here is not symmetry. `searchCommands` scored a command's
   * label, id and category, so the single most common navigation in a fleet UI -
   * "go to that session" - could not be answered by the palette at all unless the
   * session's name happened to be in a command label.
   *
   * Sessions and Projects need no new data: `fleetCommands` already registers a
   * `session.focus:<id>` and `project.focus:<id>` per row, so scoping is a filter
   * over the registry. Files are the one scope with no command behind them, so they
   * are fetched (`/api/projects/{id}/search?mode=names`) and turned into rows that
   * exist only while the query does.
   */
  const { scope: paletteScopeName, term: paletteTerm } = paletteScope(paletteQuery)
  const [fileMatches, setFileMatches] = useState<Array<{ name: string; path: string }>>([])
  useEffect(() => {
    if (!paletteOpen || paletteScopeName !== 'files' || !activeProject) { setFileMatches([]); return }
    const needle = paletteTerm.trim()
    if (!needle) { setFileMatches([]); return }
    // Debounced, because this is a bounded but real filesystem walk on the daemon and
    // the palette fires on every keystroke.
    let live = true
    const timer = window.setTimeout(() => {
      void api<{ items: Array<{ name: string; path: string }> }>('GET', `/api/projects/${activeProject.id}/search?mode=names&q=${encodeURIComponent(needle)}`)
        .then(result => { if (live) setFileMatches(result.items.slice(0, 50)) })
        .catch(() => { if (live) setFileMatches([]) })
    }, 120)
    return () => { live = false; window.clearTimeout(timer) }
  }, [paletteOpen, paletteScopeName, paletteTerm, activeProject?.id])

  const scopedCommands = useMemo((): Command[] => {
    if (paletteScopeName === 'sessions') return commands.filter(command => command.id.startsWith('session.focus:'))
    if (paletteScopeName === 'projects') return commands.filter(command => command.id.startsWith('project.focus:') || command.id.startsWith('project.activate('))
    if (paletteScopeName === 'files') {
      return fileMatches.map((file): Command => ({
        id: `file.open:${file.path}`, label: file.path, category: 'view', available: !!activeProject,
        run: () => { if (activeProject) openProjectFile(activeProject, file.path) },
      }))
    }
    return commands
  }, [paletteScopeName, commands, fileMatches, activeProject?.id])

  // Nothing is scored while the palette is closed; `commands.ts` owns that gate.
  // File rows arrive already filtered by the daemon's own walk, so re-scoring them
  // against the same term would only re-sort what the search already ranked.
  const shownCommands = paletteScopeName === 'files'
    ? scopedCommands
    : paletteResults(paletteOpen, scopedCommands, paletteTerm)
  useEffect(() => setPaletteIndex(0), [paletteQuery, paletteOpen])
  useEffect(()=>{if(!paletteOpen)return;const frame=requestAnimationFrame(()=>{paletteInput.current?.focus();paletteInput.current?.setSelectionRange(paletteInput.current.value.length,paletteInput.current.value.length)});return()=>cancelAnimationFrame(frame)},[paletteOpen])

  /**
   * The regions the keyboard can own, in the order `focus.next` walks them.
   *
   * Declared as a hoisted function because the command list below builds itself from
   * it during its own initializer, and each entry closes over App state (opening the
   * sidebar before focusing it, for instance) so it cannot live at module scope.
   *
   * A region that is closed is *opened* first rather than skipped. "Focus the side
   * panel" from a keyboard means "put me in the side panel", and refusing because it
   * happens to be shut is the answer that makes the command useless exactly when it
   * is most wanted.
   */
  function focusRegions(): Array<{ id: string; label: string; selector: string; before?: () => void }> {
    return [
      { id: 'focus.terminal', label: 'Focus the terminal grid', selector: '.pane-stack.focused-pane .xterm-helper-textarea, .pane-stack .xterm-helper-textarea, .pane-stack.focused-pane' },
      { id: 'focus.tabBar', label: "Focus the focused pane's tab bar", selector: '.pane-stack.focused-pane .stack-tabs [role="tab"], .pane-stack .stack-tabs [role="tab"]' },
      { id: 'focus.sidebar', label: 'Focus the Projects sidebar', selector: '.sidebar .project-row, .sidebar button, .sidebar', before: () => { setSidebarOpen(true); setNavigationSidebarOpen(true) } },
      { id: 'focus.drawer', label: 'Focus the side panel', selector: '.utility-drawer [role="tab"], .utility-drawer button, .utility-drawer', before: () => runNamedCommand('drawer.open') },
      { id: 'focus.composer', label: 'Focus the message composer', selector: '.mobile-terminal-draft textarea, .mobile-terminal-draft input' },
    ]
  }

  /**
   * Move keyboard focus into a region.
   *
   * The selector lists fallbacks most-specific first, because a region's ideal
   * target may not be rendered - a pane with no terminal has no xterm textarea, and
   * landing on the pane element itself is still better than the focus staying where
   * it was with no feedback. `preventScroll` because focusing the sidebar must not
   * scroll the terminal grid out from under the user.
   */
  function focusRegion(selector: string): void {
    // Two frames, not one: `before` may have opened a region that is not in the DOM
    // yet, and Preact commits on the next frame. Re-querying rather than caching the
    // element is what makes this safe to run against a region that just appeared.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const target = document.querySelector<HTMLElement>(selector)
      if (!target) { setError('That part of the screen is not open.'); return }
      if (target.tabIndex < 0 && !/^(a|button|input|select|textarea)$/i.test(target.tagName)) target.tabIndex = -1
      target.focus({ preventScroll: true })
    }))
  }

  /** Step to the next region that is currently on screen, wrapping. */
  function cycleRegion(offset: number): void {
    const regions = focusRegions()
    const present = regions.filter(region => !!document.querySelector(region.selector))
    if (!present.length) return
    const active = document.activeElement
    const index = present.findIndex(region => {
      const root = region.selector.split(',')[0].trim().split(' ')[0]
      return active instanceof Element && !!active.closest(root)
    })
    const next = present[((index < 0 ? 0 : index) + offset + present.length) % present.length]
    next.before?.()
    focusRegion(next.selector)
  }

  /** Focus a pane by its stack id, landing on whichever tab that pane last had open. */
  function focusPaneStack(stackId: string): void {
    const pane = workspacePanes.find(item => item.id === stackId)
    const child = pane?.children.find(item => item.id === pane.active_child_id) || pane?.children[0]
    if (!child) return
    setFocusedViewId(child.id)
    if (child.kind === 'terminal') setActiveId(child.id)
  }

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
      // Escape cancels an armed sequence before it means anything else. Without
      // this the only way out of a mistyped leader would be to press a key that
      // is not in the tree, which is exactly the state a user cannot reason about.
      if (event.key === 'Escape' && dispatchPending().length) {
        event.preventDefault()
        cancelKeymap()
        setPendingChords([])
        setPendingOptions([])
        return
      }
      // A modifier pressed on its own never advances the machine: holding Ctrl to
      // reach the second half of a chord would otherwise abandon the sequence.
      if (isModifierOnly(event.code)) return
      const chord = keyChord(event)
      const outcome = advanceKeymap(chord)
      if (outcome.kind === 'pending') {
        event.preventDefault()
        setPendingChords(outcome.pending)
        setPendingOptions(keymapOptions())
        return
      }
      // The sequence ended one way or another, so the overlay goes with it. Guarded
      // by the current value rather than fired blindly: this handler runs on every
      // keystroke, and an unconditional setState here would re-render the whole
      // shell on each one.
      setPendingChords(current => current.length ? [] : current)
      setPendingOptions(current => current.length ? [] : current)
      if (outcome.kind === 'abandon') {
        // Swallowed rather than passed through. Forwarding it would type a stray
        // character into a terminal the user believed was listening for the second
        // half of a shortcut, which is the one outcome nobody can attribute.
        event.preventDefault()
        setError(`${displayChord(outcome.pending.join(' '), host.platform)} ${displayChord(chord, host.platform)} is not a shortcut.`)
        return
      }
      if (outcome.kind === 'run' && runNamedCommand(outcome.command)) {
        event.preventDefault()
        return
      }
      // One level, not everything on screen. This handler stays bubble-phase on window so
      // a surface that owns Escape for itself — the utility drawer's focus-scoped handler,
      // the shortcut recorder in Settings — still shields it by stopping propagation.
      //
      // The dismiss stack directly, never `backTarget`: Escape with nothing open belongs
      // to the terminal, and stepping the workspace back a tab from inside an agent's TUI
      // is exactly the kind of side effect the flat Escape handlers were replaced to stop.
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
      setGroupMenu(null)
      setNoteMenu(null)
      setStaticPreviewMenu(null)
      setTabMenu(null)
      setEmptyMenu(null)
      setDrawerDisplayMenu(null)
      setConfiguratorMenu(null)
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
  useDismissLevel(() => setConfiguratorMenu(null), !!configuratorMenu, 'configurator-menu')
  useDismissLevel(() => setSortMenu(null), !!sortMenu, 'sort-menu')
  useDismissLevel(() => setGroupMenu(null), !!groupMenu, 'group-menu')
  useDismissLevel(() => setNoteMenu(null), !!noteMenu, 'note-menu')
  useDismissLevel(() => setStaticPreviewMenu(null), !!staticPreviewMenu, 'static-preview-menu')
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
  useDismissLevel(() => setHandoffState(null), !!handoffState, 'handoff-export')
  // The sidebar filter is a level too, so Escape and the platform back gesture put the
  // tree back. It is not gated on `mobileWorkspace` the way the sidebar itself is: the
  // filter is transient on every device, so it never leaves the stack permanently armed.
  useDismissLevel(() => closeSidebarSearch(), sidebarSearchOpen, 'sidebar-search')

  // -- sidebar filter -----------------------------------------------------
  // Focus follows opening, so the button and the keyboard both land on the input.
  useEffect(() => { if (sidebarSearchOpen) sidebarSearchRef.current?.focus() }, [sidebarSearchOpen])
  // Typing settles before the list re-ranks. Cleared on close so a query typed, abandoned,
  // and re-opened cannot arrive after the fact.
  useEffect(() => {
    if (!sidebarSearchOpen) return
    const timer = window.setTimeout(() => setSidebarSearchQuery(sidebarSearchInput), SIDEBAR_SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [sidebarSearchInput, sidebarSearchOpen])
  // Polled rather than scheduled, because what feeds it is a ref: every interaction
  // stamps `sidebarSearchTouchedAt` without re-rendering, and a `setTimeout` restarted
  // per interaction would have to be state to be restartable from a pointer move.
  useEffect(() => {
    if (!sidebarSearchOpen) return
    touchSidebarSearch()
    const timer = window.setInterval(() => {
      if (sidebarSearchExpired(sidebarSearchTouchedAt.current, Date.now())) closeSidebarSearch()
    }, SIDEBAR_SEARCH_IDLE_TICK_MS)
    return () => window.clearInterval(timer)
  }, [sidebarSearchOpen])

  // The platform back gesture steps back one place inside the app: an open overlay level
  // first, then the recent-views ring (mobile only). Installed for every device, not just
  // the mobile workspace: a desktop browser's Back button and mouse-4 reach the same
  // handler, and a standalone PWA has no other route back at all.
  useEffect(() => installSystemBack(backTarget), [backTarget])
  // Whether an entry in the ring still names something reachable depends on state the
  // ring cannot see, and the history sentinel must not stay armed against entries that
  // name nothing. A pane closing, a Project going away, or the layout mode flipping is
  // therefore reported to it rather than polled.
  useEffect(() => { viewHistory.touch() }, [mobileWorkspace, viewBack, layoutMap, projects])

  // -- redeploy tracking --------------------------------------------------
  // Mirror every state change out to sessionStorage, so a reload or a second tab
  // in this browser session comes up already knowing a redeploy is in flight.
  useEffect(() => { saveRedeploy(sessionStorageOrNull(), redeploy) }, [redeploy])
  // A request that failed in the moments before the daemon went away would
  // otherwise leave its toast sitting on top of the overlay that explains why.
  useEffect(() => { if (redeployDown) setError('') }, [redeployDown])
  const redeployRef = useRef(redeploy)
  redeployRef.current = redeploy
  // One wait loop for every entry path - this tab started it, the daemon
  // broadcast it, or the boot-time sentinel found it - so a client that did not
  // click the button behaves exactly like the one that did. Keyed on whether a
  // redeploy is in flight, never on the state itself: reading through a ref keeps
  // each probe's own update from tearing down and restarting the timer.
  const redeployActive = waitsOnDaemon(redeploy.phase)
  useEffect(() => {
    if (!redeployActive) return
    let cancelled = false
    let timer: number | undefined
    const ask = async (path: string) => {
      const controller = new AbortController()
      const deadline = window.setTimeout(() => controller.abort(), REDEPLOY_PROBE_TIMEOUT_MS)
      try { return await fetch(path, { cache: 'no-store', signal: controller.signal }) }
      finally { window.clearTimeout(deadline) }
    }
    const probeOnce = async (): Promise<ProbeResult> => {
      try {
        const health = await ask('/api/health')
        if (!health.ok) return { healthy: false }
      } catch { return { healthy: false } }
      // The daemon answered, so it is still serving the build stage and can also
      // report where that build has got to. A failure on this second request is
      // transient and must not be mistaken for the daemon going away.
      try {
        const response = await ask('/api/daemon/redeploy')
        if (response.ok) return { healthy: true, status: await response.json() as RedeployStatus }
      } catch { /* fall through with no status */ }
      return { healthy: true, status: null }
    }
    // Self-rescheduling rather than an interval: a slow probe must not have the
    // next one start on top of it, or a burst of piled-up failures would spend
    // the entire two-strike budget at once and raise the overlay on one stall.
    const schedule = () => { timer = window.setTimeout(() => { void tick() }, REDEPLOY_POLL_MS) }
    const tick = async () => {
      const probe = await probeOnce()
      if (cancelled) return
      const verdict = applyProbe(redeployRef.current, probe, Date.now())
      if (verdict.action === 'wait') { setRedeploy(verdict.state); schedule(); return }
      if (verdict.action === 'reload') {
        // Drop the sentinel before navigating, or the reloaded page resumes a
        // wait loop for a redeploy that has already finished, and hand the next
        // load a one-shot request to report what the redeploy actually did.
        markResultPending(sessionStorageOrNull())
        location.reload()
        return
      }
      setRedeploy(IDLE_REDEPLOY)
      if (verdict.action === 'timeout') {
        setError('The redeploy did not finish in time. Check redeploy.log in the data directory.')
        return
      }
      // Ended without the daemon ever going away: a refused preflight or a
      // failed build, so the running app was never touched.
      const notice = outcomeNotice(probe.healthy ? probe.status?.last_result : null)
      const tail = probe.healthy && Array.isArray(probe.status?.log_tail)
        ? probe.status.log_tail.slice(-3).join(' · ') : ''
      setError(notice || `Redeploy stopped before the app was replaced; the current app is untouched. ${tail || 'Check redeploy.log in the data directory.'}`)
    }
    void tick()
    return () => { cancelled = true; if (timer !== undefined) window.clearTimeout(timer) }
  }, [redeployActive])
  // The reload at the end of a successful redeploy lands here. A rollback is the
  // reason this exists: the app comes back looking entirely normal, so without
  // being told, nobody would know the change never shipped.
  useEffect(() => {
    if (!takeResultPending(sessionStorageOrNull())) return
    void (async () => {
      try {
        const response = await fetch('/api/daemon/redeploy', { cache: 'no-store' })
        if (!response.ok) return
        const status = await response.json() as RedeployStatus
        if (!outcomeIsFresh(status.last_result, Date.now())) return
        const notice = outcomeNotice(status.last_result)
        if (notice) setRedeployNotice(notice)
      } catch { /* the outcome is a courtesy; the app is up either way */ }
    })()
  }, [])
  // A dismissable level that refuses to be dismissed: back must not walk out of the app
  // while the daemon is mid-restart, and there is nothing behind these overlays to reach.
  // The daemon-down stage only - during a redeploy's build stage there is a working app
  // behind this, and swallowing back there would be wrong as well as useless.
  useEffect(() => {
    if (!daemonReloading && !redeployDown) return
    const id = dismissStack.register({ label: 'daemon-reload', blocking: true, dismiss: () => undefined })
    return () => dismissStack.unregister(id)
  }, [daemonReloading, redeployDown])
  // Field diagnostics for "back did the wrong thing" reports: the live level names, the
  // recent transition ring, and the views back would walk next, readable from a phone's
  // remote console with no build change.
  useEffect(() => {
    ;(window as unknown as { __muxDismiss?: unknown }).__muxDismiss = {
      depth: () => dismissStack.depth(),
      top: () => dismissStack.topLabel(),
      trace: () => dismissStack.trace(),
      views: () => viewHistory.entries(),
      // What back would actually act on, overlays and views together.
      backDepth: () => backTarget.depth(),
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
  // Settings' section drawer, for the same reason and by the same route. Only supplied
  // to the resolver while Settings is the level back would act on: the palette or the
  // theme picker opened over it is what a swipe then means, not the drawer underneath.
  const settingsNav = useRef({ open: false })
  settingsNav.current = { open: settingsNavOpen }
  // Region gestures act on the element the touch began on, and two of them need things
  // declared far below this recognizer (the mobile tab list, the menu openers). A ref so
  // the effect's dependency list stays the four values that actually change how a touch
  // is *read* — rebuilding these listeners on every render would drop sequences in flight.
  const runSurfaceGestureRef = useRef<(surface: SurfaceGesture, path: readonly Element[], at: { x: number; y: number }) => void>(() => {})
  useEffect(() => {
    if (!mobileWorkspace) return
    let state: { startX:number; startY:number; lastX:number; lastY:number; maxPointers:number; start:number; axis:'?'|'h'|'v'; region:GestureRegion|null; panning:boolean; path:readonly Element[]; claims:ReturnType<typeof markPointerDragClaims> } | null = null
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
      // A **region** answers to a swipe of its own, and is admitted on its own terms:
      // past the workspace allowlist above (the top bar and the voice dock are chrome
      // outside it), and past the horizontal-scroller veto (three of the five regions
      // *are* one). Recognized without the `touchmove` listener below — there is nothing
      // to preventDefault, and attaching one is precisely what swallows a rail's first
      // horizontal drag — so travel is measured from the touch that lifts.
      // A surface drawn over a region takes its touches whole: the rail's overflow popover
      // lives inside `.terminal-action-rail` and scrolls vertically, so without this a
      // scroll through its chips read as the rail's upward swipe and opened the app menu.
      // Dropped here rather than in `regionForPath` alone, because a path that is merely
      // "not a region" still resolves the workspace slots — and a sideways drag across the
      // panel would then change tabs behind it.
      if (pathShadowsGesture(path)) { state = null; detachMove(); return }
      const region = regionForPath(path)
      if (!(target instanceof Element) || (!region && (!target.closest('.mobile-unified-workspace, .sidebar, .sidebar-scrim, .utility-drawer, .utility-drawer-scrim, .modal-layer, .settings-layer, .usage-layer, .process-layer, .folder-picker-layer, .palette-layer') || pathOwnsHorizontalScroll(path, node => getComputedStyle(node).overflowX)))) { state = null; detachMove(); return }
      // Which panning a horizontal region swipe would be stealing, decided here while the
      // path is in hand. Two regions claim horizontal, and both sit next to a strip that
      // scrolls only when it overflows — the dock's actions, the top bar's account
      // switcher — so a fixed answer would be right half the time.
      const panning = region ? pathOwnsHorizontalScroll(path, node => getComputedStyle(node).overflowX) : false
      // A drag that has claimed the pointer owns it outright (`pointerDragClaim.ts`); a
      // second finger landing mid-drag does not get to start a gesture behind it.
      if (pointerDragOwnsPointer()) { state = null; detachMove(); return }
      const point = centroid(event.touches)
      if (!state) state = { startX: point.x, startY: point.y, lastX: point.x, lastY: point.y, maxPointers: event.touches.length, start: Date.now(), axis: '?', region, panning, path, claims: markPointerDragClaims() }
      else state.maxPointers = Math.max(state.maxPointers, event.touches.length)
      // Two fingers is never text entry, so lower the keyboard the moment the second
      // one lands rather than waiting for the command at touchend. An editor focuses
      // its input on every pointerdown (that is how a tap places the caret), so a
      // two-finger swipe starting over a note raises the keyboard on the way in — and
      // a swipe later is far too late to hide that it happened. Blurring in the same
      // frame the focus landed is what keeps it from ever animating up.
      // Not for a region sequence: every region gesture is single-finger by construction,
      // so a second finger there resolves to nothing — exactly as it did when the regions
      // were excluded outright — and dropping the keyboard for a gesture that will not run
      // is a change this one has no business making.
      if (event.touches.length >= 2 && !state.region) dismissSoftKeyboard()
      if (!state.region) attachMove()
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
      // A region sequence ran with no move listener, so the last position it recorded is
      // still the touch-down one. The finger that lifted is where it ended.
      const lifted = state.region ? event.changedTouches[0] : undefined
      if (lifted) { state.lastX = lifted.clientX; state.lastY = lifted.clientY }
      const sample = { pointerCount: state.maxPointers, dx: state.lastX - state.startX, dy: state.lastY - state.startY, durationMs: Date.now() - state.start }
      const panels = overlayPanels.current
      // A region that is not the command rail resolves to its own act and never to a slot.
      // Everything about it is local, so anything painted over the workspace — a modal, or
      // either slide-in panel — means the swipe is about that instead, and nothing runs.
      if (state.region && state.region !== 'commandRail') {
        const { region, panning, path } = state
        const at = { x: state.lastX, y: state.lastY }
        state = null
        if (!surfaceGestures) return
        if (panels.sidebarOpen || panels.drawerOpen || gestureOverlayDepth(dismissStack.depth(), panels) > 0) return
        const direction = classifyRegionGesture(sample)
        if (!direction) return
        // Horizontal yields to whatever is already panning under the finger.
        if (panning && isHorizontalDirection(direction)) return
        const surface = surfaceGestureFor(region, direction)
        if (!surface) return
        navigator.vibrate?.(12)
        runSurfaceGestureRef.current(surface, path, at)
        return
      }
      const slot = state.region ? classifyRailGesture(sample) : classifyGesture(sample)
      state = null
      if (!slot) return
      // `topLabel()` rather than a `settingsOpen` flag: what matters is whether Settings
      // (or its own drawer) is the level on top, so a picker opened above it keeps the
      // swipe for itself instead of quietly working the drawer behind it.
      const settingsOnTop = dismissStack.topLabel() === 'settings' || dismissStack.topLabel() === 'settings-nav'
      const command = resolveGestureCommand(slot, mobileGestures, panels, swipeAwayClose, {
        // `dismissStack.depth()`, never `backTarget.depth()`. This asks "is an overlay
        // painted over the workspace", and the answer decides that every other slot
        // resolves to nothing. Counting the recent-views ring here would make that true
        // permanently - one tab switch and no gesture would ever run again.
        depth: gestureOverlayDepth(dismissStack.depth(), panels),
        enabled: overlayBack,
        panel: settingsOnTop ? { open: settingsNav.current.open, toggle: SETTINGS_NAV_TOGGLE, close: SETTINGS_NAV_CLOSE } : undefined,
      })
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
  }, [mobileWorkspace, mobileGestures, swipeAwayClose, overlayBack, surfaceGestures])

  const recordClientStartupTiming=(sessionId:string,milestone:StartupMilestone,elapsedMs:number)=>{
    const current=clientStartupTimingValues.current[sessionId]||{}
    if(current[milestone]!==undefined)return
    const next={...current,[milestone]:elapsedMs}
    clientStartupTimingValues.current[sessionId]=next
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
      const runTrigger=<PaneRunTrigger
        projectName={activeProject?.name}
        mobile={mobileWorkspace}
        expanded={runMenu?.project.id===activeProject?.id&&runMenu?.trigger===`pane:${node.id}`}
        order={previewIds.length}
        onOpen={element=>{
          if(!activeProject)return
          setFocusedViewId(activeChild.id)
          toggleRunMenu(activeProject,element,`pane:${node.id}`)
        }}
      />
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
      return <section data-pane-stack-id={node.id} data-tutorial="workspace-pane" class={`pane-stack ${focusedPane?'focused-pane':''} ${paneDropClass}`} onPointerDown={event=>{if(event.button!==2)setFocusedViewId(activeChild.id)}}><OverflowRail className="stack-tabs" wrapperClassName="stack-tabs-rail" activeKey={activeChild.id} focusKey={focusedPane?activeChild.id:undefined} stripProps={{'data-tutorial':'tab-strip',role:'tablist','aria-label':'Workspace tabs'}}>
        {node.children.map(child=>{
          const activate=()=>{if(suppressDragClickRef.current===`tab:${child.id}`){suppressDragClickRef.current=null;return}setFocusedViewId(child.id);if(child.kind==='terminal')setActiveId(child.id);if(child.id!==activeChild.id)void updateLayout(projectId,activateStackChild(activeLayout,node.id,child.id))}
          const dragClass=dragStackTab?.overId===child.id&&dragStackTab.side?`drag-over drop-${dragStackTab.side}`:''
          const dragStyle={order:previewIds.indexOf(child.id)}
          if(child.kind==='preview'){
            const preview=previews[child.id]
            // A loopback preview is titled by its URL; a static one by its file name, because
            // its `file://` url is a long absolute path that reads as noise on a tab.
            const label=preview?(isStaticPreview(preview)?previewLabel(preview):preview.url):child.id
            return <div key={child.id} data-reorder-id={child.id} data-tutorial="tab-drag-source" style={dragStyle} class={`stack-tab-shell draggable-tab ${dragStackTab?.childId===child.id?'dragging':''} ${dragClass}`} onPointerDown={event=>beginWorkspaceTabDrag(event,{stackId:node.id,childId:child.id,kind:child.kind,targetStackId:node.id,zone:'tabs',previewIds:node.children.map(item=>item.id),overId:null,side:null},label)}><button role="tab" aria-label={`${label} preview tab`} title={label} aria-selected={child.id===activeChild.id} class={`tab-main preview-tab ${child.id===activeChild.id?'active':''}`} onClick={activate} onContextMenu={event=>{event.preventDefault();event.stopPropagation();openTabMenu(child,label,event.clientX,event.clientY)}}><span class="preview-tab-glyph" aria-hidden="true">◱</span>{preview?previewLabel(preview):child.id}</button>{closeTab(child,label)}</div>
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
          if(child.kind==='changemap'){
            const label=changeMapTabLabel(child.id)
            return <div key={child.id} data-reorder-id={child.id} data-tutorial="tab-drag-source" style={dragStyle} class={`stack-tab-shell draggable-tab resource-tab ${dragStackTab?.childId===child.id?'dragging':''} ${dragClass}`} onPointerDown={event=>beginWorkspaceTabDrag(event,{stackId:node.id,childId:child.id,kind:child.kind,targetStackId:node.id,zone:'tabs',previewIds:node.children.map(item=>item.id),overId:null,side:null},label)}><button role="tab" aria-label={`${label} change map tab`} title={label} aria-selected={child.id===activeChild.id} class={`tab-main ${child.id===activeChild.id?'active':''}`} onClick={activate} onContextMenu={event=>{event.preventDefault();event.stopPropagation();openTabMenu(child,label,event.clientX,event.clientY)}}><span class="preview-tab-glyph" aria-hidden="true">◈</span>{label}</button>{closeTab(child,label)}</div>
          }
          const session=sessions.find(item=>item.id===child.id)
          // sessionName, not session.name: the generated title is the whole point of
          // titling, and a tab strip showing `claude-15036b` while the sidebar shows
          // the real name is the surface where you actually need to tell panes apart.
          const label=session?sessionName(session):child.id
          return <div key={child.id} data-reorder-id={child.id} data-tutorial="tab-drag-source" style={dragStyle} class={`stack-tab-shell draggable-tab ${session?.pending?'pending-terminal-tab':''} ${dragStackTab?.childId===child.id?'dragging':''} ${dragClass}`} onPointerDown={event=>{if(!session?.pending)beginWorkspaceTabDrag(event,{stackId:node.id,childId:child.id,kind:child.kind,targetStackId:node.id,zone:'tabs',previewIds:node.children.map(item=>item.id),overId:null,side:null},label)}}><button role="tab" aria-label={`${label} session tab`} aria-selected={child.id===activeChild.id} class={`tab-main ${child.id===activeChild.id?'active':''} ${session?.state||''} ${session&&isColdSession(session)?'cold':''}`} onClick={activate} onContextMenu={event=>{event.preventDefault();event.stopPropagation();if(session&&!session.pending)openSessionMenu(session,event.clientX,event.clientY,'tab')}}>{sessionStateDot(session,rowConfig.dotShape,null,sessionStandingMark(session,rowConfig))}{sessionGlyph(session)}{voiceGlyph(session,tabVoiceMode(session))}{activityGlyphs(session,rowConfig.standing)}{mobileDraftIndicator(child.id)}{label}</button>{closeTab(child,label,session)}</div>
         })}{runTrigger}
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
      return <ProjectResource key={`${activeProject.id}:${node.id}`} project={activeProject} resource={identity} onOpenFile={path=>{if(identity.kind==='worktree-file'){openWorktreeFile(activeProject,identity.worktree,path);return}if(suppressDragClickRef.current===`file:${noteResourceId('file',path)}`){suppressDragClickRef.current=null;return}openProjectFile(activeProject,path)}} onFileDragStart={identity.kind==='worktree-file'?undefined:(path,event)=>beginFileTabDrag(event,path)} onSendToAgent={setSendToAgent} onPreviewFile={path=>void openStaticPreview(activeProject,path,identity.kind==='worktree-file'?identity.worktree:undefined,node.id)}/>
    }
    if(node.kind==='history')return <section class="workspace-leaf-placeholder"><strong>History moved</strong><span>Session history is now a full-screen overlay.</span><button onClick={()=>{setHistoryOpen(true);void updateLayout(projectId,removeLeaf(layoutValues.current[projectId]||emptyLayout(),'history',node.id))}}>Open History</button></section>
    if(node.kind==='queue'){
      const targetSessionId=queueLeafSessionId(node.id)
      if(!targetSessionId)return <section class="workspace-leaf-placeholder"><strong>queue unavailable</strong><span>{node.id}</span></section>
      // The pop-out rendering: target pinned to the leaf rather than following focus, and
      // no pop-out button of its own. Everything else is the same panel the drawer shows.
      return <QueuePane key={node.id} sessionId={targetSessionId} sessions={sessions} onSelectSession={sid=>{const owner=sessions.find(item=>item.id===sid);if(owner)void selectSession(owner)}}/>
    }
    if(node.kind==='changemap'){
      const targetSessionId=changeMapLeafSessionId(node.id)
      const owner=targetSessionId?sessions.find(item=>item.id===targetSessionId):undefined
      if(!targetSessionId)return <section class="workspace-leaf-placeholder"><strong>change map unavailable</strong><span>{node.id}</span></section>
      // The map is built from one session's recorded writes, so an ended target has no
      // map to draw rather than an empty one — say so instead of reading as "no edits".
      if(!owner)return <section class="workspace-leaf-placeholder"><strong>session ended</strong><span>Its change map is no longer available.</span><button onClick={()=>void updateLayout(projectId,removeLeaf(layoutValues.current[projectId]||emptyLayout(),'changemap',node.id))}>close tab</button></section>
      // Pinned to the leaf rather than following focus, and with no pop-out button of
      // its own — the same panel the drawer tab shows, already popped out. The Project
      // is the *owner's*, not the active one: a pinned map outlives the sidebar
      // selection, and opening one of its files must land in the right checkout.
      const mapProject=projects.find(item=>item.id===owner.project_id)||activeProject
      return <LazyChangeMap key={node.id} session={owner} project={mapProject}
        onOpenFile={(path,worktree)=>{
          if(!mapProject)return
          if(worktree)openWorktreeFile(mapProject,worktree,path)
          else openProjectFile(mapProject,path)
        }}/>
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
      <div class={`pane-bar ${agentSession?'agent-pane-bar':''}`}><div class="pane-identity"><span class="pane-title" title={sessionName(session)||id}>{sessionName(session)||id}</span></div>{!agentSession&&<div class="pane-path">{session.cwd}</div>}</div>
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
    // The pane carries no read-aloud surface at all — no chip group, and no player strip.
    // Both were per-session controls repeated once per *drawn pane*, answering the same
    // question on whichever panes happened to be on screen, and a split with four agents
    // drew four of them. Everything they did now lives in one place that follows focus:
    // the voice panel's `tts` tab (mode, content, on-demand generate, transport, the clip
    // list), reached from the one voice control in the top bar. The pane's remaining
    // per-session controls — `appr:`, `queue`, `transcript` — are in the pane tools.
    //
    // What replaced the strip on the pane is *nothing*, deliberately: which sessions speak
    // is reported by a mark on the sidebar row and the tab (`voice` row field), which costs
    // no pane space and is legible for every session at once rather than one at a time.
    // The header names the session and nothing else. Its state is on the tab, on the sidebar
    // row, and in the terminal the reader is already looking at, whereas the *name* is the one
    // thing those surfaces crop: a tab is only as wide as the strip allows. The name therefore
    // takes the column the status line used to hold, bounded by `fit-content()` in the
    // stylesheet so a long generated title cannot squeeze the path, voice chips, or tools.
    const paneTitle=sessionName(session)||id
    // Faults are not state, and they have no other pane-level surface — an agent header draws
    // no path chip, which is where the boundary warning otherwise lives — so they keep a marker
    // beside the name rather than disappearing with the status line. A stale transcript is the
    // one fault that looks like a healthy session: the pane may be reading a conversation this
    // PTY is no longer running (an unfollowable /clear or /new), so it is marked visibly and
    // not only in the tooltip. What counts as a fault is `sessionFaults`, deliberately not this
    // file: a marker drawn from "a diagnostic string exists" fires on every healthy session.
    const paneFaults=sessionFaults(session)
    // The full name leads the tooltip because the rendered one is truncated. The status line
    // follows it, unrendered: it costs no width on hover, and it keeps one reading of the state
    // within reach of the pane without competing with the name for the bar. The routine parser
    // line and delivery readiness ride along as detail — every observed session reports both,
    // which is exactly why neither may raise the marker.
    const paneTitleHint=[
      paneTitle,
      sessionStatus(session),
      ...paneFaults,
      session.parser_status!=='degraded'&&session.parser_diagnostic,
      session.delivery_readiness&&`delivery::${session.delivery_readiness.state} (${session.delivery_readiness.reason}) · authorized::no`,
    ].filter(Boolean).join('\n')
    // `key` matters here in a way it does not for a single-child stack: a stack now
    // renders its active pane *and* its warm siblings, so without a stable identity a
    // reorder would rebuild terminals rather than move them.
    const terminalPane=<section key={id} class={`terminal-pane ${activeId === id ? 'focused' : ''} ${paneVisible ? '' : 'pane-warm'}`} aria-hidden={paneVisible?undefined:'true'} onPointerDown={() => {setActiveId(id);setFocusedViewId(id)}}>
      <div class={`pane-bar ${agentSession?'agent-pane-bar':''}`} onContextMenu={openPaneMenu} onDblClick={() => setZoomedId(current => current === id ? null : id)}>
        <div class="pane-identity"><span class="pane-title" title={paneTitleHint}>{paneTitle}</span>{!!paneFaults.length&&<span class="pane-fault" role="img" aria-label={`${paneFaults.length===1?'Session fault':'Session faults'}: ${paneFaults.join('; ')}`} title={paneFaults.join('\n')}>⚠</span>}</div>
        {!agentSession&&<div class={`pane-path ${remoteBoundary?'remote':boundaryUnknown?'boundary-unknown':cwdIsLive?'live':'last-known'}`} title={nonLocalBoundary?'non-local terminal boundary; local cwd, Git, transcript, hooks, shim PATH repair, and agent promotion are unavailable':cwdIsLive?`live cwd · ${displayedCwd}`:`last known (spawn) cwd · ${displayedCwd}`}>{remoteBoundary?<span>remote::</span>:boundaryUnknown?<span>boundary::unknown::</span>:cwdIsLive?'':<span>last-known::</span>}{displayedCwd}</div>}
        {/* `appr:` leads the tools group. It is a standing *mode* rather than a one-shot
            action, so it reads first and the two surfaces that open a panel (`queue`,
            `transcript`) follow it, with the overflow menu last. It stays on every agent
            pane, including ones where no mode can be selected: a control that disappears
            when unavailable teaches the operator it does not exist, while one that stays
            and says why teaches them what would make it work. It does not cycle on click —
            the three positions are not a ladder you want to pass *through* (`allow_all` is
            not a step on the way back to `wait`) — so it opens a menu and each mode is
            chosen directly. `ApprovalChip` renders nothing on a shell backend, which is
            what lets this one group serve both header variants. */}
        <div class="pane-tools"><ApprovalChip session={session}/>{deliversHarnessPrompts(session.backend)&&<button class={`pane-tool-label queue-chip${(queueSummary[session.id]?.pending||0)>0?' has-pending':''}`} aria-label={`Open the prompt queue for ${sessionName(session)}`} title={`Prompt queue · ${queueSummary[session.id]?.pending||0} pending`} onClick={()=>void openQueueForSession(session.id)}>queue{(queueSummary[session.id]?.pending||0)>0?`:${queueSummary[session.id].pending}`:''}</button>}{hasHarnessTranscript(session.backend)&&<button class="pane-tool-label transcript-chip" aria-label={`Open the transcript for ${sessionName(session)}`} title="Read transcript" onClick={()=>void openTranscriptForSession(session.id)}>transcript</button>}{/* No `proc` chip. It carries no state of its own while `queue` reports its pending count, and
            on a phone it cost 40px of a bar that also has to fit the session name and path. What it
            opened is now the drawer's Processes tab, which pins this session's row first, and the
            session context menu and palette still open the inspector directly. */}<button aria-label={`More actions for ${sessionName(session)}`} title="Session actions" onClick={event=>{const rect=event.currentTarget.getBoundingClientRect();openPaneMenu({clientX:rect.right,clientY:rect.bottom,stopPropagation:()=>event.stopPropagation()})}}>⋯</button></div>
      </div>
      {isEndedSession(session)&&<EndedPaneBanner
        session={session}
        onResume={isAgent(session)?()=>void resumeSession(session):undefined}
        onRestart={isInactiveSession(session)&&session.backend==='shell'?()=>void resumeSession(session):canRestartCold(session)?()=>void relaunchSession(session):undefined}
        onOpenTranscript={hasHarnessTranscript(session.backend)?()=>showHistoryEntry(session.agent_run_id||session.id):undefined}
      />}
      <TerminalPane session={session} onState={updateSession} startupOrigin={startupOrigins.current[session.id]} onStartupTiming={(milestone,elapsedMs)=>recordClientStartupTiming(session.id,milestone,elapsedMs)} broadcast={broadcast} scrollback={xtermScrollback} rendererPreference={terminalRenderer} windowsPty={windowsPty} mobileInput={mobileInput} uiScale={uiScale} visible={paneVisible} claudeMaxColumns={claudeMaxColumns} onConfigureRail={openActionEditor} onBranch={()=>void branchSession(session)} />
    </section>
    if(insideStack)return terminalPane
    return <section data-tutorial="workspace-pane" class="pane-stack singleton-stack"><OverflowRail className="stack-tabs" wrapperClassName="stack-tabs-rail" activeKey={id} stripProps={{'data-tutorial':'tab-strip',role:'tablist','aria-label':'Terminal tabs'}}>
      <div data-tutorial="tab-drag-source" class="stack-tab-shell"><button role="tab" aria-label={`${sessionName(session)} session tab`} aria-selected="true" class={`tab-main active ${session.state} ${isColdSession(session)?'cold':''} ${isInactiveSession(session)?'inactive':''}`} onClick={()=>setActiveId(id)} onContextMenu={event=>{event.preventDefault();event.stopPropagation();openSessionMenu(session,event.clientX,event.clientY,'tab')}}>{sessionStateDot(session,rowConfig.dotShape,null,sessionStandingMark(session,rowConfig))}{sessionGlyph(session)}{voiceGlyph(session,tabVoiceMode(session))}{activityGlyphs(session,rowConfig.standing)}{mobileDraftIndicator(id)}{sessionName(session)}</button><button class={`tab-close ${confirmKillId===id?'confirming':''}`} aria-label={`${isEndedSession(session)?'Remove session':confirmKillId===id?'Confirm close terminal':'Close terminal'}: ${sessionName(session)}`} title={isEndedSession(session)?'Remove session':confirmKillId===id?'Confirm kill terminal':'Close and kill terminal'} onClick={event=>{event.stopPropagation();requestKill(session)}}>{confirmKillId===id?'✓':'×'}</button></div>
      <PaneRunTrigger projectName={activeProject?.name} mobile={mobileWorkspace} expanded={runMenu?.project.id===activeProject?.id&&runMenu?.trigger===`pane:${id}`} order={1} onOpen={element=>{if(!activeProject)return;setFocusedViewId(id);toggleRunMenu(activeProject,element,`pane:${id}`)}}/>
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
  /** A static preview belongs to a Project, not a session, so it gets its own row under the
   *  Project rather than nesting under whichever terminal happened to be focused when it was
   *  opened. Closing its tab leaves the registration standing; this row reattaches it, which
   *  is the same contract a detected server's row has. */
  const sidebarStaticPreviewRow=(preview:Preview,project:Project)=>{
    const layout=layoutMap[project.id]||parseLayout(project.layout)
    const previewStack=stackForView(layout,preview.id)
    const selected=previewStack?.active_child_id===preview.id
    // The right-click menu covers the whole entry, `×` included: that button sits outside
    // `.sidebar-note-row`, so a menu bound to the row alone would let a right-click on the
    // × fall through to the sidebar's own background menu.
    return <div key={preview.id} class={`static-preview-entry ${selected?'active':''}`} onContextMenu={event=>{
      event.preventDefault()
      event.stopPropagation()
      if(mobileWorkspace)return
      openStaticPreviewMenu(preview,event.clientX,event.clientY)
    }}>
      <button class={`sidebar-note-row preview-row static-preview-row ${selected?'active':''}`} title={`${preview.url} · served from disk by mux`} onClick={event=>{
        event.stopPropagation()
        setProjectId(project.id)
        setFocusedViewId(preview.id)
        if(previewStack)void updateLayout(project.id,activateStackChild(layout,previewStack.id,preview.id))
        else{
          // Reopen it the way every other resource opens: a tab in the pane you were last in,
          // rather than splitting geometry off on a guess.
          const anchor=(focusedViewId&&stackForView(layout,focusedViewId)?focusedViewId:null)||terminalIds(layout)[0]||leaves(layout)[0]?.id||null
          void updateLayout(project.id,openTab(layout,openAnchorId(layout,anchor),resourceLeaf('preview',preview.id)))
        }
        setSidebarOpen(false)
      }}>
        <span class="note-branch" aria-hidden="true">└</span><span class="note-copy"><strong>{previewLabel(preview)}</strong></span>
      </button>
      {/* Closing the *tab* deliberately keeps the registration, the same contract a detected
          server has. Nothing else rediscovers a static preview, so this row carries the one
          control that actually retires it. */}
      <button class="static-preview-remove" title={`Remove the ${previewLabel(preview)} preview`} aria-label={`Remove the ${previewLabel(preview)} preview`} onClick={event=>{event.stopPropagation();void removeStaticPreview(preview)}}>×</button>
    </div>
  }
  const openStaticPreviewMenu=(preview:Preview,x:number,y:number)=>{
    setContextMenu(null);setProjectMenu(null);setSidebarMenu(null);setSortMenu(null);setGroupMenu(null);setNoteMenu(null);setTabMenu(null);setEmptyMenu(null);setDrawerDisplayMenu(null);setRunMenu(null);setMainMenuOpen(false)
    setStaticPreviewMenu({previewId:preview.id,projectId:preview.project_id,label:previewLabel(preview),x,y})
  }
  const removeStaticPreview=async(preview:Preview)=>{
    try{
      await api('DELETE',`/api/previews/${encodeURIComponent(preview.id)}`)
      setPreviews(current=>{const next={...current};delete next[preview.id];return next})
      const layout=layoutMap[preview.project_id]||emptyLayout()
      if(stackForView(layout,preview.id))await updateLayout(preview.project_id,removeLeaf(layout,'preview',preview.id))
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }
  /** Land a registered Preview in the app: the item itself, the Project layout it was
   *  attached into, and — when the caller was asking to see it — focus. */
  const attachPreview=(preview:Preview,project:Project,focus=false)=>{
    setPreviews(current=>({...current,[preview.id]:preview}))
    setProjects(items=>items.map(item=>item.id===project.id?project:item))
    setLayoutMap(current=>({...current,[project.id]:parseLayout(project.layout)}))
    if(!focus)return
    setProjectId(project.id)
    setFocusedViewId(preview.id)
  }
  const openDetectedServer=async(server:{url:string},session:Session)=>{
    try{
      const result=await api<{preview:Preview;project:Project}>('POST','/api/previews',{session_id:session.id,url:server.url,attach:true})
      attachPreview(result.preview,result.project,true)
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }
  /** Serve one HTML document in a checkout as a Preview leaf. No process and no port: the
   *  daemon serves the file's own folder, so the page's relative assets resolve and the
   *  `/preview/<id>/` route works over the tailnet like any other preview.
   *  `targetViewId` is the tab it was launched from, so the preview lands in that pane
   *  rather than splitting an unrelated one. */
  const openStaticPreview=async(project:Project,path:string,worktree?:string,targetViewId?:string)=>{
    try{
      const result=await api<{preview:Preview;project:Project}>('POST','/api/previews',{
        kind:'static',project_id:project.id,path,attach:true,
        ...(worktree?{worktree}:{}),
        ...(targetViewId?{target_view_id:targetViewId}:{}),
      })
      attachPreview(result.preview,result.project,true)
      setSidebarOpen(false)
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
    const identityOnly=mobileWorkspace&&!rowConfig.mobileFields
    // Sidebar attention tier for agent rows. The focused row keeps its own
    // `.active` treatment; a row visible in another split pane reads as
    // "viewing" (on screen, not focused); an off-screen row with unseen output
    // is "unread"; an off-screen, already-seen row is "read" and recedes.
    const agent=isAgent(session)
    const attention=!agent||activeId===session.id?''
      :visibleSessionIds.includes(session.id)?'viewing'
      :isUnread(session,ackedTurns)?'unread':'read'
    return <div class="session-entry"><button data-sidebar-session-id={session.id} data-sidebar-project-id={session.project_id} data-sidebar-reorder={placement==='paned'&&!session.pending?undefined:'off'} class={`session-row ${activeId === session.id ? 'active' : ''} ${isSearchCursorSession(session.id)?'search-cursor':''} ${agent?'agent':''} ${attention} ${session.state} ${isColdSession(session)?'cold':''} ${isInactiveSession(session)?'inactive':''} ${session.pending?'pending-terminal-row':''}`} title={isInactiveSession(session)?inactiveSessionSummary(session):isColdSession(session)?coldSessionSummary(session):undefined} onPointerDown={event=>{if(!session.pending)beginSessionPointerDrag(event,session)}} onContextMenu={event => { event.preventDefault();if(!session.pending&&!mobileWorkspace)openSessionMenu(session,event.clientX,event.clientY,'sidebar') }} onClick={() => {if(suppressDragClickRef.current===`session:${session.id}`){suppressDragClickRef.current=null;return}void selectSession(session)}}>
      {sessionStateDot(session,rowConfig.dotShape,sessionContextArc(session,rowConfig),sessionStandingMark(session,rowConfig))}
      <SessionRowLive session={session} config={rowConfig} facts={rowFacts} identityOnly={identityOnly}/>
      {!session.pending&&<span class="row-actions" onPointerDown={event=>event.stopPropagation()} onClick={event => event.stopPropagation()}><button class={confirmKillId === session.id ? 'confirming' : ''} title={confirmKillId === session.id ? (isEndedSession(session) ? 'Confirm remove' : 'Confirm kill') : (isEndedSession(session) ? 'Remove from sidebar' : 'Kill')} onClick={() => runNamedCommand(`session.requestKill(${session.id})`)}>{confirmKillId === session.id ? '✓' : '×'}</button></span>}
    </button>{spawnedPreviews.map(preview=>sidebarPreviewRow(preview,session))}</div>
  }
  /** Terminals under `node` that the sidebar is still drawing. The pane tree's own
   *  pruning rule and the typed filter's are the same question asked twice, so they
   *  are one count: a branch with none left is not a branch, and a cluster down to
   *  one stops drawing itself as a cluster. Without the filter half, a split whose
   *  other side was filtered out would draw an empty branch beside the match. */
  const drawnTerminals=(node:PaneNode|PaneLeaf):number=>{
    if(node.type==='leaf')return node.kind==='terminal'&&sessionPassesFilter(node.id)?1:0
    const children=node.type==='stack'?node.children:[node.first,node.second]
    return children.reduce((total,child)=>total+drawnTerminals(child),0)
  }
  const sidebarNode=(node:PaneNode|PaneLeaf|null|undefined):ComponentChildren=>{
    if(!node)return null
    if(node.type==='leaf'){
      if(node.kind!=='terminal'||!sessionPassesFilter(node.id))return null
      const session=sessions.find(item=>item.id===node.id)
      return session?sessionRow(session):null
    }
    const branches=(node.type==='stack'?node.children:[node.first,node.second]).filter(child=>drawnTerminals(child)>0)
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
      // Mobile: the hold opens the tab menu here; a drag past DRAG_BEGIN dismisses it in onMove.
      ()=>{mobileTabHeldRef.current=false;if(mobileWorkspace)openMenu(event.clientX,event.clientY);if(mobileWorkspace)navigator.vibrate?.(15)},
      pointer=>{setContextMenu(null);setTabMenu(null);latestPointer={clientX:pointer.clientX,clientY:pointer.clientY};preview(pointer);if(scrollFrame===null)scrollFrame=window.requestAnimationFrame(autoScroll)},
      ()=>{stopAutoScroll();const chosen=target;target=null;showPointerDropIndicator(null);if(chosen&&chosen.id!==leaf.id)commitMobileTabOrder(leaf,chosen)},
      ()=>{stopAutoScroll();target=null;showPointerDropIndicator(null)},
      mobileWorkspace?MOBILE_HOLD_DRAG:POINTER_MOVE_DRAG,
    )
  }
  /** The name a mobile tab's menu is titled with. Shared with the swipe that opens the
   *  same menu, so the two doors cannot drift into naming the tab differently. */
  const mobileTabMenuLabel=(leaf:PaneLeaf):string=>{
    if(leaf.kind==='terminal'){const session=sessions.find(item=>item.id===leaf.id);return session?sessionName(session):leaf.id}
    if(leaf.kind==='preview')return previews[leaf.id]?.url||leaf.id
    if(leaf.kind==='history')return 'History'
    if(leaf.kind==='queue')return queueTabLabel(leaf.id)
    if(leaf.kind==='changemap')return changeMapTabLabel(leaf.id)
    return noteTabLabel(leaf.id)
  }
  /**
   * Carry out a recognized region gesture.
   *
   * Four of the nine resolve to ordinary commands, so a chord, the palette and a swipe
   * all reach the same code. The other three cannot: they act on *the element the touch
   * started on*, which no command id can name — so they read their target back out of
   * the composed path the recognizer kept, and open the menu the tab's own hold would
   * have opened, at the point the finger left.
   */
  const runSurfaceGesture=(surface:SurfaceGesture,path:readonly Element[],at:{x:number;y:number})=>{
    const inPath=<T extends Element>(match:(element:Element)=>element is T):T|null=>path.find(match) as T|null
    switch(surface){
      case 'voiceDock.expand': runNamedCommand('voice.dockExpand'); return
      case 'voiceDock.collapse': runNamedCommand('voice.dockCollapse'); return
      case 'voiceDock.modeNext': runNamedCommand('voice.panelModeNext'); return
      case 'voiceDock.modePrevious': runNamedCommand('voice.panelModePrevious'); return
      case 'projectName.next': runNamedCommand('project.next'); return
      case 'projectName.previous': runNamedCommand('project.previous'); return
      case 'projectName.menu': {
        // The same toggle the button's own tap does, including the closing half: this
        // element carries `data-menu-toggle`, so the document dismisser deliberately
        // leaves it alone and a second swipe would otherwise only ever reopen.
        if(!activeProject)return
        if(projectMenu){setProjectMenu(null);return}
        openProjectMenuAt(activeProject,at.x,at.y)
        return
      }
      case 'tabRail.menu': {
        const shell=inPath((element):element is HTMLElement=>element instanceof HTMLElement&&!!element.dataset.reorderId)
        const leaf=shell?mobileProjection.tabs.find(tab=>tab.id===shell.dataset.reorderId):undefined
        if(!leaf)return
        // The hold this swipe is a faster door to may have already fired and opened the
        // very same menu; reopening it a few pixels away is not a second act.
        if(contextMenu?.session?.id===leaf.id||tabMenu?.leaf.id===leaf.id)return
        const session=leaf.kind==='terminal'?sessions.find(item=>item.id===leaf.id):undefined
        mobileTabHeldRef.current=true
        if(session&&!session.pending)openSessionMenu(session,at.x,at.y,'mobile')
        else if(leaf.kind!=='terminal')openTabMenu(leaf,mobileTabMenuLabel(leaf),at.x,at.y,'mobile')
        return
      }
      case 'quotaChip.accounts': {
        // Literally "as if you had tapped it": the popover's open state belongs to
        // `ProviderAccounts` and nothing else needs it, so this presses the chip rather
        // than lifting that state into App for one gesture. A synthetic click raises no
        // `pointerdown`, so the outside-press dismissers stay out of it.
        const chip=inPath((element):element is HTMLElement=>element instanceof HTMLElement&&element.classList.contains('rail-quota'))
        chip?.click()
        return
      }
      case 'micToggle.reveal': {
        // Idempotent in both directions, unlike the button's own tap: a drag down means
        // "be open" and a drag up means "be gone", and neither should undo itself when
        // the panel is already where it was asked to be.
        if(voiceDockRef.current.state==='chip')dispatchVoiceDock({kind:'toggle'})
        return
      }
      case 'micToggle.hide': {
        if(voiceDockRef.current.state!=='chip')dispatchVoiceDock({kind:'set',state:'chip'})
        return
      }
      case 'runTrigger.menu': {
        // The launcher anchors to its button, so the button is what it needs — not the
        // point the finger left, which is wherever the drag happened to end.
        const trigger=inPath((element):element is HTMLElement=>element instanceof HTMLElement&&element.classList.contains('mobile-run-trigger'))
        if(activeProject&&trigger)toggleRunMenu(activeProject,trigger)
        return
      }
      case 'navToggle.open': runNamedCommand('sidebar.open'); return
      case 'drawerToggle.open': runNamedCommand('drawer.open'); return
      case 'noteRail.outline': {
        // `note.outline` asks "who has focus?", while pulling the Notes rail or resource
        // header does not move focus. Resolve the Project-note editor from the gesture's
        // own surface and name it explicitly. Scratchpad is excluded by resource kind.
        const direct=inPath((element):element is HTMLElement=>element.tagName==='CONTINUITY-EDITOR')
        const resource=inPath((element):element is HTMLElement=>element instanceof HTMLElement&&element.classList.contains('project-resource')&&element.dataset.resourceKind==='note')
        const notes=inPath((element):element is HTMLElement=>element instanceof HTMLElement&&element.classList.contains('notes-tab'))
        const editor=direct||resource?.querySelector<HTMLElement>('continuity-editor')||notes?.querySelector<HTMLElement>('.project-resource[data-resource-kind="note"] continuity-editor')
        if(!editor)return
        window.dispatchEvent(new CustomEvent('mux:note-outline',{cancelable:true,detail:{editor}}))
        return
      }
    }
  }
  runSurfaceGestureRef.current=runSurfaceGesture
  const mobileTab=(leaf:PaneLeaf):ComponentChildren=>{
    const selected=leaf.id===mobileProjection.selected?.id
    const session=leaf.kind==='terminal'?sessions.find(item=>item.id===leaf.id):undefined
    const label=mobileTabMenuLabel(leaf)
    const visibleLabel=mobileTabLabel(leaf)
    const glyph=leaf.kind==='terminal'?<>{sessionStateDot(session,rowConfig.dotShape,null,sessionStandingMark(session,rowConfig))}{sessionGlyph(session)}{voiceGlyph(session,tabVoiceMode(session))}{activityGlyphs(session,rowConfig.standing)}{mobileDraftIndicator(leaf.id)}</>:<span class="preview-tab-glyph" aria-hidden="true">{leaf.kind==='preview'?'◱':leaf.kind==='history'?'◷':leaf.kind==='queue'?'⇥':leaf.kind==='changemap'?'◈':'◇'}</span>
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
  // Mobile intentionally has no pane Run trigger, so an empty projection would render a
  // bare strip; drop the row entirely and let the empty stage own the section.
  const mobileUnifiedWorkspace=<section data-tutorial="workspace-pane" class={`pane-stack mobile-unified-workspace ${mobileProjection.tabs.length?'':'no-tabs'}`}>
    {mobileProjection.tabs.length>0&&<OverflowRail className="stack-tabs mobile-unified-tabs" wrapperClassName="stack-tabs-rail" activeKey={mobileProjection.selected?.id} stripProps={{'data-tutorial':'tab-strip',role:'tablist','aria-label':'All Project tabs'}}>
      {mobileProjection.tabs.map(mobileTab)}
    </OverflowRail>}
    <div class="stack-active mobile-unified-active">{mobileProjection.selected?renderPaneNode(mobileProjection.selected,'mobile',true):<div class="empty-stage"><div class="hero-terminal" aria-hidden="true">&gt;_</div><h1>Your Project workspace.</h1><p>Run a terminal, or open a note, a file, or a preview to begin. Files and notes live in the side panel.</p><QuestLog signals={questSignals} onAction={questAction} onDismiss={dismissQuest}/></div>}</div>
  </section>

  // Where the keyboard cursor is, over the rows the filter left drawn in sidebar order.
  // Unset means "wherever the best match is": typing re-ranks, so the cursor follows the
  // new best match without the user having to steer, and an arrow key takes over from
  // there. Held as a position rather than a row id because the list under it changes on
  // every keystroke and a clamp is the honest way to survive that.
  const sidebarSearchRows=sidebarFilter?.order||[]
  const sidebarBestIndex=sidebarFilter?.best
    ? sidebarSearchRows.findIndex(row=>sameSearchRow(row,sidebarFilter.best))
    : NO_SEARCH_CURSOR
  const sidebarCursorIndex=sidebarSearchCursor>NO_SEARCH_CURSOR
    ? clampSearchCursor(sidebarSearchCursor,sidebarSearchRows.length)
    : sidebarBestIndex
  const sidebarCursorRow=sidebarCursorIndex>NO_SEARCH_CURSOR?sidebarSearchRows[sidebarCursorIndex]||null:null
  const isSearchCursorProject=(id:string)=>sidebarCursorRow?.kind==='project'&&sidebarCursorRow.id===id
  const isSearchCursorSession=(id:string)=>sidebarCursorRow?.kind==='session'&&sidebarCursorRow.id===id
  const activateSidebarSearchResult=(result:SidebarSearchCandidate)=>{
    touchSidebarSearch()
    if(result.kind==='project')selectProject(result.projectId)
    else{
      const session=sessions.find(item=>item.id===result.id)
      // A row whose session vanished between the render and the keypress: the Project is
      // still where the user was pointing, so land there rather than doing nothing.
      if(session)void selectSession(session)
      else selectProject(result.projectId)
    }
    closeSidebarSearch()
  }
  const moveSidebarSearchCursor=(delta:number)=>{
    touchSidebarSearch()
    setSidebarSearchCursor(moveSearchCursor(sidebarCursorIndex,delta,sidebarSearchRows.length))
  }
  const staticPreviewsFor=(id:string)=>Object.values(previews)
    .filter(item=>item.project_id===id&&isStaticPreview(item)&&item.listed!==false)
    .sort((a,b)=>previewLabel(a).localeCompare(previewLabel(b))||a.id.localeCompare(b.id))
  const sidebarProjectRow=(project:Project)=>{
    const children = sessions
      .filter(session => session.project_id === project.id)
      .sort((a,b)=>a.created_at-b.created_at||a.id.localeCompare(b.id))
    const projectLayout=resolveLayout(layoutMap[project.id],project.layout)
    const projectPaneIds=terminalIds(projectLayout)
    const unpanedChildren=children.filter(session=>!projectPaneIds.includes(session.id)&&sessionPassesFilter(session.id))
    const dropClass=dragProject?.overId===project.id&&dragProject.side?`project-drop-target drop-${dragProject.side}`:''
    // A fold is a resting-state preference, and a filter that left a match hidden behind
    // one would be answering "where is X" with silence — so while filtering, a Project
    // with anything left under it draws open. The stored flag is untouched and comes back
    // when the filter clears, which is why the toggle is replaced by its spacer here:
    // folding inside a transient lens would set a preference nobody could see take effect.
    const filteredOpen=!!sidebarFilter&&children.some(session=>sessionPassesFilter(session.id))
    const collapsed=collapsedProjects.has(project.id)&&!filteredOpen
    const liveCount=children.filter(session=>!session.pending&&!['exited','crashed'].includes(session.state)).length
    const hasSessions=children.length>0&&!sidebarFilter
    return <section key={project.id} data-reorder-id={project.id} style={{order:projectPreviewIds.indexOf(project.id)}} class={`project-group ${project.id === projectId ? 'active' : ''} ${collapsed?'collapsed':''} ${dropClass}`}>
      <div class={`project-row draggable-project ${isSearchCursorProject(project.id)?'search-cursor':''} ${dragProject?.id===project.id?'dragging':''}`} title={mobileWorkspace?'Hold for actions, hold and drag to reorder or regroup':'Drag to reorder, or into a Group'} onPointerDown={event=>beginProjectPointerDrag(event,project)} onContextMenu={event => { event.preventDefault();if(!mobileWorkspace)openProjectMenuAt(project,event.clientX,event.clientY) }} onClick={()=>{if(suppressDragClickRef.current===`project:${project.id}`){suppressDragClickRef.current=null;return}selectProject(project.id)}}>
        {hasSessions?<button class="project-chevron project-collapse-toggle" aria-expanded={!collapsed} aria-label={`${collapsed?'Expand':'Collapse'} ${project.name}`} title={collapsed?'Expand project':'Collapse project'} onPointerDown={event=>event.stopPropagation()} onClick={event=>{event.stopPropagation();toggleProjectCollapsed(project.id)}}>{collapsed?'▸':'▾'}</button>:<span class="project-chevron project-collapse-spacer" aria-hidden="true"/>}<strong class="project-name-cell"><span class="project-name-text">{project.name}</span>{collapsed&&liveCount>0&&<span class="project-collapsed-badge" title={`${liveCount} active session${liveCount===1?'':'s'}`}>{liveCount}</span>}</strong><button data-menu-toggle class="project-row-menu" title={`Project actions for ${project.name}`} aria-label={`Project actions for ${project.name}`} aria-haspopup="menu" aria-expanded={projectMenu?.project.id===project.id} onPointerDown={event=>event.stopPropagation()} onClick={event=>{event.stopPropagation();if(projectMenu?.project.id===project.id){setProjectMenu(null);return}const rect=event.currentTarget.getBoundingClientRect();openProjectMenuAt(project,rect.left,rect.bottom+4)}}>⋮</button><button data-tutorial="project-run" class="project-row-run" title={`Run in ${project.name}`} aria-label={`Run in ${project.name}`} onPointerDown={event=>event.stopPropagation()} onClick={event=>{event.stopPropagation();openRunMenu(project,event.currentTarget)}}>▶</button>
      </div>
      {!collapsed&&<div class="session-list">
        {sidebarNode(projectLayout.root)}
        {unpanedChildren.map(session=>sessionRow(session,'unpaned'))}
        {staticPreviewsFor(project.id).map(preview=>sidebarStaticPreviewRow(preview,project))}
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
      {/* One voice control, two actions: tap opens the panel, hold starts listening.
          It was two buttons - a microphone and a panel chip - which on this row meant
          two voice buttons whose difference had to be remembered. */}
      <VoiceControl conversation={conversation} configured={!!voiceStatus?.stt_enabled} dock={voiceDock.state} pendingActions={assistantPendingActions} unseen={assistantUnseen} onToggleDock={()=>dispatchVoiceDock({kind:'toggle'})}/>
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
      {/* In the bar rather than under it. A floating card below a 44px toolbar covers the
          top of the workspace for the whole multi-minute build, on the screen that has the
          least of it to spare. It is a real toolbar control here: spinner and clock only,
          with the phase word and the build log behind a tap. The Project title is the item
          that gives up the width - it already ellipsizes, and it is also in the sidebar. */}
      {mobileWorkspace&&<RedeployChip state={redeploy} inline />}
      <button class="mobile-drawer-toggle" aria-label={clipboardOpen?'Close side panel':`Open side panel (${DRAWER_TABS.find(tab=>tab.id===drawerTabId)?.label||'clipboard'})`} aria-expanded={clipboardOpen} title={clipboardOpen?'Close side panel':`Side panel — ${DRAWER_TABS.find(tab=>tab.id===drawerTabId)?.label||'clipboard'}`} onClick={()=>setClipboardOpen(value=>!value)}><SidePanelIcon/></button>
    </div>
    <InteractionHud />
    {/* Desktop only: the phone renders it inside `.mobile-toolbar` above, and mounting
        both would run a second one-second timer for a chip nobody can see. Pinned under
        `.app-topbar` for every client, whether or not it is the one that started the
        redeploy. Non-blocking: during the build stage there is a working app underneath. */}
    {!mobileWorkspace&&<RedeployChip state={redeploy} />}

    <ContinuityBanner />
    {/* A release update, which is a different thing from the UI-build strip below:
        that one says this browser tab is behind the daemon it is already talking to
        and reloads itself, while this says the installed swe-mux is behind the
        published one and never installs anything. Both are rows of chrome, so
        neither covers a terminal. */}
    <UpdateBanner />
    {uiUpdateAvailable && <div class="ui-update-banner" role="status" aria-live="polite">
      <strong>UI update ready</strong>
      <span>This device will reload when the page is hidden.</span>
      <button onClick={() => location.reload()}>Reload now</button>
    </div>}
    {broadcast && <div class="broadcast-banner"><strong>Broadcast input is on</strong><span>Keystrokes mirror to sessions in the broadcast set.</span><button onClick={() => setBroadcast(false)}>Stop broadcasting</button></div>}

    <div class={`workspace ${sidebarCollapsed?'sidebar-collapsed':''} ${clipboardOpen&&!mobileWorkspace?'drawer-open':''} ${drawerTabDisplay==='title'?'drawer-tabs-title':''}`} style={{'--sidebar-width':`${sidebarWidth}px`,'--drawer-width':`${renderedDrawerWidth}px`,'--utility-rail-width':`${utilityRailWidth}px`} as JSX.CSSProperties}>
      <header class="app-topbar">
        <div class="app-identity"><button class="sidebar-collapse" aria-label={sidebarCollapsed?'Expand sidebar':'Collapse sidebar'} title={sidebarCollapsed?'Expand sidebar':'Collapse sidebar'} onClick={toggleSidebar}>{sidebarCollapsed?'»':'«'}</button><span class="daemon-ok" title="daemon::connected" aria-label="daemon connected"><i aria-hidden="true" /></span><strong class="desktop-project-name" title={activeProject?.name||'No Project selected'}>{activeProject?.name||'No Project'}</strong><VoiceControl conversation={conversation} configured={!!voiceStatus?.stt_enabled} dock={voiceDock.state} pendingActions={assistantPendingActions} unseen={assistantUnseen} onToggleDock={()=>dispatchVoiceDock({kind:'toggle'})}/> {activeProject&&<button data-tutorial="run" class="project-run-header" aria-haspopup="menu" aria-expanded={runMenu?.project.id===activeProject.id&&runMenu?.trigger==='project-run'} title={`Run in ${activeProject.name}`} onClick={event=>toggleRunMenu(activeProject,event.currentTarget)}>▶ Run</button>}</div>
      </header>
      <aside ref={sidebarRef} class={`sidebar ${sidebarOpen ? 'open' : ''}`} onContextMenu={event=>{const target=event.target as Element;if(target.closest('.sidebar-heading,.project-row,.session-row,.sidebar-note-row,.static-preview-entry,.sidebar-footer'))return;event.preventDefault();setContextMenu(null);setProjectMenu(null);setNoteMenu(null);setStaticPreviewMenu(null);setSortMenu(null);setGroupMenu(null);setMainMenuOpen(false);setSidebarMenu({x:event.clientX,y:event.clientY})}}>
        {/* PROJECTS names the whole navigation tree. Ungrouped Projects are root
            rows, while only explicit Groups receive their own headers.
            Its five controls are the tree's own: filter, fold, sort, the registry
            behind it, and add. The registry and add used to be a footer button and an app-menu row —
            both a screen away from the thing they act on. On a fine pointer they are
            revealed by hovering the header (see `.sidebar-projects-header` in the
            stylesheet); touch has no hover, so touch always shows them. */}
        {/* Searching takes the whole header rather than opening a row under it: the
            controls beside it act on a tree that is not currently drawn, and a filter
            that pushed the list down a line would move every result out from under the
            pointer at the moment it appeared. */}
        {sidebarSearchOpen
          ?<div class="sidebar-tools sidebar-projects-header searching">
            <span class="sidebar-search-mark" aria-hidden="true"><SearchIcon/></span>
            <input
              ref={sidebarSearchRef}
              class="sidebar-search-input"
              type="text"
              // Not `type="search"`: the browser's own clear affordance sits where this
              // row's close button already is, and its Escape handling would swallow the
              // key before the dismiss stack sees it.
              placeholder="Filter Projects and sessions"
              aria-label="Filter Projects and sessions"
              autocomplete="off"
              spellcheck={false}
              value={sidebarSearchInput}
              // Every keystroke releases the cursor back to "wherever the best match is",
              // because the ranking it was steering over has just been recomputed.
              onInput={event=>{touchSidebarSearch();setSidebarSearchInput(event.currentTarget.value);setSidebarSearchCursor(NO_SEARCH_CURSOR)}}
              onKeyDown={event=>{
                touchSidebarSearch()
                if(event.key==='ArrowDown'){event.preventDefault();moveSidebarSearchCursor(1);return}
                if(event.key==='ArrowUp'){event.preventDefault();moveSidebarSearchCursor(-1);return}
                if(event.key!=='Enter')return
                event.preventDefault()
                // Enter commits to the marked row, which is the best match until an arrow
                // key moves it. With nothing typed the sidebar is its ordinary tree, so
                // there is no mark and Enter does nothing rather than opening whichever
                // Project happens to sit at the top.
                if(sidebarCursorRow)activateSidebarSearchResult(sidebarCursorRow)
              }}
            />
            <button class="sidebar-tool" aria-label="Close filter" title="Close filter" onClick={closeSidebarSearch}>×</button>
          </div>
          :<div class="sidebar-tools sidebar-projects-header">
            <strong>PROJECTS</strong>
            {/* First of the controls because it is the one that scales: fold and sort
                rearrange a tree you can still see, and this is what you reach for when
                you cannot. */}
            <button class="sidebar-tool" disabled={!displayProjects.length} aria-label="Filter Projects and sessions" title="Filter Projects and sessions" onClick={openSidebarSearch}><SearchIcon/></button>
            <button class="sidebar-tool" disabled={!displayProjects.length} aria-label={allFolded?'Expand all Projects and Groups':'Collapse all Projects and Groups'} title={allFolded?'Expand all Projects and Groups':'Collapse all Projects and Groups'} onClick={()=>setAllFolded(!allFolded)}>{allFolded?<UnfoldMoreIcon/>:<UnfoldLessIcon/>}</button>
            <button class={`sidebar-tool sidebar-sort ${sidebarOrder.projectSort==='custom'?'':'active'}`} disabled={!displayProjects.length} aria-haspopup="menu" aria-expanded={!!sortMenu} aria-label="Sort Projects and Groups" title={`Sort - ${projectSortLabel(sidebarOrder.projectSort)}`} onClick={event=>{event.stopPropagation();if(sortMenu){setSortMenu(null);return}const rect=event.currentTarget.getBoundingClientRect();openSortMenu(rect.right,rect.bottom+4)}}>⇅</button>
            <button data-tutorial="projects" class="sidebar-tool" aria-label="Manage Projects" title="Manage Projects" onClick={()=>openProjectsManager()}><CogIcon/></button>
            {/* Both surfaces at once: the registry opens behind the create dialog, so
                dismissing the dialog lands on the registry rather than back on the tree.
                Reaching "add" used to mean opening the registry and finding its button. */}
            <button class="sidebar-tool" aria-label="Add a Project" title="Add a Project" onClick={()=>{openProjectsManager();void createProject()}}><PlusIcon/></button>
          </div>}
        <div class="project-tree" onPointerMove={sidebarSearchOpen?touchSidebarSearch:undefined}>
          {/* The box the token engine's character budget is measured from: one
              row's text column, at zero height, inside the real container chain.
              The wrappers are the tree's own classes and `.row-metric` restates the
              row's columns from the same variables, so the measured column cannot
              drift from the drawn one — and the gutter is `--session-dot`, which
              the user sets. It carries neither the `.session-row` class nor a
              `data-group-id`/`data-reorder-id`, so no selector, drag, or drop
              target elsewhere can resolve to it. */}
          <div ref={rowMetricRef} class="row-metric-probe sidebar-project-list" aria-hidden="true">
            <div class="project-group"><div class="session-list">
              <div class="row-metric">
                <span />
                <span class="session-copy" data-metric="copy">
                  <span class="row-line top"><span data-metric="top">0000000000</span></span>
                  <span class="row-line bottom"><span data-metric="bottom">0000000000</span></span>
                </span>
              </div>
            </div></div>
          </div>
          {/* The filter hides rows out of this tree rather than replacing it. Nothing here
              is reordered and nothing moves: the only difference a query makes is which
              rows are still present, so the column being searched keeps looking like the
              column that was there before. `pointermove` feeds the idle clock because
              reading the tree is using the filter - without it, one you are still looking
              at retires itself mid-scan. */}
          {sidebarFilter&&!sidebarFilter.order.length
            ?<p class="sidebar-search-empty">No Project or session matches "{sidebarSearchQuery.trim()}"</p>
            :<>
          {/* Three states, not two. An empty list before the first read has
              settled is "not loaded yet" and must not be drawn as "you have no
              Projects" - that CTA told every operator with a populated sidebar
              to create their first Project for the first second of every load,
              which is the same empty-vs-unknown conflation the Agent Environment
              catalog is written to avoid. The skeleton is deliberately the shape
              of a Project row so the sidebar does not reflow when the real ones
              arrive. */}
          {projectsView==='loading'&&<div class="sidebar-skeleton" role="status" aria-label="Loading Projects" aria-live="polite">
            {[0,1,2].map(row=><div key={row} class="sidebar-skeleton-row" />)}
          </div>}
          {projectsView==='none-shown'&&<button data-tutorial="empty-project" class="empty-project-cta" onClick={()=>openProjectsManager()}><strong>No Projects shown</strong><small>Open Projects to show or add an active Project.</small></button>}
          {projectsView==='none-registered'&&<button data-tutorial="empty-project" class="empty-project-cta" onClick={()=>openProjectsManager()}><strong>Create your first Project</strong><small>Open Projects to add a canonical folder.</small></button>}
          {/* One flat sequence of Group sections and runs of root Projects, in the order
              the active sort put them. Under Manual that is every root Project and then
              every Group, which is one run and the two-tier tree; under any other mode the
              root splits into the runs between the Groups.

              `data-group-id` is what a Project drag reads to decide which Group it is
              dropping into; a root run carries the empty string for "ungrouped", which is
              why each run has to be its own list rather than one list broken up visually.
              A root run renders while any Group exists even with nothing in it, so
              there is always somewhere to drag a Project back out to — otherwise a user
              who grouped every Project would have no way to ungroup one by hand. */}
          {rootRows.map(row=>{
            // Filtering prunes the runs and sections rather than only the rows inside them:
            // an empty root run would otherwise draw its "drag a Project here" hint into a
            // filtered tree, inviting a gesture that is inert while a query is up.
            if(row.kind==='root'){
              const items=row.items.filter(project=>!sidebarFilter||sidebarFilter.projects.has(project.id))
              if(sidebarFilter&&!items.length)return null
              return <div class="sidebar-project-list sidebar-ungrouped-projects" data-group-id="" key={row.key}>
                {items.map(project=>sidebarProjectRow(project))}
                {!items.length&&<p class="project-list-empty">Drag a Project here to ungroup it</p>}
              </div>
            }
            const bucket=row.bucket
            if(sidebarFilter&&!sidebarFilter.groups.has(bucket.id))return null
            const bucketItems=bucket.items.filter(project=>!sidebarFilter||sidebarFilter.projects.has(project.id))
            // Same reason a filtered Project draws open: a Group folded shut would hide the
            // match the query just found. The stored flag is untouched, and the header's
            // fold click is inert while filtering rather than silently setting a preference
            // whose effect nobody can see.
            const bucketCollapsed=isBucketCollapsed(sidebarOrder,bucket.id)&&!sidebarFilter
            const peerIds=bucketItems.map(item=>item.id)
            // Folding a section hides whichever Project holds the waiting agent, so
            // the header has to answer for all of them: a count for how much is live,
            // and the strongest state as a dot, because a bare count cannot say that
            // something in here is waiting on you.
            const bucketStatus=bucketCollapsed?projectSetRailStatus(sessions,peerIds,ackedTurns):null
            // The whole section is the Group's right-click target, not just its header: the
            // header is a one-line strip, and the drop hint below it is part of the same
            // Group. Rows inside carry their own menus and are let through untouched.
            return <section class={`sidebar-project-list sidebar-project-bucket ${bucketCollapsed?'collapsed':''} ${bucketItems.length?'':'empty'}`} key={bucket.id} data-reorder-id={bucket.id} data-group-id={bucket.id} onContextMenu={event=>{if((event.target as Element).closest('.project-row,.session-row'))return;event.preventDefault();event.stopPropagation();openGroupMenu(bucket.id,event.clientX,event.clientY)}}>
            {/* Desktop uses the header as both drag handle and collapse toggle. Mobile
                keeps only the tap-to-fold half because Project rows are its sole sidebar
                reorder target, and gets the Group menu on a hold instead of the right-click
                it has no way to perform. The rename button stops either parent gesture. */}
            <header title={mobileWorkspace?`${bucket.name} - tap to ${bucketCollapsed?'expand':'collapse'}, hold for actions`:`${bucket.name} - click to ${bucketCollapsed?'expand':'collapse'}, drag to reorder, right-click for actions`} onPointerDown={event=>{if(mobileWorkspace){groupHeldRef.current=false;beginLongPress(event,(x,y)=>{groupHeldRef.current=true;openGroupMenu(bucket.id,x,y)})}else beginBucketPointerDrag(event,bucket.id,bucket.name)}} onPointerUp={cancelLongPress} onPointerCancel={cancelLongPress} onPointerMove={moveLongPress} onClick={()=>{if(suppressDragClickRef.current===`bucket:${bucket.id}`){suppressDragClickRef.current=null;return}
              // The hold fires while the finger is still down, so the click it ends with
              // would fold the Group behind the menu it just opened.
              if(groupHeldRef.current){groupHeldRef.current=false;return}
              // A filtered Group is drawn open whatever its stored flag says, so folding
              // here would set a preference with no visible effect until the filter clears.
              if(sidebarFilter)return
              setSidebarOrder(toggleBucketCollapsed(sidebarOrder,bucket.id))}}>
              <span class="bucket-chevron" aria-hidden="true">{bucketCollapsed?'▸':'▾'}</span><span class="bucket-name">{bucket.name}</span>
              {/* Folded, the header is the only thing left of the Group, so it carries both
                  counts: how many Projects are inside (which the fold hid outright) and how
                  many sessions are live across them, the latter coloured by the strongest
                  state so a bare number cannot hide something waiting on you. */}
              {bucketCollapsed&&<span class="bucket-count-badge" title={`${bucketItems.length} Project${bucketItems.length===1?'':'s'} in this group`}>{bucketItems.length}</span>}
              {bucketStatus&&bucketStatus.liveCount>0&&<span class={`bucket-collapsed-badge activity-${bucketStatus.activity} ${bucketStatus.unread?'unread':''}`} title={`${bucketStatus.liveCount} live session${bucketStatus.liveCount===1?'':'s'} · ${projectRailActivityLabel[bucketStatus.activity]}${bucketStatus.unread?' · unread output':''}`}><i aria-hidden="true"/>{bucketStatus.liveCount}</span>}
              {/* Rename only. Sort lives in the PROJECTS header, and delete is in the
                  Group's context menu behind a confirm — it carried a `×` here once, one
                  pixel from the fold toggle, and no header button should be able to
                  dissolve a Group in a single stray click. */}
              <button class="bucket-rename" title="Rename group" aria-label={`Rename group ${bucket.name}`} onPointerDown={event=>event.stopPropagation()} onClick={event=>{event.stopPropagation();const group=projectGroups.find(item=>item.id===bucket.id);if(group)setGroupEdit({id:group.id,name:group.name})}}>✎</button></header>
              {!bucketCollapsed&&bucketItems.map(project=>sidebarProjectRow(project))}
              {/* Both the explanation and the drop target: an empty Group with no body
                  is a header alone, which is too thin a strip to aim a dragged row at. */}
              {!bucketCollapsed&&!bucketItems.length&&!sidebarFilter&&<p class="project-list-empty">Drag a Project here</p>}
            </section>})}
            </>}
        </div>
        <div class="sidebar-status">
          {/* The one surface that carries the empty-state invitation. `firstRun` holds
              it back while the harness panel or the tour is on screen: the tour has an
              account step of its own, and two invitations to the same thing at once is
              the overwhelm the first-run work exists to remove. */}
          <AccountSwitcher onManage={()=>openSettings('Accounts')} promptDismissed={accountPromptDismissed!==false} promptSuppressed={firstRun!=='none'} onDismissPrompt={dismissAccountPrompt}/>
          <ResourceUsageSummary snapshot={processFleet} sessions={sessions} onRefresh={()=>void loadProcesses()} onOpenFleet={()=>openProcessViewer()}/>
        </div>
        <div class="sidebar-footer"><button data-tutorial="menu" class="menu-trigger" onClick={() => setMainMenuOpen(value => !value)}><span>:</span> menu</button><button type="button" class={`notify-trigger ${alertsEnabled?'':'off'}`} aria-pressed={alertsEnabled} title={alertsEnabled?'Alerts on - click to mute sounds and push':'Alerts muted - click to restore sounds and push'} aria-label={alertsEnabled?'Mute alerts':'Enable alerts'} onClick={toggleAlerts}><svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 2c-2.2 0-3.6 1.6-3.6 3.9 0 2.7-1.2 3.6-1.2 4.6h9.6c0-1-1.2-1.9-1.2-4.6C11.6 3.6 10.2 2 8 2Z"/><path d="M6.6 12.6a1.5 1.5 0 0 0 2.8 0"/>{!alertsEnabled&&<line x1="2.6" y1="2.6" x2="13.4" y2="13.4"/>}</svg></button><button
          type="button"
          class={`configurator-trigger${configuratorLaunch.enabled?'':' off'}`}
          disabled={!configuratorLaunch.enabled}
          title={configuratorLaunch.reason}
          aria-label={configuratorLaunch.reason}
          onContextMenu={event=>{if(!opensChooser(event,configuratorOptions))return;event.preventDefault();setConfiguratorMenu({x:event.clientX,y:event.clientY})}}
          onClick={event=>{
            if(opensChooser(event,configuratorOptions)){setConfiguratorMenu({x:event.clientX,y:event.clientY});return}
            void launchConfigurator()
          }}
        ><svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="8" cy="8" r="2.1"/><path d="M8 1.6v1.9M8 12.5v1.9M14.4 8h-1.9M3.5 8H1.6M12.5 3.5l-1.3 1.3M4.8 11.2l-1.3 1.3M12.5 12.5l-1.3-1.3M4.8 4.8 3.5 3.5"/></svg></button>{/* Three controls, and the third is the reason the old rule needed
            restating. The rule was "app-wide switches, not navigation", which is why
            the gear that used to sit beside this bell is gone: Settings is one click
            inside the menu, and a second permanent door to it saved nothing. The
            configurator button is not a door — it starts an agent session about this
            install — and the footer is where a control that belongs to the whole app
            rather than to the tree above it goes. The Projects registry still lives on
            the PROJECTS header, beside the tree it edits. */}</div>
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
          <ResourceUsageSummary compact snapshot={processFleet} sessions={sessions} onRefresh={()=>void loadProcesses()} onOpenFleet={()=>openProcessViewer()}/>
          <AccountSwitcher variant="rail" placement="up" onManage={()=>openSettings('Accounts')}/>
        </div>
        {/* Run stays reachable while the sidebar is collapsed, including before a Project
            has any pane tabs whose local + could open it. */}
        <button data-tutorial="run" class="rail-button rail-run" aria-haspopup="menu" aria-expanded={!!activeProject&&runMenu?.project.id===activeProject.id&&runMenu?.trigger==='project-run'} aria-label={activeProject?`Run in ${activeProject.name}`:'Run'} title={activeProject?`Run in ${activeProject.name}`:'Run'} disabled={!activeProject} onClick={event=>activeProject&&toggleRunMenu(activeProject,event.currentTarget)}>▶</button>
        {/* The footer's configurator button has to exist here too: collapsing the
            sidebar must not remove a control, and an expand round-trip to ask a
            question about the app is the round-trip this button exists to avoid. */}
        <button class="rail-button" disabled={!configuratorLaunch.enabled} aria-label={configuratorLaunch.reason} title={configuratorLaunch.reason} onContextMenu={event=>{if(!opensChooser(event,configuratorOptions))return;event.preventDefault();setConfiguratorMenu({x:event.clientX,y:event.clientY})}} onClick={event=>{if(opensChooser(event,configuratorOptions)){setConfiguratorMenu({x:event.clientX,y:event.clientY});return}void launchConfigurator()}}>⚙</button>
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
        onSegment={selectDrawerTabSegment}
        onHelp={openHelp}
        sectionReveal={drawerSectionReveal||undefined}
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
        readAloud={!!voiceStatus?.enabled}
        notifications={notificationData}
        onNotificationsChanged={()=>void loadNotifications()}
        unread={notificationUnread}
        onOpenSession={sessionId=>{const session=sessions.find(item=>item.id===sessionId);if(!session){setError('That session is no longer live.');return}void selectSession(session)}}
        onOpenHistoryEntry={historyId=>{if(mobileWorkspace)setClipboardOpen(false);showHistoryEntry(historyId)}}
        onOpenSettings={section=>{if(mobileWorkspace)setClipboardOpen(false);openSettings(section)}}
        onManagePrompts={()=>{if(mobileWorkspace||transientDrawerTab)setClipboardOpen(false);setPromptScope(null);setPromptTargetId(null);setPromptLibraryCreating(false);setPromptLibraryOpen(true)}}
        onOpenFile={path=>{
          // The drag ghost's pointer-up also fires a click on the row it started from.
          if(suppressDragClickRef.current===`file:${noteResourceId('file',path)}`){suppressDragClickRef.current=null;return}
          if(activeProject)openProjectFile(activeProject,path)
        }}
        onOpenWorktreeFile={(worktree,path)=>{if(activeProject)openWorktreeFile(activeProject,worktree,path)}}
        // The drawer names no view of its own, so the preview lands in the pane you were last
        // in — the same anchor rule opening a file from here already follows.
        onPreviewFile={(path,worktree)=>{if(activeProject)void openStaticPreview(activeProject,path,worktree,focusedViewId||undefined)}}
        onProjectUpdated={updated=>setProjects(items=>items.map(item=>item.id===updated.id?updated:item))}
        // Desktop only: the drawer is an in-flow column there, so a file row can be dragged
        // onto a visible pane. On mobile it is an overlay with nothing to drop onto.
        onFileDragStart={mobileWorkspace?undefined:(path,event)=>beginFileTabDrag(event,path)}
        onSendToAgent={request=>{if(mobileWorkspace)setClipboardOpen(false);setSendToAgent(request)}}
        queueOpenToken={queueOpenToken || undefined}
        onQueueOpenAsTab={sessionId=>void openQueueTab(sessionId)}
        onChangeMapOpenAsTab={sessionId=>void openChangeMapTab(sessionId)}
        projects={projects}
        // '' (all Projects) is the stored default; an unscoped tab means the Project the
        // drawer is sitting beside, so it resolves to the active one at render time and
        // follows a Project switch instead of pinning whichever was active when it opened.
        processScope={resolveProjectScope(processProjectScope,projectId||'',projects)}
        onProcessScope={setProcessProjectScope}
        // Same resolution as the process scope: an unscoped tab follows the Project the
        // drawer is sitting beside rather than pinning whichever was active on open.
        scheduleScope={scheduleScope&&projects.some(project=>project.id===scheduleScope)?scheduleScope:(projectId||'')}
        onScheduleScope={setScheduleScope}
        scheduleSeed={scheduleSeed}
        onScheduleSeedConsumed={()=>setScheduleSeed(null)}
        profiles={profiles}
        // The Processes tab registers the preview itself, so this only lands the result:
        // focused, because attaching a preview from the drawer is a request to look at it.
        onPreviewAttached={(preview,project)=>attachPreview(preview,project,true)}
        onOpenInspector={scope=>openProcessViewer(null,scope)}
        onOpenProjectSettings={id=>{const target=projects.find(item=>item.id===id);if(target)openProjectsManager({project:target})}}
        onOpenAutomationDashboard={()=>openAutomation('activity',activeProject?.id)}
        queuePending={queuePendingTotal}
        onOpenFleetQueue={()=>openFleetQueue()}
        notesAllProjects={notesAllProjects}
        onNotesAllProjects={setNotesAllProjects}
        onOpenNote={(targetProject,noteId,title,place)=>{
          setNoteTitles(current=>({...current,[`${targetProject}:${noteId}`]:title}))
          openBrowsedNote(targetProject,noteId,place)
        }}
        onOpenScratchpad={openScratchpad}
        scratchpadEnabled={scratchpadEnabled}
        onScratchpadEnabled={persistScratchpadEnabled}
        drawerNoteId={drawerNoteId}
        noteTargetClaimToken={drawerNoteClaimRequest?.projectId===projectId&&drawerNoteClaimRequest.resourceId===drawerNoteId?drawerNoteClaimRequest.token:undefined}
        onNoteTargetClaimed={token=>setDrawerNoteClaimRequest(current=>current?.token===token?null:current)}
        onPopDrawerNoteToTab={resourceId=>popDrawerNoteToTab(resourceId,projectId)}
        tabDisplay={drawerTabDisplay}
        onTabDragStart={beginDrawerTabDrag}
        onProjectionTabReorder={beginMobileDrawerTabDrag}
        hiddenTabs={hiddenDrawerTabs}
        onTabDisplayMenu={(x,y,tab)=>openDrawerDisplayMenu(x,y,'tabs',tab)}
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
          const target=insertIntoFocusedSurface(text,activeId,{onRefused:setError})
          if(target==='none')setError('Focus a terminal or note before inserting text.')
          return target
        }}
        // A prompt template is written for an agent to read: routing one into whichever
        // note or file pane happened to be focused last edits that document instead.
        onInsertPrompt={text=>{
          const target=insertIntoFocusedSurface(text,activeId,{terminalsOnly:true,onRefused:setError})
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
        {/* The same predicate the open drawer's strips use. No `peek`: this rail is only
            rendered while the drawer is closed, and a peek exists only while one is open. */}
        {drawerLauncherTabs.filter(tab=>drawerTabVisible(tab.id,{hidden:hiddenDrawerTabs,hasTranscript:hasHarnessTranscript(active?.backend)})).map(tab=>{
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
              openDrawerDisplayMenu(event.clientX,event.clientY,'rail',tab.id)
            }}
            onClick={()=>showDrawerTab(tab.id)}
          >{utilityRailDisplay==='title'?<span class="drawer-tab-title">{tab.label}</span>:<Icon/>}{tab.id==='notifications'&&notificationUnread>0&&<i class="drawer-badge">{notificationUnread>99?'99+':notificationUnread}</i>}</button>
        })}
      </nav>}

      <main data-tutorial="workspace" class="main-stage" onContextMenu={event => { if (!activeLayout.root) { event.preventDefault(); setEmptyMenu({ x: event.clientX, y: event.clientY }) } }}>
        <div class="project-workspace unified-workspace">
          <div class="terminal-workspace">
            {mobileWorkspace?mobileUnifiedWorkspace:(activeLayout.root||focusedOutsideLayout) ? <div class="pane-tree">{renderPaneNode(zoomedId ? stackForView(activeLayout,zoomedId)||activeLayout.root! : focusedOutsideLayout&&activeId ? paneStack([terminalLeaf(activeId)],activeId) : activeLayout.root!)}</div> : <div class="pane-tree"><section data-tutorial="workspace-pane" class="pane-stack empty-workspace-pane">
              <div class="stack-active empty-stage"><div class="hero-terminal" aria-hidden="true">&gt;_</div><h1>Your Project workspace.</h1><p>Run a terminal, or open a note, a file, or a preview to begin. Files and notes live in the side panel.</p><QuestLog signals={questSignals} onAction={questAction} onDismiss={dismissQuest}/></div>
            </section></div>}
          </div>
        </div>
      </main>
      {/* The one voice surface, mounted once for the life of the app and never moved.
          It is a grid item in the main stage's own cell rather than a track of its own, so
          it floats over the top of the workspace without changing any pane's row count —
          a pane that resizes when a voice panel opens reflows a live agent's terminal, and
          the reflowed scrollback does not come back when it closes.
          Rendered unconditionally, at every dock state including the collapsed chip:
          `.voice-dock.chip` hides it in CSS, which keeps the assistant conversation inside
          it mounted, streaming, and speaking while the workspace is clear. */}
      <div class="voice-dock-anchor">
        <VoiceDock
          conversation={conversation}
          commands={commands}
          configuredCommands={voiceStatus?.commands}
          onOpenSettings={()=>openSettings('Voice')}
          mode={voicePanelMode}
          onMode={setVoicePanelMode}
          assistantView={assistantView}
          readView={readView}
          captureConfigured={!!voiceStatus?.stt_enabled}
          dock={voiceDock.state}
          onDock={step=>dispatchVoiceDock({kind:step})}
        />
      </div>
    </div>

    {launcherOpen && <div class="quick-launcher" role="dialog" aria-modal="true" aria-label="New terminal custom">
      <div class="quick-heading"><span>NEW TERMINAL CUSTOM::{projects.find(project => project.id === launcherProject)?.name?.toUpperCase()}{launcherSplit?'::SPLIT':''}</span><button onClick={() => setLauncherOpen(false)}>×</button></div>
      <form onSubmit={event => { event.preventDefault(); void spawnTerminal(launcherProject, launcherSplit, launcherProfile) }}>
        <label>Shell profile<Dropdown value={launcherProfile} onChange={setLauncherProfile} options={profiles.filter(profile=>profile.backend==='shell').map(profile=>({value:profile.id,label:`${profile.marker} · ${profile.label}`}))}/><small>{profiles.find(profile=>profile.id===launcherProfile)?.capabilities.join(' · ')}</small></label>
        <label>Project root<input value={projects.find(project=>project.id===launcherProject)?.root||''} readOnly /></label>
        <button class="primary" type="submit">Open {profiles.find(item=>item.id===launcherProfile)?.label || 'terminal'}</button>
      </form>
    </div>}

    {runMenu&&<ProjectRunMenu project={runMenu.project} profiles={profiles} anchor={{x:runMenu.x,y:runMenu.y}} onClose={()=>{runMenuClosedAt.current=Date.now();setRunMenu(null)}} onLaunch={(backend,profileId)=>{const target=runMenu.project.id;setRunMenu(null);void spawnTerminal(target,false,profileId,undefined,'after',backend)}} onCustom={()=>{const target=runMenu.project.id;setRunMenu(null);openLauncher(target)}} onSessions={items=>void attachActionSessions(runMenu.project.id,items)} onWorktreeCreated={(path,backend)=>void startWorktreeSession(runMenu.project.id,path,backend)} onError={setError}/>}

    {/* Sits above the workspace and takes no focus: the sequence is still being
        typed, and moving focus would end it. Labels come from the live registry, so
        a command that is not available right now still says what it is. */}
    <WhichKey
      pending={pendingChords}
      options={pendingOptions}
      platform={host.platform}
      labelFor={id => commandRegistryRef.current.find(command => command.id === id)?.label || id}
    />
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
      }} placeholder="Type a command…  @ session  # Project  : file" autofocus />
        {/* Named rather than implied: nobody discovers a prefix syntax by accident,
            and a palette that silently means four things is worse than one that
            means one. The active scope is highlighted so the row list is explicable. */}
        <div class="palette-scopes" role="tablist" aria-label="Palette scope">
          {(Object.entries(PALETTE_PREFIXES) as Array<[string, typeof paletteScopeName]>).map(([prefix, name]) =>
            <button key={name} type="button" role="tab" aria-selected={paletteScopeName === name}
              class={paletteScopeName === name ? 'active' : ''}
              onClick={() => { setPaletteQuery(name === 'commands' ? paletteTerm : `${prefix}${paletteTerm}`); paletteInput.current?.focus() }}
            ><kbd>{prefix}</kbd>{name}</button>)}
        </div>
        <div id="command-results" role="listbox">{shownCommands.map((command, index) => <button id={`command-${command.id.replaceAll(/[^a-zA-Z0-9_-]/g,'-')}`} role="option" aria-selected={index===paletteIndex} class={index === paletteIndex ? 'active' : ''} disabled={!command.available} title={command.disabledReason} onMouseEnter={() => setPaletteIndex(index)} onClick={() => { if (runNamedCommand(command.id)) { setPaletteOpen(false); setPaletteQuery('') } }}><span><small>{command.category}</small>{command.label}</span>{bindingFor(command.id, keymap) && <kbd>{displayChord(bindingFor(command.id, keymap), host.platform)}</kbd>}</button>)}</div>
      </div>
    </div>}

    {contextMenu && <div ref={el=>fitMenuInViewport(el)} class="context-menu" role="menu" aria-label={`Session actions for ${sessionName(contextMenu.session)}`} style={{ left: clampContextMenuLeft(contextMenu.x, innerWidth), top: Math.max(4, Math.min(contextMenu.y, innerHeight - 360)) }}>
      <div class="context-title">{sessionStateDot(contextMenu.session,rowConfig.dotShape,null,sessionStandingMark(contextMenu.session,rowConfig))}<strong>{sessionName(contextMenu.session)}</strong></div>
      {/* PID and branch, and nothing else. The startup timing that sat here with them is a
          number about how the session *began*, which nobody right-clicks a live session to
          learn — it belongs to the startup diagnostics, not to the menu you open to rename
          or kill something. */}
      <div class="context-session-info">
        <span title={isInactiveSession(contextMenu.session)?'This session has no running process':"Process ID of the session's root process"}>{isInactiveSession(contextMenu.session)?'No process':`PID ${contextMenu.session.pid}`}</span>
        {contextMenu.session.git.branch&&<span class="git-chip" title={`Git branch ${contextMenu.session.git.branch}${contextMenu.session.git.dirty?` · ${contextMenu.session.git.dirty} changed files`:' · clean'}`}>git:{contextMenu.session.git.branch}{contextMenu.session.git.dirty?` +${contextMenu.session.git.dirty}`:''}</span>}
      </div>
      <button class="menu-row" onClick={() => runNamedCommand('session.rename')}><span class="menu-row-icon" aria-hidden="true"><RenameIcon/></span><span class="menu-row-label">Rename</span></button>
      {isAgent(contextMenu.session)&&contextMenu.session.auto_named!==false&&!isEndedSession(contextMenu.session)&&<button class="menu-row" onClick={() => runNamedCommand('session.regenerateTitle')}><span class="menu-row-icon" aria-hidden="true"><SparkleIcon/></span><span class="menu-row-label">Regenerate title</span></button>}
      {/* No `Open in focused pane`. Clicking the row already does it, from the same list
          the menu was opened on, so the row existed only to say so a second time. */}
      {isInactiveSession(contextMenu.session)&&<button class="menu-row" onClick={() => runNamedCommand('session.resumeInactive')}><span class="menu-row-icon" aria-hidden="true"><ResumeIcon/></span><span class="menu-row-label">{contextMenu.session.backend==='shell'?'Restart terminal':'Resume'}</span></button>}
      {!isInactiveSession(contextMenu.session)&&['exited', 'crashed'].includes(contextMenu.session.state) && isAgent(contextMenu.session) && <button class="menu-row" onClick={() => runNamedCommand('session.resume')}><span class="menu-row-icon" aria-hidden="true"><ResumeIcon/></span><span class="menu-row-label">Resume</span></button>}
      {canRestartCold(contextMenu.session) && <button class="menu-row" onClick={() => runNamedCommand('session.restartCold')}><span class="menu-row-icon" aria-hidden="true"><RefreshIcon/></span><span class="menu-row-label">Restart terminal</span></button>}
      {activityBadges(contextMenu.session).length>0&&<button class="menu-row" onClick={() => runNamedCommand('session.clearStandingActivity')}><span class="menu-row-icon" aria-hidden="true"><ClearIcon/></span><span class="menu-row-label">Clear standing activity</span></button>}
      {contextMenu.session.state==='awaiting'&&contextMenu.session.awaiting_reason==='approval'&&<button class="menu-row" onClick={() => runNamedCommand('session.approveOnce')}><span class="menu-row-icon" aria-hidden="true"><CheckIcon/></span><span class="menu-row-label">Approve this request</span></button>}
      {/* Revoking is offered wherever a grant is standing, and only revoking:
          *granting* authority from a right-click on a row you are not looking at
          is the wrong affordance for it, and the pane's strip is where the mode,
          its rules, and its budget are all visible together. */}
      {effectiveApprovalMode(contextMenu.session,Date.now()/1000)!=='wait'&&<button class="menu-row" onClick={() => runNamedCommand('session.approvals.wait')}><span class="menu-row-icon" aria-hidden="true"><ShieldOffIcon/></span><span class="menu-row-label">Stop auto-approving here</span></button>}
      {isAgent(contextMenu.session)&&!isEndedSession(contextMenu.session)&&<button class="menu-row" onClick={()=>runNamedCommand('session.toggleRead')}><span class="menu-row-icon" aria-hidden="true"><MailIcon/></span><span class="menu-row-label">{isUnread(contextMenu.session,ackedTurns)?'Mark as read':'Mark as unread'}</span></button>}
      <button class="menu-row" onClick={() => runNamedCommand('session.copyId')}><span class="menu-row-icon" aria-hidden="true"><CopyIcon/></span><span class="menu-row-label">Copy session ID</span></button>
      {/* Pane-only, deliberately. A session's own ⋯ header menu is where its
          full detail lives, and this is an errand you run while working *in* a
          session rather than while pointing at one from a list. On a sidebar row
          or a tab title it was pure length, between the two things those menus are
          actually opened for, Rename and Kill. Same action, same command, one surface.
          Insert prompt template and Processes-and-previews left even from here: both
          open a whole surface of their own (the prompt library, the Resources dialog)
          from a menu whose other rows all act on the session immediately, and both are
          a command and a drawer tab away in the place you already are. */}
      {contextMenu.source==='pane'&&<button class="menu-row" onClick={() => runNamedCommand('session.copyCwd')}><span class="menu-row-icon" aria-hidden="true"><CopyPathIcon/></span><span class="menu-row-label">Copy working directory</span></button>}
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
      {voiceStatus?.enabled&&isAgent(contextMenu.session)&&<MenuGroup id="session-voice" label={`Read aloud · ${voiceModeLabel(effectiveVoiceMode(contextMenu.session))}`} icon={<SpeakerIcon/>} openId={menuGroup} onOpenChange={setMenuGroup} hint="Spoken replies for this session">
        {/* Four flat rows for a setting most sessions never change, sitting between
            the actions this menu exists for. Behind one row carrying its current
            mode, so the common case reads the state without opening anything.
            These four keep the plain rows: three are a radio set whose `✓` already
            marks the state, and an icon column beside a check column would draw two
            marks for one fact. */}
        {(['off','on_demand','auto'] as VoiceMode[]).map(mode=><button key={mode} onClick={()=>{void setVoiceMode(contextMenu.session,mode);setContextMenu(null)}}>{effectiveVoiceMode(contextMenu.session)===mode?'✓ ':''}{mode==='off'?'Off':mode==='on_demand'?'On demand':'Auto on reply'}</button>)}
        <button onClick={()=>{const target=contextMenu.session;setContextMenu(null);void speakLastReply(target)}}>Speak last reply now</button>
      </MenuGroup>}
      <div class="context-rule" />
      <button class="menu-row" onClick={() => runNamedCommand('session.broadcastMembership')}><span class="menu-row-icon" aria-hidden="true"><BroadcastIcon/></span><span class="menu-row-label">{contextMenu.session.broadcast ? 'Remove from broadcast' : 'Add to broadcast'}</span></button>
      {!isEndedSession(contextMenu.session)&&<button class="menu-row" onClick={() => runNamedCommand('session.standDown')}><span class="menu-row-icon" aria-hidden="true"><PowerIcon/></span><span class="menu-row-label">Stand down</span></button>}
      {/* One control, two marks: a live session is stopped (power), an ended one is
          only cleared off the list (bin). The label already switches; the icon has to
          switch with it or it contradicts the word beside it. */}
      <button class="menu-row danger" onClick={() => runNamedCommand('session.killImmediate')}><span class="menu-row-icon" aria-hidden="true">{isEndedSession(contextMenu.session) ? <TrashIcon/> : <PowerIcon/>}</span><span class="menu-row-label">{isEndedSession(contextMenu.session) ? 'Remove from sidebar' : 'Kill and remove'}</span></button>
      {/* The sweep, offered only from a row that is itself dead and only when it would
          clear more than that row. Ended sessions arrive in runs - a wave of worktree
          agents finishes, a Project is left overnight - and clearing them one row at a
          time is the same click repeated with the list re-sorting under the pointer
          between each. At a count of one it is the row directly above it, word for word,
          and a menu that offers the same act twice makes both rows worth reading to find
          out they are the same. Beneath the single remove rather than above it: the
          specific thing the menu was opened on comes first, the bulk act second. */}
      {isEndedSession(contextMenu.session)&&!isInactiveSession(contextMenu.session)&&clearEndedCount>1&&<button class="menu-row danger" onClick={() => runNamedCommand('session.clearEnded')}><span class="menu-row-icon" aria-hidden="true"><TrashSweepIcon/></span><span class="menu-row-label">Remove all {clearEndedCount} ended sessions</span></button>}
    </div>}

    {projectMenu && <div ref={el=>fitMenuInViewport(el)} class="context-menu" role="menu" aria-label={`Project actions for ${projectMenu.project.name}`} style={{ left: clampContextMenuLeft(projectMenu.x, innerWidth), top: Math.max(4, Math.min(projectMenu.y, innerHeight - 320)) }}>
      <div class="context-title"><strong>{projectMenu.project.name}</strong></div>
      {/* Starting work belongs to the Run button (sidebar header, every Project row,
          and the mobile rail), which offers the same backends plus Project tasks —
          duplicating it here left two doors to one action. */}
      {/* Two surfaces the app menu opens globally, prefiltered to this Project — and only
          two. The category headers went with the rest: a heading that labels three rows in
          a menu of nine is a fifth of the height spent saying what each icon now says.
          Notes, Processes, Fleet queue, and Browse files all left because each is a drawer
          tab or a dialog that opens on the *selected* Project anyway, so right-clicking a
          Project row to reach them was a second route to a place one click away — and the
          two that stayed are the ones with no such home. Collapse-in-sidebar left because
          clicking the Project header is the fold, and the two Move rows left because
          long-press drag is the reorder path and the buttons could only ever step one
          place at a time. */}
      <button class="menu-row" onClick={() => runNamedCommand('history.openProject')}><span class="menu-row-icon" aria-hidden="true"><HistoryIcon/></span><span class="menu-row-label">Session history</span></button>
      <button class="menu-row" onClick={() => runNamedCommand('prompts.openProject')}><span class="menu-row-icon" aria-hidden="true"><PromptsIcon/></span><span class="menu-row-label">Prompt library</span></button>
      <button class="menu-row" onClick={() => runNamedCommand('project.reveal')}><span class="menu-row-icon" aria-hidden="true"><RevealIcon/></span><span class="menu-row-label">Reveal in Explorer</span></button>
      {/* A Group is a list, so it is a list: a pop-out that scrolls, exactly like the
          Maintenance and Run menus. A `Dropdown` would also do the job now, but a picker
          nested inside a context menu would open a second overlay above one, and every row
          around this one is a `.menu-row` — the pop-out is what makes it read as one menu. */}
      <MenuGroup id="project-group" label={`Group · ${projectGroups.find(group=>group.id===projectMenu.project.group_id)?.name||'Ungrouped'}`} icon={<GroupIcon/>} openId={menuGroup} onOpenChange={setMenuGroup} hint="Which sidebar Group holds this Project">
        {[{id:'',name:'Ungrouped'},...projectGroups].map(group=>{
          const active=(projectMenu.project.group_id||'')===group.id
          return <button key={group.id||'ungrouped'} class="menu-row" role="menuitemradio" aria-checked={active} onClick={()=>void assignProjectGroup(projectMenu.project,group.id||null)}><span class="menu-row-icon" aria-hidden="true">{active?<CheckIcon/>:<GroupIcon/>}</span><span class="menu-row-label">{group.name}</span></button>
        })}
        <div class="context-rule" />
        {/* Creating from here also *moves* this Project into what it creates. Opening the
            same empty dialog and leaving the Project where it was would make the row a
            detour to the sidebar menu rather than an answer to "put this somewhere new". */}
        <button class="menu-row" onClick={()=>{const target=projectMenu.project;setProjectMenu(null);setGroupEdit({name:'',adoptProjectId:target.id})}}><span class="menu-row-icon" aria-hidden="true"><PlusIcon/></span><span class="menu-row-label">Create new group</span></button>
      </MenuGroup>
      <button class="menu-row" onClick={() => runNamedCommand('project.rename')}><span class="menu-row-icon" aria-hidden="true"><RenameIcon/></span><span class="menu-row-label">Rename</span></button>
      <button class="menu-row" onClick={() => runNamedCommand('project.settings')}><span class="menu-row-icon" aria-hidden="true"><CogIcon/></span><span class="menu-row-label">Project settings</span></button>
      <div class="context-rule" />
      {confirmHideId!==projectMenu.project.id&&<button class="menu-row" onClick={()=>{const target=projectMenu.project;if(canHideProject(openWorkFor(target))){setProjectMenu(null);void hideProject(target)}else setConfirmHideId(target.id)}}><span class="menu-row-icon" aria-hidden="true"><HideIcon/></span><span class="menu-row-label">Hide from sidebar</span></button>}
      {confirmHideId===projectMenu.project.id&&<>
        <div class="context-subtitle">CLOSE OPEN WORK TO HIDE</div>
        <div class="context-note">{describeOpenWork(openWorkFor(projectMenu.project))||'No live work'} still attached. Hiding would strand it off-screen.</div>
        <button class="menu-row danger" onClick={()=>{const target=projectMenu.project;setProjectMenu(null);setConfirmHideId(null);void closeWorkAndHideProject(target).catch(cause=>{setError(cause instanceof Error?cause.message:String(cause));void refresh()})}}><span class="menu-row-icon" aria-hidden="true"><HideIcon/></span><span class="menu-row-label">Close it &amp; hide</span></button>
        <button class="menu-row" onClick={()=>setConfirmHideId(null)}><span class="menu-row-icon" aria-hidden="true"><CloseIcon/></span><span class="menu-row-label">Cancel</span></button>
      </>}
      <button class="menu-row danger" onClick={() => runNamedCommand('project.delete')}><span class="menu-row-icon" aria-hidden="true"><TrashIcon/></span><span class="menu-row-label">Remove from swe-mux</span></button>
    </div>}

    {sidebarMenu&&<div ref={el=>fitMenuInViewport(el)} class="context-menu" role="menu" aria-label="Sidebar actions" style={{left:clampContextMenuLeft(sidebarMenu.x,innerWidth),top:Math.max(4,Math.min(sidebarMenu.y,innerHeight-300))}}>
      <div class="context-title"><strong>PROJECTS</strong></div>
      <button class="menu-row" onClick={()=>{setSidebarMenu(null);runNamedCommand('project.add')}}><span class="menu-row-icon" aria-hidden="true"><PlusIcon/></span><span class="menu-row-label">Add project…</span></button>
      <button class="menu-row" onClick={()=>{setSidebarMenu(null);runNamedCommand('project.create')}}><span class="menu-row-icon" aria-hidden="true"><FilesIcon/></span><span class="menu-row-label">Manage projects…</span></button>
      <button class="menu-row" onClick={()=>{setSidebarMenu(null);setGroupEdit({name:''})}}><span class="menu-row-icon" aria-hidden="true"><GroupIcon/></span><span class="menu-row-label">Create group</span></button>
      <button class="menu-row" onClick={()=>{setSidebarMenu(null);runNamedCommand('history.open')}}><span class="menu-row-icon" aria-hidden="true"><HistoryIcon/></span><span class="menu-row-label">Session history</span></button>
      <button class="menu-row" onClick={()=>{setSidebarMenu(null);runNamedCommand('notes.browse')}}><span class="menu-row-icon" aria-hidden="true"><NotePencilIcon/></span><span class="menu-row-label">Notes…</span></button>
      <button class="menu-row" onClick={()=>{setSidebarMenu(null);runNamedCommand('processes.all')}}><span class="menu-row-icon" aria-hidden="true"><ProcessesIcon/></span><span class="menu-row-label">Resources…</span></button>
      <div class="context-rule" />
      <button class="menu-row" onClick={()=>{setSidebarMenu(null);runNamedCommand('settings.open')}}><span class="menu-row-icon" aria-hidden="true"><CogIcon/></span><span class="menu-row-label">All Settings…</span></button>
      <button class="menu-row" title="Reload daemon (keep sessions)" onClick={()=>{setSidebarMenu(null);runNamedCommand('daemon.reload')}}><span class="menu-row-icon" aria-hidden="true"><ServerIcon/></span><span class="menu-row-label">Reload daemon (keep sessions)</span></button>
      <button class="menu-row" title="Rebuild + redeploy app (keep sessions)" onClick={()=>{setSidebarMenu(null);runNamedCommand('app.redeploy')}}><span class="menu-row-icon" aria-hidden="true"><PackageIcon/></span><span class="menu-row-label">Rebuild + redeploy app (keep sessions)</span></button>
    </div>}

    {/* The configurator's harness chooser. Only ever reached by an explicit
        modifier press, and only when more than one agent is available — a menu
        offering the single thing a plain press would already have done is worse
        than no menu. The default is marked rather than reordered so the list
        stays in the registry's own order between presses. */}
    {configuratorMenu&&<div ref={el=>fitMenuInViewport(el)} class="context-menu" role="menu" aria-label="Launch the configurator with" style={{left:clampContextMenuLeft(configuratorMenu.x,innerWidth),top:Math.max(4,Math.min(configuratorMenu.y,innerHeight-200))}}>
      <div class="context-title"><strong>ASK ABOUT SWE-MUX</strong></div>
      {(configuratorOptions?.harnesses||[]).map(name=>
        <button key={name} class="menu-row" onClick={()=>void launchConfigurator(name)}>
          <span class="menu-row-label">{harnessDisplayName(name)}</span>
          {name===configuratorOptions?.default_harness&&<span class="menu-row-note">default</span>}
        </button>)}
      <div class="context-rule" />
      <button class="menu-row" onClick={()=>{setConfiguratorMenu(null);openSettings('Harnesses')}}><span class="menu-row-icon" aria-hidden="true"><CogIcon/></span><span class="menu-row-label">Choose a default harness…</span></button>
    </div>}

    {/* A Group's own menu: the three things a Group can be told to do, in the place a
        right-click already looks for them. Rename mirrors the header's ✎ and fold mirrors
        clicking the header, so the menu is discoverable rather than exclusive; delete has no
        other home, which is why it needed one. */}
    {groupMenu&&(()=>{
      const group=projectGroups.find(item=>item.id===groupMenu.groupId)
      // The Group was deleted or unregistered under the open menu.
      if(!group)return null
      const held=projects.filter(project=>project.group_id===group.id)
      const folded=isBucketCollapsed(sidebarOrder,group.id)
      const confirming=confirmGroupDeleteId===group.id
      return <div ref={el=>fitMenuInViewport(el)} class="context-menu" role="menu" aria-label={`Group actions for ${group.name}`} style={{left:clampContextMenuLeft(groupMenu.x,innerWidth),top:Math.max(4,Math.min(groupMenu.y,innerHeight-220))}}>
        <div class="context-title"><strong>{group.name}</strong><small>{held.length?`${held.length} Project${held.length===1?'':'s'}`:'Empty'}</small></div>
        <button onClick={()=>{setGroupMenu(null);setGroupEdit({id:group.id,name:group.name})}}>Rename group…</button>
        <button onClick={()=>{setGroupMenu(null);setSidebarOrder(toggleBucketCollapsed(sidebarOrder,group.id))}}>{folded?'Expand group':'Collapse group'}</button>
        {!confirming&&<button class="danger" onClick={()=>setConfirmGroupDeleteId(group.id)}>Delete group…</button>}
        {confirming&&<>
          <div class="context-subtitle">DELETE THIS GROUP</div>
          {/* What survives, stated before the button that does it: the fear this dialog
              exists to answer is that the Projects go with the Group. */}
          <div class="context-note">{held.length?`${held.length} Project${held.length===1?'':'s'} return to the root list.`:'The Group is empty.'} No folder, session, layout, or history is touched.</div>
          <button class="danger" onClick={()=>void deleteGroup(group)}>Delete “{group.name}”</button>
          <button onClick={()=>setConfirmGroupDeleteId(null)}>Cancel</button>
        </>}
      </div>
    })()}

    {/* One sort for the whole sidebar, Groups included. It was per section once, so a
        hand-arranged shortlist and an alphabetical pile could coexist; that put a ⇅ on every
        Group header for a preference that in practice was set the same everywhere, so the
        modes collapsed into one and the control moved to the PROJECTS header. Group
        placement followed it here for a sharper reason: as its own setting it could only
        order Groups among Groups, below every root Project, so no mode could lift a Group
        for the work inside it. */}
    {sortMenu&&<div ref={el=>fitMenuInViewport(el)} class="context-menu" role="menu" aria-label="Sort Projects and Groups" style={{left:clampContextMenuLeft(sortMenu.x,innerWidth),top:Math.max(4,Math.min(sortMenu.y,innerHeight-300))}}>
      <div class="context-title"><strong>SORT PROJECTS</strong></div>
      {PROJECT_SORT_OPTIONS.map(option=>{
        const active=sidebarOrder.projectSort===option.id
        return <button key={option.id} title={option.hint} aria-checked={active} role="menuitemradio" onClick={()=>{setSidebarOrder(setProjectSortMode(sidebarOrder,option.id));setSortMenu(null)}}>{active?'✓ ':''}{option.label}</button>
      })}
      <div class="context-note">Every mode but Manual sorts Groups in among the root Projects, keyed by the Project in them that leads. Manual keeps Groups below the root list, in the order you dragged them; placing a Project or a Group by hand is what returns the sidebar to it.</div>
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

    {/* Static previews only. A session-owned preview row has no equivalent: it follows its
        listener and is retired when that stops, so there is nothing here for it to do. */}
    {staticPreviewMenu&&<div ref={el=>fitMenuInViewport(el)} class="context-menu" role="menu" aria-label={`Preview actions for ${staticPreviewMenu.label}`} style={{left:clampContextMenuLeft(staticPreviewMenu.x,innerWidth),top:Math.max(4,Math.min(staticPreviewMenu.y,innerHeight-90))}}>
      <div class="context-title"><strong>{staticPreviewMenu.label}</strong></div>
      <button onClick={()=>{
        const target=previews[staticPreviewMenu.previewId]
        setStaticPreviewMenu(null)
        if(target)void removeStaticPreview(target)
      }}>Close preview</button>
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

    {drawerDisplayMenu&&<div ref={el=>fitMenuInViewport(el)} class="context-menu drawer-display-menu" role="menu" aria-label={`${drawerDisplayMenu.surface==='tabs'?'Drawer tabs':'Right rail'} options`} style={{left:clampContextMenuLeft(drawerDisplayMenu.x,innerWidth),top:Math.max(4,Math.min(drawerDisplayMenu.y,innerHeight-200))}}>
      <div class="context-title"><strong>{drawerDisplayMenu.surface==='tabs'?'DRAWER TABS':'RIGHT RAIL'}</strong></div>
      {(()=>{const display=drawerDisplayMenu.surface==='tabs'?drawerTabDisplay:utilityRailDisplay;return <button role="menuitemcheckbox" aria-checked={display==='title'} onClick={()=>void persistDrawerDisplay(drawerDisplayMenu.surface,display==='icon'?'title':'icon')}>{display==='title'?'✓ ':''}Text labels</button>})()}
      <div class="context-rule" />
      {/* Hiding the tab you right-clicked is the common action and stays flat; the full
          checklist that undoes it is one row down, and its label carries the count so a
          rail missing something says so without being opened. Refusing to hide the last
          tab is what keeps this menu reachable at all — it lives on the tab strip. */}
      {drawerDisplayMenu.tab&&(()=>{
        const tab=drawerDisplayMenu.tab
        const blocked=!canHideDrawerTab(hiddenDrawerTabs,tab)
        const segment=resolveDrawerSegment(tab,activeDrawerPresentation.selected_segments[tab],{
          hasTranscript:hasHarnessTranscript(active?.backend),
          isAgentSession:!!active&&isAgentBackend(active.backend),
        })
        const topic=helpTopicForDrawer(tab,segment)
        return <>
          {topic&&<button role="menuitem" onClick={()=>{setDrawerDisplayMenu(null);openHelp(topic.id)}}>Help: {topic.title}</button>}
          <button
            role="menuitem"
            disabled={blocked}
            title={blocked?'The side panel must keep at least one tab.':undefined}
            onClick={()=>{setDrawerDisplayMenu(null);setDrawerTabHidden(tab,true)}}
          >Hide {drawerTab(tab).label}</button>
        </>
      })()}
      <MenuGroup
        id="drawer-visible-tabs"
        label={`Panels · ${DRAWER_TABS.length-hiddenDrawerTabs.length} of ${DRAWER_TABS.length}`}
        openId={menuGroup}
        onOpenChange={setMenuGroup}
        hint="Choose which tabs the side panel carries"
      >
        {DRAWER_TABS.map(tab=>{
          const shown=!hiddenDrawerTabs.includes(tab.id)
          const blocked=shown&&!canHideDrawerTab(hiddenDrawerTabs,tab.id)
          return <button
            key={tab.id}
            role="menuitemcheckbox"
            aria-checked={shown}
            disabled={blocked}
            title={blocked?'The side panel must keep at least one tab.':tab.title}
            onClick={()=>setDrawerTabHidden(tab.id,shown)}
          >{shown?'✓ ':''}{tab.label}</button>
        })}
        <div class="context-rule" />
        <button role="menuitem" disabled={!hiddenDrawerTabs.length} onClick={()=>{setDrawerDisplayMenu(null);showAllDrawerTabs()}}>Show all</button>
      </MenuGroup>
      <div class="context-rule" />
      <button role="menuitem" disabled={!clipboardOpen} onClick={()=>{setDrawerDisplayMenu(null);setClipboardOpen(false)}}>Collapse utility drawer</button>
    </div>}

    {mainMenuOpen && <div data-tutorial="main-menu" class="context-menu main-menu" role="menu" aria-label="swe-mux menu">
      <div class="context-title"><strong>swe-mux menu</strong></div>
      {/* The app-wide viewers, unfolded. They spent a while behind a `Utilities` row on
          the argument that ten of them made the menu a wall — but the wall was ten, and
          the consolidation that turned four resource modals into one Resources dialog took
          it to seven. Seven rows is a menu; a fold over seven rows is a click that buys
          back four rows of height and costs one on every visit, and it hid the counts on
          Fleet queue and Notifications behind a summary badge that had to be invented to
          replace them. Those counts are back where they belong, on the rows themselves.
          Right-clicking a Project row still opens the Project-scoped versions prefiltered
          to it; anything that acts on one Project lives there, not here. */}
      <button class="menu-row" onClick={() => runNamedCommand('history.open')}><span class="menu-row-icon" aria-hidden="true"><HistoryIcon/></span><span class="menu-row-label">Session history</span></button>
      <button class="menu-row" onClick={() => runNamedCommand('notes.browse')}><span class="menu-row-icon" aria-hidden="true"><NotePencilIcon/></span><span class="menu-row-label">Notes</span></button>
      <button class="menu-row" onClick={() => runNamedCommand('queue.fleet')}><span class="menu-row-icon" aria-hidden="true"><QueueClockIcon/></span><span class="menu-row-label">Fleet queue{queuePendingTotal?` [${queuePendingTotal} pending]`:''}</span></button>
      <button class="menu-row" onClick={()=>runNamedCommand('prompts.open')}><span class="menu-row-icon" aria-hidden="true"><PromptsIcon/></span><span class="menu-row-label">Prompt library</span></button>
      <button class="menu-row" onClick={()=>runNamedCommand('clipboard.open')}><span class="menu-row-icon" aria-hidden="true"><ClipboardHistoryIcon/></span><span class="menu-row-label">Clipboard history</span></button>
      {/* One row for processes, bandwidth, storage, and fleet activity — segments of one
          dialog. The named entry points survive as palette commands and as the sidebar's
          resource chip, which lands on the segment it was already showing. */}
      <button class="menu-row" onClick={() => runNamedCommand('resources.open')}><span class="menu-row-icon" aria-hidden="true"><ProcessesIcon/></span><span class="menu-row-label">Resources</span></button>
      {/* Spend is the eighth row, and it is worth the row. It was a segment of Resources,
          where the surface named for money had no total on its first screen and three of
          its six tabs measured neither a token nor a dollar. The menu-row budget is real
          and this is what it is for: a subject nobody finds by guessing which meter it was
          filed under. */}
      <button class="menu-row" onClick={() => runNamedCommand('usage.open')}><span class="menu-row-icon" aria-hidden="true"><SpendIcon/></span><span class="menu-row-label">Usage &amp; spend</span></button>
      <button class="menu-row" onClick={() => runNamedCommand('notifications.open')}><span class="menu-row-icon" aria-hidden="true"><AlertsIcon/></span><span class="menu-row-label">Notifications{notificationUnread?` [${notificationUnread} new]`:''}</span></button>
      <div class="context-rule"/>
      {/* The Project registry is reachable from the sidebar's own PROJECTS header too,
          beside the tree it edits. It is repeated here on purpose: the header button is
          discoverable only once the sidebar is open and the header is in view, and this
          menu is where every other app-wide surface is looked for. Two doors to one
          registry is the lesser cost. */}
      <button class="menu-row" onClick={() => runNamedCommand('project.create')}><span class="menu-row-icon" aria-hidden="true"><FilesIcon/></span><span class="menu-row-label">Projects</span></button>
      <button class="menu-row" onClick={() => runNamedCommand('actions.configure')}><span class="menu-row-icon" aria-hidden="true"><CommandKeyIcon/></span><span class="menu-row-label">Configure Actions</span></button>
      <button class="menu-row" onClick={() => runNamedCommand('hooks.open')}><span class="menu-row-icon" aria-hidden="true"><DashboardIcon/></span><span class="menu-row-label">Automation Dashboard</span></button>
      <MenuGroup id="maintenance" label="Maintenance" icon={<WrenchIcon/>} openId={menuGroup} onOpenChange={setMenuGroup} hint="Reload and rebuild without reaping live sessions">
        {/* Three rows that all mean "reload", so the marks name the thing reloaded rather
            than the act: the daemon, the frozen bundle, the page. */}
        <button class="menu-row" title="Reload daemon (keep sessions)" onClick={() => runNamedCommand('daemon.reload')}><span class="menu-row-icon" aria-hidden="true"><ServerIcon/></span><span class="menu-row-label">Reload daemon (keep sessions)</span></button>
        <button class="menu-row" title="Rebuild + redeploy app (keep sessions)" onClick={() => runNamedCommand('app.redeploy')}><span class="menu-row-icon" aria-hidden="true"><PackageIcon/></span><span class="menu-row-label">Rebuild + redeploy app (keep sessions)</span></button>
        <button class="menu-row" onClick={() => runNamedCommand('ui.reload')}><span class="menu-row-icon" aria-hidden="true"><RefreshIcon/></span><span class="menu-row-label">Reload UI</span></button>
      </MenuGroup>
      <div class="context-rule"/>
      {/* Broadcast is an app-wide input mode, not a Project action: membership is
          per-session, set from a session's own context menu. */}
      <button class="menu-row" onClick={() => { setMainMenuOpen(false); runNamedCommand('broadcast.toggle') }}><span class="menu-row-icon" aria-hidden="true"><BroadcastIcon/></span><span class="menu-row-label">{broadcast ? 'Stop broadcasting input' : 'Start broadcasting input'}</span></button>
      {/* The palette is a search box over every command, so it wears the magnifier the
          sidebar filter wears. Nothing else in this menu is a search. */}
      <button class="menu-row" onClick={() => { setMainMenuOpen(false); runNamedCommand('palette.open') }}><span class="menu-row-icon" aria-hidden="true"><SearchIcon/></span><span class="menu-row-label">Command palette</span><span class="menu-hint">ctrl alt p</span></button>
      <div class="context-rule"/>
      <button class="menu-row" onClick={() => runNamedCommand('settings.open')}><span class="menu-row-icon" aria-hidden="true"><CogIcon/></span><span class="menu-row-label">Settings</span></button>
      {/* Last row, and the only one that answers "what is any of this". It is repeated
          as a palette command and a voice phrase rather than living only here, because
          the person who needs it is the person who does not know this menu exists. */}
      <button class="menu-row" onClick={() => runNamedCommand('help.open')}><span class="menu-row-icon" aria-hidden="true"><HelpIcon/></span><span class="menu-row-label">Help</span></button>
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
    {/* Only the daemon-down stage blocks. While the build runs the app is fully
        usable and the corner chip is the whole of the UI's report. */}
    {redeployDown&&<div class="modal-layer daemon-reload-layer" role="alertdialog" aria-modal="true" aria-label="App restarting"><div class="modal daemon-reload-modal"><h2>Restarting the app…</h2><p>The rebuilt app is being swapped in and restarted around your live sessions, which are held by the PTY supervisor and are not affected. A cold start can take a few minutes; this page reloads by itself when it comes back.</p></div></div>}
    {redeployConfirmOpen&&<div class="modal-layer daemon-reload-layer" role="alertdialog" aria-modal="true" aria-label="Confirm redeploy" onClick={()=>setRedeployConfirmOpen(false)}><div class="modal daemon-reload-modal" onClick={event=>event.stopPropagation()}><h2>Rebuild + redeploy app?</h2><p>Rebuilds the frozen desktop app from source and restarts it around your live sessions. The build takes a few minutes and runs alongside the app you are using now, so you can keep working until it restarts. A failed build leaves the current app running.</p>{interruptionSummary(redeployInterruptions)&&<p class="redeploy-interrupts"><strong>{interruptionSummary(redeployInterruptions)}</strong><span>{redeployInterruptions?.note}</span></p>}{holderWarning(redeployHolders)&&<p class="redeploy-interrupts redeploy-blocked"><strong>{holderWarning(redeployHolders)}</strong><span>Stop those processes (or close the tab or session they belong to) first - the app bundle cannot be replaced while they hold it open.</span></p>}<div class="modal-actions"><button type="button" onClick={()=>setRedeployConfirmOpen(false)}>Cancel</button><button type="button" class="primary" onClick={()=>void startRedeploy()}>Rebuild + redeploy</button></div></div></div>}

    {historyOpen&&<HistoryBrowser projects={orderedProjects} initialProjectId={historyScope} initialEntryId={historyEntry} onClose={()=>setHistoryOpen(false)} onResume={resumeHistoryEntry} onScheduleResume={scheduleResumeFromHistory} onHandoff={openHandoff}/>}

    {/* Opened with no target, the registry lands on the Project in focus rather than on
        whichever one sorts first: "manage Projects" almost always means the one being
        worked in, and the alphabetically-first Project is nobody's intent. */}
    {projectsManagerOpen&&<ProjectsManager projects={projects} groups={projectGroups} sessions={sessions} profiles={profiles} initialProjectId={projectsManagerFocus?.projectId||projectId} initialSetting={projectsManagerFocus?.setting} revealToken={revealToken} onClose={()=>{setProjectsManagerOpen(false);setProjectsManagerFocus(null)}} onAdd={()=>void createProject()} onAddGroup={()=>setGroupEdit({name:''})} onOpen={project=>{setProjectId(project.id);setProjectsManagerOpen(false)}} onPatch={patchManagedProject} onRemove={removeProject}/>}

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
        <label>Group<Dropdown value={projectCreate.group_id} onChange={group=>setProjectCreate(value=>({...value,group_id:group}))} options={[{value:'',label:'Ungrouped'},...projectGroups.map(group=>({value:group.id,label:group.name}))]}/></label>
        {/* One choice instead of twenty checkboxes found later. Everything in this set
            reads transcripts swe-mux already stores and never calls a model, which is
            what makes it safe to default on; the scan timeline is the one that spends
            and is deliberately not in it. A set the install-wide ceiling blocks is
            greyed with the reason, because the daemon would refuse the grant
            (`automation_globally_disabled`) and a checkbox that then errors is worse
            than one that says why it is off. */}
        <label class="check project-create-automations">
          <input type="checkbox" checked={projectCreate.automations&&!startingSetBlocked('recommended')} disabled={startingSetBlocked('recommended')} onChange={event=>setProjectCreate(value=>({...value,automations:event.currentTarget.checked}))} />
          <span><strong>Turn on the free analysis automations</strong>
          <small>Change map, findings detectors, and commit provenance, for this Project.
          Free — they read what swe-mux already captures and never call a model. Recorded
          in the Project’s <code>.swe-mux/config.toml</code>, and changeable any time.</small></span>
        </label>
        {/* The two optional sets. Never defaulted on: one can bill and the other hands
            agents real authority, so each is a deliberate choice rather than part of
            the common name-folder-Enter path. Both apply through the same grant path
            as the free set, dependency closure and audit record included. */}
        <label class="check project-create-automations">
          <input type="checkbox" checked={projectCreate.llm&&!startingSetBlocked('llm')} disabled={startingSetBlocked('llm')} onChange={event=>setProjectCreate(value=>({...value,llm:event.currentTarget.checked}))} />
          <span><strong>Turn on the model-backed automations</strong>
          <small>Scan timeline (armed for every new session), adaptive session titles,
          and model narration, plus the detectors they rank over. These call your
          configured model and can cost money; the budgets are install-wide, in
          the Automation workspace.{grantsCatalogue&&!grantsCatalogue.llm.ready?' No verified model provider yet, so these stay inert until one is set up under Settings → Accounts.':''}{startingSetBlocked('llm')?' Part of this set is disabled install-wide in Automation → Policy.':''}</small></span>
        </label>
        <label class="check project-create-automations">
          <input type="checkbox" checked={projectCreate.autonomy&&!startingSetBlocked('autonomy')} disabled={startingSetBlocked('autonomy')} onChange={event=>setProjectCreate(value=>({...value,autonomy:event.currentTarget.checked}))} />
          <span><strong>Let agents act without per-request approval</strong>
          <small>Agents working in this Project can spawn sessions and start landings
          directly, each still under its hourly budget, with spawn-request review on for
          anything that still arrives as a draft. Interrupting or messaging into live
          sessions stays behind its own approval. Recorded in the Project’s
          <code>.swe-mux/config.toml</code>; lower it any time in the Projects registry.</small></span>
        </label>
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


    {handoffState&&<div class="modal-layer control-plane-modal-layer" role="dialog" aria-modal="true" aria-label="Handoff export" onMouseDown={event=>event.target===event.currentTarget&&setHandoffState(null)}><section class="modal control-plane-modal"><div class="modal-heading"><div><span>HANDOFF::EXPORT</span><h2>{historyName(handoffState.entry)}</h2></div><button aria-label="Close handoff" onClick={()=>setHandoffState(null)}>×</button></div><div class="control-plane-modal-body"><p>{handoffState.message}</p><textarea class="handoff-export" readOnly value={handoffState.markdown}/></div><div class="modal-footer"><span>read-only annotation export</span><button onClick={()=>{const blob=new Blob([handoffState.markdown],{type:'text/markdown'});const url=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=url;anchor.download=`handoff-${handoffState.entry.id}.md`;anchor.click();URL.revokeObjectURL(url)}}>Download</button><button class="primary" onClick={()=>void navigator.clipboard.writeText(handoffState.markdown).then(()=>setHandoffState(current=>current?{...current,message:'Copied to clipboard.'}:current)).catch(()=>setHandoffState(current=>current?{...current,message:'Clipboard blocked. Select the text and copy it manually.'}:current))}>Copy</button></div></section></div>}

    {sendToAgent&&<SendToAgentPicker request={sendToAgent} projects={orderedProjects} sessions={sessions} onClose={()=>setSendToAgent(null)} onSend={deliverToAgent}/>}

    {settingsOpen && SettingsView && <SettingsView activeUiScale={uiScale} onUiScalePreview={previewUiScaleConfig} initialSection={settingsSection} initialSetting={settingsSetting} revealToken={revealToken} voiceCommands={commands} onStartTutorial={startTutorial} onStartVoiceSetup={()=>{setSettingsOpen(false);setSettingsNavOpen(false);setVoiceSetupOpen(true)}} onLaunchConfigurator={harness=>void launchConfigurator(harness)} navOpen={settingsNavOpen} onNavOpenChange={setSettingsNavOpen} drawerHiddenTabs={hiddenDrawerTabs} onDrawerTabHidden={setDrawerTabHidden} onShowAllDrawerTabs={showAllDrawerTabs} onOpenUsage={()=>{setSettingsOpen(false);setUsageOpen('agents')}} onOpenAutomation={()=>{setSettingsOpen(false);openAutomation('policy')}} onClose={() => { setSettingsOpen(false); setSettingsNavOpen(false); void refresh(); void loadProfiles(); void loadConfig(false) }} />}

    {/* Both first-run surfaces are drawn from ONE decision (`firstRunSurface`), so
        "exactly one of them, ever" is a property of the function rather than of two
        conditions that have to agree. The harness panel leads and the tour waits; the
        reasoning is on the function. */}
    {firstRun === 'harness' && <HarnessSetup
      tierNeeded={experienceTierUnchosen}
      onDone={()=>{setHarnessSetupNeeded(false); void loadConfig(false); void refresh()}}
      // Handing off to Settings → Agents is a choice to configure by hand, so the tour must
      // not open on top of that. It is suppressed for this session only and NOT marked
      // complete: declining the harness panel is not declining the tour, and silently
      // consuming a first-run walk the user never saw is the more expensive mistake.
      onConfigureMore={()=>{setHarnessSetupNeeded(false); setTutorialOpen(false); openSettings('Agents')}}
    />}
    {voiceSetupOpen && <VoiceSetup onClose={()=>setVoiceSetupOpen(false)}
    />}

    {actionEditorOpen && <ActionEditorModal projectId={active?.project_id || activeProject?.id} onClose={() => setActionEditorOpen(false)} />}

    {/* Resolved from the live list each render, so a session that ends or is removed
        under the dialog closes it instead of leaving it aimed at a pane that is gone. */}
    {(()=>{const target=branchPickerId?sessions.find(item=>item.id===branchPickerId):null
      return target?<BranchPicker session={target} onClose={()=>setBranchPickerId(null)} onBranch={request=>runBranch(target,request)}/>:null})()}
    {promptLibraryOpen&&<PromptLibrary project={promptScope||activeProject} backend={(sessions.find(session=>session.id===promptTargetId)||active)?.backend} startCreating={promptLibraryCreating} onClose={()=>{setPromptLibraryOpen(false);setPromptTargetId(null);setPromptLibraryCreating(false)}} onInsert={text=>{const sid=promptTargetId||activeId;if(sid)void insertIntoTerminal(sid,text,false).catch(cause=>setError(cause instanceof Error?cause.message:String(cause)))}}/>}

    {resourcesOpen&&<ResourcesModal
      initial={resourcesOpen}
      initialSessionId={processSession?.id||null}
      initialProjectId={processScope}
      sessions={sessions}
      projects={projects}
      onAttached={attachPreview}
      onClose={()=>{setResourcesOpen(null);setProcessSession(null)}}
    />}
    {usageOpen&&<UsageModal
      initial={usageOpen}
      onConfigure={()=>{setUsageOpen(null);openSettings('Usage analytics')}}
      onOpenAutomation={()=>{setUsageOpen(null);openAutomation('usage',activeProject?.id)}}
      onClose={()=>setUsageOpen(null)}
    />}
    {fleetQueue&&<FleetQueue projects={projects} initialProjectId={fleetQueue.projectId} onOpenQueue={sessionId=>void openQueueForSession(sessionId)} onClose={()=>setFleetQueue(null)}/>}
    {automationOpen&&<AutomationDashboard projects={projects} initialView={automationOpen.view} initialProjectId={automationOpen.projectId} initialSetting={automationOpen.setting} revealToken={automationOpen.revealToken} onClose={()=>setAutomationOpen(null)}
      // Alerts and spend are mirrored inside the dashboard now (the same
      // AttentionInbox and AutomationSpendView components the drawer and the
      // Usage dialog draw), so the old escape links are gone rather than
      // duplicated.
      onOpenSession={sessionId=>{const session=sessions.find(item=>item.id===sessionId);if(!session){setError('The automation session is no longer live.');return}setAutomationOpen(null);void selectSession(session)}}/>}



    {notificationToast&&<button class="notification-toast" aria-live="assertive" onClick={()=>{setNotificationToast(null);openNotifications()}}><strong>{notificationToast.session_name||'daemon'}</strong><span>{notificationToast.type.replaceAll('_',' ')}</span><small>open notifications</small></button>}

    {firstRun === 'tutorial' && <GuidedTutorial hasProject={projects.length>0} onNavigate={navigateTutorial} onExit={closeTutorial} onComplete={closeTutorial}/>}

    {helpTopicOpen !== null && <HelpModal
      initialTopic={helpTopicOpen || null}
      onClose={()=>setHelpTopicOpen(null)}
      onStartTutorial={startTutorial}
      onOpenConfigurator={()=>{setHelpTopicOpen(null);void launchConfigurator()}}
      configurator={{enabled:configuratorLaunch.enabled,reason:configuratorLaunch.reason}}
    />}

    {/* One stack, not two independently anchored toasts: both used to be pinned
        to the same corner and would render exactly on top of each other. */}
    {(redeployNotice || error) && <div class="toast-stack">
      {redeployNotice && <div class="toast redeploy-notice" role="alert" onClick={() => setRedeployNotice('')}>{redeployNotice}<span>×</span></div>}
      {error && <div class="toast" onClick={() => setError('')}>{error}<span>×</span></div>}
    </div>}
  </div>
}
