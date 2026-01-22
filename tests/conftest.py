"""
Test configuration and fixtures for JARVIS
"""

import pytest
import os
import tempfile
from unittest.mock import Mock, patch
from pathlib import Path


@pytest.fixture
def temp_env_file():
    """Create a temporary .env file for testing"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
        f.write("""STT_MODEL=base
LLM_MODEL=test-model
TTS_MODEL_ONNX=test.onnx
TTS_MODEL_JSON=test.json
SUPERMCP_SERVER_PATH=SuperMCP/SuperMCP.py
SUPERMCP_TIMEOUT=30
""")
        f.flush()
        yield f.name
    os.unlink(f.name)


@pytest.fixture
def mock_config():
    """Mock configuration for testing"""
    return {
        'STT_MODEL': 'base',
        'LLM_MODEL': 'test-model',
        'TTS_MODEL_ONNX': 'test.onnx',
        'TTS_MODEL_JSON': 'test.json',
        'SUPERMCP_SERVER_PATH': 'SuperMCP/SuperMCP.py',
        'SUPERMCP_TIMEOUT': 30
    }


@pytest.fixture
def mock_system_info():
    """Mock system information"""
    return {
        'system': 'linux',
        'release': '5.4.0',
        'version': '#1 SMP Debian',
        'machine': 'x86_64',
        'shell': ['bash', '-lc']
    }


@pytest.fixture
def mock_llm_response():
    """Mock LLM response factory"""
    from tests.integration_utils import mock_llm_response
    return mock_llm_response


@pytest.fixture
def mock_llm_conversation():
    """Mock LLM conversation response factory"""
    from tests.integration_utils import mock_llm_conversation
    return mock_llm_conversation


@pytest.fixture
def mock_llm_supermcp_command():
    """Mock LLM SuperMCP command response factory"""
    from tests.integration_utils import mock_llm_supermcp_command
    return mock_llm_supermcp_command


@pytest.fixture
def mock_supermcp_client():
    """Mock SuperMCP client for testing"""
    from tests.integration_utils import create_mock_supermcp_client
    return create_mock_supermcp_client()


@pytest.fixture
def temp_mcp_config():
    """Create a temporary MCP configuration file"""
    from tests.integration_utils import create_test_mcp_config
    config_file = create_test_mcp_config()

    yield config_file

    # Cleanup
    try:
        os.unlink(config_file)
    except:
        pass


@pytest.fixture
def mock_approval_handler():
    """Mock approval handler for testing approval workflows"""
    from tests.integration_utils import MockApprovalHandler
    return MockApprovalHandler(approve_commands=True)


@pytest.fixture
def mock_approval_handler_deny():
    """Mock approval handler that denies commands"""
    from tests.integration_utils import MockApprovalHandler
    return MockApprovalHandler(approve_commands=False)


@pytest.fixture
def mock_llm_provider():
    """Mock LLM provider for testing"""
    from tests.integration_utils import create_mock_llm_provider
    return create_mock_llm_provider


@pytest.fixture
def temp_test_directory():
    """Create a temporary directory with test files"""
    from tests.integration_utils import create_temp_directory_with_files

    test_files = {
        'test.txt': 'Hello World',
        'subdir/test.py': 'print("test")',
        'data.json': '{"key": "value"}'
    }

    temp_dir = create_temp_directory_with_files(test_files)

    yield temp_dir

    # Cleanup
    import shutil
    try:
        shutil.rmtree(temp_dir)
    except:
        pass


@pytest.fixture
def test_jarvis_config():
    """Test configuration for JARVIS"""
    from tests.integration_utils import create_test_jarvis_config
    return create_test_jarvis_config()


@pytest.fixture
def jarvis_instance(mock_llm_provider, mock_supermcp_client, mock_approval_handler):
    """Create a Jarvis instance with mocked dependencies"""
    from unittest.mock import patch
    from jarvis.main import Jarvis

    # Create mock LLM responses
    llm_responses = [
        {"user_request": "Conversation", "output": "Hello! How can I help you?"}
    ]

    mock_provider = mock_llm_provider(llm_responses)

    with patch('jarvis.core.component_factory.ComponentFactory.create_llm') as mock_create_llm, \
         patch('jarvis.core.component_factory.ComponentFactory.create_supermcp') as mock_create_supermcp, \
         patch('jarvis.core.component_factory.ComponentFactory.create_tts_optional') as mock_create_tts, \
         patch('jarvis.core.component_factory.ComponentFactory.create_voice_manager_optional') as mock_create_vm:

        # Mock LLM
        mock_llm = Mock()
        mock_llm.ask = Mock(return_value=llm_responses[0])
        mock_create_llm.return_value = mock_llm

        # Mock SuperMCP
        mock_create_supermcp.return_value = Mock()

        # Mock optional components
        mock_create_tts.return_value = None
        mock_create_vm.return_value = None

        # Create Jarvis instance
        jarvis = Jarvis(text_mode=True)

        yield jarvis
