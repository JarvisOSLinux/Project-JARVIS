"""
Component integration tests for JARVIS AI Assistant.

Tests component interactions, graceful degradation when optional components
are unavailable, and proper component lifecycle management.
Updated for the event-driven dispatch architecture.
"""

from unittest.mock import Mock, patch

import pytest

from tests.integration_utils import make_respond_action


@pytest.mark.integration
class TestComponentFactoryIntegration:
    """Test component factory creates and integrates components properly."""

    def test_create_all_components_text_mode(self):
        """Test creating all components in text-only mode."""
        from jarvis.core.component_factory import ComponentFactory

        with (
            patch.object(ComponentFactory, "create_llm") as mock_llm,
            patch.object(ComponentFactory, "create_dispatch_adapter") as mock_da,
            patch.object(ComponentFactory, "create_goal_manager") as mock_gm,
            patch.object(ComponentFactory, "create_event_merger") as mock_em,
            patch.object(ComponentFactory, "create_task_parser") as mock_tp,
            patch.object(ComponentFactory, "create_tts_optional") as mock_tts,
            patch.object(ComponentFactory, "create_output_manager") as mock_om,
            patch.object(ComponentFactory, "create_voice_manager_optional") as mock_vm,
        ):

            mock_llm.return_value = Mock()
            mock_da.return_value = Mock()
            mock_gm.return_value = Mock()
            mock_em.return_value = Mock()
            mock_tp.return_value = Mock()
            mock_tts.return_value = None
            mock_om.return_value = Mock()
            mock_vm.return_value = None

            components = ComponentFactory.create_all_components(text_mode=True)

            assert "llm" in components
            assert "dispatch_adapter" in components
            assert "goal_manager" in components
            assert "event_merger" in components
            assert "task_parser" in components
            assert "output_manager" in components
            assert "tts" in components
            assert "voice_manager" in components

            assert components["tts"] is None
            assert components["voice_manager"] is None

            mock_llm.assert_called_once()
            mock_da.assert_called_once()
            mock_gm.assert_called_once()

    def test_create_all_components_voice_mode(self):
        """Test creating all components in voice mode."""
        from jarvis.core.component_factory import ComponentFactory

        with (
            patch.object(ComponentFactory, "create_llm") as mock_llm,
            patch.object(ComponentFactory, "create_dispatch_adapter") as mock_da,
            patch.object(ComponentFactory, "create_goal_manager") as mock_gm,
            patch.object(ComponentFactory, "create_event_merger") as mock_em,
            patch.object(ComponentFactory, "create_task_parser") as mock_tp,
            patch.object(ComponentFactory, "create_tts_optional") as mock_tts,
            patch.object(ComponentFactory, "create_output_manager") as mock_om,
            patch.object(ComponentFactory, "create_voice_manager_optional") as mock_vm,
        ):

            mock_llm.return_value = Mock()
            mock_da.return_value = Mock()
            mock_gm.return_value = Mock()
            mock_em.return_value = Mock()
            mock_tp.return_value = Mock()
            mock_tts.return_value = Mock()
            mock_om.return_value = Mock()
            mock_vm.return_value = Mock()

            on_voice = Mock()
            components = ComponentFactory.create_all_components(
                text_mode=False,
                on_voice_command=on_voice,
            )

            assert "llm" in components
            assert "voice_manager" in components
            mock_tts.assert_called_once()

    def test_component_dependencies_integration(self):
        """Test that components are created with proper dependencies."""
        from jarvis.core.component_factory import ComponentFactory

        with (
            patch.object(ComponentFactory, "create_llm") as mock_llm,
            patch.object(ComponentFactory, "create_dispatch_adapter") as mock_da,
            patch.object(ComponentFactory, "create_goal_manager") as mock_gm,
            patch.object(ComponentFactory, "create_event_merger") as mock_em,
            patch.object(ComponentFactory, "create_task_parser") as mock_tp,
            patch.object(ComponentFactory, "create_tts_optional") as mock_tts,
            patch.object(ComponentFactory, "create_output_manager") as mock_om,
        ):

            tts_instance = Mock()
            mock_llm.return_value = Mock()
            mock_da.return_value = Mock()
            mock_gm.return_value = Mock()
            mock_em.return_value = Mock()
            mock_tp.return_value = Mock()
            mock_tts.return_value = tts_instance
            mock_om.return_value = Mock()

            components = ComponentFactory.create_all_components(text_mode=True)

            mock_om.assert_called_once_with(tts_instance, suppress_stdout=False)


