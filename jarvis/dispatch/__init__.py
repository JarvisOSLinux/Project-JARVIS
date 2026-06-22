"""
Dispatch subsystem — Python interface layer for the Rust dispatch engine.

This package is NOT the orchestrator itself. The Rust ``dispatch`` binary
(a separate repo/crate) is the execution engine that spawns MCP tool calls
in parallel, tracks PIDs, and fires signals (INIT/EXIT/REMIND/WAIT/KILL).

This Python package wraps that binary:

- ``adapter.py``       — subprocess lifecycle, MCP JSON-RPC over stdio
- ``discovery.py``     — semantic + keyword tool search via dmcp
- ``dmcp_registry.py`` — dmcp CLI wrappers (install, tools, config)
- ``goal_manager.py``  — tracks user goals and links them to dispatch PIDs
- ``event_merger.py``  — merges voice/CLI/socket/dispatch events
- ``transport.py``     — MCP transport type resolution
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
