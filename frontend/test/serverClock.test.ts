import assert from 'node:assert/strict'
import test from 'node:test'
import {
  CLOCK_OFFSET_NOISE_FLOOR_SECONDS, noteServerDate, resetServerClock,
  serverClockOffsetSeconds, serverNow,
} from '../src/serverClock.ts'

const httpDate = (ms: number) => new Date(ms).toUTCString()

test('a device in step with the daemon holds no offset', () => {
  resetServerClock()
  const sentAt = 1_700_000_000_000
  noteServerDate(httpDate(sentAt + 20), sentAt, sentAt + 40)
  assert.equal(serverClockOffsetSeconds(), 0)
})

test('a device behind the daemon is corrected forward', () => {
  resetServerClock()
  const sentAt = 1_700_000_000_000
  // The daemon is 30 s ahead of this browser's clock.
  noteServerDate(httpDate(sentAt + 30_000), sentAt, sentAt + 40)
  assert.ok(Math.abs(serverClockOffsetSeconds() - 30) < 1)
  assert.ok(Math.abs(serverNow(sentAt) - (sentAt + 30_000) / 1000) < 1)
})

test('a device ahead of the daemon is corrected backward', () => {
  resetServerClock()
  const sentAt = 1_700_000_000_000
  noteServerDate(httpDate(sentAt - 45_000), sentAt, sentAt + 40)
  assert.ok(Math.abs(serverClockOffsetSeconds() + 45) < 1)
})

test('round-trip latency is halved out rather than read as skew', () => {
  resetServerClock()
  const sentAt = 1_700_000_000_000
  // A 20 s round trip whose Date header was written at the midpoint. Attributing
  // the whole trip to the clock would invent a 10 s offset.
  noteServerDate(httpDate(sentAt + 10_000), sentAt, sentAt + 20_000)
  assert.equal(serverClockOffsetSeconds(), 0)
})

test('a held offset does not jitter on readings inside the noise floor', () => {
  resetServerClock()
  const sentAt = 1_700_000_000_000
  noteServerDate(httpDate(sentAt + 30_000), sentAt, sentAt + 40)
  const settled = serverClockOffsetSeconds()
  // A duration the user watches count up must not step backwards because one
  // poll landed a second differently.
  const nudge = (CLOCK_OFFSET_NOISE_FLOOR_SECONDS - 1) * 1000
  noteServerDate(httpDate(sentAt + 30_000 + nudge), sentAt, sentAt + 40)
  assert.equal(serverClockOffsetSeconds(), settled)
})

test('a missing or unparseable Date header leaves the offset alone', () => {
  resetServerClock()
  const sentAt = 1_700_000_000_000
  noteServerDate(httpDate(sentAt + 30_000), sentAt, sentAt + 40)
  const settled = serverClockOffsetSeconds()
  noteServerDate(null, sentAt, sentAt + 40)
  noteServerDate('not a date', sentAt, sentAt + 40)
  assert.equal(serverClockOffsetSeconds(), settled)
})
