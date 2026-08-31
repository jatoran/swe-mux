import assert from 'node:assert/strict'
import test from 'node:test'
import { pluginPaneTarget } from '../src/pluginPanes.ts'
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

