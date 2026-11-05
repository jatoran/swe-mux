import { t } from './localization/index.ts'
/**
 * Deep links from a gated surface to the exact control that ungates it.
 *
 * A surface that cannot work because something is switched off has three jobs: say so, name
 * the switch, and take you to it. The first two are prose each surface owns; this module is
 * the third. A target names the overlay to open, the Settings tab to land on when that is the
 * overlay, and the `data-setting` id of the control to reveal once there. `settingReveal.ts`
 * does the revealing and `SettingLink.tsx` is how a surface asks for one.
 *
 * Pure and apart from every renderer, for the same reason `settingsTabs.ts` is: "where does
 * this switch live" is worth asserting without mounting a modal. `test/settingTargets.test.ts`
 * checks every target's `data-setting` against the source that must carry it, so renaming a
 * control fails a test rather than quietly stranding the links that point at it.
 *
 * Three destinations own controls: ordinary install Settings, the unified Automation
 * workspace, and the Projects registry for non-automation Project policy.
 */

export type SettingSurface = 'settings' | 'project' | 'automation'

export type SettingTarget = {
  surface: SettingSurface
  /** Settings only: the deep-link section name, resolved to a tab by `tabForSection`. */
  section?: string
  /** The `data-setting` id to reveal. Omitted for a target that is only an area. */
  setting?: string
  /** What the control does, in the words the arriving user will read. */
  label: string
  /** Where it lives, for the link's title. */
  where: string
}

/**
 * Every deep-linkable switch, keyed by a stable id the calling surface names.
 *
 * `setting` values are the daemon's own config keys for install settings and `automation:<id>`
 * for a Project's control-plane opt-ins, so a target reads as what it points at and greps
 * straight to the control.
 */
