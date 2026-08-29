"""Choosing a model at launch, and telling afterwards whether the choice took.

Three layers, and each exists because the one before it provably cannot answer:

* **The vocabulary** refuses a name a CLI would die on, without ever consulting a
  list of released models - a list lags every vendor and would refuse models that
  work.
* **The catalogue** (`test_model_catalog.py`) says what this machine actually has,
  and is advisory for the same reason.
* **The agreement check** here compares what a session reports running against
  what its launch asked for, which is the only layer that can catch a fuzzy match
  that landed somewhere else or a CLI that never validated the flag at all.

All five harnesses were measured against their own CLIs on 2026-08-29; the
evidence lives beside each declaration in `harness.py`, and the live tier
(`test_live_model_flag.py`) re-asks the CLIs rather than trusting these strings.
"""

from __future__ import annotations

import pytest

from swe_mux.harness import (
    HARNESSES,
    ModelSelection,
    model_agreement,
    model_selection,
    normalize_model_name,
    resolve_launch_model,
    strip_model_args,
)


def test_every_agent_harness_declares_how_its_model_is_chosen() -> None:
    """`None` is a legal answer and no harness needs it any more.

    It was the answer for omp, pi, and opencode until their CLIs were measured,
    and all three do take a model flag - so the product had been refusing a
    supported thing on three of five harnesses. The declaration stays per harness
    because the *vocabularies* genuinely differ; what is no longer acceptable is
    an unanswered one.
    """
    for name, harness in HARNESSES.items():
        if name == "shell":
            continue
        assert harness.model_selection is not None, name


def test_a_normalized_model_name_can_never_become_a_second_flag() -> None:
    """The value lands in argv beside a flag; that is the whole threat model."""
    assert normalize_model_name("  Claude Opus 5 ") == "claude-opus-5"
    assert normalize_model_name("anthropic/Claude-Sonnet-4-5") == (
        "anthropic/claude-sonnet-4-5"
    )
    # pi's thinking suffix and omp's routed provider both survive.
    assert normalize_model_name("openai-codex/gpt-5.4:high") == "openai-codex/gpt-5.4:high"
    assert normalize_model_name("openrouter/~anthropic/claude-opus-latest") == (
        "openrouter/~anthropic/claude-opus-latest"
    )
    # Interior whitespace is joined rather than refused - that is how a spoken
    # "claude opus 5" becomes an id - so what has to be impossible is the token
    # *starting* like a flag, and every one of these does.
    for hostile in ("--model", "-m", "--settings x", "", "   ", "-opus"):
        assert normalize_model_name(hostile) == "", hostile


def test_each_vocabulary_asks_the_question_its_own_cli_asks() -> None:
    """A shared rule would be wrong in one direction or the other for someone.

    Claude and Codex have real namespaces, so a name from another vendor is
    recognizably wrong and refusing it saves a pane that would die at startup.
    opencode accepts `provider/model` and nothing else. omp and pi fuzzy-match
    anything, so there is no vocabulary to check and pretending otherwise would
    refuse working models.
    """
    # namespaced: aliases, ids, and a spoken family-plus-version form.
    assert resolve_launch_model("claude", "opus") == "opus"
    assert resolve_launch_model("claude", "opus 5") == "claude-opus-5"
    assert resolve_launch_model("claude", "claude-sonnet-4-5") == "claude-sonnet-4-5"
    assert resolve_launch_model("claude", "gpt-5.4") is None
    assert resolve_launch_model("codex", "gpt-5.6-terra") == "gpt-5.6-terra"
    assert resolve_launch_model("codex", "opus") is None

    # qualified: one slash, both halves present.
    assert resolve_launch_model("opencode", "anthropic/claude-sonnet-4-5") == (
        "anthropic/claude-sonnet-4-5"
    )
    for refused in ("sonnet", "anthropic/", "/claude"):
        assert resolve_launch_model("opencode", refused) is None, refused

    # pattern: anything model-shaped, in any of the spellings its CLI documents.
    for accepted in ("opus", "gpt-5.4", "openai/gpt-5.4", "openai-codex/gpt-5.4:high"):
        assert resolve_launch_model("omp", accepted) == accepted, accepted
        assert resolve_launch_model("pi", accepted) == accepted, accepted
    # Shape is still enforced, so the argv guarantee survives the loose vocabulary.
    assert resolve_launch_model("omp", "--dangerously-skip-permissions") is None


def test_a_vocabulary_declaration_must_be_internally_consistent() -> None:
    """The three fields are not independent, and a wrong pairing is silent.

    A `pattern` selection carrying an id namespace would read as if it checked
    one; an unverifiable alias that is not an alias could never be reached.
    """
    with pytest.raises(ValueError):
        ModelSelection(argv=(), aliases=frozenset(), id_prefixes=("x-",))
    with pytest.raises(ValueError):
        ModelSelection(argv=("--model",), aliases=frozenset(), id_prefixes=())
    with pytest.raises(ValueError):
        ModelSelection(
            argv=("--model",), aliases=frozenset(), id_prefixes=("x-",), vocabulary="pattern"
        )
    with pytest.raises(ValueError):
        ModelSelection(
            argv=("--model",),
            aliases=frozenset({"fast"}),
            id_prefixes=("x-",),
            unverifiable_aliases=frozenset({"slow"}),
        )


