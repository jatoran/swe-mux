import assert from 'node:assert/strict'
import test from 'node:test'
import { isComposingKey } from '../src/inputComposition.ts'
import { terminalKeyDecision } from '../src/terminalKeys.ts'

for (const marker of [{isComposing:true}, {keyCode:229}, {key:'Process'}]) {
  test('IME selection keeps terminal commands untouched: '+JSON.stringify(marker), () => {
    for (const key of ['Enter','Escape',' ','1','c','v','ArrowDown']) {
      const event = {type:'keydown',key,ctrlKey:true,shiftKey:true,altKey:false,metaKey:false,...marker}
      assert.equal(isComposingKey(event),true)
      assert.deepEqual(terminalKeyDecision(event,true,true,'claude'),{kind:'pass'})
    }
  })
}
test('ordinary shortcuts remain available after composition', () => {
  const event = {type:'keydown',key:'Enter',ctrlKey:true,shiftKey:false,altKey:false,metaKey:false,isComposing:false,keyCode:13}
  assert.equal(isComposingKey(event),false)
  assert.equal(terminalKeyDecision(event,false,false,'claude').kind,'sendInput')
})
