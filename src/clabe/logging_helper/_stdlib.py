import datetime
import logging
import os
from pathlib import Path
from typing import TypeVar

import aind_behavior_services.utils as utils
import rich.logging
import rich.style

TLogger = TypeVar("TLogger", bound=logging.Logger)

log_fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
datetime_fmt = "%Y-%m-%dT%H%M%S%z"


class _SeverityHighlightingHandler(rich.logging.RichHandler):
    """
    A custom logging handler that highlights log messages based on severity.

    This handler extends RichHandler to provide visual highlighting for error and critical
    log messages using different styles and colors for better visibility.

    Attributes:
        error_style (rich.style.Style): Style for error level messages
        critical_style (rich.style.Style): Style for critical level messages
    """

    def __init__(self, *args, **kwargs):
        """
        Initializes the severity highlighting handler.

        Args:
            *args: Arguments passed to the parent RichHandler
            **kwargs: Keyword arguments passed to the parent RichHandler (highlighter is removed if present)
        """
        # I don't think this is necessary, but just in case, better to fail early
        if "highlighter" in kwargs:
            del kwargs["highlighter"]
        super().__init__(*args, **kwargs)

        self.error_style = rich.style.Style(color="white", bgcolor="red")
        self.critical_style = rich.style.Style(color="white", bgcolor="red", bold=True)

    def render_message(self, record, message):  # type: ignore[override]
        """
        Renders log messages with severity-based styling.

        Applies different visual styles to log messages based on their severity level,
        with special formatting for error and critical messages.

        Args:
            record: The log record containing message metadata
            message: The log message to render

        Returns:
            str: The styled message string
        """
        if record.levelno >= logging.CRITICAL:
            return f"[{self.critical_style}]{message}[/]"
        elif record.levelno >= logging.ERROR:
            return f"[{self.error_style}]{message}[/]"
        else:
            return message


rich_handler = _SeverityHighlightingHandler(rich_tracebacks=True, show_time=False)


class _TzFormatter(logging.Formatter):
    """
    A custom logging formatter that supports timezone-aware timestamps.

    This formatter extends the standard logging.Formatter to provide timezone-aware
    timestamp formatting for log records.

    Attributes:
        _tz (Optional[timezone]): The timezone to use for formatting timestamps
    """

    def __init__(self, *args, **kwargs):
        """
        Initializes the formatter with optional timezone information.

        Args:
            *args: Positional arguments for the base Formatter class
            **kwargs: Keyword arguments for the base Formatter class. The 'tz' keyword can be used to specify a timezone
        """
        self._tz = kwargs.pop("tz", None)
        super().__init__(*args, **kwargs)

    def formatTime(self, record, datefmt=None) -> str:
        """
        Formats the time of a log record using the specified timezone.

        Converts the log record timestamp to the configured timezone and formats
        it using the AIND behavior services datetime formatting utilities.

        Args:
            record: The log record to format
            datefmt: An optional date format string (unused). Defaults to None

        Returns:
            str: A string representation of the formatted time
        """
        record_time = datetime.datetime.fromtimestamp(record.created, tz=self._tz)
        return utils.format_datetime(record_time)


utc_formatter = _TzFormatter(log_fmt, tz=datetime.timezone.utc)


def add_file_handler(logger: TLogger, output_path: os.PathLike) -> TLogger:
    """
    Adds a file handler to the logger to write logs to a file.

    Creates a new file handler with UTC timezone formatting and adds it to the
    specified logger for persistent log storage.

    Args:
        logger: The logger to which the file handler will be added
        output_path: The path to the log file

    Returns:
        TLogger: The logger with the added file handler
    """
    file_handler = logging.FileHandler(Path(output_path), encoding="utf-8", mode="w")
    file_handler.setFormatter(utc_formatter)
    logger.addHandler(file_handler)
    return logger


def shutdown_logger(logger: TLogger) -> TLogger:
    """
    Shuts down the logger by closing all file handlers and calling logging.shutdown().

    Performs a complete shutdown of the logging system, ensuring all file handlers
    are properly closed and resources are released.

    Args:
        logger: The logger to shut down

    Returns:
        TLogger: The logger with closed file handlers
    """
    close_file_handlers(logger)
    logging.shutdown()
    return logger


def close_file_handlers(logger: TLogger) -> TLogger:
    """
    Closes all file handlers associated with the logger.

    Iterates through all handlers associated with the logger and closes any
    file handlers to ensure proper resource cleanup.

    Args:
        logger: The logger whose file handlers will be closed

    Returns:
        TLogger: The logger with closed file handlers
    """
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler):
            handler.close()
    return logger
