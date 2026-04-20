"""
Centralized logging configuration for JARVIS AI Assistant

This module provides a unified logging interface with:
- Configurable log levels
- Console and file output
- Colored console output
- Structured log formatting
"""

import logging
import sys
from pathlib import Path
from typing import Optional
from datetime import datetime


class ColoredFormatter(logging.Formatter):
    """Custom formatter with color support for console output"""
    
    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
    }
    RESET = '\033[0m'
    BOLD = '\033[1m'
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record with colors"""
        # Add color to level name
        levelname = record.levelname
        if levelname in self.COLORS:
            record.levelname = f"{self.COLORS[levelname]}{self.BOLD}{levelname}{self.RESET}"
        
        # Format the message
        result = super().format(record)
        
        return result


def _is_stdio_stream(stream: object) -> bool:
    return stream in (sys.stdout, sys.stderr)


def _ensure_no_last_resort(logger: logging.Logger) -> None:
    """If a logger has propagate=False and no handlers, logging emits via lastResort (stderr).

    That corrupts Textual TUIs.  Attach a NullHandler whenever the logger would
    otherwise have no handlers.
    """
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())


class JarvisLogger:
    """
    Centralized logger for JARVIS
    
    Provides consistent logging across all modules with:
    - Console output with colors
    - Optional file output
    - Configurable log levels
    """
    
    _loggers = {}
    _initialized = False
    _log_level = logging.INFO
    _log_file: Optional[Path] = None
    _file_handler: Optional[logging.FileHandler] = None
    _console_enabled: bool = True
    
    @classmethod
    def setup(cls, log_level: str = "INFO", log_file: Optional[str] = None, 
              enable_colors: bool = True) -> None:
        """
        Initialize the logging system
        
        Args:
            log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            log_file: Optional path to log file
            enable_colors: Enable colored console output
        """
        if cls._initialized:
            return
        
        # Convert string level to logging constant
        numeric_level = getattr(logging, log_level.upper(), logging.INFO)
        cls._log_level = numeric_level
        
        # Setup file logging if requested
        if log_file:
            cls._log_file = Path(log_file)
            cls._log_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Create file handler with rotation
            cls._file_handler = logging.FileHandler(
                cls._log_file,
                mode='a',
                encoding='utf-8'
            )
            cls._file_handler.setLevel(logging.DEBUG)  # Always log everything to file
            
            # File format (without colors)
            file_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            cls._file_handler.setFormatter(file_formatter)
        
        cls._initialized = True
        cls._enable_colors = enable_colors
    
    @classmethod
    def get_logger(cls, name: str) -> logging.Logger:
        """
        Get a logger instance for a specific module
        
        Args:
            name: Name of the module (usually __name__)
            
        Returns:
            Configured logger instance
        """
        # Initialize with defaults if not already done
        if not cls._initialized:
            cls.setup()
        
        # Return cached logger if exists
        if name in cls._loggers:
            return cls._loggers[name]
        
        # Create new logger
        logger = logging.getLogger(name)
        logger.setLevel(cls._log_level)
        logger.handlers.clear()  # Remove any existing handlers
        
        if cls._console_enabled:
            # Console handler
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(cls._log_level)

            # Choose formatter based on color preference
            if cls._enable_colors and sys.stdout.isatty():
                console_formatter = ColoredFormatter(
                    '%(levelname)s - %(name)s - %(message)s'
                )
            else:
                console_formatter = logging.Formatter(
                    '%(levelname)s - %(name)s - %(message)s'
                )

            console_handler.setFormatter(console_formatter)
            logger.addHandler(console_handler)
        
        # Add file handler if configured
        if cls._file_handler:
            logger.addHandler(cls._file_handler)

        if not cls._console_enabled and not cls._file_handler:
            _ensure_no_last_resort(logger)
        
        # Prevent propagation to root logger
        logger.propagate = False
        
        # Cache the logger
        cls._loggers[name] = logger
        
        return logger
    
    @classmethod
    def set_level(cls, level: str) -> None:
        """
        Change log level for all existing loggers
        
        Args:
            level: New log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        """
        numeric_level = getattr(logging, level.upper(), logging.INFO)
        cls._log_level = numeric_level
        
        # Update all existing loggers
        for logger in cls._loggers.values():
            logger.setLevel(numeric_level)
            for handler in logger.handlers:
                if isinstance(handler, logging.StreamHandler) and _is_stdio_stream(
                    getattr(handler, "stream", None)
                ):
                    handler.setLevel(numeric_level)

    @classmethod
    def set_console_enabled(cls, enabled: bool) -> None:
        """Enable/disable console log output across current and future loggers."""
        cls._console_enabled = enabled
        for logger in cls._loggers.values():
            for handler in list(logger.handlers):
                if isinstance(handler, logging.StreamHandler) and _is_stdio_stream(
                    getattr(handler, "stream", None)
                ):
                    logger.removeHandler(handler)

            if enabled:
                for handler in list(logger.handlers):
                    if isinstance(handler, logging.NullHandler):
                        logger.removeHandler(handler)
                console_handler = logging.StreamHandler(sys.stdout)
                console_handler.setLevel(cls._log_level)
                if cls._enable_colors and sys.stdout.isatty():
                    console_formatter = ColoredFormatter(
                        '%(levelname)s - %(name)s - %(message)s'
                    )
                else:
                    console_formatter = logging.Formatter(
                        '%(levelname)s - %(name)s - %(message)s'
                    )
                console_handler.setFormatter(console_formatter)
                logger.addHandler(console_handler)
            else:
                # Drop stray NullHandlers from prior toggles, then re-attach if needed.
                for handler in list(logger.handlers):
                    if isinstance(handler, logging.NullHandler):
                        logger.removeHandler(handler)
                if not logger.handlers:
                    _ensure_no_last_resort(logger)

    @classmethod
    def apply_tui_root_mitigation(cls) -> None:
        """Detach stdio StreamHandlers from the root logger (third-party libs).

        Called when starting the Textual TUI so httpx/mcp/etc. do not paint over
        the alternate screen.
        """
        root = logging.getLogger()
        for handler in list(root.handlers):
            if isinstance(handler, logging.StreamHandler) and _is_stdio_stream(
                getattr(handler, "stream", None)
            ):
                root.removeHandler(handler)
        if not root.handlers:
            root.addHandler(logging.NullHandler())
    
    @classmethod
    def get_log_file(cls) -> Optional[Path]:
        """Get the current log file path"""
        return cls._log_file


# Convenience function for getting loggers
def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for a module
    
    Args:
        name: Module name (use __name__)
        
    Returns:
        Configured logger instance
        
    Example:
        from jarvis.core.logger import get_logger
        logger = get_logger(__name__)
        logger.info("Hello, world!")
    """
    return JarvisLogger.get_logger(name)

