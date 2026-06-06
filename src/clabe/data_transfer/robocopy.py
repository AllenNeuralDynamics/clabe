import logging
import shutil
from os import PathLike, makedirs
from pathlib import Path
from typing import ClassVar, List, Optional

from pydantic import Field

from ..apps import ExecutableApp
from ..apps._base import Command, CommandError, CommandResult, identity_parser
from ..apps._executors import _DefaultExecutorMixin
from ..services import ServiceSettings
from ._base import DataTransfer

logger = logging.getLogger(__name__)

DEFAULT_EXTRA_ARGS = "/E /DCOPY:DAT /R:100 /W:3"

# Robocopy exit codes 0-7 are informational successes; only 8+ indicate errors.
_ROBOCOPY_SUCCESS_MAX = 7

_HAS_ROBOCOPY = shutil.which("robocopy") is not None


class RobocopySettings(ServiceSettings):
    """
    Settings for the RobocopyService.

    Configuration for Robocopy file transfer including destination, logging, and
    copy options.
    """

    __yml_section__: ClassVar[Optional[str]] = "robocopy"

    destination: PathLike
    log: Optional[PathLike] = None
    extra_args: str = DEFAULT_EXTRA_ARGS
    delete_src: bool = False
    overwrite: bool = False
    force_dir: bool = True
    exclude_files: List[str] = Field(default_factory=list)
    exclude_dirs: List[str] = Field(default_factory=list)


class RobocopyService(DataTransfer[RobocopySettings], _DefaultExecutorMixin, ExecutableApp):
    """
    A data transfer service that uses Robocopy to copy files between directories.

    Provides a wrapper around the Windows Robocopy utility with configurable options
    for file copying, logging, and directory management.

    Attributes:
        command: The robocopy command to be executed

    Methods:
        transfer: Executes the Robocopy file transfer
        validate: Validates the Robocopy service configuration
    """

    def __init__(
        self,
        source: PathLike,
        settings: RobocopySettings,
    ):
        """
        Initializes the RobocopyService.

        Args:
            source: The source root directory to copy from
            settings: RobocopySettings containing destination and options

        Example:
            ```python
            settings = RobocopySettings(
                destination="D:/destination",
                exclude_dirs=["__pycache__", ".git"],
                exclude_files=["*.pyc"],
            )
            service = RobocopyService("C:/source", settings)
            ```
        """
        self.source = source
        self._settings = settings
        self._command = self._build_command()

    @property
    def command(self) -> Command[CommandResult]:
        """Returns the robocopy command to be executed."""
        return self._command

    def _build_command(self) -> Command[CommandResult]:
        """
        Builds the robocopy command from the configured source, destination, and options.

        Returns:
            A Command object ready for execution.
        """
        src = Path(self.source)
        dst = Path(self._settings.destination)

        if self._settings.force_dir:
            makedirs(dst, exist_ok=True)

        cmd: List[str] = ["robocopy", str(src), str(dst)]

        if self._settings.extra_args:
            cmd.extend(self._settings.extra_args.split())

        if self._settings.exclude_files:
            cmd.extend(["/XF"] + self._settings.exclude_files)

        if self._settings.exclude_dirs:
            cmd.extend(["/XD"] + self._settings.exclude_dirs)

        if self._settings.log:
            cmd.append(f"/LOG:{dst / self._settings.log}")
        if self._settings.delete_src:
            cmd.append("/MOV")
        if self._settings.overwrite:
            cmd.append("/IS")

        return Command(cmd=cmd, output_parser=identity_parser)

    def transfer(self) -> None:
        """
        Executes the data transfer using Robocopy.

        Uses the command executor pattern to run robocopy with configured settings.

        Example:
            ```python
            settings = RobocopySettings(destination="D:/backup")
            service = RobocopyService("C:/data", settings)
            service.transfer()
            ```
        """
        from ..ui import MessageLevel, notify

        logger.info("Starting robocopy transfer service.")
        notify("Transferring data (robocopy)…", MessageLevel.INFO)
        try:
            self.run()
        except CommandError as e:
            if e.exit_code > _ROBOCOPY_SUCCESS_MAX:
                raise
            logger.debug("Robocopy exited with code %d (informational success).", e.exit_code)
        logger.info("Robocopy transfer completed.")

    def validate(self) -> bool:
        """
        Validates whether the Robocopy command is available on the system.

        Returns:
            True if Robocopy is available, False otherwise
        """
        if not _HAS_ROBOCOPY:
            logger.warning("Robocopy command is not available on this system.")
            return False
        return True
