import { t, sourceText, type MessageId } from './localization/index.ts'
/**
 * The Settings panel's navigation model: which tabs exist, how they group, how a
 * deep link names one, and which tabs expose separate pages.
 *
 * It lives apart from `Settings.tsx` because none of it needs a renderer. The
 * panel is one very large component, and the rules that decide *where a setting
 * is* are worth being able to assert without mounting it.
 */

/**
 * Every tab names one subsystem, and `group` is what lets seventeen of them stay
 * navigable. This array is the single source of truth for both layouts: the
 * desktop and mobile sidebars draw a heading wherever the group changes. Tabs of
 * one group must therefore stay contiguous - a group is its run, not its members.
 */
export const settingsTabs = [
  {id:'general',get label() { return t("settingsTabs.c910d474dc") },get group(){return t("settingsTabs.87bb59ba2f")}},
  {id:'projects',get label() { return t("settingsTabs.04e2a9728a") },get group(){return t("settingsTabs.87bb59ba2f")}},
  {id:'terminals',get label() { return t("settingsTabs.7482c40f80") },get group(){return t("settingsTabs.87bb59ba2f")}},
  {id:'git',get label() { return t("settingsTabs.b949c922b6") },get group(){return t("settingsTabs.87bb59ba2f")}},
  {id:'processes',get label() { return t("settingsTabs.4a81a7ba53") },get group(){return t("settingsTabs.87bb59ba2f")}},
  {id:'harnesses',get label() { return t("settingsTabs.f817714a1a") },get group(){return t("settingsTabs.279b44d2ab")}},
  {id:'accounts',get label() { return t("settingsTabs.8a7c8b67fe") },get group(){return t("settingsTabs.279b44d2ab")}},
  {id:'queue',get label() { return t("settingsTabs.ac87afaf2f") },get group(){return t("settingsTabs.279b44d2ab")}},
  {id:'automation',get label() { return t("settingsTabs.d909750b1b") },get group(){return t("settingsTabs.279b44d2ab")}},
  {id:'plugins',get label() { return t("settingsTabs.9514b7ff48") },get group(){return t("settingsTabs.279b44d2ab")}},
  {id:'usage',get label() { return t("settingsTabs.8d59829c1e") },get group(){return t("settingsTabs.279b44d2ab")}},
  {id:'appearance',get label() { return t("settingsTabs.3907fa7f80") },get group(){return t("settingsTabs.c26b3ed4ce")}},
  {id:'actions',get label() { return t("settingsTabs.ff8059dc67") },get group(){return t("settingsTabs.c26b3ed4ce")}},
  {id:'input',get label() { return t("settingsTabs.36ecb4f866") },get group(){return t("settingsTabs.c26b3ed4ce")}},
  {id:'notes',get label() { return t("settingsTabs.8a7525b149") },get group(){return t("settingsTabs.c26b3ed4ce")}},
  {id:'voice',get label() { return t("settingsTabs.87bf2bc085") },get group(){return t("settingsTabs.c26b3ed4ce")}},
  {id:'notifications',get label() { return t("settingsTabs.f62e9267d1") },get group(){return t("settingsTabs.6725e7bbcd")}},
  {id:'remote',get label() { return t("settingsTabs.ffa98e02f7") },get group(){return t("settingsTabs.6725e7bbcd")}},
  {id:'maintenance',get label() { return t("settingsTabs.17ccfa5b68") },get group(){return t("settingsTabs.6725e7bbcd")}},
] as const

export type SettingsTab = typeof settingsTabs[number]['id']
export type SettingsTabEntry = typeof settingsTabs[number]

export type SettingsSubpage = { id:string; label:string }

/**
 * Long Settings tabs are real page collections, not anchors into one document.
 *
 * Only tabs that are genuinely several screens long earn pages: each declared page must
 * itself be substantial, because a page holding two controls costs a navigation step to
 * show less than a glance would. Every other tab renders as one scrolling column, and
 * the sidebar lists its sections as scroll anchors while it is the active tab.
 *
 * IDs match `sectionSlug(label)`, which is also stamped onto the rendered `<h3>`. The
 * explicit registry lets the sidebar expose a tab's pages before that tab has mounted,
 * while the renderer audit below still catches a heading that drifts from its declaration.
 */
