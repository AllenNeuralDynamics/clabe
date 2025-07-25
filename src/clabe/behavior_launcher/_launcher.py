from __future__ import annotations

import enum
import glob
import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, List, Optional, Self, Union

import pydantic
from aind_behavior_services.utils import model_from_json_file
from typing_extensions import override

from .. import logging_helper, ui
from ..launcher._base import BaseLauncher, TRig, TSession, TTaskLogic
from ..services import ServiceSettings
from ._aind_auth import validate_aind_username
from ._cli import BehaviorCliArgs
from ._model_modifiers import BySubjectModifierManager

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from clabe.behavior_launcher._services import BehaviorServicesFactoryManager
else:
    BehaviorServicesFactoryManager = Any


class BehaviorLauncher(BaseLauncher[TRig, TSession, TTaskLogic]):
    """
    A launcher for behavior experiments that manages services, experiment configuration, and
    execution hooks.

    This class extends the BaseLauncher to provide specific functionality for behavior experiments,
    including service management, configuration handling, and experiment lifecycle hooks.

    Attributes:
        settings (BehaviorCliArgs): CLI arguments and configuration settings
        services_factory_manager (BehaviorServicesFactoryManager): Manager for experiment services
        _by_subject_modifiers_manager (BySubjectModifierManager): Manager for subject-specific modifications

    Example:
        ```python
        # Create a behavior launcher
        launcher = BehaviorLauncher(
            settings=BehaviorCliArgs(...),
            rig_schema_model=RigModelType,
            session_schema_model=SessionModelType,
            task_logic_schema_model=TaskLogicModelType,
            picker=DefaultBehaviorPicker,
        )
        # Run the experiment
        launcher.run()
        ```
    """

    settings: BehaviorCliArgs
    services_factory_manager: BehaviorServicesFactoryManager
    _by_subject_modifiers_manager: BySubjectModifierManager[TRig, TSession, TTaskLogic]

    def __init__(  # pylint: disable=useless-parent-delegation
        self,
        *,
        settings: BehaviorCliArgs,
        rig_schema_model,
        session_schema_model,
        task_logic_schema_model,
        picker,
        services=None,
        attached_logger=None,
        by_subject_modifiers_manager: Optional[BySubjectModifierManager[TRig, TSession, TTaskLogic]] = None,
        **kwargs,
    ):
        """
        Initialize the BehaviorLauncher.

        Args:
            settings (BehaviorCliArgs): CLI arguments and configuration settings
            rig_schema_model: Model class for rig configuration
            session_schema_model: Model class for session configuration
            task_logic_schema_model: Model class for task logic configuration
            picker: Configuration picker instance
            services: Optional services configuration
            attached_logger: Optional logger instance
            by_subject_modifiers_manager: Optional manager for subject-specific modifications
            **kwargs: Additional keyword arguments passed to parent
        """
        super().__init__(
            settings=settings,
            rig_schema_model=rig_schema_model,
            session_schema_model=session_schema_model,
            task_logic_schema_model=task_logic_schema_model,
            picker=picker,
            services=services,
            attached_logger=attached_logger,
            **kwargs,
        )
        self._by_subject_modifiers_manager = (
            by_subject_modifiers_manager or BySubjectModifierManager[TRig, TSession, TTaskLogic]()
        )

    @property
    def by_subject_modifiers_manager(self) -> BySubjectModifierManager[TRig, TSession, TTaskLogic]:
        """
        Returns the manager for by-subject modifiers.

        Returns:
            BySubjectModifierManager: The by-subject modifiers manager.
        """
        return self._by_subject_modifiers_manager

    @override
    def _pre_run_hook(self, *args, **kwargs) -> Self:
        """
        Hook executed before the main run logic.

        Performs initialization validation, sets experiment metadata, and applies
        subject-specific modifications to schemas.

        Args:
            *args: Variable length argument list
            **kwargs: Arbitrary keyword arguments

        Returns:
            Self: The current instance for method chaining.
        """
        logger.info("Pre-run hook started.")

        if self.settings.validate_init:
            logger.debug("Validating initialization.")
            if self.services_factory_manager.resource_monitor is not None:
                logger.debug("Evaluating resource monitor constraints.")
                if not self.services_factory_manager.resource_monitor.evaluate_constraints():
                    logger.critical("Resource monitor constraints failed.")
                    self._exit(-1)

        self.session_schema.experiment = self.task_logic_schema.name
        self.session_schema.experiment_version = self.task_logic_schema.version
        self.by_subject_modifiers_manager.apply_modifiers(
            rig_schema=self.rig_schema, session_schema=self.session_schema, task_logic_schema=self.task_logic_schema
        )
        return self

    @override
    def _run_hook(self, *args, **kwargs) -> Self:
        """
        Hook executed during the main run logic.

        Configures and runs the main experiment application, handling any
        execution errors that may occur.

        Args:
            *args: Variable length argument list
            **kwargs: Arbitrary keyword arguments

        Returns:
            Self: The current instance for method chaining.

        Raises:
            ValueError: If required schema instances are not set
        """
        logger.info("Running hook started.")
        if self._session_schema is None:
            raise ValueError("Session schema instance not set.")
        if self._task_logic_schema is None:
            raise ValueError("Task logic schema instance not set.")
        if self._rig_schema is None:
            raise ValueError("Rig schema instance not set.")

        self.services_factory_manager.app.add_app_settings(launcher=self)

        try:
            self.services_factory_manager.app.run()
            _ = self.services_factory_manager.app.output_from_result(allow_stderr=True)
        except subprocess.CalledProcessError as e:
            logger.critical("Bonsai app failed to run. %s", e)
            self._exit(-1)
        return self

    @override
    def _post_run_hook(self, *args, **kwargs) -> Self:
        """
        Hook executed after the main run logic.

        Handles experiment cleanup including finalizing the picker, data mapping,
        log management, and data transfer.

        Args:
            *args: Variable length argument list
            **kwargs: Arbitrary keyword arguments

        Returns:
            Self: The current instance for method chaining.
        """
        logger.info("Post-run hook started.")

        try:
            self.picker.finalize()
            logger.info("Picker finalized successfully.")
        except Exception as e:
            logger.error("Picker finalized with errors: %s", e)

        if (not self.settings.skip_data_mapping) and (self.services_factory_manager.data_mapper is not None):
            try:
                self.services_factory_manager.data_mapper.map()
                logger.info("Mapping successful.")
            except Exception as e:
                logger.error("Data mapper service has failed: %s", e)

        logging_helper.close_file_handlers(logger)

        try:
            self._copy_tmp_directory(self.session_directory / "Behavior" / "Logs")
        except ValueError:
            logger.error("Failed to copy temporary logs directory to session directory.")

        if (not self.settings.skip_data_transfer) and (self.services_factory_manager.data_transfer is not None):
            try:
                if not self.services_factory_manager.data_transfer.validate():
                    raise ValueError("Data transfer service failed validation.")
                self.services_factory_manager.data_transfer.transfer()
            except Exception as e:
                logger.error("Data transfer service has failed: %s", e)

        return self

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


