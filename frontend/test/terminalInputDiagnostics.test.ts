import assert from 'node:assert/strict'
import test from 'node:test'
import {
  TerminalInputLatencyTracker,
  inputEventPerformanceTime,
} from '../src/terminalInputDiagnostics.ts'

test('browser event timestamps normalize across performance and epoch clocks', () => {
  assert.equal(inputEventPerformanceTime(900, 1_000, 50_000), 900)
  assert.equal(inputEventPerformanceTime(49_900, 1_000, 50_000), 1_000)
  assert.equal(inputEventPerformanceTime(1_800_000_000_000, 1_000, 1_800_000_000_100), 900)
  assert.equal(inputEventPerformanceTime(Number.NaN, 1_000, 50_000), 1_000)
})

test('input probes expose bounded timing metadata without input content', () => {
  const tracker = new TerminalInputLatencyTracker()
  const { probe, frame } = tracker.begin(
    { eventAt: 1_000, onDataAt: 1_100, source: 'beforeinput' },
    3,
    1_250,
    1_800_000_000_000,
    64,
  )

  assert.deepEqual(frame, {
    input_seq: probe.seq,
    client_sent_at_ms: 1_800_000_000_000,
    client_event_delay_ms: 250,
    client_queue_delay_ms: 150,
    input_source: 'beforeinput',
    ws_buffered_bytes: 64,
  })
  assert.equal('data' in frame, false)
})

test('slow daemon acknowledgement is separated from browser event delay', () => {
  const tracker = new TerminalInputLatencyTracker()
  const { probe } = tracker.begin(
    { eventAt: 1_000, onDataAt: 1_020, source: 'keydown' },
    1,
    1_030,
    10_000,
    0,
  )
  assert.equal(tracker.acknowledge(probe.seq, 1_200, 10_100), null)

  const second = tracker.begin(
    { eventAt: 2_000, onDataAt: 2_010, source: 'keydown' },
    1,
    2_020,
    11_000,
    32,
  ).probe
  assert.deepEqual(tracker.acknowledge(second.seq, 2_520, 11_200), {
    inputSeq: second.seq,
    source: 'keydown',
    eventToSendMs: 20,
    queueMs: 10,
    sendToAckMs: 500,
    serverReceivedAtMs: 11_200,
    bufferedBefore: 32,
  })
})

test('echo recovery reports one aggregate with transport parse and frame segments', () => {
  const tracker = new TerminalInputLatencyTracker()
  const first = tracker.begin(
    { eventAt: 1_000, onDataAt: 1_050, source: 'input' },
    2,
    1_100,
    10_000,
    0,
  ).probe
  const last = tracker.begin(
    { eventAt: 1_200, onDataAt: 1_210, source: 'input' },
    3,
    1_220,
    10_120,
    8,
  ).probe
  const batch = tracker.takeEchoBatch(1_900)
  assert.ok(batch)

  assert.deepEqual(tracker.completeEchoBatch(batch, 2_000, 2_200), {
    firstInputSeq: first.seq,
    lastInputSeq: last.seq,
    source: 'input',
    inputs: 2,
    bytes: 5,
    eventToSendMs: 100,
    queueMs: 50,
    sendToOutputMs: 800,
    outputToParseMs: 100,
    parseToFrameMs: 200,
    totalMs: 1_200,
    bufferedBefore: 0,
  })
  assert.equal(tracker.takeEchoBatch(2_300), null)
})

test('rejected input is removed from acknowledgement and echo correlation', () => {
  const tracker = new TerminalInputLatencyTracker()
  const { probe } = tracker.begin(
    { eventAt: 1_000, onDataAt: 1_000, source: 'paste' },
    12,
    1_000,
    10_000,
    0,
  )
  tracker.reject(probe.seq)
  assert.equal(tracker.acknowledge(probe.seq, 2_000, 11_000), null)
  assert.equal(tracker.takeEchoBatch(2_000), null)
})
