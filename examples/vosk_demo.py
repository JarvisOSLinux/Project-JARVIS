#!/usr/bin/env python3
"""
Vosk Wake Word Detection Demo

This example demonstrates how to use Vosk for offline wake word detection.
Vosk is a completely offline speech recognition toolkit that can be used
for wake word detection without any internet connection.

Requirements:
    pip install vosk pyaudio

Usage:
    python vosk_demo.py
"""

import json
import sounddevice as sd
import numpy as np
import vosk
import sys
import time
import threading
from queue import Queue

class VoskWakeWordDetector:
    """Simple wake word detector using Vosk."""
    
    def __init__(self, model_path=None, wake_words=["jarvis", "hey jarvis"]):
        """
        Initialize Vosk wake word detector.
        
        Args:
            model_path: Path to Vosk model (if None, will try to download)
            wake_words: List of wake words to detect
        """
        self.wake_words = [word.lower() for word in wake_words]
        self.model_path = model_path
        self.model = None
        self.recognizer = None
        self.stream = None
        self.audio_buffer = None
        self.running = False
        self.detection_queue = Queue()
    
    def _audio_callback(self, indata, frames, time, status):
        """Callback for sounddevice audio input."""
        if status:
            print(f"Audio callback status: {status}")
        # Convert numpy array to bytes and put in buffer
        audio_bytes = indata.tobytes()
        self.audio_buffer.put(audio_bytes)
        
    def initialize(self):
        """Initialize Vosk model and audio."""
        try:
            # Initialize Vosk model
            if self.model_path:
                self.model = vosk.Model(self.model_path)
            else:
                # Try to use a small model (you'll need to download this)
                print("\tNo model path provided. You need to download a Vosk model.")
                print("\tDownload from: https://alphacephei.com/vosk/models")
                print("\tExample: wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip")
                return False
            
            self.recognizer = vosk.KaldiRecognizer(self.model, 16000)
            
            # Initialize sounddevice stream
            self.audio_buffer = Queue()
            self.stream = sd.InputStream(
                samplerate=16000,
                channels=1,
                dtype='int16',
                blocksize=8000,
                callback=self._audio_callback
            )
            self.stream.start()
            
            print("\tVosk wake word detector initialized successfully!")
            return True
            
        except Exception as e:
            print(f"\tFailed to initialize Vosk: {e}")
            return False
    
    def start_listening(self):
        """Start listening for wake words."""
        if not self.initialize():
            return False
            
        self.running = True
        print("\tListening for wake words...")
        print(f"\tWake words: {', '.join(self.wake_words)}")
        print("Press Ctrl+C to stop.\n")
        
        try:
            while self.running:
                try:
                    data = self.audio_buffer.get(timeout=0.1)
                except Empty:
                    continue
                
                if self.recognizer.AcceptWaveform(data):
                    result = json.loads(self.recognizer.Result())
                    text = result.get('text', '').lower().strip()
                    
                    if text:
                        print(f"\tRecognized: '{text}'")
                        
                        # Check if any wake word is in the recognized text
                        for wake_word in self.wake_words:
                            if wake_word in text:
                                print(f"\tWAKE WORD DETECTED: '{wake_word}'")
                                print(f"\tFull text: '{text}'")
                                print(f"\tTime: {time.strftime('%H:%M:%S')}")
                                print("\t-> This is where you'd start your voice processing pipeline\n")
                                
                                # Queue the detection
                                self.detection_queue.put({
                                    'wake_word': wake_word,
                                    'full_text': text,
                                    'timestamp': time.time()
                                })
                                break
                else:
                    # Partial result
                    partial = json.loads(self.recognizer.PartialResult())
                    partial_text = partial.get('partial', '').lower().strip()
                    if partial_text:
                        print(f"Partial: '{partial_text}'", end='\r')
                        
        except KeyboardInterrupt:
            print("\nStopping...")
        except Exception as e:
            print(f"ERROR: failure during listening: {e}")
        finally:
            self.stop_listening()
    
    def stop_listening(self):
        """Stop listening and cleanup."""
        self.running = False
        
        if self.stream:
            self.stream.stop()
            self.stream.close()
        
        print("Listening stopped")
    
    def get_detection(self, timeout=None):
        """Get the next wake word detection."""
        try:
            return self.detection_queue.get(timeout=timeout)
        except:
            return None

def main():
    """Main demo function."""
    print("🎤 Vosk Wake Word Detection Demo")
    print("=" * 50)
    
    model_path = "models/vosk-model-small-en-us-0.15"  # Set this to your model path
    
    if not model_path:
        print("No model path provided")
        print("\nTo get started:")
        print("1) Download a Vosk model:")
        print("\twget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip")
        print("2) Unzip it:")
        print("\tunzip vosk-model-small-en-us-0.15.zip")
        print("3) Update the model_path in this script")
        print("4) Run the demo again")
        return
    
    # Create detector
    detector = VoskWakeWordDetector(
        model_path=model_path,
        wake_words=["jarvis", "hey jarvis", "okay jarvis"]
    )
    
    try:
        # Start listening
        detector.start_listening()
        
    except KeyboardInterrupt:
        print("\nDemo interrupted by user")
    finally:
        detector.stop_listening()
        print("Demo completed")

if __name__ == "__main__":
    main()
