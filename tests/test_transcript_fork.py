"""The fork writer and the cut points it is aimed at."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swe_mux.transcript_fork import (
    FORK_MAX_SOURCE_BYTES,
    ForkPlan,
    ForkRefused,
    ForkUnsupported,
    fork_supported,
    mint_conversation_id,
    write_fork,
)
from swe_mux.transcript_view import conversation_cut_points

from .support.claude_transcript import SIDECAR_BODY, SIDECAR_NAME, read_records, write_source

FORK_ID = "bbbbbbbb-2222-4a7b-8c9d-0e1f2a3b4c5d"


def _points(path: Path) -> list:
    points = conversation_cut_points(path, "claude")
    assert points is not None
    return points


def _plan(source: Path, cut: int, *, fork_id: str = FORK_ID) -> ForkPlan:
    return ForkPlan(
        backend="claude",
        source_path=source,
        source_conversation_id=source.stem,
        fork_conversation_id=fork_id,
        target_path=source.with_name(f"{fork_id}.jsonl"),
        cut_offset=cut,
        title_marker=f"[branch {fork_id[:8]}]",
    )


def test_only_a_harness_with_a_writer_is_forkable() -> None:
    """Support is declared per dialect, so an unimplemented one refuses rather than guesses."""
    assert fork_supported("claude") is True
    assert fork_supported("codex") is False
    assert fork_supported("shell") is False
    with pytest.raises(ForkUnsupported):
        mint_conversation_id("codex")


def test_cut_points_are_offered_for_every_displayed_message(tmp_path: Path) -> None:
    points = _points(write_source(tmp_path))
    assert [(point.role, point.ordinal) for point in points] == [
        ("user", 0),
        ("assistant", 1),
        ("assistant", 2),
        ("user", 3),
        ("assistant", 4),
    ]
    # Spans are strictly increasing and non-overlapping: a cut names a record
    # boundary, so two messages sharing one would make the same cut mean two things.
    ends = [point.source_end for point in points]
    assert ends == sorted(ends)
    assert all(point.source_start < point.source_end for point in points)


def test_a_reply_with_an_unanswered_tool_call_is_not_a_legal_cut(tmp_path: Path) -> None:
    """The defect this catches is not cosmetic.

    A conversation whose last assistant turn asked for a tool and never received the
    result is rejected by the provider outright, so a fork cut there would not load
    at all. The reply *after* the result is clean, which is why the answer has to be
    per point rather than per conversation.
    """
    points = _points(write_source(tmp_path))
    calling, answering = points[1], points[2]
    assert calling.open_tool_calls == 1
    assert answering.open_tool_calls == 0


def test_a_dialect_with_no_measured_rule_offers_no_cut_points(tmp_path: Path) -> None:
    """`None` rather than an empty list: "cannot say" and "nothing to say" differ.

    A caller that read an empty list as "no legal cuts" would silently disable
    branching; one that read it as "every cut is legal" would write conversations the
    provider rejects. Neither is a defensible default, so the reader declines.
    """
    rollout = tmp_path / "rollout-2026-08-17T12-00-00-cccccccc.jsonl"
    rollout.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "cccccccc"}}) + "\n",
        encoding="utf-8",
    )
    assert conversation_cut_points(rollout, "codex") is None


def test_a_fork_carries_the_prefix_and_leaves_the_source_untouched(tmp_path: Path) -> None:
    source = write_source(tmp_path)
    before = source.read_bytes()
    points = _points(source)
    # Cut after the reply that follows the tool result: everything from the second
    # prompt onward is what this branch is discarding.
    outcome = write_fork(_plan(source, points[2].source_end))

    assert source.read_bytes() == before
    forked = read_records(outcome.path)
    texts = [
        record["message"]["content"]
        for record in forked
        if record.get("type") == "user" and isinstance(record["message"]["content"], str)
    ]
    assert texts == ["first prompt"]
    assert not any(record.get("uuid") in {"u2", "a3"} for record in forked)
    assert outcome.conversation_id == FORK_ID
    assert outcome.records_written == len(forked)


def test_every_record_in_a_fork_claims_the_fork(tmp_path: Path) -> None:
    """A record naming the source conversation inside the fork's file is two
    conversations disagreeing about which one this is."""
    source = write_source(tmp_path)
    outcome = write_fork(_plan(source, _points(source)[-1].source_end))
    identities = {
        record["sessionId"] for record in read_records(outcome.path) if record.get("sessionId")
    }
    assert identities == {FORK_ID}


def test_a_queued_prompt_is_not_inherited(tmp_path: Path) -> None:
    """The one failure mode a branch must not have.

    A `queue-operation` is a prompt the operator staged against the *source* pane.
    Carrying it into the fork would deliver somebody's queued message into a
    conversation they had not yet decided to have.
    """
    source = write_source(tmp_path)
    outcome = write_fork(_plan(source, _points(source)[-1].source_end))
    kinds = [record.get("type") for record in read_records(outcome.path)]
    assert "queue-operation" not in kinds
    assert outcome.records_dropped >= 1


def test_a_record_naming_a_message_the_fork_kept_survives(tmp_path: Path) -> None:
    source = write_source(tmp_path)
    outcome = write_fork(_plan(source, _points(source)[-1].source_end))
    survivors = [
        record for record in read_records(outcome.path) if record.get("type") == "last-prompt"
    ]
    assert [record["leafUuid"] for record in survivors] == ["a2"]


@pytest.mark.parametrize(
    ("record", "kept"),
    [
        ({"type": "last-prompt", "leafUuid": "u1"}, True),
        ({"type": "last-prompt", "leafUuid": "never-written"}, False),
        ({"type": "file-history-delta", "messageId": "u1", "snapshotMessageId": "u1"}, True),
        ({"type": "file-history-delta", "messageId": "u1", "snapshotMessageId": "gone"}, False),
    ],
)
def test_a_record_naming_a_message_the_fork_does_not_have_is_dropped(
    record: dict, kept: bool
) -> None:
    """A checkpoint or recalled prompt pointing at a turn this conversation never had.

    Asserted against the transform rather than through a written fork, because a cut
    always lands on a message boundary and Claude writes these records adjacent to
    the message they name, so no cut currently separates the two. The rule is kept
    anyway: it is what makes the writer correct for a cut that removes a *range*
    rather than a suffix, which is the next thing this module is for.
    """
    from swe_mux.transcript_fork import _transform_claude_record

    plan = _plan(Path("unused.jsonl"), 1)
    result = _transform_claude_record(
        dict(record),
        plan,
        kept_uuids={"u1"},
        mentions_source=False,
        roots=("unused",),
        fork_root="unused",
        sidecar_names=set(),
    )
    assert (result is not None) is kept


def test_a_fork_owns_its_sidecar_files_rather_than_borrowing_them(tmp_path: Path) -> None:
    """Independence from the conversation it came from.

    Claude refers to an oversized tool result by absolute path under the
    conversation's own directory. A fork that copied the reference verbatim reads out
    of the source's directory and keeps working right up until that conversation is
    cleaned up, at which point a branch made weeks earlier starts failing to open its
    own tool output.
    """
    source = write_source(tmp_path)
    outcome = write_fork(_plan(source, _points(source)[-1].source_end))

    copied = outcome.path.parent / FORK_ID / SIDECAR_NAME
    assert copied.read_text(encoding="utf-8") == SIDECAR_BODY
    assert outcome.attachments_copied == 1
    body = outcome.path.read_text(encoding="utf-8")
    assert FORK_ID in body
    assert source.stem not in body


def test_the_fork_marks_its_titles_so_the_cli_has_no_collision_to_break(tmp_path: Path) -> None:
    """On 2026-08-14 a fork and its source shared a title, and the CLI's
    name-collision resolver wrote ~57 MB of generated suffixes into each transcript
    trying to separate them. Owning the fork's title means never handing it a clash."""
    source = write_source(tmp_path)
    outcome = write_fork(_plan(source, _points(source)[-1].source_end))
    titles = [
        record["aiTitle"]
        for record in read_records(outcome.path)
        if record.get("type") == "ai-title"
    ]
    assert titles == [f"Investigate the thing [branch {FORK_ID[:8]}]"]


