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
 * Three overlays own switches, and the split is not arbitrary:
 *  - `settings`   — install-wide configuration (the Settings panel, tab named by `section`).
 *  - `project`    — one Project's own opt-ins (the Projects registry, the only per-Project editor).
 *  - `automation` — the two global automation master switches, which live on the Automation
 *                   dashboard rather than in Settings. Prose that sends people to
 *                   "Settings → Automation" for these is wrong, and was.
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
  'schedules.install': {
    surface: 'settings', section: 'Automation', setting: 'scheduled_runs_enabled',
    label: 'Let schedules start sessions', where: 'Settings → Automation',
  },
  'alerts.master': {
    surface: 'settings', section: 'Alerts', setting: 'alerts_enabled',
    label: 'Alerts for this device', where: 'Settings → Alerts',
  },
  // The two global automation switches are on the dashboard, not in Settings.
  'automation.engine': {
    surface: 'automation', setting: 'automation_enabled',
    label: 'Automation engine', where: 'Automation dashboard → Global controls',
  },
  'automation.scanTimeline': {
    surface: 'automation', setting: 'scan_timeline_enabled',
    label: 'Scan timeline', where: 'Automation dashboard → Global controls',
  },
  // Per-Project opt-ins. Every one of these is off until a human turns it on for that
  // Project, so a surface reading from one is inert rather than empty until then.
  'project.automations': {
    surface: 'project', setting: 'automations',
    label: 'Control-plane automations', where: 'Project settings',
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
