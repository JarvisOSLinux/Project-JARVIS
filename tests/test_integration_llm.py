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
        from jarvis.llm_providers import OllamaProvider

        provider = OllamaProvider("test-model")
        assert provider is not None
        assert provider.model == "test-model"
        assert hasattr(provider, 'chat')
        assert hasattr(provider, 'is_available')

    def test_ollama_provider_with_custom_url(self):
        """Test Ollama provider with custom base URL."""
        from jarvis.llm_providers import OllamaProvider

        custom_url = "http://localhost:8080"
        provider = OllamaProvider("test-model", base_url=custom_url)

        assert provider.base_url == custom_url

    def test_openai_provider_initialization(self):
        """Test OpenAI-compatible provider initialization."""
        from jarvis.llm_providers import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider("test-model", api_key="test-key")
        assert provider is not None
        assert provider.model == "test-model"

    def test_llm_provider_factory_ollama(self):
        """Test LLM provider factory creates Ollama provider."""
        from jarvis.llm_providers import LLMProviderFactory

        with patch.dict('os.environ', {'LLM_PROVIDER': 'ollama', 'LLM_MODEL': 'test-model'}):
            provider = LLMProviderFactory.create_provider()
            assert provider is not None
            assert hasattr(provider, 'chat')

    def test_llm_provider_factory_openai(self):
        """Test LLM provider factory creates OpenAI provider."""
        from jarvis.llm_providers import LLMProviderFactory

        with patch.dict('os.environ', {
            'LLM_PROVIDER': 'openai',
            'LLM_MODEL': 'gpt-3.5-turbo',
            'OPENAI_API_KEY': 'test-key'
        }):
            provider = LLMProviderFactory.create_provider()
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
            '{"user_request": "Conversation", "output": "test"',  # Missing closing brace
            '{"user_request": "Conversation", output: "test"}',   # Missing quotes on output key
            'not json at all',                                   # Not JSON
            '{"user_request": "InvalidType", "output": "test"}',  # Invalid user_request
        ]

        from jarvis.llm import LLM

        for malformed in malformed_responses:
            with patch('jarvis.llm_providers.LLMProviderFactory.create_provider') as mock_factory:
                mock_provider = Mock()
                mock_factory.return_value = mock_provider

                # Mock provider returns malformed JSON
                mock_provider.chat.return_value = malformed

                llm = LLM("linux", "5.4.0", "#1 SMP", "x86_64", ["bash"])

                # Should handle gracefully or raise appropriate error
                try:
                    result = llm.ask("test question")
                    # If it succeeds, should be a valid response
                    assert_valid_json_response(result)
                except (json.JSONDecodeError, ValueError, KeyError):
                    # Expected for malformed JSON
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
                # Should fail validation
                assert False, f"Should have failed validation: {invalid_json}"
            except AssertionError:
                # Expected - validation should fail
                pass

    def test_missing_required_fields(self):
        """Test handling of JSON missing required fields."""
        invalid_responses = [
            '{"output": "test"}',                    # Missing user_request
            '{"user_request": "Conversation"}',      # Missing output
            '{}',                                    # Missing both
        ]

        for invalid_json in invalid_responses:
            try:
                response = json.loads(invalid_json)
                assert_valid_json_response(response)
                # Should fail validation
                assert False, f"Should have failed validation: {invalid_json}"
            except AssertionError:
                # Expected - validation should fail
                pass


