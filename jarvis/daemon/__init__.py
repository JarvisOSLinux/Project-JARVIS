"""
JARVIS Daemon - WebSocket-based service architecture

This module provides the daemon infrastructure for Project JARVIS,
enabling multiple frontends (CLI, Voice, KDE) to connect to a single
running instance via WebSocket.
"""

from .protocol import Message, MessageType
from .gateway import Gateway
from .core import JarvisCore

__all__ = ['Message', 'MessageType', 'Gateway', 'JarvisCore']
