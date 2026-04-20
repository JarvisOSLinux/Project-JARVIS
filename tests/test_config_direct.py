"""
Direct unit tests for jarvis.config module
"""

import os
from unittest.mock import patch

import pytest


@pytest.mark.config
class TestConfigDirect:
    """Direct test cases for Config class"""

    def test_config_import(self):
        """Test that config can be imported"""
        from jarvis.config import Config

        assert Config is not None

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "test-model",
            "TTS_MODEL_ONNX": "test.onnx",
            "TTS_MODEL_JSON": "test.json",
            "DISPATCH_TIMEOUT": "60",
        },
    )
    def test_config_values_from_env(self):
        """Test that config values are loaded from environment variables"""
        import importlib

        import jarvis.config

        importlib.reload(jarvis.config)
        Config = jarvis.config.Config

        assert Config.LLM_MODEL == "test-model"
        assert Config.TTS_MODEL_ONNX == "test.onnx"
        assert Config.TTS_MODEL_JSON == "test.json"
        assert Config.DISPATCH_TIMEOUT == 60

    def test_llm_root_prompt_content(self):
        """Test LLM_ROOT_PROMPT contains action instructions"""
        from jarvis.config import Config

        assert '"action"' in Config.LLM_ROOT_PROMPT
        assert '"dispatch"' in Config.LLM_ROOT_PROMPT
        assert '"respond"' in Config.LLM_ROOT_PROMPT
        assert '"store"' in Config.LLM_ROOT_PROMPT
        assert '"recall"' in Config.LLM_ROOT_PROMPT
        assert '"search_memory"' in Config.LLM_ROOT_PROMPT

    def test_llm_wrong_json_format_message(self):
        """Test LLM_WRONG_JSON_FORMAT_MESSAGE content"""
        from jarvis.config import Config

        message = Config.LLM_WRONG_JSON_FORMAT_MESSAGE

        assert "JSON" in message
        assert "action" in message

    def test_llm_root_prompt_formatting(self):
        """Test LLM_ROOT_PROMPT formatting with system information"""
        from jarvis.config import Config

        system_info = {
            "system": "linux",
            "release": "5.4.0",
            "machine": "x86_64",
            "shell": ["bash", "-lc"],
            "data_consent_note": "Test consent note",
        }

        formatted = Config.LLM_ROOT_PROMPT.format(**system_info)

        assert "linux" in formatted
        assert "5.4.0" in formatted
        assert "x86_64" in formatted

    def test_default_values(self):
        """Test default values when environment variables are not set"""
        from jarvis.config import Config

        assert hasattr(Config, "LLM_MODEL")
        assert hasattr(Config, "TTS_MODEL_ONNX")
        assert hasattr(Config, "TTS_MODEL_JSON")
        assert hasattr(Config, "DISPATCH_BINARY")
        assert isinstance(Config.DISPATCH_TIMEOUT, int)
        assert Config.DISPATCH_TIMEOUT > 0
