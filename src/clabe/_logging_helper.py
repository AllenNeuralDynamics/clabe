import datetime
import logging
import logging.handlers
import os
from pathlib import Path
from typing import Optional, TypeVar, TYPE_CHECKING

import aind_behavior_services.utils as utils
import rich.logging
import rich.style

if TYPE_CHECKING:
    from .launcher import BaseLauncher
    TLauncher = TypeVar("TLauncher", bound="BaseLauncher")
else:
    TLauncher = TypeVar("TLauncher")

TLogger = TypeVar("TLogger", bound=logging.Logger)

fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
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
            **kwargs: Keyword arguments passed to the parent RichHandler (highlighter is removed)
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
            **kwargs: Keyword arguments for the base Formatter class.
                      The 'tz' keyword can be used to specify a timezone
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
            datefmt: An optional date format string (unused)

        Returns:
            str: A string representation of the formatted time
        """
        record_time = datetime.datetime.fromtimestamp(record.created, tz=self._tz)
        return utils.format_datetime(record_time)


utc_formatter = _TzFormatter(fmt, tz=datetime.timezone.utc)


class AibsLogServerHandler(logging.handlers.SocketHandler):
    def __init__(
        self,
        project_name: str,
        version: str,
        host: str,
        port: int,
        rig_id: Optional[str] = None,
        comp_id: Optional[str] = None,
        *args,
        **kwargs,
    ):
        super().__init__(host, port, *args, **kwargs)

        self.project_name = project_name
        self.version = version
        self.rig_id = rig_id or os.getenv("aibs_rig_id", None)
        self.comp_id = comp_id or os.getenv("aibs_comp_id", None)

        if not self.rig_id:
            raise ValueError("Rig id must be provided or set in the environment variable 'aibs_rig_id'.")
        if not self.comp_id:
            raise ValueError("Computer id must be provided or set in the environment variable 'aibs_comp_id'.")

        self.formatter = logging.Formatter(
            fmt="%(asctime)s\n%(name)s\n%(levelname)s\n%(funcName)s (%(filename)s:%(lineno)d)\n%(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def emit(self, record: logging.LogRecord) -> None:
        record.project = self.project_name
        record.rig_id = self.rig_id
        record.comp_id = self.comp_id
        record.version = self.version
        record.extra = None  # set extra to None because this sends a pickled record
        super().emit(record)

    @staticmethod
    def add_handler(
        logger: TLogger,
        logserver_url: str,
        version: str,
        project_name: str,
    ) -> TLogger:
        host, port = logserver_url.split(":")
        socket_handler = AibsLogServerHandler(
            host=host,
            port=int(port),
            project_name=project_name,
            version=version,
        )
        logger.addHandler(socket_handler)
        return logger

    @staticmethod
    def attach_to_launcher(launcher: TLauncher,
                           logserver_url: str,
                           version: str,
                           project_name: str) -> TLauncher:

        AibsLogServerHandler.add_handler(
            launcher.logger,
            logserver_url=logserver_url,
            version=version,
            project_name=project_name,
        )
        return launcher


def add_file_logger(logger: TLogger, output_path: os.PathLike) -> TLogger:
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


def shutdown_logger(logger: TLogger) -> None:
    """
    Shuts down the logger by closing all file handlers and calling logging.shutdown().

    Performs a complete shutdown of the logging system, ensuring all file handlers
    are properly closed and resources are released.

    Args:
        logger: The logger to shut down
    """
    close_file_handlers(logger)
    logging.shutdown()


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
