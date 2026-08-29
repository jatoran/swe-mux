"""Re-ask the real CLIs what the registry claims about their model flags.

Every declaration in `harness.py` under `model_selection` is a statement about a
program mux does not ship, made on a day someone ran it. The rest of the suite
tests mux's side of that statement perfectly and can never notice when the other
side moves - a renamed flag, a listing whose columns changed, a CLI that starts
validating (or stops). This tier is the part that can.

What it checks, and why each one is here rather than assumed:

* **The catalogue parses, and everything it lists is launchable.** This is the
  strongest single assertion available, because it plays the two declarations off
  against each other using the CLI's own data: if the vocabulary refuses an id the
  CLI itself publishes, an agent gets a list it cannot spawn from.
* **A model the CLI cannot have is refused, and the refusal names it.** Four of
  the five do this before sending anything. Codex is the exception and is asserted
  *as* the exception, because that asymmetry is the entire argument for the
  observed-model check existing at all.

Not a gate. It needs the CLIs installed and authenticated, and it spawns
processes. Run it deliberately:

    uv run pytest tests/test_live_model_flag.py -m live_model_flag

**It measures the CLIs' model validation, not pane lifecycle.** Each check runs
the CLI's own headless path, which is deterministic and needs no terminal; how a
*pane* dies when a launch flag is rejected is `spawn_probe`'s question and is
covered by its own tests.

No provider quota is consumed. Every invocation either reads a local listing or
carries a model the CLI is expected to refuse before any request leaves the host -
and the one CLI that does send is expected to be refused by the provider on the
first turn, which is exactly the fact being pinned.
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass

import pytest

from swe_mux.harness import (
    agent_harnesses,
    descriptor,
    model_catalog,
    model_selection,
    resolve_launch_model,
)
from swe_mux.model_catalog import catalog_for
from swe_mux.shim_paths import which_real

pytestmark = pytest.mark.live_model_flag

#: A name no vendor will ever ship, long enough that no fuzzy matcher can find a
#: neighbour for it. The whole point is that every CLI must fail to resolve it.
BOGUS_MODEL = "definitely-not-a-real-model-xyz"
#: Generous: a cold CLI on Windows behind Defender has been measured past 20s.
RUN_TIMEOUT_SECONDS = 90.0


#: Complaints a CLI makes about an *argument* rather than about a model. If one
#: of these appears, the declared flag has been renamed or removed - which the
#: vocabulary check upstream cannot see, because it validates the value.
FLAG_COMPLAINTS = ("unknown option", "unrecognized option", "unknown argument", "unknown flag")


@dataclass(frozen=True, slots=True)
class HeadlessRun:
    """How one CLI is asked to attempt a turn without a terminal.

    Declared per harness *here* rather than in the registry: it describes each
    CLI's non-interactive entry point, which the product never uses, so putting it
    in `harness.py` would add an axis nothing outside this file reads.
    """

    #: Arguments before the model flag. Codex and opencode gate a headless turn on
    #: a subcommand; the other three take a top-level print flag.
    prefix: tuple[str, ...]
    #: The prompt, positionally, after the flags. Every one of these takes it there.
    prompt: tuple[str, ...]
    #: True when the CLI resolves the model against its own catalogue and refuses
    #: before anything is sent. False means it starts and finds out later, which
    #: is the case no spawn-time probe can catch.
    validates_locally: bool
    #: True when the failure names the model it was given. False is a finding, not
    #: an omission: a pane that died this way explains nothing to its operator.
    names_the_model: bool


# Measured 2026-08-29 on this host, one row per CLI, each from a real run:
#
#   claude    exit 1, "[claude-code:unrecognized_model]", nothing sent
#   codex     exit 1, but only after "Model metadata for `<bogus>` not found.
#             Defaulting to fallback metadata", a started session, and the
#             provider's 400 on the first turn
#   omp       exit 1, 'Model "<bogus>" not found'
#   pi        exit 1, 'Model "<bogus>" not found. Use --list-models…'
#   opencode  exit 1, an opaque `UnknownError` / "Unexpected server error" that
#             never names the model - which is why mux refuses a bare name itself
#             rather than forwarding one and letting this be the explanation
HEADLESS = {
    "claude": HeadlessRun(("-p",), ("say ok",), True, True),
    "codex": HeadlessRun(("exec",), ("say ok",), False, True),
    "omp": HeadlessRun(("-p",), ("say ok",), True, True),
    "pi": HeadlessRun(("-p",), ("say ok",), True, True),
    "opencode": HeadlessRun(("run",), ("say ok",), True, False),
}


def _executable(harness: str) -> str:
    """The real CLI, or a skip. Never a shim.

    Resolution goes through `which_real` and not `shutil.which` because the daemon
    prepends `~/.mux/bin` to PATH and writes a shim for every harness - a plain
    lookup finds the shim, and running the shim re-enters the agent launcher.

    The stem fallback is not cosmetic: the registry names `codex.exe`, and an npm
    install on Windows provides `codex.CMD` and no `.exe` at all, so the declared
    name alone silently skips the one harness whose row here matters most.
    """
    name = descriptor(harness).executable
    resolved = which_real(name) or which_real(name.removesuffix(".exe"))
    if not resolved:
        pytest.skip(f"{harness} is not installed on this host")
    return resolved


def _run(harness: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - resolved real executable, fixed args
        [_executable(harness), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=RUN_TIMEOUT_SECONDS,
        check=False,
    )


@pytest.mark.parametrize("harness", sorted(agent_harnesses()))
def test_every_model_the_cli_lists_is_one_mux_would_launch_it_on(harness: str) -> None:
    """The two declarations are played off against each other on live data.

    A vocabulary that refuses ids the CLI itself publishes hands an agent a list
    it cannot spawn from, which is the loop this feature exists to close. It has
    already caught one real bug in review: an id shape written from opencode's
    two-part ids dropped 462 of omp's 470 entries, all of which route a provider
    through a second slash.
    """
    if model_catalog(harness) is None:
        pytest.skip(f"{harness} publishes no model listing")
    _executable(harness)
    result = asyncio.run(catalog_for(harness, refresh=True))
    assert result.models, f"{result.command} listed nothing: {result.error}"
    unlaunchable = [
        model for model in result.models if resolve_launch_model(harness, model) != model
    ]
    assert not unlaunchable, (
        f"{result.command} lists models {harness}'s declared vocabulary refuses: "
        f"{unlaunchable[:5]}"
    )


@pytest.mark.parametrize("harness", sorted(agent_harnesses()))
def test_the_cli_still_refuses_a_model_it_cannot_have(harness: str) -> None:
    """Whether a CLI validates its own model flag is what the layers are sized to.

    A CLI that starts refusing gains nothing here; a CLI that *stops* refusing
    silently moves a startup failure to the first turn, where only the observed
    model can find it. Either direction is a real change to how much the spawn
    probe can promise, so it is asserted rather than remembered.
    """
    run = HEADLESS[harness]
    selection = model_selection(harness)
    assert selection is not None
    completed = _run(harness, *run.prefix, selection.argv[0], BOGUS_MODEL, *run.prompt)
    output = completed.stdout.lower()
    assert completed.returncode != 0, (
        f"{harness} accepted {BOGUS_MODEL!r} and exited 0: {completed.stdout[-400:]}"
    )
    # A renamed or removed flag also exits nonzero, and would otherwise pass this
    # test while breaking every launch. It is a different failure and gets a
    # different sentence.
    complaint = [text for text in FLAG_COMPLAINTS if text in output]
    assert not complaint, (
        f"{harness} does not know {selection.argv[0]} any more ({complaint[0]}): "
        f"{completed.stdout[-400:]}"
    )
    if run.names_the_model:
        assert BOGUS_MODEL in output, (
            f"{harness} failed without naming the model it was given: "
            f"{completed.stdout[-400:]}"
        )
    if run.validates_locally:
        # No session, no request: the pane dies at startup, which is what makes
        # the spawn probe able to explain it.
        assert "fallback metadata" not in output, (
            f"{harness} has started accepting unknown models - it now fails on the "
            f"first turn instead of at startup, and its row here should say so: "
            f"{completed.stdout[-400:]}"
        )
    else:
        # Codex: the failure is the provider's, arriving after the session has
        # already started. Pinned so the day it starts validating is visible,
        # because that day the spawn probe can promise more than it does now.
        assert "fallback metadata" in output or "not supported" in output, (
            f"{harness} no longer starts on an unknown model - the spawn probe can "
            f"now catch it, and its row here should say so: {completed.stdout[-400:]}"
        )


def test_the_headless_table_covers_every_registered_harness() -> None:
    """A harness added to the registry with no row here would silently skip.

    The same failure the adapter matrix exists to stop, one layer out: the tier
    would keep passing while saying nothing at all about the new CLI.
    """
    assert set(HEADLESS) == set(agent_harnesses())
