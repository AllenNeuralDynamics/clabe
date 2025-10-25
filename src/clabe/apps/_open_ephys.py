import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import ClassVar, Optional, Self

import pydantic
from typing_extensions import override

from ..services import ServiceSettings
from ..ui import DefaultUIHelper, UiHelper
from ._base import App

logger = logging.getLogger(__name__)


class OpenEphysAppSettings(ServiceSettings):
    """
    Settings for the BonsaiApp.

    Configuration for Bonsai workflow execution including paths, modes, and
    execution parameters.
    """

    __yml_section__: ClassVar[Optional[str]] = "open_ephys_app"

    signal_chain: os.PathLike
    executable: os.PathLike = Path("./.open_ephys/open_ephys.exe")
    cwd: Optional[os.PathLike] = None
    timeout: Optional[float] = None

    @pydantic.field_validator("signal_chain", "executable", mode="after", check_fields=True)
    @classmethod
    def _resolve_path(cls, value: os.PathLike) -> os.PathLike:
        """
        Resolves the path to an absolute path.

        Args:
            value: The path to resolve

        Returns:
            os.PathLike: The absolute path
        """
        return Path(value).resolve()


class OpenEphysApp(App[None]):
    """
    A class to manage the execution of Open Ephys GUI.

    Handles Open Ephys GUI execution, configuration management, and process
    monitoring for ephys experiments.

    Methods:
        run: Executes the Open Ephys GUI
        get_result: Retrieves the result of the Open Ephys execution
        add_app_settings: Adds or updates application settings
        validate: Validates the Open Ephys application configuration
    """

    def __init__(
        self,
        /,
        settings: OpenEphysAppSettings,
        ui_helper: Optional[UiHelper] = None,
        **kwargs,
    ) -> None:
        """
        Initializes the OpenEphysApp instance.

        Args:
            settings: Settings for the Open Ephys App
            ui_helper: UI helper instance. Defaults to DefaultUIHelper
            **kwargs: Additional keyword arguments

        Example:
            ```python
            # Create and run a Open Ephys app
            app = OpenEphysApp(settings=OpenEphysAppSettings(signal_chain="signal_chain.xml"))
            app.run()
            ```
        """
        self.settings = settings
        self._completed_process: Optional[subprocess.CompletedProcess] = None
        self.ui_helper = ui_helper if ui_helper is not None else DefaultUIHelper()

    def get_result(self, *, allow_stderr: bool = True) -> None:
        """
        Returns the result of the Bonsai process execution.

        Args:
            allow_stderr: Whether to allow stderr in the output. Defaults to True

        Returns:
            None

        Raises:
            RuntimeError: If the app has not been run yet
        """
        if self._completed_process is None:
            raise RuntimeError("The app has not been run yet.")
        return self._process_process_output(allow_stderr=allow_stderr)

    def add_app_settings(self, *args, **kwargs):
        """
        Adds application-specific settings to the additional properties.

        Args:
            *args: Positional arguments (unused)
            **kwargs: Additional keyword arguments to add to settings

        Returns:
            Self: The updated instance of OpenEphysApp
        """
        return self

    def validate(self, *args, **kwargs) -> bool:
        """
        Validates the existence of required files and directories.

        Args:
            *args: Additional positional arguments (unused)
            **kwargs: Additional keyword arguments (unused)

        Returns:
            bool: True if validation is successful

        Raises:
            FileNotFoundError: If any required file or directory is missing
        """
        if not Path(self.settings.executable).exists():
            raise FileNotFoundError(f"Executable not found: {self.settings.executable}")
        if not Path(self.settings.signal_chain).exists():
            raise FileNotFoundError(f"Signal chain file not found: {self.settings.signal_chain}")
        return True

    @override
    def run(self) -> Self:
        """
        Runs the Open Ephys GUI process.

        Returns:
            Self: The updated instance

        Raises:
            FileNotFoundError: If validation fails
            subprocess.CalledProcessError: If the Open Ephys GUI process fails
        """
        self.validate()

        logger.info("Open Ephys process running...")
        try:
            __cmd = f'"{self.settings.executable}" "{self.settings.signal_chain}"'
            logger.debug("Launching Open Ephys with %s", __cmd)
            cwd = self.settings.cwd or os.getcwd()

            proc = subprocess.run(__cmd, cwd=cwd, check=True, timeout=self.settings.timeout, capture_output=True)
        except subprocess.CalledProcessError as e:
            logger.error(
                "Error running Open Ephys GUI process. %s\nProcess stderr: %s",
                e,
                e.stderr if e.stderr else "No stderr output",
            )
            raise
        self._completed_process = proc
        logger.info("Open Ephys GUI process completed.")
        return self

    async def run_async(self) -> Self:
        """
        Runs the Open Ephys GUI process asynchronously without blocking.

        This method executes the Open Ephys GUI in a non-blocking manner,
        allowing other async operations to run concurrently.

        Returns:
            Self: The updated instance

        Raises:
            FileNotFoundError: If validation fails
            subprocess.CalledProcessError: If the Bonsai process fails

        Example:
            ```python
            app = OpenEphysApp(settings=OpenEphysAppSettings(signal_chain="signal_chain.xml"))
            await app.run_async()
            ```
        """
        self.validate()

        logger.info("Open Ephys GUI process running asynchronously...")

        __cmd = f'"{self.settings.executable}" "{self.settings.signal_chain}"'
        logger.debug("Launching Open Ephys GUI asynchronously with %s", __cmd)
        cwd = self.settings.cwd or os.getcwd()

        process = await asyncio.create_subprocess_shell(
            __cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        try:
            if self.settings.timeout is not None:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.settings.timeout,
                )
            else:
                stdout, stderr = await process.communicate()
        except asyncio.TimeoutError as err:
            process.kill()
            await process.wait()
            raise subprocess.TimeoutExpired(
                cmd=__cmd,
                timeout=self.settings.timeout or 0,
                output=None,
                stderr=None,
            ) from err

        # Unfortunately the asyncio implementation does not return a CompletedProcess
        # So, we mock one
        returncode = process.returncode if process.returncode is not None else -1
        self._completed_process = subprocess.CompletedProcess(
            args=__cmd,
            returncode=returncode,
            stdout=stdout.decode() if stdout else "",
            stderr=stderr.decode() if stderr else "",
        )

        if returncode != 0:
            logger.error(
                "Error running Open Ephys GUI process. Return code: %s\nProcess stderr: %s",
                returncode,
                self._completed_process.stderr if self._completed_process.stderr else "No stderr output",
            )
            raise subprocess.CalledProcessError(
                returncode=returncode,
                cmd=__cmd,
                output=self._completed_process.stdout,
                stderr=self._completed_process.stderr,
            )

        logger.info("Open Ephys GUI process completed.")
        return self

    def _process_process_output(self, *, allow_stderr: Optional[bool]) -> None:
        """
        Processes the output from the Open Ephys GUI process result.

        Args:
            allow_stderr: Whether to allow stderr output. If None, prompts user

        Returns:
            None

        Raises:
            RuntimeError: If the app has not been run yet
            subprocess.CalledProcessError: If the process exits with an error
        """
        proc = self._completed_process
        if proc is None:
            raise RuntimeError("The app has not been run yet.")

        try:
            proc.check_returncode()
        except subprocess.CalledProcessError:
            self._log_process_std_output("Open Ephys GUI", proc)
            raise
        else:
            self._log_process_std_output("Open Ephys GUI", proc)
            if len(proc.stderr) > 0:
                logger.error("Open Ephys GUI process finished with errors.")
                if allow_stderr is None:
                    allow_stderr = self.ui_helper.prompt_yes_no_question("Would you like to see the error message?")
                if allow_stderr is False:
                    raise subprocess.CalledProcessError(1, proc.args)
        return

    def _log_process_std_output(self, process_name: str, proc: subprocess.CompletedProcess) -> None:
        """
        Logs the standard output and error of a process.

        Args:
            process_name: Name of the process
            proc: The process result
        """
        if len(proc.stdout) > 0:
            logger.info("%s full stdout dump: \n%s", process_name, proc.stdout)
        if len(proc.stderr) > 0:
            logger.error("%s full stderr dump: \n%s", process_name, proc.stderr)
