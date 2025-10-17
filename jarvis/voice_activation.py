import threading
import time
from typing import Callable, Optional, List
from queue import Queue, Empty

class VoiceActivation:
    """
    Voice activation using Vosk for wake word detection.
    
    This class provides wake word detection that can trigger
    the existing voice processing pipeline using Vosk's
    real-time speech recognition.
    """
    
    def __init__(
        self,
        wake_words: List[str] = ["jarvis", "hey jarvis"],
        model_path: str = "vosk-model-small-en-us-0.15",
        sample_rate: int = 16000,
        chunk_size: int = 4000,
        sensitivity: float = 0.8,
        on_wake_word: Optional[Callable[[], None]] = None
    ):
        """
        Initialize voice activation.
        
        Args:
            wake_words: List of wake words to detect
            model_path: Path to Vosk model directory
            sample_rate: Audio sample rate
            chunk_size: Audio chunk size for processing
            sensitivity: Wake word detection sensitivity (0.0 to 1.0)
            on_wake_word: Callback function called when wake word is detected
        """
        self.wake_words = [word.lower() for word in wake_words]
        self.model_path = model_path
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.sensitivity = sensitivity
        self.on_wake_word = on_wake_word
        
        # Import Vosk components
        try:
            import vosk
            import sounddevice as sd
            import json
            import numpy as np
            self.vosk = vosk
            self.sounddevice = sd
            self.json = json
            self.numpy = np
        except ImportError as e:
            raise ImportError(f"Required dependencies not found: {e}. Please install: pip install vosk sounddevice")
        
        # Vosk components
        self._model = None
        self._recognizer = None
        
        # sounddevice components
        self._stream = None
        
        # Threading
        self._listening_thread = None
        self._running = threading.Event()
        self._activation_queue = Queue()
        
        # Statistics
        self._detection_count = 0
        self._last_detection_time = 0.0
        
        # Audio buffer for sounddevice callback
        self._audio_buffer = Queue()
        
    def initialize(self) -> bool:
        """
        Initialize Vosk and audio system.
        
        Returns:
            True if initialization successful, False otherwise
        """
        try:
            # Initialize Vosk model
            print(f"Loading Vosk model from: {self.model_path}")
            self._model = self.vosk.Model(self.model_path)
            self._recognizer = self.vosk.KaldiRecognizer(self._model, self.sample_rate)
            
            print(f"Voice Activation initialized:")
            print(f"   Wake words: {', '.join(self.wake_words)}")
            print(f"   Sample rate: {self.sample_rate} Hz")
            print(f"   Chunk size: {self.chunk_size} samples")
            print(f"   Sensitivity: {self.sensitivity}")
            
            return True
            
        except Exception as e:
            print(f"Failed to initialize voice activation: {e}")
            return False
    
    def start_listening(self) -> bool:
        """
        Start listening for wake words.
        
        Returns:
            True if started successfully, False otherwise
        """
        if self._running.is_set():
            return True
            
        if not self._model:
            if not self.initialize():
                return False
        
        try:
            # Initialize sounddevice stream
            stream_params = {
                'samplerate': self.sample_rate,
                'channels': 1,
                'dtype': 'int16',
                'blocksize': self.chunk_size,
                'callback': self._audio_callback
            }
            
            self._stream = self.sounddevice.InputStream(**stream_params)
            self._stream.start()
            
            # Start listening thread
            self._running.set()
            self._listening_thread = threading.Thread(
                target=self._listen_loop, 
                daemon=True
            )
            self._listening_thread.start()
            
            print("Voice activation listening started")
            return True
            
        except Exception as e:
            print(f"Failed to start listening: {e}")
            self.stop_listening()
            return False
    
    def stop_listening(self) -> None:
        """Stop listening for wake words."""
        if not self._running.is_set():
            return
            
        self._running.clear()
        
        # Wait for thread to finish
        if self._listening_thread:
            self._listening_thread.join(timeout=2.0)
            self._listening_thread = None
        
        # Cleanup audio
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        
        print("Voice activation stopped")
    
    def _audio_callback(self, indata, frames, time, status):
        """Callback for sounddevice audio input."""
        if status:
            print(f"Audio callback status: {status}")
        # Convert numpy array to bytes and put in buffer
        audio_bytes = indata.tobytes()
        self._audio_buffer.put(audio_bytes)
    
    def _listen_loop(self) -> None:
        """Main listening loop running in separate thread."""
        try:
            while self._running.is_set():
                # Read audio frame from buffer
                try:
                    data = self._audio_buffer.get(timeout=0.1)
                except Empty:
                    continue
                
                if self._recognizer.AcceptWaveform(data):
                    # Final result
                    result = self.json.loads(self._recognizer.Result())
                    text = result.get('text', '').lower().strip()
                    
                    if text:
                        self._check_for_wake_word(text)
                else:
                    # Partial result - check for wake words in real-time
                    partial = self.json.loads(self._recognizer.PartialResult())
                    partial_text = partial.get('partial', '').lower().strip()
                    
                    if partial_text:
                        self._check_for_wake_word(partial_text)
                        
        except Exception as e:
            print(f"Error in listening loop: {e}")
        finally:
            self._running.clear()
    
    def _check_for_wake_word(self, text: str) -> None:
        """Check if the given text contains any wake words."""
        current_time = time.time()
        
        # Prevent multiple detections within 2 seconds
        if current_time - self._last_detection_time < 2.0:
            return
        
        # Check if any wake word is in the text
        for wake_word in self.wake_words:
            if wake_word in text:
                self._handle_wake_word_detection(wake_word, text, current_time)
                break
    
    def _handle_wake_word_detection(self, wake_word: str, full_text: str, current_time: float) -> None:
        """Handle wake word detection."""
        self._detection_count += 1
        self._last_detection_time = current_time
        
        print(f"   WAKE WORD DETECTED!")
        print(f"   Word: '{wake_word}'")
        print(f"   Full text: '{full_text}'")
        print(f"   Detection #{self._detection_count}")
        print(f"   Time: {time.strftime('%H:%M:%S')}")
        
        # Queue the activation
        self._activation_queue.put({
            'wake_word': wake_word,
            'full_text': full_text,
            'timestamp': current_time,
            'count': self._detection_count
        })
        
        # Call callback if provided
        if self.on_wake_word:
            try:
                self.on_wake_word()
            except Exception as e:
                print(f"Error in wake word callback: {e}")
    
    def get_activation(self, timeout: Optional[float] = None) -> Optional[dict]:
        """
        Get the next wake word activation.
        
        Args:
            timeout: Maximum time to wait for activation (None = non-blocking)
            
        Returns:
            Activation data dict or None if no activation
        """
        try:
            return self._activation_queue.get(timeout=timeout)
        except Empty:
            return None
    
    def is_listening(self) -> bool:
        """Check if currently listening for wake words."""
        return self._running.is_set()
    
    def get_stats(self) -> dict:
        """Get detection statistics."""
        return {
            'detection_count': self._detection_count,
            'last_detection_time': self._last_detection_time,
            'is_listening': self.is_listening(),
            'wake_words': self.wake_words.copy()
        }
    
    def cleanup(self) -> None:
        """Clean up resources."""
        self.stop_listening()
        
        if self._recognizer:
            self._recognizer = None
        if self._model:
            self._model = None
        
        # Drain queue
        while not self._activation_queue.empty():
            try:
                self._activation_queue.get_nowait()
            except Empty:
                break
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.cleanup()


# Example usage and testing
if __name__ == "__main__":
    def on_wake_word():
        print("Wake word callback triggered!")
    
    # Create voice activation instance
    va = VoiceActivation(
        wake_words=["jarvis", "hey jarvis", "okay jarvis"],
        on_wake_word=on_wake_word
    )
    
    try:
        # Start listening
        if va.start_listening():
            print("Listening for wake words... Press Ctrl+C to exit")
            
            # Main loop - check for activations
            while True:
                activation = va.get_activation(timeout=1.0)
                if activation:
                    print(f"Received activation: {activation}")
                    
                # Print stats every 10 seconds
                if int(time.time()) % 10 == 0:
                    stats = va.get_stats()
                    print(f"Stats: {stats['detection_count']} detections")
                    
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        va.cleanup()
        print("Cleanup completed")