def test_agreement_reads_what_ran_against_what_was_asked_for() -> None:
    """Containment, not equality: every accepted spelling is shorter than the id.

    An alias, a fuzzy pattern, and a qualified id whose provider the harness
    reports in a separate field would all fail an equality check, and calling all
    three a divergence would make the reading worthless.
    """
    assert model_agreement("claude", "opus", "anthropic", "claude-opus-5") == "agreed"
    assert model_agreement("omp", "gpt-5.4", "openai", "openai/gpt-5.4") == "agreed"
    # opencode reports the provider beside the model rather than inside it.
    assert model_agreement(
        "opencode", "anthropic/claude-sonnet-4-5", "anthropic", "claude-sonnet-4-5"
    ) == "agreed"
    # pi's thinking level is a setting, not part of any id, and is never reported.
    assert model_agreement("pi", "openai/gpt-5.4:high", "openai", "gpt-5.4") == "agreed"


def test_agreement_names_the_fuzzy_match_that_landed_somewhere_else() -> None:
    """The failure only this layer can see, on the harnesses that can produce it.

    omp and pi resolve a pattern against their own catalogue, so a request can
    quietly run a different real model; Codex does not validate its flag at all
    and dies on the provider's 400 at the first turn. None of that is visible to
    a launch-time check or a spawn-time probe.
    """
    assert model_agreement("omp", "opus", "anthropic", "claude-sonnet-4-5") == "divergent"
    assert model_agreement("codex", "gpt-5.6-terra", "openai", "gpt-4") == "divergent"


def test_a_session_that_has_not_answered_yet_is_pending_rather_than_wrong() -> None:
    """Silence is not disagreement, and this is the ordinary state of a new pane.

    Every harness reports its model only once a turn has produced usage, so a
    freshly spawned session has nothing to compare. Reporting that as a divergence
    would fire on every single spawn.
    """
    assert model_agreement("claude", "opus", None, None) == "pending"
    assert model_agreement("claude", "opus", "anthropic", "") == "pending"
    # A launch that named no model has no question to answer.
    assert model_agreement("claude", "", "anthropic", "claude-opus-5") == "pending"
    # ...and neither does a harness with no declared selection.
    assert model_agreement("shell", "opus", "anthropic", "claude-opus-5") == "pending"


def test_a_mode_is_unverifiable_rather_than_divergent() -> None:
    """`opusplan` runs two models by design, so no observed id can confirm it.

    Reported apart from `divergent` deliberately. A check that cried wrong on a
    session doing exactly what it was told stops being read, and this one is worth
    nothing if it is not believed.
    """
    assert model_agreement("claude", "opusplan", "anthropic", "claude-sonnet-5") == (
        "unverifiable"
    )
    assert model_agreement("claude", "default", "anthropic", "claude-opus-5") == (
        "unverifiable"
    )
    # The plain family alias is still checked - only the modes are exempt.
    assert model_agreement("claude", "opus", "anthropic", "claude-sonnet-5") == "divergent"


def test_the_requests_model_still_replaces_what_the_new_harnesses_profiles_set() -> None:
    """Adding three vocabularies must not lose the precedence rule.

    Two `--model` flags on one command line is a per-CLI coin toss, so a profile's
    model is stripped rather than left to fight the request's.
    """
    assert strip_model_args("omp", ["--model", "opus", "--thinking", "high"]) == (
        ["--thinking", "high"]
    )
    assert strip_model_args("omp", ["--model=opus"]) == []
    assert strip_model_args("opencode", ["-m", "anthropic/claude-opus-5", "--auto"]) == (
        ["--auto"]
    )
    assert strip_model_args("pi", ["--model", "gpt-5.4:high"]) == []
    # omp's role models are not the session's model and must survive untouched: a
    # request naming a model must not silently unset a profile's `--smol`/`--plan`.
    assert strip_model_args("omp", ["--smol", "haiku", "--model", "opus"]) == (
        ["--smol", "haiku"]
    )


def test_a_model_flag_stays_out_of_reserved_argv_for_the_new_harnesses() -> None:
    """A launch profile pinning a model is supported and must stay savable."""
    from swe_mux.harness import reserved_launch_arg_conflict

    for name in ("omp", "pi", "opencode"):
        selection = model_selection(name)
        assert selection is not None
        for flag in selection.argv:
            assert reserved_launch_arg_conflict(name, [flag, "x"]) is None, (name, flag)
