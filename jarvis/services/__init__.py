"""
JARVIS Services - Standalone service components

This module contains services that run as separate processes/daemons
and communicate with the JARVIS Gateway via the protocol.

Services:
- VoiceService: Always-on voice listening with wake word detection
"""

from .voice_service import VoiceService

__all__ = ['VoiceService']
