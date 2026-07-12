from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SessionState = Literal["starting", "running", "working", "idle", "awaiting", "exited", "crashed"]


@dataclass(slots=True)
class GitState:
    branch: str | None = None
    dirty: int = 0
    ahead: int = 0
    behind: int = 0


@dataclass(slots=True)
class SessionRecord:
    id: str
    name: str
    space_id: str
    backend: str
    native_session_id: str
    cwd: str
    exe: str
    args: list[str]
    auto_named: bool = True
    pid: int = -1
    created_at: float = field(default_factory=time.time)
    state: SessionState = "starting"
    state_detail: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    context_window: int = 0
    context_pct: float = 0.0
    last_activity_ts: float = field(default_factory=time.time)
    git: GitState = field(default_factory=GitState)
    pinned_attention: bool = False
    broadcast: bool = False

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SpaceRecord:
    id: str
    name: str
    position: int
    layout: dict[str, Any] | None = None
    default_cwd: str | None = None
    default_backend: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MuxEvent:
    ts: float
    session_id: str | None
    source: str
    type: str
    payload: dict[str, Any]

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)
