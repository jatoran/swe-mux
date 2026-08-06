import assert from 'node:assert/strict'
import test from 'node:test'
import {
  WHEEL_ACK_TIMEOUT_MS,
  WHEEL_BATCH_MAX,
  WHEEL_QUEUE_MAX,
  createWheelPacer,
  isWheelReportBurst,
} from '../src/terminalWheelPacing.ts'

const UP = '\x1b[<64;42;18M'
const DOWN = '\x1b[<65;42;18M'
const FRAME_MS = 16

function harness() {
  let clock = 0
  const sent: { data: string; broadcast: boolean; at: number }[] = []
  let nextId = 1
  const frames = new Map<number, () => void>()
  const pacer = createWheelPacer(
    (data, broadcast) => sent.push({ data, broadcast, at: clock }),
    {
      now: () => clock,
      schedule: fn => { const id = nextId++; frames.set(id, fn); return id },
      cancel: id => { frames.delete(id) },
    },
  )
  // One animation frame: advance the clock, then run whatever was scheduled.
  const frame = (ms = FRAME_MS) => {
    clock += ms
    const due = [...frames.values()]
    frames.clear()
    for (const fn of due) fn()
  }
  const reports = () => sent.reduce((total, s) => total + s.data.split('\x1b').length - 1, 0)
  return { pacer, sent, frame, reports, get scheduled() { return frames.size } }
}

test('wheel bursts are recognized and typing is not', () => {
  assert.equal(isWheelReportBurst(UP), true)
  assert.equal(isWheelReportBurst(DOWN.repeat(7)), true)
  assert.equal(isWheelReportBurst('a'), false)
  assert.equal(isWheelReportBurst('\r'), false)
  // A click report must never be paced: its ordering against keystrokes matters.
  assert.equal(isWheelReportBurst('\x1b[<0;10;10M'), false)
  // Modified wheels are application chords, not flick traffic.
  assert.equal(isWheelReportBurst('\x1b[<68;10;10M'), false)
  // A wheel report followed by anything else is not a pure burst.
  assert.equal(isWheelReportBurst(UP + 'x'), false)
})

test('an idle notch sends immediately and whole', () => {
  const { pacer, sent, reports } = harness()
  pacer.push(UP.repeat(7), false)
  assert.equal(sent.length, 1)
  assert.equal(reports(), 7, 'a single notch must not be split or shortened')
})

test('human-rate scrolling passes through at full distance', () => {
  // ~30 notches/s of 7 reports each, acked promptly: the pacer must be transparent.
  const { pacer, frame, reports } = harness()
  let pushed = 0
  for (let notch = 0; notch < 30; notch += 1) {
    pacer.push(UP.repeat(7), false)
    pushed += 7
    pacer.noteOutput()
    frame()
    pacer.noteOutput()
    frame()
  }
  for (let i = 0; i < 20; i += 1) { pacer.noteOutput(); frame() }
  assert.equal(reports(), pushed, 'no report may be shed at sustainable rates')
})

test('the next batch waits for the repaint ack', () => {
  const { pacer, sent, frame } = harness()
  for (let i = 0; i < 3; i += 1) pacer.push(UP.repeat(7), false)
  assert.equal(sent.length, 1, 'immediate first batch')
  frame()
  frame()
  assert.equal(sent.length, 1, 'unacked — nothing further released')
  pacer.noteOutput()
  frame()
  assert.equal(sent.length, 2, 'ack releases the next batch')
})

test('without an ack the timeout releases the next batch, not forever', () => {
  const { pacer, sent, frame } = harness()
  for (let i = 0; i < 3; i += 1) pacer.push(UP.repeat(7), false)
  // No noteOutput: the application is at its buffer edge and repaints nothing.
  let waited = 0
  while (sent.length === 1 && waited < WHEEL_ACK_TIMEOUT_MS * 3) { frame(); waited += FRAME_MS }
  assert.equal(sent.length, 2, 'timeout must release the batch')
  assert.ok(sent[1].at - sent[0].at >= WHEEL_ACK_TIMEOUT_MS, 'released only after the timeout')
})

test('a violent flick is shed at the queue cap instead of banked', () => {
  const { pacer, frame, reports } = harness()
  // 400 notches with no acks and no frames between them: a ~1s free-spin flick.
  for (let notch = 0; notch < 400; notch += 1) pacer.push(UP.repeat(7), false)
  for (let i = 0; i < 100; i += 1) { pacer.noteOutput(); frame() }
  assert.ok(
    reports() <= WHEEL_QUEUE_MAX + WHEEL_BATCH_MAX,
    `2800 reports must shed to the cap plus the immediate batch, sent ${reports()}`,
  )
})

test('reversing direction drops the stale queue', () => {
  const { pacer, sent, frame } = harness()
  for (let notch = 0; notch < 3; notch += 1) pacer.push(UP.repeat(7), false)
  pacer.push(DOWN.repeat(7), false)
  for (let i = 0; i < 40; i += 1) { pacer.noteOutput(); frame() }
  const stream = sent.map(s => s.data).join('')
  const upAfterDown = stream.indexOf(UP, stream.indexOf(DOWN))
  assert.equal(upAfterDown, -1, 'no queued up-report may land after the user reversed')
})

test('flush sends the whole queue so later input cannot overtake it', () => {
  const { pacer, reports, scheduled } = harness()
  for (let notch = 0; notch < 3; notch += 1) pacer.push(UP.repeat(7), false)
  assert.equal(reports(), 7, 'only the immediate one-notch batch so far')
  pacer.flush()
  assert.equal(reports(), 21)
  assert.equal(scheduled, 0)
})

test('discard drops the queue outright for view commands', () => {
  const { pacer, frame, reports, scheduled } = harness()
  for (let notch = 0; notch < 3; notch += 1) pacer.push(UP.repeat(7), false)
  const before = reports()
  pacer.discard()
  for (let i = 0; i < 10; i += 1) { pacer.noteOutput(); frame() }
  assert.equal(reports(), before, 'nothing queued may land after a discard')
  assert.equal(scheduled, 0)
})

test('dispose cancels the pending frame and refuses further pushes', () => {
  const { pacer, frame, reports, scheduled } = harness()
  for (let notch = 0; notch < 3; notch += 1) pacer.push(UP.repeat(7), false)
  pacer.dispose()
  assert.equal(scheduled, 0)
  const before = reports()
  pacer.push(UP.repeat(7), false)
  frame()
  assert.equal(reports(), before)
})
