import logging
import shutil
import subprocess
from os import PathLike, makedirs
from pathlib import Path
from typing import Dict, Optional

from .. import ui
from ._base import DataTransfer

logger = logging.getLogger(__name__)

DEFAULT_EXTRA_ARGS = "/E /DCOPY:DAT /R:100 /W:3 /tee"

_HAS_ROBOCOPY = shutil.which("robocopy") is not None


class RobocopyService(DataTransfer):
    """
    A data transfer service that uses the Robocopy command-line utility to copy files
    between source and destination directories.
    """

    def __init__(
        self,
        source: PathLike,
        destination: PathLike,
        log: Optional[PathLike] = None,
        extra_args: Optional[str] = None,
        delete_src: bool = False,
        overwrite: bool = False,
        force_dir: bool = True,
        ui_helper: Optional[ui.UiHelper] = None,
    ):
        """
        Initializes the RobocopyService.

        Args:
            source: The source directory or file to copy.
            destination: The destination directory or file.
            log: Optional log file path for Robocopy output. Default is None.
            extra_args: Additional arguments for the Robocopy command. Default is None.
            delete_src: Whether to delete the source after copying. Default is False.
            overwrite: Whether to overwrite existing files at the destination. Default is False.
            force_dir: Whether to ensure the destination directory exists. Default is True.
            ui_helper: UI helper for user prompts. Default is None.
        """
        self.source = source
        self.destination = destination
        self.delete_src = delete_src
        self.overwrite = overwrite
        self.force_dir = force_dir
        self.log = log
        self.extra_args = extra_args if extra_args else DEFAULT_EXTRA_ARGS
        self._ui_helper = ui_helper or ui.DefaultUIHelper()

    def transfer(
        self,
    ) -> None:
        """
        Executes the data transfer using Robocopy.
        """

        # Loop through each source-destination pair and call robocopy'
        logger.info("Starting robocopy transfer service.")
        src_dist = self._solve_src_dst_mapping(self.source, self.destination)
        if src_dist is None:
            raise ValueError("Source and destination should be provided.")

        for src, dst in src_dist.items():
            dst = Path(dst)
            src = Path(src)
            try:
                command = ["robocopy", f"{src.as_posix()}", f"{dst.as_posix()}", self.extra_args]
                if self.log:
                    command.append(f'/LOG:"{Path(dst) / self.log}"')
                if self.delete_src:
                    command.append("/MOV")
                if self.overwrite:
                    command.append("/IS")
                if self.force_dir:
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

        Args:
            source: A single source path or a dictionary mapping sources to destinations.
            destination: The destination path if the source is a single path.

        Returns:
            A dictionary mapping source paths to destination paths.

        Raises:
            ValueError: If the input arguments are invalid.
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
            True if Robocopy is available, False otherwise.
        """
        if not _HAS_ROBOCOPY:
            logger.error("Robocopy command is not available on this system.")
            return False
        return True

    def prompt_input(self) -> bool:
        """
        Prompts the user to confirm whether to trigger the Robocopy transfer.

        Returns:
            True if the user confirms, False otherwise.
        """
        return self._ui_helper.prompt_yes_no_question("Would you like to trigger robocopy (Y/N)?")
