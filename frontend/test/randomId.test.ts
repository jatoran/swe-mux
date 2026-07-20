import assert from 'node:assert/strict'
import test from 'node:test'
import { browserUuid } from '../src/layout.ts'

test('browser UUID works when randomUUID is unavailable on direct HTTP', () => {
  let next = 0
  const insecureContextCrypto = {
    getRandomValues<T extends ArrayBufferView | null>(array: T): T {
      if (array instanceof Uint8Array) {
        for (let index = 0; index < array.length; index += 1) array[index] = next++
      }
      return array
    },
  }
  const id = browserUuid(insecureContextCrypto)
  assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
})
