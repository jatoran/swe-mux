# Terminal input incident runbook

Use this procedure when typed terminal input stops appearing and later catches up.
The purpose is to locate the delay boundary, not infer it from the visible symptom.

## Report shape

Record:

- mux session id
- local time and timezone
- approximate freeze duration
- device and browser
- whether the text later appeared
- whether only one pane froze
- whether the agent was idle or producing output

Session id and time are enough to begin.
The remaining fields resolve cases where the browser did not emit any diagnostic frame.

## Fetch the window

Convert the reported time to epoch seconds and query a window beginning 60 seconds before it:

```text
GET /api/events?session=<session-id>&since=<epoch-minus-60>&limit=500
```

Retain events through at least 60 seconds after the report.
Use `GET /api/sessions/<session-id>/state-log` for current ownership, connection, geometry, and rejection counters.
Use `GET /api/diagnostics/background` to rule in or out daemon event-loop stalls and failed background loops.

## Evidence

`terminal_input` means the daemon accepted input and wrote it to the PTY.
The event is sampled at most once every two seconds.
Traced physical input adds:

```text
input_seq
client_sent_at_ms
client_event_delay_ms
client_queue_delay_ms
input_source
ws_buffered_bytes
server_received_at_ms
```

`client_sent_at_ms` and `server_received_at_ms` use different device wall clocks on mobile.
Do not treat their subtraction as precise latency.
Use `input_ack_latency.sendToAckMs`, which stays entirely on the browser performance clock, for the transport round trip.

`terminal_input_diagnostic` phases:

| Phase | Boundary |
|---|---|
| `input_event_delay` | Native keyboard, IME, or paste event to WebSocket send |
| `input_main_thread_stall` | Browser main thread missed its 250 ms clock by at least 500 ms near physical input |
| `input_socket_backlog` | WebSocket already held at least 64 KiB when input was sent |
| `input_ack_latency` | WebSocket send to daemon receipt acknowledgement |
| `input_echo_latency` | Physical event through first eligible PTY output, xterm parse completion, and next animation frame |

`input_echo_latency` divides the total into `eventToSendMs`, `queueMs`, `sendToOutputMs`, `outputToParseMs`, and `parseToFrameMs`.
It also records the number and byte count of physical input events grouped into the recovery output batch.

Related events:

- `terminal_input_rejected`: ownership race; the client should claim and replay once.
- `terminal_input_owner`: ownership decision and device.
- `terminal_attached` / `terminal_detached`: connection transition.
- `terminal_client_repair`: surface, renderer, replay, or xterm write-pipeline recovery.
- `terminal_repaint_requested`: daemon or browser forced the CLI to restate its screen.
- `terminal_paste_trace`: one record per paste into a readable composer - payload shape (codepoint count, flagged non-ASCII codepoints, head/tail excerpt) plus the composer region, its row text, and the cursor before the paste and 600 ms after.
  Adjudicates the transient caret-lands-short-of-the-tail paste defect after the fact: hidden codepoints in the payload, or an after-cursor that disagrees with the after-rows' text end, each name a different culprit.
  It also carries the paste's provenance - `origin` (`native` for Ctrl+V, else `rail`/`insert`/`mobile`/`manual`/`attachment`), `captureSource`, `backend`, and `bracketedPasteMode`.
  Read those first when the report names a control: without `origin` every path looks identical here, and `bracketed` says what the bytes carried rather than whether the child was going to honour it.
  An `origin` of `null` means the bytes did not come through the pane's paste path.
- `terminal_paste_unclaimed`: a native text paste reached xterm's own textarea handler, so the pane's capture-phase claim was bypassed and the payload went out with whatever xterm's mode mirror decided.
  Content-free (length, mode, multiline flag, target tag).
  This is the one event that separates "Ctrl+V went out wrong" from "Ctrl+V behaved exactly like the button": the trace fires from `onData`, downstream of both, and cannot see which DOM listener produced the bytes.

## Adjudication

1. `input_event_delay` or `input_main_thread_stall` dominates: browser main-thread, UI, or IME dispatch delay.
2. Event delay is low and `input_ack_latency` is high: WebSocket, network, or daemon scheduling delay before receipt.
3. Acknowledgement is fast and `sendToOutputMs` is high: PTY supervisor, ConPTY, or CLI echo delay.
4. `outputToParseMs` is high: xterm parser backlog or failure.
5. `parseToFrameMs` is high: renderer, compositor, or browser scheduling delay after parsing.
6. `input_socket_backlog` is present: browser WebSocket transmission backlog contributed.
7. `terminal_input_rejected` is present: diagnose ownership before latency.
8. No traced input reached the daemon during the gap: the browser or OS did not dispatch it, the pane lacked the new bundle, or the socket disconnected.

Streaming agent output can satisfy the first-output correlation before the typed character's actual echo.
Use the acknowledgement and main-thread phases as authoritative boundaries in that case, and treat `input_echo_latency` as an upper-level pipeline signal rather than character-level proof.

## Bounds and privacy

- A latency phase persists only at 400 ms or longer, except the 500 ms main-thread threshold and 64 KiB socket-backlog threshold.
- Each diagnostic phase persists at most once per second per session.
- Pending browser correlations expire after 30 seconds and cap at 128.
- Server detail is allowlisted and clamped to 512 bytes.
- Diagnostic events contain no typed text, with one deliberate exception: `terminal_paste_trace` carries a bounded excerpt and codepoint summary of the pasted payload plus bounded composer row text, under its own 4096-byte clamp, because that content is the evidence it exists to keep.
