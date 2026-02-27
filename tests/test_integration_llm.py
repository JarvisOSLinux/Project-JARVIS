"""
LLM integration tests for JARVIS AI Assistant.

Tests LLM provider integration, JSON response parsing, response types,
context management, and error handling.
"""

import pytest
import json
from unittest.mock import Mock, patch
from tests.integration_utils import (
    mock_llm_response,
    assert_valid_json_response,
    create_mock_llm_provider
)


@pytest.mark.integration
class TestLLMProviderIntegration:
    """Test LLM provider initialization and basic functionality."""

    def test_ollama_provider_initialization(self):
        """Test Ollama provider initializes correctly."""
        from jarvis.llm.providers.ollama import OllamaProvider

        provider = OllamaProvider("test-model")
        assert provider is not None
        assert provider.model == "test-model"
        assert hasattr(provider, 'chat')
        assert hasattr(provider, 'is_available')

    def test_ollama_provider_with_custom_url(self):
        """Test Ollama provider with custom base URL."""
        from jarvis.llm.providers.ollama import OllamaProvider

        custom_url = "http://localhost:8080"
        provider = OllamaProvider("test-model", base_url=custom_url)

        assert provider.base_url == custom_url

    def test_api_provider_initialization(self):
        """Test API provider initialization."""
        from jarvis.llm.providers.api import APIProvider

        provider = APIProvider("test-model", api_url="http://localhost:8080", api_key="test-key")
        assert provider is not None
        assert provider.model == "test-model"

    def test_llm_provider_factory_ollama(self):
        """Test LLM provider factory creates Ollama provider."""
        from jarvis.llm.providers import create_provider

        provider = create_provider(provider="ollama", model="test-model")
        assert provider is not None
        assert hasattr(provider, 'chat')

    def test_llm_provider_factory_api(self):
        """Test LLM provider factory creates API provider."""
        from jarvis.llm.providers import create_provider

        provider = create_provider(
            provider="api",
            model="gpt-3.5-turbo",
            api_url="http://localhost:8080",
            api_key="test-key",
        )
        assert provider is not None
        assert hasattr(provider, 'chat')


@pytest.mark.integration
class TestLLMJsonResponseParsing:
    """Test LLM JSON response parsing and validation."""

    def test_valid_conversation_response_parsing(self, mock_llm_response):
        """Test parsing valid conversation response."""
        response = mock_llm_response("Conversation", "Hello! How can I help?")
        assert_valid_json_response(response)
        assert response["user_request"] == "Conversation"
        assert response["output"] == "Hello! How can I help?"

    def test_valid_supermcp_response_parsing(self, mock_llm_response):
        """Test parsing valid SuperMCP response."""
        commands = "list_servers(); call_server_tool(EchoMCP, echo, {message: 'test'})"
        response = mock_llm_response("SuperMCP", commands)
        assert_valid_json_response(response)
        assert response["user_request"] == "SuperMCP"
        assert "list_servers()" in response["output"]

    def test_malformed_json_handling(self):
        """Test handling of malformed JSON responses."""
        malformed_responses = [
            '{"user_request": "Conversation", "output": "test"',
            '{"user_request": "Conversation", output: "test"}',
            'not json at all',
            '{"user_request": "InvalidType", "output": "test"}',
        ]

        from jarvis.llm import LLM

        for malformed in malformed_responses:
            mock_provider = Mock()
            mock_provider.chat.return_value = malformed

            llm = LLM(
                provider=mock_provider,
                system_prompt="test",
                wrong_json_message="Fix your JSON",
            )

            try:
                result = llm.ask("test question")
                assert_valid_json_response(result)
            except (json.JSONDecodeError, ValueError, KeyError):
                pass

    def test_invalid_user_request_type(self):
        """Test handling of invalid user_request types."""
        invalid_responses = [
            '{"user_request": "invalid_type", "output": "test"}',
            '{"user_request": "", "output": "test"}',
            '{"user_request": null, "output": "test"}',
        ]

        for invalid_json in invalid_responses:
            try:
                response = json.loads(invalid_json)
                assert_valid_json_response(response)
                assert False, f"Should have failed validation: {invalid_json}"
            except AssertionError:
                pass

    def test_missing_required_fields(self):
        """Test handling of JSON missing required fields."""
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
        """Test complete conversation response workflow."""
        from jarvis.llm import LLM

        mock_provider = create_mock_llm_provider([
            mock_llm_response("Conversation", "Hello! I'm JARVIS.")
        ])

        llm = LLM(provider=mock_provider, system_prompt="test")
        result = llm.ask("Hello")

        assert_valid_json_response(result)
        assert result["user_request"] == "Conversation"
        assert "JARVIS" in result["output"]

    def test_supermcp_response_workflow(self):
        """Test SuperMCP response workflow."""
        from jarvis.llm import LLM

        commands = "list_servers(); inspect_server(EchoMCP)"
        mock_provider = create_mock_llm_provider([
            mock_llm_response("SuperMCP", commands)
        ])

        llm = LLM(provider=mock_provider, system_prompt="test")
        result = llm.ask("What servers are available?")

        assert_valid_json_response(result)
        assert result["user_request"] == "SuperMCP"
        assert "list_servers()" in result["output"]
        assert "inspect_server(EchoMCP)" in result["output"]

    def test_llm_error_recovery(self):
        """Test LLM error recovery with malformed responses."""
        from jarvis.llm import LLM

        mock_provider = Mock()
        mock_provider.chat.side_effect = [
            '{"user_request": "Conversation", "output": "test"',
            mock_llm_response("Conversation", "Sorry, I had trouble processing that.")
        ]

        llm = LLM(provider=mock_provider, system_prompt="test", wrong_json_message="Fix JSON")
        result = llm.ask("test question")
        assert_valid_json_response(result)


