"""
Comprehensive health check tests for JARVIS AI Assistant.

These tests verify that all modules and subsystems can be imported
and that the component factory creates components correctly.
"""

import pytest
import os
from pathlib import Path
from unittest.mock import patch, Mock


@pytest.mark.health
class TestModuleImports:
    """Test that all modules can be imported successfully."""

    def test_config_import(self):
        from jarvis.config import Config
        assert Config is not None

    def test_core_modules_import(self):
        from jarvis.core import SystemInfo, TaskParser, OutputManager, ComponentFactory
        assert SystemInfo is not None
        assert TaskParser is not None
        assert OutputManager is not None
        assert ComponentFactory is not None

    def test_llm_modules_import(self):
        from jarvis.llm import LLM, BaseLLMProvider, create_provider
        assert LLM is not None
        assert BaseLLMProvider is not None
        assert create_provider is not None

    def test_dispatch_modules_import(self):
        from jarvis.dispatch import DispatchAdapter, GoalManager, EventMerger, Event
        assert DispatchAdapter is not None
        assert GoalManager is not None
        assert EventMerger is not None
        assert Event is not None

    def test_voice_audio_import(self):
        from jarvis.voice.audio import (
            check_audio_input_available,
            check_audio_output_available,
            list_audio_devices,
            AudioUnavailableError,
        )
        assert check_audio_input_available is not None
        assert AudioUnavailableError is not None

    def test_main_module_import(self):
        from jarvis.main import Jarvis
        assert Jarvis is not None

    def test_optional_modules_graceful(self):
        """Test that optional voice modules handle missing dependencies gracefully."""
        try:
            from jarvis.voice.stt.vosk_stt import VoskSTT
        except (ImportError, Exception):
            pass  # Expected if dependencies missing

        try:
            from jarvis.voice.tts.piper_tts import PiperTTS
        except (ImportError, Exception):
            pass

        try:
            from jarvis.voice.activation.vosk_activation import VoskActivation
        except (ImportError, Exception):
            pass


@pytest.mark.health
class TestComponentFactory:
    """Test component factory health and creation."""

    def test_create_task_parser(self):
        from jarvis.core.component_factory import ComponentFactory
        parser = ComponentFactory.create_task_parser()
        assert parser is not None
        assert hasattr(parser, 'parse')

    def test_create_dispatch_adapter(self):
        from jarvis.core.component_factory import ComponentFactory
        adapter = ComponentFactory.create_dispatch_adapter()
        assert adapter is not None
        assert hasattr(adapter, 'connect')
        assert hasattr(adapter, 'send_tasks')

    def test_create_goal_manager(self):
        from jarvis.core.component_factory import ComponentFactory
        gm = ComponentFactory.create_goal_manager()
        assert gm is not None
        assert hasattr(gm, 'add_goal')
        assert hasattr(gm, 'get_context')

    def test_create_event_merger(self):
        from jarvis.core.component_factory import ComponentFactory
        em = ComponentFactory.create_event_merger()
        assert em is not None
        assert hasattr(em, 'start')
        assert hasattr(em, 'stop')

    def test_create_output_manager_no_tts(self):
        from jarvis.core.component_factory import ComponentFactory
        om = ComponentFactory.create_output_manager(None)
        assert om is not None
        assert hasattr(om, 'handle_response')

    def test_create_tts_optional(self):
        from jarvis.core.component_factory import ComponentFactory
        tts = ComponentFactory.create_tts_optional()
        # Should return None or TTS instance, but never crash
        assert tts is None or hasattr(tts, 'say')


@pytest.mark.health
class TestAudioDetection:
    """Test audio detection health."""

    def test_check_audio_input_available(self):
        from jarvis.voice.audio import check_audio_input_available
        result = check_audio_input_available()
        assert isinstance(result, bool)

    def test_check_audio_output_available(self):
        from jarvis.voice.audio import check_audio_output_available
        result = check_audio_output_available()
        assert isinstance(result, bool)

    def test_list_audio_devices(self):
        from jarvis.voice.audio import list_audio_devices, AudioUnavailableError
        try:
            devices = list_audio_devices()
            assert isinstance(devices, (list, dict))
        except AudioUnavailableError:
            pass  # Expected if audio libraries not available


@pytest.mark.health
class TestLLM:
    """Test LLM subsystem health."""

    def test_llm_provider_factory(self):
        from jarvis.llm.providers import create_provider
        try:
            provider = create_provider(provider="ollama", model="test-model")
            assert provider is not None
        except Exception:
            pytest.skip("LLM provider factory failed (expected if Ollama not running)")

    def test_system_info_extraction(self):
        from jarvis.core.system_info import SystemInfo
        system_info = SystemInfo.get_system_info()
        required_keys = ['system', 'release', 'version', 'machine', 'shell']
        for key in required_keys:
            assert key in system_info
            assert system_info[key] is not None


@pytest.mark.health
class TestConfig:
    """Test configuration health."""

    def test_config_has_expected_attributes(self):
        from jarvis.config import Config
        assert hasattr(Config, 'LLM_PROVIDER')
        assert hasattr(Config, 'LLM_MODEL')
        assert hasattr(Config, 'OUTPUT_MODE')
        assert hasattr(Config, 'LOG_LEVEL')
        assert hasattr(Config, 'DISPATCH_TIMEOUT')
        assert hasattr(Config, 'DISPATCH_BINARY')

    def test_config_default_values(self):
        from jarvis.config import Config
        assert Config.OUTPUT_MODE in ['text', 'voice']
        assert isinstance(Config.LOG_LEVEL, str)
        assert Config.DISPATCH_TIMEOUT > 0

    def test_llm_rule_formatting(self):
        from jarvis.config import Config
        system_info = {
            'system': 'linux',
            'release': '5.4.0',
            'version': '#1 SMP Debian',
            'machine': 'x86_64',
            'shell': ['bash', '-lc']
        }
        formatted_rule = Config.LLM_RULE.format(**system_info)
        assert 'linux' in formatted_rule
        assert 'dispatch' in formatted_rule.lower() or 'action' in formatted_rule.lower()
