from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SpawnSpec:
    """A platform-neutral description of a process to run in a PTY.

    Adapters keep arguments structured.  Turning ``argv`` into the command-line
    representation required by a particular PTY belongs to the platform host.
    """

    executable: str
    argv: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class SpawnOptions:
    cwd: Path
    exe: str | None = None
    args: list[str] = field(default_factory=list)
    session_id: str | None = None


class BackendAdapter(Protocol):
    name: str
    # True when the CLI itself reports conversation replacement over the daemon's
    # hook ingress (Claude's SessionStart hook). For such backends the filesystem
    # transcript-switch heuristic is never consulted: the CLI's own report is
    # strictly stronger evidence, and the heuristic is the one mechanism that can
    # latch a session onto a sibling's conversation in a shared cwd.
    reports_conversation_rollover: bool
    # True when mux dictates the conversation id at spawn, so
    # ``record.native_session_id`` is authoritative from the first moment and only
    # an exact id match may bind a transcript (Claude's injected ``--session-id``).
    # False for backends that mint their own conversation id: they carry the mux
    # session id as a placeholder until discovery, so binding has to go through
    # corroborated elimination instead. Do not infer this from the *shape* of the
    # id — both kinds are UUIDs, and treating a placeholder as authoritative leaves
    # the session permanently unobserved.
    assigns_conversation_id: bool

    def spawn_spec(self, sid: str, opts: SpawnOptions) -> SpawnSpec: ...
    def resume_spec(self, native_id: str, opts: SpawnOptions) -> SpawnSpec: ...
    def resume_continues_conversation(self, recorded_cwd: str, target_cwd: str) -> bool:
        """Whether resuming at ``target_cwd`` continues the recorded conversation.

        True means the CLI reopens the same conversation, under the same id, in the
        same transcript file: the resumed pane continues an agent run that already
        has a history row and must inherit it rather than open a second entry over
        one file. False means the resume lands on a genuinely new conversation, which
        earns its own row and a ``resume`` lineage edge.

        Only the adapter can answer this, because the answer is the CLI's own
        transcript-resolution rule: Claude resolves by working directory, so the
        answer depends on where the resume runs, while Codex resolves by thread id
        and reopens the original rollout wherever the pane runs.
        """
        ...

    def transcript_path(self, native_id: str, cwd: Path) -> Path | None: ...
    def graceful_exit_keys(self) -> str: ...
    def recent_transcripts(self, cwd: Path, created_at: float) -> list[tuple[float, Path, str]]: ...
    async def await_transcript(
        self, native_id: str, cwd: Path, created_at: float, stop: asyncio.Event
    ) -> Path | None: ...
    def transcript_native_id(self, path: Path) -> str | None: ...
    def cleanup(self, session_id: str) -> None: ...
    def session_env(self, session_id: str) -> Mapping[str, str]: ...
    def configure(self, executable: str, args: list[str]) -> None: ...
    def media_reference(self, path: Path) -> str: ...
