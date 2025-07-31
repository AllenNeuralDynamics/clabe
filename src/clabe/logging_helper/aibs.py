import logging
import logging.handlers
import os
from typing import TYPE_CHECKING, ClassVar, TypeVar

import pydantic

from ..services import ServiceSettings

if TYPE_CHECKING:
    from ..launcher import BaseLauncher

    TLauncher = TypeVar("TLauncher", bound="BaseLauncher")
else:
    TLauncher = TypeVar("TLauncher")

TLogger = TypeVar("TLogger", bound=logging.Logger)


def _getenv(key: str) -> str:
    """Gets an environment variable, raising a ValueError if it is not set."""
    value = os.getenv(key, None)
    if value is None:
        raise ValueError(f"Environment variable '{key}' is not set.")
    return value


class AibsLogServerHandlerSettings(ServiceSettings):
    """
    Settings for the AIBS log server handler.

    Attributes:
        rig_id (str): The ID of the rig.
        comp_id (str): The ID of the computer.
        project_name (str): The name of the project.
        version (str): The version of the project.
        host (str): The hostname of the log server.
        port (int): The port of the log server.
        level (int): The logging level.
    """

    _yml_section: ClassVar[str] = "aibs_log_server_handler"

    rig_id: str = pydantic.Field(default_factory=lambda: _getenv("aibs_rig_id"))
    comp_id: str = pydantic.Field(default_factory=lambda: _getenv("aibs_comp_id"))
    project_name: str
    version: str
    host: str = "eng-logtools.corp.alleninstitute.org"
    port: int = 9000
    level: int = logging.ERROR


class AibsLogServerHandler(logging.handlers.SocketHandler):
    """
    A custom logging handler that sends log records to the AIBS log server.

    This handler extends the standard SocketHandler to include project-specific
    metadata in the log records before sending them to the log server.

    Attributes:
        project_name (str): The name of the project.
        version (str): The version of the project.
        rig_id (str): The ID of the rig.
        comp_id (str): The ID of the computer.

    Examples:
        ```python
        import logging
        from clabe.logging_helper.aibs import AibsLogServerHandler, AibsLogServerHandlerSettings

        # Initialize the handler with settings
        settings = AibsLogServerHandlerSettings(
            project_name='my_project',
            version='1.0.0',
            host='localhost',
            port=5000
        )
        handler = AibsLogServerHandler(settings=settings)

        # Initialize with custom level
        settings = AibsLogServerHandlerSettings(
            project_name='my_project',
            version='1.0.0',
            host='localhost',
            port=5000,
            level=logging.WARNING
        )
        handler = AibsLogServerHandler(settings=settings)
        ```
    """

    def __init__(
        self,
        settings: AibsLogServerHandlerSettings,
        *args,
        **kwargs,
    ):
        """
        Initializes the AIBS log server handler.

        Args:
            settings: AibsLogServerHandlerSettings containing all configuration options
            *args: Additional arguments to pass to the SocketHandler.
            **kwargs: Additional keyword arguments to pass to the SocketHandler.
        """
        super().__init__(settings.host, settings.port, *args, **kwargs)
        self.setLevel(settings.level)
        self._settings = settings

        self.formatter = logging.Formatter(
            fmt="%(asctime)s\n%(name)s\n%(levelname)s\n%(funcName)s (%(filename)s:%(lineno)d)\n%(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emits a log record.

        Adds project-specific information to the log record before emitting it.

        Args:
            record: The log record to emit.
        """
        record.project = self._settings.project_name
        record.rig_id = self._settings.rig_id
        record.comp_id = self._settings.comp_id
        record.version = self._settings.version
        record.extra = None  # set extra to None because this sends a pickled record
        super().emit(record)


def add_handler(
    logger: TLogger,
    settings: AibsLogServerHandlerSettings,
) -> TLogger:
    """
    Adds an AIBS log server handler to the logger.

    Args:
        logger: The logger to add the handler to.
        settings: AibsLogServerHandlerSettings containing configuration options

    Returns:
        The logger with the added handler.

    Examples:
        ```python
        import logging
        from clabe.logging_helper.aibs import add_handler, AibsLogServerHandlerSettings

        # Create a logger
        logger = logging.getLogger('my_logger')
        logger.setLevel(logging.INFO)

        # Add the AIBS log server handler with default ERROR level
        settings = AibsLogServerHandlerSettings(
            project_name='my_project',
            version='1.0.0',
            host='localhost',
            port=5000
        )
        logger = add_handler(logger, settings)

        # Add handler with custom level
        settings = AibsLogServerHandlerSettings(
            project_name='my_project',
            version='1.0.0',
            host='localhost',
            port=5000,
            level=logging.WARNING
        )
        logger = add_handler(logger, settings)
        ```
    """
    socket_handler = AibsLogServerHandler(settings=settings)
    logger.addHandler(socket_handler)
    return logger


def attach_to_launcher(launcher: TLauncher, settings: AibsLogServerHandlerSettings) -> TLauncher:
    """
    Attaches an AIBS log server handler to a launcher instance.

    Args:
        launcher: The launcher instance to attach the handler to.
        settings: AibsLogServerHandlerSettings containing configuration options

    Returns:
        The launcher instance with the attached handler.

    Examples:
        ```python
        import logging
        from clabe.launcher import BaseLauncher
        from clabe.logging_helper.aibs import attach_to_launcher, AibsLogServerHandlerSettings

        # Initialize the launcher
        launcher = MyLauncher(...) # Replace with your custom launcher class

        # Attach the AIBS log server handler with default ERROR level
        settings = AibsLogServerHandlerSettings(
            project_name='my_launcher_project',
            version='1.0.0',
            host='localhost',
            port=5000
        )
        launcher = attach_to_launcher(launcher, settings)

        # Attach handler with custom level
        settings = AibsLogServerHandlerSettings(
            project_name='my_launcher_project',
            version='1.0.0',
            host='localhost',
            port=5000,
            level=logging.WARNING
        )
        launcher = attach_to_launcher(launcher, settings)
        ```
    """

    add_handler(
        launcher.logger,
        settings=settings,
    )
    return launcher
