import assert from 'node:assert/strict'
import test from 'node:test'
import {
  isTerminalProtocolResponse,
  shouldSuppressTerminalProtocolResponse,
} from '../src/terminalProtocol.ts'

test('fresh replay forwards only terminal-generated protocol responses', () => {
  assert.equal(isTerminalProtocolResponse('\x1b[?1;2c'), true)
  assert.equal(isTerminalProtocolResponse('\x1b[1;1R'), true)
  assert.equal(isTerminalProtocolResponse('\x1b[0n'), true)
  assert.equal(isTerminalProtocolResponse('\x1b]10;rgb:c0c0/caca/f5f5\x1b\\'), true)
  assert.equal(isTerminalProtocolResponse('\x1b]11;rgb:1a1a/1b1b/2626\x1b\\'), true)
  assert.equal(
    isTerminalProtocolResponse(
      '\x1b]10;rgb:c0c0/caca/f5f5\x1b\\\x1b]11;rgb:1a1a/1b1b/2626\x1b\\',
    ),
    true,
  )
  assert.equal(isTerminalProtocolResponse('dir\r'), false)
  assert.equal(isTerminalProtocolResponse('\r'), false)
  assert.equal(isTerminalProtocolResponse('\x1b'), false)
  assert.equal(isTerminalProtocolResponse('typed\x1b]10;rgb:c0c0/caca/f5f5\x1b\\'), false)
})

test('DECRPM mode reports are protocol responses, so oh-my-pi startup probes get answers', () => {
  // omp sends DECRQM for modes 2026/2048/2031/1010/1011 at spawn; xterm answers
  // with `CSI ? Ps ; Pm $ y` (and `CSI Ps ; Pm $ y` for the ANSI variant). Those
  // answers must ride the protocol-response path — recognized here — or the
  // first-attach probe window drops them as user input.
  assert.equal(isTerminalProtocolResponse('\x1b[?2026;2$y'), true)
  assert.equal(isTerminalProtocolResponse('\x1b[?2048;0$y'), true)
  assert.equal(isTerminalProtocolResponse('\x1b[?1010;0$y\x1b[?1011;0$y'), true)
  assert.equal(isTerminalProtocolResponse('\x1b[4;2$y'), true)
  assert.equal(isTerminalProtocolResponse('typed\x1b[?2026;2$y'), false)
})

test('Codex drops bounded startup color replies that can arrive as composer input', () => {
  const foreground = '\x1b]10;rgb:c0c0/caca/f5f5\x1b\\'
  const background = '\x1b]11;rgb:1a1a/1b1b/2626\x1b\\'
  assert.equal(shouldSuppressTerminalProtocolResponse(foreground, 'codex'), true)
  assert.equal(shouldSuppressTerminalProtocolResponse(foreground + background, 'codex'), true)
  assert.equal(shouldSuppressTerminalProtocolResponse(foreground, 'claude'), false)
  assert.equal(shouldSuppressTerminalProtocolResponse(foreground, 'shell'), false)
  assert.equal(shouldSuppressTerminalProtocolResponse('\x1b[?1;2c', 'codex'), false)
  assert.equal(
    shouldSuppressTerminalProtocolResponse(
      'typed\x1b]10;rgb:c0c0/caca/f5f5\x1b\\',
      'codex',
    ),
    false,
  )
})
