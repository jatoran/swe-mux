import assert from 'node:assert/strict'
import test from 'node:test'
import { activateDrawerTab, defaultDrawerLayout, normalizeDrawerProjectPresentation } from '../src/drawerLayout.ts'
import {
  presentationWithTransientDrawerTab, transientDrawerTabForProject,
} from '../src/drawerTransient.ts'

test('a transient Actions visit leaves the Project presentation unchanged', () => {
  const layout = defaultDrawerLayout()
  const persistent = activateDrawerTab(normalizeDrawerProjectPresentation(null, layout), layout, 'notes')
  const snapshot = JSON.stringify(persistent)
  const transient = { projectId: 'project-1', tab: 'actions' as const }
  const tab = transientDrawerTabForProject(transient, 'project-1')
  const rendered = presentationWithTransientDrawerTab(persistent, layout, tab)

  assert.equal(rendered.focused_tab, 'actions')
  assert.equal(persistent.focused_tab, 'notes')
  assert.equal(JSON.stringify(persistent), snapshot)
})

test('a transient tab never crosses Project boundaries', () => {
  const layout = defaultDrawerLayout()
  const persistent = activateDrawerTab(normalizeDrawerProjectPresentation(null, layout), layout, 'git')
  const transient = { projectId: 'project-1', tab: 'actions' as const }
  const tab = transientDrawerTabForProject(transient, 'project-2')

  assert.equal(tab, null)
  assert.equal(presentationWithTransientDrawerTab(persistent, layout, tab), persistent)
})
