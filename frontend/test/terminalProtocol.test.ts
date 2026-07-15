import assert from 'node:assert/strict'
import test from 'node:test'
import { isTerminalProtocolResponse } from '../src/terminalProtocol.ts'

test('fresh replay forwards only terminal-generated protocol responses', () => {
  assert.equal(isTerminalProtocolResponse('\x1b[?1;2c'), true)
  assert.equal(isTerminalProtocolResponse('\x1b[1;1R'), true)
  assert.equal(isTerminalProtocolResponse('\x1b[0n'), true)
  assert.equal(isTerminalProtocolResponse('dir\r'), false)
  assert.equal(isTerminalProtocolResponse('\r'), false)
  assert.equal(isTerminalProtocolResponse('\x1b'), false)
})
