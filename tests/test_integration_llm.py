"""
LLM integration tests for JARVIS AI Assistant.

Tests LLM provider initialization, JSON response parsing,
context management, and error handling.
"""

import pytest
import json
from unittest.mock import Mock, patch
from tests.integration_utils import (
    mock_llm_response,
    assert_valid_json_response,
    create_mock_llm_provider,
)


@pytest.mark.integration
class TestLLMProviderIntegration:
    """Test LLM provider initialization and basic functionality."""

    def test_ollama_provider_initialization(self):
        from jarvis.llm.providers.ollama import OllamaProvider
        provider = OllamaProvider("test-model")
        assert provider is not None
        assert provider.model == "test-model"
        assert hasattr(provider, 'chat')
        assert hasattr(provider, 'is_available')

    def test_ollama_provider_with_custom_url(self):
        from jarvis.llm.providers.ollama import OllamaProvider
        custom_url = "http://localhost:8080"
        provider = OllamaProvider("test-model", base_url=custom_url)
        assert provider.base_url == custom_url

    def test_api_provider_initialization(self):
        from jarvis.llm.providers.api import APIProvider
        provider = APIProvider("test-model", api_url="http://localhost:8080", api_key="test-key")
        assert provider is not None
        assert provider.model == "test-model"

    def test_llm_provider_factory_ollama(self):
        from jarvis.llm.providers import create_provider
        provider = create_provider(provider="ollama", model="test-model")
        assert provider is not None
        assert hasattr(provider, 'chat')

    def test_llm_provider_factory_api(self):
        from jarvis.llm.providers import create_provider
        provider = create_provider(
            provider="api", model="gpt-3.5-turbo",
            api_url="http://localhost:8080", api_key="test-key",
        )
        assert provider is not None
        assert hasattr(provider, 'chat')


@pytest.mark.integration
class TestLLMJsonResponseParsing:
    """Test LLM JSON response parsing and validation."""

    def test_valid_conversation_response_parsing(self):
        response = mock_llm_response("Conversation", "Hello! How can I help?")
        assert_valid_json_response(response)
        assert response["user_request"] == "Conversation"
        assert response["output"] == "Hello! How can I help?"

    def test_valid_supermcp_response_parsing(self):
        commands = "list_servers(); call_server_tool(EchoMCP, echo, {message: 'test'})"
        response = mock_llm_response("SuperMCP", commands)
        assert_valid_json_response(response)
        assert response["user_request"] == "SuperMCP"
        assert "list_servers()" in response["output"]

    def test_malformed_json_then_recovery(self):
        """Test that malformed JSON triggers a retry that succeeds."""
        from jarvis.llm import LLM

        mock_provider = Mock()
        mock_provider.model = "test"
        # preload -> malformed -> recovery
        mock_provider.chat.side_effect = [
            json.dumps({"status": "ready"}),  # preload
            '{"user_request": "Conversation", "output": "test"',  # malformed
            json.dumps(mock_llm_response("Conversation", "Recovered!")),  # retry
        ]

        llm = LLM(
            provider=mock_provider,
            system_prompt="test",
            wrong_json_message="Fix your JSON",
        )

        result = llm.ask("test question")
        assert_valid_json_response(result)
        assert result["output"] == "Recovered!"

    def test_valid_json_with_unknown_type_is_parsed(self):
        """Test that valid JSON with an unrecognized type is still returned."""
        from jarvis.llm import LLM

        mock_provider = Mock()
        mock_provider.model = "test"
        mock_provider.chat.side_effect = [
            json.dumps({"status": "ready"}),  # preload
            '{"user_request": "InvalidType", "output": "test"}',
        ]

        llm = LLM(provider=mock_provider, system_prompt="test")
        result = llm.ask("test")

        assert isinstance(result, dict)
        assert result["user_request"] == "InvalidType"

    def test_invalid_user_request_type(self):
        invalid_responses = [
            '{"user_request": "invalid_type", "output": "test"}',
            '{"user_request": "", "output": "test"}',
        ]

        for invalid_json in invalid_responses:
            try:
                response = json.loads(invalid_json)
                assert_valid_json_response(response)
                assert False, f"Should have failed validation: {invalid_json}"
            except AssertionError:
                pass

    def test_missing_required_fields(self):
        invalid_responses = [
            '{"output": "test"}',
            '{"user_request": "Conversation"}',
            '{}',
        ]

        for invalid_json in invalid_responses:
            try:
                response = json.loads(invalid_json)
                assert_valid_json_response(response)
                assert False, f"Should have failed validation: {invalid_json}"
            except AssertionError:
                pass


