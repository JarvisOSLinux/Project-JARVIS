import pytest

from jarvis.config import Config


@pytest.mark.parametrize(
    "prompt",
    [Config.LLM_ROOT_PROMPT, Config.LLM_ROOT_PROMPT_NO_CONTEXTOR],
)
def test_prompt_tells_model_not_to_vary_command_after_unanswered_password(prompt):
    assert "was not granted" in prompt
    assert "did not complete" in prompt
    assert "do NOT retry" in prompt
    assert "added flags" in prompt