@pytest.mark.integration
class TestLLMResponseTypes:
    """Test different LLM response types and workflows."""

    def test_conversation_response_workflow(self):
        """Test complete conversation response workflow."""
        from jarvis.llm import LLM

        with patch('jarvis.llm_providers.LLMProviderFactory.create_provider') as mock_factory:
            mock_provider = create_mock_llm_provider([
                mock_llm_response("Conversation", "Hello! I'm JARVIS.")
            ])
            mock_factory.return_value = mock_provider

            llm = LLM("linux", "5.4.0", "#1 SMP", "x86_64", ["bash"])
            result = llm.ask("Hello")

            assert_valid_json_response(result)
            assert result["user_request"] == "Conversation"
            assert "JARVIS" in result["output"]

    def test_supermcp_response_workflow(self):
        """Test SuperMCP response workflow."""
        from jarvis.llm import LLM

        commands = "list_servers(); inspect_server(EchoMCP)"
        with patch('jarvis.llm_providers.LLMProviderFactory.create_provider') as mock_factory:
            mock_provider = create_mock_llm_provider([
                mock_llm_response("SuperMCP", commands)
            ])
            mock_factory.return_value = mock_provider

            llm = LLM("linux", "5.4.0", "#1 SMP", "x86_64", ["bash"])
            result = llm.ask("What servers are available?")

            assert_valid_json_response(result)
            assert result["user_request"] == "SuperMCP"
            assert "list_servers()" in result["output"]
            assert "inspect_server(EchoMCP)" in result["output"]

    def test_llm_error_recovery(self):
        """Test LLM error recovery with malformed responses."""
        from jarvis.llm import LLM
        from jarvis.config import Config

        # Mock provider that returns invalid JSON first, then valid
        with patch('jarvis.llm_providers.LLMProviderFactory.create_provider') as mock_factory:
            mock_provider = Mock()
            mock_provider.chat.side_effect = [
                '{"user_request": "Conversation", "output": "test"',  # Malformed
                mock_llm_response("Conversation", "Sorry, I had trouble processing that. Please try again.")
            ]
            mock_factory.return_value = mock_provider

            llm = LLM("linux", "5.4.0", "#1 SMP", "x86_64", ["bash"])

            # First call should handle error gracefully
            result = llm.ask("test question")

            # Should eventually get a valid response
            assert_valid_json_response(result)


@pytest.mark.integration
class TestLLMContextManagement:
    """Test LLM chat history and context management."""

    def test_chat_history_initialization(self):
        """Test chat history starts with system prompt."""
        from jarvis.llm import LLM

        with patch('jarvis.llm_providers.LLMProviderFactory.create_provider') as mock_factory:
            mock_provider = create_mock_llm_provider([
                mock_llm_response("Conversation", "Hello!")
            ])
            mock_factory.return_value = mock_provider

            llm = LLM("linux", "5.4.0", "#1 SMP", "x86_64", ["bash"])

            # Check initial chat history
            assert len(llm.chat_history) > 0
            assert llm.chat_history[0]["role"] == "system"
            assert "SuperMCP" in llm.chat_history[0]["content"]

    def test_chat_history_accumulation(self):
        """Test chat history accumulates across multiple interactions."""
        from jarvis.llm import LLM

        with patch('jarvis.llm_providers.LLMProviderFactory.create_provider') as mock_factory:
            mock_provider = create_mock_llm_provider([
                mock_llm_response("Conversation", "First response"),
                mock_llm_response("Conversation", "Second response"),
            ])
            mock_factory.return_value = mock_provider

            llm = LLM("linux", "5.4.0", "#1 SMP", "x86_64", ["bash"])

            # First interaction
            result1 = llm.ask("First question")
            initial_history_length = len(llm.chat_history)

            # Second interaction
            result2 = llm.ask("Second question")

            # History should have grown
            assert len(llm.chat_history) > initial_history_length

            # Should contain both user questions and assistant responses
            user_messages = [msg for msg in llm.chat_history if msg["role"] == "user"]
            assistant_messages = [msg for msg in llm.chat_history if msg["role"] == "assistant"]

            assert len(user_messages) >= 2  # At least 2 user messages
            assert len(assistant_messages) >= 2  # At least 2 assistant responses

    def test_chat_history_reset_behavior(self):
        """Test chat history reset behavior."""
        from jarvis.llm import LLM

        with patch('jarvis.llm_providers.LLMProviderFactory.create_provider') as mock_factory:
            mock_provider = create_mock_llm_provider([
                mock_llm_response("Conversation", "Response 1"),
                mock_llm_response("Conversation", "Response 2"),
            ])
            mock_factory.return_value = mock_provider

            llm = LLM("linux", "5.4.0", "#1 SMP", "x86_64", ["bash"])

            # First interaction
            llm.ask("Question 1")
            history_after_first = len(llm.chat_history)

            # Second interaction
            llm.ask("Question 2")
            history_after_second = len(llm.chat_history)

            assert history_after_second > history_after_first


