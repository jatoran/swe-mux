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
    label: 'Keep clipboard history', where: 'Settings → Input',
  },
  'queue.autoDelivery': {
    surface: 'settings', section: 'Prompt queue', setting: 'auto_delivery_enabled',
    label: 'Allow auto-delivery for agent conversations', where: 'Settings → Prompt queue',
  },
  // How long a conversation may sit idle before its grant lapses. A *value*
  // rather than a switch - a gate can honestly offer "turn this on", never "pick
  // a number" - so it stays a link, and the surfaces that report a lapse route
  // here rather than growing a second editor for the same bound.
  'queue.grantWindow': {
    surface: 'settings', section: 'Prompt queue', setting: 'auto_delivery_session_ttl_minutes',
    label: 'Idle minutes before a grant lapses', where: 'Settings → Prompt queue',
  },
  'queue.agentMessaging': {
    surface: 'settings', section: 'Prompt queue', setting: 'agent_messaging_enabled',
    label: 'Allow agent-to-agent messages', where: 'Settings → Prompt queue',
  },
  'approvals.autoAnswer': {
    surface: 'settings', section: 'Prompt queue', setting: 'approval_auto_enabled',
    label: 'Allow swe-mux to answer approvals', where: 'Settings → Prompt queue',
  },
  'usage.ccusage': {
    surface: 'settings', section: 'Usage', setting: 'ccusage_enabled',
    label: 'Enable ccusage refresh', where: 'Settings → Usage',
  },
  'voice.tts': {
    surface: 'settings', section: 'Voice', setting: 'tts_enabled',
    label: 'Enable read aloud', where: 'Settings → Voice',
  },
  'voice.stt': {
    surface: 'settings', section: 'Voice', setting: 'stt_enabled',
    label: 'Enable microphone input', where: 'Settings → Voice',
  },
  // The assistant's own master switch. It is the fourth section of the Voice tab rather
  // than a tab of its own, and every off-state used to say "Settings → Assistant" - a tab
  // that has never existed, so the one user who needed the switch was sent nowhere. That
  // is exactly what this table exists to stop: a link resolves against `settingsTabs` and
  // `settingTargets.test.ts` fails when it stops resolving, where a hardcoded name in prose
  // fails silently and forever.
  'assistant.enable': {
    surface: 'settings', section: 'Voice', setting: 'assistant_enabled',
    label: 'Enable the Mux assistant', where: 'Settings → Voice → Mux assistant',
  },
  // Which agent CLIs appear in the launchers. A *set* rather than a switch, so it stays a
  // link: the Run menu's empty agent list routes here rather than offering to enable a
  // harness the machine may not have installed.
  'harnesses.enabled': {
    surface: 'settings', section: 'Harnesses', setting: 'harness_enabled',
    label: 'Which agents appear in launchers', where: 'Settings → Harnesses',
  },
  'terminals.claudeWidth': {
    surface: 'settings', section: 'Terminals', setting: 'claude_max_columns',
    label: 'Claude width limit', where: 'Settings → Terminals',
  },
  // Whether typing an agent's name in a terminal launches it through mux. Linked
  // from the console-contention notice because that notice is the one moment a
  // user is looking at the consequence of this setting being on, and "why is my
  // shell fighting my agent" has no other answer they could reach.
  'terminals.agentShims': {
    surface: 'settings', section: 'Terminals', setting: 'agent_shims_on_shell_path',
    label: 'Launch agents through swe-mux when typed in a terminal',
    where: 'Settings → Terminals',
  },
  'projects.newProjectParent': {
    surface: 'settings', section: 'Projects', setting: 'new_project_parent',
    label: 'Default parent folder for new projects', where: 'Settings → Projects',
  },
  // What the file tree and the resource watchers skip. A *value* rather than a switch - a
  // gate can offer "turn this on", never "type a glob list" - so it is a link, offered from
  // the file explorer's own header because that is the surface where a reader notices the
  // tree is full of things they never want to see. The global list rather than the
  // Project's: this is the one that hides `node_modules` everywhere, and the panel it lands
  // on names the per-Project list that composes with it.
  'projects.ignorePatterns': {
    surface: 'settings', section: 'Projects', setting: 'project_ignore_patterns',
    label: 'Ignore patterns', where: 'Settings → Projects',
  },
  'schedules.install': {
    surface: 'automation', setting: 'scheduled_runs_enabled',
    label: 'Let schedules start sessions', where: 'Automation → Global policy',
  },
  'alerts.master': {
    surface: 'settings', section: 'Alerts', setting: 'alerts_enabled',
    label: 'Alerts for this device', where: 'Settings → Alerts',
  },
  // Which endpoint every model-backed feature calls. A *value* rather than a switch -
  // choosing a provider and typing a URL, a key, and a model id is not something a gate
  // can honestly offer in one press - so it stays a link, and the surfaces held back by
  // an unverified endpoint route here rather than pretending to grant it.
  'accounts.llmProvider': {
    surface: 'settings', section: 'Accounts', setting: 'llm_provider',
    label: 'Model provider', where: 'Settings → Accounts',
  },
  // Global automation controls and the graph they govern share one workspace.
  'automation.engine': {
    surface: 'automation', setting: 'automation_enabled',
    label: 'Automation engine', where: 'Automation → Global policy',
  },
  'automation.scanTimeline': {
    surface: 'automation', setting: 'scan_timeline_enabled',
    label: 'Scan timeline', where: 'Automation → Global policy',
  },
  'automation.scanTimelineModel': {
    surface: 'settings', section: 'Accounts', setting: 'scan_timeline_model',
    label: 'Scan timeline model', where: 'Settings → Accounts → Models',
  },
  'automation.attentionNarrationModel': {
    surface: 'settings', section: 'Accounts', setting: 'attention_narration_model',
    label: 'Attention narration model', where: 'Settings → Accounts → Models',
  },
  'automation.projectCardModel': {
    surface: 'settings', section: 'Accounts', setting: 'project_card_model',
    label: 'Project context card model', where: 'Settings → Accounts → Models',
  },
  'automation.budgets': {
    surface: 'automation', setting: 'automation_daily_budget',
    label: 'Automation budgets', where: 'Automation → Global policy',
  },
  // The land queue's install-wide emergency stop. Its own switch rather than a facet of
  // the automation engine: the sweep that moves a trunk checks this and nothing else, so
  // a queue with it off accepts requests and then silently never advances one.
  'automation.landQueue': {
    surface: 'automation', setting: 'land_queue_enabled',
    label: 'Let the land queue move trunks', where: 'Automation → Global policy',
  },
  // Per-Project opt-ins. Every one of these is off until a human turns it on for that
  // Project, so a surface reading from one is inert rather than empty until then.
  'project.automations': {
    surface: 'automation', setting: 'automations',
    label: 'Automations', where: 'Automation → Projects',
  },
  'project.scanTimeline': {
    surface: 'automation', setting: 'automation:scan_timeline',
    label: 'Scan timeline permitted for this Project', where: 'Automation → Projects',
  },
  'project.scanTimelineAutoArm': {
    surface: 'automation', setting: 'scan_timeline_auto_enable',
    label: 'Arm every new conversation', where: 'Automation → Projects',
  },
  'project.codeGraph': {
    surface: 'automation', setting: 'automation:code_graph',
    label: 'Code-structure graph', where: 'Automation → Projects',
  },
  'project.scheduledRuns': {
    surface: 'automation', setting: 'automation:scheduled_runs',
    label: 'Scheduled runs', where: 'Automation → Projects',
  },
  'project.attentionRanking': {
    surface: 'automation', setting: 'automation:attention_ranking',
    label: 'Attention ranking', where: 'Automation → Projects',
  },
  'project.spawnReview': {
    surface: 'automation', setting: 'automation:observation_inbox',
    label: 'Spawn request review', where: 'Automation → Projects',
  },
  // The four model-free detectors the Findings pane reads. Named individually rather
  // than behind `project.automations`, because that target is an *area*: it reveals the
  // heading above twenty checkboxes and leaves the reader to find four of them by name.
  'project.provenanceGraph': {
    surface: 'automation', setting: 'automation:provenance_graph',
    label: 'Provenance graph', where: 'Automation → Projects',
  },
  'project.loopDetection': {
    surface: 'automation', setting: 'automation:loop_detection',
    label: 'Loop / stall detection', where: 'Automation → Projects',
  },
  'project.declaredVsVerified': {
    surface: 'automation', setting: 'automation:declared_vs_verified',
    label: 'Declared vs verified', where: 'Automation → Projects',
  },
  'project.docDebt': {
    surface: 'automation', setting: 'automation:doc_debt',
    label: 'Doc-debt ledger', where: 'Automation → Projects',
  },
  'project.landQueue': {
    surface: 'automation', setting: 'automation:land_queue',
    label: 'Land queue', where: 'Automation → Projects',
  },
  'project.sessionControl': {
    surface: 'automation', setting: 'automation:session_control',
    label: 'Agent session control', where: 'Automation → Projects',
  },
  // The authority fields. Each is an opt-in's second half: the automation decides
  // whether an agent may ask, and these decide whether a human still approves each
  // time. They had no control anywhere until now - only a line in a committed TOML
  // file - which made "draft" unreachable to change and invisible to discover.
  'project.landGrant': {
    surface: 'project', setting: 'land_grant',
    label: 'Agent-initiated landing', where: 'Project settings',
  },
  'project.sessionControlGrant': {
    surface: 'project', setting: 'session_control_grant',
    label: 'Agent interrupt and end', where: 'Project settings',
  },
  'project.spawnGrant': {
    surface: 'project', setting: 'spawn_grant',
    label: 'Agent-initiated spawn', where: 'Project settings',
  },
  'project.interjectGrant': {
    surface: 'project', setting: 'interject_grant',
    label: 'Mid-turn agent messages', where: 'Project settings',
  },
  'project.settings': {
    surface: 'project',
    label: 'This Project’s settings', where: 'Project settings',
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
