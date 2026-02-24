"""Conversation loops package."""

from agent.loops.default_loops import build_default_loops
from agent.loops.registry import LoopRegistry


def build_default_registry() -> LoopRegistry:
    registry = LoopRegistry()
    for loop in build_default_loops():
        registry.register(loop)
    return registry
