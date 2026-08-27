"""Who is reading the pane's console: the agent, its shell, or both.

A session spawned as a shell and then promoted around an agent CLI typed into it
(`backends.md`, "A plain terminal promotes itself") has *two* processes that can
legitimately own the pseudoconsole over its lifetime - the shell that was spawned
into it, and the agent launched from that shell. Exactly one of them may own it at
a time, and the whole launch chain exists to keep the shell blocked for as long as
the agent lives:

    ConPTY -> pwsh -> cmd.exe -> swe-mux.exe (shim) -> claude.exe

Every link in that chain blocks on the next, and measured 2026-08-27 the chain is
correct when nothing interrupts it: a 4-second child kept the whole chain waiting
4.3 seconds. So the failure is never "the wrapper was in the way" - it is "some
middle link stopped waiting while the agent kept the console". When one does, the
shell prints a prompt, its line editor starts reading, and the two readers split
the keystroke stream while both paint. The observed symptom is a composer that
shows none of what was typed, SGR mouse reports echoed as literal text, and the
shell's own history prediction appearing over the agent's UI.

**Why the wrapper stopped waiting in the observed case is not known**, and that is
the second half of what this module is for. Two candidate mechanisms were checked
and one was refuted: an injected `0x03` does *not* become a `CTRL_C_EVENT` through
mux's own pseudoconsole on the measured host (winpty/OpenConsole headless delivers
it as a key event, and `tests/test_shim_console_handoff.py` skips rather than
pretending otherwise), so console interrupts are not how it happened here. The
wrapper also emitted nothing at all - `_promote`/`_demote` are fire-and-forget -
so there was no record that it had started, what it resolved, which child it
spawned, or how it died. `agent_launcher` now reports all four
(`MUX_SHIM_URL`), which is what will answer this the next time it happens.

Detection is therefore deliberately independent of cause. Two signals reach the
same verdict: the wrapper reporting its own child outliving it, and a shell prompt
arriving under a live agent. A wrapper killed hard enough to run neither an
`atexit` nor a console handler emits nothing, and the second signal still catches
it.

The daemon could not previously see any of that. `SessionManager._confirm_agent_exit`
had exactly two outcomes for "a shell prompt appeared under a promoted session" -
demote, or silently give up after its bounded retry - and the retry is what fired,
because the agent was still alive and still writing its transcript. So mux went on
believing it owned a healthy agent pane for as long as the session lived.

This module is the third outcome. It is pure: callers supply the evidence and it
returns a verdict, so the ordering of the rules is testable without a pseudoconsole.
The one function that touches the system (:func:`probe_console_participants`) is
separated below and returns the same evidence shape.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

log = logging.getLogger(__name__)

#: How long after a promotion a shell prompt is still assumed to be a straggler
#: from before the launch rather than evidence about the console. Mirrors
#: ``session.AGENT_EXIT_PROMPT_GRACE_SECONDS``; the two are the same fact and the
#: caller passes its own elapsed time in, so this is the floor rather than a
#: second policy.
PROMOTION_GRACE_SECONDS = 3.0

#: Verdicts, and what a caller owes each one.
#:
#: ``agent_gone``  - the CLI has exited; demote the session (the historical path).
#: ``contended``   - the CLI is alive *and* the shell has the console back. Report
#:                   it; never demote, because the run is not over and dropping the
#:                   agent's identity would lose its transcript binding too.
#: ``unknown``     - no evidence either way. Do nothing, and say nothing.
ConsoleVerdict = Literal["agent_gone", "contended", "unknown"]

#: Why the agent is no longer the console's only reader. Ordered by how much the
#: operator can do about it.
ContentionReason = Literal[
    # The shim's child outlived the shim. Its parent is gone, so nothing is
    # holding the shell back and nothing will reap the agent when the pane ends.
    "agent_orphaned",
    # The agent is alive and inside the pane's process tree, but the shell has
    # printed a prompt anyway: a middle link of the launch chain unblocked.
    "shell_regained_console",
]


@dataclass(frozen=True)
class ConsoleEvidence:
    """What the daemon knows when a shell prompt appears under a promoted session.

    Every field is deliberately three-valued where measurement can fail. ``None``
    means "not measured", never "false": treating an unmeasured process as dead is
    what would turn a slow psutil pass into a spurious demotion, and treating it as
    alive would turn one into a spurious contention report. Both are wrong, and
    saying nothing is always available.
    """

    #: False when the session is not promoted; the caller has nothing to classify.
    backend_is_agent: bool
    #: Seconds since the promotion this prompt is being read against.
    seconds_since_promotion: float
    #: The agent CLI's own pid, from the shim report or the harness's published
    #: CLI state. ``None`` when nothing has told us - a shim-less launch, or a
    #: harness that publishes no state.
    agent_pid: int | None = None
    #: Whether that pid is still running. ``None`` when there was no pid to check.
    agent_alive: bool | None = None
    #: Whether it is still a descendant of the pane's PTY root. ``None`` when the
    #: pid, the root, or the walk was unavailable.
    agent_in_pty_tree: bool | None = None
    #: Whether the transcript has been quiet long enough to corroborate an exit.
    #: The historical signal, and still the only one for a harness with no pid.
    transcript_quiet: bool = False


@dataclass(frozen=True)
class ConsoleVerdictResult:
    verdict: ConsoleVerdict
    #: Stable machine-readable cause, safe to put in an event and to switch on.
    reason: str
    #: Set only for ``contended``, so a consumer can tell the two apart without
    #: re-deriving them from ``reason``.
    contention: ContentionReason | None = None

    @property
    def contended(self) -> bool:
        return self.verdict == "contended"


def classify_shell_prompt(evidence: ConsoleEvidence) -> ConsoleVerdictResult:
    """Decide what a shell prompt under a promoted session means.

    Ordered; first match wins. The ordering is the design:

    1. Not promoted - there is nothing to say, and the caller should not have asked.
    2. Inside the promotion grace window - the launch itself emits a prompt on the
       way past, so a prompt this early is a pipeline straggler.
    3. The agent is alive but no longer under the PTY root - it was orphaned. This
       is checked before the plain liveness rule because it is the more specific
       fact and it names a different repair (nothing will reap this process).
    4. The agent is alive - contention, whatever the transcript says. A live CLI
       plus a shell prompt is two readers, and the transcript cannot refute it.
    5. The agent's pid is known and dead - it exited; demote.
    6. No pid at all - fall back to the transcript, which is what a shim-less
       launch has always been judged on.

    Rule 4 sitting above rule 6 is the whole fix: transcript quiet was previously
    the *only* question, so a live agent that happened to be idle for two seconds
    would have been demoted, and a live agent that was busy produced silence.
    """
    if not evidence.backend_is_agent:
        return ConsoleVerdictResult("unknown", "not_promoted")
    if evidence.seconds_since_promotion < PROMOTION_GRACE_SECONDS:
        return ConsoleVerdictResult("unknown", "within_promotion_grace")
    if evidence.agent_alive is True:
        if evidence.agent_in_pty_tree is False:
            return ConsoleVerdictResult("contended", "agent_orphaned", "agent_orphaned")
        return ConsoleVerdictResult(
            "contended", "shell_regained_console", "shell_regained_console"
        )
    if evidence.agent_alive is False:
        return ConsoleVerdictResult("agent_gone", "agent_process_exited")
    if evidence.transcript_quiet:
        return ConsoleVerdictResult("agent_gone", "transcript_quiet")
    return ConsoleVerdictResult("unknown", "agent_still_writing")


@dataclass(frozen=True)
class ConsoleParticipant:
    """One live process in a session's own process tree."""

    pid: int
    name: str
    parent_pid: int | None
    #: True for the pane's PTY root (the shell or the directly-spawned CLI).
    is_root: bool = False
    #: True when this pid is the promoted agent the daemon believes it has.
    is_agent: bool = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "name": self.name,
            "parent_pid": self.parent_pid,
            "is_root": self.is_root,
            "is_agent": self.is_agent,
        }


