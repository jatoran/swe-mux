# Terminal gateway

A browser terminal's bytes used to go browser -> daemon -> PTY supervisor and back, on the daemon's one event loop.
Every daemon stall - a GIL-heavy reconcile, a slow scan, a restart - therefore froze every terminal with it, and that was the incident `development/DAEMON_LOAD_ISOLATION.md` exists to end.
The terminal gateway is a process of its own that carries terminals instead: browser -> gateway -> supervisor and back, with the daemon told about each keystroke afterwards.
The daemon can stall, or restart, and typing keeps echoing.

Status (2026-10-18): the gateway process exists (item 28) and, with `terminal_gateway_enabled` on, the daemon finds or starts it and owns its lifecycle (item 29).
The daemon does not yet hand it sessions or send browsers to it; that is items 30 to 32, and the switch stays off until item 34.

## What runs where

- **The gateway** hosts the terminal cores (`terminal_core`) for the sessions the daemon hands it: the same stream, core and browser protocol the daemon runs in process, so a browser cannot tell which process answered.
  Keystrokes are written to the PTY, and their accounting done, before anything is sent anywhere.
- **The daemon** keeps everything a session is beyond its terminal: the record, the event log, status detection, delivery readiness, and what typing *means* - approval cancellation, the composer estimate, interrupt intent.
  It holds a mirror of each gateway-held terminal (`terminal_core.remote.RemoteTerminal`) on `Session.terminal`, where an in-process `TerminalCore` otherwise stands, so its readers do not know which process the core is in.
- **The supervisor** owns the PTYs, as before.
  The gateway is a client of it at the `terminal` scope.

The control protocol between daemon and gateway is in `design/interfaces.md` ("Terminal gateway control protocol").
Its shape is item 27's: ordered notices, keystroke writes applied by the daemon exactly once and in order, one unacknowledged summary per session so a stalled daemon costs the gateway a bounded amount, and conditional writes judged where the keystrokes arrive.

## The supervisor link

`terminal_gateway.SupervisorLink` connects with `hello {scope: "terminal"}` and asks for `output_gap` and `subscribe_since`.
At that scope the supervisor accepts only `subscribe / unsubscribe / write / resize / list / ping` and never sends a session's metadata, which carries its hook secret and MCP token - so the gateway can carry a terminal without being able to impersonate its agent.
`output_gap` means the supervisor never waits on the gateway: if the gateway falls behind, the supervisor drops that session's output for it and says where its copy stops (`gap`), and the gateway resubscribes from there.
`subscribe_since` makes that resubscribe, and the one after a lost connection, a delta: the supervisor answers with only what the gateway missed.
A supervisor too old to offer them still works, the way the daemon used to: whole-buffer resubscribes, and no promise that it will not wait.

The link reads `supervisor.json` from the data directory, reconnects with backoff after a loss, and resubscribes every session it holds from the position it reached.
A subscribe reply is applied inside the read loop, before the next frame, so output that follows the reply always lands after the bytes the reply carried.

