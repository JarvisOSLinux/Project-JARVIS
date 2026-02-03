"""
JARVIS CLI Client - Thin command-line client for daemon

This client connects to the JARVIS daemon, sends queries,
and displays responses. It's a stateless, fire-and-forget client.
"""

import asyncio
import sys
from typing import Optional

from ..core.logger import get_logger
from ..daemon.protocol import (
    Message, MessageType, ClientSource,
    create_query, create_status_request, create_approval_response
)

logger = get_logger(__name__)

# Default daemon connection settings
DEFAULT_DAEMON_HOST = "127.0.0.1"
DEFAULT_DAEMON_PORT = 18789


class CLIClient:
    """
    Thin CLI client for JARVIS daemon.

    Connects to daemon, sends a query, receives response, and exits.
    """

    def __init__(self, host: str = DEFAULT_DAEMON_HOST,
                 port: int = DEFAULT_DAEMON_PORT):
        """
        Initialize CLI client.

        Args:
            host: Daemon host
            port: Daemon port
        """
        self.host = host
        self.port = port
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    async def connect(self) -> bool:
        """
        Connect to the daemon.

        Returns:
            True if connected successfully
        """
        try:
            self._reader, self._writer = await asyncio.open_connection(
                self.host, self.port
            )
            return True
        except ConnectionRefusedError:
            logger.error(f"Could not connect to daemon at {self.host}:{self.port}")
            logger.error("Is the daemon running? Start it with: jarvis daemon start")
            return False
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from daemon"""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    async def send_query(self, text: str) -> Optional[str]:
        """
        Send a query to the daemon and return the response.

        Args:
            text: Query text

        Returns:
            Response text or None on error
        """
        if not await self.connect():
            return None

        try:
            # Send query
            query = create_query(text, ClientSource.CLI, audio_response=False)
            await self._send_message(query)

            # Wait for response (with approval handling)
            while True:
                response = await self._receive_message(timeout=120.0)

                if not response:
                    logger.error("No response from daemon (timeout)")
                    return None

                if response.type == MessageType.RESPONSE:
                    return response.text

                elif response.type == MessageType.ERROR:
                    logger.error(f"Daemon error: {response.error_message}")
                    return f"Error: {response.error_message}"

                elif response.type == MessageType.APPROVAL_REQUEST:
                    # Handle approval request
                    approved = await self._handle_approval_request(response)

                    # Send approval response
                    approval_msg = create_approval_response(
                        approved=approved,
                        reply_to=response.reply_to or response.id,
                        source=ClientSource.CLI
                    )
                    await self._send_message(approval_msg)

                    # Continue waiting for final response
                    continue

                elif response.type == MessageType.PARTIAL:
                    # Stream partial response
                    if response.text:
                        print(response.text, end='', flush=True)
                    continue

                else:
                    logger.warning(f"Unexpected message type: {response.type}")

        finally:
            await self.disconnect()

    async def get_status(self) -> Optional[dict]:
        """
        Get daemon status.

        Returns:
            Status dictionary or None on error
        """
        if not await self.connect():
            return None

        try:
            status_msg = create_status_request(ClientSource.CLI)
            await self._send_message(status_msg)

            response = await self._receive_message(timeout=5.0)
            if response and response.type == MessageType.STATUS_RESPONSE:
                return response.data

            return None

        finally:
            await self.disconnect()

    async def _handle_approval_request(self, request: Message) -> bool:
        """
        Handle approval request from daemon.

        Args:
            request: Approval request message

        Returns:
            True if approved, False if denied
        """
        data = request.data or {}
        command = data.get('command', 'unknown command')
        security_level = data.get('security_level', 'unknown')

        # Display approval prompt
        print(f"\n{'='*60}")
        print(f"APPROVAL REQUIRED")
        print(f"{'='*60}")
        print(f"Command: {command}")
        print(f"Security Level: {security_level}")
        print(f"{'='*60}")

        # Get user input
        try:
            response = input("Allow this command? (yes/no): ").strip().lower()
        except EOFError:
            # Non-interactive mode
            logger.warning("Non-interactive mode, denying command")
            return False

        # Parse response
        approval_keywords = ['yes', 'y', 'allow', 'approve', 'ok', 'okay']
        return any(word in response for word in approval_keywords)

    async def _send_message(self, message: Message) -> bool:
        """Send message to daemon"""
        if not self._writer:
            return False

        try:
            data = message.to_json() + "\n"
            self._writer.write(data.encode())
            await self._writer.drain()
            return True
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False

    async def _receive_message(self, timeout: float = 30.0) -> Optional[Message]:
        """Receive message from daemon"""
        if not self._reader:
            return None

        try:
            line = await asyncio.wait_for(
                self._reader.readline(),
                timeout=timeout
            )
            if line:
                return Message.from_json(line.decode().strip())
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.error(f"Error receiving message: {e}")

        return None


def query(text: str, host: str = DEFAULT_DAEMON_HOST,
          port: int = DEFAULT_DAEMON_PORT) -> Optional[str]:
    """
    Send a query to the daemon (synchronous wrapper).

    Args:
        text: Query text
        host: Daemon host
        port: Daemon port

    Returns:
        Response text or None on error
    """
    client = CLIClient(host, port)
    return asyncio.run(client.send_query(text))


def status(host: str = DEFAULT_DAEMON_HOST,
           port: int = DEFAULT_DAEMON_PORT) -> Optional[dict]:
    """
    Get daemon status (synchronous wrapper).

    Args:
        host: Daemon host
        port: Daemon port

    Returns:
        Status dictionary or None
    """
    client = CLIClient(host, port)
    return asyncio.run(client.get_status())


def is_daemon_running(host: str = DEFAULT_DAEMON_HOST,
                      port: int = DEFAULT_DAEMON_PORT) -> bool:
    """
    Check if daemon is running.

    Args:
        host: Daemon host
        port: Daemon port

    Returns:
        True if daemon is running
    """
    try:
        return status(host, port) is not None
    except Exception:
        return False


def is_daemon_running_quiet(host: str = DEFAULT_DAEMON_HOST,
                            port: int = DEFAULT_DAEMON_PORT) -> bool:
    """
    Check if daemon is running without logging errors.

    Used for startup checks where we expect it might not be running.

    Args:
        host: Daemon host
        port: Daemon port

    Returns:
        True if daemon is running
    """
    import asyncio
    import socket

    # Quick socket check - faster than full protocol handshake
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False
