import assert from 'node:assert/strict'
import test from 'node:test'
import { VOICE_COMMS_PROTOCOL, voiceCommsMessage } from '../src/voiceComms.ts'

test('a plain voice message is trimmed and tagged', () => {
  assert.equal(voiceCommsMessage('  Run the focused tests.  ',false),'[voice] Run the focused tests.')
})

test('the first message of a conversation carries the spoken-reply protocol', () => {
  const first=voiceCommsMessage('Explain the failure.',true)
  assert.ok(first.startsWith(`${VOICE_COMMS_PROTOCOL}\n\n[voice] `))
  assert.ok(first.includes('one or two natural spoken sentences'))
})
