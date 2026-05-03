import logging
import sys
from pathlib import Path
from typing import Optional


class ColoredFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"
    BOLD = "\033[1m"

    def format(self, record: logging.LogRecord) -> str:
        levelname = record.levelname
        if levelname in self.COLORS:
            record.levelname = (
                f"{self.COLORS[levelname]}{self.BOLD}{levelname}{self.RESET}"
            )
        return super().format(record)


def _is_stdio_stream(stream: object) -> bool:
    return stream in (sys.stdout, sys.stderr)


def _is_console_stream_handler(handler: logging.Handler) -> bool:
    return isinstance(handler, logging.StreamHandler) and not isinstance(
        handler, logging.FileHandler
    )


def _ensure_no_last_resort(logger: logging.Logger) -> None:
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())


_FILE_FORMATTER = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


class JarvisLogger:
    _loggers = {}
    _initialized = False
    _log_level = logging.INFO
    _log_file: Optional[Path] = None
    _file_handler: Optional[logging.FileHandler] = None
    _console_enabled: bool = True
    _enable_colors: bool = True

    @classmethod
    def setup(
        cls,
        log_level: str = "INFO",
        log_file: Optional[str] = None,
        enable_colors: bool = True,
    ) -> None:
        if cls._initialized:
            return

        numeric_level = getattr(logging, log_level.upper(), logging.INFO)
        cls._log_level = numeric_level
        cls._enable_colors = enable_colors

        if log_file:
            cls._attach_file_handler(log_file)

        cls._initialized = True

    @classmethod
    def configure_file_logging(cls, log_file: str) -> None:
        """Attach (or replace) the file handler after initial setup.

        Call this from jarvis/__init__.py after all core imports have
        settled, because those imports call get_logger() at module level
        which locks _initialized=True before the explicit setup() call
        in __init__.py can supply the log_file path.
        """
        if cls._file_handler is not None:
            for logger in cls._loggers.values():
                logger.removeHandler(cls._file_handler)
            cls._file_handler.close()
            cls._file_handler = None
            cls._log_file = None

        cls._attach_file_handler(log_file)

        for logger in cls._loggers.values():
            if cls._file_handler not in logger.handlers:
                logger.addHandler(cls._file_handler)

    @classmethod
    def _attach_file_handler(cls, log_file: str) -> None:
        cls._log_file = Path(log_file)
        cls._log_file.parent.mkdir(parents=True, exist_ok=True)
        cls._file_handler = logging.FileHandler(
            cls._log_file, mode="a", encoding="utf-8"
        )
        cls._file_handler.setLevel(logging.DEBUG)
        cls._file_handler.setFormatter(_FILE_FORMATTER)

    @classmethod
    def get_logger(cls, name: str) -> logging.Logger:
        if not cls._initialized:
            cls.setup()

        if name in cls._loggers:
            return cls._loggers[name]

        logger = logging.getLogger(name)
        logger.setLevel(cls._log_level)
        logger.handlers.clear()

        if cls._console_enabled:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(cls._log_level)
            if cls._enable_colors and sys.stdout.isatty():
                console_formatter = ColoredFormatter(
                    "%(levelname)s - %(name)s - %(message)s"
                )
            else:
                console_formatter = logging.Formatter(
                    "%(levelname)s - %(name)s - %(message)s"
                )
            console_handler.setFormatter(console_formatter)
            logger.addHandler(console_handler)

        if cls._file_handler:
            logger.addHandler(cls._file_handler)

        if not cls._console_enabled and not cls._file_handler:
            _ensure_no_last_resort(logger)

        logger.propagate = False
        cls._loggers[name] = logger
        return logger

    @classmethod
    def set_level(cls, level: str) -> None:
        numeric_level = getattr(logging, level.upper(), logging.INFO)
        cls._log_level = numeric_level
        for logger in cls._loggers.values():
            logger.setLevel(numeric_level)
            for handler in logger.handlers:
                if _is_console_stream_handler(handler):
                    handler.setLevel(numeric_level)

    @classmethod
    def set_console_enabled(cls, enabled: bool) -> None:
        cls._console_enabled = enabled
        for logger in cls._loggers.values():
            for handler in list(logger.handlers):
                if _is_console_stream_handler(handler):
                    logger.removeHandler(handler)

            if enabled:
                for handler in list(logger.handlers):
                    if isinstance(handler, logging.NullHandler):
                        logger.removeHandler(handler)
                console_handler = logging.StreamHandler(sys.stdout)
                console_handler.setLevel(cls._log_level)
                if cls._enable_colors and sys.stdout.isatty():
                    console_formatter = ColoredFormatter(
                        "%(levelname)s - %(name)s - %(message)s"
                    )
                else:
                    console_formatter = logging.Formatter(
                        "%(levelname)s - %(name)s - %(message)s"
                    )
                console_handler.setFormatter(console_formatter)
                logger.addHandler(console_handler)
            else:
                for handler in list(logger.handlers):
                    if isinstance(handler, logging.NullHandler):
                        logger.removeHandler(handler)
                if not logger.handlers:
                    _ensure_no_last_resort(logger)

    @classmethod
    def apply_tui_root_mitigation(cls) -> None:
        root = logging.getLogger()
        for handler in list(root.handlers):
            if _is_console_stream_handler(handler):
                root.removeHandler(handler)
        if not root.handlers:
            root.addHandler(logging.NullHandler())

    @classmethod
    def get_log_file(cls) -> Optional[Path]:
        return cls._log_file


def get_logger(name: str) -> logging.Logger:
    return JarvisLogger.get_logger(name)
