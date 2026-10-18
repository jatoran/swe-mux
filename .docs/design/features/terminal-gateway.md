# Terminal gateway

A browser terminal's bytes used to go browser -> daemon -> PTY supervisor and back, on the daemon's one event loop.
Every daemon stall - a GIL-heavy reconcile, a slow scan, a restart - therefore froze every terminal with it, and that was the incident `development/archive/DAEMON_LOAD_ISOLATION.md` exists to end.
The terminal gateway is a process of its own that carries terminals instead: browser -> gateway -> supervisor and back, with the daemon told about each keystroke afterwards.
The daemon can stall, or restart, and typing keeps echoing.

Status (2026-10-18): on by default (item 34), with `terminal_gateway_enabled` as the switch that puts every terminal back on the daemon.
The daemon finds or starts the gateway and owns its lifecycle (items 28 and 29), browsers open terminals on it whenever the daemon reports it (item 30, reached through Tailscale Serve from an `https:` page, item 31), and every supervised session's terminal is handed to it and taken back when it goes (item 32).
A redeploy, an update and a daemon restart each treat it as below (item 33).

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

`/pty/{sid}` is the terminal WebSocket (also served as `/gateway/pty/{sid}`, for a reverse proxy that keeps its mount prefix), and `/health` reports readiness.
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

## Browsers

The frontend chooses per connection (`frontend/src/terminalTransport.ts`, item 30), from the `terminal_transport` the daemon reports on `/api/health`, which the liveness probe already polls every four seconds.
The last report is remembered for ten minutes in `localStorage`, because a reconnect during a daemon restart, and a page reloaded from the service worker while the daemon is down, are exactly when the gateway matters and the daemon cannot be asked.

- **The daemon is same-origin `/pty/<id>` on every access path**, and stays the fallback for everything.
- **An `http:` page reaches the gateway on its own port**, `ws://<host>:<port>/pty/<id>`: loopback, the tailnet address, the WSL bridge.
- **An `https:` page goes through the proxy in front of it**, `wss://<host>/gateway/pty/<id>`, because a page served over TLS cannot open `ws:` to another port.
  Tailscale Serve mounts the gateway at `/gateway` beside the daemon's root route, and the gateway answers under that prefix as well as without it, so it works whether the proxy strips the prefix or not; Serve does strip it (measured on 1.102.2), so a Serve request arrives as `/pty/<id>`.
  The plan had the mount at `/pty` itself, which would have taken the daemon's `/pty` away from an `https:` page - the very route a down gateway has to fall back to.
  The daemon keeps the mount equal to the gateway's port and removes it while the gateway is disabled, touching it only while the root route is its own (`tailscale.reconcile_gateway_serve`, `design/features/remote-access.md`).
  The mount follows config rather than the gateway's state: a gateway that is down leaves a mount answering 502, which the page treats as a gateway that never opened and falls back from, while a mount that came and went with each restart would churn the operator's Serve config.
- **The direct tailnet address needs the gateway's port admitted** as well as the daemon's, so the Windows firewall inspection judges both and its repair writes one rule with both ports, and the POSIX probe tries both (`design/features/remote-access.md`).
- **The Vite dev server proxies `/gateway` the same way**, so development is the `https:` shape over plain HTTP.

A close says where to go next, and the pane reconnects at once rather than after its backoff: 4010 from the gateway (the session is the daemon's - in-process or cold) holds that one session on the daemon for a minute; 4011 (the gateway is going away) holds the gateway off for two seconds; a gateway that never opened is backed off from, five seconds doubling to a minute, while the daemon serves; and 4012 from the daemon (the session is the gateway's) sends the pane back at once, even ahead of the next health report.
A session bounced more than four times in ten seconds falls back to the ordinary reconnect backoff, so two processes that disagree cannot make a busy loop of it.

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
- **Reported** as `terminal_transport` on `/api/health`, which is what the frontend reads to choose a transport (item 30), as `terminal_gateway` on `/api/diagnostics/background`, and as the `daemon.terminal_transport` row of `swemux doctor`, which warns when the gateway is enabled and not carrying terminals.
  It says `gateway` while the gateway carries terminals: whenever the daemon is connected to it, and through a lost control connection to one still running that holds them, because that gateway is still serving their browsers.
