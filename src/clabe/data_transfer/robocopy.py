import logging
import shutil
from enum import IntFlag
from os import PathLike, makedirs
from pathlib import Path
from typing import ClassVar

from pydantic import Field

from ..apps import ExecutableApp
from ..apps._base import AsyncExecutor, Command, CommandError, CommandResult, Executor, identity_parser
from ..apps._executors import _DefaultExecutorMixin
from ..runnable import runnable
from ..services import ServiceSettings
from ._base import DataTransfer

logger = logging.getLogger(__name__)

DEFAULT_EXTRA_ARGS = "/E /DCOPY:DAT /R:100 /W:3"


class RobocopyExitCode(IntFlag):
    """Bitmask flags returned by Robocopy as its exit code.

    Robocopy ORs these bits together, so a single exit code can encode multiple
    conditions simultaneously (e.g. ``FILES_COPIED | EXTRA_FILES`` == ``3``).
    Codes whose only set bits are in the 0–4 range (i.e. values 0–7) are
    informational successes.  Any code with bit 3 (``COPY_FAILURES``) or bit 4
    (``FATAL_ERROR``) set indicates an actual problem.

    References:
        https://learn.microsoft.com/en-us/troubleshoot/windows-server/backup-and-storage/return-codes-used-robocopy-utility
    """

    NO_CHANGE = 0
    """No files were copied; the destination was already up to date."""
    FILES_COPIED = 1
    """One or more files were copied successfully."""
    EXTRA_FILES = 2
    """Extra files or directories were found in the destination."""
    MISMATCHED = 4
    """Mismatched files or directories were detected."""
    COPY_FAILURES = 8
    """One or more files could not be copied (retry limit exceeded)."""
    FATAL_ERROR = 16
    """Serious error; Robocopy did not copy any files."""


# Bits that, when set, indicate a real failure rather than an informational outcome.
_ROBOCOPY_ERROR_MASK = RobocopyExitCode.COPY_FAILURES | RobocopyExitCode.FATAL_ERROR

_HAS_ROBOCOPY = shutil.which("robocopy") is not None


class _RobocopyCommand(Command[CommandResult]):
    """Command that normalises Robocopy's informational exit codes before they propagate.

    Robocopy exits non-zero even on a perfectly successful copy (codes 1–7 are
    bitmask flags for what happened, not failures).  Catching the resulting
    `CommandError` here — inside `execute` / `execute_async` —
    means callers never observe it, so the ``@runnable`` lifecycle and OTel spans
    always reflect the true outcome regardless of which executor is used.

    Additionally we override the exit code to ensure that downstream code that tries
    to raise from the the results doesn't have false positives.
    """

    def execute(self, executor: Executor) -> CommandResult:
        """Execute the Robocopy command, normalising exit codes."""
        try:
            return super().execute(executor)
        except CommandError as e:
            if e.exit_code & _ROBOCOPY_ERROR_MASK:
                raise
            logger.debug("Robocopy finished: %s (code %d).", RobocopyExitCode(e.exit_code), e.exit_code)
            return CommandResult(stdout=e.stdout, stderr=e.stderr, exit_code=0)

    async def execute_async(self, executor: AsyncExecutor) -> CommandResult:
        """Execute the Robocopy command asynchronously, normalising exit codes."""
        try:
            return await super().execute_async(executor)
        except CommandError as e:
            if e.exit_code & _ROBOCOPY_ERROR_MASK:
                raise
            logger.debug("Robocopy finished: %s (code %d).", RobocopyExitCode(e.exit_code), e.exit_code)
            return CommandResult(stdout=e.stdout, stderr=e.stderr, exit_code=0)


class RobocopySettings(ServiceSettings):
    """
    Settings for the RobocopyService.

    Configuration for Robocopy file transfer including destination, logging, and
    copy options.
    """

    __yml_section__: ClassVar[str | None] = "robocopy"

    destination: PathLike
    log: PathLike | None = None
    extra_args: str = DEFAULT_EXTRA_ARGS
    delete_src: bool = False
    overwrite: bool = False
    force_dir: bool = True
    exclude_files: list[str] = Field(default_factory=list)
    exclude_dirs: list[str] = Field(default_factory=list)


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

        cmd: list[str] = ["robocopy", str(src), str(dst)]

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

        return _RobocopyCommand(cmd=cmd, output_parser=identity_parser)

    @runnable(name="Transfer (robocopy)", notify="Transferring data (robocopy)…")
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
        self.run()

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
