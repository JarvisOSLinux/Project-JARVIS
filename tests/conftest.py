"""
Test configuration and fixtures for JARVIS
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest


@pytest.fixture
def temp_env_file():
    """Create a temporary .env file for testing"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write("""DISPATCH_TIMEOUT=30
OUTPUT_MODE=text
""")
        f.flush()
        yield f.name
    os.unlink(f.name)


@pytest.fixture
def mock_config():
    """Mock configuration for testing"""
    return {
        "DISPATCH_TIMEOUT": 60,
        "OUTPUT_MODE": "text",
        "LOG_LEVEL": "INFO",
    }


@pytest.fixture
def mock_system_info():
    """Mock system information"""
    return {
        "system": "linux",
        "release": "5.4.0",
        "version": "#1 SMP Debian",
        "machine": "x86_64",
        "shell": ["bash", "-lc"],
    }


@pytest.fixture
def mock_llm():
    """Create a mock LLM that returns dispatch-format responses."""
    llm = Mock()
    llm.ask = Mock(
        return_value={"action": "respond", "output": "Hello! How can I help you?"}
    )
    llm.reset_history = Mock()
    llm.provider = Mock()
    llm.provider.model = "test-model"
    return llm


@pytest.fixture
def mock_dispatch_adapter():
    """Create a mock DispatchAdapter."""
    adapter = Mock()
    adapter.is_connected = False
    adapter.connect = AsyncMock()
    adapter.disconnect = AsyncMock()
    adapter.send_tasks = AsyncMock(return_value={"pids": [1, 2]})
    adapter.kill_tasks = AsyncMock(return_value={"killed": [1, 2]})
    adapter.set_timer = AsyncMock(return_value={"pid": 99})
    adapter.get_signal_window = AsyncMock(return_value=[])
    return adapter


@pytest.fixture
def mock_goal_manager():
    """Create a real GoalManager (it has no external dependencies)."""
    from jarvis.dispatch.goal_manager import GoalManager

    return GoalManager()


@pytest.fixture
def mock_event_merger():
    """Create a mock EventMerger."""
    merger = Mock()
    merger.start = Mock()
    merger.stop = AsyncMock()
    return merger


@pytest.fixture
def mock_output_manager():
    """Create a mock OutputManager."""
    om = Mock()
    om.handle_response = Mock()
    om.get_current_mode = Mock(return_value="text")
    om.is_voice_mode = Mock(return_value=False)
    om.has_tts = Mock(return_value=False)
    return om


@pytest.fixture
def mock_task_parser():
    """Create a real TaskParser (it's stateless, no external dependencies)."""
    from jarvis.core.command_parser import TaskParser

    return TaskParser()


@pytest.fixture
def temp_test_directory():
    """Create a temporary directory with test files"""
    from tests.integration_utils import create_temp_directory_with_files

    test_files = {
        "test.txt": "Hello World",
        "subdir/test.py": 'print("test")',
        "data.json": '{"key": "value"}',
    }

    temp_dir = create_temp_directory_with_files(test_files)
    yield temp_dir

    import shutil

    try:
        shutil.rmtree(temp_dir)
    except Exception:
        pass


@pytest.fixture
def jarvis_instance(
    mock_llm,
    mock_dispatch_adapter,
    mock_goal_manager,
    mock_event_merger,
    mock_task_parser,
    mock_output_manager,
):
    """Create a Jarvis instance with mocked dependencies for the dispatch architecture."""
    from jarvis.main import Jarvis

    with patch(
        "jarvis.core.component_factory.ComponentFactory.create_all_components"
    ) as mock_create_all:
        mock_create_all.return_value = {
            "llm": mock_llm,
            "dispatch_adapter": mock_dispatch_adapter,
            "goal_manager": mock_goal_manager,
            "event_merger": mock_event_merger,
            "task_parser": mock_task_parser,
            "output_manager": mock_output_manager,
            "contextor": None,
            "embeddings": None,
            "kernel_client": Mock(available=False),
            "confirmation_manager": Mock(),
            "tts": None,
            "voice_manager": None,
        }

        jarvis = Jarvis(text_mode=True)
        yield jarvis
