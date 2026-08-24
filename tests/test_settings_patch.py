"""Path-scoped edits to a document whose schema this process does not hold.

The property every test here is really about: **an operation cannot touch what it
did not name.** That is what makes a second editor safe on a document the daemon
cannot validate, and it is a claim about the shape of the request rather than
about the care of whoever composed it - so it has to hold for malformed batches,
for partially-applicable ones, and for paths that resolve to the wrong kind of
thing.
"""

from __future__ import annotations

import copy

import pytest

from swe_mux.settings_patch import (
    MAX_OPERATIONS,
    PatchError,
    apply_operations,
    parse_path,
    resolve,
)

# The live rail shape, reduced. `row-2-43lio` is the row from the session that
# motivated this: an operator asking for four arrow buttons to come out of it.
RAIL = {
    "version": 3,
    "items": [{"id": "padArrows", "label": "Arrows"}, {"id": "up", "label": "Up"}],
    "layouts": {
        "mobile": {
            "strip": [
                {"id": "mobile-strip", "items": ["kbdToggle", "paste"]},
                {
                    "id": "row-2-43lio",
                    "items": ["ctrlU", "ctrlC", "padArrows", "up", "down", "left", "right"],
                },
            ]
        }
    },
    "projects": {"p-swe-mux": {"mode": "delta"}},
}


# ------------------------------------------------------------------- paths


def test_a_selector_names_a_row_by_its_own_id() -> None:
    """Positional paths address a different row after a reorder; ids do not."""
    row = resolve(RAIL, "/layouts/mobile/strip/[id=row-2-43lio]")
    assert row["items"][0] == "ctrlU"
    assert resolve(RAIL, "/layouts/mobile/strip/1") == row


def test_a_negative_index_counts_from_the_end() -> None:
    assert resolve(RAIL, "/layouts/mobile/strip/-1")["id"] == "row-2-43lio"


def test_a_pointer_escape_survives_a_key_containing_a_slash() -> None:
    assert parse_path("/a~1b/c") == ["a/b", "c"]
    assert parse_path("/a~0b") == ["a~b"]


def test_a_path_that_does_not_resolve_says_which_step_failed() -> None:
    with pytest.raises(PatchError, match="has no key 'tablet'"):
        resolve(RAIL, "/layouts/tablet/strip")
    with pytest.raises(PatchError, match="row-9"):
        resolve(RAIL, "/layouts/mobile/strip/[id=row-9]/items")
    with pytest.raises(PatchError, match="not an array"):
        resolve(RAIL, "/layouts/mobile/0")


def test_a_path_must_be_rooted() -> None:
    with pytest.raises(PatchError, match="must start with"):
        resolve(RAIL, "layouts/mobile")


# -------------------------------------------------------------- operations


def test_remove_values_takes_the_named_entries_and_nothing_else() -> None:
    """The operation the motivating request needed, and why it is not four removes.

    Index-based removal shifts the indices of every later element, so four
    positional deletes composed against one reading remove the wrong things after
    the first. Naming the values is order-independent and cannot be thrown off.
    """
    document = copy.deepcopy(RAIL)
    result, notes = apply_operations(
        document,
        [
            {
                "op": "remove_values",
                "path": "/layouts/mobile/strip/[id=row-2-43lio]/items",
                "values": ["up", "down", "left", "right"],
            }
        ],
    )
    assert resolve(result, "/layouts/mobile/strip/[id=row-2-43lio]/items") == [
        "ctrlU",
        "ctrlC",
        "padArrows",
    ]
    # The catalog, the other row, the version, and the project override are all
    # untouched - not because the code was careful but because nothing named them.
    assert result["items"] == RAIL["items"]
    other = resolve(result, "/layouts/mobile/strip/[id=mobile-strip]/items")
    assert other == ["kbdToggle", "paste"]
    assert result["projects"] == RAIL["projects"]
    assert result["version"] == 3
    assert "removed 4" in notes[0]


def test_removing_values_that_are_not_there_at_all_is_an_error() -> None:
    """Idempotent is right; silent is not.

    An operation that matched nothing and reported success would let an agent
    tell the operator it removed four buttons that are still on their rail.
    """
    with pytest.raises(PatchError, match="none of"):
        apply_operations(
            copy.deepcopy(RAIL),
            [
                {
                    "op": "remove_values",
                    "path": "/layouts/mobile/strip/[id=row-2-43lio]/items",
                    "values": ["nope"],
                }
            ],
        )