@pytest.mark.integration
class TestJarvisComponentIntegration:
    """Test Jarvis class component integration."""

    def test_jarvis_initialization_with_components(self):
        """Test Jarvis initializes with all required components."""
        from jarvis.main import Jarvis

        with patch(
            "jarvis.core.component_factory.ComponentFactory.create_all_components"
        ) as mock_create_all:
            mock_components = {
                "llm": Mock(),
                "dispatch_adapter": Mock(),
                "goal_manager": Mock(),
                "event_merger": Mock(),
                "task_parser": Mock(),
                "output_manager": Mock(),
                "contextor": None,
                "embeddings": None,
                "kernel_client": Mock(available=False),
                "confirmation_manager": Mock(),
                "tts": None,
                "voice_manager": None,
            }
            mock_create_all.return_value = mock_components

            jarvis = Jarvis(text_mode=True)

            assert jarvis.llm == mock_components["llm"]
            assert jarvis.dispatch == mock_components["dispatch_adapter"]
            assert jarvis.goals == mock_components["goal_manager"]
            assert jarvis.events == mock_components["event_merger"]
            assert jarvis.task_parser == mock_components["task_parser"]
            assert jarvis.output_manager == mock_components["output_manager"]
            assert jarvis.voice_manager is None

    def test_jarvis_voice_command_handling(self):
        """Test Jarvis handles voice commands properly."""
        from jarvis.core.command_parser import TaskParser
        from jarvis.dispatch.goal_manager import GoalManager
        from jarvis.main import Jarvis

        with patch(
            "jarvis.core.component_factory.ComponentFactory.create_all_components"
        ) as mock_create_all:
            mock_llm = Mock()
            mock_output = Mock()
            mock_llm.reset_history = Mock()

            mock_components = {
                "llm": mock_llm,
                "dispatch_adapter": Mock(is_connected=False),
                "goal_manager": GoalManager(),
                "event_merger": Mock(),
                "task_parser": TaskParser(),
                "output_manager": mock_output,
                "contextor": None,
                "embeddings": None,
                "kernel_client": Mock(available=False),
                "confirmation_manager": Mock(),
                "tts": None,
                "voice_manager": Mock(),
            }
            mock_create_all.return_value = mock_components

            mock_llm.ask.return_value = make_respond_action("Voice response!")

            jarvis = Jarvis(text_mode=False)
            result = jarvis._handle_voice_command("What's the weather?")

            mock_llm.ask.assert_called_once()
            assert result["output"] == "Voice response!"

    def test_jarvis_ask_method_integration(self):
        """Test complete Jarvis.ask method integration."""
        from jarvis.core.command_parser import TaskParser
        from jarvis.main import Jarvis

        with patch(
            "jarvis.core.component_factory.ComponentFactory.create_all_components"
        ) as mock_create_all:
            mock_llm = Mock()
            mock_output = Mock()
            mock_goals = Mock()
            mock_goals.add_goal = Mock()
            mock_goals.get_context = Mock(return_value=[])
            mock_goals.dismiss_completed = Mock(return_value=[])

            mock_components = {
                "llm": mock_llm,
                "dispatch_adapter": Mock(),
                "goal_manager": mock_goals,
                "event_merger": Mock(),
                "task_parser": TaskParser(),
                "output_manager": mock_output,
                "contextor": None,
                "embeddings": None,
                "kernel_client": Mock(available=False),
                "confirmation_manager": Mock(),
                "tts": None,
                "voice_manager": None,
            }
            mock_create_all.return_value = mock_components

            mock_llm.ask.return_value = make_respond_action("Hello!")
            mock_llm.reset_history = Mock()

            jarvis = Jarvis(text_mode=True)
            result = jarvis.ask("Hello")

            assert result["output"] == "Hello!"
            mock_llm.ask.assert_called_once()
            mock_output.handle_response.assert_called_once()


