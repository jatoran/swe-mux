import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const app = readFileSync(join(import.meta.dirname, '..', 'src', 'App.tsx'), 'utf8')
const css = readFileSync(join(import.meta.dirname, '..', 'src', 'style.css'), 'utf8')

test('the empty workspace stage offers the Run menu itself, front and centre', () => {
  // The stage used to name four ways to begin and offer none; the only control attached to
  // the region was a right-click menu, invisible and unreachable on touch. The button opens
  // the same Run menu every other trigger does rather than a backend shortcut, so there is
  // still exactly one launcher surface.
  assert.ok(app.includes("const EMPTY_STAGE_RUN_TRIGGER='empty-workspace'"))
  assert.ok(app.includes('onClick={event=>toggleRunMenu(activeProject,event.currentTarget,EMPTY_STAGE_RUN_TRIGGER)}'))
  assert.ok(app.includes('>▶ Run shell or session</button>'))
  // A menu trigger says it is one, and says whether its menu is up - by its own trigger id,
  // so the header chip's open menu does not read as this button's.
  assert.ok(app.includes("aria-expanded={runMenu?.project.id===activeProject.id&&runMenu?.trigger===EMPTY_STAGE_RUN_TRIGGER}"))
  assert.ok(app.includes('aria-haspopup="menu"\n      aria-expanded={runMenu?.project.id===activeProject.id&&runMenu?.trigger===EMPTY_STAGE_RUN_TRIGGER}'))
  // No active Project means nothing to run in, so the button goes with it and the copy stays.
  assert.ok(app.includes('{activeProject&&<button\n      type="button"\n      class="empty-stage-run"'))
  assert.ok(app.includes('<p>Run a terminal, or open a note, a file, or a preview to begin. Files and notes live in the side panel.</p>'))
})

test('desktop and mobile draw the same empty stage', () => {
  // One helper, two placements: the desktop pane tree's stage and the mobile projection's.
  // Two hand-copied blocks is how the desktop and mobile copy drifted before.
  assert.equal(app.match(/<h1>Your Project workspace\.<\/h1>/g)?.length, 1, 'the copy lives once')
  assert.ok(app.includes("{emptyStage('stack-active empty-stage')}"), 'the desktop pane tree uses the helper')
  assert.ok(app.includes(":emptyStage('empty-stage')}"), 'the mobile projection uses the helper')
})

test('the stage button is drawn as an accent control larger than the header chip', () => {
  // Scoped under `.empty-stage` because the generic `.empty-stage button` rule would win
  // every shared property otherwise, leaving a 34px grey button under a comment promising
  // an accent one.
  assert.match(css, /\.empty-stage \.empty-stage-run \{[^}]*height:calc\(38px\*var\(--ui-scale\)\)[^}]*color:var\(--accent\)/)
  assert.match(css, /\.empty-stage \.empty-stage-run\[aria-expanded="true"\]/, 'an open menu reads on its trigger')
})
