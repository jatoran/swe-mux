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
  {id:'general',label:'General',group:'Workspace'},
  {id:'projects',label:'Projects',group:'Workspace'},
  {id:'terminals',label:'Terminals',group:'Workspace'},
  {id:'git',label:'Git',group:'Workspace'},
  {id:'processes',label:'Processes',group:'Workspace'},
  {id:'harnesses',label:'Harnesses',group:'Agents'},
  {id:'accounts',label:'Accounts',group:'Agents'},
  {id:'queue',label:'Prompt queue',group:'Agents'},
  {id:'automation',label:'Automation',group:'Agents'},
  {id:'plugins',label:'Plugins',group:'Agents'},
  {id:'usage',label:'Usage',group:'Agents'},
  {id:'appearance',label:'Appearance',group:'Interface'},
  {id:'input',label:'Input',group:'Interface'},
  {id:'notes',label:'Text editor',group:'Interface'},
  {id:'voice',label:'Voice',group:'Interface'},
  {id:'notifications',label:'Alerts',group:'System'},
  {id:'remote',label:'Remote',group:'System'},
  {id:'diagnostics',label:'Diagnostics',group:'System'},
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
const subpages = (...labels:string[]):SettingsSubpage[] => labels.map(label=>({id:subpageSlug(label),label}))

export const settingsSubpages:Partial<Record<SettingsTab,SettingsSubpage[]>> = {
  accounts:subpages('Provider accounts','Model provider','Models'),
  queue:subpages('Overview','Auto-delivery','Approvals','Agent messaging','Agent actuation','Queue history'),
  input:subpages('Pointer','Mobile terminal','Clipboard history','Touch gestures','Keyboard shortcuts'),
  voice:subpages('Read aloud','Talk & dictation','Voice commands','Mux assistant','Diagnostics'),
}

const groupedHeadings:Partial<Record<SettingsTab,Record<string,string>>> = {
  voice:{
    'Read aloud (TTS)':'read-aloud','Read aloud':'read-aloud','Voice and engine':'read-aloud','TTS provider':'read-aloud',Pronunciation:'read-aloud','Spoken summary':'read-aloud','Clip storage':'read-aloud',
    'Microphone and wake words':'talk-dictation','Talk & dictation':'talk-dictation',
    'Command phrases':'voice-commands','Voice commands':'voice-commands','Command reference':'voice-commands',
    'Mux assistant':'mux-assistant',
    'Testing and latency':'diagnostics','Mobile voice':'diagnostics',
  },
}

/** The declared page that owns one rendered heading. */
export function settingsSubpageId(tab:SettingsTab,heading:string):string {
  return groupedHeadings[tab]?.[heading.trim()]||subpageSlug(heading)
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
export const settingsTabGroups: {group:string;tabs:SettingsTabEntry[]}[] =
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
  const byLabel=settingsTabs.find(tab=>tab.label.toLowerCase()===key)
  return byLabel?byLabel.id:(SECTION_ALIASES[key]||'general')
}

/**
 * Tab ids persisted by older builds, pointed at wherever their content now lives.
 * Without this a device that last used "Git & processes" silently reopens on
 * General after the split, which reads as the panel forgetting rather than as a
 * tab having been renamed.
 */
export const LEGACY_TAB_IDS:Record<string,SettingsTab> = {workspace:'git',agents:'harnesses'}

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
export const railSectionIds = (labels:string[]):SettingsRailSection[] => {
  const used=new Map<string,number>()
  const out:SettingsRailSection[]=[]
  for(const raw of labels){
    const label=raw.trim()
    if(!label)continue
    const base=sectionSlug(label)
    const seen=(used.get(base)||0)+1
    used.set(base,seen)
    out.push({id:seen>1?`${base}-${seen}`:base,label})
  }
  return out
}

export const sameRailSections = (left:SettingsRailSection[],right:SettingsRailSection[]):boolean =>
  left.length===right.length&&left.every((item,index)=>item.id===right[index].id&&item.label===right[index].label)
