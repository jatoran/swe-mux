"""Every handle `_teardown_runtime` names must actually be reached.

This file exists because that stopped being true and nothing said so. The daemon
used to store its runtime handles under plain string keys and tear them down by
the same strings; the move to `aiohttp`'s `AppKey` changed the writes and the
reads but left the teardown on the strings. An `AppKey` has no `__eq__` and no
`__hash__`, so it is hashed by identity: `app.get("provider_accounts")` against
an app that published `keys.PROVIDER_ACCOUNTS` is not a different spelling of the
same lookup, it is a miss that returns `None`. Every line of the teardown's two
loops became a silent no-op - for a week no store was closed and no service was
stopped when the daemon shut down.

Nothing failed. The single visible trace was aiohttp's finalizer printing
"Unclosed client session" long after the run that caused it, because
`ProviderAccountManager` is the one skipped service that owns a socket.

So the guard is behavioural rather than textual: publish a recording stub under
every key the teardown names, run the real `_teardown_runtime`, and assert each
stub was stopped or closed. A key that is looked up under a name the app does not
publish records nothing and fails here, whatever spelling caused it.

The list of keys is read out of the source with `ast` rather than restated, for
two reasons. A hand-kept copy would be one more place to forget, and the parse is
itself an assertion: every element of those tuples must be `keys.<NAME>`, so a
string literal creeping back in fails at collection instead of passing silently.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from aiohttp import web

from swe_mux import app_keys as keys
from swe_mux import server
from swe_mux.config import Config
from swe_mux.sqlite_store import end_shutdown_drain

#: The teardown helpers, and the method each one is expected to reach.
_HELPERS = {"_stop_handle": "stop", "_close_handle": "close"}


class Recorder:
    """A handle that remembers being torn down.

    `persist_telemetry` is here because the teardown calls it on the attention
    ranking service before stopping it; a stub that raised there would exercise
    the tolerant path rather than the ordinary one.
    """

    def __init__(self) -> None:
        self.stopped = False
        self.closed = False

    async def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True

    async def persist_telemetry(self) -> None:
        return None


def _key_name(node: ast.expr) -> str:
    """The `keys.<NAME>` a teardown line names, or a failure that says why not."""
    assert isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name), (
        f"_teardown_runtime names a handle as `{ast.unparse(node)}`; it must be "
        "`keys.<NAME>`. An AppKey is hashed by identity, so a bare string is a "
        "lookup that silently misses and tears nothing down."
    )
    assert node.value.id == "keys", (
        f"_teardown_runtime names a handle as `{ast.unparse(node)}`; handles live "
        "in `app_keys`, imported as `keys`."
    )
    assert hasattr(keys, node.attr), f"`keys.{node.attr}` does not exist"
    return str(node.attr)


def _teardown_function() -> ast.AsyncFunctionDef:
    source = Path(server.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_teardown_runtime":
            return node
    raise AssertionError("server.py no longer defines `_teardown_runtime`")


def _loop_keys(function: ast.AsyncFunctionDef, target: str, call: ast.Call) -> list[str]:
    """The keys of the `for` loop whose body contains `call` and binds `target`."""
    for loop in ast.walk(function):
        if not isinstance(loop, ast.For):
            continue
        if not (isinstance(loop.target, ast.Name) and loop.target.id == target):
            continue
        if not any(node is call for statement in loop.body for node in ast.walk(statement)):
            continue
        assert isinstance(loop.iter, ast.Tuple), (
            "a teardown loop must iterate a literal tuple of AppKeys so this "
            "guard can enumerate what it reaches"
        )
        return [_key_name(element) for element in loop.iter.elts]
    raise AssertionError(f"no enclosing `for {target} in (...)` for {ast.unparse(call)}")


def teardown_handles() -> dict[str, set[str]]:
    """Every key `_teardown_runtime` stops, and every key it closes."""
    function = _teardown_function()
    found: dict[str, set[str]] = {"stop": set(), "close": set()}
    for node in ast.walk(function):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        action = _HELPERS.get(node.func.id)
        if action is None:
            continue
        assert len(node.args) == 2, (
            f"`{node.func.id}` is called with {len(node.args)} arguments; it takes "
            "the application and the AppKey, so that a name cannot be passed instead"
        )
        argument = node.args[1]
        if isinstance(argument, ast.Name):
            found[action].update(_loop_keys(function, argument.id, node))
        else:
            found[action].add(_key_name(argument))
    return found


async def test_the_teardown_names_only_appkeys_and_reaches_every_one_of_them(
    tmp_path: Path,
) -> None:
    """Parse the teardown, stub every handle it names, run it, count the survivors."""
    handles = teardown_handles()
    assert handles["stop"], "the teardown stops nothing; the collection above is broken"
    assert handles["close"], "the teardown closes nothing; the collection above is broken"

    data_dir = tmp_path / ".mux"
    data_dir.mkdir()
    app = web.Application()
    stubs = {name: Recorder() for name in handles["stop"] | handles["close"]}
    published: dict[web.AppKey[Any], Any] = {
        keys.CONFIG: Config(data_dir=data_dir),
        keys.SHUTDOWN_STATE: {"intent": "quit"},
    }
    for name, stub in stubs.items():
        published[getattr(keys, name)] = stub
    server.publish(app, published)

    try:
        await server._teardown_runtime(app)
    finally:
        end_shutdown_drain()

    unstopped = sorted(name for name in handles["stop"] if not stubs[name].stopped)
    unclosed = sorted(name for name in handles["close"] if not stubs[name].closed)
    assert not unstopped and not unclosed, (
        "the daemon's teardown named these handles and then never reached them, "
        "which is the shape that left an aiohttp ClientSession open at every "
        f"shutdown for a week: not stopped {unstopped}, not closed {unclosed}"
    )


def test_the_teardown_reads_no_key_the_daemon_never_publishes() -> None:
    """A handle torn down under a key nothing writes is a teardown that does nothing.

    The behavioural test above cannot see this, because it publishes the stubs
    under exactly the keys the teardown reads. This one asks the other half of
    the question: does `_build_runtime_handles` actually write them?
    """
    source = Path(server.__file__).read_text(encoding="utf-8")
    written: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            for key_node in node.keys:
                if (
                    isinstance(key_node, ast.Attribute)
                    and isinstance(key_node.value, ast.Name)
                    and key_node.value.id == "keys"
                ):
                    written.add(key_node.attr)
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Attribute)
            and isinstance(node.slice.value, ast.Name)
            and node.slice.value.id == "keys"
        ):
            written.add(node.slice.attr)

    handles = teardown_handles()
    orphans = sorted((handles["stop"] | handles["close"]) - written)
    assert not orphans, (
        f"the teardown tears down {orphans}, which server.py never publishes under "
        "those keys - so those lines can only ever be no-ops"
    )
