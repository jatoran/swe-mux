import assert from 'node:assert/strict'
import { downsample, parseMuxVoice } from '../src/conversation.ts'

assert.deepEqual(parseMuxVoice('Please run the focused tests. Mux send that.'),{
  command:'send',text:'Please run the focused tests.',
})
assert.deepEqual(parseMuxVoice('Mux, send'),{command:'send',text:''})
assert.deepEqual(parseMuxVoice('Okay mux submit message'),{command:'send',text:''})
assert.deepEqual(parseMuxVoice('Hey mux, cancel that'),{command:'cancel',text:''})
assert.deepEqual(parseMuxVoice('Keep the current implementation. Mucks submit it'),{
  command:'send',text:'Keep the current implementation.',
})
assert.deepEqual(parseMuxVoice('Mux cancel that'),{command:'cancel',text:''})
assert.deepEqual(parseMuxVoice('Mux undo that'),{command:'undo',text:''})
assert.deepEqual(parseMuxVoice('Mux stop speaking'),{command:'mute',text:''})
assert.deepEqual(parseMuxVoice('Mux read the reply again'),{command:'read',text:''})
assert.deepEqual(parseMuxVoice('Mux summary mode'),{command:'summary',text:''})
assert.deepEqual(parseMuxVoice('Mux use summaries'),{command:'summary',text:''})
assert.deepEqual(parseMuxVoice('Mux read verbatim'),{command:'verbatim',text:''})
assert.deepEqual(parseMuxVoice('Mux list commands'),{command:'help',text:''})
assert.deepEqual(parseMuxVoice('Mux stop listening'),{command:'stop',text:''})
assert.deepEqual(parseMuxVoice('Actually stop this run. Mux interrupt the agent'),{
  command:'interrupt',text:'Actually stop this run.',
})
assert.deepEqual(parseMuxVoice('Explain what a mux is'),{command:null,text:'Explain what a mux is'})
assert.deepEqual(parseMuxVoice('Computer send'),{command:null,text:'Computer send'})
assert.deepEqual(parseMuxVoice('My computer sends notifications'),{command:null,text:'My computer sends notifications'})

const source=new Float32Array([1,1,-1,-1,0,0,0,0])
assert.deepEqual([...downsample(source,8,4)],[1,-1,0,0])

console.log('conversation tests passed')