function subpageSlug(label:string):string {
  return label.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'')||'section'
}
const subpages = (...ids:MessageId[]):SettingsSubpage[] => ids.map(id=>({id:subpageSlug(sourceText(id)),get label(){return t(id)}}))

export const settingsSubpages:Partial<Record<SettingsTab,SettingsSubpage[]>> = {
  accounts:subpages("settingsTabs.a701acdcc0","settingsTabs.ea70384c46","settingsTabs.d17d2d78d7"),
  queue:subpages("settingsTabs.d4b1ea5708","settingsTabs.a5c30a1abf","settingsTabs.2bfc347157","settingsTabs.be19f68d0c","settingsTabs.14be9614c7","settingsTabs.4a7178cdec"),
  appearance:subpages("settingsTabs.c26b3ed4ce","heading.30921545c4","heading.363df1ed9e"),
  input:subpages("settingsTabs.d00f1e4dc0","settingsTabs.114b9bbcf1","settingsTabs.7ca62c6a45","settingsTabs.f3e334b454","heading.e9bef0b0f3"),
  voice:subpages("heading.0791ec6847","heading.116fe62640","heading.7fca441bba","heading.0fe741492b","settingsTabs.268f14bbfe"),
}

const groupedHeadings:Partial<Record<SettingsTab,Record<string,string>>> = {
  appearance:{
    "heading.f51cb60111":'interface',"heading.efb52e7172":'interface',"heading.c70da52b01":'interface',"heading.14721e2ef2":'interface',"heading.6ba5218d81":'interface',
    "heading.30921545c4":'session-rows',
    "heading.363df1ed9e":'session-top-bars',
  },
  // The OpenRouter block (the API key, and the catalogue it fetches) is a second
  // section on the Model provider page, rendered only while that provider is
  // selected. Undeclared it matched no page, so it was hidden on every one of
  // them and the key could not be reached from Settings at all.
  accounts:{"heading.eb70c3bc14":'model-provider'},
  // The shortcuts page is three headings deep - the preset picker, the shortcut
  // tree, and the browser probe - and only the middle one is the page's name.
  // Without these its section resolved to `what-this-browser-gives-the-app`,
  // which is not a declared page, so selecting "Keyboard shortcuts" matched no
  // section, hid all of them, and drew a blank pane.
  input:{
    "heading.f5281d5e03":'keyboard-shortcuts',
    "heading.e9bef0b0f3":'keyboard-shortcuts',
    "heading.8b07606865":'keyboard-shortcuts',
  },
  voice:{
    "heading.927af3f8ff":'read-aloud',"heading.0791ec6847":'read-aloud',"heading.8439d96892":'read-aloud',"heading.6491c8fb07":'read-aloud',"heading.75cb1e131c":'read-aloud',"heading.aeba66a52f":'read-aloud',"heading.af71af7319":'read-aloud',
    "heading.c0e191a156":'talk-dictation',"heading.116fe62640":'talk-dictation',
    "heading.c75483c85c":'voice-commands',"heading.7fca441bba":'voice-commands',"heading.64a0c611be":'voice-commands',
    "heading.0fe741492b":'mux-assistant',
    "heading.cfc1c5858d":'diagnostics',"heading.732d8524f8":'diagnostics',
  },
}

/** The declared page that owns one rendered heading. */
export function settingsSubpageId(tab:SettingsTab,heading:string):string {
  const value=heading.trim()
  const group=groupedHeadings[tab]
  if(group)for(const [id,page] of Object.entries(group)){
    if(sourceText(id)===value||t(id as MessageId)===value)return page
  }
  return settingsSubpages[tab]?.find(page=>page.label===value)?.id||subpageSlug(value)
}

