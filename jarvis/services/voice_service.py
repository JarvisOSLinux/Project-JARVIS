"""
JARVIS Voice Service - Standalone voice I/O daemon

This service runs independently and handles:
- Wake word detection ("Jarvis", "Hey Jarvis", etc.)
- Speech-to-Text conversion
- Text-to-Speech output
- Communication with JARVIS daemon via socket

The voice service is a thin client that:
1. Listens for wake words
2. On wake word: captures speech, converts to text
3. Sends text query to daemon
4. Receives response from daemon
5. Speaks response via TTS
6. Returns to wake word listening
"""

import asyncio
import json
import time
from typing import Optional, Callable
from enum import Enum

from ..config import Config
from ..core.logger import get_logger
from ..core.audio_detection import check_audio_input_available, check_audio_output_available
from ..daemon.protocol import (
    Message, MessageType, ClientSource,
    create_query, create_status_request
)

logger = get_logger(__name__)

# Default daemon connection settings
DEFAULT_DAEMON_HOST = "127.0.0.1"
DEFAULT_DAEMON_PORT = 18789


class VoiceServiceState(Enum):
    """Voice service states"""
    IDLE = "idle"
    LISTENING_WAKE = "listening_wake"      # Listening for wake word
    LISTENING_COMMAND = "listening_command"  # Capturing user command
    PROCESSING = "processing"               # Waiting for daemon response
    SPEAKING = "speaking"                   # TTS output
    ERROR = "error"


