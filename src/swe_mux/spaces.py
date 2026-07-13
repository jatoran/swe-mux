from __future__ import annotations

import uuid

from .history import HistoryIndex
from .layouts import normalize_layout
from .models import SpaceRecord


class SpaceManager:
    def __init__(self, history: HistoryIndex) -> None:
        self.history = history
        self.spaces: dict[str, SpaceRecord] = {}

    async def start(self) -> None:
        default = SpaceRecord("default", "Main", 0, normalize_layout(None))
        await self.history.ensure_default_space(default)
        self.spaces = {s.id: s for s in await self.history.list_spaces()}
        for space in self.spaces.values():
            normalized = normalize_layout(space.layout)
            if space.layout != normalized:
                space.layout = normalized
                await self.history.upsert_space(space)

    async def create(self, name: str) -> SpaceRecord:
        space = SpaceRecord(str(uuid.uuid4()), name, len(self.spaces), normalize_layout(None))
        self.spaces[space.id] = space
        await self.history.upsert_space(space)
        return space

    async def update(self, space_id: str, **changes: object) -> SpaceRecord:
        space = self.spaces[space_id]
        expected = changes.pop("layout_revision", None)
        if (
            "layout" in changes
            and expected is not None
            and isinstance(expected, (str, int))
            and int(expected) != space.layout_revision
        ):
            raise ValueError(
                f"stale layout revision: expected {space.layout_revision}, received {expected}"
            )
        for key in (
            "name", "position", "layout", "default_cwd", "default_backend",
            "default_profile_id",
        ):
            if key in changes:
                setattr(space, key, changes[key])
        if "layout" in changes:
            space.layout = normalize_layout(space.layout)
            space.layout_revision += 1
        await self.history.upsert_space(space)
        return space

    async def delete(self, space_id: str) -> None:
        if space_id == "default":
            raise ValueError("the default space cannot be deleted")
        del self.spaces[space_id]
        await self.history.delete_space(space_id)
