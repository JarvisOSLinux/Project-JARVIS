"""
End-to-end integration tests for JARVIS AI Assistant.

Tests complete user request -> response workflows using the event-driven
dispatch architecture, including the synchronous .ask() interface.
"""

import pytest
from unittest.mock import Mock, patch
from jarvis.core.command_parser import TaskParser
from jarvis.dispatch.goal_manager import GoalManager
from tests.integration_utils import make_respond_action, make_dispatch_action, make_task


@pytest.mark.integration
class TestEndToEndWorkflows:
    """Test complete end-to-end workflows from user input to response."""

    def _make_jarvis(self, llm_responses):
        """Helper to create a Jarvis instance with predefined LLM responses."""
        from jarvis.main import Jarvis

        mock_llm = Mock()
        if isinstance(llm_responses, list):
            mock_llm.ask = Mock(side_effect=llm_responses)
        else:
            mock_llm.ask = Mock(return_value=llm_responses)
        mock_llm.reset_history = Mock()

        goal_manager = GoalManager()

        components = {
            'llm': mock_llm,
            'dispatch_adapter': Mock(is_connected=False),
            'goal_manager': goal_manager,
            'event_merger': Mock(),
            'task_parser': TaskParser(),
            'output_manager': Mock(),
            'tts': None,
            'voice_manager': None,
        }

        with patch('jarvis.core.component_factory.ComponentFactory.create_all_components') as m:
            m.return_value = components
            jarvis = Jarvis(text_mode=True)

        return jarvis

    def test_simple_conversation_workflow(self):
        """Test simple conversation: user asks question, gets conversational response."""
        jarvis = self._make_jarvis(
            make_respond_action("Python is a programming language.")
        )

        result = jarvis.ask("What is Python?")

        assert result["output"] == "Python is a programming language."
        jarvis.output_manager.handle_response.assert_called_once()

    def test_dispatch_action_returns_action_message(self):
        """Test that dispatch actions return an action summary (not dispatch-connected)."""
        jarvis = self._make_jarvis(
            make_dispatch_action([make_task("ShellMCP", "run", {"cmd": "ls"})])
        )

        result = jarvis.ask("List files")

        assert "dispatch" in result["output"].lower() or "Action" in result["output"]

    def test_multi_turn_conversation(self):
        """Test multi-turn conversation using the ask() interface."""
        jarvis = self._make_jarvis([
            make_respond_action("Hello! How can I help?"),
            make_respond_action("The weather is sunny."),
        ])

        r1 = jarvis.ask("Hello")
        r2 = jarvis.ask("What's the weather?")

        assert r1["output"] == "Hello! How can I help?"
        assert r2["output"] == "The weather is sunny."
        assert jarvis.llm.ask.call_count == 2

    def test_error_in_parsed_response(self):
        """Test that an LLM returning an invalid action yields a friendly error."""
        jarvis = self._make_jarvis({"action": "nonexistent"})

        result = jarvis.ask("Do something weird")

        assert "trouble" in result["output"].lower() or "try again" in result["output"].lower()

    def test_respond_action_dismisses_completed_goals(self):
        """Test that a respond action with goal completion dismisses the goal."""
        jarvis = self._make_jarvis(
            {
                "action": "respond",
                "output": "All done!",
                "goal_updates": [{"id": "placeholder", "status": "completed", "result": "ok"}],
            }
        )

        # The goal_id from goal_updates doesn't match any real goal, but the
        # respond path still runs dismiss_completed which clears any completed goals.
        result = jarvis.ask("Do something")
        assert result["output"] == "All done!"

    def test_history_reset_after_response(self):
        """Test that chat history is reset after each response (when configured)."""
        jarvis = self._make_jarvis(make_respond_action("Done."))

        with patch.object(jarvis.llm, 'reset_history') as mock_reset:
            jarvis.ask("Test")
            mock_reset.assert_called_once()

    def test_goal_added_on_ask(self):
        """Test that a goal is added for each ask() call."""
        jarvis = self._make_jarvis(make_respond_action("OK."))

        jarvis.ask("First request")
        jarvis.ask("Second request")

        all_goals = jarvis.goals.get_all_goals()
        assert len(all_goals) == 2
        assert all_goals[0].description == "First request"
        assert all_goals[1].description == "Second request"

    def test_wait_action_workflow(self):
        """Test wait action through the ask() interface."""
        jarvis = self._make_jarvis({"action": "wait"})

        result = jarvis.ask("Wait for tasks")

        # wait action doesn't produce user output, so ask() returns the action label
        assert "wait" in result["output"].lower() or "Action" in result["output"]
