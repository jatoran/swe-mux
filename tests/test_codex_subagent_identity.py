"""A pane's identity survives its own agent spawning subagent threads.

Codex 0.149 made subagents first-class *threads*: `collaborationspawn_agent`
gives each one its own conversation id, its own rollout file, and its own
`agent-turn-complete` notify fired under the root's hook credentials. Measured
live on 2026-08-23, that combined with a second defect to strand a pane:

- Its own root `SessionStart` was refused as `foreign_process_startup`, because
  the rollover gate asked whether the id it held merely *looked* like a
  conversation id. A harness that mints its own id carries the mux session id as
  a placeholder, and mux session ids are UUIDs too, so a fresh pane read as
  already bound. A refusal never continues into binding, so the pane stayed
  unbound: no transcript observer, no tokens, no context reading, for its whole
  life.
- Twelve minutes later a subagent finished, and its notify was the first event
  willing to bind. The pane adopted a thread that was already over, closed the
  root turn on it, and filtered its own CLI's next 94 hooks - the genuine turn
  end among them - as a foreign conversation. It sat green while its agent
  worked for another nine minutes.

These tests pin each rule that now stands between those two events.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from swe_mux.event_bus import EventBus
from swe_mux.harness import HARNESSES
from swe_mux.models import SessionRecord
from swe_mux.observation import (
    apply_hook_observation,
    child_thread_ids,
    conversation_rollover_decision,
    foreign_conversation_hook_id,
    session_hook_event_scope,
)
from swe_mux.session import Session

#: The mux session id. A UUID, like every mux session id, which is exactly why a
#: shape test could not tell it from a conversation Codex had reported.
PANE = "e5d0c5cd-55db-4611-af38-5920d1ef9038"
#: The thread Codex actually runs for this pane.
ROOT = "01a02f74-8c11-7d12-90df-0416c85ec0dd"
#: A subagent thread the root spawned, with its own id and its own turn end.
CHILD = "01a02f79-07da-7a93-8c8d-7752f2fd6b2e"
#: A second one, to prove the rule is about lineage rather than about one id.
SIBLING = "01a02f79-22f9-7200-acf5-b5859e44c1eb"


def codex_pane(native_id: str = PANE) -> Any:
    """A Codex session as the daemon holds one just after promotion.

    `native_session_id` is the placeholder a self-minting harness carries until
    its CLI reports the real thread, which is the state both defects lived in.
    """
    record = SessionRecord(
        PANE, "codex-pane", "default", "codex", native_id, ".", "codex.exe", [], state="idle"
    )
    record.spawn_backend = "codex"
    record.spawn_native_session_id = PANE
    record.agent_run_id = PANE
    record.run_cwd = "."
    session = cast(
        Any,
        SimpleNamespace(
            record=record,
            agent_lifecycle_id=None,
            state_source_priority=-1,
            status_health_counters={},
            state_transitions=[],
            # The identity rules ask the adapter whether mux named this
            # conversation at spawn. Read from the real registry, because
            # answering the Claude default here is precisely the mistake under
            # test: it is what makes a placeholder read as a bound conversation.
            adapter=SimpleNamespace(
                name="codex",
                assigns_conversation_id=HARNESSES["codex"].assigns_conversation_id,
                reports_conversation_rollover=HARNESSES["codex"].reports_conversation_rollover,
                resolves_transcript_by_cwd=HARNESSES["codex"].resolves_transcript_by_cwd,
            ),
        ),
    )
    session.transition = lambda state, detail, **kw: Session.transition(  # type: ignore[attr-defined]
        session, state, detail, **kw
    )
    session.publish_update = lambda: None
    return session


def start(conversation: str = ROOT) -> dict[str, Any]:
    """Codex's root SessionStart. It always reports `startup`: the CLI has no
    in-place conversation replacement to report anything else for."""
    return {"session_id": conversation, "source": "startup", "cwd": "."}


def subagent_tool(child: str, parent: str = ROOT) -> dict[str, Any]:
    """A subagent-scoped tool hook. It names the parent conversation and the
    child thread separately, which is what makes the child knowable at all."""
    return {"session_id": parent, "agent_id": child, "agent_type": "default"}


def turn_end(conversation: str) -> dict[str, Any]:
    """Codex's `notify`, which fires for every thread and marks none of them."""
    return {"session_id": conversation, "turn_id": "01a02f79-0842-7460-b31e-53d2349d9dc2"}


# ------------------------------------------------- a fresh pane binds its own id


def test_a_fresh_codex_pane_start_is_a_binding_not_a_refused_rollover() -> None:
    # The regression: the gate shape-tested the placeholder, read a fresh pane as
    # bound, and sent its own SessionStart down the rollover path, where
    # `source: "startup"` is correctly refused as a foreign process. Nothing is a
    # rollover until something is bound.
    session = codex_pane()
    decision = conversation_rollover_decision(session, "SessionStart", start())
    assert decision == (None, None, None)


async def test_a_fresh_codex_pane_binds_from_its_own_session_start() -> None:
    session = codex_pane()
    await apply_hook_observation(session, "SessionStart", start(), EventBus())
    assert session.record.native_session_id == ROOT
    assert session.agent_lifecycle_id == ROOT


