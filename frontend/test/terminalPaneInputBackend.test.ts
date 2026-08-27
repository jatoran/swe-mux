import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { terminalPanePropsEqual } from '../src/terminalPaneMemo.ts'
import type { Session } from '../src/types'

/**
 * The pane's own wiring of the input-encoding backend, which no test of the pure
 * rules can see.
 *
 * `terminalKeys.test.ts` and `inputBackend.test.ts` both take a backend as an
 * argument, so they are equally green whichever value `TerminalPane` hands them -
 * and for a promoted shell it handed them the wrong one. Measured 2026-08-27 on the
 * frozen app after the promotion fix shipped: a pane spawned as a shell and promoted
 * around a typed `claude` still treated Shift+Enter as a submit, because the
 * terminal's construction effect closed over the `session` prop from the render it
 * mounted on, where the backend was still `shell`.
 *
 * That effect deliberately does *not* re-run on a backend change - rebuilding xterm
 * on promotion would drop the socket and replay the whole buffer - which is why the
 * file keeps live refs for exactly this. The rule is therefore structural, and these
 * tests pin both halves of it: nothing inside the effect may resolve a backend from
 * the captured prop, and the memo must let a re-render through whenever the answer
 * changes.
 */

const source = readFileSync(new URL('../src/TerminalPane.tsx', import.meta.url), 'utf8')

/** The terminal construction effect: everything closed over one render's props. */
const constructionEffect = (): string => {
  const start = source.indexOf('const term = new Terminal(')
  assert.ok(start > 0, 'could not find the terminal construction effect')
  const end = source.indexOf('}, [session.id, keybindings, scrollback', start)
  assert.ok(end > start, 'could not find the construction effect dependency array')
  return source.slice(start, end)
}

const session = (overrides: Partial<Session> = {}): Session =>
  ({
    id: 'pane-1',
    backend: 'shell',
    state: 'running',
    native_session_id: 'pane-1',
    ...overrides,
  }) as Session

// The reference-compared props are hoisted so a test varies only the session. Rebuilding
// them per call would make every comparison unequal for the wrong reason, which is how a
// memo test quietly stops testing anything.
const KEYBINDINGS: Record<string, string> = {}
const MOBILE_INPUT = {}

const props = (overrides: Partial<Session> = {}) =>
  ({
    session: session(overrides),
    broadcast: false,
    keybindings: KEYBINDINGS,
    scrollback: 1000,
    rendererPreference: 'auto',
    mobileInput: MOBILE_INPUT,
    uiScale: 1,
    visible: true,
    claudeMaxColumns: 120,
  }) as never

test('the construction effect never resolves an input backend from the captured prop', () => {
  // `resolveInputBackend(session)` inside this effect reads the session object from
  // the render the effect last ran on, which for a promoted shell is the one where
  // the backend was still `shell`. The live answer is `inputBackendRef.current`.
  assert.doesNotMatch(
    constructionEffect(),
    /resolveInputBackend\(session\)/,
    'the effect resolved a backend from its captured session; use inputBackendRef.current',
  )
})

test('nothing passes the captured session where a backend is what is wanted', () => {
  // The structural half. `pasteIntoTerminal` and `acceptsTerminalAttachments` used to
  // take a `Session` and resolve the backend themselves, which made every in-effect
  // caller silently stale. Taking a backend string instead means the stale call
  // cannot be written.
  const effect = constructionEffect()
  assert.doesNotMatch(effect, /pasteIntoTerminal\([^)]*,\s*session\s*,/, 'pass a backend, not the captured session')
  assert.doesNotMatch(effect, /acceptsTerminalAttachments\(session\)/, 'pass a backend, not the captured session')
})

test('a promotion re-renders the pane', () => {
  // Without this the live refs never update, because they are assigned in the render
  // body and the memo would swallow the only prop change that matters.
  assert.equal(terminalPanePropsEqual(props(), props()), true)
  assert.equal(terminalPanePropsEqual(props(), props({ backend: 'claude' })), false)
})

test('a launch seen in an unpromoted shell re-renders the pane', () => {
  // `agent_launch_pending` is published on its own, before anything else about the
  // session changes: the backend is still `shell`, the state is still `running`, and
  // the conversation id has not been bound. A memo that compares neither swallows it,
  // and the launch window this field exists for never opens.
  assert.equal(
    terminalPanePropsEqual(props(), props({ agent_launch_pending: ['claude'] })),
    false,
  )
  assert.equal(
    terminalPanePropsEqual(
      props({ agent_launch_pending: ['claude'] }),
      props({ agent_launch_pending: ['claude'] }),
    ),
    true,
  )
})

test('a contention verdict re-renders the pane', () => {
  // The notice is rendered from the record, so a pane that does not re-render on it
  // stays silent through exactly the fault the notice exists to state.
  assert.equal(
    terminalPanePropsEqual(
      props(),
      props({ console_contention: { reason: 'agent_orphaned', since: 1 } }),
    ),
    false,
  )
})

test('the construction effect reads no backend off the captured session at all', () => {
  // The general rule, rather than one call site at a time. Four more behaviours were
  // found frozen at `shell` on a promoted pane while fixing Shift+Enter: the "Copy last
  // reply" gate, the scrollback-repaint request, the Codex column-floor font policy, and
  // the DOM-only renderer choice. All four are the same defect, and none of them are
  // reachable by a test of the pure helper each one calls.
  //
  // `backendRef.current` is equivalent at mount (it is assigned during the render that
  // schedules this effect) and correct afterwards, so there is no case left where
  // reading the prop is the right thing to do here.
  const offenders = [...constructionEffect().matchAll(/session\.backend/g)]
  assert.equal(
    offenders.length,
    0,
    'the construction effect read session.backend; use backendRef.current (or inputBackendRef.current for encoding)',
  )
})

test('a pane promoted onto a DOM-only harness drops its WebGL surface', () => {
  // The one member of that family a ref cannot fix: the renderer is chosen once, at
  // construction, and a pane that mounted as a shell chose WebGL. Claude is deliberately
  // DOM-only - a retained alternate-screen WebGL surface can come back from a hidden
  // compositing interval live but corrupt, with no context-loss event to recover from -
  // so the addon has to be disposed when the promotion lands.
  assert.match(
    source,
    /dropWebglRef\.current/,
    'nothing drops the WebGL addon when a pane is promoted onto a DOM-only harness',
  )
})
