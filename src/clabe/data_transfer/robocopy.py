import logging
import shutil
from os import PathLike, makedirs
from pathlib import Path
from typing import ClassVar, Dict, List, Optional

from ..apps import ExecutableApp
from ..apps._base import Command, CommandResult, identity_parser
from ..apps._executors import _DefaultExecutorMixin
from ..services import ServiceSettings
from ._base import DataTransfer

logger = logging.getLogger(__name__)

DEFAULT_EXTRA_ARGS = "/E /DCOPY:DAT /R:100 /W:3 /tee"

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


class RobocopyService(DataTransfer[RobocopySettings], _DefaultExecutorMixin, ExecutableApp):
    """
    A data transfer service that uses Robocopy to copy files between directories.

    Provides a wrapper around the Windows Robocopy utility with configurable options
    for file copying, logging, and directory management. Supports both single
    source-destination pairs and multiple mappings via a dictionary.

    Attributes:
        command: The robocopy command to be executed

    Methods:
        transfer: Executes the Robocopy file transfer
        validate: Validates the Robocopy service configuration
    """

    def __init__(
        self,
        source: PathLike | Dict[PathLike, PathLike],
        settings: RobocopySettings,
    ):
        """
        Initializes the RobocopyService.

        Args:
            source: The source directory/file to copy, or a dict mapping sources to destinations
            settings: RobocopySettings containing options

        Example:
            ```python
            # Single source-destination:
            settings = RobocopySettings(destination="D:/destination")
            service = RobocopyService("C:/source", settings)

            # Multiple source-destination mappings:
            service = RobocopyService({
                "C:/data1": "D:/backup1",
                "C:/data2": "D:/backup2",
            }, settings)
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
        Builds a single command that executes all robocopy operations.

        For single source-destination, returns a direct robocopy command.
        For multiple mappings, chains commands using `cmd /c`.

        Returns:
            A Command object ready for execution.
        """
        if isinstance(self.source, dict):
            src_dst_pairs = [(Path(src), Path(dst)) for src, dst in self.source.items()]
        else:
            src_dst_pairs = [(Path(self.source), Path(self._settings.destination))]

        robocopy_cmds: List[str] = []
        for src, dst in src_dst_pairs:
            if self._settings.force_dir:
                makedirs(dst, exist_ok=True)

            cmd_parts: List[str] = ["robocopy", f"{src.as_posix()}", f"{dst.as_posix()}"]

            if self._settings.extra_args:
                cmd_parts.extend(self._settings.extra_args.split())

            if self._settings.log:
                cmd_parts.append(f"/LOG:{dst / self._settings.log}")
            if self._settings.delete_src:
                cmd_parts.append("/MOV")
            if self._settings.overwrite:
                cmd_parts.append("/IS")

            robocopy_cmds.append(" ".join(cmd_parts))

        if len(robocopy_cmds) == 1:
            # Single command: split back to list for direct execution
            return Command(cmd=robocopy_cmds[0].split(), output_parser=identity_parser)

        # Multiple commands: use cmd /c to chain with & (robocopy is Windows-only)
        chained = " & ".join(robocopy_cmds)
        return Command(cmd=["cmd", "/c", chained], output_parser=identity_parser)

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
        logger.info("Starting robocopy transfer service.")
        self.run()
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
