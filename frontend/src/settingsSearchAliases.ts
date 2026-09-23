/**
 * Other names for Settings entries, so the panel-wide search answers the words people
 * actually type rather than only the words the panel happens to print.
 *
 * The index itself is harvested from rendered markup (`settingsSearch.ts`), so nothing
 * here declares a setting: an alias only *points at* an entry the walk already found,
 * by tab and visible label. That keeps the table from becoming a second registry of
 * what exists. The price is that a relabelled control silently orphans its aliases,
 * and an alias can quietly shadow a real setting added later - which is what
 * `auditSettingsAliases` is for. Two gates run it:
 *
 * - `test/settingsSearchAliases.test.ts` checks the table on its own (shape,
 *   duplicates, tab names, deep-link names) in the ordinary `npm test`;
 * - `test/renderer/settings-search.spec.ts` checks it against the live index of every
 *   tab, which is the only place a label rendered by a child component exists.
 *   `npm run check:settings-aliases` runs just that spec.
 *
 * Rules for adding one (all enforced by the audit):
 * - lower case, single-spaced, because it is compared normalized;
 * - never the exact label of any real entry on any tab, and never another target's
 *   alias: an alias is a way in when the real name is not what was typed, not a
 *   competitor to it;
 * - the target is its tab plus its label as rendered; add `section` (its `<h3>`) or
 *   `kind` only when that label appears in more than one place on the tab.
 */
import { normalizeSearchText } from './fuzzyText.ts'
import type { SettingsEntryKind, SettingsSearchEntry } from './settingsSearch.ts'
import { SECTION_ALIASES, settingsTabs, type SettingsTab } from './settingsTabs.ts'

export type SettingsAliasTarget = {
  tab: SettingsTab
  /** The entry's label as rendered. Compared normalized. */
  label: string
  /** The entry's top-level heading, when the label alone is ambiguous on its tab. */
  section?: string
  /** The entry's kind, when a heading and a control on one tab share a label. */
  kind?: SettingsEntryKind
  aliases: string[]
}