@pytest.mark.integration
class TestLLMContextManagement:
    """Test LLM chat history and context management."""

    def test_chat_history_initialization(self):
        """Test chat history starts with system prompt."""
        from jarvis.llm import LLM

        mock_provider = create_mock_llm_provider([
            mock_llm_response("Conversation", "Hello!")
        ])

        llm = LLM(provider=mock_provider, system_prompt="You are JARVIS with SuperMCP")

        assert len(llm.chat_history) > 0
        assert llm.chat_history[0]["role"] == "system"
        assert "SuperMCP" in llm.chat_history[0]["content"]

    def test_chat_history_accumulation(self):
        """Test chat history accumulates across multiple interactions."""
        from jarvis.llm import LLM

        mock_provider = create_mock_llm_provider([
            mock_llm_response("Conversation", "First response"),
            mock_llm_response("Conversation", "Second response"),
        ])

        llm = LLM(provider=mock_provider, system_prompt="test")

        result1 = llm.ask("First question")
        initial_history_length = len(llm.chat_history)

        result2 = llm.ask("Second question")
        assert len(llm.chat_history) > initial_history_length

        user_messages = [msg for msg in llm.chat_history if msg["role"] == "user"]
        assert len(user_messages) >= 2

    def test_chat_history_reset_behavior(self):
        """Test chat history reset behavior."""
        from jarvis.llm import LLM

        mock_provider = create_mock_llm_provider([
            mock_llm_response("Conversation", "Response 1"),
            mock_llm_response("Conversation", "Response 2"),
        ])

        llm = LLM(provider=mock_provider, system_prompt="test")

        llm.ask("Question 1")
        history_after_first = len(llm.chat_history)

        llm.ask("Question 2")
        history_after_second = len(llm.chat_history)

        assert history_after_second > history_after_first


@pytest.mark.integration
class TestLLMProviderSwitching:
    """Test switching between different LLM providers."""

    def test_provider_switching_ollama_to_api(self):
        """Test switching from Ollama to API provider."""
        from jarvis.llm.providers import create_provider

        ollama_provider = create_provider(provider="ollama", model="llama2")
        assert ollama_provider is not None

        api_provider = create_provider(
            provider="api",
            model="gpt-3.5-turbo",
            api_url="http://localhost:8080",
            api_key="test-key",
        )
        assert api_provider is not None
        assert api_provider != ollama_provider

    def test_provider_availability_check(self):
        """Test provider availability checking."""
        from jarvis.llm.providers.ollama import OllamaProvider
        from jarvis.llm.providers.api import APIProvider

        ollama_provider = OllamaProvider("test-model")
        availability = ollama_provider.is_available()
        assert isinstance(availability, bool)

        api_provider = APIProvider("test-model", api_url="http://localhost:8080", api_key="fake-key")
        availability = api_provider.is_available()
        assert isinstance(availability, bool)


@pytest.mark.integration
class TestLLMErrorHandling:
    """Test LLM error handling and edge cases."""

    def test_provider_initialization_failure(self):
        """Test handling of provider initialization failures."""
        from jarvis.llm.providers import create_provider

        with pytest.raises(ValueError):
            create_provider(provider="invalid_provider", model="test")

    def test_missing_model(self):
        """Test handling of missing model."""
        from jarvis.llm.providers import create_provider

        with pytest.raises(ValueError):
            create_provider(provider="ollama", model="")

    def test_llm_timeout_handling(self):
        """Test handling of LLM request timeouts."""
        from jarvis.llm import LLM

        mock_provider = Mock()
        mock_provider.chat.side_effect = TimeoutError("Request timed out")

        llm = LLM(provider=mock_provider, system_prompt="test")

        with pytest.raises(TimeoutError):
            llm.ask("test question")

    def test_empty_llm_response(self):
        """Test handling of empty LLM responses."""
        from jarvis.llm import LLM

        mock_provider = create_mock_llm_provider([
            "",
            mock_llm_response("Conversation", "Sorry, I didn't understand that.")
        ])

        llm = LLM(provider=mock_provider, system_prompt="test", wrong_json_message="Fix JSON")
        result = llm.ask("test")
        assert_valid_json_response(result)
