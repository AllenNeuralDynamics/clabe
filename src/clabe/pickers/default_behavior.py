import glob
import logging
import os
from pathlib import Path
from typing import Callable, ClassVar, List, Optional, Type, TypeVar, Union

import pydantic
from aind_behavior_curriculum import TrainerState
from aind_behavior_services import Rig, Session, Task
from aind_behavior_services.utils import model_from_json_file

from .. import ui
from .._typing import TRig, TSession, TTask
from ..cache_manager import CacheManager
from ..constants import ByAnimalFiles
from ..launcher import Launcher
from ..services import ServiceSettings
from ..utils.aind_validators import validate_rig_computer_name, validate_username

logger = logging.getLogger(__name__)
T = TypeVar("T")
TInjectable = TypeVar("TInjectable")


class DefaultBehaviorPickerSettings(ServiceSettings):
    """
    Settings for the default behavior picker.

    Attributes:
        config_library_dir: The directory where configuration files are stored.
    """

    __yml_section__: ClassVar[Optional[str]] = "default_behavior_picker"

    config_library_dir: os.PathLike


class DefaultBehaviorPicker:
    """
    A picker class for selecting rig, session, and task configurations.

    Provides methods to initialize directories, pick configurations, and prompt user
    inputs for various components of the experiment setup. Manages the configuration
    library structure and user interactions for selecting experiment parameters.

    Properties:
        frontend: Frontend mediating user interface interactions
        trainer_state: The current trainer state
        config_library_dir: Path to the configuration library directory
        rig_dir: Path to the rig configurations directory
        subject_dir: Path to the subject configurations directory
        task_dir: Path to the task configurations directory

    Methods:
        pick_rig: Picks the rig configuration
        pick_session: Picks the session configuration
        pick_task: Picks the task configuration
        pick_trainer_state: Picks the trainer state configuration
        choose_subject: Allows the user to choose a subject
        prompt_experimenter: Prompts for experimenter information
        dump_model: Saves a Pydantic model to a file
    """

    RIG_SUFFIX: str = "Rig"
    SUBJECT_SUFFIX: str = "Subjects"
    TASK_SUFFIX: str = "Task"

    def __init__(
        self,
        settings: DefaultBehaviorPickerSettings,
        launcher: Launcher,
        frontend: Optional[ui.Frontend] = None,
        experimenter_validator: Optional[Callable[[str], bool]] = validate_username,
        rig_validator: Optional[Callable[[Rig], Rig]] = validate_rig_computer_name,
        use_cache: bool = True,
    ):
        """
        Initializes the DefaultBehaviorPicker.

        Args:
            settings: Settings containing configuration including config_library_dir. By default, attempts to rely on DefaultBehaviorPickerSettings to automatic loading from yaml files
            launcher: The launcher instance for managing experiment execution
            frontend: Frontend mediating user interaction. If None, uses the launcher's frontend. Defaults to None
            experimenter_validator: Function to validate the experimenter's username. If None, no validation is performed. Defaults to validate_username
            rig_validator: Function to validate the rig configuration. If None, no validation is performed. Defaults to validate_rig_computer_name
            use_cache: Whether to use caching for selections. Defaults to True
        """
        self._launcher = launcher
        self._frontend = launcher.frontend if frontend is None else frontend
        self._settings = settings
        self._ensure_directories()
        self._experimenter_validator = experimenter_validator
        self._rig_validator = rig_validator
        self._trainer_state: Optional[TrainerState] = None
        self._session: Optional[Session] = None
        self._cache_manager = CacheManager.get_instance()
        self._use_cache = use_cache

    @property
    def frontend(self) -> ui.Frontend:
        """
        Retrieves the registered frontend.

        Returns:
            Frontend: The registered frontend

        Raises:
            ValueError: If no frontend is registered
        """
        if self._frontend is None:
            raise ValueError("Frontend is not registered")
        return self._frontend

    @property
    def trainer_state(self) -> TrainerState:
        """
        Returns the current trainer state.

        Returns:
            TrainerState: The current trainer state

        Raises:
            ValueError: If the trainer state is not set
        """
        if self._trainer_state is None:
            raise ValueError("Trainer state not set.")
        return self._trainer_state

    @property
    def session_directory(self) -> Path:
        """Returns the directory path for the current session."""
        return self._launcher.session_directory

    @property
    def session(self) -> Session:
        """Returns the current session model."""
        return self._launcher.session

    @property
    def config_library_dir(self) -> Path:
        """
        Returns the path to the configuration library directory.

        Returns:
            Path: The configuration library directory
        """
        return Path(self._settings.config_library_dir)

    @property
    def rig_dir(self) -> Path:
        """
        Returns the path to the rig configuration directory.

        Returns:
            Path: The rig configuration directory
        """
        return Path(os.path.join(self.config_library_dir, self.RIG_SUFFIX, self._launcher.computer_name))

    @property
    def subject_dir(self) -> Path:
        """
        Returns the path to the subject configuration directory.

        Returns:
            Path: The subject configuration directory
        """
        return Path(os.path.join(self.config_library_dir, self.SUBJECT_SUFFIX))

    @property
    def task_dir(self) -> Path:
        """
        Returns the path to the task configuration directory.

        Returns:
            Path: The task configuration directory
        """
        return Path(os.path.join(self.config_library_dir, self.TASK_SUFFIX))

    def _ensure_directories(self) -> None:
        """
        Ensures the required directories for configuration files exist.

        Creates the configuration library directory and all required subdirectories
        for storing rig, task, and subject configurations.
        """
        self._launcher.create_directory(self.config_library_dir)
        self._launcher.create_directory(self.task_dir)
        self._launcher.create_directory(self.rig_dir)
        self._launcher.create_directory(self.subject_dir)

    def pick_rig(self, model: Type[TRig]) -> TRig:
        """
        Prompts the user to select a rig configuration file.

        Searches for available rig configuration files and either automatically
        selects a single file or prompts the user to choose from multiple options.

        Args:
            model: The rig model type to validate against

        Returns:
            TRig: The selected rig configuration

        Raises:
            ValueError: If no rig configuration files are found or an invalid choice is made
        """
        rig: TRig | None = None
        rig_path: str | None = None

        # Check cache for previously used rigs
        if self._use_cache:
            cache = self._cache_manager.try_get_cache(model.__name__)
        else:
            cache = None

        if cache:
            cache.sort()
            rig_path = self.frontend.prompt_pick(
                ui.PickRequest(
                    label=f"Choose a rig for {model.__name__}:",
                    options=cache,
                    allow_none=True,
                    none_label="Select from library",
                    field="rig",
                )
            )
            if rig_path is not None:
                rig = self._load_rig_from_path(Path(rig_path), model)

        # Prompt user to select a rig if not already selected
        while rig_path is None:
            available_rigs = glob.glob(os.path.join(self.rig_dir, "*.json"))
            # We raise if no rigs are found to prevent an infinite loop
            if len(available_rigs) == 0:
                self.frontend.notify("No rig config files found.", ui.MessageLevel.ERROR)
                raise ValueError("No rig config files found.")
            # Use the single available rig config file
            elif len(available_rigs) == 1:
                self.frontend.notify(f"Found a single rig config file. Using {available_rigs[0]}.")
                rig_path = available_rigs[0]
                rig = model_from_json_file(rig_path, model)
            else:
                rig_path = self.frontend.prompt_pick(
                    ui.PickRequest(
                        label=f"Choose a rig for {model.__name__}:", options=available_rigs, field="rig"
                    )
                )
                if rig_path is not None:
                    rig = self._load_rig_from_path(Path(rig_path), model)
        assert rig_path is not None
        assert rig is not None
        if self._rig_validator:
            rig = self._rig_validator(rig)
        # Add the selected rig path to the cache
        self._cache_manager.add_to_cache("rigs", rig_path)
        return rig

    @staticmethod
    def _load_rig_from_path(path: Path, model: Type[TRig]) -> TRig | None:
        """Load a rig configuration from a given path."""
        try:
            rig = model_from_json_file(path, model)
            logger.info("Using %s.", path)
            return rig
        except pydantic.ValidationError as e:
            logger.error("Failed to validate pydantic model. Try again. %s", e)
        except ValueError as e:
            logger.info("Invalid choice. Try again. %s", e)
        return None

    def pick_session(self, model: Type[TSession] = Session) -> TSession:
        """
        Prompts the user to select or create a session configuration.

        Collects experimenter information, subject selection, and session notes
        to create a new session configuration with appropriate metadata.

        Args:
            model: The session model type to instantiate. Defaults to Session

        Returns:
            TSession: The created or selected session configuration
        """

        experimenter = self.prompt_experimenter(strict=True)
        subject = self.choose_subject(self.subject_dir)

        if not (self.subject_dir / subject).exists():
            logger.info("Directory for subject %s does not exist. Creating a new one.", subject)
            os.makedirs(self.subject_dir / subject)

        notes = self.frontend.prompt_text(ui.TextRequest(label="Enter notes", field="notes"))
        session = model(
            subject=subject,
            notes=notes,
            experimenter=experimenter if experimenter is not None else [],
            commit_hash=self._launcher.repository.head.commit.hexsha,
            allow_dirty_repo=self._launcher.settings.debug_mode or self._launcher.settings.allow_dirty,
            skip_hardware_validation=self._launcher.settings.skip_hardware_validation,
        )
        self._session = session
        return session

    def pick_task(self, model: Type[TTask]) -> TTask:
        """
        Prompts the user to select or create a task configuration.

        Attempts to load task in the following order:
        1. From CLI if already set
        2. From subject-specific folder
        3. From user selection in task library

        Args:
            model: The task model type to validate against

        Returns:
            TTask: The created or selected task configuration

        Raises:
            ValueError: If no valid task file is found
        """
        task: Optional[TTask] = None
        if self._session is None:
            raise ValueError("Session must be picked (pick_session) before picking task.")

        try:
            f = self.subject_dir / self._session.subject / (ByAnimalFiles.TASK.value + ".json")
            logger.info("Attempting to load task from subject folder: %s", f)
            task = model_from_json_file(f, model)
        except (ValueError, FileNotFoundError, pydantic.ValidationError) as e:
            logger.warning("Failed to find a valid task file. %s", e)
        else:
            self.frontend.notify("Found a valid task file in subject folder.", ui.MessageLevel.SUCCESS)
            _is_manual = not self.frontend.prompt_confirm(ui.ConfirmRequest(label="Would you like to use this task?"))
            if not _is_manual:
                if task is not None:
                    return task
                else:
                    self.frontend.notify("No valid task file found in subject folder.", ui.MessageLevel.ERROR)
                    raise ValueError("No valid task file found.")
            else:
                task = None

        # If not found, we prompt the user to choose/enter a task file
        while task is None:
            try:
                _path = Path(os.path.join(self.config_library_dir, self.task_dir))
                available_files = glob.glob(os.path.join(_path, "*.json"))
                if len(available_files) == 0:
                    break
                path = self.frontend.prompt_pick(
                    ui.PickRequest(
                        label=f"Choose a task for {model.__name__}:", options=available_files, field="task"
                    )
                )
                if not isinstance(path, str):
                    raise ValueError("Invalid choice.")
                if not os.path.isfile(path):
                    raise FileNotFoundError(f"File not found: {path}")
                task = model_from_json_file(path, model)
                logger.info("User entered: %s.", path)
            except pydantic.ValidationError as e:
                logger.error("Failed to validate pydantic model. Try again. %s", e)
            except (ValueError, FileNotFoundError) as e:
                logger.info("Invalid choice. Try again. %s", e)
        if task is None:
            self.frontend.notify("No task file found.", ui.MessageLevel.ERROR)
            raise ValueError("No task file found.")

        return task

    def pick_trainer_state(self, task_model: Type[TTask]) -> tuple[TrainerState, TTask]:
        """
        Prompts the user to select or create a trainer state configuration.

        Attempts to load trainer state in the following order:
        1. If task already exists in launcher, will return an empty TrainerState
        2. From subject-specific folder

        It will launcher.set_task if the deserialized TrainerState is valid.

        Args:
            task_model: The task model type to validate against

        Returns:
            tuple[TrainerState, TTask]: The deserialized TrainerState object and validated task

        Raises:
            ValueError: If no valid task file is found or session is not set
        """

        if self._session is None:
            raise ValueError("Session must be picked (pick_session) before picking trainer state.")
        try:
            f = self.subject_dir / self._session.subject / (ByAnimalFiles.TRAINER_STATE.value + ".json")
            logger.info("Attempting to load trainer state from subject folder: %s", f)
            trainer_state = model_from_json_file(f, TrainerState)
            if trainer_state.stage is None:
                raise ValueError("Trainer state stage is None, cannot use this trainer state.")
        except (ValueError, FileNotFoundError, pydantic.ValidationError) as e:
            logger.error("Failed to find a valid task file. %s", e)
            raise
        else:
            self._trainer_state = trainer_state

        if not self._trainer_state.is_on_curriculum:
            self.frontend.notify("Deserialized TrainerState is NOT on curriculum.", ui.MessageLevel.WARNING)

        assert self._trainer_state.stage is not None
        return (
            self.trainer_state,
            task_model.model_validate_json(self.trainer_state.stage.task.model_dump_json()),
        )

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
        if self._use_cache:
            subjects = self._cache_manager.try_get_cache("subjects")
        else:
            subjects = None
        options = sorted(subjects) if subjects else []

        subject: Optional[str] = None
        while not subject:
            subject = self.frontend.prompt_autocomplete(
                ui.AutoCompleteRequest(
                    label="Subject (type to filter, or enter a new one)",
                    options=options,
                    field="subject",
                )
            )
        self._cache_manager.add_to_cache("subjects", subject)
        return subject

    def prompt_experimenter(self, strict: bool = True) -> Optional[List[str]]:
        """
        Prompts the user to enter the experimenter's name(s).

        Accepts multiple experimenter names separated by commas or spaces.
        Validates names using the configured validator function if provided.

        Args:
            strict: Whether to enforce non-empty input. Defaults to True

        Returns:
            Optional[List[str]]: List of experimenter names

        Example:
            ```python
            # Prompt for experimenter with validation
            names = picker.prompt_experimenter(strict=True)
            print("Experimenters:", names)
            ```
        """
        if self._use_cache:
            experimenters_cache = self._cache_manager.try_get_cache("experimenters")
        else:
            experimenters_cache = None
        options = sorted(experimenters_cache) if experimenters_cache else []
        experimenter: Optional[List[str]] = None
        while experimenter is None:
            _input = self.frontend.prompt_autocomplete(
                ui.AutoCompleteRequest(
                    label="Experimenter name(s) (type to filter, comma-separated for multiple)",
                    options=options,
                    field="experimenter",
                )
            )
            experimenter = _input.replace(",", " ").split()
            if strict & (len(experimenter) == 0):
                self.frontend.notify("Experimenter name is not valid. Try again.", ui.MessageLevel.WARNING)
                experimenter = None
            else:
                if self._experimenter_validator:
                    for name in experimenter:
                        if not self._experimenter_validator(name):
                            self.frontend.notify(
                                f"Experimenter name: {name}, is not valid. Try again", ui.MessageLevel.WARNING
                            )
                            experimenter = None
                            break
        self._cache_manager.add_to_cache("experimenters", ",".join(experimenter))
        return experimenter

    def dump_model(
        self,
        model: Union[Rig, Task, TrainerState],
    ) -> Optional[Path]:
        """
        Saves the provided model to the appropriate configuration file.

        Args:
            model: The model instance to save

        Returns:
            Optional[Path]: The path to the saved model file, or None if not saved
        """

        path: Path
        if isinstance(model, Rig):
            path = self.rig_dir / ("rig.json")
        elif isinstance(model, Task):
            if self._session is None:
                raise ValueError("Session must be picked (pick_session) before dumping task.")
            path = Path(self.subject_dir) / self._session.subject / (ByAnimalFiles.TASK.value + ".json")
        elif isinstance(model, TrainerState):
            if self._session is None:
                raise ValueError("Session must be picked (pick_session) before dumping trainer state.")
            path = Path(self.subject_dir) / self._session.subject / (ByAnimalFiles.TRAINER_STATE.value + ".json")
        else:
            raise ValueError("Model type not supported for dumping.")

        os.makedirs(path.parent, exist_ok=True)
        if path.exists():
            overwrite = self.frontend.prompt_confirm(
                ui.ConfirmRequest(label=f"File {path} already exists. Overwrite?")
            )
            if not overwrite:
                logger.info("User chose not to overwrite the existing file: %s", path)
                return None
        with open(path, "w", encoding="utf-8") as f:
            f.write(model.model_dump_json(indent=2))
        self.frontend.notify(f"Saved model to {path}", ui.MessageLevel.SUCCESS)
        return path
