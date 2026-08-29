"""Reading a harness's own model listing, and refusing to guess at one.

The fixtures below are trimmed captures of what the three CLIs actually printed
on 2026-08-29, decoration included, because the thing being tested is a parser
against real output rather than against a format someone remembered. The live
tier (`test_live_model_flag.py`) re-runs the CLIs and re-checks these shapes; this
file is what fails in the default gate when the parser breaks.

The catalogue is advisory everywhere it is used. Nothing here decides whether a
model may be launched - `test_model_selection.py` owns that - because a CLI's
list lags every vendor release and refusing a model for being absent from one
would refuse models that work.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from swe_mux import model_catalog
from swe_mux.harness import ModelCatalog, resolve_launch_model
from swe_mux.harness import model_catalog as declared_catalog

# `opencode models`: one `provider/model` per line, no decoration at all.
OPENCODE_OUTPUT = """opencode/big-pickle
openai/gpt-5.4
openai/gpt-5.6-terra
anthropic/claude-sonnet-4-5
"""

# `omp models --json`: `selector` is the exact string `--model` takes, and omp
# routes a provider through a second slash and a tilde.
OMP_OUTPUT = """{"models":[
{"provider":"openai-codex","id":"gpt-5.4","selector":"openai-codex/gpt-5.4"},
{"provider":"openrouter","id":"~anthropic/claude-opus-latest",
 "selector":"openrouter/~anthropic/claude-opus-latest"},
{"provider":"openrouter","id":"anthropic/claude-sonnet-4.5:batch",
 "selector":"openrouter/anthropic/claude-sonnet-4.5:batch"}
]}"""

# `pi --list-models`: a whitespace-aligned table under a `provider  model  …`
# header. The id is the first two columns joined by a slash.
PI_OUTPUT = """provider      model                context  max-out  thinking  images
openai-codex  gpt-5.3-codex-spark  128K     128K     yes       no
openai-codex  gpt-5.4              272K     128K     yes       yes
"""


def _parse(harness: str, text: str) -> tuple[str, ...]:
    catalog = declared_catalog(harness)
    assert catalog is not None, harness
    return model_catalog.parse_models(catalog, text)


def test_each_declared_format_reads_its_own_clis_real_output() -> None:
    assert _parse("opencode", OPENCODE_OUTPUT) == (
        "opencode/big-pickle",
        "openai/gpt-5.4",
        "openai/gpt-5.6-terra",
        "anthropic/claude-sonnet-4-5",
    )
    assert _parse("omp", OMP_OUTPUT) == (
        "openai-codex/gpt-5.4",
        "openrouter/~anthropic/claude-opus-latest",
        "openrouter/anthropic/claude-sonnet-4.5:batch",
    )
    assert _parse("pi", PI_OUTPUT) == (
        "openai-codex/gpt-5.3-codex-spark",
        "openai-codex/gpt-5.4",
    )


def test_everything_a_catalogue_lists_is_something_the_harness_would_accept() -> None:
    """The two declarations have to agree, and nothing else checks that they do.

    A vocabulary that refuses ids the CLI itself publishes would hand an agent a
    list it cannot spawn from - the exact loop this feature exists to close. The
    tilde-and-second-slash selectors are why: an id shape written from opencode's
    two-part ids would have refused 462 of omp's 470 entries.
    """
    for harness, text in (
        ("opencode", OPENCODE_OUTPUT),
        ("omp", OMP_OUTPUT),
        ("pi", PI_OUTPUT),
    ):
        for model in _parse(harness, text):
            assert resolve_launch_model(harness, model) == model, (harness, model)


def test_decoration_is_stripped_and_furniture_is_never_mistaken_for_a_model() -> None:
    """A table border passing for a model is worse than missing one entirely.

    A CLI that does not believe it is being piped prints colour; one that draws a
    table prints box glyphs. Both were observed in these listings.
    """
    decorated = "\x1b[1m\x1b[36mopenai\x1b[39m\x1b[22m/gpt-5.4\n"
    assert _parse("opencode", decorated) == ("openai/gpt-5.4",)
    furniture = "┌───────┬───────┐\n│ model │ ctx   │\n└───────┴───────┘\n"
    assert _parse("opencode", furniture) == ()


def test_a_listing_whose_shape_moved_returns_nothing_rather_than_wrong_rows() -> None:
    """Guessing at an unrecognized layout returns *fewer* models, which reads as
    a small account rather than as a broken parser. The header check is what makes
    pi's table parseable instead of guessed, so a header that moved is an empty
    answer - and `catalog_for` pairs an empty answer with the command that
    produced it, which is a diagnosis.
    """
    assert _parse("pi", "vendor  name  context\nopenai-codex  gpt-5.4  272K\n") == ()
    assert _parse("omp", "not json at all") == ()
    assert _parse("omp", '{"data":[{"selector":"a/b"}]}') == ()


def test_claude_and_codex_report_having_no_listing_rather_than_no_models() -> None:
    """"Nothing to ask" and "asked and got nothing" must never read the same.

    Neither CLI has a command that lists models, and an empty list with no
    explanation would read as an unauthenticated machine.
    """
    for name in ("claude", "codex"):
        assert declared_catalog(name) is None, name
        result = asyncio.run(model_catalog.catalog_for(name))
        assert result.models == ()
        assert result.error is not None and "no command that lists models" in result.error


@pytest.mark.asyncio
async def test_a_probe_runs_once_per_ttl_and_a_refresh_re_asks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """These are subprocesses, and an agent choosing a model asks more than once."""
    calls: list[list[str]] = []

    async def fake_run(argv: Any, **_kwargs: Any) -> Any:
        calls.append(list(argv))
        from swe_mux.bounded_subprocess import ProcessOutcome

        return ProcessOutcome(0, OPENCODE_OUTPUT.encode(), b"", False, False, 1.0)

    monkeypatch.setattr(model_catalog, "run_bounded", fake_run)
    monkeypatch.setattr("swe_mux.shim_paths.which_real", lambda _name: "C:/bin/opencode")
    model_catalog.clear_cache()
    try:
        first = await model_catalog.catalog_for("opencode")
        second = await model_catalog.catalog_for("opencode")
        assert first.models == second.models and len(calls) == 1
        # The command is reported whether or not it was re-run, so a thin answer
        # can be reproduced by hand instead of argued about.
        assert first.command.endswith("models")
        await model_catalog.catalog_for("opencode", refresh=True)
        assert len(calls) == 2
    finally:
        model_catalog.clear_cache()


@pytest.mark.asyncio
async def test_a_cli_that_ran_and_said_nothing_useful_names_its_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty catalogue is a diagnosis, and the diagnosis needs the command.

    Told only "no models", an operator cannot tell an unauthenticated host from a
    listing whose format moved - and the second one is mux's bug.
    """

    async def fake_run(_argv: Any, **_kwargs: Any) -> Any:
        from swe_mux.bounded_subprocess import ProcessOutcome

        return ProcessOutcome(1, b"no providers are authenticated", b"", False, False, 1.0)

    monkeypatch.setattr(model_catalog, "run_bounded", fake_run)
    monkeypatch.setattr("swe_mux.shim_paths.which_real", lambda _name: "C:/bin/opencode")
    model_catalog.clear_cache()
    try:
        result = await model_catalog.catalog_for("opencode")
        assert result.models == () and result.exit_code == 1
        assert result.error is not None
        assert "models" in result.error and "no providers" in result.error
    finally:
        model_catalog.clear_cache()


