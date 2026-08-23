# Reaching this install from another device

## The supported shape

swe-mux binds **loopback**, plus the Tailscale IPv4 address of this machine when
`tailnet_enabled` is on (which is the default).
Both listeners serve the same UI and the same API.

There is no swe-mux login. Access control is Tailscale's: whoever is on the tailnet
and permitted by its policy can reach it. That is the whole security model, and it is
worth saying out loud when someone asks to widen access.

## What is deliberately not supported

`0.0.0.0`, LAN interfaces, router port forwarding, and Tailscale Funnel.
`host` validates against loopback addresses only, and the Tailscale address is
detected rather than configured - an arbitrary non-loopback bind is refused.

If someone wants to reach swe-mux from a machine that is not on their tailnet, the
supported answer is an SSH local forward whose browser-facing end is loopback, for
example `ssh -L 9876:127.0.0.1:8765 workstation`, reaching it at
`http://127.0.0.1:9876/`.
Addressing that forward through a LAN hostname is rejected.

Do not propose binding a wider interface as a workaround. It is not a configuration
this daemon accepts, and the refusal is the feature.

## Detection failure is not breakage

If Tailscale detection fails, loopback keeps working and a diagnostic is reported.
`configurator_diagnostics` carries the remote and firewall state; read it before
concluding anything about why a phone cannot connect.

`--local-only` disables the tailnet listener for one run without changing the
setting.

## The phone

Two things beyond plain reachability matter on a phone.

**Microphone capture needs a secure context.** Plain HTTP over the tailnet is not
one. So swe-mux provisions a private Tailscale Serve listener on **HTTPS 443**
proxying to the loopback port, and voice on mobile goes through that.
It has to be 443 rather than the swe-mux port, because the daemon already binds its
own port directly on the Tailscale address for the HTTP fallback and a Serve listener
there would collide.
Serve is brought up best-effort at daemon start and is idempotent; there is a setup
route that re-runs the same bounded command for one-time HTTPS approval or repair.

The common failure is not swe-mux's: the phone needs Tailscale's DNS enabled to
resolve the `.ts.net` name.

**Push notifications need the app installed** as a web app on the phone, and they are
per-device-class settings rather than install-wide ones. Notification preferences are
stored per profile (`desktop` / `mobile`) precisely so the phone and the desktop can
disagree about what is worth interrupting for - the phone defaults to staying quiet
while the operator is demonstrably working at another device.

## Firewall

The diagnostics report inspects the host firewall and reports what it finds.
On POSIX hosts it will report the exact command that would open the port and will
**not** run it. Opening a port needs root and is a security decision; a daemon does
not get to make it, and neither do you. Show the command, explain it, and let the
operator run it.

## Previews

Development servers stay on workstation loopback. A remote browser reaches them only
through a registered preview route, and absolute loopback calls between services in
one Project are remapped to the destination's registered route rather than to the
remote device's own loopback.

A **static** preview needs no server at all - the daemon reads the document from the
Project checkout, and its route id derives from the directory rather than a port, so
a link sent to a phone survives daemon restarts.
