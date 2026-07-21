import assert from 'node:assert/strict'
import {
  TUTORIAL_STORAGE_KEY,
  TUTORIAL_VERSION,
  completeTutorial,
  matchesTutorialAction,
  placeTutorialCard,
  resetTutorial,
  shouldStartTutorial,
} from '../src/tutorial.ts'

const values=new Map<string,string>()
const storage={
  getItem:(key:string)=>values.get(key)??null,
  setItem:(key:string,value:string)=>{values.set(key,value)},
  removeItem:(key:string)=>{values.delete(key)},
}

assert.equal(shouldStartTutorial(storage),true)
completeTutorial(storage)
assert.equal(values.get(TUTORIAL_STORAGE_KEY),TUTORIAL_VERSION)
assert.equal(shouldStartTutorial(storage),false)
resetTutorial(storage)
assert.equal(shouldStartTutorial(storage),true)

assert.deepEqual(
  placeTutorialCard({left:20,right:120,top:200,bottom:260,width:100,height:60},{width:1000,height:700},{width:380,height:250}),
  {left:142,top:105,side:'right'},
)

assert.equal(matchesTutorialAction({action:'session-launched',backend:'shell'},{action:'session-launched',backend:'shell'}),true)
assert.equal(matchesTutorialAction({action:'session-launched',backend:'shell'},{action:'session-launched',backend:'codex'}),false)
assert.equal(matchesTutorialAction({action:'tab-dropped',zone:'tabs'},{action:'tab-dropped',zone:'tabs'}),true)
assert.equal(matchesTutorialAction({action:'tab-dropped',zone:'split'},{action:'tab-dropped',zone:'right'}),true)
assert.equal(matchesTutorialAction({action:'tab-dropped',zone:'split'},{action:'tab-dropped',zone:'tabs'}),false)
assert.deepEqual(
  placeTutorialCard({left:850,right:950,top:200,bottom:260,width:100,height:60},{width:1000,height:700},{width:380,height:250}),
  {left:448,top:105,side:'left'},
)
assert.deepEqual(
  placeTutorialCard(null,{width:1000,height:700},{width:380,height:250}),
  {left:310,top:225,side:'center'},
)

console.log('tutorial tests passed')