class VoiceService:
    """
    Voice service that handles all audio I/O.

    This runs as a separate service from the main daemon,
    communicating via socket protocol.
    """

    def __init__(self, daemon_host: str = DEFAULT_DAEMON_HOST,
                 daemon_port: int = DEFAULT_DAEMON_PORT):
        """
        Initialize the voice service.

        Args:
            daemon_host: JARVIS daemon host
            daemon_port: JARVIS daemon port
        """
        self.daemon_host = daemon_host
        self.daemon_port = daemon_port

        self._state = VoiceServiceState.IDLE
        self._running = False

        # Connection to daemon
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

        # Voice components (lazy loaded)
        self._voice_activation = None
        self._stt = None
        self._tts = None

        # Statistics
        self._commands_processed = 0
        self._wake_detections = 0

    async def start(self) -> None:
        """Start the voice service"""
        logger.info("Starting JARVIS Voice Service...")

        # Check audio availability
        if not check_audio_input_available():
            logger.error("No audio input devices available")
            raise RuntimeError("Audio input unavailable")

        # Initialize voice components
        self._initialize_voice_components()

        # Connect to daemon
        await self._connect_to_daemon()

        self._running = True
        self._state = VoiceServiceState.LISTENING_WAKE

        logger.info("Voice Service started - listening for wake word")
        logger.info(f"Wake words: {', '.join(Config.WAKE_WORDS)}")

        try:
            await self._main_loop()
        finally:
            await self._cleanup()

    async def stop(self) -> None:
        """Stop the voice service"""
        logger.info("Stopping Voice Service...")
        self._running = False

    def _initialize_voice_components(self) -> None:
        """Initialize voice input/output components"""
        try:
            from ..voice_activation import VoiceActivation
            from ..voice_input import SpeechToText

            # Voice activation (wake word detection)
            self._voice_activation = VoiceActivation(
                wake_words=Config.WAKE_WORDS,
                model_path=Config.VOSK_MODEL_PATH,
                sample_rate=16000,
                chunk_size=4000,
                sensitivity=Config.VOICE_ACTIVATION_SENSITIVITY,
                on_wake_word=self._on_wake_word
            )

            # Speech-to-Text
            self._stt = SpeechToText(
                model_path=Config.VOSK_MODEL_PATH,
                sample_rate=16000,
                chunk_size=4000,
                phrase_timeout=3.0,
                silence_timeout=1.0,
                device_index=None
            )

            logger.info("Voice input components initialized")

        except ImportError as e:
            logger.error(f"Voice input dependencies not available: {e}")
            raise RuntimeError(f"Missing voice dependencies: {e}")

        # TTS (optional)
        if check_audio_output_available():
            try:
                from ..voice_output import TextToSpeech

                self._tts = TextToSpeech(
                    model_path=f"models/piper/{Config.TTS_MODEL_ONNX}",
                    config_path=f"models/piper/{Config.TTS_MODEL_JSON}",
                )
                logger.info("TTS initialized")
            except Exception as e:
                logger.warning(f"TTS unavailable: {e}")
                self._tts = None
        else:
            logger.warning("No audio output devices, TTS disabled")
            self._tts = None

    async def _connect_to_daemon(self) -> None:
        """Connect to the JARVIS daemon"""
        max_retries = 5
        retry_delay = 2.0

        for attempt in range(max_retries):
            try:
                logger.info(f"Connecting to daemon at {self.daemon_host}:{self.daemon_port}...")
                self._reader, self._writer = await asyncio.open_connection(
                    self.daemon_host, self.daemon_port
                )
                logger.info("Connected to daemon")

                # Send status request to verify connection
                status_msg = create_status_request(ClientSource.VOICE)
                await self._send_message(status_msg)

                response = await self._receive_message(timeout=5.0)
                if response and response.type == MessageType.STATUS_RESPONSE:
                    logger.info(f"Daemon status: {response.data}")
                    return

            except ConnectionRefusedError:
                logger.warning(f"Daemon not available (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
            except Exception as e:
                logger.error(f"Connection error: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)

        raise RuntimeError("Could not connect to JARVIS daemon")

    async def _main_loop(self) -> None:
        """Main voice service loop"""

        # Start wake word detection
        if not self._voice_activation.start_listening():
            raise RuntimeError("Failed to start wake word detection")

        try:
            while self._running:
                if self._state == VoiceServiceState.LISTENING_WAKE:
                    # Check for wake word activation
                    activation = self._voice_activation.get_activation(timeout=0.5)
                    if activation:
                        self._wake_detections += 1
                        logger.info(f"Wake word detected: {activation['wake_word']}")
                        await self._process_voice_command()

                elif self._state == VoiceServiceState.ERROR:
                    # Try to recover
                    await asyncio.sleep(1.0)
                    self._state = VoiceServiceState.LISTENING_WAKE

                else:
                    await asyncio.sleep(0.1)

        except KeyboardInterrupt:
            logger.info("Voice service interrupted")
        finally:
            self._voice_activation.stop_listening()

    def _on_wake_word(self) -> None:
        """Callback when wake word is detected"""
        logger.debug("Wake word callback triggered")
        # The main loop will handle this via get_activation()

    async def _process_voice_command(self) -> None:
        """Process a voice command after wake word detection"""
        self._state = VoiceServiceState.LISTENING_COMMAND

        # Stop wake word detection to free audio resources
        self._voice_activation.stop_listening()

        try:
            # Capture user command via STT
            logger.info("Listening for command...")
            self._stt.start()

            command_text = None
            try:
                for text, is_final in self._stt.iter_results():
                    if is_final and text.strip():
                        command_text = text.strip()
                        logger.info(f"Command: {command_text}")
                        break
            finally:
                self._stt.stop()

            if not command_text:
                logger.info("No command detected")
                return

            # Send to daemon
            self._state = VoiceServiceState.PROCESSING
            self._commands_processed += 1

            query = create_query(
                text=command_text,
                source=ClientSource.VOICE,
                audio_response=True
            )

            await self._send_message(query)

            # Wait for response
            response = await self._receive_message(timeout=60.0)

            if response:
                if response.type == MessageType.RESPONSE:
                    await self._handle_response(response)
                elif response.type == MessageType.ERROR:
                    logger.error(f"Daemon error: {response.error_message}")
                    await self._speak("Sorry, I encountered an error.")
                elif response.type == MessageType.APPROVAL_REQUEST:
                    await self._handle_approval_request(response)
            else:
                logger.warning("No response from daemon")
                await self._speak("Sorry, I didn't get a response.")

        except Exception as e:
            logger.error(f"Error processing command: {e}", exc_info=True)
            self._state = VoiceServiceState.ERROR
        finally:
            # Resume wake word detection
            self._state = VoiceServiceState.LISTENING_WAKE
            self._voice_activation.start_listening()

    async def _handle_response(self, response: Message) -> None:
        """Handle response from daemon"""
        text = response.text or "I don't have a response."
        logger.info(f"Response: {text[:100]}...")

        # Speak response
        await self._speak(text)

        # Check for follow-up question
        if text.rstrip().endswith('?'):
            await self._handle_follow_up()

    async def _handle_approval_request(self, request: Message) -> None:
        """Handle command approval request"""
        data = request.data or {}
        command = data.get('command', 'unknown command')
        security_level = data.get('security_level', 'unknown')

        # Speak approval request
        await self._speak(f"I need your approval to run: {command}. Say yes or no.")

        # Listen for approval
        self._stt.start()
        approval_text = None
        try:
            for text, is_final in self._stt.iter_results():
                if is_final and text.strip():
                    approval_text = text.strip().lower()
                    break
        finally:
            self._stt.stop()

        # Parse approval
        approved = False
        if approval_text:
            approval_keywords = ['yes', 'y', 'approve', 'allow', 'ok', 'okay', 'sure', 'go']
            approved = any(word in approval_text for word in approval_keywords)

        # Send approval response
        from ..daemon.protocol import create_approval_response
        approval_response = create_approval_response(
            approved=approved,
            reply_to=request.reply_to or request.id,
            source=ClientSource.VOICE
        )
        await self._send_message(approval_response)

        if approved:
            await self._speak("Approved. Executing command.")
            # Wait for execution result
            response = await self._receive_message(timeout=30.0)
            if response and response.type == MessageType.RESPONSE:
                await self._handle_response(response)
        else:
            await self._speak("Command denied.")

    async def _handle_follow_up(self) -> None:
        """Handle follow-up after LLM asks a question"""
        logger.info("Listening for follow-up response (10s timeout)...")

        self._stt.start()
        start_time = time.time()
        follow_up_text = None

        try:
            for text, is_final in self._stt.iter_results():
                if time.time() - start_time > 10:
                    logger.info("Follow-up timed out")
                    break
                if is_final and text.strip():
                    follow_up_text = text.strip()
                    break
        finally:
            self._stt.stop()

        if follow_up_text:
            logger.info(f"Follow-up: {follow_up_text}")

            query = create_query(
                text=follow_up_text,
                source=ClientSource.VOICE,
                audio_response=True
            )
            await self._send_message(query)

            response = await self._receive_message(timeout=60.0)
            if response and response.type == MessageType.RESPONSE:
                await self._handle_response(response)

    async def _speak(self, text: str) -> None:
        """Speak text via TTS"""
        self._state = VoiceServiceState.SPEAKING

        if self._tts:
            try:
                # Run TTS in thread pool to avoid blocking
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._tts.say, text)
            except Exception as e:
                logger.warning(f"TTS failed: {e}")
                print(f"JARVIS: {text}")
        else:
            print(f"JARVIS: {text}")

    async def _send_message(self, message: Message) -> bool:
        """Send message to daemon"""
        if not self._writer:
            logger.error("Not connected to daemon")
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
            logger.warning("Timeout waiting for daemon response")
        except Exception as e:
            logger.error(f"Error receiving message: {e}")

        return None

    async def _cleanup(self) -> None:
        """Cleanup resources"""
        logger.info("Cleaning up voice service...")

        if self._voice_activation:
            self._voice_activation.cleanup()

        if self._stt:
            self._stt.stop()

        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass

    def get_status(self) -> dict:
        """Get service status"""
        return {
            'state': self._state.value,
            'running': self._running,
            'commands_processed': self._commands_processed,
            'wake_detections': self._wake_detections,
            'has_tts': self._tts is not None,
            'daemon_connected': self._writer is not None
        }


def run_voice_service(daemon_host: str = DEFAULT_DAEMON_HOST,
                      daemon_port: int = DEFAULT_DAEMON_PORT) -> None:
    """
    Run the voice service (blocking).

    This is the main entry point for running the voice service.
    """
    service = VoiceService(daemon_host, daemon_port)

    try:
        asyncio.run(service.start())
    except KeyboardInterrupt:
        logger.info("Voice service interrupted by user")
    except Exception as e:
        logger.error(f"Voice service error: {e}", exc_info=True)


# Entry point for running as module
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="JARVIS Voice Service")
    parser.add_argument("--host", default=DEFAULT_DAEMON_HOST, help="Daemon host")
    parser.add_argument("--port", type=int, default=DEFAULT_DAEMON_PORT, help="Daemon port")
    args = parser.parse_args()

    run_voice_service(args.host, args.port)
