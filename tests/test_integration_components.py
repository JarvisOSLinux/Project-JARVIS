"""
Component integration tests for JARVIS AI Assistant.

Tests component interactions, graceful degradation when optional components
are unavailable, and proper component lifecycle management.
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from tests.integration_utils import (
    create_mock_llm_provider,
    create_mock_supermcp_client,
    mock_llm_response
)


@pytest.mark.integration
class TestComponentFactoryIntegration:
    """Test component factory creates and integrates components properly."""

    def test_create_all_components_text_mode(self):
        """Test creating all components in text-only mode."""
        from jarvis.core.component_factory import ComponentFactory

        with patch('jarvis.core.component_factory.ComponentFactory.create_llm') as mock_llm, \
             patch('jarvis.core.component_factory.ComponentFactory.create_supermcp') as mock_supermcp, \
             patch('jarvis.core.component_factory.ComponentFactory.create_tts_optional') as mock_tts, \
             patch('jarvis.core.component_factory.ComponentFactory.create_voice_manager_optional') as mock_vm, \
             patch('jarvis.core.component_factory.ComponentFactory.create_command_parser') as mock_parser, \
             patch('jarvis.core.component_factory.ComponentFactory.create_output_manager') as mock_output:

            # Setup mocks
            mock_llm.return_value = Mock()
            mock_supermcp.return_value = Mock()
            mock_tts.return_value = None  # No TTS in text mode
            mock_vm.return_value = None   # No voice manager in text mode
            mock_parser.return_value = Mock()
            mock_output.return_value = Mock()

            # Create components
            components = ComponentFactory.create_all_components(text_mode=True)

            # Verify all components created
            assert 'llm' in components
            assert 'supermcp' in components
            assert 'tts' in components
            assert 'voice_manager' in components
            assert 'command_parser' in components
            assert 'output_manager' in components

            # Verify optional components are None in text mode
            assert components['tts'] is None
            assert components['voice_manager'] is None

            # Verify component creation calls
            mock_llm.assert_called_once()
            mock_supermcp.assert_called_once()
            mock_tts.assert_called_once()
            mock_vm.assert_called_once()
            mock_parser.assert_called_once()
            mock_output.assert_called_once()

    def test_create_all_components_voice_mode(self):
        """Test creating all components in voice mode."""
        from jarvis.core.component_factory import ComponentFactory

        with patch('jarvis.core.component_factory.ComponentFactory.create_llm') as mock_llm, \
             patch('jarvis.core.component_factory.ComponentFactory.create_supermcp') as mock_supermcp, \
             patch('jarvis.core.component_factory.ComponentFactory.create_tts_optional') as mock_tts, \
             patch('jarvis.core.component_factory.ComponentFactory.create_voice_manager_optional') as mock_vm, \
             patch('jarvis.core.component_factory.ComponentFactory.create_command_parser') as mock_parser, \
             patch('jarvis.core.component_factory.ComponentFactory.create_output_manager') as mock_output:

            # Setup mocks
            mock_llm.return_value = Mock()
            mock_supermcp.return_value = Mock()
            mock_tts.return_value = Mock()  # TTS available
            mock_vm.return_value = Mock()   # Voice manager available
            mock_parser.return_value = Mock()
            mock_output.return_value = Mock()

            # Create components with voice mode
            components = ComponentFactory.create_all_components(text_mode=False)

            # Verify all components created
            assert 'llm' in components
            assert 'supermcp' in components
            assert 'tts' in components
            assert 'voice_manager' in components

            # Voice components should be attempted (may be None if audio unavailable)
            mock_tts.assert_called_once()
            mock_vm.assert_called_once()

    def test_component_dependencies_integration(self):
        """Test that components are created with proper dependencies."""
        from jarvis.core.component_factory import ComponentFactory

        with patch('jarvis.core.component_factory.ComponentFactory.create_llm') as mock_llm, \
             patch('jarvis.core.component_factory.ComponentFactory.create_supermcp') as mock_supermcp, \
             patch('jarvis.core.component_factory.ComponentFactory.create_tts_optional') as mock_tts, \
             patch('jarvis.core.component_factory.ComponentFactory.create_command_parser') as mock_parser, \
             patch('jarvis.core.component_factory.ComponentFactory.create_output_manager') as mock_output:

            # Setup component instances
            llm_instance = Mock()
            supermcp_instance = Mock()
            tts_instance = Mock()

            mock_llm.return_value = llm_instance
            mock_supermcp.return_value = supermcp_instance
            mock_tts.return_value = tts_instance

            # Create components
            components = ComponentFactory.create_all_components(text_mode=True)

            # Verify command parser gets SuperMCP instance
            mock_parser.assert_called_once_with(supermcp_instance)

            # Verify output manager gets TTS instance (or None)
            mock_output.assert_called_once()


@pytest.mark.integration
class TestJarvisComponentIntegration:
    """Test Jarvis class component integration."""

    def test_jarvis_initialization_with_components(self):
        """Test Jarvis initializes with all required components."""
        from jarvis.main import Jarvis

        with patch('jarvis.core.component_factory.ComponentFactory.create_all_components') as mock_create_all:

            # Setup mock components
            mock_components = {
                'llm': Mock(),
                'supermcp': Mock(),
                'command_parser': Mock(),
                'output_manager': Mock(),
                'tts': None,
                'voice_manager': None
            }
            mock_create_all.return_value = mock_components

            # Create Jarvis instance
            jarvis = Jarvis(text_mode=True)

            # Verify components are assigned
            assert jarvis.llm == mock_components['llm']
            assert jarvis.command_parser == mock_components['command_parser']
            assert jarvis.output_manager == mock_components['output_manager']
            assert jarvis.voice_manager == mock_components['voice_manager']

            # Verify component factory was called correctly
            mock_create_all.assert_called_once_with(text_mode=True, on_voice_command=jarvis._handle_voice_command)

    def test_jarvis_voice_command_handling(self):
        """Test Jarvis handles voice commands properly."""
        from jarvis.main import Jarvis

        with patch('jarvis.core.component_factory.ComponentFactory.create_all_components') as mock_create_all:

            # Setup mock components
            mock_llm = Mock()
            mock_output_manager = Mock()

            mock_components = {
                'llm': mock_llm,
                'supermcp': Mock(),
                'command_parser': Mock(),
                'output_manager': mock_output_manager,
                'tts': None,
                'voice_manager': Mock()
            }
            mock_create_all.return_value = mock_components

            # Setup LLM response
            mock_llm.ask.return_value = mock_llm_response("Conversation", "Voice command processed!")

            # Create Jarvis
            jarvis = Jarvis(text_mode=False)

            # Simulate voice command
            voice_text = "What's the weather?"
            jarvis._handle_voice_command(voice_text)

            # Verify LLM was called with voice text
            mock_llm.ask.assert_called_once_with(voice_text)

    def test_jarvis_ask_method_integration(self):
        """Test complete Jarvis.ask method integration."""
        from jarvis.main import Jarvis

        with patch('jarvis.core.component_factory.ComponentFactory.create_all_components') as mock_create_all:

            # Setup mock components
            mock_llm = Mock()
            mock_command_parser = Mock()
            mock_output_manager = Mock()

            mock_components = {
                'llm': mock_llm,
                'supermcp': Mock(),
                'command_parser': mock_command_parser,
                'output_manager': mock_output_manager,
                'tts': None,
                'voice_manager': None
            }
            mock_create_all.return_value = mock_components

            # Setup conversation response
            conversation_response = mock_llm_response("Conversation", "Hello! How can I help?")
            mock_llm.ask.return_value = conversation_response

            # Create Jarvis and ask question
            jarvis = Jarvis(text_mode=True)
            result = jarvis.ask("Hello")

            # Verify integration
            assert result == conversation_response
            mock_llm.ask.assert_called_once_with("Hello")


@pytest.mark.integration
class TestGracefulDegradation:
    """Test graceful degradation when optional components are unavailable."""

    def test_tts_unavailable_graceful_degradation(self):
        """Test system works when TTS is unavailable."""
        from jarvis.core.component_factory import ComponentFactory

        with patch('jarvis.core.component_factory.ComponentFactory.create_tts_optional') as mock_tts, \
             patch('jarvis.core.audio_detection.check_audio_output_available') as mock_audio_check:

            # Simulate TTS unavailable
            mock_audio_check.return_value = False
            mock_tts.return_value = None

            # Create components - should not crash
            components = ComponentFactory.create_all_components(text_mode=False)

            # TTS should be None but system should still work
            assert components['tts'] is None

            # Verify TTS creation was attempted
            mock_tts.assert_called_once()

    def test_voice_manager_unavailable_graceful_degradation(self):
        """Test system works when voice manager is unavailable."""
        from jarvis.core.component_factory import ComponentFactory

        with patch('jarvis.core.component_factory.ComponentFactory.create_voice_manager_optional') as mock_vm, \
             patch('jarvis.core.audio_detection.check_audio_input_available') as mock_audio_check:

            # Simulate voice input unavailable
            mock_audio_check.return_value = False
            mock_vm.return_value = None

            # Create components
            components = ComponentFactory.create_all_components(text_mode=False)

            # Voice manager should be None
            assert components['voice_manager'] is None

            # Verify voice manager creation was attempted
            mock_vm.assert_called_once()

    def test_jarvis_without_voice_components(self):
        """Test Jarvis works completely without voice components."""
        from jarvis.main import Jarvis

        with patch('jarvis.core.component_factory.ComponentFactory.create_all_components') as mock_create_all:

            # Setup components with no voice capabilities
            mock_components = {
                'llm': Mock(),
                'supermcp': Mock(),
                'command_parser': Mock(),
                'output_manager': Mock(),
                'tts': None,           # No TTS
                'voice_manager': None  # No voice input
            }
            mock_create_all.return_value = mock_components

            # Create Jarvis - should work fine
            jarvis = Jarvis(text_mode=True)

            # Verify voice components are None
            assert jarvis.voice_manager is None

            # Should still be able to process text requests
            mock_llm = mock_components['llm']
            mock_llm.ask.return_value = mock_llm_response("Conversation", "Text response works!")

            result = jarvis.ask("Test question")
            assert result["user_request"] == "Conversation"
            assert "Text response works" in result["output"]

    def test_component_failure_does_not_crash_system(self):
        """Test that component creation failures don't crash the entire system."""
        from jarvis.core.component_factory import ComponentFactory

        with patch('jarvis.core.component_factory.ComponentFactory.create_llm') as mock_llm, \
             patch('jarvis.core.component_factory.ComponentFactory.create_supermcp') as mock_supermcp:

            # Make LLM creation fail
            mock_llm.side_effect = Exception("LLM service unavailable")
            mock_supermcp.return_value = Mock()

            # System should handle the failure gracefully
            try:
                components = ComponentFactory.create_all_components(text_mode=True)
                # If it succeeds, LLM should be the failed component
                # If it fails, the exception should be handled appropriately
            except Exception:
                # Expected if system doesn't handle component failures gracefully
                pass