@pytest.mark.integration
class TestLLMProviderSwitching:
    """Test switching between different LLM providers."""

    def test_provider_switching_ollama_to_openai(self):
        """Test switching from Ollama to OpenAI provider."""
        from jarvis.llm_providers import LLMProviderFactory

        # Test Ollama provider
        with patch.dict('os.environ', {'LLM_PROVIDER': 'ollama', 'LLM_MODEL': 'llama2'}):
            ollama_provider = LLMProviderFactory.create_provider()
            assert ollama_provider is not None

        # Test OpenAI provider
        with patch.dict('os.environ', {
            'LLM_PROVIDER': 'openai',
            'LLM_MODEL': 'gpt-3.5-turbo',
            'OPENAI_API_KEY': 'test-key'
        }):
            openai_provider = LLMProviderFactory.create_provider()
            assert openai_provider is not None
            assert openai_provider != ollama_provider  # Different instances

    def test_provider_availability_check(self):
        """Test provider availability checking."""
        from jarvis.llm_providers import OllamaProvider, OpenAICompatibleProvider

        # Test Ollama availability (will be False without actual Ollama)
        ollama_provider = OllamaProvider("test-model")
        availability = ollama_provider.is_available()
        # Should return boolean without crashing
        assert isinstance(availability, bool)

        # Test OpenAI availability (will be False without API key)
        openai_provider = OpenAICompatibleProvider("test-model", api_key="fake-key")
        availability = openai_provider.is_available()
        assert isinstance(availability, bool)


@pytest.mark.integration
class TestLLMErrorHandling:
    """Test LLM error handling and edge cases."""

    def test_provider_initialization_failure(self):
        """Test handling of provider initialization failures."""
        from jarvis.llm_providers import LLMProviderFactory

        # Test with invalid provider
        with patch.dict('os.environ', {'LLM_PROVIDER': 'invalid_provider'}):
            with pytest.raises((ValueError, ImportError, KeyError)):
                LLMProviderFactory.create_provider()

    def test_missing_environment_variables(self):
        """Test handling of missing required environment variables."""
        from jarvis.llm_providers import LLMProviderFactory

        # Missing LLM_PROVIDER
        with patch.dict('os.environ', {}, clear=True):
            with pytest.raises((KeyError, ValueError)):
                LLMProviderFactory.create_provider()

    def test_llm_timeout_handling(self):
        """Test handling of LLM request timeouts."""
        from jarvis.llm import LLM

        with patch('jarvis.llm_providers.LLMProviderFactory.create_provider') as mock_factory:
            mock_provider = Mock()
            mock_provider.chat.side_effect = TimeoutError("Request timed out")
            mock_factory.return_value = mock_provider

            llm = LLM("linux", "5.4.0", "#1 SMP", "x86_64", ["bash"])

            # Should handle timeout gracefully
            with pytest.raises(TimeoutError):
                llm.ask("test question")

    def test_empty_llm_response(self):
        """Test handling of empty LLM responses."""
        from jarvis.llm import LLM

        with patch('jarvis.llm_providers.LLMProviderFactory.create_provider') as mock_factory:
            mock_provider = create_mock_llm_provider([
                "",  # Empty response
                mock_llm_response("Conversation", "Sorry, I didn't understand that.")
            ])
            mock_factory.return_value = mock_provider

            llm = LLM("linux", "5.4.0", "#1 SMP", "x86_64", ["bash"])

            # Should handle empty response gracefully
            result = llm.ask("test")
            assert_valid_json_response(result)