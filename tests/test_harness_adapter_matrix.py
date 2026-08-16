"""Every registered harness must launch, resume, and exit through its adapter.

The per-adapter tests in `test_adapters.py` and `test_pi_opencode_adapters.py` are
thorough but name-scoped: each constructs one adapter class directly, so a sixth
harness added to the registry gets no adapter coverage and nothing says so. This
parametrizes over `HARNESSES` through the real `build_agent_adapter` dispatch - the
same call `SessionManager.spawn` makes - so a new harness cannot ship without its
adapter producing a launchable spawn spec, a resume spec that targets the
conversation, and a graceful exit. It is the adapter-level sibling of
`test_every_dialect_has_a_reader_that_actually_reads`.

Coverage is enforced two ways, both required:

1. Parametrizing over `HARNESSES` means a new harness is exercised automatically -
   no per-harness test to remember to write.
2. `_ADAPTER_EXPECTATIONS` must carry one entry per harness, and
   `test_adapter_matrix_covers_every_harness` fails when it does not. This is what
   makes "add a harness" fail loudly until its launch contract is declared, rather
   than passing on a generic default written for somebody else. A missing entry is
   the coverage guard: future harnesses fail without coverage.

The env-key set is the one genuinely per-harness fact asserted here (claude and
codex thread their instrumentation through argv and a settings file with no env,
omp sets two pi display knobs, opencode points at a layered config file). Declaring
it per harness is deliberate: a new harness author has to state what environment
their launch depends on, and a drift in that wiring fails against the declaration.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from swe_mux.adapters import build_agent_adapter
from swe_mux.adapters.base import SpawnOptions
from swe_mux.harness import HARNESSES, HarnessDescriptor


@dataclass(frozen=True)
class _AdapterExpectation:
    """The per-harness launch contract the matrix asserts.

    ``spawn_env_keys`` is the exact set of environment keys the adapter's own
    ``spawn_spec`` sets (not the full session environment, which the manager adds).
    A harness that depends on no environment declares the empty set explicitly, so
    "I set nothing" is a stated fact rather than a forgotten entry.
    """

    spawn_env_keys: frozenset[str]


# One entry per registered harness. A missing entry fails
# `test_adapter_matrix_covers_every_harness`; a wrong entry fails the parametrized
# contract below. Values verified against the live adapters, 2026-08-15.
_ADAPTER_EXPECTATIONS: dict[str, _AdapterExpectation] = {
    "claude": _AdapterExpectation(spawn_env_keys=frozenset()),
    "codex": _AdapterExpectation(spawn_env_keys=frozenset()),
    "omp": _AdapterExpectation(
        spawn_env_keys=frozenset({"PI_FORCE_IMAGE_PROTOCOL", "PI_NO_SYNC_OUTPUT"})
    ),
    "pi": _AdapterExpectation(spawn_env_keys=frozenset()),
    "opencode": _AdapterExpectation(spawn_env_keys=frozenset({"OPENCODE_CONFIG"})),
}


def _adapter(harness: HarnessDescriptor, data_dir: Path):
    """Build the adapter the way the spawn path does, with a fake executable.

    No real CLI is required: this exercises the descriptor-to-adapter wiring and
    the argv/env the harness would be launched with, which is the part that breaks
    silently when a new harness is added.
    """
    return build_agent_adapter(
        harness,
        executable=f"C:/fake/{harness.script_base_name}.cmd",
        args=list(harness.default_args),
        data_dir=data_dir,
        mcp_url="http://127.0.0.1:8765/mcp",
    )


def test_adapter_matrix_covers_every_harness() -> None:
    """The coverage guard: a harness with no expectation entry is a failure.

    This is the half that makes a future harness fail without coverage. Adding one
    to `HARNESSES` without adding its launch contract here fails immediately, the
    same way a new transcript dialect must add a sample record.
    """
    assert set(_ADAPTER_EXPECTATIONS) == set(HARNESSES), (
        "every registered harness needs an _ADAPTER_EXPECTATIONS entry declaring its "
        "launch contract; missing: "
        f"{sorted(set(HARNESSES) - set(_ADAPTER_EXPECTATIONS))}, "
        f"stale: {sorted(set(_ADAPTER_EXPECTATIONS) - set(HARNESSES))}"
    )


@pytest.mark.parametrize("name", sorted(HARNESSES), ids=lambda n: n)
def test_harness_builds_a_launchable_spawn_spec(name: str, tmp_path: Path) -> None:
    """build_agent_adapter -> spawn_spec produces a runnable process for the harness."""
    harness = HARNESSES[name]
    adapter = _adapter(harness, tmp_path / name)
    spec = adapter.spawn_spec(f"sid-{name}", SpawnOptions(cwd=tmp_path, session_id=f"sid-{name}"))

    assert spec.executable == f"C:/fake/{harness.script_base_name}.cmd"
    # The adapter's own env is a stable per-harness fact; the manager layers the
    # session identity vars on top of this.
    assert set(spec.env) == _ADAPTER_EXPECTATIONS[name].spawn_env_keys
    # Every argv element is a string (the PTY host will list2cmdline them).
    assert all(isinstance(token, str) for token in spec.argv)


@pytest.mark.parametrize("name", sorted(HARNESSES), ids=lambda n: n)
def test_harness_resume_spec_targets_the_conversation(name: str, tmp_path: Path) -> None:
    """resume_spec must carry the harness's declared resume tokens and the native id.

    The resume tokens come from the registry (`resume_argv`), so this reads the
    declared fact back rather than hardcoding a per-harness flag - a harness that
    changes how it resumes updates one descriptor field and this follows.
    """
    harness = HARNESSES[name]
    adapter = _adapter(harness, tmp_path / name)
    native_id = f"native-{name}"
    spec = adapter.resume_spec(native_id, SpawnOptions(cwd=tmp_path, session_id=f"sid-{name}"))

    assert native_id in spec.argv, f"{name} resume spec does not target the conversation id"
    for token in harness.resume_argv:
        assert token in spec.argv, f"{name} resume spec is missing declared resume token {token!r}"


@pytest.mark.parametrize("name", sorted(HARNESSES), ids=lambda n: n)
def test_harness_declares_graceful_exit_keys(name: str, tmp_path: Path) -> None:
    """A harness that cannot be asked to exit gracefully would be killed hard every time."""
    adapter = _adapter(HARNESSES[name], tmp_path / name)
    assert adapter.graceful_exit_keys(), f"{name} declares no graceful exit keystrokes"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows command-line quoting")
@pytest.mark.parametrize("name", sorted(HARNESSES), ids=lambda n: n)
def test_harness_argv_survives_windows_conpty_quoting(name: str, tmp_path: Path) -> None:
    """Each harness's argv must round-trip through the exact ConPTY quoting path.

    `PtyHost.spawn` turns structured argv into a command line with
    `subprocess.list2cmdline`, then Windows re-parses it with `CommandLineToArgvW`.
    Codex threads whole TOML fragments with quotes and brackets through `-c`, which
    is precisely the shape that a naive quoting layer corrupts. Asserting the
    round-trip per harness catches a launch that would arrive at the CLI with
    mangled arguments - invisible to an argv-only adapter test, and expensive to
    diagnose from a CLI that simply "started wrong".
    """
    import ctypes
    from ctypes import wintypes

    harness = HARNESSES[name]
    adapter = _adapter(harness, tmp_path / name)
    opts = SpawnOptions(cwd=tmp_path, session_id=f"sid-{name}")
    argv = adapter.spawn_spec(f"sid-{name}", opts).argv
    if not argv:
        pytest.skip(f"{name} launches with no argv")
    cmdline = subprocess.list2cmdline(argv)

    argv_from_cmdline = ctypes.windll.shell32.CommandLineToArgvW
    argv_from_cmdline.restype = ctypes.POINTER(wintypes.LPWSTR)
    argv_from_cmdline.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    count = ctypes.c_int(0)
    parsed_ptr = argv_from_cmdline(cmdline, ctypes.byref(count))
    try:
        parsed = [parsed_ptr[i] for i in range(count.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(parsed_ptr)
    assert parsed == list(argv), (
        f"{name} argv is corrupted by ConPTY command-line quoting: "
        f"{list(argv)!r} -> {cmdline!r} -> {parsed!r}"
    )
