"""
Text-to-speech provider sub-package.

Use ``create_tts()`` to get the right provider by name.
"""

from ..base import TTSProvider


def create_tts(provider: str = "piper", **kwargs) -> TTSProvider:
    """Create a TTS provider instance.

    Args:
        provider: Provider name (currently ``"piper"``).
        **kwargs: Passed through to the provider constructor
                  (model_path, config_path, etc.).

    Returns:
        An initialised TTSProvider.

    Raises:
        ValueError: If the provider name is unknown.
    """
    if provider == "piper":
        from .piper_tts import PiperTTS
        return PiperTTS(**kwargs)
    raise ValueError(
        f"Unknown TTS provider: '{provider}'. Available: piper"
    )


__all__ = ["TTSProvider", "create_tts"]
