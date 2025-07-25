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
    value = os.getenv(key, None)
    if value is None:
        raise ValueError(f"Environment variable '{key}' is not set.")
    return value


class AibsLogServerHandlerSettings(ServiceSettings):
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
        import os
        from clabe.logging_helper.aibs import AibsLogServerHandler

        # Initialize the handler with default ERROR level
        handler = AibsLogServerHandler(
            project_name='my_project',
            version='1.0.0',
            host='localhost',
            port=5000
        )

        # Initialize the handler with custom level
        handler = AibsLogServerHandler(
            project_name='my_project',
            version='1.0.0',
            host='localhost',
            port=5000,
            level=logging.WARNING
        )
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
            project_name: The name of the project.
            version: The version of the project.
            host: The hostname of the log server.
            port: The port of the log server.
            rig_id: The ID of the rig. If not provided, it will be read from
                the 'aibs_rig_id' environment variable.
            comp_id: The ID of the computer. If not provided, it will be read
                from the 'aibs_comp_id' environment variable.
            level: The minimum logging level to handle. Defaults to logging.ERROR.
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
        logserver_url: The URL of the log server in the format 'host:port'.
        version: The version of the project.
        project_name: The name of the project.
        level: The minimum logging level to handle. Defaults to logging.ERROR.

    Returns:
        The logger with the added handler.

    Examples:
        ```python
        import logging
        import os
        from clabe.logging_helper.aibs import add_handler

        # Create a logger
        logger = logging.getLogger('my_logger')
        logger.setLevel(logging.INFO)

        # Add the AIBS log server handler with default ERROR level
        logger = add_handler(
            logger,
            logserver_url='localhost:5000',
            version='1.0.0',
            project_name='my_project',
        )

        # Add handler with custom level
        logger = add_handler(
            logger,
            logserver_url='localhost:5000',
            version='1.0.0',
            project_name='my_project',
            level=logging.WARNING
        )
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
        logserver_url: The URL of the log server in the format 'host:port'.
        version: The version of the project.
        project_name: The name of the project.
        level: The minimum logging level to handle. Defaults to logging.ERROR.

    Returns:
        The launcher instance with the attached handler.

    Examples:
        ```python
        import logging
        import os
        from clabe.launcher import BaseLauncher
        from clabe.logging_helper.aibs import attach_to_launcher

        # Initialize the launcher
        launcher = MyLauncher(...) # Replace with your custom launcher class

        # Attach the AIBS log server handler with default ERROR level
        launcher = attach_to_launcher(
            launcher,
            logserver_url='localhost:5000',
            version='1.0.0',
            project_name='my_launcher_project',
        )

        # Attach handler with custom level
        launcher = attach_to_launcher(
            launcher,
            logserver_url='localhost:5000',
            version='1.0.0',
            project_name='my_launcher_project',
            level=logging.WARNING
        )
        ```
    """

    add_handler(
        launcher.logger,
        settings=settings,
    )
    return launcher
