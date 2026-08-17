/**
 * The Settings panel's navigation model: which tabs exist, how they group, how a
 * deep link names one, and how a tab's in-page section rail is numbered.
 *
 * It lives apart from `Settings.tsx` because none of it needs a renderer. The
 * panel is one very large component, and the rules that decide *where a setting
 * is* are worth being able to assert without mounting it.
 */

/**
 * Every tab names one subsystem, and `group` is what lets seventeen of them stay
 * navigable. This array is the single source of truth for both layouts: the
 * desktop sidebar draws a heading wherever the group changes, and the mobile rail
 * renders the same order flat with the headings suppressed. Tabs of one group
 * must therefore stay contiguous — a group is its run, not its members.
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
  // The schedules themselves live in the drawer's Schedule tab; only the install-wide
  // limits are here, under Automation.
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
// …) always wins: that caller knows where the user needs to be.
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

// The in-tab half of remembering the tab. Landing at the top of a seven-section
// tab re-costs the scroll that got you to the control you came back for, so each
// tab reopens at the section it was left on.
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
 * Fewer sections than this and a rail costs a row to say what one glance already
 * shows, so short tabs render none. Tabs are not annotated with whether they get
 * a rail — the count decides, from what the tab actually rendered.
 */
export const SECTION_RAIL_MIN = 4

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
