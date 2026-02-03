"""
JARVIS Daemon Protocol

Defines the message protocol for communication between frontends and the daemon.
All messages are JSON-encoded with a standardized structure.
"""

import json
import uuid
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List
from datetime import datetime


class MessageType(Enum):
    """Types of messages in the JARVIS protocol"""

    # Client -> Daemon
    QUERY = "query"                    # User query/command
    APPROVAL_RESPONSE = "approval"     # Response to approval request
    CANCEL = "cancel"                  # Cancel current operation
    STATUS = "status"                  # Request daemon status
    PING = "ping"                      # Keep-alive ping

    # Daemon -> Client
    RESPONSE = "response"              # Final response to query
    PARTIAL = "partial"                # Streaming/partial response
    APPROVAL_REQUEST = "approval_req"  # Request user approval
    ERROR = "error"                    # Error message
    STATUS_RESPONSE = "status_resp"    # Daemon status response
    PONG = "pong"                      # Keep-alive pong

    # Bidirectional
    DISCONNECT = "disconnect"          # Client/daemon disconnect


class ClientSource(Enum):
    """Source of client connection"""
    CLI = "cli"
    VOICE = "voice"
    KDE = "kde"
    API = "api"
    UNKNOWN = "unknown"


@dataclass
class Message:
    """
    Standard message format for JARVIS daemon communication.

    All fields are serializable to JSON for WebSocket transmission.
    """
    type: MessageType
    source: ClientSource = ClientSource.UNKNOWN

    # Message identification
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    # Correlation (for responses)
    reply_to: Optional[str] = None

    # Payload
    text: Optional[str] = None
    data: Optional[Dict[str, Any]] = None

    # Voice-specific options
    audio_response: bool = False      # Request voice output

    # Error info
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    # Tools/MCP info
    tools_used: Optional[List[str]] = None

    def to_json(self) -> str:
        """Serialize message to JSON string"""
        d = asdict(self)
        # Convert enums to strings
        d['type'] = self.type.value
        d['source'] = self.source.value
        # Remove None values for cleaner output
        d = {k: v for k, v in d.items() if v is not None}
        return json.dumps(d)

    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary"""
        d = asdict(self)
        d['type'] = self.type.value
        d['source'] = self.source.value
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_json(cls, json_str: str) -> 'Message':
        """Deserialize message from JSON string"""
        d = json.loads(json_str)
        return cls.from_dict(d)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'Message':
        """Create message from dictionary"""
        # Convert string enums back to enum types
        msg_type = MessageType(d.get('type', 'query'))
        source = ClientSource(d.get('source', 'unknown'))

        return cls(
            type=msg_type,
            source=source,
            id=d.get('id', str(uuid.uuid4())),
            timestamp=d.get('timestamp', datetime.utcnow().isoformat()),
            reply_to=d.get('reply_to'),
            text=d.get('text'),
            data=d.get('data'),
            audio_response=d.get('audio_response', False),
            error_code=d.get('error_code'),
            error_message=d.get('error_message'),
            tools_used=d.get('tools_used'),
        )


# Factory functions for common message types

def create_query(text: str, source: ClientSource = ClientSource.CLI,
                 audio_response: bool = False) -> Message:
    """Create a query message"""
    return Message(
        type=MessageType.QUERY,
        source=source,
        text=text,
        audio_response=audio_response
    )


def create_response(text: str, reply_to: str,
                    tools_used: Optional[List[str]] = None) -> Message:
    """Create a response message"""
    return Message(
        type=MessageType.RESPONSE,
        text=text,
        reply_to=reply_to,
        tools_used=tools_used
    )


def create_error(error_message: str, error_code: str = "UNKNOWN",
                 reply_to: Optional[str] = None) -> Message:
    """Create an error message"""
    return Message(
        type=MessageType.ERROR,
        error_code=error_code,
        error_message=error_message,
        reply_to=reply_to
    )


def create_approval_request(command: str, security_level: str,
                           reply_to: str) -> Message:
    """Create an approval request message"""
    return Message(
        type=MessageType.APPROVAL_REQUEST,
        reply_to=reply_to,
        data={
            'command': command,
            'security_level': security_level
        }
    )


def create_approval_response(approved: bool, reply_to: str,
                            source: ClientSource = ClientSource.CLI) -> Message:
    """Create an approval response message"""
    return Message(
        type=MessageType.APPROVAL_RESPONSE,
        source=source,
        reply_to=reply_to,
        data={'approved': approved}
    )


def create_status_request(source: ClientSource = ClientSource.CLI) -> Message:
    """Create a status request message"""
    return Message(
        type=MessageType.STATUS,
        source=source
    )


def create_status_response(status: Dict[str, Any], reply_to: str) -> Message:
    """Create a status response message"""
    return Message(
        type=MessageType.STATUS_RESPONSE,
        reply_to=reply_to,
        data=status
    )


def create_ping() -> Message:
    """Create a ping message"""
    return Message(type=MessageType.PING)


def create_pong(reply_to: str) -> Message:
    """Create a pong message"""
    return Message(type=MessageType.PONG, reply_to=reply_to)
