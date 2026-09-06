import assert from 'node:assert/strict'
import test from 'node:test'
import worker from '../index.js'
import { isVideoPath, parseRange, videoResponse, VIDEO_ROUTES } from '../media.mjs'

const request = (headers = {}, method = 'GET', path = '/img/example.mp4') =>
  new Request(`https://example.test${path}`, { method, headers })
const asset = (text = '0123456789', headers = {}) => new Response(text, {
  headers: { 'Content-Length': String(text.length), 'Content-Type': 'video/mp4', ETag: '"one"', ...headers },
})

test('the declared routing patterns match only flat public video assets', () => {
  for (const route of VIDEO_ROUTES) assert.equal(isVideoPath(route.replace('*', 'example')), true)
  for (const path of ['/version.json', '/img/example.webp', '/other/example.mp4', '/img/nested/example.mp4']) {
    assert.equal(isVideoPath(path), false)
  }
})

test('closed, open, suffix and oversized ranges select the right bytes', async () => {
  for (const [range, wanted, contentRange] of [
    ['bytes=2-5', '2345', 'bytes 2-5/10'],
    ['bytes=7-', '789', 'bytes 7-9/10'],
    ['bytes=-3', '789', 'bytes 7-9/10'],
    ['bytes=8-99999999999999999999', '89', 'bytes 8-9/10'],
    ['bytes=-99999999999999999999', '0123456789', 'bytes 0-9/10'],
  ]) {
    const response = videoResponse(request({ Range: range }), asset())
    assert.equal(response.status, 206)
    assert.equal(response.headers.get('Content-Range'), contentRange)
    assert.equal(response.headers.get('Content-Length'), String(wanted.length))
    assert.equal(await response.text(), wanted)
  }
})

test('unsatisfiable ranges return 416 and the representation length', async () => {
  for (const value of ['bytes=10-', 'bytes=5-2', 'bytes=-0', 'bytes=999999999999999999999-']) {
    const response = videoResponse(request({ Range: value }), asset())
    assert.equal(response.status, 416)
    assert.equal(response.headers.get('Content-Range'), 'bytes */10')
    assert.equal(await response.text(), '')
  }
  assert.deepEqual(parseRange('bytes=0-', 0), { unsatisfiable: true })
})

test('invalid units and multipart ranges fall back to a full representation', async () => {
  for (const value of ['bytes=', 'items=1-3', 'bytes=0-1,5-6', 'bytes=-']) {
    const response = videoResponse(request({ Range: value }), asset())
    assert.equal(response.status, 200)
    assert.equal(await response.text(), '0123456789')
  }
})

test('If-Range requires a matching strong validator', async () => {
  for (const [validator, status] of [['"one"', 206], ['"two"', 200], ['W/"one"', 200]]) {
    const response = videoResponse(request({ Range: 'bytes=1-2', 'If-Range': validator }), asset())
    assert.equal(response.status, status)
    await response.body.cancel()
  }
  const response = videoResponse(request({ Range: 'bytes=1-2', 'If-Range': 'Fri, 04 Sep 2026 00:00:00 GMT' }),
    asset('0123456789', { 'Last-Modified': 'Fri, 04 Sep 2026 00:00:00 GMT' }))
  assert.equal(response.status, 206)
  assert.equal(await response.text(), '12')
})

test('HEAD advertises byte ranges without a body; missing assets retain their status', () => {
  const head = videoResponse(request({ Range: 'bytes=1-2' }, 'HEAD'), new Response(null, { headers: { 'Content-Length': '10' } }))
  assert.equal(head.status, 200)
  assert.equal(head.headers.get('Accept-Ranges'), 'bytes')
  assert.equal(head.body, null)
  const missing = new Response('missing', { status: 404 })
  assert.equal(videoResponse(request(), missing), missing)
})

test('range streaming crosses chunks and cancels the unread tail', async () => {
  let cancelled = false
  const stream = new ReadableStream({
    start(controller) {
      for (const chunk of ['012', '345', '678', '9']) controller.enqueue(new TextEncoder().encode(chunk))
    },
    cancel() { cancelled = true },
  })
  const response = videoResponse(request({ Range: 'bytes=2-6' }), new Response(stream, { headers: { 'Content-Length': '10' } }))
  assert.equal(await response.text(), '23456')
  assert.equal(cancelled, true)
})

test('a short asset errors instead of claiming it delivered missing bytes', async () => {
  const response = videoResponse(request({ Range: 'bytes=1-8' }), asset('123', { 'Content-Length': '10' }))
  await assert.rejects(response.text(), /ended before/)
})

test('Workers uses a fixed-length stream for a partial response', async () => {
  let length
  globalThis.FixedLengthStream = class extends TransformStream {
    constructor(expected) { super(); length = expected }
  }
  try {
    const response = videoResponse(request({ Range: 'bytes=2-4' }), asset())
    assert.equal(await response.text(), '234')
    assert.equal(length, 3)
  } finally { delete globalThis.FixedLengthStream }
})

test('video ranges are served without counting or identifying visitors', async () => {
  let counted = 0
  let upstream
  const env = {
    ASSETS: { fetch(input) { upstream = input; return asset() } },
    METRICS: { writeDataPoint() { counted++ } },
  }
  const response = await worker.fetch(request({ Range: 'bytes=4-6' }), env)
  assert.equal(response.status, 206)
  assert.equal(await response.text(), '456')
  assert.equal(upstream.headers.get('Range'), null)
  assert.equal(upstream.headers.get('Accept-Encoding'), 'identity')
  assert.equal(counted, 0)
  await worker.fetch(request({}, 'GET', '/version.json'), env)
  assert.equal(counted, 1)
})
