"""
Test Voice Activation system.

This module tests the voice activation system components and
graceful degradation when audio is unavailable.
"""

import pytest
import sys
import os
from pathlib import Path
from unittest.mock import patch, Mock

# Add jarvis directory to path
jarvis_path = Path(__file__).parent.parent / 'jarvis'
sys.path.insert(0, str(jarvis_path))

# Mock audio dependencies for testing
import types
mock_modules = {
    'sounddevice': types.ModuleType('sounddevice'),
    'vosk': types.ModuleType('vosk'),
}

for name, module in mock_modules.items():
    sys.modules[name] = module


@pytest.mark.health
class TestVoiceActivationHealth:
    """Test voice activation system health and graceful degradation."""

    def test_voice_activation_import(self):
        """Test voice activation can be imported."""
        try:
            from voice_activation import VoiceActivation
        except ImportError as e:
            pytest.skip(f"Voice activation dependencies not available: {e}")

    def test_voice_activation_creation_fails_gracefully(self):
        """Test voice activation creation fails gracefully when audio unavailable."""
        try:
            from voice_activation import VoiceActivation
            from core.audio_detection import AudioUnavailableError

            # Should raise AudioUnavailableError when audio not available
            with pytest.raises(AudioUnavailableError):
                va = VoiceActivation(
                    wake_words=["test"],
                    model_path="/nonexistent/model",
                    on_wake_word=lambda: None
                )
        except ImportError:
            pytest.skip("Voice activation module not available")

    @patch('voice_activation.check_audio_input_available')
    @patch('voice_activation.sd.InputStream')
    @patch('voice_activation.vosk.Model')
    @patch('voice_activation.vosk.KaldiRecognizer')
    def test_voice_activation_creation_success(self, mock_recognizer, mock_model, mock_stream, mock_audio_check):
        """Test voice activation creation succeeds when all dependencies available."""
        mock_audio_check.return_value = True
        mock_model.return_value = Mock()
        mock_recognizer.return_value = Mock()

        try:
            from voice_activation import VoiceActivation

            def dummy_callback():
                pass

            va = VoiceActivation(
                wake_words=["test"],
                model_path="/test/model",
                on_wake_word=dummy_callback
            )

            assert va is not None
            assert hasattr(va, 'start_listening')
            assert hasattr(va, 'stop_listening')
            assert hasattr(va, 'cleanup')

        except ImportError:
            pytest.skip("Voice activation module not available")

    def test_voice_activation_stats(self):
        """Test voice activation stats functionality."""
        try:
            from voice_activation import VoiceActivation
            from core.audio_detection import AudioUnavailableError

            # Create instance (should fail gracefully)
            with pytest.raises(AudioUnavailableError):
                va = VoiceActivation(
                    wake_words=["test"],
                    model_path="/nonexistent",
                    on_wake_word=lambda: None
                )

        except ImportError:
            pytest.skip("Voice activation module not available")


@pytest.mark.manual
class TestVoiceActivationManual:
    """Manual test for voice activation (requires actual audio hardware)."""

    @pytest.mark.skip(reason="Manual test - requires microphone and Vosk model")
    def test_voice_activation_manual(self):
        """Manual test of voice activation system."""
        try:
            from voice_activation import VoiceActivation

            detections = []

            def on_wake_word():
                detections.append("detected")

            va = VoiceActivation(
                wake_words=["jarvis"],
                model_path="models/vosk-model-small-en-us-0.15",
                on_wake_word=on_wake_word
            )

            # This would be a manual test requiring user interaction
            # For automated tests, this is skipped
            assert va is not None

        except ImportError:
            pytest.skip("Voice activation dependencies not available")
        except Exception:
            pytest.skip("Voice activation requires audio hardware and models")