_BehaviorPickerAlias = ui.PickerBase[BehaviorLauncher[TRig, TSession, TTaskLogic], TRig, TSession, TTaskLogic]


class ByAnimalFiles(enum.StrEnum):
    """
    Enum for file types associated with animals in the experiment.

    Defines the standard file types that can be associated with individual
    animals/subjects in behavior experiments.

    Example:
        ```python
        # Use the task logic file type
        filename = f"{ByAnimalFiles.TASK_LOGIC}.json"
        ```
    """

    TASK_LOGIC = "task_logic"


class DefaultBehaviorPickerSettings(ServiceSettings):
    config_library_dir: os.PathLike


class DefaultBehaviorPicker(_BehaviorPickerAlias[TRig, TSession, TTaskLogic]):
    """
    A picker class for selecting rig, session, and task logic configurations for behavior experiments.

    This class provides methods to initialize directories, pick configurations, and prompt user inputs
    for various components of the experiment setup. It manages the configuration library structure
    and user interactions for selecting experiment parameters.

    Attributes:
        RIG_SUFFIX (str): Directory suffix for rig configurations
        SUBJECT_SUFFIX (str): Directory suffix for subject configurations
        TASK_LOGIC_SUFFIX (str): Directory suffix for task logic configurations

    Example:
        ```python
        # Create a default behavior picker
        picker = DefaultBehaviorPicker(
            launcher=some_launcher_instance,
            config_library_dir="config_dir",
        )
        # Initialize and pick configurations
        picker.initialize()
        rig = picker.pick_rig()
        session = picker.pick_session()
        task_logic = picker.pick_task_logic()
        ```
    """

    RIG_SUFFIX: str = "Rig"
    SUBJECT_SUFFIX: str = "Subjects"
    TASK_LOGIC_SUFFIX: str = "TaskLogic"

    @override
    def __init__(
        self,
        launcher: Optional[BehaviorLauncher[TRig, TSession, TTaskLogic]] = None,
        *,
        settings: DefaultBehaviorPickerSettings,
        ui_helper: Optional[ui.DefaultUIHelper] = None,
        experimenter_validator: Optional[Callable[[str], bool]] = validate_aind_username,
        **kwargs,
    ):
        """
        Initializes the DefaultBehaviorPicker.

        Args:
            launcher: The launcher instance associated with the picker
            ui_helper: Helper for user interface interactions
            config_library_dir: Path to the configuration library directory
            experimenter_validator: Function to validate the experimenter's username. If None, no validation is performed
            **kwargs: Additional keyword arguments
        """
        super().__init__(launcher, ui_helper=ui_helper, **kwargs)
        self._settings = settings
        self._experimenter_validator = experimenter_validator

    @property
    def config_library_dir(self) -> Path:
        """
        Returns the path to the configuration library directory.

        Returns:
            Path: The configuration library directory.
        """
        return Path(self._settings.config_library_dir)

    @property
    def rig_dir(self) -> Path:
        """
        Returns the path to the rig configuration directory.

        Returns:
            Path: The rig configuration directory.
        """
        return Path(os.path.join(self.config_library_dir, self.RIG_SUFFIX, self.launcher.computer_name))

    @property
    def subject_dir(self) -> Path:
        """
        Returns the path to the subject configuration directory.

        Returns:
            Path: The subject configuration directory.
        """
        return Path(os.path.join(self.config_library_dir, self.SUBJECT_SUFFIX))

    @property
    def task_logic_dir(self) -> Path:
        """
        Returns the path to the task logic configuration directory.

        Returns:
            Path: The task logic configuration directory.
        """
        return Path(os.path.join(self.config_library_dir, self.TASK_LOGIC_SUFFIX))

    @override
    def initialize(self) -> None:
        """
        Initializes the picker by creating required directories if needed.
        """
        if self.launcher.settings.create_directories:
            self._create_directories()

    def _create_directories(self) -> None:
        """
        Creates the required directories for configuration files.

        Creates the configuration library directory and all required subdirectories
        for storing rig, task logic, and subject configurations.
        """
        self.launcher.create_directory(self.config_library_dir)
        self.launcher.create_directory(self.task_logic_dir)
        self.launcher.create_directory(self.rig_dir)
        self.launcher.create_directory(self.subject_dir)

    def pick_rig(self) -> TRig:
        """
        Prompts the user to select a rig configuration file.

        Searches for available rig configuration files and either automatically
        selects a single file or prompts the user to choose from multiple options.

        Returns:
            TRig: The selected rig configuration.

        Raises:
            ValueError: If no rig configuration files are found or an invalid choice is made.
        """
        available_rigs = glob.glob(os.path.join(self.rig_dir, "*.json"))
        if len(available_rigs) == 0:
            logger.error("No rig config files found.")
            raise ValueError("No rig config files found.")
        elif len(available_rigs) == 1:
            logger.info("Found a single rig config file. Using %s.", {available_rigs[0]})
            return model_from_json_file(available_rigs[0], self.launcher.rig_schema_model)
        else:
            while True:
                try:
                    path = self.ui_helper.prompt_pick_from_list(available_rigs, prompt="Choose a rig:")
                    if not isinstance(path, str):
                        raise ValueError("Invalid choice.")
                    rig = model_from_json_file(path, self.launcher.rig_schema_model)
                    logger.info("Using %s.", path)
                    return rig
                except pydantic.ValidationError as e:
                    logger.error("Failed to validate pydantic model. Try again. %s", e)
                except ValueError as e:
                    logger.info("Invalid choice. Try again. %s", e)

    def pick_session(self) -> TSession:
        """
        Prompts the user to select or create a session configuration.

        Collects experimenter information, subject selection, and session notes
        to create a new session configuration with appropriate metadata.

        Returns:
            TSession: The created or selected session configuration.
        """
        experimenter = self.prompt_experimenter(strict=True)
        if self.launcher.subject is not None:
            logger.info("Subject provided via CLABE: %s", self.launcher.settings.subject)
            subject = self.launcher.subject
        else:
            subject = self.choose_subject(self.subject_dir)
            self.launcher.subject = subject
            if not (self.subject_dir / subject).exists():
                logger.info("Directory for subject %s does not exist. Creating a new one.", subject)
                os.makedirs(self.subject_dir / subject)

        notes = self.ui_helper.prompt_text("Enter notes: ")

        return self.launcher.session_schema_model(
            experiment="",  # Will be set later
            root_path=str(self.launcher.data_dir.resolve())
            if not self.launcher.group_by_subject_log
            else str(self.launcher.data_dir.resolve() / subject),
            subject=subject,
            notes=notes,
            experimenter=experimenter if experimenter is not None else [],
            commit_hash=self.launcher.repository.head.commit.hexsha,
            allow_dirty_repo=self.launcher.is_debug_mode or self.launcher.allow_dirty,
            skip_hardware_validation=self.launcher.skip_hardware_validation,
            experiment_version="",  # Will be set later
        )

    def pick_task_logic(self) -> TTaskLogic:
        """
        Prompts the user to select or create a task logic configuration.

        Attempts to load task logic in the following order:
        1. From CLI if already set
        2. From subject-specific folder
        3. From user selection in task logic library

        Returns:
            TTaskLogic: The created or selected task logic configuration.

        Raises:
            ValueError: If no valid task logic file is found.
        """
        task_logic: Optional[TTaskLogic]
        try:  # If the task logic is already set (e.g. from CLI), skip the prompt
            task_logic = self.launcher.task_logic_schema
            assert task_logic is not None
            return task_logic
        except ValueError:
            task_logic = None

        # Else, we check inside the subject folder for an existing task file
        try:
            f = self.subject_dir / self.launcher.session_schema.subject / (ByAnimalFiles.TASK_LOGIC.value + ".json")
            logger.info("Attempting to load task logic from subject folder: %s", f)
            task_logic = model_from_json_file(f, self.launcher.task_logic_schema_model)
        except (ValueError, FileNotFoundError, pydantic.ValidationError) as e:
            logger.warning("Failed to find a valid task logic file. %s", e)
        else:
            logger.info("Found a valid task logic file in subject folder!")
            _is_manual = not self.ui_helper.prompt_yes_no_question("Would you like to use this task logic?")
            if not _is_manual:
                return task_logic
            else:
                task_logic = None

        # If not found, we prompt the user to choose/enter a task logic file
        while task_logic is None:
            try:
                _path = Path(os.path.join(self.config_library_dir, self.task_logic_dir))
                available_files = glob.glob(os.path.join(_path, "*.json"))
                if len(available_files) == 0:
                    break
                path = self.ui_helper.prompt_pick_from_list(available_files, prompt="Choose a task logic:")
                if not isinstance(path, str):
                    raise ValueError("Invalid choice.")
                if not os.path.isfile(path):
                    raise FileNotFoundError(f"File not found: {path}")
                task_logic = model_from_json_file(path, self.launcher.task_logic_schema_model)
                logger.info("User entered: %s.", path)
            except pydantic.ValidationError as e:
                logger.error("Failed to validate pydantic model. Try again. %s", e)
            except (ValueError, FileNotFoundError) as e:
                logger.info("Invalid choice. Try again. %s", e)
        if task_logic is None:
            logger.error("No task logic file found.")
            raise ValueError("No task logic file found.")
        return task_logic

    def choose_subject(self, directory: str | os.PathLike) -> str:
        """
        Prompts the user to select or manually enter a subject name.

        Allows the user to either type a new subject name or select from
        existing subject directories.

        Args:
            directory: Path to the directory containing subject folders

        Returns:
            str: The selected or entered subject name.

        Example:
            ```python
            # Choose a subject from the subjects directory
            subject = picker.choose_subject("Subjects")
            ```
        """
        subject = None
        while subject is None:
            subject = self.ui_helper.input("Enter subject name: ")
            if subject == "":
                subject = self.ui_helper.prompt_pick_from_list(
                    [
                        os.path.basename(folder)
                        for folder in os.listdir(directory)
                        if os.path.isdir(os.path.join(directory, folder))
                    ],
                    prompt="Choose a subject:",
                    allow_0_as_none=True,
                )
            else:
                return subject

        return subject

    def prompt_experimenter(self, strict: bool = True) -> Optional[List[str]]:
        """
        Prompts the user to enter the experimenter's name(s).

        Accepts multiple experimenter names separated by commas or spaces.
        Validates names using the configured validator function if provided.

        Args:
            strict: Whether to enforce non-empty input

        Returns:
            Optional[List[str]]: List of experimenter names.

        Example:
            ```python
            # Prompt for experimenter with validation
            names = picker.prompt_experimenter(strict=True)
            print("Experimenters:", names)
            ```
        """
        experimenter: Optional[List[str]] = None
        while experimenter is None:
            _user_input = self.ui_helper.prompt_text("Experimenter name: ")
            experimenter = _user_input.replace(",", " ").split()
            if strict & (len(experimenter) == 0):
                logger.info("Experimenter name is not valid. Try again.")
                experimenter = None
            else:
                if self._experimenter_validator:
                    for name in experimenter:
                        if not self._experimenter_validator(name):
                            logger.warning("Experimenter name: %s, is not valid. Try again", name)
                            experimenter = None
                            break
        return experimenter

    def finalize(self) -> None:
        """
        Finalizes the picker operations.

        Currently a no-op but can be extended for cleanup operations.
        """
        return
