"""Registry for loop handlers."""

from __future__ import annotations

from collections.abc import Iterable

from agent.loops.base import Loop


class LoopRegistry:
    """In-memory loop registry."""

    def __init__(self) -> None:
        self._loops: dict[str, Loop] = {}

    def register(self, loop: Loop) -> None:
        self._loops[loop.id] = loop

    def get(self, loop_id: str) -> Loop | None:
        return self._loops.get(loop_id)

    def list_loops(self) -> Iterable[Loop]:
        return tuple(self._loops.values())