def test_a_cut_at_the_very_start_is_refused(tmp_path: Path) -> None:
    source = write_source(tmp_path)
    with pytest.raises(ForkRefused) as caught:
        write_fork(_plan(source, 0))
    assert caught.value.code == "empty_prefix"


def test_an_id_that_already_names_a_conversation_is_refused(tmp_path: Path) -> None:
    """Minting over an existing file would overwrite somebody else's conversation."""
    source = write_source(tmp_path)
    source.with_name(f"{FORK_ID}.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ForkRefused) as caught:
        write_fork(_plan(source, _points(source)[-1].source_end))
    assert caught.value.code == "fork_id_taken"


def test_a_pathological_conversation_is_refused_rather_than_stalled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 550 MB rollout exists in the wild, and a Claude title loop once produced
    57 MB of junk in one file. Either must fail as a stated refusal."""
    source = write_source(tmp_path)
    monkeypatch.setattr("swe_mux.transcript_fork.FORK_MAX_SOURCE_BYTES", 10)
    with pytest.raises(ForkRefused) as caught:
        write_fork(_plan(source, _points(source)[-1].source_end))
    assert caught.value.code == "source_too_large"
    assert FORK_MAX_SOURCE_BYTES > 10


def test_a_failed_write_leaves_no_partial_conversation_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A half-written transcript is a conversation the CLI would try to open."""
    source = write_source(tmp_path)
    plan = _plan(source, _points(source)[-1].source_end)

    def explode(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("swe_mux.transcript_fork._copy_sidecars", explode)
    with pytest.raises(OSError):
        write_fork(plan)
    assert not plan.target_path.exists()
    assert not list(source.parent.glob(f".{plan.target_path.name}.*.tmp"))
