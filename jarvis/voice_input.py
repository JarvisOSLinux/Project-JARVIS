import threading
import json
import sounddevice as sd
import vosk
import numpy as np
from queue import Queue, Empty
from datetime import datetime, timedelta
from typing import Callable, Generator, Optional, Tuple

class SpeechToText:
    """
    Real-time, offline speech-to-text using Vosk for fast, accurate transcription.

    Usage:
        stt = SpeechToText(model_path="vosk-model-small-en-us-0.15")
        stt.start()
        try:
            for text, is_final in stt.iter_results():
                print(("FINAL: " if is_final else "PARTIAL: ") + text)
        finally:
            stt.stop()
    """

    def __init__(
        self,
        model_path: str = "vosk-model-small-en-us-0.15",
        sample_rate: int = 16000,
        chunk_size: int = 4000,
        phrase_timeout: float = 3.0,
        silence_timeout: float = 1.0,
        device_index: Optional[int] = None,
    ):
        """
        Initialize Vosk-based speech-to-text.
        
        Args:
            model_path: Path to Vosk model directory
            sample_rate: Audio sample rate (must match model)
            chunk_size: Audio chunk size for processing
            phrase_timeout: Timeout for phrase completion
            silence_timeout: Timeout for silence detection
            device_index: Audio device index (None for default)
        """
        self.model_path = model_path
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.phrase_timeout = phrase_timeout
        self.silence_timeout = silence_timeout
        self.device_index = device_index

        # I/O queues
        self._result_q: Queue[Tuple[str, bool]] = Queue()  # (text, is_final)

        # Vosk components
        self._model: Optional[vosk.Model] = None
        self._recognizer: Optional[vosk.KaldiRecognizer] = None
        
        # sounddevice components
        self._stream: Optional[sd.InputStream] = None

        # Processing thread state
        self._worker_thread: Optional[threading.Thread] = None
        self._running = threading.Event()

        # Transcription state
        self._last_speech_time: Optional[datetime] = None
        self._last_emitted_text = ""  # for coalescing partials
        self._current_phrase = ""

        # Optional callback for push updates
        self._on_update: Optional[Callable[[str, bool], None]] = None
        
        # Audio buffer for sounddevice callback
        self._audio_buffer = Queue()

    @staticmethod
    def list_audio_devices() -> None:
        """List available audio input devices."""
        print("Available audio input devices:")
        devices = sd.query_devices()
        for i, device in enumerate(devices):
            if device['max_input_channels'] > 0:
                print(f"[{i}] {device['name']} (channels: {device['max_input_channels']})")

    def on_update(self, cb: Callable[[str, bool], None]) -> None:
        """Register a callback called as `cb(text, is_final)`."""
        self._on_update = cb
    
    def _audio_callback(self, indata, frames, time, status):
        """Callback for sounddevice audio input."""
        if status:
            print(f"Audio callback status: {status}")
        # Convert numpy array to bytes and put in buffer
        audio_bytes = indata.tobytes()
        self._audio_buffer.put(audio_bytes)

    def start(self) -> None:
        """Load model, open mic, start background processing."""
        if self._running.is_set():
            return
            
        try:
            # Load Vosk model
            print(f"Loading Vosk model from: {self.model_path}")
            self._model = vosk.Model(self.model_path)
            self._recognizer = vosk.KaldiRecognizer(self._model, self.sample_rate)
            print("✅ Vosk model loaded successfully")

            # Initialize sounddevice stream
            stream_params = {
                'samplerate': self.sample_rate,
                'channels': 1,
                'dtype': 'int16',
                'blocksize': self.chunk_size,
                'callback': self._audio_callback
            }
            
            # Add device_index only if specified
            if self.device_index is not None:
                stream_params['device'] = self.device_index
            
            self._stream = sd.InputStream(**stream_params)
            self._stream.start()
            print("✅ Audio stream initialized")

            # Start processing thread
            self._running.set()
            self._worker_thread = threading.Thread(target=self._process_loop, daemon=True)
            self._worker_thread.start()
            print("✅ Speech-to-text processing started")

        except Exception as e:
            print(f"❌ Failed to start speech-to-text: {e}")
            self.stop()
            raise

    def stop(self) -> None:
        """Stop processing and cleanup resources."""
        if not self._running.is_set():
            return
            
        self._running.clear()

        # Wait for thread to finish
        if self._worker_thread:
            self._worker_thread.join(timeout=2.0)
            self._worker_thread = None

        # Close audio stream
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        # Release Vosk model
        self._recognizer = None
        self._model = None

        # Drain queues
        self._drain_queue(self._result_q)
        self._drain_queue(self._audio_buffer)

        # Reset state
        self._last_speech_time = None
        self._last_emitted_text = ""
        self._current_phrase = ""

        print("🔇 Speech-to-text stopped")

    def _drain_queue(self, q: Queue) -> None:
        """Drain a queue of all items."""
        try:
            while True:
                q.get_nowait()
        except Empty:
            pass

    def _process_loop(self) -> None:
        """Main processing loop: read audio, transcribe with Vosk, emit results."""
        while self._running.is_set():
            try:
                # Read audio chunk from buffer
                try:
                    data = self._audio_buffer.get(timeout=0.1)
                except Empty:
                    continue
                
                if self._recognizer.AcceptWaveform(data):
                    # Final result
                    result = json.loads(self._recognizer.Result())
                    text = result.get('text', '').strip()
                    
                    if text:
                        self._current_phrase = text
                        self._last_speech_time = datetime.utcnow()
                        self._emit(text, is_final=True)
                        self._last_emitted_text = text
                        print(f"📝 FINAL: {text}")
                else:
                    # Partial result
                    partial = json.loads(self._recognizer.PartialResult())
                    partial_text = partial.get('partial', '').strip()
                    
                    if partial_text and partial_text != self._last_emitted_text:
                        self._last_speech_time = datetime.utcnow()
                        self._emit(partial_text, is_final=False)
                        self._last_emitted_text = partial_text
                        print(f"🔄 PARTIAL: {partial_text}", end='\r')
                
                # Check for silence timeout
                if self._last_speech_time:
                    silence_duration = datetime.utcnow() - self._last_speech_time
                    if silence_duration > timedelta(seconds=self.silence_timeout):
                        if self._current_phrase and self._current_phrase != self._last_emitted_text:
                            # Emit the current phrase as final
                            self._emit(self._current_phrase, is_final=True)
                            self._last_emitted_text = self._current_phrase
                            print(f"📝 FINAL (silence): {self._current_phrase}")
                        self._current_phrase = ""
                        self._last_speech_time = None
                        
            except Exception as e:
                if self._running.is_set():  # Only print error if we're still supposed to be running
                    print(f"❌ Error in processing loop: {e}")
                break

    def _emit(self, text: str, is_final: bool) -> None:
        """Emit a transcription result."""
        try:
            self._result_q.put_nowait((text, is_final))
        except Exception:
            pass
        if self._on_update:
            try:
                self._on_update(text, is_final)
            except Exception:
                pass

    def iter_results(self) -> Generator[Tuple[str, bool], None, None]:
        """Yield (text, is_final) as they arrive. Blocks."""
        while self._running.is_set():
            try:
                yield self._result_q.get(timeout=0.1)
            except Empty:
                continue

    def read(self, timeout: Optional[float] = None) -> Optional[Tuple[str, bool]]:
        """Pop one result (text, is_final). None if no result before `timeout`."""
        try:
            return self._result_q.get(timeout=timeout)
        except Empty:
            return None

    def is_running(self) -> bool:
        """Check if speech-to-text is currently running."""
        return self._running.is_set()

    def get_stats(self) -> dict:
        """Get processing statistics."""
        return {
            'is_running': self.is_running(),
            'model_path': self.model_path,
            'sample_rate': self.sample_rate,
            'chunk_size': self.chunk_size,
            'current_phrase': self._current_phrase,
            'last_emitted_text': self._last_emitted_text
        }