- **Metered into the daemon's counters.** The gateway's cumulative `metering` reports are folded into `/api/diagnostics/network` (`NetworkUsage.merge_remote`), counted in the totals and per peer, with the gateway's channels named `pty (gateway)`.
- **Covered by the frontend overlay's pin.** The gateway's browser routes (`terminal_gateway.BROWSER_ROUTES`, which `build_app` registers from) and its protocol version join the API digest an overlay is checked against, so an overlay built against one gateway is refused beside another.

The build id this daemon computes and the one a freshly started gateway computes are asserted equal by a test, because the replacement rule compares them and a digest that depended on what else a process had loaded would replace every gateway on every daemon start.

## Restarts, redeploys and updates

The gateway runs the app's own image (`swe-mux.exe --gateway-child`), and that one fact decides what each way of reloading swe-mux does to it.

- **A daemon restart leaves it alone.** "Reload daemon (keep sessions)", `swemux reload-daemon` and `POST /api/daemon/restart` detach the daemon; the gateway goes on carrying every terminal through the gap, and the successor adopts it (the same build, so it is not replaced).
  Typing keeps echoing the whole time, which the `live_daemon` tier proves with one browser socket held open across a detach, a stretch with no daemon at all, and the successor's start.
  The redeploy script's `--skip-build` bounce is the same: it renames nothing, so it leaves the gateway running too.
