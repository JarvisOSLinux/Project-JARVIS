"""
Project JARVIS - AI-Native Voice Assistant with Concurrent Task Dispatch

JARVIS is an AI-powered voice assistant that combines natural language
processing with concurrent tool execution through the dispatch system
and MCP (Model Context Protocol) servers.

Copyright (C) 2025 YakupAtahanov

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

__version__ = "1.0.0"
__author__ = "YakupAtahanov"
__email__ = "your.email@example.com"
__license__ = "GPL-3.0"
__description__ = "AI-Native Voice Assistant with Concurrent Task Dispatch"

# Initialize logging when package is imported
from .config import Config
from .core.logger import JarvisLogger

# Setup logging with configuration
JarvisLogger.setup(
    log_level=Config.LOG_LEVEL,
    log_file=Config.LOG_FILE if Config.LOG_FILE else None,
    enable_colors=Config.LOG_COLORS
)

from .main import Jarvis

__all__ = ["Jarvis"]
