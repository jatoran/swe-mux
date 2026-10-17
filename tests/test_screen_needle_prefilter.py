"""The needle prefilter changes what a classification costs, never what it answers.

Classification ran every marker regex over the whole 32 KiB tail, case-insensitively,
to prove that phrases most screens do not show were absent: about 1 ms of a 2.2 ms
classification (`DAEMON_LOAD_ISOLATION.md` item 6). `_casefolded` proves the same
absences with substring tests, and the predicates still run wherever a needle is
present. These tests hold the answer fixed: the prefiltered classifier against the
same classifier with the prefilter switched off, over tails built from every rule's
own phrases in mixed case, with escapes, digits and the characters the case folds
disagree on.
"""

from __future__ import annotations

import random
import re
import string

import pytest

from swe_mux import session as session_module
from swe_mux.session import PTY_RULES, _casefolded, pty_tail_explain

# Phrases that match (or nearly match) each rule, mined from the rules themselves
# so a new rule is exercised without editing this file.
_LITERAL_PHRASES = sorted(set(session_module._LITERAL_PREDICATES.values()))
_EXTRA_PHRASES = [
    "3 tasks still running",
    "12 shells still running",
    "still running",
    "waiting for 2 background tasks",
    "waiting for background task",
    "Select a model  enter to select",
    "Resume a conversation",
    "Are you sure you want to continue connecting (yes/no/[fingerprint])?",
    "user@host's password:",
    "Connection to host closed.",
    "client_loop: send disconnect: Broken pipe",
    "π > (sub) ▶",
    "Working...",
    "❯ ",
    "> ",
]
_NOISE = [
    "\x1b[38;5;45m",
    "\x1b[0m",
    "\x1b[2K",
    "\x1b[1G",
    "\x1b[8G",
    "\x1b]0;claude - working\x07",
    "\r\n",
    "\n",
    "\r",
    "\t",
    "  ",
    "\x01",
    "0123",
    "İ",  # the three characters the two case folds disagree on
    "ı",
    "ſ",
    "K",  # Kelvin sign: folds to k under both
    " ",
    "\x85",
]


def _mangle_case(text: str, rng: random.Random) -> str:
    out = []
    for character in text:
        roll = rng.random()
        if character in string.ascii_letters and roll < 0.3:
            out.append(character.swapcase())
        elif character == "i" and roll < 0.33:
            out.append(rng.choice(["İ", "ı"]))
        elif character == "s" and roll < 0.33:
            out.append("ſ")
        elif character == "k" and roll < 0.33:
            out.append("K")
        else:
            out.append(character)
    return "".join(out)


def _tail(rng: random.Random) -> str:
    parts: list[str] = []
    for _ in range(rng.randint(1, 30)):
        roll = rng.random()
        if roll < 0.35:
            phrase = rng.choice(_LITERAL_PHRASES + _EXTRA_PHRASES)
            parts.append(_mangle_case(phrase, rng) if rng.random() < 0.4 else phrase)
        elif roll < 0.7:
            parts.append(rng.choice(_NOISE))
        else:
            alphabet = string.ascii_letters + " .:-"
            parts.append("".join(rng.choice(alphabet) for _ in range(rng.randint(1, 30))))
    return "".join(parts)


def _explain_both(
    monkeypatch: pytest.MonkeyPatch, tail: str, **kwargs: object
) -> tuple[object, object]:
    fast = pty_tail_explain(tail, **kwargs)  # type: ignore[arg-type]
    with monkeypatch.context() as patch:
        patch.setattr(session_module, "_casefolded", lambda _normalized: None)
        slow = pty_tail_explain(tail, **kwargs)  # type: ignore[arg-type]
    return fast, slow


def test_the_prefilter_never_changes_an_explanation(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = random.Random(20261017)
    backends = [None, "claude", "codex", "shell", "omp", "pi", "opencode"]
    statuses = [None, "busy", "waiting", "idle"]
    matched_rules: set[str] = set()
    for _ in range(3000):
        tail = _tail(rng)
        backend = rng.choice(backends)
        status = rng.choice(statuses)
        fast, slow = _explain_both(monkeypatch, tail, backend=backend, cli_state_status=status)
        assert fast == slow, f"prefilter changed the answer for {tail!r} ({backend}, {status})"
        matched_rules.update(
            str(item["id"]) for item in fast["rules"] if item["matched"]  # type: ignore[index]
        )
    # The corpus must actually reach the rules the prefilter skips, or this proves
    # nothing about them.
    needled = {
        rule.id
        for rule, needles in zip(PTY_RULES, session_module._PTY_RULE_NEEDLES, strict=True)
        if needles
    }
    assert len(needled & matched_rules) >= len(needled) - 1, sorted(needled - matched_rules)


def test_the_case_folds_agree_outside_the_three_exceptions() -> None:
    """`_casefolded` is only sound if this table is complete; recompute it."""
    sample = "".join(chr(code) for code in range(0x3000))
    for letter in string.ascii_lowercase:
        for found in set(re.findall(letter, sample, re.IGNORECASE)):
            if found.lower() != letter:
                assert found in session_module._FOLD_EXCEPTIONS, f"U+{ord(found):04X}"
    assert _casefolded("plain text") == "plain text"
    for exception in session_module._FOLD_EXCEPTIONS:
        assert _casefolded(f"esc to cancel {exception}") is None


def test_every_needle_is_present_in_what_its_rule_matches() -> None:
    """A declared needle that a match can lack would hide a real match."""
    for rule, needles in zip(PTY_RULES, session_module._PTY_RULE_NEEDLES, strict=True):
        for phrase in _LITERAL_PHRASES + _EXTRA_PHRASES:
            match = rule.predicate.search(phrase)
            if match is None:
                continue
            folded = match.group(0).lower()
            for needle in needles:
                assert needle in folded, (rule.id, needle, phrase)
