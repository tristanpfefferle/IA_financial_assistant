"""Shared types for conversational loop state machine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class LoopContext:
    """Persisted context for a single active loop."""

    loop_id: str
    step: str
    data: dict[str, Any] = field(default_factory=dict)
    blocking: bool = True


@dataclass(slots=True)
class LoopReply:
    """Reply produced by the loop router or a loop handler."""

    reply: str
    next_loop: LoopContext | None = None
    updates: dict[str, Any] = field(default_factory=dict)
    handled: bool = False
