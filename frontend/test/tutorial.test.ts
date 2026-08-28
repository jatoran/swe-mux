import assert from 'node:assert/strict'
import test from 'node:test'
import {
  TUTORIAL_STORAGE_KEY,
  TUTORIAL_VERSION,
  completeTutorial,
  firstRunSurface,
  matchesTutorialAction,
  mobileTutorialChrome,
  placeTutorialCard,
  resetTutorial,
  shouldStartTutorial,
} from '../src/tutorial.ts'

/** A fresh in-memory `Storage` per test, so completion cannot leak between them. */
function storageWith(){
  const values=new Map<string,string>()
  return {
    values,
    getItem:(key:string)=>values.get(key)??null,
    setItem:(key:string,value:string)=>{values.set(key,value)},
    removeItem:(key:string)=>{values.delete(key)},
  }
}

test('the tutorial runs once per version, and a reset arms it again', () => {
  const storage=storageWith()
  assert.equal(shouldStartTutorial(storage),true)
  completeTutorial(storage)
  assert.equal(storage.values.get(TUTORIAL_STORAGE_KEY),TUTORIAL_VERSION)
  assert.equal(shouldStartTutorial(storage),false)
  resetTutorial(storage)
  assert.equal(shouldStartTutorial(storage),true)
})

test('the card is placed beside its anchor, and centred when there is none', () => {
  assert.deepEqual(
    placeTutorialCard({left:20,right:120,top:200,bottom:260,width:100,height:60},{width:1000,height:700},{width:380,height:250}),
    {left:142,top:105,side:'right'},
  )
  assert.deepEqual(
    placeTutorialCard({left:850,right:950,top:200,bottom:260,width:100,height:60},{width:1000,height:700},{width:380,height:250}),
    {left:448,top:105,side:'left'},
  )
  assert.deepEqual(
    placeTutorialCard(null,{width:1000,height:700},{width:380,height:250}),
    {left:310,top:225,side:'center'},
  )
})

test('the Notes step opens the side panel on a phone, not the navigation sidebar', () => {
  // The bug this pins: `resources` is anchored on the Notes control, which the *side
  // panel* carries (the desktop launcher rail while it is closed, its own tab strip once
  // open). It used to open the navigation sidebar, which has never carried that anchor -
  // so on a phone the step spotlighted nothing and, having an action, offered no Next.
  assert.equal(mobileTutorialChrome('resources'), 'side-panel')
  // The steps that really are behind the navigation sidebar keep it.
  assert.equal(mobileTutorialChrome('projects'), 'sidebar')
  assert.equal(mobileTutorialChrome('features'), 'sidebar')
  assert.equal(mobileTutorialChrome('feature-menu'), 'sidebar')
  assert.equal(mobileTutorialChrome('configurator'), 'sidebar')
  // And a step whose anchor is already on screen opens nothing.
  assert.equal(mobileTutorialChrome('welcome'), null)
  assert.equal(mobileTutorialChrome('run'), null)
  assert.equal(mobileTutorialChrome('workspace'), null)
})

test('exactly one first-run surface is ever chosen, and the harness panel leads', () => {
  const fresh = { tutorialArmed: true, configResolved: false, harnessSetupNeeded: false, settingsOpen: false }
  // Before the config fetch settles, whether the harness panel is needed is unknown - so
  // the answer is neither, not "the tour". Painting the tour here and covering it a
  // fetch later is exactly the stacked first frame this replaces.
  assert.equal(firstRunSurface(fresh), 'none')
  // It settles saying setup is needed: the panel leads alone.
  assert.equal(firstRunSurface({ ...fresh, configResolved: true, harnessSetupNeeded: true }), 'harness')
  // The panel is done; the tour follows.
  assert.equal(firstRunSurface({ ...fresh, configResolved: true }), 'tutorial')
  // A tour already completed on this device leaves the panel to run on its own.
  assert.equal(firstRunSurface({ ...fresh, tutorialArmed: false, configResolved: true, harnessSetupNeeded: true }), 'harness')
  assert.equal(firstRunSurface({ ...fresh, tutorialArmed: false, configResolved: true }), 'none')
  // Settings hides the panel (it is where its own "Configure in Settings…" leads), and
  // the tour must not slip in underneath it while setup is still outstanding.
  assert.equal(firstRunSurface({ ...fresh, configResolved: true, harnessSetupNeeded: true, settingsOpen: true }), 'none')
})

test('no combination of first-run state produces two surfaces', () => {
  // The defect was two independent conditions that happened to be true together. A total
  // function cannot have that shape, and this walks every input to say so.
  const bits = [false, true]
  let seen = 0
  for (const tutorialArmed of bits) for (const configResolved of bits) {
    for (const harnessSetupNeeded of bits) for (const settingsOpen of bits) {
      const surface = firstRunSurface({ tutorialArmed, configResolved, harnessSetupNeeded, settingsOpen })
      assert.ok(['harness', 'tutorial', 'none'].includes(surface))
      seen++
    }
  }
  assert.equal(seen, 16)
})

test('a tutorial step advances only on the action it asked for', () => {
  assert.equal(matchesTutorialAction({action:'session-launched',backend:'shell'},{action:'session-launched',backend:'shell'}),true)
  assert.equal(matchesTutorialAction({action:'session-launched',backend:'shell'},{action:'session-launched',backend:'codex'}),false)
  assert.equal(matchesTutorialAction({action:'tab-dropped',zone:'tabs'},{action:'tab-dropped',zone:'tabs'}),true)
  assert.equal(matchesTutorialAction({action:'tab-dropped',zone:'split'},{action:'tab-dropped',zone:'right'}),true)
  assert.equal(matchesTutorialAction({action:'tab-dropped',zone:'split'},{action:'tab-dropped',zone:'tabs'}),false)
})