export const SETTINGS_SEARCH_ALIASES: SettingsAliasTarget[] = [
  // General
  { tab: 'general', label: 'Startup directory', aliases: ['cwd', 'start folder', 'initial directory'] },
  { tab: 'general', label: 'Tier', aliases: ['experience level', 'beginner mode', 'simple mode', 'advanced mode'] },
  { tab: 'general', label: 'Reset & run tutorial', aliases: ['onboarding', 'walkthrough', 'tour'] },
  { tab: 'general', label: 'Reveal config directory', aliases: ['config file', 'config.toml', 'settings file', 'open config'] },
  { tab: 'general', label: 'Export sanitized', aliases: ['export settings', 'backup settings'] },
  { tab: 'general', label: 'Restore defaults', aliases: ['reset settings', 'reset config'] },
  // Projects
  { tab: 'projects', label: 'Default parent folder', aliases: ['new project folder', 'projects directory', 'project root'] },
  { tab: 'projects', label: 'Ignore patterns', aliases: ['gitignore', 'exclude files', 'hidden files'] },
  // Terminals
  { tab: 'terminals', label: 'Renderer', aliases: ['webgl', 'gpu', 'hardware acceleration'] },
  { tab: 'terminals', label: 'Scrollback bytes', aliases: ['history size', 'buffer size', 'scrollback size', 'scrollback lines'] },
  { tab: 'terminals', label: 'Global default terminal profile', aliases: ['default shell', 'shell', 'powershell', 'bash'] },
  { tab: 'terminals', label: 'Launch agents through swe-mux', aliases: ['agent shim', 'intercept agent'] },
  // Git
  { tab: 'git', label: 'Worktree root', aliases: ['worktree folder', 'worktrees directory'] },
  { tab: 'git', label: 'Git poll seconds', aliases: ['git refresh', 'git polling'] },
  // Processes
  { tab: 'processes', label: 'Session process priority', aliases: ['cpu priority', 'nice', 'niceness'] },
  // Harnesses
  { tab: 'harnesses', label: 'Default harness', kind: 'field', aliases: ['default agent', 'default cli'] },
  { tab: 'harnesses', label: 'Instrument with mux hooks', aliases: ['hooks', 'agent hooks'] },
  { tab: 'harnesses', label: 'Reconcile native history on startup', aliases: ['import history', 'conversation import'] },
  // Accounts
  { tab: 'accounts', label: 'Provider accounts', kind: 'section', aliases: ['switch account', 'claude login', 'codex login', 'multiple accounts'] },
  { tab: 'accounts', label: 'Model provider', kind: 'field', aliases: ['llm provider', 'ai provider', 'openrouter', 'llm endpoint'] },
  { tab: 'accounts', label: 'API key', aliases: ['openrouter key', 'llm key'] },
  { tab: 'accounts', label: 'Refresh models', aliases: ['model list', 'model catalog'] },
  // Prompt queue
  { tab: 'queue', label: 'Allow auto-delivery for agent conversations', aliases: ['autopilot', 'auto send', 'auto-send'] },
  { tab: 'queue', label: 'Allow swe-mux to answer approvals', aliases: ['auto approve', 'auto-approve', 'yolo', 'permissions'] },
  { tab: 'queue', label: 'Allow agent-to-agent messages', aliases: ['agent chat', 'inter-agent'] },
  { tab: 'queue', label: 'Let agents request spawns', aliases: ['spawn agents', 'subagents'] },
  // Automation
  { tab: 'automation', label: 'Open Automation workspace', aliases: ['workflows', 'triggers', 'cron', 'automations'] },
  // Plugins
  { tab: 'plugins', label: 'Browse marketplace', aliases: ['install plugin', 'addons', 'add-ons'] },
  // Usage
  { tab: 'usage', label: 'Open telemetry dashboard', aliases: ['spend', 'billing', 'token usage', 'usage dashboard'] },
  { tab: 'usage', label: 'Provider quota poll minutes', aliases: ['quota', 'rate limit'] },
  // Appearance
  { tab: 'appearance', label: 'Theme', aliases: ['dark mode', 'light mode', 'color scheme', 'colors', 'colours', 'skin'] },
  { tab: 'appearance', label: 'This browser', aliases: ['desktop mode', 'mobile mode', 'phone layout', 'force mobile'] },
  { tab: 'appearance', label: 'Font family', aliases: ['typeface', 'monospace font'] },
  { tab: 'appearance', label: 'Desktop interface scale', aliases: ['zoom', 'ui size', 'text size'] },
  { tab: 'appearance', label: 'Mobile interface scale', aliases: ['mobile zoom', 'phone text size'] },
  { tab: 'appearance', label: 'Drawer tabs', aliases: ['right panel', 'drawer'] },
  { tab: 'appearance', label: 'Session rows', kind: 'section', aliases: ['sidebar rows', 'session list'] },
  { tab: 'appearance', label: 'Session top bars', kind: 'section', aliases: ['pane header', 'title bar', 'pane toolbar'] },
  // Actions
  { tab: 'actions', label: 'Show the rail on desktop', aliases: ['command rail', 'button bar', 'toolbar'] },
  { tab: 'actions', label: 'Add custom action', aliases: ['custom button', 'macro', 'snippet'] },
  // Input
  { tab: 'input', label: 'Middle-click paste', aliases: ['paste'] },
  { tab: 'input', label: 'Broadcast by default', aliases: ['send to all', 'multi-input', 'sync panes', 'synchronize panes'] },
  { tab: 'input', label: 'Scroll direction', aliases: ['natural scrolling', 'invert scroll', 'reverse scroll'] },
  { tab: 'input', label: 'Keep clipboard history', aliases: ['clipboard', 'copy history'] },
  { tab: 'input', label: 'Touch gestures', aliases: ['swipe', 'gestures'] },
  { tab: 'input', label: 'Keyboard preset', aliases: ['keymap preset', 'vim', 'emacs', 'tmux keys'] },
  { tab: 'input', label: 'Keyboard shortcuts', aliases: ['hotkeys', 'keybindings', 'key bindings', 'keymap', 'bindings'] },
  // Notes
  { tab: 'notes', label: 'Spellcheck', aliases: ['spelling', 'spell check'] },
  { tab: 'notes', label: 'Global Scratchpad', aliases: ['scratch', 'quick notes'] },
  { tab: 'notes', label: 'Editor shortcuts', aliases: ['note hotkeys', 'note keybindings'] },
  // Voice
  { tab: 'voice', label: 'Read aloud is on (master)', aliases: ['tts', 'text to speech', 'speak replies'] },
  { tab: 'voice', label: 'Provider', aliases: ['tts engine', 'voice engine', 'kokoro', 'sapi'] },
  { tab: 'voice', label: 'Enable Talk & dictation', kind: 'field', aliases: ['microphone', 'mic', 'speech to text', 'stt', 'voice input', 'push to talk'] },
  { tab: 'voice', label: 'Wake words', kind: 'field', aliases: ['hotword', 'wake phrase', 'hey mux'] },
  { tab: 'voice', label: 'Enable the Mux assistant', aliases: ['voice assistant', 'ai assistant', 'chatbot'] },
  // Alerts
  { tab: 'notifications', label: 'Sound in the open app', aliases: ['alert sound', 'notification sound', 'chime', 'beep'] },
  { tab: 'notifications', label: 'Push in the background', aliases: ['push notifications', 'web push'] },
  { tab: 'notifications', label: 'Quiet hours', aliases: ['do not disturb', 'dnd', 'mute'] },
  // Remote
  { tab: 'remote', label: 'Listen on Tailscale IPv4', aliases: ['tailscale', 'tailnet', 'lan access', 'network access'] },
  { tab: 'remote', label: 'Connect a phone…', aliases: ['mobile access', 'qr code', 'pair phone'] },
  // Maintenance
  { tab: 'maintenance', label: 'Check for new releases', aliases: ['update', 'updates', 'auto update', 'upgrade'] },
  { tab: 'maintenance', label: 'Reload daemon (keep sessions)', aliases: ['restart daemon', 'restart server', 'restart'] },
  { tab: 'maintenance', label: 'Daemon log level', aliases: ['debug logging', 'verbose', 'logs'] },
  { tab: 'maintenance', label: 'Export diagnostics', kind: 'action', aliases: ['bug report', 'support bundle'] },
  { tab: 'maintenance', label: 'Factory reset…', aliases: ['wipe', 'erase all data', 'start over'] },
]

