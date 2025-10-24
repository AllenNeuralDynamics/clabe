from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import shutil
import threading
from pathlib import Path
from typing import Awaitable, Callable, Optional, Self, TypeVar, Union

import pydantic
from aind_behavior_services import (
    AindBehaviorSessionModel,
)

from .. import __version__, logging_helper
from ..git_manager import GitRepository
from ..ui import DefaultUIHelper, UiHelper
from ..utils import abspath, format_datetime, utcnow
from ._cli import LauncherCliArgs

logger = logging.getLogger(__name__)

TModel = TypeVar("TModel", bound=pydantic.BaseModel)
TLauncher = TypeVar("TLauncher", bound="Launcher")


class Launcher:
    """
    Base class for experiment launchers. Provides functionality for managing
    configuration files, directories, and experiment execution.

    This class serves as the foundation for launcher implementations, providing
    session management, directory handling, validation, and lifecycle management.

    Properties:
        logger: The logger instance used by the launcher
        settings: The launcher configuration settings
        session: The registered session model
        session_directory: The path to the session directory

    Methods:
        register_session: Registers a session model with the launcher
        run_experiment: Main entry point for launcher execution
        copy_logs: Closes file handlers and copies temporary data
        make_header: Creates a formatted header string for the launcher
        validate: Validates dependencies required for the launcher
        save_temp_model: Saves a temporary JSON representation of a schema model
        create_directory: Creates a directory at the specified path
    """

    def __init__(
        self,
        *,
        settings: LauncherCliArgs,
        attached_logger: Optional[logging.Logger] = None,
        ui_helper: UiHelper = DefaultUIHelper(),
    ) -> None:
        """
        Initializes the Launcher instance.

        Args:
            settings: The settings for the launcher
            attached_logger: An attached logger instance. Defaults to None
            ui_helper: The UI helper for user interactions. Defaults to DefaultUIHelper
        """
        self._settings = settings
        self.ui_helper = ui_helper
        self.temp_dir = abspath(settings.temp_dir) / format_datetime(utcnow())
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.computer_name = os.environ["COMPUTERNAME"]

        # Solve logger
        if attached_logger:
            _logger = logging_helper.add_file_handler(attached_logger, self.temp_dir / "launcher.log")
        else:
            root_logger = logging.getLogger()
            _logger = logging_helper.add_file_handler(root_logger, self.temp_dir / "launcher.log")

        if settings.debug_mode:
            _logger.setLevel(logging.DEBUG)

        self._logger = _logger

        repository_dir = Path(self.settings.repository_dir) if self.settings.repository_dir is not None else None
        self.repository = GitRepository() if repository_dir is None else GitRepository(path=repository_dir)
        self._cwd = self.repository.working_dir
        os.chdir(self._cwd)

        self._ensure_directory_structure()

        self._session: Optional[AindBehaviorSessionModel] = None
        self._has_copied_logs = False

    def register_session(self, session: AindBehaviorSessionModel) -> Self:
        """
        Registers the session model with the launcher.

        Args:
            session: The session model to register

        Returns:
            Self: The updated instance

        Raises:
            ValueError: If a session is already registered
        """
        if self._session is None:
            self._session = session
            logger.debug("Creating session directory at: %s", self.session_directory)
            self.session_directory.mkdir(parents=True, exist_ok=True)
        else:
            raise ValueError("Session already registered.")
        return self

    @property
    def session(self) -> AindBehaviorSessionModel:
        """
        Returns the registered session model.

        Returns:
            AindBehaviorSessionModel: The session model

        Raises:
            ValueError: If session is not set
        """
        if self._session is None:
            raise ValueError("Session is not set.")
        else:
            return self._session

    def run_experiment(self, experiment: Union[Callable[[Self], None], Callable[[Self], Awaitable[None]]]) -> None:
        """
        Main entry point for the launcher execution.

        Orchestrates the complete launcher workflow including validation,
        experiment execution, and cleanup.

        Args:
            experiment: A callable or async callable that takes the launcher as an argument and runs the experiment

        Example:
            ```python
            def my_experiment(launcher: Launcher):
                # Experiment logic here
                pass

            async def my_async_experiment(launcher: Launcher):
                # Async experiment logic here
                pass

            launcher = Launcher(...)
            launcher.run_experiment(my_experiment)
            launcher.run_experiment(my_async_experiment)
            ```
        """
        _code = 0
        try:
            logger.info(self.make_header())
            if self.settings.debug_mode:
                self._print_debug()

            if not self.settings.debug_mode:
                self.validate()

            result = experiment(self)
            if asyncio.iscoroutine(result):
                asyncio.run(result)

        except KeyboardInterrupt:
            logger.error("User interrupted the process.")
            _code = -1
        except Exception as e:
            logger.error("Launcher failed: %s", e, exc_info=True)
            _code = -1
        finally:
            try:
                self.copy_logs()
            except ValueError as ve:  # In the case session_directory fails
                logger.error("Failed to copy logs: %s", ve)  # we swallow the error
                self._exit(-1)
            else:
                self._exit(_code)

    def copy_logs(self, dst: Optional[os.PathLike] = None, suffix: str = "Behavior/Logs") -> None:
        """
        Closes the file handlers of the launcher and copies the temporary data to the session directory.

        This method is typically called at the end of the launcher by a registered callable that transfers data.

        Args:
            dst: Destination path for logs. If None, uses session_directory/suffix. Defaults to None
            suffix: Suffix to append to session directory path. Defaults to "Behavior/Logs"
        """
        if self._has_copied_logs:
            return None

        logging_helper.close_file_handlers(logger)
        if dst is not None:
            out = self._copy_tmp_directory(dst)
        else:
            out = self._copy_tmp_directory(self.session_directory / suffix)
        logger.info("Copied logs to %s", out)
        self._has_copied_logs = True

    @property
    def logger(self) -> logging.Logger:
        """
        Returns the logger instance used by the launcher.

        Returns:
            logging.Logger: The logger instance
        """
        return self._logger

    @property
    def settings(self) -> LauncherCliArgs:
        """
        Returns the launcher settings.

        Returns:
            LauncherCliArgs: The launcher settings
        """
        return self._settings

    @property
    def session_directory(self) -> Path:
        """
        Returns the session directory path.

        Returns:
            Path: The session directory path

        Raises:
            ValueError: If session_name is not set in the session schema
        """
        session = self.session
        if session.session_name is None:
            raise ValueError("session.session_name is not set.")
        else:
            return Path(session.root_path) / (session.session_name if session.session_name is not None else "")

    def make_header(self) -> str:
        """
        Creates a formatted header string for the launcher.

        Generates a header containing the CLABE ASCII art logo and version information
        for the launcher and schema models.

        Returns:
            str: The formatted header string
        """
        _HEADER = r"""

         ██████╗██╗      █████╗ ██████╗ ███████╗
        ██╔════╝██║     ██╔══██╗██╔══██╗██╔════╝
        ██║     ██║     ███████║██████╔╝█████╗
        ██║     ██║     ██╔══██║██╔══██╗██╔══╝
        ╚██████╗███████╗██║  ██║██████╔╝███████╗
        ╚═════╝╚══════╝╚═╝  ╚═╝╚═════╝ ╚══════╝

        Command-line-interface Launcher for AIND Behavior Experiments
        Press Control+C to exit at any time.
        """

        _str = (
            f"-------------------------------\n{_HEADER}\nCLABE Version: {__version__}\n-------------------------------"
        )

        return _str

    def _exit(self, code: int = 0, _force: bool = False) -> None:
        """
        Exits the launcher with the specified exit code.

        Performs cleanup operations and exits the application, optionally
        prompting the user before exit.

        Args:
            code: The exit code to use. Defaults to 0
            _force: Whether to force exit without user prompt. Defaults to False
        """
        logger.debug("Exiting with code %s", code)
        if logger is not None:
            logging_helper.shutdown_logger(logger)
        if not _force:
            self.ui_helper.input("Press any key to exit...")

    def _print_debug(self) -> None:
        """
        Prints diagnostic information for debugging purposes.

        Outputs detailed information about the launcher state including
        directories, settings, and configuration for troubleshooting.
        """
        logger.debug(
            "-------------------------------\n"
            "Diagnosis:\n"
            "-------------------------------\n"
            "Current Directory: %s\n"
            "Repository: %s\n"
            "Computer Name: %s\n"
            "Data Directory: %s\n"
            "Temporary Directory: %s\n"
            "Settings: %s\n"
            "-------------------------------",
            self._cwd,
            self.repository.working_dir,
            self.computer_name,
            self.settings.data_dir,
            self.temp_dir,
            self.settings,
        )

    def validate(self) -> None:
        """
        Validates the dependencies required for the launcher to run.

        Checks Git repository state, handles dirty repository conditions,
        and ensures all prerequisites are met for experiment execution.

        Example:
            launcher = MyLauncher(...)
            try:
                launcher.validate()
                print("Validation successful")
            except Exception as e:
                print(f"Validation failed: {e}")
        """
        if self.repository.is_dirty():
            logger.warning(
                "Git repository is dirty. Discard changes before continuing unless you know what you are doing!"
                "Uncommitted files: %s",
                self.repository.uncommitted_changes(),
            )
            if not self.settings.allow_dirty:
                self.repository.try_prompt_full_reset(self.ui_helper, force_reset=False)
                if self.repository.is_dirty_with_submodules():
                    logger.error("Dirty repository not allowed. Exiting. Consider running with --allow-dirty flag.")
                    raise RuntimeError("Dirty repository not allowed.")

    def _ensure_directory_structure(self) -> None:
        """
        Creates the required directory structure for the launcher.

        Creates data and temporary directories needed for launcher operation,
        exiting with an error code if creation fails.
        """
        try:
            # Create data directory if it doesn't exist
            if not os.path.exists(self.settings.data_dir):
                self.create_directory(self.settings.data_dir)

            # Create temp directory if it doesn't exist
            if not os.path.exists(self.temp_dir):
                self.create_directory(self.temp_dir)

        except OSError as e:
            logger.error("Failed to create directory structure: %s", e)
            raise

    @staticmethod
    def create_directory(directory: os.PathLike) -> None:
        """
        Creates a directory at the specified path if it does not already exist.

        To prevent deadlocks from network issues/auth, this function will run on a separate thread
        and timeout after 2 seconds.

        Args:
            directory: The path of the directory to create

        Raises:
            OSError: If the directory creation fails
            TimeoutError: If the directory creation times out after 2 seconds
        """

        def _create_directory_with_timeout():
            if not os.path.exists(abspath(directory)):
                logger.debug("Creating  %s", directory)
                try:
                    os.makedirs(directory)
                except OSError as e:
                    logger.error("Failed to create directory %s: %s", directory, e)
                    raise

        thread = threading.Thread(target=_create_directory_with_timeout)
        thread.start()
        thread.join(timeout=2.0)

        if thread.is_alive():
            logger.error("Directory creation timed out after 2 seconds")
            raise TimeoutError(f"Failed to create directory {directory} within 2 seconds")

    def _copy_tmp_directory(self, dst: os.PathLike) -> Path:
        """
        Copies the temporary directory to the specified destination.

        Args:
            dst: The destination path for copying the temporary directory

        Returns:
            Path: The destination path where files were copied
        """
        dst = Path(dst) / ".launcher"
        shutil.copytree(self.temp_dir, dst, dirs_exist_ok=True)
        return dst

    def save_temp_model(self, model: pydantic.BaseModel, directory: Optional[os.PathLike] = None) -> Path:
        """
        Saves a temporary JSON representation of a schema model.

        Args:
            model: The schema model to save
            directory: The directory to save the file in. Defaults to temp_dir if None

        Returns:
            Path: The path to the saved file
        """
        directory = Path(directory) if directory is not None else Path(self.temp_dir)
        os.makedirs(directory, exist_ok=True)

        random_data = str(random.random()).encode("utf-8")
        sha_hash = hashlib.sha256(random_data).hexdigest()[:8]

        fname = f"{model.__class__.__name__}_{sha_hash}.json"
        fpath = os.path.join(directory, fname)
        with open(fpath, "w+", encoding="utf-8") as f:
            f.write(model.model_dump_json(indent=2))
        return Path(fpath)
