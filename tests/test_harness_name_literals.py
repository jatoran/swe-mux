"""Keep harness names out of code that should be asking the registry.

The registry makes a new harness cheap only while consumers derive behaviour from
declared capabilities. A raw harness name in code that is not one of the declared
homes below is the mechanism by which that stops being true, and it fails in the
expensive direction: the new harness silently takes a default written for somebody
else, with no error, no type failure, and no log line.

**Scoped per function, not per module.** An earlier version of this test allow-listed
whole files, and that hid a real finding: `reconcile.py` was exempted as
"assert_never anchored" on the strength of one anchored dispatch, while
`scan_external_transcripts` in the same file hardcoded a two-vendor tuple with no
anchor at all, so retroactive discovery silently existed for two harnesses out of
five. A module-wide exemption is only ever correct when the whole module is about one
harness; anywhere else the unit is the function.

Three shapes are legitimate:

- **The registry itself and one harness's own code.** `harness.py` declares the names;
  an adapter is by construction about one harness. These are module-scoped.
- **An exhaustive dispatch.** A name-keyed `if/elif` chain ending in `assert_never` is
  not a parity hazard: adding a harness is a type error there, which is the intended
  cost. Recognized automatically, per function, by finding the `assert_never` call.
- **A declared table** whose missing entry means "no behaviour" rather than "wrong
  behaviour", listed by function with the reason that makes it safe.

Everything else must reach a descriptor predicate. If this test fails on new code, the
fix is almost never to extend the allowlist: it is to declare the fact on
`HarnessDescriptor` and read it back, the way `publishes_cli_state`,
`assigns_conversation_id`, and `conversation_discovery` are read.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from swe_mux.harness import HARNESSES

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src" / "swe_mux"
FRONTEND_ROOT = Path(__file__).resolve().parent.parent / "frontend" / "src"

# Whole modules that are about one harness, or that are the registry itself.
_MODULE_ALLOWLIST: dict[str, str] = {
    "harness.py": "the registry itself declares the names",
    "adapters/claude.py": "one harness's own launch and transcript code",
    "adapters/codex.py": "one harness's own launch and transcript code",
    "adapters/omp.py": "one harness's own launch and transcript code",
    "adapters/pi.py": "one harness's own launch and transcript code",
    "adapters/opencode.py": "one harness's own launch and transcript code",
    "opencode_store.py": "one harness's own conversation store reader",
    "cli_state.py": "reads one harness's job and state files",
    # The only module-wide exemption that is not one harness's own code, and it is
    # earned rather than assumed: every path here is about the closed
    # `ManagedProvider` set, `_provider_profile` is `assert_never` anchored, and
    # `test_managed_providers_are_the_registry_set` asserts the literal equals the
    # harnesses declaring `provider_account_management`. A third such harness is
    # therefore a test failure and then a type error, not a silent credential write.
    "provider_accounts.py": "closed ManagedProvider set, anchored and asserted",
}

# Individual functions (or a module's top level, spelled `<module>`) that may name a
# harness, each with the reason the naming is safe there. An entry here is a claim
# that a missing harness means *no behaviour*, never wrong behaviour.
_FUNCTION_ALLOWLIST: dict[str, str] = {
    # Declared tables and module constants whose missing entry means no behaviour.
    "config.py::<module>": "legacy ccusage source-command migration ids",
    "usage.py::<module>": (
        "ccusage source display labels; unknown sources use a generic fallback"
    ),
    "observation.py::<module>": "classifier backend set, checked against the registry",
    "operational_telemetry.py::<module>": "parser revisions keyed by transcript dialect",
    "agent_context.py::<module>": "sync direction ids are a stable public contract",
    "session.py::<module>": "PTY screen rules, each scoped to the harnesses it was captured on",
    "spawn_contract.py::<module>": "scrubs one CLI's nested-session environment markers",
    # One harness's own code, the function-scoped equivalent of an adapter module.
    "agent_launcher.py::_claude": "one harness's own launch argv",
    "agent_launcher.py::_codex": "one harness's own launch argv",
    "agent_launcher.py::_omp": "one harness's own launch argv",
    "agent_launcher.py::_pi": "one harness's own launch argv",
    "agent_launcher.py::_opencode": "one harness's own launch argv",
    "agent_launcher.py::_launch": "names the family whose resolver needs its JS entrypoint",
    "agent_launcher.py::main": "retires the per-session artifacts two harnesses materialize",
    "agent_skills.py::_claude_inventory": "one harness's own skill roots",
    "agent_skills.py::_codex_inventory": "one harness's own skill roots",
    "agent_skills.py::_omp_inventory": "one harness's own skill roots",
    "agent_skills.py::_pi_inventory": "one harness's own skill roots",
    "agent_skills.py::_opencode_inventory": "one harness's own skill roots",
    "agent_skills.py::_scan_skill_root": "two measured naming deviations, stated in its docstring",
    "observation.py::_claude": "one harness's own record classifier",
    "observation.py::_codex": "one harness's own record classifier",
    "observation.py::_omp": "one dialect's record classifier",
    "agent_context.py::AgentContextService._claude_provider": "one harness's memory provider",
    "agent_context.py::AgentContextService._codex_provider": "one harness's memory provider",
    "agent_context.py::AgentContextService._source_descriptor": "memory source-id prefix contract",
    "agent_environment.py::_runtime": "one harness's version-probe argv",
    "agent_environment.py::_hook_inventory": "additively prepends one harness's extension items",
    "agent_environment.py::discover_agent_environment": "orders one harness's extension section",
    "launchers.py::resolve_codex_pty_command": "resolves one npm package's JS entrypoint",
    "reconcile.py::inspect_claude": "one harness's own discovery header reader",
    "reconcile.py::inspect_codex": "one harness's own discovery header reader",
    "operational_telemetry.py::OperationalTelemetryStore.review_quota_resets": (
        "manual-usage review is a Codex-only evidence kind"
    ),
    "operational_telemetry.py::OperationalTelemetryStore.review_quota_resets.op": (
        "manual-usage review is a Codex-only evidence kind"
    ),
    # Dialect comparisons that happen to share a spelling with a harness name.
    "transcript_view.py::_conversation_records": "compares dialects, not harnesses",
    # Dialect-keyed capability registries. The key is the record format, which is why
    # pi and oh-my-pi will share one entry rather than needing two; a missing key
    # means "mux cannot do this here" and every reader of these tables states that.
    "transcript_view.py::<module>": "dialect-keyed scanner registry, not harnesses",
    "transcript_fork.py::<module>": "dialect-keyed writer registry, not harnesses",
    # Anchored dispatches whose `assert_never` the scanner cannot see from here.
    "adapters/__init__.py::build_agent_adapter": "adapter-family dispatch, assert_never anchored",
}

# The frontend has no `assert_never`, so its allowlist stays module-scoped and narrow:
# a declared table whose missing entry means "no behaviour" is fine, nothing else.
_FRONTEND_ALLOWLIST: dict[str, str] = {
    "harnessRegistry.ts": "the browser's view of the registry",
    "harnessRegistrySeed.ts": "generated from the daemon's descriptors",
    "terminalCaretPlacement.ts": "caret resolver table; a missing entry means no tap steering",
    "commandRail.ts": "rail items declare the harnesses they apply to",
    "ProviderAccounts.tsx": "managed-provider branding, matching ManagedProvider",
    "SessionRowSettings.tsx": "static preview fixtures, not runtime behaviour",
    # A model family that happens to share a spelling with a harness. `openai/codex`
    # is a model id, not the Codex CLI, and compacting it is a display concern with
    # no harness capability behind it.
    "modelDisplay.ts": "model family names, not harnesses",
}

_NAMES = frozenset(HARNESSES)


def _has_assert_never(node: ast.AST) -> bool:
    """Whether this scope terminates a dispatch with `assert_never`.

    Nested function bodies are excluded, so an inner helper's anchor never vouches
    for its enclosing function.
    """
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            target = child.func
            name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
            if name == "assert_never":
                return True
    return False


def _named_scopes(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    """Every function scope, plus `<module>` for everything outside one."""
    scopes: list[tuple[str, ast.AST]] = []
    functions: list[ast.AST] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                qualname = f"{prefix}{child.name}"
                scopes.append((qualname, child))
                functions.append(child)
                visit(child, f"{qualname}.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    inside = {id(node) for function in functions for node in ast.walk(function)}
    module_level = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and id(node) not in inside
    ]
    scopes.append(("<module>", ast.Module(body=[], type_ignores=[])))
    return [*scopes, *(("<module>", node) for node in module_level)]


def _findings(path: Path) -> set[str]:
    """Scopes in this module that name a harness without an anchor or an exemption.

    Parsed rather than grepped, so a name inside a comment or a docstring is not a
    finding: prose explaining why a rule exists is exactly what the repository wants
    more of. Only a string *value* the code can branch on counts.
    """
    relative = path.relative_to(SOURCE_ROOT).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    functions: dict[str, ast.AST] = {}
    for qualname, node in _named_scopes(tree):
        if qualname != "<module>":
            functions[qualname] = node
    inside = {
        id(child)
        for node in functions.values()
        for child in ast.walk(node)
    }

    offenders: set[str] = set()
    for qualname, node in functions.items():
        names = {
            child.value
            for child in ast.walk(node)
            if isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and child.value in _NAMES
        }
        if not names:
            continue
        if _has_assert_never(node):
            continue
        if f"{relative}::{qualname}" in _FUNCTION_ALLOWLIST:
            continue
        offenders.add(f"{relative}::{qualname} {sorted(names)}")

    module_names = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in _NAMES
        and id(node) not in inside
    }
    if module_names and f"{relative}::<module>" not in _FUNCTION_ALLOWLIST:
        offenders.add(f"{relative}::<module> {sorted(module_names)}")
    return offenders


def _python_sources() -> list[Path]:
    return sorted(
        path
        for path in SOURCE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
        and path.relative_to(SOURCE_ROOT).as_posix() not in _MODULE_ALLOWLIST
    )


@pytest.mark.parametrize("path", _python_sources(), ids=lambda path: path.name)
def test_backend_code_asks_the_registry_rather_than_naming_a_harness(path: Path) -> None:
    """No harness-name constant in a function that is not an exhaustive dispatch."""
    offenders = sorted(_findings(path))
    assert not offenders, (
        f"{path.name} names harnesses in {offenders}. Declare the fact on "
        f"HarnessDescriptor and read it back, or add the specific function to "
        f"_FUNCTION_ALLOWLIST in {Path(__file__).name} with the reason it is safe. "
        f"Allow-listing the whole module is not the fix: doing that is what hid the "
        f"two-vendor discovery scanner."
    )


@pytest.mark.parametrize(
    "path",
    sorted(path for path in FRONTEND_ROOT.rglob("*.ts*") if path.name not in _FRONTEND_ALLOWLIST),
    ids=lambda path: path.name,
)
def test_frontend_modules_read_declared_capabilities(path: Path) -> None:
    """No harness-name literal in browser code outside a declared home.

    The frontend has no exhaustiveness escape hatch, so every per-harness fact it
    needs has to arrive in the registry payload. A name compiled in here is a second
    copy of a daemon-owned fact and drifts from it: the command rail's copy typed
    `/name` on oh-my-pi long after the daemon knew the CLI wanted `/skill:name`.
    """
    text = path.read_text(encoding="utf-8")
    offenders = sorted(
        {name for name in _NAMES if f"'{name}'" in text or f'"{name}"' in text}
    )
    assert not offenders, (
        f"{path.name} names {offenders} directly. Publish the trait from "
        f"`public_harness_registry` and read it through harnessRegistry.ts, or add "
        f"this module to the allowlist in {Path(__file__).name} with its reason."
    )


def test_every_allowlist_entry_still_points_at_real_code() -> None:
    """A stale entry hides a real finding, so each one must still resolve.

    Both halves matter: a module that was renamed, and a *function* that was renamed
    or deleted while its exemption stayed behind. The second is what makes a
    function-scoped allowlist safer than a module-scoped one, and it only holds if
    the entries are checked.
    """
    for relative in _MODULE_ALLOWLIST:
        assert (SOURCE_ROOT / relative).is_file(), f"stale module entry: {relative}"
    for name in _FRONTEND_ALLOWLIST:
        assert list(FRONTEND_ROOT.rglob(name)), f"stale frontend entry: {name}"
    for entry in _FUNCTION_ALLOWLIST:
        relative, _, qualname = entry.partition("::")
        path = SOURCE_ROOT / relative
        assert path.is_file(), f"stale function entry, module missing: {entry}"
        if qualname == "<module>":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = {found for found, _node in _named_scopes(tree) if found != "<module>"}
        assert qualname in names, f"stale function entry, function missing: {entry}"


def test_an_unanchored_dispatch_is_reported_even_beside_an_anchored_one(
    tmp_path: Path,
) -> None:
    """The regression that motivated per-function scoping.

    `reconcile.py` held both shapes: an `assert_never`-anchored dispatch and, in the
    same file, a hardcoded two-vendor tuple. A module-scoped allowlist accepted the
    file on the strength of the first and never looked at the second.
    """
    module = tmp_path / "sample.py"
    module.write_text(
        "from typing import assert_never\n"
        "def anchored(backend):\n"
        "    if backend == 'claude':\n"
        "        return 1\n"
        "    assert_never(backend)\n"
        "def unanchored():\n"
        "    return [('claude', 1), ('codex', 2)]\n",
        encoding="utf-8",
    )
    tree = ast.parse(module.read_text(encoding="utf-8"))
    scopes = {name: node for name, node in _named_scopes(tree) if name != "<module>"}
    assert _has_assert_never(scopes["anchored"]) is True
    assert _has_assert_never(scopes["unanchored"]) is False
