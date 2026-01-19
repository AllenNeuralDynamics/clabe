import logging
import shutil
from os import PathLike, makedirs
from pathlib import Path
from typing import ClassVar, Dict, Optional

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
    for file copying, logging, and directory management.

    Attributes:
        command: The underlying robocopy command that will be executed

    Methods:
        transfer: Executes the Robocopy file transfer
        validate: Validates the Robocopy service configuration
        prompt_input: Prompts the user to confirm the file transfer
    """

    def __init__(
        self,
        source: PathLike,
        settings: RobocopySettings,
    ):
        """
        Initializes the RobocopyService.

        Args:
            source: The source directory or file to copy
            settings: RobocopySettings containing destination and options

        Example:
            ```python
            # Initialize with basic parameters:
            settings = RobocopySettings(destination="D:/destination")
            service = RobocopyService("C:/source", settings)

            # Initialize with logging and move operation:
            settings = RobocopySettings(
                destination="D:/archive/data",
                log="transfer.log",
                delete_src=True,
                extra_args="/E /COPY:DAT /R:10"
            )
            service = RobocopyService("C:/temp/data", settings)
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
        Builds the robocopy command based on settings.

        Returns:
            A Command object ready for execution

        Raises:
            ValueError: If source and destination mapping cannot be resolved
        """
        src_dst = self._solve_src_dst_mapping(self.source, self._settings.destination)
        if src_dst is None:
            raise ValueError("Source and destination should be provided.")

        commands = []
        for src, dst in src_dst.items():
            dst = Path(dst)
            src = Path(src)

            if self._settings.force_dir:
                makedirs(dst, exist_ok=True)

            cmd_parts = ["robocopy", f'"{src.as_posix()}"', f'"{dst.as_posix()}"', self._settings.extra_args]

            if self._settings.log:
                cmd_parts.append(f'/LOG:"{Path(dst) / self._settings.log}"')
            if self._settings.delete_src:
                cmd_parts.append("/MOV")
            if self._settings.overwrite:
                cmd_parts.append("/IS")

            commands.append(" ".join(cmd_parts))

        # TODO there may be a better way to chain with robocopy
        full_command = " && ".join(commands)
        return Command(cmd=full_command, output_parser=identity_parser)

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

    @staticmethod
    def _solve_src_dst_mapping(
        source: Optional[PathLike | Dict[PathLike, PathLike]], destination: Optional[PathLike]
    ) -> Optional[Dict[PathLike, PathLike]]:
        """
        Resolves the mapping between source and destination paths.

        Handles both single path mappings and dictionary-based multiple mappings
        to create a consistent source-to-destination mapping structure.

        Args:
            source: A single source path or a dictionary mapping sources to destinations
            destination: The destination path if the source is a single path

        Returns:
            A dictionary mapping source paths to destination paths

        Raises:
            ValueError: If the input arguments are invalid or inconsistent
        """
        if source is None:
            return None
        if isinstance(source, dict):
            if destination:
                raise ValueError("Destination should not be provided when source is a dictionary.")
            else:
                return source
        else:
            source = Path(source)
            if not destination:
                raise ValueError("Destination should be provided when source is a single path.")
            return {source: Path(destination)}

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
