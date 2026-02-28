"""
Dispatch subsystem for JARVIS.

Handles concurrent task execution through the dispatch binary,
goal tracking for user requests, and async event merging for
dual-input (user messages + dispatch signals).
"""

from .adapter import DispatchAdapter
from .goal_manager import GoalManager, Goal
from .event_merger import EventMerger, Event

__all__ = [
    'DispatchAdapter',
    'GoalManager',
    'Goal',
    'EventMerger',
    'Event',
]
