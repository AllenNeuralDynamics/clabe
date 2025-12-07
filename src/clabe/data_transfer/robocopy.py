import logging
import shutil
import subprocess
from os import PathLike, makedirs
from pathlib import Path
from typing import ClassVar, Dict, Optional

from .. import ui
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

    __yml_section__: ClassVar[str] = "robocopy"

    destination: PathLike
    log: Optional[PathLike] = None
    extra_args: str = DEFAULT_EXTRA_ARGS
    delete_src: bool = False
    overwrite: bool = False
    force_dir: bool = True


class RobocopyService(DataTransfer[RobocopySettings]):
    """
    A data transfer service that uses Robocopy to copy files between directories.

    Provides a wrapper around the Windows Robocopy utility with configurable options
    for file copying, logging, and directory management.

    Methods:
        transfer: Executes the Robocopy file transfer
        validate: Validates the Robocopy service configuration
        prompt_input: Prompts the user to confirm the file transfer
    """

    def __init__(
        self,
        source: PathLike,
        settings: RobocopySettings,
        *,
        ui_helper: Optional[ui.IUiHelper] = None,
    ):
        """
        Initializes the RobocopyService.

        Args:
            source: The source directory or file to copy
            settings: RobocopySettings containing destination and options
            ui_helper: UI helper for user prompts. Default is None

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
        self._ui_helper = ui_helper or ui.DefaultUIHelper()

    def transfer(
        self,
    ) -> None:
        """
        Executes the data transfer using Robocopy.

        Processes source-destination mappings and executes Robocopy commands
        for each pair, handling logging and error reporting.
        """

        # Loop through each source-destination pair and call robocopy'
        logger.info("Starting robocopy transfer service.")
        src_dist = self._solve_src_dst_mapping(self.source, self._settings.destination)
        if src_dist is None:
            raise ValueError("Source and destination should be provided.")

        for src, dst in src_dist.items():
            dst = Path(dst)
            src = Path(src)
            try:
                command = ["robocopy", f"{src.as_posix()}", f"{dst.as_posix()}", self._settings.extra_args]
                if self._settings.log:
                    command.append(f'/LOG:"{Path(dst) / self._settings.log}"')
                if self._settings.delete_src:
                    command.append("/MOV")
                if self._settings.overwrite:
                    command.append("/IS")
                if self._settings.force_dir:
                    makedirs(dst, exist_ok=True)
                cmd = " ".join(command)
                logger.info("Running Robocopy command: %s", cmd)
                with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process:
                    if process.stdout:
                        for line in process.stdout:
                            logger.info(line.strip())
                _ = process.wait()
                logger.info("Successfully copied from %s to %s:\n", src, dst)
            except subprocess.CalledProcessError as e:
                logger.error("Error copying from %s to %s:\n%s", src, dst, e.stdout)

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

    def prompt_input(self) -> bool:
        """
        Prompts the user to confirm whether to trigger the Robocopy transfer.

        Returns:
            True if the user confirms, False otherwise

        Example:
            ```python
            # Interactive transfer confirmation:
            settings = RobocopySettings(destination="D:/backup")
            service = RobocopyService("C:/data", settings)
            if service.prompt_input():
                service.transfer()
                # User confirmed, transfer proceeds
            else:
                print("Transfer cancelled by user")
            ```
        """
        return self._ui_helper.prompt_yes_no_question("Would you like to trigger robocopy (Y/N)?")
