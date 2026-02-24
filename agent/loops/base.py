"""Loop protocol abstraction."""

from __future__ import annotations

from typing import Any, Protocol

from agent.loops.types import LoopContext, LoopReply


class Loop(Protocol):
    """Conversation loop contract."""

    id: str
    blocking: bool

    def handle(
        self,
        message: str,
        ctx: LoopContext,
        *,
        services: Any,
        profile_id: Any,
        user_id: Any,
    ) -> LoopReply:
        """Handle one message for the active loop."""

    def can_enter(
        self,
        global_state: dict[str, Any],
        services: Any,
        profile_id: Any,
        user_id: Any,
    ) -> bool:
        """Return whether this loop can be entered from current global state."""