- **A redeploy or an in-app update restarts it.** The staged swap renames the app bundle, which Windows refuses while any process runs an image from inside it, so the swap has to end the gateway first (`bundle_apply.stop_terminal_gateway`).
  It does so last - after the daemon has detached and the shell has been stopped, once nothing is left that could start another - and politely: `quit` over the gateway's own control port, authenticated by the token in its record, so it closes every browser with 4011 and removes its record on the way out; only a gateway still there after ten seconds is terminated, by pid and creation time.
  The relaunched daemon starts the new build's gateway and hands it the terminals again, and every browser reconnects: with a delta when its page kept its cursor (a phone's tab), and with a whole replay from the desktop window, which the redeploy relaunches with the app.
  The run's log and `redeploy-result.json` say which process carries the terminals afterwards (`report_terminal_transport`, waiting up to 90 s for a gateway on its way), and that is reported, never judged: a gateway that does not come up leaves the terminals on the daemon, which serves them as it always did, so it is a warning rather than a rollback.
  The update applier is the same code (`swemux update-apply` runs `bundle_apply` from the console client's copy), which is why the stop lives in `gateway_discovery.py`: that bundle carries no aiohttp, so the module restates the control protocol's version and its `quit` message rather than importing `terminal_core`, and a test holds the copies equal.
- **The installer needs nothing new.** It writes no firewall rule (the in-app repair does, and admits both ports with one rule naming the one program, item 31), and an upgrade meets the gateway exactly as it meets the windowless daemon child and the supervisor: a process holding a file under `{app}`, which its Restart Manager pass (`CloseApplications=yes`) is there to close.
- **A supervisor update reaps it with everything else**, through the daemon's quit intent, which ends the gateway before the sessions.

## Which process holds a terminal

A session's terminal core - input ownership, geometry, the keystroke accounting - lives in exactly one process at a time (item 32, `terminal_placement.TerminalPlacement`).
A terminal written and resized from two processes has two owners and two sets of books, so this is the rule the rest of this section exists to keep.

- **The gateway may hold** a live session whose pseudoconsole the PTY supervisor owns, because it reaches the bytes through the supervisor and nothing else.
  An in-process session, a cold one and an ended one stay on the daemon.
- **To the gateway** when the daemon connects to it - every eligible session at once, inside `RemoteHub.connect` between the handshake and the first notice, so the `sessions` notice names the complete set and no other task sees a terminal moved and not yet announced - and when an eligible session starts while it is connected, before the spawn returns, so the browser that opens it finds it already there.
  The session's terminal becomes a `RemoteTerminal` mirror over the same `TerminalStream`, and the daemon's own core is **retired** in the same instant (`TerminalCore.retire`): from then on it writes and resizes nothing, a socket mid-handshake is refused, each message on an attached socket is checked before it is handled, and every attached browser is closed with 4012 and reconnects to the gateway with its ring position, for a delta.
- **Back to the daemon** when the session ends, after its `exit` frame has gone to the gateway's browsers (`RemoteTerminal.exit_sent`), and, for every session, when the gateway holding them is gone: exited, replaced, or ended as hung.
  The new in-process core is seeded from the mirror's last reading with the input revision moved on once (`RemoteTerminal.hand_back`), so a delivery judged against the gateway's count fails safe rather than landing on input the gateway accepted and never reported.
  Ownership and viewports do not carry: they belong to browser connections, and every one reconnects.
- **A restart moves nothing.** A restarting daemon leaves its gateway carrying every terminal; the successor's first connection adopts every eligible session again, the gateway keeps the cores and browsers it already has (`upsert` refreshes, never replaces), and the successor applies the keystroke writes typed while nobody listened.
  Until that first connection has succeeded or failed, the daemon's `/pty` waits for it - at most three seconds, before the upgrade - for a session the gateway may hold (`GatewayHost.settle`), because a daemon that has just started cannot tell whether the gateway it left running still carries that terminal for other browsers.
- **A lost control connection is not a lost gateway.** A gateway still running keeps its sessions: it is still serving their browsers, and taking them back would put two writers on each PTY.
  The daemon reconnects; `terminal_transport` keeps saying `gateway`.
- **A periodic reconcile** (every five seconds while connected) releases what no hook reported: a session removed from the registry, a new session object under an old id, a session ended through a path that sent no hook.

### A hung gateway

A gateway that is alive but not running its loop answers nobody, and its terminals answer nobody either.
The daemon decides that one is hung, ends it, and takes its terminals back - and is built so that a stalled *daemon* can never end a healthy gateway, which is the incident shape this plan exists to end.

- **Pinged every five seconds** over the control connection (`ping`, a capability; a gateway that did not agree it is not pinged), with ten seconds to answer.
  A miss counts only when the gateway sent nothing at all meanwhile, and only when the daemon's own timer fired on time: a timeout that fired half again later than it was set measured the daemon's stall, not the gateway's silence, and is not counted.
- **Three counted misses make a suspicion, not a verdict.** The daemon then asks the gateway's own browser listener for `/health`, from a thread, with five seconds to answer; any answer at all, a 503 included, means its loop is running and it is serving its browsers, so it keeps its sessions and only the control connection is renewed.
  The same check guards a gateway that has refused the control connection for thirty seconds while still running.
- **Silence there too is hung.** The daemon terminates it by its identity, kills it if a termination does not take (a stopped process holds a termination request until it runs again), and takes every terminal back only once it is gone - if it cannot be ended, its terminals stay with it rather than gain a second writer.
  The next incarnation starts a fresh gateway and hands them over again.
  `hung_ends` and `last_hung_reason` on `terminal_gateway` say it happened, and the lifecycle ledger records it.

## Key files

- `src/swe_mux/gateway_host.py` - the daemon's side: find or start, replace, restart, stop by intent, the liveness ping and the hung-gateway rule, `settle`, `terminal_transport`.
- `src/swe_mux/terminal_placement.py` - which process holds each session's terminal, and the one place that moves it.
- `src/swe_mux/terminal_gateway.py` - the process: `SupervisorLink`, `TerminalGateway`, `InstanceLock`, discovery, `gateway_command`, `main`.
- `src/swe_mux/gateway_discovery.py` - the discovery record and `stop_gateway`, importable without the daemon or aiohttp, for the staged swap.
- `src/swe_mux/bundle_apply.py` - the swap's gateway stop and its transport report.
- `src/swe_mux/terminal_core/gateway.py` - `GatewayHub`, the cores it hosts and the daemon connection.
- `src/swe_mux/terminal_core/remote.py` - the daemon's mirror.
- `src/swe_mux/terminal_core/protocol.py` - the control protocol.
- `src/swe_mux/desktop.py` - the frozen `--gateway-child` dispatch.
- `tests/test_terminal_gateway.py` - against a real in-process `SupervisorServer` (`tests/support/supervisor_wire.py`), ending with the gateway as a process and the daemon's loop blocked under it.
- `tests/test_gateway_host.py` - the real gateway as a process, driven by the real host: start, adopt, replace, restart, quit, terminals taken back from a dead gateway and from a suspended one.
- `tests/test_terminal_handoff.py` - both directions of the move with browsers attached, against a real `Session`, `TerminalPlacement`, `RemoteHub` and `GatewayHub`.
- `tests/test_gateway_discovery.py` - `stop_gateway` against the real gateway and against stand-ins it cannot ask.
- `tests/test_live_daemon.py` - the real `muxd` entry point with the gateway on: a terminal through it, `quit` taking it, and typing across a detach restart; run in CI on all three hosts, from source on Linux and macOS.