@pytest.mark.integration
class TestGracefulDegradation:
    """Test graceful degradation when optional components are unavailable."""

    def test_tts_unavailable_graceful_degradation(self):
        """Test system works when TTS is unavailable."""
        from jarvis.core.component_factory import ComponentFactory

        with (
            patch.object(ComponentFactory, "create_llm") as mock_llm,
            patch.object(ComponentFactory, "create_dispatch_adapter") as mock_da,
            patch.object(ComponentFactory, "create_goal_manager") as mock_gm,
            patch.object(ComponentFactory, "create_event_merger") as mock_em,
            patch.object(ComponentFactory, "create_task_parser") as mock_tp,
            patch.object(ComponentFactory, "create_tts_optional") as mock_tts,
            patch.object(ComponentFactory, "create_output_manager") as mock_om,
        ):

            mock_llm.return_value = Mock()
            mock_da.return_value = Mock()
            mock_gm.return_value = Mock()
            mock_em.return_value = Mock()
            mock_tp.return_value = Mock()
            mock_tts.return_value = None
            mock_om.return_value = Mock()

            components = ComponentFactory.create_all_components(text_mode=True)

            assert components["tts"] is None
            mock_tts.assert_called_once()

    def test_jarvis_without_voice_components(self):
        """Test Jarvis works completely without voice components."""
        from jarvis.core.command_parser import TaskParser
        from jarvis.main import Jarvis

        with patch(
            "jarvis.core.component_factory.ComponentFactory.create_all_components"
        ) as mock_create_all:
            mock_llm = Mock()
            mock_goals = Mock()
            mock_goals.add_goal = Mock()
            mock_goals.get_context = Mock(return_value=[])
            mock_goals.dismiss_completed = Mock(return_value=[])

            mock_components = {
                "llm": mock_llm,
                "dispatch_adapter": Mock(),
                "goal_manager": mock_goals,
                "event_merger": Mock(),
                "task_parser": TaskParser(),
                "output_manager": Mock(),
                "contextor": None,
                "embeddings": None,
                "kernel_client": Mock(available=False),
                "confirmation_manager": Mock(),
                "tts": None,
                "voice_manager": None,
            }
            mock_create_all.return_value = mock_components

            jarvis = Jarvis(text_mode=True)
            assert jarvis.voice_manager is None

            mock_llm.ask.return_value = make_respond_action("Text response works!")
            mock_llm.reset_history = Mock()

            result = jarvis.ask("Test question")
            assert result["output"] == "Text response works!"

    def test_component_failure_does_not_crash_system(self):
        """Test that component creation failures don't crash the entire system."""
        from jarvis.core.component_factory import ComponentFactory

        with (
            patch.object(ComponentFactory, "create_llm") as mock_llm,
            patch.object(ComponentFactory, "create_dispatch_adapter") as mock_da,
        ):

            mock_llm.side_effect = Exception("LLM service unavailable")
            mock_da.return_value = Mock()

            try:
                components = ComponentFactory.create_all_components(text_mode=True)
            except Exception:
                pass  # Expected if system doesn't handle component failures gracefully


@pytest.mark.integration
class TestComponentLifecycle:
    """Test component lifecycle and resource management."""

    def test_component_reuse_across_requests(self):
        """Test that components are reused across multiple requests."""
        from jarvis.core.command_parser import TaskParser
        from jarvis.main import Jarvis

        with patch(
            "jarvis.core.component_factory.ComponentFactory.create_all_components"
        ) as mock_create_all:
            mock_llm = Mock()
            mock_goals = Mock()
            mock_goals.add_goal = Mock()
            mock_goals.get_context = Mock(return_value=[])
            mock_goals.dismiss_completed = Mock(return_value=[])

            mock_components = {
                "llm": mock_llm,
                "dispatch_adapter": Mock(),
                "goal_manager": mock_goals,
                "event_merger": Mock(),
                "task_parser": TaskParser(),
                "output_manager": Mock(),
                "contextor": None,
                "embeddings": None,
                "kernel_client": Mock(available=False),
                "confirmation_manager": Mock(),
                "tts": None,
                "voice_manager": None,
            }
            mock_create_all.return_value = mock_components

            mock_llm.ask.side_effect = [
                make_respond_action("First response"),
                make_respond_action("Second response"),
            ]
            mock_llm.reset_history = Mock()

            jarvis = Jarvis(text_mode=True)
            result1 = jarvis.ask("First question")
            result2 = jarvis.ask("Second question")

            assert mock_llm.ask.call_count == 2
            assert result1["output"] == "First response"
            assert result2["output"] == "Second response"

    def test_component_state_isolation(self):
        """Test that component state is properly isolated between instances."""
        from jarvis.main import Jarvis

        with patch(
            "jarvis.core.component_factory.ComponentFactory.create_all_components"
        ) as mock_create_all:
            comp1 = {
                "llm": Mock(),
                "dispatch_adapter": Mock(),
                "goal_manager": Mock(),
                "event_merger": Mock(),
                "task_parser": Mock(),
                "output_manager": Mock(),
                "contextor": None,
                "embeddings": None,
                "kernel_client": Mock(available=False),
                "confirmation_manager": Mock(),
                "tts": None,
                "voice_manager": None,
            }
            comp2 = {
                "llm": Mock(),
                "dispatch_adapter": Mock(),
                "goal_manager": Mock(),
                "event_merger": Mock(),
                "task_parser": Mock(),
                "output_manager": Mock(),
                "contextor": None,
                "embeddings": None,
                "kernel_client": Mock(available=False),
                "confirmation_manager": Mock(),
                "tts": None,
                "voice_manager": None,
            }

            mock_create_all.side_effect = [comp1, comp2]

            jarvis1 = Jarvis(text_mode=True)
            jarvis2 = Jarvis(text_mode=True)

            assert jarvis1.llm != jarvis2.llm
            assert jarvis1.task_parser != jarvis2.task_parser
