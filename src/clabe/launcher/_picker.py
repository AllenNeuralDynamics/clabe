from __future__ import annotations

import glob
import logging
import os
from pathlib import Path
from typing import Callable, List, Optional

import pydantic
from aind_behavior_services.utils import model_from_json_file
from typing_extensions import override

from clabe import launcher

from .. import ui
from ..services import ServiceSettings
from ..utils import ByAnimalFiles
from ..utils.aind_auth import validate_aind_username
from ._base import TRig, TSession, TTaskLogic

logger = logging.getLogger(__name__)


class DefaultBehaviorPickerSettings(ServiceSettings):
    """
    Settings for the default behavior picker.

    Attributes:
        config_library_dir: The directory where configuration files are stored.
    """

    config_library_dir: os.PathLike


class DefaultBehaviorPicker(ui.PickerBase[launcher.Launcher[TRig, TSession, TTaskLogic], TRig, TSession, TTaskLogic]):
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
        # Create settings for the picker
        settings = DefaultBehaviorPickerSettings(config_library_dir="config_dir")

        # Create a default behavior picker
        picker = DefaultBehaviorPicker(
            launcher=some_launcher_instance,
            settings=settings,
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
        *,
        settings: DefaultBehaviorPickerSettings,
        ui_helper: Optional[ui.DefaultUIHelper] = None,
        experimenter_validator: Optional[Callable[[str], bool]] = validate_aind_username,
        **kwargs,
    ):
        """
        Initializes the DefaultBehaviorPicker.

        Args:
            settings: Settings containing configuration including config_library_dir
            ui_helper: Helper for user interface interactions
            experimenter_validator: Function to validate the experimenter's username. If None, no validation is performed
            **kwargs: Additional keyword arguments
        """
        super().__init__(ui_helper=ui_helper, **kwargs)
        self._launcher: launcher.Launcher[TRig, TSession, TTaskLogic]
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
        if self._launcher is None:
            raise ValueError("Launcher is not initialized. Call initialize(launcher) first.")
        return Path(os.path.join(self.config_library_dir, self.RIG_SUFFIX, self._launcher.computer_name))

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

    def initialize(self, launcher: launcher.Launcher[TRig, TSession, TTaskLogic]) -> None:
        """
        Initializes the picker by creating required directories if needed.
        """
        self._launcher = launcher
        self._ui_helper = launcher.ui_helper
        if self._launcher.settings.create_directories:
            self._create_directories(launcher)

    def _create_directories(self, launcher: launcher.Launcher[TRig, TSession, TTaskLogic]) -> None:
        """
        Creates the required directories for configuration files.

        Creates the configuration library directory and all required subdirectories
        for storing rig, task logic, and subject configurations.
        """
        launcher.create_directory(self.config_library_dir)
        launcher.create_directory(self.task_logic_dir)
        launcher.create_directory(self.rig_dir)
        launcher.create_directory(self.subject_dir)

    def pick_rig(self, launcher: launcher.Launcher[TRig, TSession, TTaskLogic]) -> TRig:
        """
        Prompts the user to select a rig configuration file.

        Searches for available rig configuration files and either automatically
        selects a single file or prompts the user to choose from multiple options.

        Returns:
            TRig: The selected rig configuration.

        Raises:
            ValueError: If no rig configuration files are found or an invalid choice is made.
        """
        rig = launcher.get_rig()
        if rig is not None:
            logger.info("Rig already set in launcher. Using existing rig.")
            return rig
        available_rigs = glob.glob(os.path.join(self.rig_dir, "*.json"))
        if len(available_rigs) == 0:
            logger.error("No rig config files found.")
            raise ValueError("No rig config files found.")
        elif len(available_rigs) == 1:
            logger.info("Found a single rig config file. Using %s.", {available_rigs[0]})
            rig = model_from_json_file(available_rigs[0], launcher.get_rig_model())
            launcher.set_rig(rig)
            return rig
        else:
            while True:
                try:
                    path = self.ui_helper.prompt_pick_from_list(available_rigs, prompt="Choose a rig:")
                    if not isinstance(path, str):
                        raise ValueError("Invalid choice.")
                    rig = model_from_json_file(path, launcher.get_rig_model())
                    logger.info("Using %s.", path)
                    launcher.set_rig(rig)
                    return rig
                except pydantic.ValidationError as e:
                    logger.error("Failed to validate pydantic model. Try again. %s", e)
                except ValueError as e:
                    logger.info("Invalid choice. Try again. %s", e)

    def pick_session(self, launcher: launcher.Launcher[TRig, TSession, TTaskLogic]) -> TSession:
        """
        Prompts the user to select or create a session configuration.

        Collects experimenter information, subject selection, and session notes
        to create a new session configuration with appropriate metadata.

        Returns:
            TSession: The created or selected session configuration.
        """
        if (session := launcher.get_session()) is not None:
            logger.info("Session already set in launcher. Using existing session.")
            return session

        experimenter = self.prompt_experimenter(strict=True)
        if launcher.subject is not None:
            logger.info("Subject provided via CLABE: %s", launcher.subject)
            subject = launcher.subject
        else:
            subject = self.choose_subject(self.subject_dir)
            launcher.subject = subject

            if not (self.subject_dir / subject).exists():
                logger.info("Directory for subject %s does not exist. Creating a new one.", subject)
                os.makedirs(self.subject_dir / subject)

        notes = self.ui_helper.prompt_text("Enter notes: ")
        session = launcher.get_session_model()(
            experiment="",  # Will be set later
            root_path=str(Path(launcher.settings.data_dir).resolve() / subject),
            subject=subject,
            notes=notes,
            experimenter=experimenter if experimenter is not None else [],
            commit_hash=launcher.repository.head.commit.hexsha,
            allow_dirty_repo=launcher.settings.debug_mode or launcher.settings.allow_dirty,
            skip_hardware_validation=launcher.settings.skip_hardware_validation,
            experiment_version="",  # Will be set later
        )
        launcher.set_session(session)
        return session

    def pick_task_logic(self, launcher: launcher.Launcher[TRig, TSession, TTaskLogic]) -> TTaskLogic:
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
        if (task_logic := launcher.get_task_logic()) is not None:
            logger.info("Task logic already set in launcher. Using existing task logic.")
            launcher.set_task_logic(task_logic)
            return task_logic

        # Else, we check inside the subject folder for an existing task file
        try:
            if launcher.subject is None:
                logger.error("No subject set in launcher. Cannot load task logic.")
                raise ValueError("No subject set in launcher.")
            f = self.subject_dir / launcher.subject / (ByAnimalFiles.TASK_LOGIC.value + ".json")
            logger.info("Attempting to load task logic from subject folder: %s", f)
            task_logic = model_from_json_file(f, launcher.get_task_logic_model())
        except (ValueError, FileNotFoundError, pydantic.ValidationError) as e:
            logger.warning("Failed to find a valid task logic file. %s", e)
        else:
            logger.info("Found a valid task logic file in subject folder!")
            _is_manual = not self.ui_helper.prompt_yes_no_question("Would you like to use this task logic?")
            if not _is_manual:
                if task_logic is not None:
                    launcher.set_task_logic(task_logic)
                    return task_logic
                else:
                    logger.error("No valid task logic file found in subject folder.")
                    raise ValueError("No valid task logic file found.")
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
                task_logic = model_from_json_file(path, launcher.get_task_logic_model())
                logger.info("User entered: %s.", path)
            except pydantic.ValidationError as e:
                logger.error("Failed to validate pydantic model. Try again. %s", e)
            except (ValueError, FileNotFoundError) as e:
                logger.info("Invalid choice. Try again. %s", e)
        if task_logic is None:
            logger.error("No task logic file found.")
            raise ValueError("No task logic file found.")

        launcher.get_session(strict=True).experiment = task_logic.name
        launcher.get_session(strict=True).experiment_version = task_logic.version
        launcher.set_task_logic(task_logic)
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
