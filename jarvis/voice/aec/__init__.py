"""
Acoustic echo canceller (AEC) provider sub-package.

Use ``create_echo_canceller()`` to get the right provider by name.
"""

from ..base import EchoCanceller


def create_echo_canceller(provider: str = "webrtc", **kwargs) -> EchoCanceller:
    """Create an AEC provider instance.

    Args:
        provider: Provider name (currently ``"webrtc"``).
        **kwargs: Passed through to the provider constructor
                  (sample_rate, reference_sample_rate, etc.).

    Returns:
        An initialised EchoCanceller.

    Raises:
        ValueError: If the provider name is unknown.
    """
    if provider == "webrtc":
        from .webrtc_aec import WebRtcAEC

        return WebRtcAEC(**kwargs)
    raise ValueError(f"Unknown AEC provider: '{provider}'. Available: webrtc")


__all__ = ["EchoCanceller", "create_echo_canceller"]