export const SETTING_TARGETS = {
  'clipboard.history': {
    surface: 'settings', section: 'Input', setting: 'clipboard_history_enabled',
    get label() { return t("settingTargets.5194a6b4e2") }, get where() { return t("settingTargets.9777cfce5d") },
  },
  'queue.autoDelivery': {
    surface: 'settings', section: 'Prompt queue', setting: 'auto_delivery_enabled',
    get label() { return t("settingTargets.a7ed28c4c2") }, get where() { return t("settingTargets.5deb897fba") },
  },
  // How long a conversation may sit idle before its grant lapses. A *value*
  // rather than a switch - a gate can honestly offer "turn this on", never "pick
  // a number" - so it stays a link, and the surfaces that report a lapse route
  // here rather than growing a second editor for the same bound.
  'queue.grantWindow': {
    surface: 'settings', section: 'Prompt queue', setting: 'auto_delivery_session_ttl_minutes',
    get label() { return t("settingTargets.827f88a0d2") }, get where() { return t("settingTargets.5deb897fba") },
  },
  'queue.agentMessaging': {
    surface: 'settings', section: 'Prompt queue', setting: 'agent_messaging_enabled',
    get label() { return t("settingTargets.6671bb2f54") }, get where() { return t("settingTargets.5deb897fba") },
  },
  'approvals.autoAnswer': {
    surface: 'settings', section: 'Prompt queue', setting: 'approval_auto_enabled',
    get label() { return t("settingTargets.98473e42a5") }, get where() { return t("settingTargets.5deb897fba") },
  },
  'usage.ccusage': {
    surface: 'settings', section: 'Usage', setting: 'ccusage_enabled',
    get label() { return t("settingTargets.d2ede78359") }, get where() { return t("settingTargets.9f4f94caba") },
  },
  'voice.tts': {
    surface: 'settings', section: 'Voice', setting: 'tts_enabled',
    get label() { return t("settingTargets.4779fa4d8d") }, get where() { return t("settingTargets.b2416e9ca6") },
  },
  'voice.stt': {
    surface: 'settings', section: 'Voice', setting: 'stt_enabled',
    get label() { return t("settingTargets.4ebea1d825") }, get where() { return t("settingTargets.b2416e9ca6") },
  },
  // The assistant's own master switch. It is the fourth section of the Voice tab rather
  // than a tab of its own, and every off-state used to say "Settings → Assistant" - a tab
  // that has never existed, so the one user who needed the switch was sent nowhere. That
  // is exactly what this table exists to stop: a link resolves against `settingsTabs` and
  // `settingTargets.test.ts` fails when it stops resolving, where a hardcoded name in prose
  // fails silently and forever.
  'assistant.enable': {
    surface: 'settings', section: 'Voice', setting: 'assistant_enabled',
    get label() { return t("settingTargets.dff4641557") }, get where() { return t("settingTargets.426d11e4a5") },
  },
  // Which agent CLIs appear in the launchers. A *set* rather than a switch, so it stays a
  // link: the Run menu's empty agent list routes here rather than offering to enable a
  // harness the machine may not have installed.
  'harnesses.enabled': {
    surface: 'settings', section: 'Harnesses', setting: 'harness_enabled',
    get label() { return t("settingTargets.6fc05abdf9") }, get where() { return t("settingTargets.69f90292bb") },
  },
  'terminals.claudeWidth': {
    surface: 'settings', section: 'Terminals', setting: 'claude_max_columns',
    get label() { return t("settingTargets.5a898c22f2") }, get where() { return t("settingTargets.cc4a642917") },
  },
  // Whether typing an agent's name in a terminal launches it through mux. Linked
  // from the console-contention notice because that notice is the one moment a
  // user is looking at the consequence of this setting being on, and "why is my
  // shell fighting my agent" has no other answer they could reach.
  'terminals.agentShims': {
    surface: 'settings', section: 'Terminals', setting: 'agent_shims_on_shell_path',
    get label() { return t("settingTargets.575b8e9cc8") },
    get where() { return t("settingTargets.cc4a642917") },
  },
  'appearance.sessionRows': {
    surface: 'settings', section: 'Appearance', setting: 'session_rows',
    get label() { return t("settingTargets.1b40fed8d4") }, get where() { return t("settingTargets.d83563260d") },
  },
  'appearance.sessionTopbar': {
    surface: 'settings', section: 'Appearance', setting: 'session_topbar',
    get label() { return t("settingTargets.a089ff7b83") }, get where() { return t("settingTargets.cb3a056df0") },
  },
  'projects.newProjectParent': {
    surface: 'settings', section: 'Projects', setting: 'new_project_parent',
    get label() { return t("settingTargets.5c79a421c4") }, get where() { return t("settingTargets.74569ceed2") },
  },
  // What the file tree and the resource watchers skip. A *value* rather than a switch - a
  // gate can offer "turn this on", never "type a glob list" - so it is a link, offered from
  // the file explorer's own header because that is the surface where a reader notices the
  // tree is full of things they never want to see. The global list rather than the
  // Project's: this is the one that hides `node_modules` everywhere, and the panel it lands
  // on names the per-Project list that composes with it.
  'projects.ignorePatterns': {
    surface: 'settings', section: 'Projects', setting: 'project_ignore_patterns',
    get label() { return t("settingTargets.6b12ef00cf") }, get where() { return t("settingTargets.74569ceed2") },
  },
  'schedules.install': {
    surface: 'automation', setting: 'scheduled_runs_enabled',
    get label() { return t("settingTargets.1c966575f6") }, get where() { return t("settingTargets.ec9e4fafd5") },
  },
  'alerts.master': {
    surface: 'settings', section: 'Alerts', setting: 'alerts_enabled',
    get label() { return t("settingTargets.03fd43a3a1") }, get where() { return t("settingTargets.783dbbd6c7") },
  },
  // Which endpoint every model-backed feature calls. A *value* rather than a switch -
  // choosing a provider and typing a URL, a key, and a model id is not something a gate
  // can honestly offer in one press - so it stays a link, and the surfaces held back by
  // an unverified endpoint route here rather than pretending to grant it.
  'accounts.llmProvider': {
    surface: 'settings', section: 'Accounts', setting: 'llm_provider',
    get label() { return t("settingTargets.ea70384c46") }, get where() { return t("settingTargets.bd741bd0dc") },
  },
  // Global automation controls and the graph they govern share one workspace.
  'automation.engine': {
    surface: 'automation', setting: 'automation_enabled',
    get label() { return t("settingTargets.efc95288ba") }, get where() { return t("settingTargets.ec9e4fafd5") },
  },
  'automation.scanTimeline': {
    surface: 'automation', setting: 'scan_timeline_enabled',
    get label() { return t("settingTargets.446eb6eb25") }, get where() { return t("settingTargets.ec9e4fafd5") },
  },
  'automation.scanTimelineModel': {
    surface: 'settings', section: 'Accounts', setting: 'scan_timeline_model',
    get label() { return t("settingTargets.4de13678da") }, get where() { return t("settingTargets.174313f841") },
  },
  'automation.attentionNarrationModel': {
    surface: 'settings', section: 'Accounts', setting: 'attention_narration_model',
    get label() { return t("settingTargets.bf9b2a9f1e") }, get where() { return t("settingTargets.174313f841") },
  },
  'automation.projectCardModel': {
    surface: 'settings', section: 'Accounts', setting: 'project_card_model',
    get label() { return t("settingTargets.573c3bc117") }, get where() { return t("settingTargets.174313f841") },
  },
  'automation.budgets': {
    surface: 'automation', setting: 'automation_daily_budget',
    get label() { return t("settingTargets.863a101589") }, get where() { return t("settingTargets.ec9e4fafd5") },
  },
  // The land queue's install-wide emergency stop. Its own switch rather than a facet of
  // the automation engine: the sweep that moves a trunk checks this and nothing else, so
  // a queue with it off accepts requests and then silently never advances one.
  'automation.landQueue': {
    surface: 'automation', setting: 'land_queue_enabled',
    get label() { return t("settingTargets.a2e3a5aabf") }, get where() { return t("settingTargets.ec9e4fafd5") },
  },
  // Per-Project opt-ins. Every one of these is off until a human turns it on for that
  // Project, so a surface reading from one is inert rather than empty until then.
  'project.automations': {
    surface: 'automation', setting: 'automations',
    get label() { return t("settingTargets.ad1fb9ec0c") }, get where() { return t("settingTargets.5be6ac8db0") },
  },
  'project.scanTimeline': {
    surface: 'automation', setting: 'automation:scan_timeline',
    get label() { return t("settingTargets.93b1aef862") }, get where() { return t("settingTargets.5be6ac8db0") },
  },
  'project.scanTimelineAutoArm': {
    surface: 'automation', setting: 'scan_timeline_auto_enable',
    get label() { return t("settingTargets.ae2454b096") }, get where() { return t("settingTargets.5be6ac8db0") },
  },
  'project.titleRefinements': {
    surface: 'automation', setting: 'title_refinements',
    get label() { return t("settingTargets.5e0d71209a") }, get where() { return t("settingTargets.5be6ac8db0") },
  },
  'project.codeGraph': {
    surface: 'automation', setting: 'automation:code_graph',
    get label() { return t("settingTargets.a5a07135c7") }, get where() { return t("settingTargets.5be6ac8db0") },
  },
  'project.scheduledRuns': {
    surface: 'automation', setting: 'automation:scheduled_runs',
    get label() { return t("settingTargets.19110a1000") }, get where() { return t("settingTargets.5be6ac8db0") },
  },
  'project.attentionRanking': {
    surface: 'automation', setting: 'automation:attention_ranking',
    get label() { return t("settingTargets.7c85333db5") }, get where() { return t("settingTargets.5be6ac8db0") },
  },
  'project.spawnReview': {
    surface: 'automation', setting: 'automation:observation_inbox',
    get label() { return t("settingTargets.69c40c600e") }, get where() { return t("settingTargets.5be6ac8db0") },
  },
  // The four model-free detectors the Findings pane reads. Named individually rather
  // than behind `project.automations`, because that target is an *area*: it reveals the
  // heading above twenty checkboxes and leaves the reader to find four of them by name.
  'project.provenanceGraph': {
    surface: 'automation', setting: 'automation:provenance_graph',
    get label() { return t("settingTargets.b0d4609be9") }, get where() { return t("settingTargets.5be6ac8db0") },
  },
  'project.loopDetection': {
    surface: 'automation', setting: 'automation:loop_detection',
    get label() { return t("settingTargets.42465236ac") }, get where() { return t("settingTargets.5be6ac8db0") },
  },
  'project.declaredVsVerified': {
    surface: 'automation', setting: 'automation:declared_vs_verified',
    get label() { return t("settingTargets.256e71f389") }, get where() { return t("settingTargets.5be6ac8db0") },
  },
  'project.docDebt': {
    surface: 'automation', setting: 'automation:doc_debt',
    get label() { return t("settingTargets.c8d5a1d12a") }, get where() { return t("settingTargets.5be6ac8db0") },
  },
  'project.landQueue': {
    surface: 'automation', setting: 'automation:land_queue',
    get label() { return t("settingTargets.788d20dbca") }, get where() { return t("settingTargets.5be6ac8db0") },
  },
  'project.sessionControl': {
    surface: 'automation', setting: 'automation:session_control',
    get label() { return t("settingTargets.009e0ae5e8") }, get where() { return t("settingTargets.5be6ac8db0") },
  },
  // The authority fields. Each is an opt-in's second half: the automation decides
  // whether an agent may ask, and these decide whether a human still approves each
  // time. They had no control anywhere until 2026-08-25 - only a line in a committed
  // TOML file - which made "draft" unreachable to change and invisible to discover.
  //
  // They point at the Automation workspace rather than the Projects registry as of
  // 2026-08-29: that is where the install-wide default and ceiling live, and a target
  // aimed at a surface that can only show one of the three layers would reveal a
  // control that cannot answer the question the reader arrived with. The registry
  // keeps a summary and a link, the same treatment the automation opt-ins already get.
  'project.authority': {
    surface: 'automation', setting: 'agent_authority',
    get label() { return t("settingTargets.1fac324f77") }, get where() { return t("settingTargets.37a381f097") },
  },
  'project.landGrant': {
    surface: 'automation', setting: 'land_grant',
    get label() { return t("settingTargets.05445d4325") }, get where() { return t("settingTargets.37a381f097") },
  },
  'project.landVerifyGrant': {
    surface: 'project', setting: 'land_verify_grant',
    get label() { return t("settingTargets.59560c8d30") }, get where() { return t("settingTargets.fccd73a69e") },
  },
  'project.sessionControlGrant': {
    surface: 'automation', setting: 'session_control_grant',
    get label() { return t("settingTargets.8f585f6bfa") }, get where() { return t("settingTargets.37a381f097") },
  },
  'project.spawnGrant': {
    surface: 'automation', setting: 'spawn_grant',
    get label() { return t("settingTargets.b926fdc36e") }, get where() { return t("settingTargets.37a381f097") },
  },
  'project.interjectGrant': {
    surface: 'automation', setting: 'interject_grant',
    get label() { return t("settingTargets.b36d9cc563") }, get where() { return t("settingTargets.37a381f097") },
  },
  'project.messageEnvelope': {
    surface: 'automation', setting: 'message_envelope',
    get label() { return t("settingTargets.17bf57b622") }, get where() { return t("settingTargets.37a381f097") },
  },
  'project.settings': {
    surface: 'project',
    get label() { return t("settingTargets.75c9b49472") }, get where() { return t("settingTargets.fccd73a69e") },
  },
} as const satisfies Record<string, SettingTarget>

export type SettingTargetId = keyof typeof SETTING_TARGETS

export const settingTarget = (id: SettingTargetId): SettingTarget => SETTING_TARGETS[id]

/** The `data-setting` id a Project automation row carries. One rule, both ends. */
export const automationSetting = (automationId: string): string => `automation:${automationId}`

/**
 * The event a `SettingLink` dispatches. `App` owns the routing, because opening one overlay
 * means closing whichever other one is up, and only `App` knows which that is.
 *
 * A window event rather than a prop chain: these links live at the bottom of surfaces several
 * components deep (the change map, the findings pane, an approval chip inside a pane bar), and
 * threading a navigation callback through every one of them is how the existing partial
 * coverage happened. Same shape as the `mux:command` and `mux:error` channels.
 */
export const OPEN_SETTING_EVENT = 'mux:open-setting'

export type OpenSettingDetail = {
  target: SettingTargetId
  /** Project-surface targets only; falls back to the active Project when omitted. */
  projectId?: string
}

export function requestSetting(target: SettingTargetId, projectId?: string): void {
  window.dispatchEvent(new CustomEvent<OpenSettingDetail>(OPEN_SETTING_EVENT, {
    detail: { target, projectId },
  }))
}
