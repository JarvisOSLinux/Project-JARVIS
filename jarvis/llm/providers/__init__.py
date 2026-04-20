"""
LLM provider sub-package.

Use ``create_provider()`` to get the right provider by name.
"""

from ..base import BaseLLMProvider


def create_provider(
    provider: str = "ollama",
    model: str = "",
    **kwargs,
) -> BaseLLMProvider:
    """Create an LLM provider instance.

    Args:
        provider: Provider name (``"ollama"`` or ``"api"``).
        model: Model name / identifier.
        **kwargs: Provider-specific options (base_url, api_key, etc.).

    Returns:
        An initialised BaseLLMProvider.

    Raises:
        ValueError: If the provider name is unknown or required config is missing.
    """
    if not model:
        raise ValueError("model must be specified")

    if provider == "ollama":
        from .ollama import OllamaProvider

        return OllamaProvider(model=model, **kwargs)

    if provider == "api":
        from .api import APIProvider

        return APIProvider(model=model, **kwargs)

    raise ValueError(f"Unknown LLM provider: '{provider}'. Available: ollama, api")


__all__ = ["BaseLLMProvider", "create_provider"]
