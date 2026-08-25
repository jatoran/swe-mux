import assert from 'node:assert/strict'
import test from 'node:test'
import {
  TUTORIAL_STORAGE_KEY,
  TUTORIAL_VERSION,
  completeTutorial,
  matchesTutorialAction,
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

test('a tutorial step advances only on the action it asked for', () => {
  assert.equal(matchesTutorialAction({action:'session-launched',backend:'shell'},{action:'session-launched',backend:'shell'}),true)
  assert.equal(matchesTutorialAction({action:'session-launched',backend:'shell'},{action:'session-launched',backend:'codex'}),false)
  assert.equal(matchesTutorialAction({action:'tab-dropped',zone:'tabs'},{action:'tab-dropped',zone:'tabs'}),true)
  assert.equal(matchesTutorialAction({action:'tab-dropped',zone:'split'},{action:'tab-dropped',zone:'right'}),true)
  assert.equal(matchesTutorialAction({action:'tab-dropped',zone:'split'},{action:'tab-dropped',zone:'tabs'}),false)
})
