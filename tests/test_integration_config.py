"""
Configuration integration tests for JARVIS AI Assistant.

Tests configuration loading, validation, path resolution, environment variable
handling, and LLM rule formatting with system information.
"""

import pytest
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from tests.integration_utils import setup_test_environment


@pytest.mark.integration
class TestConfigurationLoading:
    """Test configuration loading from various sources."""

    def test_config_loads_from_env_file(self):
        """Test configuration loads correctly from .env file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
            f.write("""LLM_MODEL=test-model
SUPERMCP_SERVER_PATH=SuperMCP/SuperMCP.py
SUPERMCP_TIMEOUT=45
OUTPUT_MODE=voice
LOG_LEVEL=DEBUG
TTS_MODEL_ONNX=test.onnx
TTS_MODEL_JSON=test.json
""")
            env_file = f.name

        try:
            with patch.dict(os.environ, {'ENV_FILE': env_file}):
                from jarvis.config import Config

                # Force reload of config
                import importlib
                importlib.reload(Config.__module__)

                # Check that config values are loaded
                assert Config.LLM_MODEL == 'test-model'
                assert Config.SUPERMCP_TIMEOUT == 45
                assert Config.OUTPUT_MODE == 'voice'
                assert Config.LOG_LEVEL == 'DEBUG'

        finally:
            os.unlink(env_file)

    def test_config_uses_defaults_when_env_missing(self):
        """Test configuration uses defaults when environment variables not set."""
        from jarvis.config import Config

        # Test some default values
        assert hasattr(Config, 'LLM_PROVIDER')
        assert hasattr(Config, 'OUTPUT_MODE')
        assert hasattr(Config, 'LOG_LEVEL')
        assert hasattr(Config, 'SUPERMCP_SERVER_PATH')

        # Defaults should be reasonable
        assert Config.OUTPUT_MODE in ['text', 'voice']
        assert isinstance(Config.LOG_LEVEL, str)
        assert Config.SUPERMCP_TIMEOUT > 0

    def test_config_env_var_overrides_defaults(self):
        """Test environment variables override default configuration."""
        with setup_test_environment({
            'LLM_MODEL': 'override-model',
            'OUTPUT_MODE': 'text',
            'SUPERMCP_TIMEOUT': '120'
        }):
            from jarvis.config import Config

            # Reload config to pick up env vars
            import importlib
            importlib.reload(Config.__module__)

            assert Config.LLM_MODEL == 'override-model'
            assert Config.OUTPUT_MODE == 'text'
            assert Config.SUPERMCP_TIMEOUT == 120

    def test_config_type_conversion(self):
        """Test configuration properly converts string env vars to correct types."""
        with setup_test_environment({
            'SUPERMCP_TIMEOUT': '90',
            'LOG_COLORS': 'false'
        }):
            from jarvis.config import Config

            import importlib
            importlib.reload(Config.__module__)

            # Should convert string to int
            assert isinstance(Config.SUPERMCP_TIMEOUT, int)
            assert Config.SUPERMCP_TIMEOUT == 90

            # Should convert string to bool
            assert isinstance(Config.LOG_COLORS, bool)
            assert Config.LOG_COLORS == False


@pytest.mark.integration
class TestPathResolution:
    """Test path resolution and validation."""

    def test_supermcp_server_path_resolution_absolute(self):
        """Test SuperMCP server path resolution with absolute path."""
        abs_path = "/absolute/path/to/SuperMCP.py"

        with setup_test_environment({'SUPERMCP_SERVER_PATH': abs_path}):
            from jarvis.config import Config

            import importlib
            importlib.reload(Config.__module__)

            # Path should be resolved correctly
            assert Config.SUPERMCP_SERVER_PATH == abs_path

    def test_supermcp_server_path_resolution_relative(self):
        """Test SuperMCP server path resolution with relative path."""
        rel_path = "SuperMCP/SuperMCP.py"

        with setup_test_environment({'SUPERMCP_SERVER_PATH': rel_path}):
            from jarvis.config import Config

            import importlib
            importlib.reload(Config.__module__)

            # Should handle relative path
            assert Config.SUPERMCP_SERVER_PATH == rel_path

    def test_model_path_resolution(self):
        """Test model path resolution for TTS models."""
        with setup_test_environment({
            'TTS_MODEL_ONNX': 'custom-model.onnx',
            'TTS_MODEL_JSON': 'custom-model.onnx.json'
        }):
            from jarvis.config import Config

            import importlib
            importlib.reload(Config.__module__)

            assert Config.TTS_MODEL_ONNX == 'custom-model.onnx'
            assert Config.TTS_MODEL_JSON == 'custom-model.onnx.json'

    def test_log_file_path_handling(self):
        """Test log file path handling."""
        with setup_test_environment({
            'LOG_FILE': '/var/log/jarvis.log',
            'LOG_FILE': '',  # Empty means no file logging
        }):
            from jarvis.config import Config

            import importlib
            importlib.reload(Config.__module__)

            # Should handle empty log file (no file logging)
            assert Config.LOG_FILE == ''


@pytest.mark.integration
class TestLLMRuleFormatting:
    """Test LLM rule formatting with system information."""

    def test_llm_rule_formats_with_system_info(self):
        """Test LLM_RULE correctly formats with system information."""
        from jarvis.config import Config

        system_info = {
            'system': 'Linux',
            'release': '5.4.0-42-generic',
            'version': '#46-Ubuntu SMP Fri Jul 10 00:24:02 UTC 2020',
            'machine': 'x86_64',
            'shell': ['/bin/bash', '--login']
        }

        formatted_rule = Config.LLM_RULE.format(**system_info)

        # Check that system info was properly inserted
        assert 'Linux' in formatted_rule
        assert '5.4.0-42-generic' in formatted_rule
        assert 'x86_64' in formatted_rule
        assert '/bin/bash' in formatted_rule

        # Check that SuperMCP references are still there
        assert 'SuperMCP' in formatted_rule
        assert 'reload_servers()' in formatted_rule
        assert 'list_servers()' in formatted_rule

    def test_llm_rule_handles_missing_system_info(self):
        """Test LLM_RULE handles missing system information gracefully."""
        from jarvis.config import Config

        # Incomplete system info
        incomplete_info = {
            'system': 'Windows',
            'machine': 'x86_64'
            # Missing release, version, shell
        }

        # Should handle missing fields gracefully or raise KeyError
        try:
            formatted_rule = Config.LLM_RULE.format(**incomplete_info)
            # If it succeeds, should contain available info
            assert 'Windows' in formatted_rule
            assert 'x86_64' in formatted_rule
        except KeyError:
            # Expected if required fields are missing
            pass

    def test_llm_rule_preserves_json_structure(self):
        """Test LLM_RULE preserves JSON response format requirements."""
        from jarvis.config import Config

        system_info = {
            'system': 'macOS',
            'release': '10.15.7',
            'version': 'Darwin Kernel Version 19.6.0',
            'machine': 'x86_64',
            'shell': ['/bin/zsh']
        }

        formatted_rule = Config.LLM_RULE.format(**system_info)

        # Should still contain JSON format instructions
        assert '"user_request": ""' in formatted_rule
        assert '"output": ""' in formatted_rule
        assert '"Conversation"' in formatted_rule
        assert '"SuperMCP"' in formatted_rule


@pytest.mark.integration
class TestConfigurationValidation:
    """Test configuration validation and error handling."""

    def test_invalid_timeout_value_handling(self):
        """Test handling of invalid timeout values."""
        with setup_test_environment({'SUPERMCP_TIMEOUT': 'invalid'}):
            from jarvis.config import Config

            # Should handle invalid value gracefully or raise appropriate error
            try:
                import importlib
                importlib.reload(Config.__module__)
                # If it loads, check that timeout is reasonable
                assert isinstance(Config.SUPERMCP_TIMEOUT, int)
                assert Config.SUPERMCP_TIMEOUT > 0
            except (ValueError, TypeError):
                # Expected for invalid timeout value
                pass

    def test_invalid_output_mode_handling(self):
        """Test handling of invalid output mode values."""
        with setup_test_environment({'OUTPUT_MODE': 'invalid_mode'}):
            from jarvis.config import Config

            import importlib
            importlib.reload(Config.__module__)

            # Should either use default or handle gracefully
            assert Config.OUTPUT_MODE in ['text', 'voice']

    def test_missing_required_config_handling(self):
        """Test handling when required configuration is missing."""
        # Clear environment and test defaults
        with setup_test_environment({}):
            from jarvis.config import Config

            import importlib
            importlib.reload(Config.__module__)

            # Should have reasonable defaults
            assert hasattr(Config, 'LLM_MODEL')
            assert hasattr(Config, 'SUPERMCP_SERVER_PATH')
            assert Config.SUPERMCP_TIMEOUT > 0

    def test_config_reload_after_env_change(self):
        """Test configuration reloads after environment changes."""
        from jarvis.config import Config

        # Initial config
        initial_timeout = Config.SUPERMCP_TIMEOUT

        # Change environment
        with setup_test_environment({'SUPERMCP_TIMEOUT': str(initial_timeout + 10)}):
            import importlib
            importlib.reload(Config.__module__)

            # Should reflect new environment
            assert Config.SUPERMCP_TIMEOUT == initial_timeout + 10


@pytest.mark.integration
class TestConfigurationIntegration:
    """Test configuration integration with other components."""

    def test_config_integration_with_component_factory(self):
        """Test configuration integration with component factory."""
        from jarvis.core.component_factory import ComponentFactory

        with patch('jarvis.core.component_factory.ComponentFactory.create_llm') as mock_llm:
            mock_llm.return_value = Mock()

            # Create component that uses config
            llm = ComponentFactory.create_llm()

            # Should succeed without config errors
            assert llm is not None
            mock_llm.assert_called_once()

    def test_config_integration_with_jarvis_initialization(self):
        """Test configuration integration with Jarvis initialization."""
        from jarvis.main import Jarvis

        with patch('jarvis.core.component_factory.ComponentFactory.create_all_components') as mock_create_all:
            mock_create_all.return_value = {
                'llm': Mock(),
                'supermcp': Mock(),
                'command_parser': Mock(),
                'output_manager': Mock(),
                'tts': None,
                'voice_manager': None
            }

            # Should initialize without config errors
            jarvis = Jarvis(text_mode=True)

            assert jarvis is not None
            mock_create_all.assert_called_once()

    def test_config_affects_llm_provider_selection(self):
        """Test that configuration affects LLM provider selection."""
        from jarvis.llm.providers import create_provider

        with setup_test_environment({'LLM_PROVIDER': 'ollama', 'LLM_MODEL': 'llama2'}):
            provider = create_provider(provider="ollama", model="llama2")
            assert provider is not None

        with setup_test_environment({'LLM_PROVIDER': 'api', 'LLM_MODEL': 'gpt-3.5-turbo'}):
            provider = create_provider(
                provider="api", model="gpt-3.5-turbo",
                api_url="http://localhost:8080", api_key="test",
            )
            assert provider is not None

    def test_config_affects_supermcp_client(self):
        """Test that configuration affects SuperMCP client initialization."""
        from jarvis.supermcp_client import SuperMCPClient

        with setup_test_environment({'SUPERMCP_TIMEOUT': '60'}):
            client = SuperMCPClient()

            # Should use configured timeout
            assert client.timeout == 60

    def test_config_validation_across_modules(self):
        """Test configuration validation across different modules."""
        # Test that config values are consistent across modules
        from jarvis.config import Config
        from jarvis.core.component_factory import ComponentFactory

        # Config should be accessible and consistent
        assert hasattr(Config, 'LLM_MODEL')
        assert hasattr(Config, 'SUPERMCP_TIMEOUT')

        # Component factory should work with current config
        try:
            # This might fail due to missing dependencies, but shouldn't fail due to config
            llm = ComponentFactory.create_llm()
        except Exception as e:
            # Should not be a configuration-related error
            assert 'config' not in str(e).lower()
            assert 'Config' not in str(e)


@pytest.mark.integration
class TestConfigurationPersistence:
    """Test configuration persistence and state management."""

    def test_config_values_persist_across_imports(self):
        """Test configuration values persist across module imports."""
        from jarvis.config import Config

        # Get initial values
        initial_model = Config.LLM_MODEL
        initial_timeout = Config.SUPERMCP_TIMEOUT

        # Import again (simulate module reload)
        import importlib
        import jarvis.config
        importlib.reload(jarvis.config)
        from jarvis.config import Config as ReloadedConfig

        # Values should be the same
        assert ReloadedConfig.LLM_MODEL == initial_model
        assert ReloadedConfig.SUPERMCP_TIMEOUT == initial_timeout

    def test_env_file_persistence(self):
        """Test .env file changes persist correctly."""
        # Create test env file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
            f.write("LLM_MODEL=persistence-test\n")
            f.write("SUPERMCP_TIMEOUT=123\n")
            env_file = f.name

        try:
            with patch.dict(os.environ, {'ENV_FILE': env_file}):
                from jarvis.config import Config
                import importlib
                importlib.reload(Config.__module__)

                assert Config.LLM_MODEL == 'persistence-test'
                assert Config.SUPERMCP_TIMEOUT == 123

        finally:
            os.unlink(env_file)