**When a session is subscribed is a policy** (`--subscribe`), and the default was settled by measurement (item 28's record):

- `eager` (default): as soon as the daemon hands the session over.
  A browser's attach is then a read of this process's own memory: 2.7 ms at p50, with nothing waiting on the supervisor.
  The cost is the rings: 114 MiB for 36 sessions holding 2 MiB each, against 41 MiB with none - the same bound the daemon already pays for its own copy.
- `on_attach`: at a session's first browser, released, ring and all, a linger (`--subscribe-linger-seconds`, 600 by default) after its last.
  Memory follows the sessions being watched, and each session's first attach after a release costs a whole-buffer subscribe: 15.5 ms at p50 for a 2 MiB ring.

Parsing every session's output was immeasurable under either policy (36 sessions at 20 KiB/s each for ten seconds), so CPU did not decide it.

## The browser listener

`/pty/{sid}` is the terminal WebSocket, and `/health` reports readiness.
The gateway binds the daemon's host set (loopback, the tailnet address, the WSL bridge address) on its own port, which the daemon passes it, re-binds a listener asyncio closes after a failed accept (`ListenerGuard`), and runs under the same `resilient_event_loop` the daemon does.

- **Browser policy** is the daemon's, from the same module (`http_origin_policy.gateway_middleware`, item 26): 421 for a Host outside the allowlist, 403 for a page that is not this machine's.
  The page's port may be the daemon's, the gateway's own, or 443 on a `*.ts.net` name, but its host must be the host the gateway was reached on.
- **A session the gateway does not hold** - one never handed over, or an in-process or cold session the daemon keeps - is closed with 4010, and the browser goes to the daemon.
  A supervisor the link cannot reach is 4011: the gateway is the right place, just not right now.
- **`/health` answers 503 until the gateway has a supervisor and has heard from a daemon**, because before the first daemon it does not know which sessions it holds.
  The body names the supervisor link's state, whether a daemon is attached, the session count, the build and its protocol.
- **Traffic is metered** by the daemon's own `MeteredWebSocketResponse` into a `NetworkUsage` of the gateway's, sent to the daemon as a cumulative `metering` report every five seconds, so a report the daemon misses costs nothing.

## Lifecycle

- **One gateway per data directory, on every platform.**
  It takes an exclusive lock on `terminal-gateway.lock` for its whole life; the operating system drops the lock however the process dies, so a crashed gateway never locks out its successor.
  The supervisor's single-instance guard is a Windows named mutex with no POSIX twin, which is why this one is a file lock.
  A second gateway exits with code 3.
- **Discovery**: once both listeners are bound it writes `terminal-gateway.json` - pid, process creation time, browser port and hosts, control port, token, build, build id and protocol - atomically, and removes it on exit when the record is still its own.
- **The build id** is a digest over the compiled code of every `swe_mux` module the gateway loads (`GATEWAY_MODULES`), because `__version__` moves per release while the frozen app is rebuilt per commit.
  A test imports the gateway in a fresh interpreter and fails when the list and what it loads disagree, which is what keeps a version-skew check (item 29) honest.
- **It outlives the daemon on purpose.**
  With no daemon attached it keeps every session it was given, keeps carrying output and keystrokes, and holds the keystroke writes the daemon has not heard until a daemon - the same one or its successor - attaches.
- **It exits** when the daemon says it is quitting (`quit`), and when there has been no daemon attached and nothing live under the supervisor for `--idle-exit-seconds` (120 by default).
  A live session keeps it running however long the daemon is away, which is what a slow redeploy needs.
- **Its process**: `cwd` in the data directory, so it never locks an app tree against a rebuild; above-normal priority (`--priority`); the daemon's GIL switch interval and a 1 ms timer resolution; its own rotating `terminal-gateway.log` (10 MB x 3), and `crash.log` for a native crash, like every swe-mux process; `terminal-gateway-console.log` (`CONSOLE_LOG_NAME`) is the name its spawner gives the stderr redirect for what the interpreter says before logging starts.
- **How it is started**: by the daemon (below), frozen as `swe-mux.exe --gateway-child` and from source as `python -m swe_mux.terminal_gateway` (`terminal_gateway.gateway_command`), never as `-m swe_mux`, which would load the whole daemon.
  Exit codes: 0 a stop, 3 another gateway already runs, 4 a listener that would not bind, 1 a failure.

## Owned by the daemon

`gateway_host.GatewayHost` is the daemon's side (item 29), started beside the worker host once the PTY supervisor is connected.
Enabled with no supervisor it starts nothing and says why (`unavailable`), because there is nothing for a gateway to carry.

- **Found before it is started.** One supervised background loop reads `terminal-gateway.json`, keeps the record only while its process is alive (pid and creation time), and connects a `RemoteHub` to it with the record's token.
  Only when there is none does it spawn one, and the spawn and every discovery read run in threads.
- **Replaced when it runs other code.** A gateway whose `build_id`, `protocol`, `port` or `daemon_port` differs from this daemon's is asked to `quit` over a connection of its own and waited for, and terminated by its identity if it stays (or at once, when it speaks a protocol the request cannot be sent in).
  Quitting closes its browsers with 4011, so the restart a redeploy causes is orchestrated rather than a dropped socket.
  A gateway this daemon started itself is never replaced for its build: it is this daemon's code by construction, so a disagreement there is logged as the bug it would be rather than becoming a restart loop.
  A port change in config applies at the next daemon start exactly this way.
- **Started outside the daemon's reach.** Outside every Job object (`popen_outside_job`), in a process group of its own on Windows and a session of its own on POSIX, `cwd` in the data directory, the desktop control token withheld from its environment, and stderr to `terminal-gateway-console.log` (truncated past 1 MiB).
  The daemon's reaper never holds it, which is the difference from the worker.
- **Started again when it dies**, with backoff from 0.5 s to 30 s reset after a minute of good service.
  A spawn that loses the instance lock (exit 3) waits for the winner's record and uses it; one that cannot bind its port (exit 4) is reported as that.
- **Stopped by intent**, before the sessions in teardown: a quitting daemon sends `quit` and terminates a gateway that has not left within ten seconds; a restarting daemon only closes its connection, and the gateway carries every terminal until the successor finds it.
- **Named as swe-mux's own.** The recovery record carries its identity and desktop recovery never signals it (`daemon-resilience.md`); the process inspector reserves its pid the way it reserves the supervisor's, so it is never a session's process and its ports are never a Preview's.
- **Reported** as `terminal_transport` on `/api/health`, which is what the frontend will read to choose a transport (item 30), as `terminal_gateway` on `/api/diagnostics/background`, and as the `daemon.terminal_transport` row of `swemux doctor`, which warns when the gateway is enabled and not carrying terminals.

The build id this daemon computes and the one a freshly started gateway computes are asserted equal by a test, because the replacement rule compares them and a digest that depended on what else a process had loaded would replace every gateway on every daemon start.

## Key files

- `src/swe_mux/gateway_host.py` - the daemon's side: find or start, replace, restart, stop by intent, `terminal_transport`.
- `src/swe_mux/terminal_gateway.py` - the process: `SupervisorLink`, `TerminalGateway`, `InstanceLock`, discovery, `gateway_command`, `main`.
- `src/swe_mux/terminal_core/gateway.py` - `GatewayHub`, the cores it hosts and the daemon connection.
- `src/swe_mux/terminal_core/remote.py` - the daemon's mirror.
- `src/swe_mux/terminal_core/protocol.py` - the control protocol.
- `src/swe_mux/desktop.py` - the frozen `--gateway-child` dispatch.
- `tests/test_terminal_gateway.py` - against a real in-process `SupervisorServer` (`tests/support/supervisor_wire.py`), ending with the gateway as a process and the daemon's loop blocked under it.
- `tests/test_gateway_host.py` - the real gateway as a process, driven by the real host: start, adopt, replace, restart, quit.