@pytest.mark.integration
class TestLLMResponseTypes:
    """Test different LLM response types and workflows."""

    def test_conversation_response_workflow(self):
        from jarvis.llm import LLM

        provider = create_mock_llm_provider([
            mock_llm_response("Conversation", "Hello! I'm JARVIS.")
        ])

        llm = LLM(provider=provider, system_prompt="test")
        result = llm.ask("Hello")

        assert_valid_json_response(result)
        assert result["user_request"] == "Conversation"
        assert "JARVIS" in result["output"]

    def test_supermcp_response_workflow(self):
        from jarvis.llm import LLM

        commands = "list_servers(); inspect_server(EchoMCP)"
        provider = create_mock_llm_provider([
            mock_llm_response("SuperMCP", commands)
        ])

        llm = LLM(provider=provider, system_prompt="test")
        result = llm.ask("What servers are available?")

        assert_valid_json_response(result)
        assert result["user_request"] == "SuperMCP"
        assert "list_servers()" in result["output"]

    def test_llm_error_recovery(self):
        from jarvis.llm import LLM

        mock_provider = Mock()
        mock_provider.model = "test"
        mock_provider.chat.side_effect = [
            '{"user_request": "Conversation", "output": "test"',  # malformed
            json.dumps(mock_llm_response("Conversation", "Sorry, I had trouble.")),
        ]

        llm = LLM(provider=mock_provider, system_prompt="test", wrong_json_message="Fix JSON")
        result = llm.ask("test question")
        assert_valid_json_response(result)


@pytest.mark.integration
class TestLLMContextManagement:
    """Test LLM chat history and context management."""

    def test_chat_history_initialization(self):
        from jarvis.llm import LLM

        provider = create_mock_llm_provider([
            mock_llm_response("Conversation", "Hello!")
        ])

        llm = LLM(provider=provider, system_prompt="You are JARVIS")
        assert len(llm.chat_history) > 0
        assert llm.chat_history[0]["role"] == "system"
        assert "JARVIS" in llm.chat_history[0]["content"]

    def test_chat_history_accumulation(self):
        from jarvis.llm import LLM

        provider = create_mock_llm_provider([
            mock_llm_response("Conversation", "First"),
            mock_llm_response("Conversation", "Second"),
        ])

        llm = LLM(provider=provider, system_prompt="test")
        llm.ask("First question")
        initial = len(llm.chat_history)
        llm.ask("Second question")
        assert len(llm.chat_history) > initial

    def test_chat_history_reset(self):
        from jarvis.llm import LLM

        provider = create_mock_llm_provider([
            mock_llm_response("Conversation", "Response"),
        ])

        llm = LLM(provider=provider, system_prompt="test")
        llm.ask("Question")
        assert len(llm.chat_history) > 1

        llm.reset_history()
        assert len(llm.chat_history) == 1
        assert llm.chat_history[0]["role"] == "system"


@pytest.mark.integration
class TestLLMProviderSwitching:
    """Test switching between different LLM providers."""

    def test_provider_switching_ollama_to_api(self):
        from jarvis.llm.providers import create_provider

        ollama = create_provider(provider="ollama", model="llama2")
        api = create_provider(
            provider="api", model="gpt-3.5-turbo",
            api_url="http://localhost:8080", api_key="test-key",
        )
        assert ollama is not None
        assert api is not None
        assert api != ollama

    def test_provider_availability_check(self):
        from jarvis.llm.providers.ollama import OllamaProvider
        from jarvis.llm.providers.api import APIProvider

        ollama = OllamaProvider("test-model")
        assert isinstance(ollama.is_available(), bool)

        api = APIProvider("test-model", api_url="http://localhost:8080", api_key="fake")
        assert isinstance(api.is_available(), bool)


@pytest.mark.integration
class TestLLMErrorHandling:
    """Test LLM error handling and edge cases."""

    def test_provider_initialization_failure(self):
        from jarvis.llm.providers import create_provider
        with pytest.raises(ValueError):
            create_provider(provider="invalid_provider", model="test")

    def test_missing_model(self):
        from jarvis.llm.providers import create_provider
        with pytest.raises(ValueError):
            create_provider(provider="ollama", model="")

    def test_llm_timeout_handling(self):
        from jarvis.llm import LLM

        mock_provider = Mock()
        mock_provider.model = "test"
        mock_provider.chat.side_effect = TimeoutError("Request timed out")

        llm = LLM(provider=mock_provider, system_prompt="test")
        with pytest.raises(TimeoutError):
            llm.ask("test question")

    def test_empty_llm_response(self):
        from jarvis.llm import LLM

        provider = create_mock_llm_provider([
            "",
            mock_llm_response("Conversation", "Sorry, I didn't understand."),
        ])

        llm = LLM(provider=provider, system_prompt="test", wrong_json_message="Fix JSON")
        result = llm.ask("test")
        assert_valid_json_response(result)
