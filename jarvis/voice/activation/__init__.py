"""
Activation (wake-word) provider sub-package.

Use ``create_activation()`` to get the right provider by name.
"""

from ..base import ActivationProvider


def create_activation(provider: str = "vosk", **kwargs) -> ActivationProvider:
    """Create an activation provider instance.

    Args:
        provider: Provider name (currently ``"vosk"``).
        **kwargs: Passed through to the provider constructor
                  (wake_words, model_path, sensitivity, etc.).

    Returns:
        An initialised ActivationProvider.

    Raises:
        ValueError: If the provider name is unknown.
    """
    if provider == "vosk":
        from .vosk_activation import VoskActivation
        return VoskActivation(**kwargs)
    raise ValueError(
        f"Unknown activation provider: '{provider}'. Available: vosk"
    )


__all__ = ["ActivationProvider", "create_activation"]