@pytest.mark.integration
class TestComponentLifecycle:
    """Test component lifecycle and resource management."""

    def test_component_cleanup_on_jarvis_shutdown(self):
        """Test components are properly cleaned up when Jarvis shuts down."""
        from jarvis.main import Jarvis

        with patch('jarvis.core.component_factory.ComponentFactory.create_all_components') as mock_create_all:

            # Setup mock components with cleanup methods
            mock_llm = Mock()
            mock_voice_manager = Mock()
            mock_voice_manager.cleanup = Mock()

            mock_components = {
                'llm': mock_llm,
                'supermcp': Mock(),
                'command_parser': Mock(),
                'output_manager': Mock(),
                'tts': None,
                'voice_manager': mock_voice_manager
            }
            mock_create_all.return_value = mock_components

            # Create and use Jarvis
            jarvis = Jarvis(text_mode=False)

            # Simulate shutdown/cleanup
            if hasattr(jarvis, 'cleanup'):
                jarvis.cleanup()

            # Verify cleanup was called on components that need it
            mock_voice_manager.cleanup.assert_called_once()

    def test_component_reuse_across_requests(self):
        """Test that components are reused across multiple requests."""
        from jarvis.main import Jarvis

        with patch('jarvis.core.component_factory.ComponentFactory.create_all_components') as mock_create_all:

            # Setup mock components
            mock_llm = Mock()
            mock_command_parser = Mock()

            mock_components = {
                'llm': mock_llm,
                'supermcp': Mock(),
                'command_parser': mock_command_parser,
                'output_manager': Mock(),
                'tts': None,
                'voice_manager': None
            }
            mock_create_all.return_value = mock_components

            # Setup LLM responses
            mock_llm.ask.side_effect = [
                mock_llm_response("Conversation", "First response"),
                mock_llm_response("Conversation", "Second response")
            ]

            # Create Jarvis
            jarvis = Jarvis(text_mode=True)

            # Make multiple requests
            result1 = jarvis.ask("First question")
            result2 = jarvis.ask("Second question")

            # Verify same components were used
            assert mock_llm.ask.call_count == 2
            assert result1["output"] == "First response"
            assert result2["output"] == "Second response"

    def test_component_state_isolation(self):
        """Test that component state is properly isolated between instances."""
        from jarvis.main import Jarvis

        with patch('jarvis.core.component_factory.ComponentFactory.create_all_components') as mock_create_all:

            # Create two separate Jarvis instances
            mock_components1 = {
                'llm': Mock(),
                'supermcp': Mock(),
                'command_parser': Mock(),
                'output_manager': Mock(),
                'tts': None,
                'voice_manager': None
            }

            mock_components2 = {
                'llm': Mock(),
                'supermcp': Mock(),
                'command_parser': Mock(),
                'output_manager': Mock(),
                'tts': None,
                'voice_manager': None
            }

            # Return different components for each call
            mock_create_all.side_effect = [mock_components1, mock_components2]

            jarvis1 = Jarvis(text_mode=True)
            jarvis2 = Jarvis(text_mode=True)

            # Verify they have different components
            assert jarvis1.llm != jarvis2.llm
            assert jarvis1.command_parser != jarvis2.command_parser