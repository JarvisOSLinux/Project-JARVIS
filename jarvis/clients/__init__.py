"""
JARVIS Clients - Frontend clients for connecting to the daemon

This module contains thin client implementations that connect
to the JARVIS daemon via the socket protocol.

Clients:
- CLIClient: Command-line interface client
"""

from .cli_client import CLIClient

__all__ = ['CLIClient']