@dataclass(frozen=True)
class ConsoleCensus:
    """The measured half of :class:`ConsoleEvidence`, plus what it was read from.

    Reported verbatim on the state-log so an incident can be diagnosed from one
    request rather than from a live `Win32_Process` walk - which is how the
    2026-08-27 case had to be diagnosed, and the reason this exists.
    """

    root_pid: int | None
    agent_pid: int | None
    agent_alive: bool | None
    agent_in_pty_tree: bool | None
    participants: tuple[ConsoleParticipant, ...] = ()
    #: Set when the walk could not be performed at all (no psutil, no root pid,
    #: access denied). Callers must treat a census with an error as no evidence.
    error: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "root_pid": self.root_pid,
            "agent_pid": self.agent_pid,
            "agent_alive": self.agent_alive,
            "agent_in_pty_tree": self.agent_in_pty_tree,
            "participants": [item.snapshot() for item in self.participants],
            "error": self.error,
        }


#: Hard bound on how much of a session's tree the census walks. A pane running a
#: build has hundreds of descendants and this answers a yes/no question about two
#: of them, so the walk is capped rather than complete; the cap is recorded by the
#: participant count falling short of reality rather than by an error, because a
#: truncated census still answers "is the agent in this tree" correctly for any
#: agent near the root, which is where an agent always is.
CENSUS_MAX_PROCESSES = 256


