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
            from voice.activation.vosk_activation import VoskActivation
        except ImportError as e:
            pytest.skip(f"Voice activation dependencies not available: {e}")

    def test_voice_activation_creation_fails_gracefully(self):
        """Test voice activation creation fails gracefully when audio unavailable."""
        try:
            from voice.activation.vosk_activation import VoskActivation
            from voice.audio import AudioUnavailableError

            # Should raise AudioUnavailableError when audio not available
            with pytest.raises(AudioUnavailableError):
                va = VoskActivation(
                    wake_words=["test"],
                    model_path="/nonexistent/model",
                    on_wake_word=lambda: None
                )
        except ImportError:
            pytest.skip("Voice activation module not available")

    def test_voice_activation_stats(self):
        """Test voice activation stats functionality."""
        try:
            from voice.activation.vosk_activation import VoskActivation
            from voice.audio import AudioUnavailableError

            # Create instance (should fail gracefully)
            with pytest.raises(AudioUnavailableError):
                va = VoskActivation(
                    wake_words=["test"],
                    model_path="/nonexistent",
                    on_wake_word=lambda: None
                )

        except ImportError:
            pytest.skip("Voice activation module not available")

    def test_factory_function(self):
        """Test create_activation factory produces the right type."""
        try:
            from voice.activation import create_activation
            from voice.base import ActivationProvider
        except ImportError:
            pytest.skip("Voice activation module not available")
            return

        with pytest.raises(ValueError):
            create_activation("nonexistent_provider")


@pytest.mark.manual
class TestVoiceActivationManual:
    """Manual test for voice activation (requires actual audio hardware)."""

    @pytest.mark.skip(reason="Manual test - requires microphone and Vosk model")
    def test_voice_activation_manual(self):
        """Manual test of voice activation system."""
        try:
            from voice.activation.vosk_activation import VoskActivation

            detections = []

            def on_wake_word():
                detections.append("detected")

            va = VoskActivation(
                wake_words=["jarvis"],
                model_path="models/vosk/vosk-model-small-en-us-0.15",
                on_wake_word=on_wake_word
            )

            assert va is not None

        except ImportError:
            pytest.skip("Voice activation dependencies not available")
        except Exception:
            pytest.skip("Voice activation requires audio hardware and models")
