"""
Error handling and recovery integration tests for JARVIS AI Assistant.

Tests error scenarios, graceful handling of LLM failures, and
robustness of the synchronous ask() interface.
"""

from unittest.mock import Mock, patch

import pytest

from jarvis.core.command_parser import TaskParser
from jarvis.dispatch.goal_manager import GoalManager
from tests.integration_utils import make_respond_action


def _make_jarvis(llm_responses=None, llm_side_effect=None):
    """Helper to create a Jarvis instance with predefined LLM behaviour."""
    from jarvis.main import Jarvis

    mock_llm = Mock()
    if llm_side_effect:
        mock_llm.ask = Mock(side_effect=llm_side_effect)
    elif llm_responses:
        if isinstance(llm_responses, list):
            mock_llm.ask = Mock(side_effect=llm_responses)
        else:
            mock_llm.ask = Mock(return_value=llm_responses)
    else:
        mock_llm.ask = Mock(return_value=make_respond_action("Default"))
    mock_llm.reset_history = Mock()

    components = {
        "llm": mock_llm,
        "dispatch_adapter": Mock(is_connected=False),
        "goal_manager": GoalManager(),
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

    with patch(
        "jarvis.core.component_factory.ComponentFactory.create_all_components"
    ) as m:
        m.return_value = components
        jarvis = Jarvis(text_mode=True)

    return jarvis


@pytest.mark.integration
class TestLLMErrorHandling:
    """Test LLM error handling and recovery."""

    def test_llm_service_unavailable(self):
        """Test handling when LLM service is completely unavailable."""
        jarvis = _make_jarvis(
            llm_side_effect=ConnectionError("LLM service unavailable")
        )

        # Jarvis.ask() does not catch provider exceptions; they propagate
        with pytest.raises(ConnectionError):
            jarvis.ask("Test question")

    def test_llm_timeout_handling(self):
        """Test handling of LLM request timeouts."""
        import asyncio

        jarvis = _make_jarvis(llm_side_effect=asyncio.TimeoutError("Request timed out"))

        with pytest.raises(asyncio.TimeoutError):
            jarvis.ask("Test question")

    def test_llm_returns_unknown_action(self):
        """Test handling when LLM returns an unknown action type."""
        jarvis = _make_jarvis(llm_responses={"action": "fly_to_moon"})

        result = jarvis.ask("Test question")

        assert isinstance(result, dict)
        assert (
            "trouble" in result["output"].lower()
            or "try again" in result["output"].lower()
        )

    def test_llm_returns_empty_dict(self):
        """Test handling of empty LLM response dict."""
        jarvis = _make_jarvis(llm_responses={})

        result = jarvis.ask("Test question")
        assert isinstance(result, dict)
        assert "output" in result

    def test_llm_provider_exception(self):
        """Test handling of generic LLM provider exceptions."""
        jarvis = _make_jarvis(llm_side_effect=Exception("Provider crashed"))

        with pytest.raises(Exception, match="Provider crashed"):
            jarvis.ask("Test question")


@pytest.mark.integration
class TestTaskParserErrorHandling:
    """Test TaskParser error handling for various malformed inputs."""

    def test_dispatch_without_tasks_key(self):
        parser = TaskParser()
        result = parser.parse({"action": "dispatch"})
        assert "error" in result

    def test_kill_without_pids(self):
        parser = TaskParser()
        result = parser.parse({"action": "kill"})
        assert "error" in result

    def test_defer_without_goal_id(self):
        parser = TaskParser()
        result = parser.parse({"action": "defer", "duration": 60})
        assert "error" in result

    def test_defer_with_zero_duration(self):
        parser = TaskParser()
        result = parser.parse({"action": "defer", "goal_id": "g1", "duration": 0})
        assert "error" in result

    def test_dispatch_with_non_list_tasks(self):
        parser = TaskParser()
        result = parser.parse({"action": "dispatch", "tasks": "not a list"})
        assert "error" in result

    def test_kill_with_non_list_pids(self):
        parser = TaskParser()
        result = parser.parse({"action": "kill", "pids": "not a list"})
        assert "error" in result


@pytest.mark.integration
class TestMultipleRequestIsolation:
    """Test that error state does not leak between requests."""

    def test_error_then_success(self):
        """Test that a failed request doesn't pollute the next one."""
        jarvis = _make_jarvis(
            llm_responses=[
                {"action": "nonexistent"},
                make_respond_action("Second request worked!"),
            ]
        )

        r1 = jarvis.ask("First failing request")
        r2 = jarvis.ask("Second normal request")

        assert isinstance(r1, dict)
        assert isinstance(r2, dict)
        assert r2["output"] == "Second request worked!"

    def test_exception_then_success(self):
        """Test that an exception doesn't prevent the next request."""
        mock_llm = Mock()
        mock_llm.ask = Mock(
            side_effect=[
                RuntimeError("Boom"),
                make_respond_action("Recovery!"),
            ]
        )
        mock_llm.reset_history = Mock()

        from jarvis.main import Jarvis

        components = {
            "llm": mock_llm,
            "dispatch_adapter": Mock(is_connected=False),
            "goal_manager": GoalManager(),
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

        with patch(
            "jarvis.core.component_factory.ComponentFactory.create_all_components"
        ) as m:
            m.return_value = components
            jarvis = Jarvis(text_mode=True)

        # First request raises
        with pytest.raises(RuntimeError):
            jarvis.ask("Boom")

        # Second request should work
        r2 = jarvis.ask("Recover")
        assert r2["output"] == "Recovery!"


@pytest.mark.integration
class TestDispatchNotConnectedHandling:
    """Test behaviour when dispatch is not connected."""

    def test_dispatch_action_without_connection(self):
        """Test dispatch action when dispatch adapter is not connected."""
        jarvis = _make_jarvis(
            llm_responses={
                "action": "dispatch",
                "tasks": [{"server": "X", "tool": "y", "params": {}}],
            }
        )

        result = jarvis.ask("Run something")

        # The ask() method catches dispatch actions and returns action label
        assert isinstance(result, dict)
        assert "output" in result

    def test_kill_action_without_connection(self):
        """Test kill action when dispatch adapter is not connected."""
        jarvis = _make_jarvis(llm_responses={"action": "kill", "pids": [1]})

        result = jarvis.ask("Kill task 1")
        assert isinstance(result, dict)
        assert "output" in result


@pytest.mark.integration
class TestGoalManagerErrorResilience:
    """Test GoalManager handles edge cases properly."""

    def test_complete_nonexistent_goal(self):
        gm = GoalManager()
        gm.complete_goal("nonexistent")  # Should not crash

    def test_fail_nonexistent_goal(self):
        gm = GoalManager()
        gm.fail_goal("nonexistent")  # Should not crash

    def test_defer_nonexistent_goal(self):
        gm = GoalManager()
        gm.defer_goal("nonexistent", 99)  # Should not crash

    def test_link_tasks_nonexistent_goal(self):
        gm = GoalManager()
        gm.link_tasks("nonexistent", [1, 2])  # Should not crash

    def test_reactivate_nonexistent_goal(self):
        gm = GoalManager()
        gm.reactivate_goal("nonexistent")  # Should not crash

    def test_dismiss_completed_empty(self):
        gm = GoalManager()
        result = gm.dismiss_completed()
        assert result == []
