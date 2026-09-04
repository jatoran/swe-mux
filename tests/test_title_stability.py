"""The title-lifecycle stability a session snapshot carries for its generated title.

`_title_stability` reads the `stability` out of the `title_lifecycle` evidence the
titler records on a title annotation, so a client can mark an auto-title that is still
converging rather than reading its churn as a bug. Legacy titles (no evidence) and
non-title annotations report no stability.
"""

from __future__ import annotations

import json

from swe_mux.routes.sessions import _title_stability


def _evidence(*entries: dict[str, object]) -> str:
    return json.dumps(list(entries), separators=(",", ":"))


def test_reads_stability_from_title_lifecycle_evidence() -> None:
    assert (
        _title_stability(_evidence({"kind": "title_lifecycle", "stability": "provisional"}))
        == "provisional"
    )
    assert (
        _title_stability(_evidence({"kind": "title_lifecycle", "stability": "settled"}))
        == "settled"
    )


def test_ignores_evidence_that_is_not_the_title_lifecycle_entry() -> None:
    assert _title_stability(_evidence({"kind": "something_else", "stability": "settled"})) is None
    assert (
        _title_stability(
            _evidence(
                {"kind": "other"},
                {"kind": "title_lifecycle", "stability": "provisional"},
            )
        )
        == "provisional"
    )


def test_legacy_and_malformed_evidence_report_no_stability() -> None:
    assert _title_stability(None) is None
    assert _title_stability("") is None
    assert _title_stability("not json") is None
    assert _title_stability(json.dumps({"kind": "title_lifecycle"})) is None  # not a list
    assert _title_stability(_evidence({"kind": "title_lifecycle"})) is None  # no stability key
    assert _title_stability(123) is None  # type: ignore[arg-type]
