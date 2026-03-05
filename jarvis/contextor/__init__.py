"""
Contextor subsystem for JARVIS.

Manages long-term memory — the LLM decides what to store and recall,
while this adapter handles the transport to the contextor backend.

The adapter supports two modes:
1. Local fallback (file-based, works immediately)
2. Binary backend via stdio (planned, for the Rust contextor binary)
"""

from .adapter import ContextorAdapter

__all__ = ["ContextorAdapter"]
