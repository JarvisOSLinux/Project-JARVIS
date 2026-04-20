"""
Audio device detection utility for JARVIS AI Assistant

This module provides functions to detect audio input/output device availability
with graceful handling of missing audio packages or devices.
"""

from typing import Dict, List, Optional, Tuple

from ..core.logger import get_logger

logger = get_logger(__name__)


class AudioUnavailableError(Exception):
    """Raised when audio functionality is requested but unavailable"""

    pass


def _try_import_sounddevice() -> Optional[object]:
    """Try to import sounddevice, return None if unavailable"""
    try:
        import sounddevice as sd

        return sd
    except ImportError:
        return None
    except Exception as e:
        logger.warning(f"Unexpected error importing sounddevice: {e}")
        return None


def check_audio_output_available() -> bool:
    """
    Check if audio output devices are available

    Returns:
        True if audio output is available, False otherwise
    """
    sd = _try_import_sounddevice()
    if sd is None:
        logger.debug("sounddevice package not available")
        return False

    try:
        devices = sd.query_devices()
        # Check for output devices (max_output_channels > 0)
        output_devices = [d for d in devices if d.get("max_output_channels", 0) > 0]

        if output_devices:
            logger.debug(f"Found {len(output_devices)} audio output device(s)")
            return True
        else:
            logger.debug("No audio output devices found")
            return False

    except Exception as e:
        logger.warning(f"Error checking audio output devices: {e}")
        return False


def check_audio_input_available() -> bool:
    """
    Check if audio input devices are available

    Returns:
        True if audio input is available, False otherwise
    """
    sd = _try_import_sounddevice()
    if sd is None:
        logger.debug("sounddevice package not available")
        return False

    try:
        devices = sd.query_devices()
        # Check for input devices (max_input_channels > 0)
        input_devices = [d for d in devices if d.get("max_input_channels", 0) > 0]

        if input_devices:
            logger.debug(f"Found {len(input_devices)} audio input device(s)")
            return True
        else:
            logger.debug("No audio input devices found")
            return False

    except Exception as e:
        logger.warning(f"Error checking audio input devices: {e}")
        return False


def list_audio_devices() -> Dict[str, List[Dict]]:
    """
    List all available audio devices

    Returns:
        Dictionary with 'input' and 'output' keys, each containing a list of device info dicts
    """
    sd = _try_import_sounddevice()
    if sd is None:
        logger.debug("sounddevice package not available for device listing")
        return {"input": [], "output": []}

    try:
        devices = sd.query_devices()
        input_devices = []
        output_devices = []

        for i, device in enumerate(devices):
            device_info = {
                "index": i,
                "name": device.get("name", "Unknown"),
                "channels_in": device.get("max_input_channels", 0),
                "channels_out": device.get("max_output_channels", 0),
                "sample_rate": device.get("default_samplerate", 0),
            }

            if device_info["channels_in"] > 0:
                input_devices.append(device_info)
            if device_info["channels_out"] > 0:
                output_devices.append(device_info)

        return {"input": input_devices, "output": output_devices}

    except Exception as e:
        logger.warning(f"Error listing audio devices: {e}")
        return {"input": [], "output": []}


def get_default_output_device() -> Optional[int]:
    """
    Get the default audio output device index

    Returns:
        Device index or None if unavailable
    """
    sd = _try_import_sounddevice()
    if sd is None:
        return None

    try:
        default_device = sd.default.device[1]  # [input, output]
        if default_device is not None:
            return default_device
        # Fallback: find first output device
        devices = sd.query_devices()
        for i, device in enumerate(devices):
            if device.get("max_output_channels", 0) > 0:
                return i
        return None
    except Exception as e:
        logger.warning(f"Error getting default output device: {e}")
        return None


def get_default_input_device() -> Optional[int]:
    """
    Get the default audio input device index

    Returns:
        Device index or None if unavailable
    """
    sd = _try_import_sounddevice()
    if sd is None:
        return None

    try:
        default_device = sd.default.device[0]  # [input, output]
        if default_device is not None:
            return default_device
        # Fallback: find first input device
        devices = sd.query_devices()
        for i, device in enumerate(devices):
            if device.get("max_input_channels", 0) > 0:
                return i
        return None
    except Exception as e:
        logger.warning(f"Error getting default input device: {e}")
        return None
