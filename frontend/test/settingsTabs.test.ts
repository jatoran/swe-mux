import assert from 'node:assert/strict'
import test from 'node:test'
import {
  LEGACY_TAB_IDS,
  railSectionIds,
  SECTION_ALIASES,
  SECTION_RAIL_MIN,
  sectionSlug,
  sameRailSections,
  settingsTabGroups,
  settingsTabs,
  tabForSection,
} from '../src/settingsTabs.ts'

const ids:string[]=settingsTabs.map(tab=>tab.id)

test('tab ids and labels are both unique', () => {
  // Ids are what localStorage and deep links carry, so duplicates would make a
  // remembered tab ambiguous and a label collision would make one unreachable by name.
  assert.equal(new Set(ids).size,ids.length,'tab ids must be unique')
  const labels=settingsTabs.map(tab=>tab.label.toLowerCase())
  assert.equal(new Set(labels).size,labels.length,'tab labels must be unique')
})

test('each settings group is one contiguous run covering every tab in order', () => {
  // A group is a contiguous run. If a tab drifts away from its group the run splits,
  // and the sidebar would draw the same heading twice - which is the bug, not the fix.
  const groupNames=settingsTabGroups.map(group=>group.group)
  assert.equal(new Set(groupNames).size,groupNames.length,'each group must be one contiguous run')
  assert.deepEqual(groupNames,['Workspace','Agents','Interface','System'])
  assert.equal(settingsTabGroups.flatMap(group=>group.tabs).length,settingsTabs.length)
  assert.deepEqual(settingsTabGroups.flatMap(group=>group.tabs).map(tab=>tab.id),ids)
})

test('every tab is addressable by its own label, case- and space-insensitively', () => {
  for(const tab of settingsTabs){
    assert.equal(tabForSection(tab.label),tab.id,`${tab.label} must resolve to itself`)
    assert.equal(tabForSection(tab.label.toUpperCase()),tab.id)
    assert.equal(tabForSection(`  ${tab.label}  `),tab.id)
  }
})

test('no alias or legacy id points at a tab that no longer exists', () => {
  // Aliases exist to survive renames; one pointing at a tab that no longer exists is
  // the exact drift the old hand-maintained map accumulated.
  for(const [alias,target] of Object.entries(SECTION_ALIASES)){
    assert.ok(ids.includes(target),`alias ${alias} points at a missing tab ${target}`)
  }
  for(const [legacy,target] of Object.entries(LEGACY_TAB_IDS)){
    assert.ok(ids.includes(target),`legacy id ${legacy} points at a missing tab ${target}`)
    assert.ok(!ids.includes(legacy),`legacy id ${legacy} is still a live tab id`)
  }
})

test('the section names the app actually passes all resolve somewhere deliberate', () => {
  // The section names App.tsx and UtilityDrawer.tsx actually pass today. Each must
  // land somewhere deliberate; General is the fallback, so reaching it means the name
  // resolved to nothing.
  for(const section of ['Accounts','Voice','Terminals','Agents','Usage analytics','Automation','Input']){
    assert.notEqual(tabForSection(section),'general',`${section} must not fall back to General`)
  }
  assert.equal(tabForSection('Agents'),'harnesses')
  assert.equal(tabForSection('Usage analytics'),'usage')
  assert.equal(tabForSection('Git & processes'),'git')
  assert.equal(tabForSection('nothing named this'),'general')
})

test('section slugs are url-safe and never empty', () => {
  assert.equal(sectionSlug('Read aloud (TTS)'),'read-aloud-tts')
  assert.equal(sectionSlug('  Budgets and execution '),'budgets-and-execution')
  assert.equal(sectionSlug('///'),'section')
})

test('rail section ids stay unique when two headings slug the same', () => {
  // A remembered section id has to survive a reload, so two headings that slug the
  // same must not both answer to the same id.
  assert.deepEqual(railSectionIds(['Theme','Session rows','']),[
    {id:'theme',label:'Theme'},
    {id:'session-rows',label:'Session rows'},
  ])
  assert.deepEqual(railSectionIds(['Firewall','Firewall','Firewall']).map(section=>section.id),
    ['firewall','firewall-2','firewall-3'])
  assert.deepEqual(railSectionIds(['  Spaced  ']),[{id:'spaced',label:'Spaced'}])
})

test('rail comparison is by content, not identity', () => {
  assert.equal(sameRailSections(railSectionIds(['A','B']),railSectionIds(['A','B'])),true)
  assert.equal(sameRailSections(railSectionIds(['A','B']),railSectionIds(['A','C'])),false)
  assert.equal(sameRailSections(railSectionIds(['A']),railSectionIds(['A','B'])),false)
})

test('a rail of one entry is never drawn as navigation', () => {
  assert.ok(SECTION_RAIL_MIN>=2,'a rail of one entry is never navigation')
})