def test_a_bound_pane_still_refuses_a_foreign_startup() -> None:
    # The rule the gate was written for survives the fix: once bound, a root
    # `startup` naming a different conversation is another process announcing
    # itself, and adopting it hands this pane's identity to a child.
    session = codex_pane(native_id=ROOT)
    decision = conversation_rollover_decision(session, "SessionStart", start(CHILD))
    assert decision.roll_to is None
    assert decision.refused == CHILD
    assert decision.refusal_reason == "foreign_process_startup"


# ------------------------------------------------------ a child thread is a child


async def test_a_child_thread_is_learned_from_the_hook_that_names_it() -> None:
    session = codex_pane()
    events = EventBus()
    await apply_hook_observation(session, "SessionStart", start(), events)
    await apply_hook_observation(session, "PreToolUse", subagent_tool(CHILD), events)
    assert child_thread_ids(session) == {CHILD}
    # And the scope rules can then recognise an event that only *names* it, which
    # the payload alone never could.
    assert session_hook_event_scope(session, "agent-turn-complete", turn_end(CHILD)) == "subagent"
    assert session_hook_event_scope(session, "agent-turn-complete", turn_end(ROOT)) == "root"


async def test_a_childs_turn_end_does_not_end_the_root_turn() -> None:
    session = codex_pane()
    events = EventBus()
    await apply_hook_observation(session, "SessionStart", start(), events)
    await apply_hook_observation(session, "UserPromptSubmit", {"session_id": ROOT}, events)
    await apply_hook_observation(session, "PreToolUse", subagent_tool(CHILD), events)
    assert session.record.state == "working"

    await apply_hook_observation(session, "agent-turn-complete", turn_end(CHILD), events)

    assert session.record.state == "working"
    assert session.record.native_session_id == ROOT


async def test_the_roots_own_turn_end_still_ends_the_turn() -> None:
    # The other half of the rule, and the one that would make a too-broad filter
    # obvious: a pane whose turn never closes is as broken as one that closes early.
    session = codex_pane()
    events = EventBus()
    await apply_hook_observation(session, "SessionStart", start(), events)
    await apply_hook_observation(session, "UserPromptSubmit", {"session_id": ROOT}, events)
    await apply_hook_observation(session, "PreToolUse", subagent_tool(CHILD), events)
    await apply_hook_observation(session, "agent-turn-complete", turn_end(CHILD), events)

    await apply_hook_observation(session, "agent-turn-complete", turn_end(ROOT), events)

    assert session.record.state == "idle"


async def test_a_childs_hooks_are_not_counted_as_a_foreign_conversation() -> None:
    # `foreign_hook_ignored` means "something else is wearing this session's
    # credentials". Every pane that runs subagents would otherwise report it
    # continuously, burying the signal under ordinary traffic.
    session = codex_pane()
    events = EventBus()
    await apply_hook_observation(session, "SessionStart", start(), events)
    await apply_hook_observation(session, "PreToolUse", subagent_tool(CHILD), events)
    await apply_hook_observation(session, "PreToolUse", subagent_tool(SIBLING), events)
    await apply_hook_observation(session, "agent-turn-complete", turn_end(CHILD), events)

    assert foreign_conversation_hook_id(session, turn_end(CHILD)) is None
    assert session.status_health_counters.get("foreign_hook_ignored", 0) == 0
    # A conversation this session never spawned is still foreign.
    assert foreign_conversation_hook_id(session, turn_end("11111111-2222-4333-8444-555555555555"))


# --------------------------------------- the backstop, when no child hook arrived


async def test_a_turn_end_contradicting_the_witnessed_root_is_ignored() -> None:
    # The child registry needs the child to have run a tool first. A subagent that
    # finishes without one is still not this pane's turn ending, and the root
    # hooks have already said which conversation this pane is running.
    session = codex_pane()
    events = EventBus()
    await apply_hook_observation(session, "UserPromptSubmit", {"session_id": ROOT}, events)
    assert session.record.state == "working"

    await apply_hook_observation(session, "agent-turn-complete", turn_end(CHILD), events)

    assert session.record.state == "working"
    assert session.record.native_session_id == PANE
    ledger = [entry["kind"] for entry in session.state_transitions]
    assert "foreign_thread_turn_end_ignored" in ledger


async def test_a_turn_end_binds_when_nothing_has_witnessed_a_root() -> None:
    # The compatibility path this rule must not break. Codex's `notify` is not a
    # lifecycle hook and is not subject to the CLI's hook trust review, so on a
    # pane whose hooks are disabled or untrusted it is the only signal there is -
    # and with no root hook to contradict it, it binds exactly as it always did.
    session = codex_pane()
    await apply_hook_observation(session, "agent-turn-complete", turn_end(ROOT), EventBus())
    assert session.record.native_session_id == ROOT
    assert session.record.state == "idle"


async def test_a_turn_end_never_witnesses_a_root_for_itself() -> None:
    # Otherwise the first subagent to finish would corroborate its own claim, and
    # the backstop above would pass it through for the rest of the session.
    session = codex_pane()
    events = EventBus()
    await apply_hook_observation(session, "agent-turn-complete", turn_end(CHILD), events)
    assert session.record.native_session_id == CHILD  # bound: nothing contradicted it

    session = codex_pane()
    await apply_hook_observation(session, "UserPromptSubmit", {"session_id": ROOT}, events)
    await apply_hook_observation(session, "agent-turn-complete", turn_end(CHILD), events)
    await apply_hook_observation(session, "agent-turn-complete", turn_end(SIBLING), events)
    assert session.record.native_session_id == PANE
