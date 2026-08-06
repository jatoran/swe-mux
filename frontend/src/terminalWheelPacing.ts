/**
 * Pacing for wheel-scroll mouse reports sent to an application that owns the mouse.
 *
 * When a TUI enables mouse tracking (Claude does), xterm turns each wheel notch into
 * several SGR scroll reports — one per line of scroll, ~7 for a 120px notch — and the
 * pane forwards them all. Each report makes an alternate-screen application repaint
 * (~2-20 KB of output), and a full-width Claude pane was measured consuming ~230
 * reports per second, while a free-spinning wheel or a trackpad flick can emit
 * thousands. Nothing else in the pipeline sheds load, so every excess report is banked
 * scrolling the terminal still performs after the finger has stopped: 4 to 12
 * *seconds* of runaway scroll after a fast flick, measured before this existed.
 *
 * The pacer closes the loop instead of guessing a rate. Reports drain in batches of at
 * most WHEEL_BATCH_MAX per animation frame — the frame cadence is itself the rate
 * floor — and a batch is released only when the previous one has been answered by PTY
 * output (`noteOutput`, the repaint arriving) or by WHEEL_ACK_TIMEOUT_MS of silence,
 * since an application scrolled to its buffer edge repaints nothing. That self-tunes
 * to the pane and the machine: at human scroll rates the CLI acks faster than the
 * wheel turns and the pacer is transparent; past the CLI's real capacity the queue
 * fills instead of the pty, and it is capped at WHEEL_QUEUE_MAX reports (~100 ms of
 * scrolling) — further notches are dropped rather than banked. A wheel is a velocity
 * control, not a distance ledger: during a flick nobody counts lines, but everybody
 * notices scrolling that continues after they stop. Reversing direction clears the
 * queue for the same reason.
 *
 * Ordering: callers must flush the queue before any non-wheel input (a click or
 * keystroke must not overtake the scrolls that preceded it), and discard it on a view
 * command like jump-to-latest, which supersedes the gesture outright.
 */

/** Reports released per acked animation-frame batch — roughly one notch's worth.
 * With 60 Hz frames this places the ceiling (~480/s) safely above any real CLI's
 * consumption, so the ack clock, not this constant, is what usually paces. */
export const WHEEL_BATCH_MAX = 8

/** How long to wait for a repaint before releasing the next batch anyway. An
 * application at its buffer edge consumes reports without producing output. */
export const WHEEL_ACK_TIMEOUT_MS = 100

/** Maximum banked reports (~3 notches, ~100 ms at measured consumption). Notches
 * beyond it shorten the gesture instead of banking runaway scroll. */
export const WHEEL_QUEUE_MAX = 24

// One or more plain vertical SGR wheel reports and nothing else. 64 is up, 65 down;
// modified wheels (shift/alt/ctrl add 4/8/16) are deliberately excluded — they are
// application chords, not flick traffic, and their ordering may matter.
const WHEEL_BURST = /^(?:\x1b\[<6[45];\d+;\d+M)+$/

export function isWheelReportBurst(data: string): boolean {
  // Cheap gate first: every report starts with ESC, and typing never does.
  return data.charCodeAt(0) === 0x1b && data.charAt(2) === '<' && WHEEL_BURST.test(data)
}

export interface WheelPacerTimers {
  now: () => number
  schedule: (fn: () => void) => number
  cancel: (id: number) => void
}

export interface WheelPacer {
  /** Queue a wheel burst (`data` must satisfy `isWheelReportBurst`). */
  push(data: string, broadcast: boolean): void
  /** The session produced output — the previous batch has been consumed. */
  noteOutput(): void
  /** Send everything still queued, now. Call before forwarding any non-wheel input. */
  flush(): void
  /** Drop everything still queued. Call when a view command supersedes the gesture. */
  discard(): void
  dispose(): void
}

export function createWheelPacer(
  send: (data: string, broadcast: boolean) => void,
  timers: WheelPacerTimers,
): WheelPacer {
  let report = ''
  let up = false
  let count = 0
  let broadcast = false
  let frame: number | null = null
  let lastSendAt = Number.NEGATIVE_INFINITY
  let ackSinceSend = true
  let disposed = false

  const cancelFrame = () => {
    if (frame !== null) { timers.cancel(frame); frame = null }
  }

  const tick = () => {
    frame = null
    if (count === 0) return
    const now = timers.now()
    if (ackSinceSend || now - lastSendAt >= WHEEL_ACK_TIMEOUT_MS) {
      const batch = Math.min(count, WHEEL_BATCH_MAX)
      count -= batch
      ackSinceSend = false
      lastSendAt = now
      send(report.repeat(batch), broadcast)
    }
    if (count > 0) frame = timers.schedule(tick)
  }

  return {
    push(data: string, wantsBroadcast: boolean) {
      if (disposed) return
      const nextUp = data.startsWith('\x1b[<64')
      if (count > 0 && nextUp !== up) count = 0
      up = nextUp
      report = data.slice(0, data.indexOf('M') + 1)
      broadcast = wantsBroadcast
      count = Math.min(count + (data.split('\x1b').length - 1), WHEEL_QUEUE_MAX)
      // The first burst of an idle gesture releases immediately — pacing must not
      // add latency to ordinary scrolling, only bound its banked depth.
      if (frame === null) tick()
    },
    noteOutput() {
      ackSinceSend = true
    },
    flush() {
      cancelFrame()
      if (count === 0) return
      const batch = report.repeat(count)
      count = 0
      lastSendAt = timers.now()
      ackSinceSend = false
      send(batch, broadcast)
    },
    discard() {
      cancelFrame()
      count = 0
    },
    dispose() {
      disposed = true
      cancelFrame()
      count = 0
    },
  }
}
