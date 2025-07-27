from __future__ import annotations

import logging
import os
import shutil
import sys
from abc import ABC
from pathlib import Path
from typing import Any, Generic, Optional, Self, Type, TypeVar, Union

import pydantic
from aind_behavior_services import (
    AindBehaviorRigModel,
    AindBehaviorSessionModel,
    AindBehaviorTaskLogicModel,
)

from .. import __version__, logging_helper
from ..git_manager import GitRepository
from ..ui import DefaultUIHelper, UiHelper
from ..utils import abspath, format_datetime, model_from_json_file, utcnow
from ._cli import BaseLauncherCliArgs
from ._hook_manager import HookManager

TRig = TypeVar("TRig", bound=AindBehaviorRigModel)
TSession = TypeVar("TSession", bound=AindBehaviorSessionModel)
TTaskLogic = TypeVar("TTaskLogic", bound=AindBehaviorTaskLogicModel)
TModel = TypeVar("TModel", bound=pydantic.BaseModel)

logger = logging.getLogger(__name__)

TLauncher = TypeVar("TLauncher", bound="BaseLauncher")


class BaseLauncher(ABC, Generic[TRig, TSession, TTaskLogic]):
    """
    Abstract base class for experiment launchers. Provides common functionality
    for managing configuration files, directories, and execution hooks.

    This class serves as the foundation for all launcher implementations, providing
    schema management, directory handling, validation, and lifecycle management.

    Type Parameters:
        TRig: Type of the rig schema model
        TSession: Type of the session schema model
        TTaskLogic: Type of the task logic schema model
    """

    def __init__(
        self,
        *,
        settings: BaseLauncherCliArgs,
        rig: Type[TRig] | TRig,
        session: Type[TSession] | TSession,
        task_logic: Type[TTaskLogic] | TTaskLogic,
        attached_logger: Optional[logging.Logger] = None,
        ui_helper: UiHelper = DefaultUIHelper(),
        **kwargs,
    ) -> None:
        """
        Initializes the BaseLauncher instance.

        Args:
            settings: The settings for the launcher
            rig_schema_model: The model class for the rig schema
            session_schema_model: The model class for the session schema
            task_logic_schema_model: The model class for the task logic schema
            picker: The picker instance for selecting schemas
            services: The services factory manager. Defaults to None
            attached_logger: An attached logger instance. Defaults to None
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

        # Create hook managers
        self._hooks: HookManager[Self, Any] = HookManager()

        repository_dir = Path(self.settings.repository_dir) if self.settings.repository_dir is not None else None
        self.repository = GitRepository() if repository_dir is None else GitRepository(path=repository_dir)
        self._cwd = self.repository.working_dir
        os.chdir(self._cwd)

        # Schemas
        self._rig_schema_model, self._rig_schema = self._solve_schema_instances(rig, self.settings.rig_path)
        self._session_schema_model, self._session_schema = self._solve_schema_instances(
            session, self.settings.session_path
        )
        self._task_logic_schema_model, self._task_logic_schema = self._solve_schema_instances(
            task_logic, self.settings.task_logic_path
        )

        self._subject: Optional[str] = self.settings.subject

        if self.settings.create_directories is True:
            self._create_directory_structure()

    def main(self) -> None:
        """
        Main entry point for the launcher execution.

        Orchestrates the complete launcher workflow including validation,
        UI prompting, hook execution, and cleanup.

        Example:
            launcher = MyLauncher(...)
            launcher.main()  # Starts the launcher workflow
        """
        try:
            logger.info(self.make_header())
            if self.is_debug_mode:
                self._print_debug()

            if not self.is_debug_mode:
                self.validate()

            logging_helper.close_file_handlers(logger)  # TODO
            self._copy_tmp_directory(self.session_directory / "Behavior" / "Logs")
            self.hook_managers.run(self)

            self.dispose()

        except KeyboardInterrupt:
            logger.critical("User interrupted the process.")
            self._exit(-1)
            return

    @property
    def hook_managers(self) -> HookManager[Self, Any]:
        """
        Returns the hook managers for the launcher.

        Provides access to the pre-run, run, and post-run hook managers
        for managing lifecycle hooks.

        Returns:
            HookManagerCollection[Self, Any]: The hook managers for the launcher
        """
        return self._hooks

    @property
    def logger(self) -> logging.Logger:
        """
        Returns the logger instance used by the launcher.

        Returns:
            logging.Logger: The logger instance
        """
        return self._logger

    @property
    def data_dir(self) -> Path:
        """
        Returns the data directory path.

        Returns:
            Path: The data directory path
        """
        return Path(self.settings.data_dir)

    @property
    def is_debug_mode(self) -> bool:
        """
        Returns whether debug mode is enabled.

        Returns:
            bool: True if debug mode is enabled
        """
        return self.settings.debug_mode

    @property
    def allow_dirty(self) -> bool:
        """
        Returns whether dirty repository is allowed.

        Returns:
            bool: True if dirty repository is allowed
        """
        return self.settings.allow_dirty

    @property
    def skip_hardware_validation(self) -> bool:
        """
        Returns whether hardware validation should be skipped.

        Returns:
            bool: True if hardware validation should be skipped
        """
        return self.settings.skip_hardware_validation

    @property
    def subject(self) -> Optional[str]:
        """
        Returns the current subject name.

        Returns:
            Optional[str]: The subject name or None if not set
        """
        return self.settings.subject

    @subject.setter
    def subject(self, value: str) -> None:
        """
        Sets the subject name.

        Args:
            value: The subject name to set

        Raises:
            ValueError: If subject is already set
        """
        if self.settings.subject is not None:
            raise ValueError("Subject already set.")
        self.settings.subject = value

    @property
    def settings(self) -> BaseLauncherCliArgs:
        """
        Returns the launcher settings.

        Returns:
            BaseLauncherCliArgs: The launcher settings
        """
        return self._settings

    # Public properties / interfaces
    @property
    def rig_schema(self) -> TRig:
        """
        Returns the rig schema instance.

        Returns:
            TRig: The rig schema instance

        Raises:
            ValueError: If rig schema instance is not set
        """
        if self._rig_schema is None:
            raise ValueError("Rig schema instance not set.")
        return self._rig_schema

    @property
    def session_schema(self) -> TSession:
        """
        Returns the session schema instance.

        Returns:
            TSession: The session schema instance

        Raises:
            ValueError: If session schema instance is not set
        """
        if self._session_schema is None:
            raise ValueError("Session schema instance not set.")
        return self._session_schema

    @property
    def task_logic_schema(self) -> TTaskLogic:
        """
        Returns the task logic schema instance.

        Returns:
            TTaskLogic: The task logic schema instance

        Raises:
            ValueError: If task logic schema instance is not set
        """
        if self._task_logic_schema is None:
            raise ValueError("Task logic schema instance not set.")
        return self._task_logic_schema

    @property
    def rig_schema_model(self) -> Type[TRig]:
        """
        Returns the rig schema model class.

        Returns:
            Type[TRig]: The rig schema model class
        """
        return self._rig_schema_model

    @property
    def session_schema_model(self) -> Type[TSession]:
        """
        Returns the session schema model class.

        Returns:
            Type[TSession]: The session schema model class
        """
        return self._session_schema_model

    @property
    def task_logic_schema_model(self) -> Type[TTaskLogic]:
        """
        Returns the task logic schema model class.

        Returns:
            Type[TTaskLogic]: The task logic schema model class
        """
        return self._task_logic_schema_model

    @property
    def session_directory(self) -> Path:
        """
        Returns the session directory path.

        Returns:
            Path: The session directory path

        Raises:
            ValueError: If session_name is not set in the session schema
        """
        if self.session_schema.session_name is None:
            raise ValueError("session_schema.session_name is not set.")
        else:
            return Path(self.session_schema.root_path) / (
                self.session_schema.session_name if self.session_schema.session_name is not None else ""
            )

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
            "-------------------------------\n"
            f"{_HEADER}\n"
            f"CLABE Version: {__version__}\n"
            f"TaskLogic ({self.task_logic_schema_model.__name__}) Schema Version: {self.task_logic_schema_model.model_construct().version}\n"
            f"Rig ({self.rig_schema_model.__name__}) Schema Version: {self.rig_schema_model.model_construct().version}\n"
            f"Session ({self.session_schema_model.__name__}) Schema Version: {self.session_schema_model.model_construct().version}\n"
            "-------------------------------"
        )

        return _str

    def _exit(self, code: int = 0, _force: bool = False) -> None:
        """
        Exits the launcher with the specified exit code.

        Performs cleanup operations and exits the application, optionally
        prompting the user before exit.

        Args:
            code: The exit code to use
            _force: Whether to force exit without user prompt
        """
        logger.info("Exiting with code %s", code)
        if logger is not None:
            logging_helper.shutdown_logger(logger)
        if not _force:
            self.ui_helper.input("Press any key to exit...")
        sys.exit(code)

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
            self.data_dir,
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
        try:
            if self.repository.is_dirty():
                logger.warning(
                    "Git repository is dirty. Discard changes before continuing unless you know what you are doing!"
                )
                if not self.allow_dirty:
                    self.repository.try_prompt_full_reset(self.ui_helper, force_reset=False)
                    if self.repository.is_dirty_with_submodules():
                        logger.critical(
                            "Dirty repository not allowed. Exiting. Consider running with --allow-dirty flag."
                        )
                        self._exit(-1)
                else:
                    logger.info("Uncommitted files: %s", self.repository.uncommitted_changes())

        except Exception as e:
            logger.critical("Failed to validate dependencies. %s", e)
            self._exit(-1)
            raise e

    def dispose(self) -> None:
        """
        Cleans up resources and exits the launcher.

        Performs final cleanup operations and gracefully exits the launcher
        with a success code.

        Example:
            launcher = MyLauncher(...)
            launcher.dispose()  # Cleans up and exits
        """
        logger.info("Disposing...")
        self._exit(0)

    def _create_directory_structure(self) -> None:
        """
        Creates the required directory structure for the launcher.

        Creates data and temporary directories needed for launcher operation,
        exiting with an error code if creation fails.
        """
        try:
            self.create_directory(self.data_dir)
            self.create_directory(self.temp_dir)

        except OSError as e:
            logger.critical("Failed to create directory structure: %s", e)
            self._exit(-1)

    @classmethod
    def create_directory(cls, directory: os.PathLike) -> None:
        """
        Creates a directory at the specified path if it does not already exist.

        Args:
            directory: The path of the directory to create

        Raises:
            OSError: If the directory creation fails
        """
        if not os.path.exists(abspath(directory)):
            logger.info("Creating  %s", directory)
            try:
                os.makedirs(directory)
            except OSError as e:
                logger.error("Failed to create directory %s: %s", directory, e)
                raise e

    def _copy_tmp_directory(self, dst: os.PathLike) -> None:
        """
        Copies the temporary directory to the specified destination.

        Args:
            dst: The destination path for copying the temporary directory
        """
        dst = Path(dst) / ".launcher"
        shutil.copytree(self.temp_dir, dst, dirs_exist_ok=True)

    def _solve_schema_instances(
        self,
        cls_input: Type[TModel] | TModel,
        path: Optional[os.PathLike],
    ) -> tuple[Type[TModel], Optional[TModel]]:
        """
        Resolves and loads schema instances for the rig and task logic.

        Loads schema definitions from JSON files and assigns them to the
        corresponding attributes if file paths are provided.

        Args:
            rig_path_path: Path to the JSON file containing the rig schema
            session_path: Path to the JSON file containing the session schema
            task_logic_path: Path to the JSON file containing the task logic schema
        """
        _instance: Optional[TModel] = None
        if not isinstance(cls_input, type):
            return type(cls_input), cls_input
        else:
            _cls = cls_input
        if path is not None:
            logger.info("Loading schema from %s", path)
            _instance = model_from_json_file(path, _cls)
        return _cls, _instance

    def save_temp_model(self, model: Union[TRig, TSession, TTaskLogic], directory: Optional[os.PathLike]) -> str:
        """
        Saves a temporary JSON representation of a schema model.

        Args:
            model (Union[TRig, TSession, TTaskLogic]): The schema model to save.
            directory (Optional[os.PathLike]): The directory to save the file in.

        Returns:
            str: The path to the saved file.
        """
        directory = Path(directory) if directory is not None else Path(self.temp_dir)
        os.makedirs(directory, exist_ok=True)
        fname = model.__class__.__name__ + ".json"
        fpath = os.path.join(directory, fname)
        with open(fpath, "w+", encoding="utf-8") as f:
            f.write(model.model_dump_json(indent=3))
        return fpath
