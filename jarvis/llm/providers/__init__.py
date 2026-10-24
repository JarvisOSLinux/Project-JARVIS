"""
LLM provider sub-package.

Use ``create_provider()`` to get the right provider by name.
"""

from ..base import BaseLLMProvider


def create_provider(
    provider: str = "llamacpp",
    model: str = "",
    **kwargs,
) -> BaseLLMProvider:
    """Create an LLM provider instance.

    Args:
        provider: Provider name (``"llamacpp"``, ``"api"``, ``"lmstudio"``, or ``"ollama-cloud"``).
        model: Model name / identifier.
        **kwargs: Provider-specific options (base_url, api_key, etc.).

    Returns:
        An initialised BaseLLMProvider.

    Raises:
        ValueError: If the provider name is unknown or required config is missing.
    """
    if not model:
        raise ValueError("model must be specified")

    if provider == "llamacpp":
        from .llamacpp import LlamaCppProvider

        return LlamaCppProvider(model=model, **kwargs)

    if provider in ("api", "lmstudio", "ollama-cloud"):
        from .api import APIProvider

        return APIProvider(model=model, **kwargs)

    raise ValueError(
        f"Unknown LLM provider: '{provider}'. Available: llamacpp, api, lmstudio, ollama-cloud"
    )


__all__ = ["BaseLLMProvider", "create_provider"]
