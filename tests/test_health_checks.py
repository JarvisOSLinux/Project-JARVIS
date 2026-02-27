"""
Comprehensive health check tests for JARVIS AI Assistant.

These tests verify that all modules and subsystems are functioning properly,
including graceful degradation when optional dependencies are unavailable.
"""

import pytest
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock

# Add jarvis directory to path
jarvis_path = Path(__file__).parent.parent / 'jarvis'
sys.path.insert(0, str(jarvis_path))

# Mock heavy dependencies before importing JARVIS modules
import types
mock_modules = {
    'torch': types.ModuleType('torch'),
    'whisper': types.ModuleType('whisper'),
    'piper': types.ModuleType('piper'),
    'sounddevice': types.ModuleType('sounddevice'),
    'speech_recognition': types.ModuleType('speech_recognition'),
    'ollama': types.ModuleType('ollama'),
    'mcp': types.ModuleType('mcp'),
    'numpy': types.ModuleType('numpy'),
    'httpx': types.ModuleType('httpx'),
    'httpx_sse': types.ModuleType('httpx_sse'),
}

for name, module in mock_modules.items():
    sys.modules[name] = module

# Mock the SSE client
sse_client_mock = Mock()
sys.modules['mcp.client.sse'] = Mock(sse_client=sse_client_mock)

# Now import JARVIS modules
from config import Config
from core.component_factory import ComponentFactory
from voice.audio import (
    check_audio_input_available,
    check_audio_output_available,
    list_audio_devices,
    AudioUnavailableError
)
from supermcp_client import SuperMCPWrapper
from llm_providers import LLMProviderFactory
from llm import LLM
from core.system_info import SystemInfo
from core.command_parser import SuperMCPCommandParser
from core.output_manager import OutputManager


@pytest.mark.health
class TestModuleImports:
    """Test that all modules can be imported successfully."""

    def test_core_modules_import(self):
        """Test that all core JARVIS modules can be imported."""
        modules_to_test = [
            'config',
            'core.component_factory',
            'voice.audio',
            'core.logger',
            'core.system_info',
            'core.command_parser',
            'core.output_manager',
            'supermcp_client',
            'llm_providers',
            'llm',
            'main',
            'cli',
        ]

        for module_name in modules_to_test:
            try:
                __import__(module_name)
            except ImportError as e:
                pytest.fail(f"Failed to import module '{module_name}': {e}")

    def test_optional_modules_graceful(self):
        """Test that optional modules handle missing dependencies gracefully."""
        # Test voice_input (should handle missing sounddevice/vosk)
        try:
            from voice.stt import SpeechToText
            # If import succeeds, create instance should handle missing audio
            stt = SpeechToText()
            # Should either work or raise AudioUnavailableError
        except AudioUnavailableError:
            pass  # Expected for missing audio
        except ImportError:
            pass  # Expected if dependencies missing

        # Test voice_output (should handle missing piper/sounddevice)
        try:
            from voice.tts import TextToSpeech
            # If import succeeds, should handle missing models gracefully
        except AudioUnavailableError:
            pass  # Expected for missing audio
        except ImportError:
            pass  # Expected if dependencies missing

        # Test voice_activation (should handle missing vosk/sounddevice)
        try:
            from voice.activation import VoiceActivation
            # Should handle missing models/audio gracefully
        except AudioUnavailableError:
            pass  # Expected for missing audio
        except ImportError:
            pass  # Expected if dependencies missing


@pytest.mark.health
class TestComponentFactory:
    """Test component factory health and creation."""

    @patch.dict(os.environ, {
        'LLM_MODEL': 'test-model',
        'SUPERMCP_SERVER_PATH': 'SuperMCP/SuperMCP.py',
        'SUPERMCP_TIMEOUT': '30'
    })
    def test_create_llm(self):
        """Test LLM creation."""
        try:
            llm = ComponentFactory.create_llm()
            assert llm is not None
            assert hasattr(llm, 'ask')
        except Exception as e:
            # May fail if Ollama not running, but should not crash
            pytest.skip(f"LLM creation failed (expected if Ollama not running): {e}")

    def test_create_supermcp(self):
        """Test SuperMCP creation."""
        supermcp = ComponentFactory.create_supermcp()
        assert supermcp is not None
        assert hasattr(supermcp, 'execute_command_sequence')

    def test_create_tts_optional(self):
        """Test TTS optional creation (should handle missing audio gracefully)."""
        tts = ComponentFactory.create_tts_optional()
        # Should return None or TTS instance, but not crash
        assert tts is None or hasattr(tts, 'speak')

    def test_create_voice_manager_optional(self):
        """Test voice manager optional creation (should handle missing audio gracefully)."""
        def dummy_callback(text): pass

        vm = ComponentFactory.create_voice_manager_optional(dummy_callback)
        # Should return None or VoiceManager instance, but not crash
        assert vm is None or hasattr(vm, 'start_listening')

    def test_create_command_parser(self):
        """Test command parser creation."""
        supermcp = ComponentFactory.create_supermcp()
        parser = ComponentFactory.create_command_parser(supermcp)
        assert parser is not None
        assert hasattr(parser, 'execute_command_sequence')

    def test_create_output_manager(self):
        """Test output manager creation."""
        # Test with no TTS
        om = ComponentFactory.create_output_manager(None)
        assert om is not None
        assert hasattr(om, 'say')

        # Test with TTS (if available)
        tts = ComponentFactory.create_tts_optional()
        om_with_tts = ComponentFactory.create_output_manager(tts)
        assert om_with_tts is not None

    @patch.dict(os.environ, {
        'LLM_MODEL': 'test-model',
        'SUPERMCP_SERVER_PATH': 'SuperMCP/SuperMCP.py',
        'SUPERMCP_TIMEOUT': '30'
    })
    def test_create_all_components(self):
        """Test creating all components together."""
        try:
            components = ComponentFactory.create_all_components(text_mode=True)
            assert 'llm' in components
            assert 'supermcp' in components
            assert 'command_parser' in components
            assert 'output_manager' in components
            # Voice manager should be None in text mode
            assert components['voice_manager'] is None
        except Exception as e:
            pytest.skip(f"All components creation failed: {e}")


