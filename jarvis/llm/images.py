"""Image payload helpers shared by the LLM providers.

Vision requests reuse the ordinary chat endpoint — the only difference is
image content attached to a message. These helpers turn an on-disk image
path into the base64 payloads each provider's wire format expects.
"""

from __future__ import annotations

import base64
import os

_MAGIC_FORMATS = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)

_EXTENSION_FORMATS = {
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".webp": "webp",
    ".gif": "gif",
}


def detect_image_format(path: str) -> str:
    """Return the image format (png/jpeg/webp/gif), magic bytes first, extension as fallback."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        head = b""
    for magic, fmt in _MAGIC_FORMATS:
        if head.startswith(magic):
            return fmt
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    ext = os.path.splitext(path)[1].lower()
    return _EXTENSION_FORMATS.get(ext, "png")


def encode_image_base64(path: str) -> str:
    """Return the file's contents base64-encoded as ASCII text."""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")
