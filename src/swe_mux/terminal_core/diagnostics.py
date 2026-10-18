"""Which client-side terminal diagnostics are recorded, and how often."""

from __future__ import annotations

# Client repair events persisted to the durable event log. An allowlist rather than a
# free phase field: the browser page is same-user but still untrusted input, and the
# log must stay enumerable for the "which repairs still fire in production" question.
CLIENT_REPAIR_PHASES = frozenset(
    {
        "write_pipeline_dead",
        "write_pipeline_backlog",
        "surface_drift_repair",
        "viewport_fit_drift_repair",
        "viewport_fit_resumed",
        "surface_repair_resumed",
        "scrollback_repaint_requested",
        "webgl_render_error",
    }
)


CLIENT_INPUT_DIAGNOSTIC_PHASES = frozenset(
    {
        "input_ack_latency",
        "input_echo_latency",
        "input_event_delay",
        "input_main_thread_stall",
        "input_socket_backlog",
        # A deliberate mobile vertical drag that moved nothing. The symptom ("swiping does
        # nothing") names no layer, and the drag's four destinations — peek pan, xterm
        # scrollback, a forwarded wheel, or `disabled` — are indistinguishable from outside
        # when they fail. The report carries which one it took and what the pane believed.
        "mobile_drag_inert",
        # A mobile gesture that was not a tap left the soft keyboard in a different state
        # than it found it. The pane holds that as an invariant in both directions and
        # reports its own violations, because the symptom is unfalsifiable from outside:
        # "it sometimes opens the keyboard" names neither a layer nor a gesture. The report
        # carries the direction, whether it was a selection, and how long the hold was —
        # duration being the discriminator, since the compat-mouse suppression window that
        # used to leak was really a cap on how long a deliberate hold was allowed to be.
        "mobile_gesture_keyboard_changed",
        # A native paste that reached xterm's own textarea handler because the pane's
        # capture listener did not claim it. Every terminal paste is supposed to converge
        # on the pane's paste path, which is what repairs bracketing and lifts a leading
        # newline out of a Codex paste; a paste that bypassed it went out with whatever
        # xterm decided, which is the shape of every "Ctrl+V submits my paste line by
        # line" report. The trace fires from `onData` and cannot see which DOM listener
        # produced the bytes, so this is the only thing that can distinguish the two.
        # Content-free - a length and two booleans - hence its place among these phases.
        "terminal_paste_unclaimed",
    }
)


CLIENT_DIAGNOSTIC_MIN_INTERVAL_SECONDS = 1.0


CLIENT_DIAGNOSTIC_DETAIL_LIMIT = 512


# The paste trace (frontend pasteTrace.ts) deliberately carries bounded pasted-content
# evidence — a head/tail excerpt, flagged codepoints, and two composer snapshots — because
# the payload's invisible characters ARE the diagnosis it exists for. It therefore
# persists as its own event type instead of joining the content-free
# `terminal_input_diagnostic` phases, and its clamp is sized for the two snapshots.
CLIENT_PASTE_TRACE_PHASE = "terminal_paste_trace"


CLIENT_PASTE_TRACE_DETAIL_LIMIT = 4096