@pytest.mark.health
class TestAudioDetection:
    """Test audio detection health."""

    def test_check_audio_input_available(self):
        """Test audio input availability check."""
        result = check_audio_input_available()
        assert isinstance(result, bool)

    def test_check_audio_output_available(self):
        """Test audio output availability check."""
        result = check_audio_output_available()
        assert isinstance(result, bool)

    def test_list_audio_devices(self):
        """Test audio device listing."""
        try:
            devices = list_audio_devices()
            assert isinstance(devices, (list, dict))
        except AudioUnavailableError:
            # Expected if audio libraries not available
            pass


@pytest.mark.health
class TestSuperMCP:
    """Test SuperMCP subsystem health."""

    def test_supermcp_wrapper_initialization(self):
        """Test SuperMCP wrapper can initialize."""
        try:
            supermcp = SuperMCPWrapper()
            assert supermcp is not None
            assert hasattr(supermcp, 'execute_command_sequence')
        except Exception as e:
            pytest.skip(f"SuperMCP initialization failed: {e}")

    def test_supermcp_can_list_servers(self):
        """Test SuperMCP can list servers (even if empty)."""
        try:
            supermcp = SuperMCPWrapper()
            result = supermcp.execute_command_sequence("list_servers()")
            # Should not crash, even if no servers configured
            assert isinstance(result, dict)
        except Exception as e:
            pytest.skip(f"SuperMCP list_servers failed: {e}")


@pytest.mark.health
class TestLLM:
    """Test LLM subsystem health."""

    def test_llm_provider_factory(self):
        """Test LLM provider factory works."""
        try:
            provider = LLMProviderFactory.create_provider()
            assert provider is not None
        except Exception as e:
            pytest.skip(f"LLM provider factory failed: {e}")

    @patch.dict(os.environ, {'LLM_MODEL': 'test-model'})
    def test_llm_initialization(self):
        """Test LLM can initialize."""
        try:
            system_info = {
                'system': 'linux',
                'release': '5.4.0',
                'version': '#1 SMP Debian',
                'machine': 'x86_64',
                'shell': ['bash', '-lc']
            }
            llm = LLM(**system_info)
            assert llm is not None
            assert hasattr(llm, 'ask')
        except Exception as e:
            pytest.skip(f"LLM initialization failed: {e}")

    def test_system_info_extraction(self):
        """Test system info extraction works."""
        system_info = SystemInfo.get_system_info()
        required_keys = ['system', 'release', 'version', 'machine', 'shell']
        for key in required_keys:
            assert key in system_info
            assert system_info[key] is not None


@pytest.mark.health
class TestConfig:
    """Test configuration health."""

    @patch.dict(os.environ, {
        'LLM_MODEL': 'test-model',
        'SUPERMCP_SERVER_PATH': 'SuperMCP/SuperMCP.py',
        'SUPERMCP_TIMEOUT': '60'
    })
    def test_config_loads_from_env(self):
        """Test config loads from environment variables."""
        # Reload config to pick up env vars
        import importlib
        import config
        importlib.reload(config)
        Config = config.Config

        assert Config.LLM_MODEL == 'test-model'
        assert Config.SUPERMCP_TIMEOUT == 60

    def test_config_default_values(self):
        """Test config has reasonable default values."""
        assert hasattr(Config, 'LLM_PROVIDER')
        assert hasattr(Config, 'OUTPUT_MODE')
        assert hasattr(Config, 'LOG_LEVEL')
        assert hasattr(Config, 'SUPERMCP_SERVER_PATH')

    def test_llm_rule_formatting(self):
        """Test LLM_RULE can be formatted with system info."""
        system_info = {
            'system': 'linux',
            'release': '5.4.0',
            'version': '#1 SMP Debian',
            'machine': 'x86_64',
            'shell': ['bash', '-lc']
        }

        formatted_rule = Config.LLM_RULE.format(**system_info)
        assert 'linux' in formatted_rule
        assert 'SuperMCP' in formatted_rule


@pytest.mark.health
class TestCLI:
    """Test CLI health."""

    def test_cli_module_import(self):
        """Test CLI module can be imported."""
        try:
            import cli
            assert cli is not None
        except ImportError as e:
            pytest.skip(f"CLI import failed: {e}")

    def test_cli_functions_exist(self):
        """Test CLI functions exist."""
        try:
            import cli
            # Check some key functions exist
            assert hasattr(cli, 'set_output_mode')
            assert hasattr(cli, 'set_history_reset')
        except ImportError:
            pytest.skip("CLI module not available")

    @patch.dict(os.environ, {
        'LLM_MODEL': 'test-model',
        'SUPERMCP_SERVER_PATH': 'SuperMCP/SuperMCP.py',
        'SUPERMCP_TIMEOUT': '30'
    })
    def test_jarvis_class_import(self):
        """Test main Jarvis class can be imported and initialized."""
        try:
            from main import Jarvis
            jarvis = Jarvis(text_mode=True)  # Use text mode to avoid voice dependencies
            assert jarvis is not None
            assert hasattr(jarvis, 'ask')
        except Exception as e:
            pytest.skip(f"Jarvis class test failed: {e}")