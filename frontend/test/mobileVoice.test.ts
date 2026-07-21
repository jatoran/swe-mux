import assert from 'node:assert/strict'
import test from 'node:test'
import { mobileVoiceDestination } from '../src/mobileVoice.ts'

test('secure mobile voice keeps the current application route',()=>{
  assert.equal(
    mobileVoiceDestination('https://mux.tail.ts.net:8765/',{
      pathname:'/project/demo',
      search:'?session=agent-1',
      hash:'#terminal',
    }),
    'https://mux.tail.ts.net:8765/project/demo?session=agent-1#terminal',
  )
})
