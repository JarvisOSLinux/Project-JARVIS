"""WebRTC-based acoustic echo canceller (Project-JARVIS#143)."""

import threading
from typing import Optional

from ...core.logger import get_logger
from ..audio import AudioUnavailableError
from ..base import EchoCanceller

logger = get_logger(__name__)


class WebRtcAEC(EchoCanceller):
    """Acoustic echo cancellation backed by ``aec-audio-processing``.

    That package vendors freedesktop.org's ``webrtc-audio-processing`` --
    the same AEC engine PipeWire's own ``module-echo-cancel`` uses -- via a
    SWIG binding, so this wraps a mature, field-tested implementation
    rather than a bespoke DSP filter.

    The underlying ``AudioProcessor`` always frames both the near-end (mic)
    and far-end (reference) streams at 10ms using the *forward* stream's
    sample rate, regardless of what rate ``set_reverse_stream_format``
    declares (confirmed by reading the wrapper's C++ source -- its
    ``process_reverse_stream`` computes frame size from the forward config).
    Reference audio is therefore resampled to ``sample_rate`` before
    framing, rather than relying on the reverse-rate field to do it.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        reference_sample_rate: int = 22050,
        stream_delay_ms: int = 80,
    ):
        try:
            import numpy as np
            from aec_audio_processing import AudioProcessor
        except ImportError as e:
            raise AudioUnavailableError(
                "aec-audio-processing (and numpy) not installed. "
                "Install with: pip install project-jarvis[voice-aec]"
            ) from e

        self._np = np
        self.sample_rate = sample_rate
        self.reference_sample_rate = reference_sample_rate
        self.stream_delay_ms = stream_delay_ms
        self._frame_bytes = 0
        self._lock = threading.Lock()
        self._mic_buf = bytearray()
        self._ref_buf = bytearray()
        self._out_buf = bytearray()
        self._AudioProcessor = AudioProcessor
        self._build_processor()

    # -- EchoCanceller interface ------------------------------------------

    def process(self, mic_frame: bytes) -> bytes:
        if not mic_frame:
            return b""
        with self._lock:
            self._mic_buf.extend(mic_frame)
            self._drain_locked()
            out = bytes(self._out_buf)
            self._out_buf.clear()
            return out

    def feed_reference(self, reference_frame: bytes) -> None:
        if not reference_frame:
            return
        resampled = self._resample(
            reference_frame, self.reference_sample_rate, self.sample_rate
        )
        with self._lock:
            self._ref_buf.extend(resampled)
            self._drain_locked()

    def reset(self) -> None:
        with self._lock:
            self._mic_buf.clear()
            self._ref_buf.clear()
            self._out_buf.clear()
            self._build_processor()

    # -- internals ----------------------------------------------------------

    def _drain_locked(self) -> None:
        """Push buffered whole frames through the underlying processor.

        Must interleave forward/reverse calls roughly one-for-one --
        draining an entire callback's worth of frames from one side in an
        uninterrupted burst (e.g. 25 reverse calls back-to-back, then 25
        forward calls) measurably breaks the internal echo-path tracking
        (~30dB attenuation with 1:1 interleaving degrades to ~6dB with
        bursts, on identical synthetic-echo input -- see
        tests/test_echo_cancellation.py). Mic input producers (sounddevice
        callbacks) and TTS reference chunks arrive as arbitrarily-sized,
        independently-timed bursts, so this is what actually keeps calls
        alternating regardless of caller chunk size.
        """
        frame_bytes = self._frame_bytes
        while len(self._ref_buf) >= frame_bytes and len(self._mic_buf) >= frame_bytes:
            ref_frame = bytes(self._ref_buf[:frame_bytes])
            del self._ref_buf[:frame_bytes]
            self._ap.process_reverse_stream(ref_frame)

            mic_frame = bytes(self._mic_buf[:frame_bytes])
            del self._mic_buf[:frame_bytes]
            self._out_buf.extend(self._ap.process_stream(mic_frame))

        # No reference data waiting (e.g. TTS isn't playing) -- still push
        # mic audio through rather than holding it hostage to a reverse
        # stream that may never arrive.
        while len(self._mic_buf) >= frame_bytes:
            mic_frame = bytes(self._mic_buf[:frame_bytes])
            del self._mic_buf[:frame_bytes]
            self._out_buf.extend(self._ap.process_stream(mic_frame))

    def _build_processor(self) -> None:
        self._ap = self._AudioProcessor(
            enable_aec=True, enable_ns=False, enable_agc=False, enable_vad=False
        )
        self._ap.set_stream_format(sample_rate_in=self.sample_rate, channel_count_in=1)
        self._ap.set_reverse_stream_format(
            sample_rate_in=self.sample_rate, channel_count_in=1
        )
        self._ap.set_stream_delay(self.stream_delay_ms)
        self._frame_bytes = self._ap.get_frame_size() * 2  # int16 mono

    def _resample(self, pcm_bytes: bytes, from_rate: int, to_rate: Optional[int]):
        if from_rate == to_rate or not pcm_bytes:
            return pcm_bytes
        np = self._np
        data = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float64)
        if len(data) == 0:
            return b""
        duration = len(data) / from_rate
        n_out = max(1, int(round(duration * to_rate)))
        x_old = np.linspace(0, duration, num=len(data), endpoint=False)
        x_new = np.linspace(0, duration, num=n_out, endpoint=False)
        resampled = np.interp(x_new, x_old, data)
        return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()
