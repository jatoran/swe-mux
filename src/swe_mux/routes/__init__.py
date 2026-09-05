"""The daemon's HTTP and WebSocket routes, one module per domain.

`server.py` stays the composition root: it builds the runtime handles and
publishes them, and it registers this package's table. What it no longer does is
hold the handlers. Each module here owns its domain's handlers, the private
helpers only that domain uses, and a `ROUTES` tuple - so a change to one feature
touches one file instead of a 17,000-line one.

The rules that keep this a decomposition rather than a pile:

- A route module never imports `server`. The composition root may depend on the
  domains; a domain that depended back on the root would make the direction a
  cycle and the boundary a fiction. `tests/test_route_modules.py` enforces it.
- Anything two domains need lives in `support.py` or in a real domain module
  outside this package (`runtime_config`, `session_media`, `preview_transport`,
  `worktree_mutation`), never in whichever route module happened to define it.
- Cross-module references go through the module (`from . import sessions`, then
  `sessions._spawn_from_body(...)`) rather than importing the function. The
  indirection is deliberate: it is what lets a test replace one implementation
  for every caller, the way it could when all of this was one module.

`ORDER` is the registration order, and it is load-bearing. aiohttp resolves in
registration order, so a static path registered after a dynamic one that also
matches it becomes unreachable. `tests/test_route_modules.py` resolves every
static path in the table and asserts it reaches its own handler.
"""

from __future__ import annotations

from aiohttp import web

from . import (
    agent_context,
    agent_ingress,
    assistant,
    attention,
    automation,
    branch,
    clipboard,
    configurator,
    desktop_integration,
    diagnostics,
    frontend,
    git,
    grants,
    history,
    insights,
    land,
    notes,
    observations,
    onboarding,
    plugins,
    processes,
    project_actions,
    project_files,
    projects,
    prompts,
    pty,
    push,
    queue,
    scan_timeline,
    schedules,
    sessions,
    settings,
    support,
    system,
    terminal,
    update,
    usage,
    voice,
)

#: Registration order. Grouped the way the table read when it was one literal:
#: the app shell and daemon control first, then configuration, then the Project
#: and session domains, then the agent-facing ingress, and the WebSockets last.
ORDER = (
    system,
    update,
    frontend,
    settings,
    automation,
    onboarding,
    attention,
    insights,
    projects,
    prompts,
    queue,
    project_actions,
    notes,
    observations,
    plugins,
    grants,
    schedules,
    agent_context,
    project_files,
    sessions,
    diagnostics,
    configurator,
    scan_timeline,
    branch,
    terminal,
    history,
    clipboard,
    push,
    assistant,
    voice,
    usage,
    processes,
    agent_ingress,
    git,
    land,
    desktop_integration,
    pty,
)


def all_routes() -> list[web.RouteDef]:
    """The whole route table, in registration order."""
    return [route for module in ORDER for route in module.ROUTES]


__all__ = ["ORDER", "all_routes", "support"]