@pytest.mark.asyncio
async def test_an_uninstalled_cli_says_so_instead_of_listing_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("swe_mux.shim_paths.which_real", lambda _name: None)
    model_catalog.clear_cache()
    try:
        result = await model_catalog.catalog_for("opencode")
        assert result.error is not None and "not installed" in result.error
    finally:
        model_catalog.clear_cache()


def test_a_catalog_declaration_must_carry_the_command_that_lists() -> None:
    with pytest.raises(ValueError):
        ModelCatalog(argv=(), format="qualified_lines")


def test_narrowing_and_suggesting_work_off_the_cached_list() -> None:
    """One substring rule over an already-fetched list, rather than three search
    flags spelled three ways - and one of the three CLIs has no search flag at all.
    """
    models = _parse("omp", OMP_OUTPUT)
    assert model_catalog.matches(models, "CLAUDE") == (
        "openrouter/~anthropic/claude-opus-latest",
        "openrouter/anthropic/claude-sonnet-4.5:batch",
    )
    assert model_catalog.matches(models, "") == models
    # A suggestion the harness would itself refuse would send a caller round the
    # same loop twice, so only launchable ids are offered.
    assert model_catalog.suggest("omp", "sonnet", models) == (
        "openrouter/anthropic/claude-sonnet-4.5:batch",
    )
    assert model_catalog.suggest("omp", "nothing-like-this", models) == ()
