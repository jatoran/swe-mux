import assert from 'node:assert/strict'
import test from 'node:test'
import {
  filterSkills,
  groupSkills,
  inventoryNote,
  scopeLabel,
  skillLabel,
  skillTitle,
  type AgentSkill,
  type SkillInventory,
} from '../src/skills.ts'

function skill(overrides: Partial<AgentSkill> = {}): AgentSkill {
  return {
    name: 'learn',
    description: 'Read project documentation before diving in',
    path: 'C:/home/.codex/skills/learn/SKILL.md',
    scope: 'user',
    origin: 'user skills',
    kind: 'skill',
    invocation: '$learn',
    mtime: 100,
    implicit: true,
    display_name: null,
    short_description: null,
    shadowed_by: null,
    added_after_start: false,
    ...overrides,
  }
}

function inventory(overrides: Partial<SkillInventory> = {}): SkillInventory {
  return {
    backend: 'codex',
    cwd: 'D:/repo',
    generated_at: 10,
    agent_loaded_at: 5,
    agent_run_started_at: 5,
    roots: [],
    skills: [],
    errors: [],
    truncated: false,
    skipped_plugins: [],
    builtin_skills_hidden: false,
    ...overrides,
  }
}

test('filter matches name, description, and origin', () => {
  const skills = [
    skill({ name: 'learn', description: 'read the docs', origin: 'user skills' }),
    skill({ name: 'dockerize', description: 'containerize a project', origin: 'plugin: dev-browser' }),
  ]
  assert.deepEqual(filterSkills(skills, 'learn').map(item => item.name), ['learn'])
  assert.deepEqual(filterSkills(skills, 'containerize').map(item => item.name), ['dockerize'])
  assert.deepEqual(filterSkills(skills, 'dev-browser').map(item => item.name), ['dockerize'])
  // A blank query is not a filter, and must not reorder the list.
  assert.deepEqual(filterSkills(skills, '  ').map(item => item.name), ['learn', 'dockerize'])
})

test('filter is substring, not subsequence', () => {
  // The palette's fuzzy matcher would return a hit for almost any query against
  // long skill descriptions, which reads as "the filter is broken".
  const skills = [skill({ name: 'learn', description: 'read the docs' })]
  assert.deepEqual(filterSkills(skills, 'lrn'), [])
})

test('groups follow precedence order and drop empties', () => {
  const skills = [
    skill({ name: 'bundled', scope: 'system' }),
    skill({ name: 'mine', scope: 'user' }),
    skill({ name: 'ours', scope: 'project' }),
  ]
  assert.deepEqual(groupSkills(skills).map(group => group.scope), ['project', 'user', 'system'])
  assert.deepEqual(groupSkills(skills).map(group => group.label), [
    scopeLabel('project'), scopeLabel('user'), scopeLabel('system'),
  ])
  assert.deepEqual(groupSkills([]).length, 0)
})

test('a Codex display name wins over the raw name', () => {
  assert.equal(skillLabel(skill({ display_name: 'Evaluate Update' })), 'Evaluate Update')
  assert.equal(skillLabel(skill({ display_name: null })), 'learn')
})

test('the tooltip leads with what changes the click', () => {
  // Both caveats mean "this will not do what the button implies", so they come
  // before the description rather than after it.
  const title = skillTitle(skill({ added_after_start: true, implicit: false, shadowed_by: 'project skills' }))
  const lines = title.split('\n')
  assert.match(lines[0], /Added after this agent loaded/)
  assert.match(lines[1], /Explicit-only/)
  assert.match(lines[2], /Shadowed by project skills/)
  assert.match(lines[3], /\$learn · user skills/)
  assert.equal(lines[4], 'Read project documentation before diving in')
})

test('an ordinary skill gets an ordinary tooltip', () => {
  assert.equal(
    skillTitle(skill()),
    '$learn · user skills\nRead project documentation before diving in',
  )
})

test("Claude's hidden built-ins are disclosed, not implied", () => {
  // A bare list would quietly assert that /review and /dataviz do not exist.
  assert.match(inventoryNote(inventory({ builtin_skills_hidden: true })), /built-in/)
  assert.equal(inventoryNote(inventory()), '')
  assert.equal(inventoryNote(null), '')
})

test('skipped plugins, truncation, and unreadable entries are all disclosed', () => {
  const note = inventoryNote(inventory({
    skipped_plugins: ['dev-browser@market'],
    truncated: true,
    errors: [{ path: 'x', message: 'directory has no SKILL.md' }],
  }))
  assert.match(note, /1 disabled plugin/)
  assert.match(note, /truncated/)
  assert.match(note, /1 unreadable/)
})
