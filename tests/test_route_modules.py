"""What has to stay true about the decomposed route package.

Three properties, each of which the decomposition could lose silently:

- the dependency direction (a domain must not import the composition root),
- the route table (the same routes, no duplicates, every module registered),
- the registration order (aiohttp resolves in order, so a static path can be
  shadowed by a dynamic one registered before it and simply stop existing).

The last one is the reason this file exists at all. Nothing else in the suite
would notice: the handler is still imported, still tested directly, and still
listed - it is only unreachable over HTTP.
"""

from __future__ import annotations

import ast
import tempfile
from pathlib import Path
from typing import Any

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from swe_mux import routes
from swe_mux.config import Config
from swe_mux.server import create_app

PACKAGE = Path(routes.__file__).parent
ROUTE_MODULES = sorted(path for path in PACKAGE.glob("*.py") if path.name != "__init__.py")


def _app() -> web.Application:
    return create_app(Config(data_dir=Path(tempfile.mkdtemp())))


def _imported_modules(path: Path) -> set[str]:
    """Every `swe_mux.*` module a file imports, at any indentation."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module and node.module.startswith("swe_mux"):
                    found.add(node.module.split(".")[-1])
                    found.update(alias.name for alias in node.names)
                continue
            # Relative: `from ..server import x` and `from .. import server` both
            # name the module, at different places in the statement.
            if node.module:
                found.add(node.module.split(".")[-1])
            else:
                found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("swe_mux"):
                    found.add(alias.name.split(".")[-1])
    return found


def test_no_route_module_imports_the_composition_root() -> None:
    """The direction is the boundary; a cycle would make it a fiction.

    `server.py` may depend on every domain - that is what a composition root is
    for. A domain that depended back could reach the runtime handles without
    being given them, and the split would buy nothing but files.
    """
    offenders = [path.name for path in ROUTE_MODULES if "server" in _imported_modules(path)]
    assert offenders == [], f"route modules importing the composition root: {offenders}"


def test_the_modules_below_the_route_layer_do_not_import_it_either() -> None:
    """`preview_transport` and friends are domains, not transport helpers.

    They existed before `routes/` and are reachable from outside it; an import
    upward would make the package boundary decorative and would put a cycle
    between `routes.support` and the modules it is meant to sit above.
    """
    package_root = PACKAGE.parent
    for name in (
        "preview_transport.py",
        "worktree_mutation.py",
        "session_media.py",
        "runtime_config.py",
        "http_support.py",
    ):
        imported = _imported_modules(package_root / name)
        assert "routes" not in imported, f"{name} imports the routes package"
        assert "server" not in imported, f"{name} imports the composition root"


def test_every_route_module_is_registered() -> None:
    """A module with a ROUTES tuple that nothing registers serves nothing."""
    registered = {Path(module.__file__ or "").name for module in routes.ORDER}
    declared = {
        path.name
        for path in ROUTE_MODULES
        if "\nROUTES" in path.read_text(encoding="utf-8")
    }
    assert declared - registered == set(), f"unregistered route modules: {declared - registered}"


def test_the_table_has_no_duplicate_route() -> None:
    """Two modules claiming one path is a merge artifact, not a decision."""
    seen: dict[tuple[str, str], str] = {}
    duplicates: list[str] = []
    for module in routes.ORDER:
        for route in module.ROUTES:
            key = (route.method, route.path)
            owner = Path(module.__file__ or "").name
            if key in seen:
                duplicates.append(f"{route.method} {route.path}: {seen[key]} and {owner}")
            seen[key] = owner
    assert duplicates == [], duplicates


async def test_every_static_path_still_reaches_its_own_handler() -> None:
    """The shadowing check: registration order decides collisions.

    `/api/history/duplicates` and `/api/history/{sid}` are the same shape to the
    router, and whichever is registered first wins. Moving a handler into another
    module moves its route with it, so this can change without any route being
    edited.
    """
    app = _app()
    shadowed: list[str] = []
    for resource in app.router.resources():
        path = resource.get_info().get("path")
        if not path:
            continue
        for route in resource:
            if route.method in ("HEAD", "*"):
                continue
            match = await app.router.resolve(make_mocked_request(route.method, path, app=app))
            if match.route.handler is not route.handler:
                expected = getattr(route.handler, "__name__", route.handler)
                actual = getattr(match.route.handler, "__name__", match.route.handler)
                shadowed.append(f"{route.method} {path}: wanted {expected}, got {actual}")
    assert shadowed == [], shadowed


def test_the_assembled_table_is_the_registered_one() -> None:
    """Nothing registers routes behind `all_routes()`'s back."""
    app = _app()
    assembled = {(route.method, route.path) for route in routes.all_routes()}
    registered: set[tuple[str, Any]] = set()
    for resource in app.router.resources():
        info = resource.get_info()
        # A dynamic resource reports its pattern as `formatter`; a static-file
        # mount reports neither and is not part of this table.
        path = info.get("path") or info.get("formatter")
        if not path:
            continue
        for route in resource:
            if route.method == "HEAD":
                continue
            registered.add((route.method, path))
    # The Preview passthrough is `web.route("*", ...)`: aiohttp keeps it as a
    # single any-method route whose reported pattern also drops the `:.*` suffix,
    # so it compares as neither the same method nor the same path. Its presence is
    # asserted on its own rather than folded into a set comparison that cannot
    # express it.
    preview = "/preview/"
    registered = {(method, path) for method, path in registered if not path.startswith(preview)}
    assembled = {(method, path) for method, path in assembled if not path.startswith(preview)}
    assert registered == assembled
    assert any(route.path.startswith(preview) for route in routes.all_routes())
