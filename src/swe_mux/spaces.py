from __future__ import annotations

import uuid

from .history import HistoryIndex
from .models import SpaceRecord


class SpaceManager:
    def __init__(self, history: HistoryIndex) -> None:
        self.history = history
        self.spaces: dict[str, SpaceRecord] = {}

    async def start(self) -> None:
        default = SpaceRecord("default", "Main", 0, None)
        await self.history.ensure_default_space(default)
        self.spaces = {s.id: s for s in await self.history.list_spaces()}

    async def create(self, name: str) -> SpaceRecord:
        space = SpaceRecord(str(uuid.uuid4()), name, len(self.spaces), None)
        self.spaces[space.id] = space
        await self.history.upsert_space(space)
        return space

    async def update(self, space_id: str, **changes: object) -> SpaceRecord:
        space = self.spaces[space_id]
        for key in ("name", "position", "layout", "default_cwd", "default_backend"):
            if key in changes:
                setattr(space, key, changes[key])
        await self.history.upsert_space(space)
        return space

    async def delete(self, space_id: str) -> None:
        if space_id == "default":
            raise ValueError("the default space cannot be deleted")
        del self.spaces[space_id]
        await self.history.delete_space(space_id)
