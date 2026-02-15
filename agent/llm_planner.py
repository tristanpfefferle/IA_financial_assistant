"""Optional LLM-based planner.

This planner is intentionally a stub for now and will later use OpenAI tool
calling to produce execution plans for messages that the deterministic planner
does not recognize.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.planner import NoopPlan, Plan


@dataclass(slots=True)
class LLMPlanner:
    """Plan messages with an LLM when deterministic parsing cannot route them."""

    def plan(self, message: str) -> Plan:
        """Return an LLM-generated plan (stubbed while feature is disabled)."""
        _ = message
        return NoopPlan(reply="LLM planner not enabled.")

