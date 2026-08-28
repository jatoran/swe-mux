# Security policy

## Supported versions

| Version | Supported |
|---|---|
| Most recent release | Yes |
| Everything older | No |

swe-mux is a small project with no long-term-support branch and no backport process.
A fix ships in the next release from `master`; there are no patch releases against an older
line.
Upgrading is the remedy for every reported issue.

## Reporting a vulnerability

Report privately through GitHub, never as a public issue or pull request.

1. Open the repository at `https://github.com/jatoran/swe-mux`.
2. Select the **Security** tab, then **Report a vulnerability** under **Advisories**.
3. Direct link: `https://github.com/jatoran/swe-mux/security/advisories/new`.

Include the swe-mux version, the operating system, whether the daemon was reachable on a
tailnet at the time, the exact steps to reproduce, and what an attacker gains.
An attached diagnostics bundle (`mux doctor --export`) is useful and contains no terminal bytes
or message content; review it before attaching it anyway.

### What to expect

- Acknowledgement of the report within 5 business days.
- An initial assessment - in scope or not, and a severity - within 10 business days.
- Progress updates at least every 14 days while the report is open.

These are the targets of a maintainer-scale project, not a contractual SLA.
Disclosure is coordinated: the fix, the advisory, and the release go out together, and the
reporter is credited unless they ask not to be.

## Scope

Scope follows the trust boundary swe-mux actually has, which is unusual enough to state
plainly before listing cases.

### The trust boundary

swe-mux is a **local daemon on the operator's own machine**.
It allocates pseudoterminals, launches agent CLIs and shells, and runs every one of them with
the full privileges of the account running the daemon.
Executing arbitrary code on that machine is its purpose, not a defect.

By default it binds loopback, and - when the tailnet listener is enabled - the machine's own
Tailscale IPv4 address inside `100.64.0.0/10`.
There is **no swe-mux login**: no accounts, no bearer tokens, no user model, and no
authorization layer between an admitted peer and the terminals.
Tailscale policy is the entire access boundary.
Any device your tailnet admits to that listener holds terminal and code-execution authority on
the host, equal to the account running the daemon.

Binding `0.0.0.0`, binding a LAN interface, router port forwarding, Tailscale Funnel, and any
other form of public ingress are **unsupported configurations**, and the daemon rejects the
Host and Origin authorities that would accompany them.
An SSH local forward whose browser-facing address is loopback (`ssh -L 9876:127.0.0.1:8765 host`)
is supported, and confers the same authority to whoever the SSH account admits.

One request leaves the machine that the operator did not configure: the daily release
check, a `GET` of `https://swemux.dev/version.json` (falling back to the GitHub Releases
API) that reports whether a newer version exists and downloads nothing.
It carries no query string, no custom header, no cookie, and no identifier of the machine
or the install, so it is byte-identical for every copy of swe-mux and conveys nothing
beyond the fact that some address asked for a public file.
It is disabled by `update_check_enabled` in Settings → Diagnostics, and disabled means no
request is made at all.
A way to make that request carry install-identifying data, or to make it happen with the
setting off, is in scope below.

Two consequences define scope.
**Exposing the daemon to an untrusted network is outside the supported configuration**, so a
report whose precondition is such an exposure describes the documented behaviour of a
configuration this project does not support, and is not a vulnerability in swe-mux.
And an attacker who already runs code as the operator's account already holds everything the
daemon holds, so a report that assumes that starting position is not a boundary crossing
either.

### In scope

A vulnerability is something that breaks the boundary above **as designed**:

- Reaching a machine-local boundary from the tailnet: the loopback-only hook ingress, its
  per-session secrets, or the desktop daemon-shutdown secret.
- Bypassing Host or Origin authority validation on a mutation or a WebSocket upgrade.
- Escaping a preview's bounds: reaching a loopback endpoint that was never registered, or
  reading a file outside a static preview's served directory.
- Path traversal or unbounded reads through Project file browsing, note storage, or file
  editors.
- Leaking a secret into somewhere it does not belong: `daemon.log`, the diagnostics bundle, the
  event bus, telemetry, a transcript, or an HTTP response. Saved provider credentials and
  configured API keys are the material that matters most here.
- Bypassing an approval gate that a human is supposed to hold: executing a repository task file
  without its exact-content approval, running a land-queue verification command a human did not
  approve, or delivering queued input to a terminal that readiness or the arming floor should
  have blocked.
- Any non-human sender - an observer, an automation, another agent's MCP call - obtaining an
  authority the design reserves for a human act.
- Escalating privilege beyond the account running the daemon, including through the Windows
  firewall repair path or the desktop elevation path.
- A vulnerable dependency that swe-mux actually redistributes in the desktop bundle or resolves
  into the wheel's install closure.
- Making the release update check identify the install - a query string, a header, a cookie,
  or any per-machine value on the request - or making it fire while `update_check_enabled`
  is off. The no-telemetry property is a design commitment, not a side effect.

### Out of scope

- Any report requiring the daemon to be exposed to an untrusted network, a shared tailnet, a
  LAN interface, `0.0.0.0`, Tailscale Funnel, or port forwarding. The documented posture is that
  an admitted peer already holds full authority.
- Anything an attacker can do only after already having code execution as the operator's
  account.
- The agent CLIs themselves. Claude Code, Codex CLI, and the other harnesses are third-party
  products; report their issues to their vendors.
- What an agent does when the operator's own approval settings permit it. Prompt injection that
  causes an agent to take an action the operator configured as auto-approved is a configuration
  outcome, not a swe-mux vulnerability. Prompt injection that reaches past the
  never-auto-approved floor **is** in scope, and belongs in the list above.
- Provider quota, billing, or account-sharing questions. swe-mux proxies nothing and resells
  nothing; each account stays under the operator's own agreement with that vendor.
- Missing hardening headers, cookie flags, or scanner findings with no demonstrated exploit
  path, and self-XSS.
- Denial of service against a daemon the reporter already controls.

## Non-goals of this project

swe-mux has no multi-tenancy, no role model, and no audit subsystem, and none are planned.
It is a single-operator tool.
Requests to add an authentication layer are product proposals rather than security reports, and
belong in an issue.
