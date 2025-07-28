from __future__ import annotations

import logging
import os
import shutil
import sys
from abc import ABC
from pathlib import Path
from typing import Any, Callable, Generic, List, Optional, Self, Type, TypeVar, overload

import pydantic
from aind_behavior_services import (
    AindBehaviorRigModel,
    AindBehaviorSessionModel,
    AindBehaviorTaskLogicModel,
)
from typing_extensions import Literal

from .. import __version__, logging_helper
from ..git_manager import GitRepository
from ..ui import DefaultUIHelper, UiHelper
from ..utils import abspath, format_datetime, utcnow
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
        rig: TRig | Type[TRig],
        session: TSession | Type[TSession],
        task_logic: TTaskLogic | Type[TTaskLogic],
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
        self._hook_manager: HookManager[Self, Any] = HookManager()

        repository_dir = Path(self.settings.repository_dir) if self.settings.repository_dir is not None else None
        self.repository = GitRepository() if repository_dir is None else GitRepository(path=repository_dir)
        self._cwd = self.repository.working_dir
        os.chdir(self._cwd)

        # Schemas
        self._rig, self._rig_model = self._resolve_model(rig)
        self._session, self._session_model = self._resolve_model(session)
        self._task_logic, self._task_logic_model = self._resolve_model(task_logic)

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
            if self.settings.debug_mode:
                self._print_debug()

            if not self.settings.debug_mode:
                self.validate()

            self.hook_manager.run(self)
            logging_helper.close_file_handlers(logger)  # TODO
            self._copy_tmp_directory(self.session_directory / "Behavior" / "Logs")  # TODO
            self.dispose()

        except KeyboardInterrupt:
            logger.critical("User interrupted the process.")
            self._exit(-1)
            return

    @property
    def hook_manager(self) -> HookManager[Self, Any]:
        """
        Returns the hook managers for the launcher.

        Provides access to the pre-run, run, and post-run hook managers
        for managing lifecycle hooks.

        Returns:
            HookManagerCollection[Self, Any]: The hook managers for the launcher
        """
        return self._hook_manager

    @overload
    def register_hook(self, hook: Callable[[Self], Any]) -> Self: ...
    @overload
    def register_hook(self, hook: List[Callable[[Self], Any]]) -> Self: ...

    def register_hook(self, hook: Callable[[Self], Any] | List[Callable[[Self], Any]]) -> Self:
        """
        Adds a hook to the launcher.

        Args:
            hook: The hook to add to the launcher
        """
        if isinstance(hook, list):
            for h in hook:
                self.hook_manager.register(h)
        else:
            self.hook_manager.register(hook)
        return self

    @property
    def logger(self) -> logging.Logger:
        """
        Returns the logger instance used by the launcher.

        Returns:
            logging.Logger: The logger instance
        """
        return self._logger

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
    @overload
    def get_rig(self) -> Optional[TRig]: ...
    @overload
    def get_rig(self, strict: Literal[False]) -> Optional[TRig]: ...
    @overload
    def get_rig(self, strict: Literal[True]) -> TRig: ...
    def get_rig(self, strict: bool = False) -> Optional[TRig]:
        """
        Returns the rig schema instance.

        Returns:
            TRig: The rig schema instance

        Raises:
            ValueError: If rig schema instance is not set
        """
        if self._rig is None and strict:
            raise ValueError("Rig schema instance is not set.")
        return self._rig

    def set_rig(self, rig: TRig, validate: bool = True) -> None:
        if self._rig is not None:
            raise ValueError("Rig already set.")
        if validate:
            if not isinstance(rig, self._rig_model):
                raise ValueError("Invalid rig schema instance.")
        self._rig = rig
        self._rig_model = type(rig)

    def get_rig_model(self) -> Type[TRig]:
        """
        Returns the rig schema model class.

        Returns:
            Type[TRig]: The rig schema model class
        """
        return self._rig_model

    @overload
    def get_session(self) -> Optional[TSession]: ...
    @overload
    def get_session(self, strict: Literal[False]) -> Optional[TSession]: ...
    @overload
    def get_session(self, strict: Literal[True]) -> TSession: ...

    def get_session(self, strict: bool = False) -> Optional[TSession]:
        """
        Returns the session schema instance.
        Args:
            strict: If True, raises ValueError if session schema is not set

        Returns:
            TSession: The session schema instance
        """
        if self._session is None and strict:
            raise ValueError("Session schema instance is not set.")
        return self._session

    def set_session(self, session: TSession, validate: bool = True) -> None:
        if self._session is not None:
            raise ValueError("Session already set.")
        if validate:
            if not isinstance(session, self._session_model):
                raise ValueError("Invalid session schema instance.")
        self._session = session
        self._session_model = type(session)

    def get_session_model(self) -> Type[TSession]:
        """
        Returns the session schema model class.

        Returns:
            Type[TSession]: The session schema model class
        """
        return self._session_model

    @overload
    def get_task_logic(self) -> Optional[TTaskLogic]: ...
    @overload
    def get_task_logic(self, strict: Literal[False]) -> Optional[TTaskLogic]: ...
    @overload
    def get_task_logic(self, strict: Literal[True]) -> TTaskLogic: ...
    def get_task_logic(self, strict: bool = False) -> Optional[TTaskLogic]:
        """
        Returns the task logic schema instance.
        Args:
            strict: If True, raises ValueError if task logic schema is not set
        Returns:
            TTaskLogic: The task logic schema instance
        """
        if self._task_logic is None and strict:
            raise ValueError("Task logic schema instance is not set.")
        return self._task_logic

    def set_task_logic(self, task_logic: TTaskLogic, validate: bool = True) -> None:
        if self._task_logic is not None:
            raise ValueError("Task logic already set.")
        if validate:
            if not isinstance(task_logic, self._task_logic_model):
                raise ValueError("Invalid task logic schema instance.")
        self._task_logic = task_logic
        self._task_logic_model = type(task_logic)

    def get_task_logic_model(self) -> Type[TTaskLogic]:
        """
        Returns the task logic schema model class.

        Returns:
            Type[TTaskLogic]: The task logic schema model class
        """
        return self._task_logic_model

    @property
    def session_directory(self) -> Path:
        """
        Returns the session directory path.

        Returns:
            Path: The session directory path

        Raises:
            ValueError: If session_name is not set in the session schema
        """
        session = self.get_session()
        if session is None:
            raise ValueError("Session schema is not set.")
        if session.session_name is None:
            raise ValueError("session_schema.session_name is not set.")
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
            "-------------------------------\n"
            f"{_HEADER}\n"
            f"CLABE Version: {__version__}\n"
            f"TaskLogic ({self.get_task_logic_model().__name__}) Schema Version: {self.get_task_logic_model().model_construct().version}\n"
            f"Rig ({self.get_rig_model().__name__}) Schema Version: {self.get_rig_model().model_construct().version}\n"
            f"Session ({self.get_session_model().__name__}) Schema Version: {self.get_session_model().model_construct().version}\n"
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
        try:
            if self.repository.is_dirty():
                logger.warning(
                    "Git repository is dirty. Discard changes before continuing unless you know what you are doing!"
                    "Uncommitted files: %s",
                    self.repository.uncommitted_changes(),
                )
                if not self.settings.allow_dirty:
                    self.repository.try_prompt_full_reset(self.ui_helper, force_reset=False)
                    if self.repository.is_dirty_with_submodules():
                        logger.critical(
                            "Dirty repository not allowed. Exiting. Consider running with --allow-dirty flag."
                        )
                        self._exit(-1)

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
            self.create_directory(self.settings.data_dir)
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

    def save_temp_model(self, model: pydantic.BaseModel, directory: Optional[os.PathLike]) -> str:
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

    @staticmethod
    def _resolve_model(model: TModel | Type[TModel]) -> tuple[Optional[TModel], Type[TModel]]:
        """
        Resolves the model and its type.

        Args:
            model: The model instance or model class to resolve

        Returns:
            tuple[TModel, Type[TModel]]: The resolved model instance and its type
        """
        if isinstance(model, type):
            return None, model
        else:
            return model, type(model)
