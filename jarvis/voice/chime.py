"""Wake-word earcon (Project-JARVIS#139).

A short, pre-rendered sound played the instant a wake word fires -- not
synthesized via TTS, so it plays instantly with no model warm-up and no
dependency on any GUI/TUI being attached. Both validation and playback are
best-effort and never raise: a bad path or missing audio hardware should
never crash the wake-word thread, just skip the chime.
"""

import os
import wave
from typing import Optional

from ..core.logger import get_logger

logger = get_logger(__name__)


def validate_chime_path(path: str) -> Optional[str]:
    """Return None if `path` is a readable WAV file, otherwise an error reason."""
    if not path:
        return "no path configured"
    if not os.path.isfile(path):
        return f"not a file: {path}"
    if not os.access(path, os.R_OK):
        return f"not readable: {path}"
    try:
        with wave.open(path, "rb"):
            pass
    except (wave.Error, EOFError, OSError) as e:
        return f"not a valid WAV file: {e}"
    return None


def play_chime(path: str) -> None:
    """Best-effort playback. Logs and returns on any failure; never raises."""
    error = validate_chime_path(path)
    if error:
        logger.warning(f"Wake chime not played ({error}), skipping")
        return

    try:
        import sounddevice as sd
    except ImportError:
        logger.debug("sounddevice not installed, skipping wake chime")
        return

    try:
        with wave.open(path, "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            frame_rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())

        dtype = {1: "int8", 2: "int16", 4: "int32"}.get(sample_width)
        if dtype is None:
            logger.warning(
                f"Wake chime has unsupported sample width {sample_width}, skipping"
            )
            return

        with sd.RawOutputStream(
            samplerate=frame_rate,
            channels=channels,
            dtype=dtype,
            blocksize=0,
        ) as stream:
            stream.write(frames)
    except Exception as e:
        logger.warning(f"Failed to play wake chime: {e}")
