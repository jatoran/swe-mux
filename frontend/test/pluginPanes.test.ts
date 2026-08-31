import assert from 'node:assert/strict'
import test from 'node:test'
import { placePluginPane, pluginPaneTarget } from '../src/pluginPanes.ts'
import { parseLayout, stackForView, terminalIds } from '../src/layout.ts'
import type { Session } from '../src/types.ts'

const session = { id: 'plugin-pane', project_id: 'project-1' } as Session

test('popup contributions remain overlay-owned', () => {
  assert.deepEqual(pluginPaneTarget(session, 'popup'), {
    mode: 'popup',
    popupId: 'plugin-pane',
  })
})

test('tab and split contributions focus their workspace session', () => {
  for (const placement of ['tab', 'split']) {
    assert.deepEqual(pluginPaneTarget(session, placement), {
      mode: 'workspace',
      projectId: 'project-1',
      sessionId: 'plugin-pane',
    })
  }
})

test('a tab contribution is placed in and focused inside its target Project layout',()=>{
  const base=placePluginPane(parseLayout(null),'first','tab',null)
  const next=placePluginPane(base,'plugin-pane','tab','first')
  assert.deepEqual(terminalIds(next),['first','plugin-pane'])
  assert.equal(stackForView(next,'plugin-pane')?.active_child_id,'plugin-pane')
  const focusedAgain=placePluginPane(next,'plugin-pane','tab','first')
  assert.deepEqual(terminalIds(focusedAgain),['first','plugin-pane'])
  assert.equal(stackForView(focusedAgain,'plugin-pane')?.active_child_id,'plugin-pane')
})

test('a split contribution creates a right-hand Project split',()=>{
  const base=placePluginPane(parseLayout(null),'first','tab',null)
  const next=placePluginPane(base,'plugin-pane','split','first')
  assert.equal(next.root?.type,'split')
  if(next.root?.type==='split')assert.equal(next.root.direction,'vertical')
})
