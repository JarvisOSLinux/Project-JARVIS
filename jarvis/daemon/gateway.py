"""
JARVIS Gateway - WebSocket server for frontend connections

The Gateway is the communication hub between JARVIS frontends (CLI, Voice, KDE)
and the JARVIS Core. It handles:
- WebSocket connections from multiple clients
- Message routing and protocol handling
- Client session management
- Graceful shutdown
"""

import asyncio
import json
import signal
from typing import Dict, Set, Optional, Callable
from dataclasses import dataclass

from ..core.logger import get_logger
from .protocol import (
    Message, MessageType, ClientSource,
    create_response, create_error, create_pong, create_status_response
)
from .core import JarvisCore

logger = get_logger(__name__)

# Default configuration
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18789


@dataclass
class ClientConnection:
    """Represents a connected client"""
    id: str
    source: ClientSource
    writer: asyncio.StreamWriter
    connected_at: float


class Gateway:
    """
    WebSocket-style Gateway server for JARVIS.

    Uses asyncio TCP with JSON protocol (simpler than full WebSocket,
    easier to implement without external dependencies).
    """

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        """
        Initialize the Gateway.

        Args:
            host: Host to bind to (default: localhost only for security)
            port: Port to listen on
        """
        self.host = host
        self.port = port
        self._server: Optional[asyncio.Server] = None
        self._clients: Dict[str, ClientConnection] = {}
        self._core: Optional[JarvisCore] = None
        self._running = False
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        """Start the Gateway server"""
        logger.info(f"Starting JARVIS Gateway on {self.host}:{self.port}")

        # Initialize core
        self._core = JarvisCore()

        # Create server
        self._server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port
        )

        self._running = True
        logger.info(f"JARVIS Gateway listening on {self.host}:{self.port}")

        # Setup signal handlers for graceful shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        async with self._server:
            await self._shutdown_event.wait()

    async def stop(self) -> None:
        """Stop the Gateway server gracefully"""
        if not self._running:
            return

        logger.info("Stopping JARVIS Gateway...")
        self._running = False

        # Close all client connections
        for client_id, client in list(self._clients.items()):
            try:
                client.writer.close()
                await client.writer.wait_closed()
            except Exception as e:
                logger.debug(f"Error closing client {client_id}: {e}")

        self._clients.clear()

        # Stop server
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        # Shutdown core
        if self._core:
            self._core.shutdown()

        self._shutdown_event.set()
        logger.info("JARVIS Gateway stopped")

    async def _handle_client(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter) -> None:
        """Handle a new client connection"""
        import time
        import uuid

        client_id = str(uuid.uuid4())
        peername = writer.get_extra_info('peername')
        logger.info(f"New connection from {peername}, assigned ID: {client_id[:8]}")

        # Create client connection (source will be updated on first message)
        client = ClientConnection(
            id=client_id,
            source=ClientSource.UNKNOWN,
            writer=writer,
            connected_at=time.time()
        )
        self._clients[client_id] = client

        try:
            while self._running:
                # Read message (newline-delimited JSON)
                try:
                    line = await asyncio.wait_for(
                        reader.readline(),
                        timeout=300.0  # 5 minute timeout
                    )
                except asyncio.TimeoutError:
                    logger.debug(f"Client {client_id[:8]} timed out")
                    break

                if not line:
                    logger.debug(f"Client {client_id[:8]} disconnected")
                    break

                # Parse message
                try:
                    message = Message.from_json(line.decode().strip())
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning(f"Invalid message from {client_id[:8]}: {e}")
                    error_msg = create_error("Invalid message format", "PARSE_ERROR")
                    await self._send_message(writer, error_msg)
                    continue

                # Update client source on first message
                if client.source == ClientSource.UNKNOWN:
                    client.source = message.source
                    if self._core:
                        self._core.register_client(client_id, message.source)

                # Handle message
                response = await self._handle_message(message, client)
                if response:
                    await self._send_message(writer, response)

        except ConnectionResetError:
            logger.debug(f"Client {client_id[:8]} connection reset")
        except Exception as e:
            logger.error(f"Error handling client {client_id[:8]}: {e}", exc_info=True)
        finally:
            # Cleanup
            if client_id in self._clients:
                del self._clients[client_id]
            if self._core:
                self._core.unregister_client(client_id)

            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

            logger.info(f"Client {client_id[:8]} disconnected")

    async def _handle_message(self, message: Message,
                              client: ClientConnection) -> Optional[Message]:
        """
        Handle an incoming message and return a response.

        Args:
            message: Incoming message
            client: Client connection

        Returns:
            Response message or None
        """
        logger.debug(f"Handling {message.type.value} from {client.id[:8]}")

        if message.type == MessageType.PING:
            return create_pong(message.id)

        elif message.type == MessageType.STATUS:
            if self._core:
                status = self._core.get_status()
                return create_status_response(status, message.id)
            return create_error("Core not initialized", "CORE_ERROR", message.id)

        elif message.type == MessageType.QUERY:
            if not self._core:
                return create_error("Core not initialized", "CORE_ERROR", message.id)

            # Process query in thread pool to avoid blocking
            loop = asyncio.get_event_loop()

            def on_approval_needed(approval_msg: Message):
                # Send approval request to client
                asyncio.run_coroutine_threadsafe(
                    self._send_message(client.writer, approval_msg),
                    loop
                )

            response = await loop.run_in_executor(
                None,
                lambda: self._core.process_query(message, on_approval_needed)
            )
            return response

        elif message.type == MessageType.APPROVAL_RESPONSE:
            if self._core:
                self._core.handle_approval_response(message)
            return None  # No direct response needed

        elif message.type == MessageType.CANCEL:
            # TODO: Implement query cancellation
            logger.info(f"Cancel request from {client.id[:8]}")
            return None

        elif message.type == MessageType.DISCONNECT:
            logger.info(f"Disconnect request from {client.id[:8]}")
            return None

        else:
            return create_error(
                f"Unknown message type: {message.type.value}",
                "UNKNOWN_TYPE",
                message.id
            )

    async def _send_message(self, writer: asyncio.StreamWriter,
                            message: Message) -> bool:
        """
        Send a message to a client.

        Args:
            writer: Client's stream writer
            message: Message to send

        Returns:
            True if sent successfully
        """
        try:
            data = message.to_json() + "\n"
            writer.write(data.encode())
            await writer.drain()
            return True
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False

    async def broadcast(self, message: Message,
                        exclude: Optional[Set[str]] = None) -> int:
        """
        Broadcast a message to all connected clients.

        Args:
            message: Message to broadcast
            exclude: Set of client IDs to exclude

        Returns:
            Number of clients message was sent to
        """
        exclude = exclude or set()
        sent_count = 0

        for client_id, client in self._clients.items():
            if client_id not in exclude:
                if await self._send_message(client.writer, message):
                    sent_count += 1

        return sent_count

    @property
    def client_count(self) -> int:
        """Get number of connected clients"""
        return len(self._clients)

    @property
    def is_running(self) -> bool:
        """Check if gateway is running"""
        return self._running


def run_gateway(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """
    Run the Gateway server (blocking).

    This is the main entry point for running the daemon.
    """
    gateway = Gateway(host, port)

    try:
        asyncio.run(gateway.start())
    except KeyboardInterrupt:
        logger.info("Gateway interrupted by user")


# Entry point for running as module
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="JARVIS Gateway Server")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host to bind to")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to listen on")
    args = parser.parse_args()

    run_gateway(args.host, args.port)
