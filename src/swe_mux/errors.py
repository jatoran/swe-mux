"""Typed domain refusals the transport translates, kept below every domain module.

One exception lives here so far, and it exists to separate two things the HTTP
layer could not previously tell apart.

`error_middleware` used to translate **any** `KeyError` into a 404. That is a
deliberate convention - some forty call sites raise one to mean "this id names
nothing" - but a `KeyError` is also what a handler raises when it reads a
dictionary key that was never written, and those are opposite facts: the first
is a correct answer to a bad request, the second is a bug. Reported identically,
the bug was answered with a confident 404, logged nowhere, and left no traceback
to find it by.

`NotFound` names the deliberate half. Anything that reaches the middleware as a
bare `KeyError` is now treated as the accident it almost always is: a 500 with a
traceback in `daemon.log`.

It subclasses `KeyError` on purpose. The convention has catch sites as well as
raise sites (`routes/diagnostics.py` falls back to a post-mortem view when a
session does not resolve, `mcp.py` maps a miss onto its own scope error), and a
new base class would have silently stopped every one of them catching the thing
they were written to catch. Subclassing means only the *raise* sites had to move,
and each catch site can narrow to `NotFound` later, one at a time, on purpose.
"""

from __future__ import annotations


class NotFound(KeyError):
    """A deliberate "this id names nothing" refusal; the transport answers 404.

    `key` is what the caller asked for and is for the log, never for the
    response body: echoing it back turned a 404 into a reflection of arbitrary
    request text. `message` is what the caller is told, and defaults to naming
    the kind of thing that was missing - which is the part a caller can act on.
    """

    __slots__ = ("key", "kind", "message")

    def __init__(self, key: object = "", *, kind: str = "resource", message: str | None = None):
        super().__init__(key)
        self.key = key
        self.kind = kind
        self.message = message or f"no such {kind}"

    def __str__(self) -> str:
        # `KeyError.__str__` is `repr(args[0])`, so anything that formats this
        # exception into a message would leak the key it was told not to echo.
        return self.message