/**
 * How many heading levels a search result names after its tab. Two is the depth the
 * panel actually nests to (`<h3>` block, `<h4>` group); a deeper path would be a
 * result row that wraps rather than one that places itself.
 */
export const BREADCRUMB_HEADINGS = 2

/**
 * Where a search result lives, as one line: tab, then the page that owns it, then
 * its headings. The page is named only when a heading does not already say it —
 * Input's pages *are* its `<h3>`s, so "Input · Keyboard shortcuts · View" repeats
 * nothing, while Voice groups several headings onto one page and "Voice · Read
 * aloud · Voice and engine" is the only form that says where to click.
 *
 * It lives here rather than in the search index because the page mapping does: the
 * index walks markup and knows nothing about which tabs paginate.
 */
export function settingsBreadcrumb(tab:SettingsTab,tabLabel:string,path:readonly string[]):string {
  const crumbs=[tabLabel]
  const pages=settingsSubpages[tab]||[]
  if(pages.length>1&&path.length){
    const page=pages.find(entry=>entry.id===settingsSubpageId(tab,path[0]))
    if(page&&page.label.trim().toLowerCase()!==path[0].trim().toLowerCase())crumbs.push(page.label)
  }
  crumbs.push(...path.slice(-BREADCRUMB_HEADINGS))
  return crumbs.join(' · ')
}

/**
 * Commands that work the narrow layout's section drawer — the slide-in twin of the
 * docked column above. They are the shell's, not the panel's, because the gesture
 * recognizer lives at the shell level and the command bus is the only channel a
 * resolved gesture has.
 */
export const SETTINGS_NAV_TOGGLE = 'settingsNav.toggle'
export const SETTINGS_NAV_CLOSE = 'settingsNav.close'

/**
 * Contiguous runs of one group, in tab order. Derived, so adding a tab to the
 * list is the whole change — there is no second list of group memberships to
 * keep in step, and a tab that drifts away from its group shows up as a repeated
 * heading rather than as a silently miscategorised tab.
 */
export const settingsTabGroups = (): {group:string;tabs:SettingsTabEntry[]}[] =>
  settingsTabs.reduce<{group:string;tabs:SettingsTabEntry[]}[]>((groups,tab)=>{
    const last=groups[groups.length-1]
    if(last&&last.group===tab.group)last.tabs.push(tab)
    else groups.push({group:tab.group,tabs:[tab]})
    return groups
  },[])

/**
 * Names a deep-link caller may use that are not simply a tab's label. Matching on
 * the label first is what keeps this from rotting: every tab is addressable by
 * its own name without being listed here, so this holds only genuine aliases —
 * older tab names and the headings callers historically passed.
 */
export const SECTION_ALIASES:Record<string,SettingsTab> = {
  agents:'harnesses',harness:'harnesses',
  'usage analytics':'usage','usage and operational telemetry':'usage',
  'git and history':'git','git and worktrees':'git','git & processes':'git',
  'project and process evidence':'processes',
  notes:'notes','note editor':'notes',
  'hooks and notifications':'notifications',notifications:'notifications',
  'remote and security':'remote',
  // The tab this renamed from. It is an alias rather than a label match because
  // Voice still has a page called "Diagnostics"; without this entry a deep link
  // saying "diagnostics" would fall through to General.
  diagnostics:'maintenance','system prerequisites':'maintenance','factory reset':'maintenance',
  'auto-delivery':'queue','agent messaging':'queue','prompt queue':'queue',
  extensions:'plugins','plugin manager':'plugins',
  // The schedules themselves live in the drawer's Schedule tab; only the install-wide
  // limits are in the Automation workspace; Settings links there.
  'scheduled runs':'automation',schedules:'automation',
  'read aloud (tts)':'voice',
}

/** Which tab a deep link's section name opens. Unknown names land on General. */
export const tabForSection = (section:string):SettingsTab => {
  const key=section.trim().toLowerCase()
  const byLabel=settingsTabs.find(tab=>tab.label.toLowerCase()===key||tab.id===key)
  return byLabel?byLabel.id:(SECTION_ALIASES[key]||'general')
}

