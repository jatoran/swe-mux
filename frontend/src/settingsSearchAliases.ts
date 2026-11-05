import { t, sourceText, type MessageId } from './localization/index.ts'
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

const aliasTerms=(ids:readonly MessageId[]):string[] => [...new Set(ids.flatMap(id=>[sourceText(id),t(id)]).map(normalizeSearchText))]

export const SETTINGS_SEARCH_ALIASES: SettingsAliasTarget[] = [
  // General
  { tab: 'general', get label() { return t("settingsSearchAliases.4953f9baec") }, get aliases(){return aliasTerms(["settingsSearchAliases.6f2f7e6c65","settingsSearchAliases.4e3d605973","settingsSearchAliases.0cbdee57ed"])} },
  { tab: 'general', get label() { return t("settingsSearchAliases.cb9e8664ed") }, get aliases(){return aliasTerms(["settingsSearchAliases.0369d26a4e","settingsSearchAliases.c0cb24b800","settingsSearchAliases.be64790185","settingsSearchAliases.979cb478f1"])} },
  { tab: 'general', get label() { return t("settingsSearchAliases.708bf8b65a") }, get aliases(){return aliasTerms(["settingsSearchAliases.a4963c4996","settingsSearchAliases.e0837458ff","settingsSearchAliases.d7b44809a0"])} },
  { tab: 'general', get label() { return t("settingsSearchAliases.1f2df45014") }, get aliases(){return aliasTerms(["settingsSearchAliases.721d60619b","settingsSearchAliases.28043ff911","settingsSearchAliases.ac6b2b35d2","settingsSearchAliases.85057bd3c7"])} },
  { tab: 'general', get label() { return t("settingsSearchAliases.cf675af0cc") }, get aliases(){return aliasTerms(["settingsSearchAliases.9f63bb4588","settingsSearchAliases.5e7744dd97"])} },
  { tab: 'general', get label() { return t("settingsSearchAliases.602f8e6370") }, get aliases(){return aliasTerms(["settingsSearchAliases.7d03e10824","settingsSearchAliases.7a73022503"])} },
  // Projects
  { tab: 'projects', get label() { return t("settingsSearchAliases.38b03c0bae") }, get aliases(){return aliasTerms(["settingsSearchAliases.bfb4174c84","settingsSearchAliases.ac8f98a712","settingsSearchAliases.9240a87f98"])} },
  { tab: 'projects', get label() { return t("settingsSearchAliases.6b12ef00cf") }, get aliases(){return aliasTerms(["settingsSearchAliases.a1aa659678","settingsSearchAliases.b7fb3a34b8","settingsSearchAliases.933eec8499"])} },
  // Terminals
  { tab: 'terminals', get label() { return t("settingsSearchAliases.9deb780b43") }, get aliases(){return aliasTerms(["settingsSearchAliases.5207e65b92","settingsSearchAliases.e3c5ba51db","settingsSearchAliases.7a9df95825"])} },
  { tab: 'terminals', get label() { return t("settingsSearchAliases.19e4a94704") }, get aliases(){return aliasTerms(["settingsSearchAliases.69cf86bb1b","settingsSearchAliases.b24add9a8c","settingsSearchAliases.36e7de90e9","settingsSearchAliases.e437e98bbe"])} },
  { tab: 'terminals', get label() { return t("settingsSearchAliases.6a30847162") }, get aliases(){return aliasTerms(["settingsSearchAliases.f5dd32ce0f","settingsSearchAliases.ce635c4eab","settingsSearchAliases.86f51b1707","settingsSearchAliases.37d2b12d5d"])} },
  { tab: 'terminals', get label() { return t("settingsSearchAliases.f6acb9c4de") }, get aliases(){return aliasTerms(["settingsSearchAliases.bcd23e32d5","settingsSearchAliases.f4cd82ba97"])} },
  // Git
  { tab: 'git', get label() { return t("settingsSearchAliases.7aabad254f") }, get aliases(){return aliasTerms(["settingsSearchAliases.479907d8bc","settingsSearchAliases.a88085d23b"])} },
  { tab: 'git', get label() { return t("settingsSearchAliases.693933a8fd") }, get aliases(){return aliasTerms(["settingsSearchAliases.d55db75d01","settingsSearchAliases.d1a15d4405"])} },
  // Processes
  { tab: 'processes', get label() { return t("settingsSearchAliases.1baa8f3ed8") }, get aliases(){return aliasTerms(["settingsSearchAliases.1fcde3b08d","settingsSearchAliases.e186022d09","settingsSearchAliases.b5ef7f493a"])} },
  // Harnesses
  { tab: 'harnesses', get label() { return t("settingsSearchAliases.c039f423b8") }, kind: 'field', get aliases(){return aliasTerms(["settingsSearchAliases.b3ea5ed73d","settingsSearchAliases.4210763bdb"])} },
  { tab: 'harnesses', get label() { return t("settingsSearchAliases.e90af19a3c") }, get aliases(){return aliasTerms(["settingsSearchAliases.a9fa9fda98","settingsSearchAliases.acec148ce7"])} },
  { tab: 'harnesses', get label() { return t("settingsSearchAliases.1f280417b0") }, get aliases(){return aliasTerms(["settingsSearchAliases.541b624758","settingsSearchAliases.c118951e6b"])} },
  // Accounts
  { tab: 'accounts', get label() { return t("settingsSearchAliases.a701acdcc0") }, kind: 'section', get aliases(){return aliasTerms(["settingsSearchAliases.4aa5dd8bec","settingsSearchAliases.1d839ac9ac","settingsSearchAliases.53b7014e73","settingsSearchAliases.fdee390edc"])} },
  { tab: 'accounts', get label() { return t("settingsSearchAliases.ea70384c46") }, kind: 'field', get aliases(){return aliasTerms(["settingsSearchAliases.0443cd7a63","settingsSearchAliases.8d5016f8ae","settingsSearchAliases.7f871cbf90","settingsSearchAliases.f5caa6bb97"])} },
  { tab: 'accounts', get label() { return t("settingsSearchAliases.16f0ee47f9") }, get aliases(){return aliasTerms(["settingsSearchAliases.1f682b3296","settingsSearchAliases.6a3ffe7250"])} },
  { tab: 'accounts', get label() { return t("settingsSearchAliases.04903063b2") }, get aliases(){return aliasTerms(["settingsSearchAliases.e2f7f430e8","settingsSearchAliases.f1a68134ac"])} },
  // Prompt queue
  { tab: 'queue', get label() { return t("settingsSearchAliases.a7ed28c4c2") }, get aliases(){return aliasTerms(["settingsSearchAliases.06a8d99fc6","settingsSearchAliases.7ff91cf545","settingsSearchAliases.5bb0dd2f64"])} },
  { tab: 'queue', get label() { return t("settingsSearchAliases.98473e42a5") }, get aliases(){return aliasTerms(["settingsSearchAliases.337abba9f5","settingsSearchAliases.deddf5e179","settingsSearchAliases.311fe3feed","settingsSearchAliases.4fe3cdd404"])} },
  { tab: 'queue', get label() { return t("settingsSearchAliases.6671bb2f54") }, get aliases(){return aliasTerms(["settingsSearchAliases.548467235e","settingsSearchAliases.4835a00136"])} },
  { tab: 'queue', get label() { return t("settingsSearchAliases.dc57c727ca") }, get aliases(){return aliasTerms(["settingsSearchAliases.c12a41ec87","settingsSearchAliases.d298391b07"])} },
  // Automation
  { tab: 'automation', get label() { return t("settingsSearchAliases.9e8cab89ac") }, get aliases(){return aliasTerms(["settingsSearchAliases.45b34f16db","settingsSearchAliases.51936fd380","settingsSearchAliases.c374eea599","settingsSearchAliases.238ae7bbfd"])} },
  // Plugins
  { tab: 'plugins', get label() { return t("settingsSearchAliases.2ad20c9748") }, get aliases(){return aliasTerms(["settingsSearchAliases.f61a07955c","settingsSearchAliases.960f41e99c","settingsSearchAliases.bcbe7ab642"])} },
  // Usage
  { tab: 'usage', get label() { return t("settingsSearchAliases.908e673dbf") }, get aliases(){return aliasTerms(["settingsSearchAliases.f64a33ff88","settingsSearchAliases.0c95c7ece1","settingsSearchAliases.860cf7215d","settingsSearchAliases.84ac3b8f96"])} },
  { tab: 'usage', get label() { return t("settingsSearchAliases.ad0cf189ea") }, get aliases(){return aliasTerms(["settingsSearchAliases.b878a6801d","settingsSearchAliases.a0f3a11be6"])} },
  // Appearance
  { tab: 'appearance', get label() { return t("heading.efb52e7172") }, get aliases(){return aliasTerms(["settingsSearchAliases.ddf377084a","settingsSearchAliases.a6ea4833bc","settingsSearchAliases.f8e7856638","settingsSearchAliases.5f40adcebb","settingsSearchAliases.9a1ee26aae","settingsSearchAliases.2a0070d014"])} },
  { tab: 'appearance', get label() { return t("settingsSearchAliases.101f4d9537") }, get aliases(){return aliasTerms(["settingsSearchAliases.27de889bbb","settingsSearchAliases.d9ae1c9874","settingsSearchAliases.1e8e7040bf","settingsSearchAliases.52b4afad71"])} },
  { tab: 'appearance', get label() { return t("settingsSearchAliases.119ef3fa60") }, get aliases(){return aliasTerms(["settingsSearchAliases.a140ff7ea9","settingsSearchAliases.0ec906a2c1"])} },
  { tab: 'appearance', get label() { return t("settingsSearchAliases.150f572502") }, get aliases(){return aliasTerms(["settingsSearchAliases.d21bb53772","settingsSearchAliases.835452c3ea","settingsSearchAliases.ec6f34a8fa"])} },
  { tab: 'appearance', get label() { return t("settingsSearchAliases.8857605282") }, get aliases(){return aliasTerms(["settingsSearchAliases.5d5b780099","settingsSearchAliases.f03d01644c"])} },
  { tab: 'appearance', get label() { return t("settingsSearchAliases.dfcf46351f") }, get aliases(){return aliasTerms(["settingsSearchAliases.f082db5796","settingsSearchAliases.d329f11d9a"])} },
  { tab: 'appearance', get label() { return t("heading.30921545c4") }, kind: 'section', get aliases(){return aliasTerms(["settingsSearchAliases.1aa9002b51","settingsSearchAliases.6812551e52"])} },
  { tab: 'appearance', get label() { return t("heading.363df1ed9e") }, kind: 'section', get aliases(){return aliasTerms(["settingsSearchAliases.9c857af028","settingsSearchAliases.df043d1199","settingsSearchAliases.eca395eae5"])} },
  // Actions
  { tab: 'actions', get label() { return t("settingsSearchAliases.e04b865f75") }, get aliases(){return aliasTerms(["settingsSearchAliases.e62c78571b","settingsSearchAliases.95b9328d0b","settingsSearchAliases.d45928fd48"])} },
  { tab: 'actions', get label() { return t("settingsSearchAliases.282a09e2cb") }, get aliases(){return aliasTerms(["settingsSearchAliases.7ba642aa90","settingsSearchAliases.27d66c0dce","settingsSearchAliases.d3086fc138"])} },
  // Input
  { tab: 'input', get label() { return t("settingsSearchAliases.42bdbfd3e3") }, get aliases(){return aliasTerms(["settingsSearchAliases.a2c183d5a1"])} },
  { tab: 'input', get label() { return t("settingsSearchAliases.fc1a062179") }, get aliases(){return aliasTerms(["settingsSearchAliases.b84d0c873a","settingsSearchAliases.fb545e4ea9","settingsSearchAliases.5e7073a035","settingsSearchAliases.f4dfbc4d7a"])} },
  { tab: 'input', get label() { return t("settingsSearchAliases.bad6ca037b") }, get aliases(){return aliasTerms(["settingsSearchAliases.ef15907363","settingsSearchAliases.3e37a2ad8f","settingsSearchAliases.061a0a32ce"])} },
  { tab: 'input', get label() { return t("settingsSearchAliases.5194a6b4e2") }, get aliases(){return aliasTerms(["settingsSearchAliases.a78c946754","settingsSearchAliases.c12251a696"])} },
  { tab: 'input', get label() { return t("settingsSearchAliases.f3e334b454") }, get aliases(){return aliasTerms(["settingsSearchAliases.02aaf1960a","settingsSearchAliases.896df61145"])} },
  { tab: 'input', get label() { return t("heading.f5281d5e03") }, get aliases(){return aliasTerms(["settingsSearchAliases.d062b7b18e","settingsSearchAliases.0f2ed9e33d","settingsSearchAliases.9dc3436295","settingsSearchAliases.bf09b07fa1"])} },
  { tab: 'input', get label() { return t("heading.e9bef0b0f3") }, get aliases(){return aliasTerms(["settingsSearchAliases.a44728efae","settingsSearchAliases.79cf3fa97e","settingsSearchAliases.2b6bffd5d9","settingsSearchAliases.101dfd46b4","settingsSearchAliases.1f7378b5c1"])} },
  // Notes
  { tab: 'notes', get label() { return t("settingsSearchAliases.8691e20252") }, get aliases(){return aliasTerms(["settingsSearchAliases.a6105b3238","settingsSearchAliases.00f42c8115"])} },
  { tab: 'notes', get label() { return t("settingsSearchAliases.b9b69191a6") }, get aliases(){return aliasTerms(["settingsSearchAliases.5a9cb6b54e","settingsSearchAliases.f4cb6b2927"])} },
  { tab: 'notes', get label() { return t("settingsSearchAliases.8178e6c324") }, get aliases(){return aliasTerms(["settingsSearchAliases.b1e4a32a40","settingsSearchAliases.93d7f7d634"])} },
  // Voice
  { tab: 'voice', get label() { return t("settingsSearchAliases.781d1ae86a") }, get aliases(){return aliasTerms(["settingsSearchAliases.317e938a48","settingsSearchAliases.3220340f6c","settingsSearchAliases.e584d86734"])} },
  { tab: 'voice', get label() { return t("settingsSearchAliases.472590ae97") }, get aliases(){return aliasTerms(["settingsSearchAliases.ee5b5b7f6f","settingsSearchAliases.a61ef801ae","settingsSearchAliases.1791019474","settingsSearchAliases.46f843559b"])} },
  { tab: 'voice', get label() { return t("settingsSearchAliases.c04a719695") }, kind: 'field', get aliases(){return aliasTerms(["settingsSearchAliases.d7f4d3ccee","settingsSearchAliases.224948ca81","settingsSearchAliases.94bf83953f","settingsSearchAliases.f9eec27ab1","settingsSearchAliases.1a0dfdf443","settingsSearchAliases.fdbe21549a"])} },
  { tab: 'voice', get label() { return t("settingsSearchAliases.f5f0563199") }, kind: 'field', get aliases(){return aliasTerms(["settingsSearchAliases.361b352d69","settingsSearchAliases.3477e22ff3","settingsSearchAliases.faf371aaf3"])} },
  { tab: 'voice', get label() { return t("settingsSearchAliases.dff4641557") }, get aliases(){return aliasTerms(["settingsSearchAliases.698e347e64","settingsSearchAliases.c5f9d8f01e","settingsSearchAliases.386ebc0efd"])} },
  // Alerts
  { tab: 'notifications', get label() { return t("settingsSearchAliases.b4c963cccb") }, get aliases(){return aliasTerms(["settingsSearchAliases.52ebc736be","settingsSearchAliases.b6972688cb","settingsSearchAliases.41a68bae2c","settingsSearchAliases.d01b7ce915"])} },
  { tab: 'notifications', get label() { return t("settingsSearchAliases.ff2e147b76") }, get aliases(){return aliasTerms(["settingsSearchAliases.29e77a40bc","settingsSearchAliases.65baf902ca"])} },
  { tab: 'notifications', get label() { return t("settingsSearchAliases.bf0671dd69") }, get aliases(){return aliasTerms(["settingsSearchAliases.d16a08bb39","settingsSearchAliases.8b38db591e","settingsSearchAliases.a7aa1b9790"])} },
  // Remote
  { tab: 'remote', get label() { return t("settingsSearchAliases.b87f5d702f") }, get aliases(){return aliasTerms(["settingsSearchAliases.795b104abe","settingsSearchAliases.ec6f049478","settingsSearchAliases.4d8608d973","settingsSearchAliases.045ee54aae"])} },
  { tab: 'remote', get label() { return t("settingsSearchAliases.5ea4f31d60") }, get aliases(){return aliasTerms(["settingsSearchAliases.d708cf35ec","settingsSearchAliases.f6deff0095","settingsSearchAliases.7eb686d54d"])} },
  // Maintenance
  { tab: 'maintenance', get label() { return t("settingsSearchAliases.4a99175ce6") }, get aliases(){return aliasTerms(["settingsSearchAliases.2937013f21","settingsSearchAliases.35759b3ca7","settingsSearchAliases.f47900787f","settingsSearchAliases.7fef9479c8"])} },
  { tab: 'maintenance', get label() { return t("settingsSearchAliases.b12a0f8d97") }, get aliases(){return aliasTerms(["settingsSearchAliases.4d791b04cb","settingsSearchAliases.d253bee345","settingsSearchAliases.3ace60b0a0"])} },
  { tab: 'maintenance', get label() { return t("settingsSearchAliases.4c36f2b86a") }, get aliases(){return aliasTerms(["settingsSearchAliases.9153a7339a","settingsSearchAliases.fd343ae984","settingsSearchAliases.98f38f12db"])} },
  { tab: 'maintenance', get label() { return t("settingsSearchAliases.b4205182f0") }, kind: 'action', get aliases(){return aliasTerms(["settingsSearchAliases.b3e5ea77d2","settingsSearchAliases.5a22eada1a"])} },
  { tab: 'maintenance', get label() { return t("settingsSearchAliases.8f6bc9df7a") }, get aliases(){return aliasTerms(["settingsSearchAliases.c6f51afddd","settingsSearchAliases.3b2d4f271f","settingsSearchAliases.2d153b4ca2"])} },
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
