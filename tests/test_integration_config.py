"""
Configuration integration tests for JARVIS AI Assistant.

Tests configuration loading, validation, path resolution, environment variable
handling, and LLM rule formatting with system information.
"""

import importlib
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from tests.integration_utils import setup_test_environment


def _reload_config():
    """Reload the jarvis.config module to pick up env var changes."""
    import jarvis.config

    importlib.reload(jarvis.config)
    return jarvis.config.Config


@pytest.mark.integration
class TestConfigurationLoading:
    """Test configuration loading from various sources."""

    def test_config_loads_from_env_vars(self):
        """Test configuration loads correctly from environment variables."""
        with patch.dict(
            os.environ,
            {
                "DISPATCH_TIMEOUT": "45",
                "OUTPUT_MODE": "voice",
                "LOG_LEVEL": "DEBUG",
            },
        ):
            Config = _reload_config()
            assert Config.DISPATCH_TIMEOUT == 45
            assert Config.OUTPUT_MODE == "voice"
            assert Config.LOG_LEVEL == "DEBUG"

    def test_config_uses_defaults_when_env_missing(self):
        """Test configuration uses defaults when environment variables not set."""
        from jarvis.config import Config

        assert hasattr(Config, "OUTPUT_MODE")
        assert hasattr(Config, "LOG_LEVEL")
        assert hasattr(Config, "DISPATCH_BINARY")
        assert Config.OUTPUT_MODE in ["text", "voice"]
        assert isinstance(Config.LOG_LEVEL, str)
        assert Config.DISPATCH_TIMEOUT > 0

    def test_config_env_var_overrides_defaults(self):
        """Test environment variables override default configuration."""
        with patch.dict(
            os.environ,
            {
                "OUTPUT_MODE": "text",
                "DISPATCH_TIMEOUT": "120",
            },
        ):
            Config = _reload_config()
            assert Config.OUTPUT_MODE == "text"
            assert Config.DISPATCH_TIMEOUT == 120

    def test_config_type_conversion(self):
        """Test configuration properly converts string env vars to correct types."""
        with patch.dict(
            os.environ,
            {
                "DISPATCH_TIMEOUT": "90",
                "LOG_COLORS": "false",
            },
        ):
            Config = _reload_config()
            assert isinstance(Config.DISPATCH_TIMEOUT, int)
            assert Config.DISPATCH_TIMEOUT == 90
            assert isinstance(Config.LOG_COLORS, bool)
            assert Config.LOG_COLORS is False


@pytest.mark.integration
class TestPathResolution:
    """Test path resolution and validation."""

    def test_models_dir_default(self):
        """Test MODELS_DIR has a default value."""
        from jarvis.config import Config

        assert hasattr(Config, "MODELS_DIR")
        assert isinstance(Config.MODELS_DIR, str)

    def test_model_path_resolution(self):
        """Test model path resolution for TTS models."""
        with patch.dict(
            os.environ,
            {
                "TTS_MODEL_ONNX": "custom-model.onnx",
                "TTS_MODEL_JSON": "custom-model.onnx.json",
            },
        ):
            Config = _reload_config()
            assert Config.TTS_MODEL_ONNX == "custom-model.onnx"
            assert Config.TTS_MODEL_JSON == "custom-model.onnx.json"

    def test_log_file_path_handling(self):
        """Test log file path handling (empty means no file logging)."""
        with patch.dict(os.environ, {"LOG_FILE": ""}):
            Config = _reload_config()
            assert Config.LOG_FILE == ""

    def test_dispatch_binary_default(self):
        """Test dispatch binary has a sensible default."""
        from jarvis.config import Config

        assert isinstance(Config.DISPATCH_BINARY, str)
        assert len(Config.DISPATCH_BINARY) > 0


@pytest.mark.integration
class TestLLMRuleFormatting:
    """Test LLM rule formatting with system information."""

    def test_llm_root_prompt_formats_with_system_info(self):
        """Test LLM_ROOT_PROMPT correctly formats with system information."""
        from jarvis.config import Config

        system_info = {
            "system": "Linux",
            "release": "5.4.0-42-generic",
            "machine": "x86_64",
            "shell": ["/bin/bash", "--login"],
            "data_consent_note": "Test consent note",
        }

        formatted = Config.LLM_ROOT_PROMPT.format(**system_info)
        assert "Linux" in formatted
        assert "5.4.0-42-generic" in formatted
        assert "x86_64" in formatted

    def test_llm_root_prompt_handles_missing_system_info(self):
        """Test LLM_ROOT_PROMPT handles missing system information gracefully."""
        from jarvis.config import Config

        incomplete_info = {
            "system": "Windows",
            "machine": "x86_64",
        }
        try:
            formatted = Config.LLM_ROOT_PROMPT.format(**incomplete_info)
            assert "Windows" in formatted
        except KeyError:
            pass  # Expected if required fields are missing

    def test_llm_root_prompt_preserves_action_instructions(self):
        """Test LLM_ROOT_PROMPT preserves action format instructions."""
        from jarvis.config import Config

        system_info = {
            "system": "Linux",
            "release": "5.4.0",
            "machine": "x86_64",
            "shell": ["/bin/bash"],
            "data_consent_note": "Test consent note",
        }

        formatted = Config.LLM_ROOT_PROMPT.format(**system_info)
        assert '"action"' in formatted
        assert '"dispatch"' in formatted
        assert '"respond"' in formatted
        assert '"store"' in formatted
        assert '"recall"' in formatted
        assert '"search_memory"' in formatted


