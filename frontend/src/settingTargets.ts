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
 * Two overlays own switches, and the split is scope:
 *  - `settings` — install-wide configuration (the Settings panel, tab named by `section`).
 *                 Every global switch and bound lives here, including the automation
 *                 master switches; the Automation dashboard shows their state and links
 *                 back rather than owning a second copy.
 *  - `project`  — one Project's own opt-ins (the Projects registry, the only per-Project editor).
 */

export type SettingSurface = 'settings' | 'project'

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
  'terminals.claudeWidth': {
    surface: 'settings', section: 'Terminals', setting: 'claude_max_columns',
    label: 'Claude width limit', where: 'Settings → Terminals',
  },
  'projects.newProjectParent': {
    surface: 'settings', section: 'Projects', setting: 'new_project_parent',
    label: 'Default parent folder for new projects', where: 'Settings → Projects',
  },
  'schedules.install': {
    surface: 'settings', section: 'Automation', setting: 'scheduled_runs_enabled',
    label: 'Let schedules start sessions', where: 'Settings → Automation',
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
  // The global automation switches live in Settings with every other install-wide
  // switch; the Automation dashboard reads their state and links here.
  'automation.engine': {
    surface: 'settings', section: 'Automation', setting: 'automation_enabled',
    label: 'Automation engine', where: 'Settings → Automation',
  },
  'automation.scanTimeline': {
    surface: 'settings', section: 'Automation', setting: 'scan_timeline_enabled',
    label: 'Scan timeline', where: 'Settings → Automation',
  },
  'automation.budgets': {
    surface: 'settings', section: 'Automation', setting: 'automation_daily_budget_usd',
    label: 'Automation budgets', where: 'Settings → Automation',
  },
  // The land queue's install-wide emergency stop. Its own switch rather than a facet of
  // the automation engine: the sweep that moves a trunk checks this and nothing else, so
  // a queue with it off accepts requests and then silently never advances one.
  'automation.landQueue': {
    surface: 'settings', section: 'Automation', setting: 'land_queue_enabled',
    label: 'Let the land queue move trunks', where: 'Settings → Automation',
  },
  // Per-Project opt-ins. Every one of these is off until a human turns it on for that
  // Project, so a surface reading from one is inert rather than empty until then.
  'project.automations': {
    surface: 'project', setting: 'automations',
    label: 'Automations', where: 'Project settings',
  },
  'project.scanTimeline': {
    surface: 'project', setting: 'automation:scan_timeline',
    label: 'Scan timeline permitted for this Project', where: 'Project settings',
  },
  'project.scanTimelineAutoArm': {
    surface: 'project', setting: 'scan_timeline_auto_enable',
    label: 'Arm every new conversation', where: 'Project settings',
  },
  'project.codeGraph': {
    surface: 'project', setting: 'automation:code_graph',
    label: 'Code-structure graph', where: 'Project settings',
  },
  'project.scheduledRuns': {
    surface: 'project', setting: 'automation:scheduled_runs',
    label: 'Scheduled runs', where: 'Project settings',
  },
  'project.attentionRanking': {
    surface: 'project', setting: 'automation:attention_ranking',
    label: 'Attention ranking', where: 'Project settings',
  },
  'project.spawnReview': {
    surface: 'project', setting: 'automation:observation_inbox',
    label: 'Spawn request review', where: 'Project settings',
  },
  // The four model-free detectors the Findings pane reads. Named individually rather
  // than behind `project.automations`, because that target is an *area*: it reveals the
  // heading above twenty checkboxes and leaves the reader to find four of them by name.
  'project.provenanceGraph': {
    surface: 'project', setting: 'automation:provenance_graph',
    label: 'Provenance graph', where: 'Project settings',
  },
  'project.loopDetection': {
    surface: 'project', setting: 'automation:loop_detection',
    label: 'Loop / stall detection', where: 'Project settings',
  },
  'project.declaredVsVerified': {
    surface: 'project', setting: 'automation:declared_vs_verified',
    label: 'Declared vs verified', where: 'Project settings',
  },
  'project.docDebt': {
    surface: 'project', setting: 'automation:doc_debt',
    label: 'Doc-debt ledger', where: 'Project settings',
  },
  'project.landQueue': {
    surface: 'project', setting: 'automation:land_queue',
    label: 'Land queue', where: 'Project settings',
  },
  'project.sessionControl': {
    surface: 'project', setting: 'automation:session_control',
    label: 'Agent session control', where: 'Project settings',
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
