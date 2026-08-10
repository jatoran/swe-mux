from __future__ import annotations

import pytest

from swe_mux.claude_models import CLAUDE_CONTEXT_WINDOWS, claude_context_window


@pytest.mark.parametrize(
    ("model", "window"),
    [
        ("claude-opus-5", 1_000_000),
        ("claude-opus-4-8", 1_000_000),
        ("claude-fable-5", 1_000_000),
        ("claude-sonnet-5", 1_000_000),
        ("claude-haiku-4-5", 200_000),
        ("claude-haiku-4-5-20251001", 200_000),
    ],
)
def test_known_models_resolve_exactly(model: str, window: int) -> None:
    assert claude_context_window(model) == window


@pytest.mark.parametrize(
    ("model", "window"),
    [
        # The shapes a table always lags: a model newer than this release, a
        # dated snapshot, and a context-variant suffix.
        ("claude-opus-6", 1_000_000),
        ("claude-sonnet-6-20270301", 1_000_000),
        ("claude-opus-5[1m]", 1_000_000),
        ("claude-haiku-5", 200_000),
    ],
)
def test_unlisted_models_fall_back_to_their_family(model: str, window: int) -> None:
    """A missing row must not read as a zero-token context window.

    That is the failure this fallback exists for: `dict.get(model, 0)` made an
    unrecognized model report 0% context used forever, which is indistinguishable
    from a fresh conversation rather than obviously broken.
    """
    assert model not in CLAUDE_CONTEXT_WINDOWS
    assert claude_context_window(model) == window


@pytest.mark.parametrize("model", [None, "", "gpt-5.6-codex", "llama-3"])
def test_non_claude_models_report_unknown(model: str | None) -> None:
    """Only Claude ids get a Claude window; everything else stays 0."""
    assert claude_context_window(model) == 0


def test_haiku_does_not_inherit_the_million_token_default() -> None:
    """Family matching is longest-prefix, so the narrower family wins."""
    assert claude_context_window("claude-haiku-9-9") == 200_000
