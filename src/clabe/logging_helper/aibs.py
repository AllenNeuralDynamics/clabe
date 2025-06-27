import logging
import logging.handlers
import os
from typing import TYPE_CHECKING, Optional, TypeVar

if TYPE_CHECKING:
    from ..launcher import BaseLauncher

    TLauncher = TypeVar("TLauncher", bound="BaseLauncher")
else:
    TLauncher = TypeVar("TLauncher")

TLogger = TypeVar("TLogger", bound=logging.Logger)


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
    """

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
            *args: Additional arguments to pass to the SocketHandler.
            **kwargs: Additional keyword arguments to pass to the SocketHandler.
        """
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
        """
        Emits a log record.

        Adds project-specific information to the log record before emitting it.

        Args:
            record: The log record to emit.
        """
        record.project = self.project_name
        record.rig_id = self.rig_id
        record.comp_id = self.comp_id
        record.version = self.version
        record.extra = None  # set extra to None because this sends a pickled record
        super().emit(record)


def add_handler(
    logger: TLogger,
    logserver_url: str,
    version: str,
    project_name: str,
) -> TLogger:
    """
    Adds an AIBS log server handler to the logger.

    Args:
        logger: The logger to add the handler to.
        logserver_url: The URL of the log server in the format 'host:port'.
        version: The version of the project.
        project_name: The name of the project.

    Returns:
        The logger with the added handler.
    """
    host, port = logserver_url.split(":")
    socket_handler = AibsLogServerHandler(
        host=host,
        port=int(port),
        project_name=project_name,
        version=version,
    )
    logger.addHandler(socket_handler)
    return logger


def attach_to_launcher(launcher: TLauncher, logserver_url: str, version: str, project_name: str) -> TLauncher:
    """
    Attaches an AIBS log server handler to a launcher instance.

    Args:
        launcher: The launcher instance to attach the handler to.
        launcher: The launcher instance to attach the handler to.
        logserver_url: The URL of the log server in the format 'host:port'.
        version: The version of the project.
        project_name: The name of the project.

    Returns:
        The launcher instance with the attached handler.
    """

    AibsLogServerHandler.add_handler(
        launcher.logger,
        logserver_url=logserver_url,
        version=version,
        project_name=project_name,
    )
    return launcher