/**
 * Tab ids persisted by older builds, pointed at wherever their content now lives.
 * Without this a device that last used "Git & processes" silently reopens on
 * General after the split, which reads as the panel forgetting rather than as a
 * tab having been renamed.
 */
export const LEGACY_TAB_IDS:Record<string,SettingsTab> = {
  workspace:'git',agents:'harnesses',diagnostics:'maintenance',
}

// Which tab Settings opens on when nothing asked for a specific one. Persisted per
// device rather than held in App state so it survives a reload — Settings is opened,
// scanned, and closed dozens of times a session, and landing on General every time
// re-costs the navigation that brought you to the tab you actually live in. An
// explicit `initialSection` (Voice from the TTS chip, Accounts from the switcher,
// and so on) always wins: that caller knows where the user needs to be.
export const SETTINGS_TAB_KEY='mux.settings.tab.v1'

export const rememberedTab = ():SettingsTab => {
  let stored:string|null=null
  try { stored=localStorage.getItem(SETTINGS_TAB_KEY) } catch { return 'general' }
  if(!stored)return 'general'
  if(LEGACY_TAB_IDS[stored])return LEGACY_TAB_IDS[stored]
  // Validated against the live tab list, so a tab that is renamed or removed
  // degrades to General instead of rendering an empty panel.
  return settingsTabs.some(tab=>tab.id===stored)?stored as SettingsTab:'general'
}

export const rememberTab = (tab:SettingsTab):void => {
  try { localStorage.setItem(SETTINGS_TAB_KEY,tab) } catch { /* private mode */ }
}

// The in-tab half of remembering the tab. Long tabs expose one separate page at a
// time, so each tab reopens the page it was left on.
export const SETTINGS_SECTION_KEY='mux.settings.section.v1'

export const rememberedSections = ():Record<string,string> => {
  try {
    const parsed:unknown=JSON.parse(localStorage.getItem(SETTINGS_SECTION_KEY)||'{}')
    return parsed&&typeof parsed==='object'&&!Array.isArray(parsed)?parsed as Record<string,string>:{}
  } catch { return {} }
}

export const rememberSection = (tab:SettingsTab,section:string):void => {
  try {
    const all=rememberedSections()
    if(all[tab]===section)return
    all[tab]=section
    localStorage.setItem(SETTINGS_SECTION_KEY,JSON.stringify(all))
  } catch { /* private mode */ }
}

/** One entry of a tab's section rail: one `<h3>` that tab rendered. */
export type SettingsRailSection = {id:string;label:string}

/**
 * Fewer sections than this and listing them is a row spent saying what one glance
 * already shows, so shorter tabs list none. Tabs are not annotated with whether they
 * get section links — the count decides, from what the tab actually rendered.
 */
export const SECTION_RAIL_MIN = 2

export const sectionSlug = (label:string):string =>
  label.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'')||'section'

/**
 * Stable ids for a tab's headings, disambiguating any that repeat. Kept pure so
 * the numbering rule is assertable without a DOM: a remembered section id has to
 * survive a reload, so two headings that slug the same must not both answer to
 * the same id.
 */
export const railSectionIds = (labels:string[],sources:readonly string[]=labels):SettingsRailSection[] => {
  const used=new Map<string,number>()
  const out:SettingsRailSection[]=[]
  for(const [index,raw] of labels.entries()){
    const label=raw.trim()
    if(!label)continue
    const base=sectionSlug(sources[index]||label)
    const seen=(used.get(base)||0)+1
    used.set(base,seen)
    out.push({id:seen>1?`${base}-${seen}`:base,label})
  }
  return out
}

export const sameRailSections = (left:SettingsRailSection[],right:SettingsRailSection[]):boolean =>
  left.length===right.length&&left.every((item,index)=>item.id===right[index].id&&item.label===right[index].label)
