import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync, readdirSync } from 'node:fs'

/**
 * A test may read a source file instead of running it - but only for a listed reason.
 *
 * Asserting on source text is fragile in both directions, and the second direction is the
 * dangerous one. A rename or a reformat breaks a test that had found no bug; and a
 * behaviour change that keeps the matched text passes a test that was written to catch
 * exactly that change. Forty-two files in this suite do it
 * (`.docs/development/CODE_QUALITY_AUDIT_2026-08-23.md` finding 28), which is not a number
 * anyone chose - it is what happens when nothing says no.
 *
 * This says no. A test file that reads source is allowed only if it is listed below with
 * the reasons it reads it, and a reason has to be a channel that does not exist yet, never
 * "a behaviour test would be more work". S12.2 converted `railDensity`'s assertion about
 * the `data-rail-density` attribute into a behaviour test against a stubbed root element;
 * the rest are listed with what they read and why.
 *
 * The way off a list entry is nearly always a renderer harness under `test/renderer`,
 * which mounts the real component and can be asked what it rendered.
 */

type Reason =
  | 'stylesheet'
  | 'composition-root'
  | 'component-jsx'
  | 'cross-language-contract'
  | 'negative-invariant'
  | 'build-artifact'
  | 'registry'
  | 'fixture'

const REASONS: Readonly<Record<Reason, string>> = {
  //: The stylesheet is the artifact under test, not an implementation of something else.
  //: A contrast floor or a grid template is a fact about the CSS, and there is no runtime
  //: to ask instead - a renderer spec could read computed style, but only for the states
  //: it happens to mount, where reading the rule covers every state at once.
  stylesheet: 'style.css is itself the subject; no runtime holds the answer',
  //: `App.tsx` is the composition root: the assertion is about how two subsystems are
  //: wired to each other, and there is no seam between them to call. The fix is
  //: extracting the wiring into a controller module (as S4 did for five of them), not a
  //: renderer spec.
  'composition-root': 'asserts on App.tsx wiring, which has no unit seam yet',
  //: The assertion is about what a component renders or which handler it binds. This is
  //: the group that should shrink: a renderer harness can mount the component and be
  //: asked, and 88 files under `test/renderer` already do exactly that.
  'component-jsx': 'asserts on a component\'s JSX; owed a renderer harness',
  //: Two implementations in two languages have to agree on a list of literals, and the
  //: only thing they share is the source. An API contract test is the real answer where
  //: the value crosses the wire; several of these do not.
  'cross-language-contract': 'pins a Python and a TypeScript declaration to each other',
  //: The assertion is that something is *absent* - no second cron implementation in the
  //: browser, no calendar arithmetic. Absence over a whole file is not observable at
  //: runtime, so source text is the only channel and this reason does not expire.
  'negative-invariant': 'asserts something is absent from a whole file',
  //: About how the bundle is built - a dynamic import, a manual chunk - which is a fact
  //: about the module graph rather than about behaviour.
  'build-artifact': 'asserts on the shape of the build, not on behaviour',
  //: Reads a source file as data because that file *is* a list, and a second copy of the
  //: list kept in the test would be the same bug one level up.
  registry: 'reads a source file as the list it is',
  //: Reads a checked-in fixture next to the test, not source.
  fixture: 'reads its own fixture, not source',
}

/**
 * Every test file that reads a file off disk, and what it reads it for.
 *
 * Adding a line here is a decision, not a formality: say which of the reasons above
 * applies, and if none does, write the behaviour test instead.
 */