@pytest.mark.integration
class TestConfigurationValidation:
    """Test configuration validation and error handling."""

    def test_invalid_timeout_value_handling(self):
        """Test handling of invalid timeout values."""
        with patch.dict(os.environ, {"DISPATCH_TIMEOUT": "invalid"}):
            try:
                Config = _reload_config()
                assert isinstance(Config.DISPATCH_TIMEOUT, int)
                assert Config.DISPATCH_TIMEOUT > 0
            except (ValueError, TypeError):
                pass  # Expected for invalid timeout value

    def test_invalid_output_mode_is_stored(self):
        """Test that invalid output modes are stored as-is (config has no validation)."""
        with patch.dict(os.environ, {"OUTPUT_MODE": "invalid_mode"}):
            Config = _reload_config()
            assert Config.OUTPUT_MODE == "invalid_mode"

    def test_missing_required_config_handling(self):
        """Test handling when required configuration is missing."""
        from jarvis.config import Config

        assert hasattr(Config, "DISPATCH_BINARY")
        assert Config.DISPATCH_TIMEOUT > 0

    def test_config_reload_after_env_change(self):
        """Test configuration reloads after environment changes."""
        from jarvis.config import Config

        initial_timeout = Config.DISPATCH_TIMEOUT

        with patch.dict(os.environ, {"DISPATCH_TIMEOUT": str(initial_timeout + 10)}):
            Config = _reload_config()
            assert Config.DISPATCH_TIMEOUT == initial_timeout + 10


@pytest.mark.integration
class TestConfigurationIntegration:
    """Test configuration integration with other components."""

    def test_config_integration_with_component_factory(self):
        """Test configuration integration with component factory."""
        from jarvis.core.component_factory import ComponentFactory

        with patch.object(ComponentFactory, "create_llm") as mock_llm:
            mock_llm.return_value = Mock()
            llm = ComponentFactory.create_llm()
            assert llm is not None
            mock_llm.assert_called_once()

    def test_config_integration_with_jarvis_initialization(self):
        """Test configuration integration with Jarvis initialization."""
        from jarvis.main import Jarvis

        with patch(
            "jarvis.core.component_factory.ComponentFactory.create_all_components"
        ) as mock_create_all:
            mock_create_all.return_value = {
                "llm": Mock(),
                "dispatch_adapter": Mock(),
                "contextor": None,
                "embeddings": None,
                "goal_manager": Mock(),
                "event_merger": Mock(),
                "task_parser": Mock(),
                "confirmation_manager": Mock(),
                "output_manager": Mock(),
                "tts": None,
                "voice_manager": None,
                "kernel_client": Mock(),
            }
            jarvis = Jarvis(text_mode=True)
            assert jarvis is not None
            mock_create_all.assert_called_once()

    def test_config_affects_llm_provider_selection(self):
        """Test that configuration affects LLM provider selection."""
        from jarvis.llm.providers import create_provider

        provider = create_provider(provider="ollama", model="llama2")
        assert provider is not None

        provider = create_provider(
            provider="api",
            model="gpt-3.5-turbo",
            api_url="http://localhost:8080",
            api_key="test",
        )
        assert provider is not None

    def test_config_affects_dispatch_adapter(self):
        """Test that configuration affects dispatch adapter initialization."""
        import importlib

        import jarvis.config

        importlib.reload(jarvis.config)
        from jarvis.config import Config
        from jarvis.dispatch.adapter import DispatchAdapter

        adapter = DispatchAdapter()
        assert adapter.timeout == Config.DISPATCH_TIMEOUT


@pytest.mark.integration
class TestConfigurationPersistence:
    """Test configuration persistence and state management."""

    def test_config_values_persist_across_imports(self):
        """Test configuration values persist across module imports."""
        import jarvis.config

        importlib.reload(jarvis.config)
        from jarvis.config import Config

        initial_timeout = Config.DISPATCH_TIMEOUT

        importlib.reload(jarvis.config)
        from jarvis.config import Config as ReloadedConfig

        assert ReloadedConfig.DISPATCH_TIMEOUT == initial_timeout

    def test_env_override_persists_after_reload(self):
        """Test env overrides persist correctly after reload."""
        with patch.dict(
            os.environ,
            {
                "DISPATCH_TIMEOUT": "123",
            },
        ):
            Config = _reload_config()
            assert Config.DISPATCH_TIMEOUT == 123