const targetMatches = (target: SettingsAliasTarget, entry: SettingsSearchEntry): boolean =>
  entry.tab === target.tab && entry.key === normalizeSearchText(target.label) &&
  (target.kind === undefined || entry.kind === target.kind) &&
  (target.section === undefined || normalizeSearchText(entry.path[0] || '') === normalizeSearchText(target.section))

/**
 * The entries with their aliases attached. New objects, never a mutation: the live-DOM
 * harvests these are built from are cached for the page session and shared between
 * index builds.
 */
export function applySettingsAliases(
  entries: SettingsSearchEntry[], table: SettingsAliasTarget[] = SETTINGS_SEARCH_ALIASES,
): SettingsSearchEntry[] {
  return entries.map(entry => {
    let aliases: string[] | null = null
    for (const target of table) {
      if (!targetMatches(target, entry)) continue
      aliases = [...(aliases || entry.aliases), ...target.aliases.map(normalizeSearchText)]
    }
    return aliases ? { ...entry, aliases } : entry
  })
}

/** Where a result is, as one line (`tab › label`), for naming an entry in a problem. */
const describe = (entry: SettingsSearchEntry): string =>
  [entry.tabLabel, ...entry.path, entry.label].join(' › ')

/**
 * Everything wrong with the alias table, as sentences. Empty means clean.
 *
 * `entries` is the full index when the caller has one (the renderer spec harvests every
 * tab); without it only the table's own consistency is checked, which is what a plain
 * unit test can see.
 */
export function auditSettingsAliases(
  table: SettingsAliasTarget[] = SETTINGS_SEARCH_ALIASES, entries?: SettingsSearchEntry[],
): string[] {
  const problems: string[] = []
  const owner = new Map<string, string>()
  const tabIds = new Set<string>(settingsTabs.map(tab => tab.id))
  const tabLabels = new Map(settingsTabs.map(tab => [normalizeSearchText(tab.label), tab.id]))
  for (const target of table) {
    const name = `${target.tab} › ${target.label}${target.section ? ` (in ${target.section})` : ''}`
    if (!tabIds.has(target.tab)) problems.push(`${name}: no Settings tab has the id "${target.tab}"`)
    if (!target.aliases.length) problems.push(`${name}: lists no aliases`)
    const ownLabel = normalizeSearchText(target.label)
    for (const alias of target.aliases) {
      const key = normalizeSearchText(alias)
      if (!key) { problems.push(`${name}: has an empty alias`); continue }
      if (alias !== key) problems.push(`${name}: alias "${alias}" is not normalized (write "${key}")`)
      if (key === ownLabel) problems.push(`${name}: alias "${key}" is the entry's own label`)
      const previous = owner.get(key)
      if (previous) problems.push(`alias "${key}" is claimed by both ${previous} and ${name}`)
      else owner.set(key, name)
      const tab = tabLabels.get(key)
      if (tab) problems.push(`${name}: alias "${key}" is the name of the ${tab} tab`)
      const deepLink = SECTION_ALIASES[key]
      if (deepLink && deepLink !== target.tab) {
        problems.push(`${name}: alias "${key}" already names the ${deepLink} tab as a deep link (SECTION_ALIASES)`)
      }
    }
  }
  if (!entries) return problems

  const byKey = new Map<string, SettingsSearchEntry[]>()
  for (const entry of entries) byKey.set(entry.key, [...(byKey.get(entry.key) || []), entry])
  for (const target of table) {
    const name = `${target.tab} › ${target.label}${target.section ? ` (in ${target.section})` : ''}`
    const hits = entries.filter(entry => targetMatches(target, entry))
    // One result is one identity (see `searchSettings`), so several hits in the same
    // place are one target rendered more than once, not an ambiguity.
    const places = new Set(hits.map(entry => `${entry.section}|${entry.kind}`))
    if (!hits.length) problems.push(`${name}: matches no entry in the index: relabelled, moved, or misspelt?`)
    else if (places.size > 1) {
      problems.push(`${name}: matches ${places.size} places (${[...new Set(hits.map(describe))].join('; ')}): add a section or kind`)
    }
    for (const alias of target.aliases) {
      const shadowed = byKey.get(normalizeSearchText(alias))
      if (shadowed?.length) {
        problems.push(`${name}: alias "${normalizeSearchText(alias)}" is the label of a real entry (${describe(shadowed[0])})`)
      }
    }
  }
  return problems
}
