"""
Dispatch subsystem for JARVIS.

Handles concurrent task execution through the dispatch binary,
goal tracking for user requests, and async event merging for
dual-input (user messages + dispatch signals).
"""

from .adapter import DispatchAdapter
from .event_merger import Event, EventMerger
from .goal_manager import Goal, GoalManager

__all__ = [
    "DispatchAdapter",
    "GoalManager",
    "Goal",
    "EventMerger",
    "Event",
]
