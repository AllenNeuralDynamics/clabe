from __future__ import annotations

import logging
import os
import shutil
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Generic, Optional, Self, Type, TypeVar

import pydantic
from aind_behavior_services import (
    AindBehaviorRigModel,
    AindBehaviorSessionModel,
    AindBehaviorTaskLogicModel,
)
from aind_behavior_services.utils import format_datetime, model_from_json_file, utcnow

from .. import __version__, logging_helper, ui
from ..git_manager import GitRepository
from ..services import ServicesFactoryManager
from .cli import BaseCliArgs

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
        settings: BaseCliArgs,
        rig_schema_model: Type[TRig],
        session_schema_model: Type[TSession],
        task_logic_schema_model: Type[TTaskLogic],
        picker: ui.PickerBase[Self, TRig, TSession, TTaskLogic],
        services: Optional[ServicesFactoryManager] = None,
        attached_logger: Optional[logging.Logger] = None,
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

        self.temp_dir = self.abspath(settings.temp_dir) / format_datetime(utcnow())
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

        # Solve services and git repository
        self._bind_launcher_services(services)

        repository_dir = Path(self.settings.repository_dir) if self.settings.repository_dir is not None else None
        self.repository = GitRepository() if repository_dir is None else GitRepository(path=repository_dir)
        self._cwd = self.repository.working_dir
        os.chdir(self._cwd)

        # Schemas
        self._rig_schema_model = rig_schema_model
        self._session_schema_model = session_schema_model
        self._task_logic_schema_model = task_logic_schema_model

        # Schema instances
        self._rig_schema: Optional[TRig] = None
        self._session_schema: Optional[TSession] = None
        self._task_logic_schema: Optional[TTaskLogic] = None
        self._solve_schema_instances(
            rig_path_path=self.settings.rig_path,
            session_path=self.settings.session_path,
            task_logic_path=self.settings.task_logic_path,
        )
        self._subject: Optional[str] = self.settings.subject

        self._register_picker(picker)
        self.picker.initialize()

        if self.settings.create_directories is True:
            self._create_directory_structure()

    @property
    def is_validate_init(self) -> bool:
        """
        Returns whether initialization validation is enabled.

        Returns:
            bool: True if initialization validation is enabled
        """
        return self.settings.validate_init

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
    def group_by_subject_log(self) -> bool:
        """
        Returns whether data logging should be grouped by subject.

        Returns:
            bool: True if data logging should be grouped by subject
        """
        return self.settings.group_by_subject_log

    def _register_picker(self, picker: ui.PickerBase[Self, TRig, TSession, TTaskLogic]) -> None:
        """
        Registers a picker for selecting schemas.

        Associates a picker instance with the launcher and ensures it has
        the necessary UI helper for user interactions.

        Args:
            picker: The picker instance to register

        Raises:
            ValueError: If the picker already has a launcher registered
        """
        if picker.has_launcher:
            raise ValueError("Picker already has a launcher registered.")
        picker.register_launcher(self)

        if not picker.has_ui_helper:
            picker.register_ui_helper(ui.DefaultUIHelper())

        self._picker = picker

        return

    @property
    def subject(self) -> Optional[str]:
        """
        Returns the current subject name.

        Returns:
            Optional[str]: The subject name or None if not set
        """
        return self._subject

    @subject.setter
    def subject(self, value: str) -> None:
        """
        Sets the subject name.

        Args:
            value: The subject name to set

        Raises:
            ValueError: If subject is already set
        """
        if self._subject is not None:
            raise ValueError("Subject already set.")
        self._subject = value

    @property
    def settings(self) -> BaseCliArgs:
        """
        Returns the launcher settings.

        Returns:
            BaseCliArgs: The launcher settings
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

    @property
    def services_factory_manager(self) -> ServicesFactoryManager:
        """
        Returns the services factory manager.

        Returns:
            ServicesFactoryManager: The services factory manager

        Raises:
            ValueError: If services instance is not set
        """
        if self._services_factory_manager is None:
            raise ValueError("Services instance not set.")
        return self._services_factory_manager

    @property
    def picker(self):
        """
        Returns the picker instance.

        Returns:
            The picker instance used for schema selection
        """
        return self._picker

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

            if self.is_validate_init:
                self.validate()

            self._ui_prompt()
            self._run_hooks()
            self.dispose()
        except KeyboardInterrupt:
            logger.critical("User interrupted the process.")
            self._exit(-1)
            return

    def _ui_prompt(self) -> Self:
        """
        Prompts for user input to select schemas if not already set.

        Uses the picker to collect session, task logic, and rig schemas
        from user input if they haven't been pre-configured.

        Returns:
            Self: The launcher instance for method chaining
        """
        if self._session_schema is None:
            self._session_schema = self.picker.pick_session()
        if self._task_logic_schema is None:
            self._task_logic_schema = self.picker.pick_task_logic()
        if self._rig_schema is None:
            self._rig_schema = self.picker.pick_rig()
        return self

    def _run_hooks(self) -> Self:
        """
        Executes the launcher lifecycle hooks in sequence.

        Runs the pre-run, run, and post-run hooks in order, logging
        completion of each phase.

        Returns:
            Self: The launcher instance for method chaining
        """
        self._pre_run_hook()
        logger.info("Pre-run hook completed.")
        self._run_hook()
        logger.info("Run hook completed.")
        self._post_run_hook()
        logger.info("Post-run hook completed.")
        return self

    @abstractmethod
    def _pre_run_hook(self, *args, **kwargs) -> Self:
        """
        Abstract method for pre-run logic. Must be implemented by subclasses.

        This hook is executed before the main run logic and should contain
        any preparation or validation logic needed before experiment execution.

        Returns:
            Self: The current instance for method chaining
        """
        raise NotImplementedError("Method not implemented.")

    @abstractmethod
    def _run_hook(self, *args, **kwargs) -> Self:
        """
        Abstract method for main run logic. Must be implemented by subclasses.

        This hook contains the core experiment execution logic and should
        handle the primary workflow of the launcher.

        Returns:
            Self: The current instance for method chaining
        """
        raise NotImplementedError("Method not implemented.")

    @abstractmethod
    def _post_run_hook(self, *args, **kwargs) -> Self:
        """
        Abstract method for post-run logic. Must be implemented by subclasses.

        This hook is executed after the main run logic and should handle
        cleanup, data processing, and finalization tasks.

        Returns:
            Self: The current instance for method chaining
        """
        raise NotImplementedError("Method not implemented.")

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
            self.picker.ui_helper.input("Press any key to exit...")
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
                    self.repository.try_prompt_full_reset(self.picker.ui_helper, force_reset=False)
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

    @classmethod
    def abspath(cls, path: os.PathLike) -> Path:
        """
        Helper method that converts a path to an absolute path.

        Args:
            path: The path to convert

        Returns:
            Path: The absolute path
        """
        return Path(path).resolve()

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
        if not os.path.exists(cls.abspath(directory)):
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

    def _bind_launcher_services(
        self, services_factory_manager: Optional[ServicesFactoryManager]
    ) -> Optional[ServicesFactoryManager]:
        """
        Binds a ServicesFactoryManager instance to the launcher.

        Associates a services factory manager with the launcher and registers
        the launcher with the services manager for bidirectional reference.

        Args:
            services_factory_manager: The services factory manager to bind

        Returns:
            Optional[ServicesFactoryManager]: The bound services factory manager
        """
        self._services_factory_manager = services_factory_manager
        if self._services_factory_manager is not None:
            self._services_factory_manager.register_launcher(self)
        return self._services_factory_manager

    def _solve_schema_instances(
        self,
        rig_path_path: Optional[os.PathLike] = None,
        session_path: Optional[os.PathLike] = None,
        task_logic_path: Optional[os.PathLike] = None,
    ) -> None:
        """
        Resolves and loads schema instances for the rig and task logic.

        Loads schema definitions from JSON files and assigns them to the
        corresponding attributes if file paths are provided.

        Args:
            rig_path_path: Path to the JSON file containing the rig schema
            session_path: Path to the JSON file containing the session schema
            task_logic_path: Path to the JSON file containing the task logic schema
        """
        if rig_path_path is not None:
            logger.info("Loading rig schema from %s", self.settings.rig_path)
            self._rig_schema = model_from_json_file(rig_path_path, self.rig_schema_model)
        if session_path is not None:
            logger.info("Loading session schema from %s", self.settings.session_path)
            self._session_schema = model_from_json_file(session_path, self.session_schema_model)
        if task_logic_path is not None:
            logger.info("Loading task logic schema from %s", self._settings.task_logic_path)
            self._task_logic_schema = model_from_json_file(task_logic_path, self.task_logic_schema_model)
