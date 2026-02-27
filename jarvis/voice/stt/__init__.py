"""
Speech-to-text provider sub-package.

Use ``create_stt()`` to get the right provider by name.
"""

from ..base import STTProvider


def create_stt(provider: str = "vosk", **kwargs) -> STTProvider:
    """Create an STT provider instance.

    Args:
        provider: Provider name (currently ``"vosk"``).
        **kwargs: Passed through to the provider constructor
                  (model_path, sample_rate, etc.).

    Returns:
        An initialised STTProvider.

    Raises:
        ValueError: If the provider name is unknown.
    """
    if provider == "vosk":
        from .vosk_stt import VoskSTT
        return VoskSTT(**kwargs)
    raise ValueError(
        f"Unknown STT provider: '{provider}'. Available: vosk"
    )


__all__ = ["STTProvider", "create_stt"]
