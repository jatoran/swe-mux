import assert from 'node:assert/strict'
import { VOICE_COMMS_PROTOCOL, voiceCommsMessage } from '../src/voiceComms.ts'

assert.equal(voiceCommsMessage('  Run the focused tests.  ',false),'[voice] Run the focused tests.')
const first=voiceCommsMessage('Explain the failure.',true)
assert.ok(first.startsWith(`${VOICE_COMMS_PROTOCOL}\n\n[voice] `))
assert.ok(first.includes('one or two natural spoken sentences'))

console.log('voice comms tests passed')