@dataclass
class _WalkState:
    seen: set[int] = field(default_factory=set)
    participants: list[ConsoleParticipant] = field(default_factory=list)


def probe_console_participants(root_pid: int | None, agent_pid: int | None) -> ConsoleCensus:
    """Walk the pane's process tree. Blocking; call this off the event loop.

    Two questions, and only two: is ``agent_pid`` alive, and is it still reachable
    from ``root_pid``. The participant list is diagnostic detail on top of those.

    Every failure is reported as ``error`` with three-valued fields left at
    ``None`` rather than guessed, because :func:`classify_shell_prompt` reads an
    unmeasured process as "say nothing" and a guessed one as grounds to act.
    """
    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil is a hard dependency
        return ConsoleCensus(root_pid, agent_pid, None, None, error="psutil_unavailable")

    agent_alive: bool | None = None
    if agent_pid is not None and agent_pid > 0:
        try:
            agent_alive = psutil.pid_exists(agent_pid) and _is_running(psutil, agent_pid)
        except (psutil.Error, OSError):
            agent_alive = None

    if root_pid is None or root_pid <= 0:
        return ConsoleCensus(root_pid, agent_pid, agent_alive, None, error="no_root_pid")

    try:
        root = psutil.Process(root_pid)
    except psutil.NoSuchProcess:
        # The pane's own root is gone. Nothing is left to be a descendant *of*, so
        # an agent that is still alive is orphaned by definition.
        return ConsoleCensus(
            root_pid,
            agent_pid,
            agent_alive,
            False if agent_alive else None,
            error="root_gone",
        )
    except (psutil.Error, OSError) as exc:
        return ConsoleCensus(root_pid, agent_pid, agent_alive, None, error=f"root_error:{exc!s}")

    state = _WalkState()
    state.participants.append(
        ConsoleParticipant(
            pid=root_pid,
            name=_process_name(psutil, root),
            parent_pid=None,
            is_root=True,
            is_agent=agent_pid == root_pid,
        )
    )
    state.seen.add(root_pid)
    try:
        for child in root.children(recursive=True):
            if len(state.participants) >= CENSUS_MAX_PROCESSES:
                break
            pid = int(child.pid)
            if pid in state.seen:
                continue
            state.seen.add(pid)
            state.participants.append(
                ConsoleParticipant(
                    pid=pid,
                    name=_process_name(psutil, child),
                    parent_pid=_parent_pid(psutil, child),
                    is_agent=agent_pid == pid,
                )
            )
    except (psutil.Error, OSError) as exc:
        return ConsoleCensus(
            root_pid,
            agent_pid,
            agent_alive,
            None,
            participants=tuple(state.participants),
            error=f"walk_error:{exc!s}",
        )

    in_tree: bool | None = None
    if agent_pid is not None and agent_pid > 0:
        in_tree = agent_pid in state.seen
    return ConsoleCensus(
        root_pid,
        agent_pid,
        agent_alive,
        in_tree,
        participants=tuple(state.participants),
    )


def _is_running(psutil_module: Any, pid: int) -> bool:
    """Whether ``pid`` is a live process rather than an unreaped zombie.

    ``pid_exists`` alone is true for a POSIX zombie, which is a CLI that has
    already exited and whose parent has not collected it - exactly the state that
    must read as "gone" rather than as contention.
    """
    try:
        process = psutil_module.Process(pid)
        return bool(process.status() != psutil_module.STATUS_ZOMBIE)
    except psutil_module.NoSuchProcess:
        return False
    except (psutil_module.Error, OSError):
        # Access denied on a live process. It exists; that is what was asked.
        return True


def _process_name(psutil_module: Any, process: Any) -> str:
    try:
        return str(process.name())
    except (psutil_module.Error, OSError):
        return ""


def _parent_pid(psutil_module: Any, process: Any) -> int | None:
    try:
        return int(process.ppid())
    except (psutil_module.Error, OSError, ValueError):
        return None