const SOURCE_TEXT_TESTS: Readonly<Record<string, readonly Reason[]>> = {
  'actionsDrawer.test.ts': ['component-jsx', 'stylesheet'],
  'attention.test.ts': ['component-jsx'],
  'budgetControl.test.ts': ['component-jsx'],
  'bundleSplit.test.ts': ['build-artifact', 'stylesheet'],
  'changeMap.test.ts': ['component-jsx', 'cross-language-contract'],
  'conversationToggle.test.ts': ['component-jsx', 'stylesheet'],
  //: `cross-language-contract` because the reason vocabulary is declared in
  //: `delivery_readiness.py` and worded in `deliveryReadiness.ts`, and a code with no
  //: sentence reaches a user as a raw identifier. `component-jsx` and
  //: `negative-invariant` for the two rules that are about what the Queue tab must
  //: *not* do: gate the Send button on the advisory, and let the composer estimate
  //: reach the verdict.
  'deliveryReadiness.test.ts': ['cross-language-contract', 'component-jsx', 'negative-invariant'],
  //: `composition-root` because the wiring under test is the one that broke: `/api/voice`
  //: is fetched in `App.tsx` and consumed through a prop, and the bug was that a single
  //: failed fetch was never retried - a fact about how the loader is called, with no seam
  //: between the two to unit-test. `negative-invariant` for the half that matters most,
  //: that the latch is absent and that no consumer bypasses the one loader; an absence
  //: over a whole file has no runtime to ask. `component-jsx` and `stylesheet` for the
  //: failure line the header now draws.
  'desktopShell.test.ts': ['composition-root', 'negative-invariant', 'component-jsx', 'stylesheet'],
  'drawerSegments.test.ts': ['composition-root', 'component-jsx', 'stylesheet'],
  'drawerTabs.test.ts': ['composition-root', 'component-jsx', 'stylesheet'],
  'findings.test.ts': ['composition-root', 'component-jsx'],
  'grants.test.ts': ['component-jsx', 'cross-language-contract'],
  //: `registry` because a feature doc is read as the list of what a help topic says - the
  //: whole point of generating that content is that no second copy of it exists to assert
  //: against. `build-artifact` for the freshness half: `helpContent.generated.ts` is a
  //: committed build output, and whether it still matches the docs is a fact about the
  //: build rather than about behaviour. `cross-language-contract` for the third read:
  //: every topic's "read more" link names a page slug that `site/tools/docs_content.py`
  //: declares, and the slug crosses no wire - the only thing the two sides share is that
  //: Python file, so a renamed page has to fail here or it fails as a 404 for a reader.
  'helpTopics.test.ts': ['registry', 'build-artifact', 'cross-language-contract'],
  'historyBrowser.test.ts': ['component-jsx', 'stylesheet'],
  'llmProvider.test.ts': ['component-jsx', 'cross-language-contract'],
  'modelRouting.test.ts': ['component-jsx'],
  'noteEditor.test.ts': ['component-jsx'],
  'projectResourceCreate.test.ts': ['component-jsx'],
  'queuePane.test.ts': ['composition-root', 'component-jsx'],
  'railClearance.test.ts': ['stylesheet'],
  'railDensity.test.ts': ['stylesheet', 'cross-language-contract', 'component-jsx'],
  'railGlassContrast.test.ts': ['stylesheet'],
  'railOverflow.test.ts': ['composition-root', 'component-jsx', 'stylesheet'],
  'railPadModel.test.ts': ['component-jsx'],
  //: The tokenizer exists in two languages - `keys.ts` records a chord and
  //: `keychords.py` validates and resolves it - and a table that disagrees with
  //: itself produces bindings that can never fire, which neither side can see alone.
  'keys.test.ts': ['cross-language-contract'],
  'scanTimeline.test.ts': ['composition-root', 'component-jsx', 'stylesheet'],
  'schedules.test.ts': ['component-jsx', 'negative-invariant'],
  'scrollbackRepaint.test.ts': ['component-jsx'],
  'sessionJoin.test.ts': ['composition-root'],
  'sessionRowClock.test.ts': ['composition-root', 'component-jsx'],
  'sessionStatus.test.ts': ['composition-root', 'component-jsx'],
  'settingReveal.test.ts': ['stylesheet'],
  'settingTargets.test.ts': ['component-jsx', 'cross-language-contract'],
  'settingsCoverage.test.ts': ['component-jsx', 'cross-language-contract'],
  'settingsSave.test.ts': ['component-jsx'],
  'smartTurnFeatures.test.ts': ['fixture'],
  'sourceText.test.ts': ['registry'],
  'styleInvariants.test.ts': ['stylesheet'],
  'tabContextMenu.test.ts': ['composition-root'],
  'terminalClipboard.test.ts': ['component-jsx', 'stylesheet'],
  'terminalRenderDiagnostics.test.ts': ['component-jsx'],
  //: `negative-invariant` and not `component-jsx`: the assertion is that a whole effect
  //: body contains no read of `session.backend`. That is absence over a region of a file,
  //: and a renderer harness could only prove it for the states it happens to mount - which
  //: is exactly how four behaviours stayed frozen at `shell` on a promoted pane after the
  //: unit tests for each of their pure helpers stayed green.
  'terminalPaneInputBackend.test.ts': ['negative-invariant'],
  'terminalViewport.test.ts': ['composition-root', 'component-jsx'],
  //: The one entry a renderer harness is *not* owed. The assertion compares prose in one
  //: component against chrome another component renders - the tour saying "Utilities"
  //: against `App.tsx`'s menu rows - and the two are never mounted together, because
  //: mounting `App` needs a daemon. `negative-invariant` is the third: the retired
  //: `Utilities` group has to be absent from both files, and absence over a whole file
  //: is not observable from a mount.
  'tourChrome.test.ts': ['component-jsx', 'composition-root', 'negative-invariant'],
  'testRegistry.test.ts': ['registry'],
  'transcriptAudio.test.ts': ['component-jsx', 'stylesheet'],
  'voiceDock.test.ts': ['composition-root', 'component-jsx', 'stylesheet'],
  'voiceGroups.test.ts': ['component-jsx'],
  'warmPanes.test.ts': ['composition-root', 'component-jsx', 'stylesheet'],
}

const here = new URL('.', import.meta.url)
const readsFiles = (name: string): boolean =>
  readFileSync(new URL(name, here), 'utf8').includes('readFileSync')
const testFiles = readdirSync(here).filter(name => name.endsWith('.test.ts'))

test('a test that reads source text is listed with the reasons it does', () => {
  const unlisted = testFiles.filter(name => readsFiles(name) && !(name in SOURCE_TEXT_TESTS))
  assert.deepEqual(
    unlisted,
    [],
    'these assert on source text without a listed reason - write the behaviour test, or '
      + `add the reason to sourceText.test.ts: ${unlisted.join(', ')}`,
  )
})

test('nothing stays listed after it stops reading source text', () => {
  // An entry left behind after a file was converted would make the list read as bigger
  // than the debt, and would silently re-permit the practice in that file.
  const stale = Object.keys(SOURCE_TEXT_TESTS).filter(
    name => !testFiles.includes(name) || !readsFiles(name),
  )
  assert.deepEqual(stale, [], `drop these entries; they no longer read source: ${stale.join(', ')}`)
})

test('every reason is one some file actually needs', () => {
  const used = new Set(Object.values(SOURCE_TEXT_TESTS).flat())
  const unused = Object.keys(REASONS).filter(reason => !used.has(reason as Reason))
  assert.deepEqual(unused, [], `no file claims these reasons any more: ${unused.join(', ')}`)
})
