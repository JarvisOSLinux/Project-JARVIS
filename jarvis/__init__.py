"""
Project JARVIS - AI-Native Voice Assistant with Concurrent Task Dispatch

Copyright (C) 2025 YakupAtahanov
License: GPL-3.0
"""

__version__ = "1.0.0"
__author__ = "YakupAtahanov"
__email__ = "your.email@example.com"
__license__ = "GPL-3.0"
__description__ = "AI-Native Voice Assistant with Concurrent Task Dispatch"

from .config import Config
from .core.logger import JarvisLogger

# Attempt early setup — may be a no-op if core module imports have already
# fired get_logger() and locked _initialized=True with defaults.
JarvisLogger.setup(
    log_level=Config.LOG_LEVEL,
    log_file=Config.LOG_FILE if Config.LOG_FILE else None,
    enable_colors=Config.LOG_COLORS,
)

# Retroactively wire the file handler onto every logger that was created
# before setup() ran (i.e. during jarvis/core/__init__.py imports above).
# This is always safe to call — it's a no-op when file_handler is already set.
if Config.LOG_FILE and JarvisLogger._file_handler is None:
    JarvisLogger.configure_file_logging(Config.LOG_FILE)
    JarvisLogger.set_level(Config.LOG_LEVEL)

from .main import Jarvis

__all__ = ["Jarvis"]