def test_a_partial_match_removes_what_is_there_and_names_what_was_not() -> None:
    result, notes = apply_operations(
        copy.deepcopy(RAIL),
        [
            {
                "op": "remove_values",
                "path": "/layouts/mobile/strip/[id=row-2-43lio]/items",
                "values": ["up", "absent"],
            }
        ],
    )
    assert "up" not in resolve(result, "/layouts/mobile/strip/[id=row-2-43lio]/items")
    assert "not present" in notes[0]


def test_insert_places_a_value_relative_to_one_already_there() -> None:
    result, _ = apply_operations(
        copy.deepcopy(RAIL),
        [
            {
                "op": "insert",
                "path": "/layouts/mobile/strip/[id=row-2-43lio]/items",
                "value": "enter",
                "after": "padArrows",
            }
        ],
    )
    items = resolve(result, "/layouts/mobile/strip/[id=row-2-43lio]/items")
    assert items[items.index("padArrows") + 1] == "enter"


def test_insert_refuses_an_anchor_that_is_not_there() -> None:
    with pytest.raises(PatchError, match="anchor"):
        apply_operations(
            copy.deepcopy(RAIL),
            [
                {
                    "op": "insert",
                    "path": "/layouts/mobile/strip/[id=row-2-43lio]/items",
                    "value": "enter",
                    "after": "gone",
                }
            ],
        )


def test_set_writes_one_value_and_reports_what_was_there() -> None:
    result, notes = apply_operations(
        copy.deepcopy(RAIL), [{"op": "set", "path": "/version", "value": 4}]
    )
    assert result["version"] == 4
    assert "was 3" in notes[0]


def test_remove_takes_one_key_or_one_element() -> None:
    result, _ = apply_operations(
        copy.deepcopy(RAIL), [{"op": "remove", "path": "/projects/p-swe-mux"}]
    )
    assert result["projects"] == {}
    result, _ = apply_operations(
        copy.deepcopy(RAIL), [{"op": "remove", "path": "/layouts/mobile/strip/[id=mobile-strip]"}]
    )
    assert [row["id"] for row in result["layouts"]["mobile"]["strip"]] == ["row-2-43lio"]


# ----------------------------------------------------------------- batching


def test_a_batch_whose_last_operation_fails_applies_none_of_them() -> None:
    """Half-edited is the worst outcome available on an opaque document.

    Nothing downstream can tell a half-applied rail from an intended one - the
    browser normalizes whatever it finds - so a batch is all or nothing, enforced
    by working on a copy the caller never sees unless every operation succeeded.
    """
    document = copy.deepcopy(RAIL)
    with pytest.raises(PatchError, match="operation 1"):
        apply_operations(
            document,
            [
                {"op": "set", "path": "/version", "value": 99},
                {"op": "remove", "path": "/layouts/tablet"},
            ],
        )
    assert document == RAIL


def test_the_input_document_is_never_mutated() -> None:
    document = copy.deepcopy(RAIL)
    apply_operations(document, [{"op": "set", "path": "/version", "value": 7}])
    assert document["version"] == 3


def test_an_unknown_operation_is_refused_rather_than_skipped() -> None:
    with pytest.raises(PatchError, match="remove_values"):
        apply_operations(copy.deepcopy(RAIL), [{"op": "replace_all", "path": "/", "value": {}}])


def test_the_document_root_cannot_be_replaced() -> None:
    """Whole-document replacement wearing a pointer is the thing this refuses."""
    with pytest.raises(PatchError, match="root cannot be edited"):
        apply_operations(copy.deepcopy(RAIL), [{"op": "set", "path": "", "value": {}}])


def test_a_runaway_batch_is_bounded() -> None:
    with pytest.raises(PatchError, match=str(MAX_OPERATIONS)):
        apply_operations(
            copy.deepcopy(RAIL),
            [{"op": "set", "path": "/version", "value": 1}] * (MAX_OPERATIONS + 1),
        )


def test_an_empty_batch_is_refused() -> None:
    with pytest.raises(PatchError, match="non-empty"):
        apply_operations(copy.deepcopy(RAIL), [])


def test_a_result_that_is_not_an_object_is_refused() -> None:
    """A settings domain is always an object, and stays one.

    Unreachable through the store, which coerces a missing or malformed domain to
    `{}` before any operation runs - but this module is the general one and a
    direct caller can hand it a list. Stored, a non-object would be kept and then
    silently ignored by the browser, which is the failure mode with no symptom.
    """
    with pytest.raises(PatchError, match="must stay one"):
        apply_operations(["a", "b"], [{"op": "remove", "path": "/0"}])
