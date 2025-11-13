import abc
import functools
import glob
import logging
import os
from pathlib import Path
from typing import Any, Callable, ClassVar, Generic, List, Optional, Protocol, Type, TypeVar, Union, runtime_checkable

import pydantic
from aind_behavior_curriculum import TrainerState
from aind_behavior_services import AindBehaviorRigModel, AindBehaviorSessionModel, AindBehaviorTaskLogicModel
from aind_behavior_services.utils import model_from_json_file
from pydantic import TypeAdapter

from .. import ui
from .._typing import TRig, TSession, TTaskLogic
from ..constants import ByAnimalFiles
from ..launcher import Launcher
from ..services import ServiceSettings
from ..utils.aind_auth import validate_aind_username

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
    A picker class for selecting rig, session, and task logic configurations.

    Provides methods to initialize directories, pick configurations, and prompt user
    inputs for various components of the experiment setup. Manages the configuration
    library structure and user interactions for selecting experiment parameters.

    Properties:
        ui_helper: Helper for user interface interactions
        trainer_state: The current trainer state
        config_library_dir: Path to the configuration library directory
        rig_dir: Path to the rig configurations directory
        subject_dir: Path to the subject configurations directory
        task_logic_dir: Path to the task logic configurations directory

    Methods:
        pick_rig: Picks the rig configuration
        pick_session: Picks the session configuration
        pick_task_logic: Picks the task logic configuration
        pick_trainer_state: Picks the trainer state configuration
        choose_subject: Allows the user to choose a subject
        prompt_experimenter: Prompts for experimenter information
        dump_model: Saves a Pydantic model to a file
    """

    RIG_SUFFIX: str = "Rig"
    SUBJECT_SUFFIX: str = "Subjects"
    TASK_LOGIC_SUFFIX: str = "TaskLogic"

    def __init__(
        self,
        settings: DefaultBehaviorPickerSettings,
        launcher: Launcher,
        ui_helper: Optional[ui.UiHelper] = None,
        experimenter_validator: Optional[Callable[[str], bool]] = validate_aind_username,
    ):
        """
        Initializes the DefaultBehaviorPicker.

        Args:
            settings: Settings containing configuration including config_library_dir. By default, attempts to rely on DefaultBehaviorPickerSettings to automatic loading from yaml files
            launcher: The launcher instance for managing experiment execution
            ui_helper: Helper for user interface interactions. If None, uses launcher's ui_helper. Defaults to None
            experimenter_validator: Function to validate the experimenter's username. If None, no validation is performed. Defaults to validate_aind_username
        """
        self._launcher = launcher
        self._ui_helper = launcher.ui_helper if ui_helper is None else ui_helper
        self._settings = settings
        self._ensure_directories()
        self._experimenter_validator = experimenter_validator
        self._trainer_state: Optional[TrainerState] = None
        self._session: Optional[AindBehaviorSessionModel] = None

    @property
    def ui_helper(self) -> ui.UiHelper:
        """
        Retrieves the registered UI helper.

        Returns:
            UiHelper: The registered UI helper

        Raises:
            ValueError: If no UI helper is registered
        """
        if self._ui_helper is None:
            raise ValueError("UI Helper is not registered")
        return self._ui_helper

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
    def session(self) -> AindBehaviorSessionModel:
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
    def task_logic_dir(self) -> Path:
        """
        Returns the path to the task logic configuration directory.

        Returns:
            Path: The task logic configuration directory
        """
        return Path(os.path.join(self.config_library_dir, self.TASK_LOGIC_SUFFIX))

    def _ensure_directories(self) -> None:
        """
        Ensures the required directories for configuration files exist.

        Creates the configuration library directory and all required subdirectories
        for storing rig, task logic, and subject configurations.
        """
        self._launcher.create_directory(self.config_library_dir)
        self._launcher.create_directory(self.task_logic_dir)
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
        available_rigs = glob.glob(os.path.join(self.rig_dir, "*.json"))
        if len(available_rigs) == 0:
            logger.error("No rig config files found.")
            raise ValueError("No rig config files found.")
        elif len(available_rigs) == 1:
            logger.info("Found a single rig config file. Using %s.", {available_rigs[0]})
            rig = model_from_json_file(available_rigs[0], model)
            return rig
        else:
            while True:
                try:
                    path = self.ui_helper.prompt_pick_from_list(available_rigs, prompt="Choose a rig:")
                    if not isinstance(path, str):
                        raise ValueError("Invalid choice.")
                    rig = model_from_json_file(path, model)
                    logger.info("Using %s.", path)
                    return rig
                except pydantic.ValidationError as e:
                    logger.error("Failed to validate pydantic model. Try again. %s", e)
                except ValueError as e:
                    logger.info("Invalid choice. Try again. %s", e)

    def pick_session(self, model: Type[TSession] = AindBehaviorSessionModel) -> TSession:
        """
        Prompts the user to select or create a session configuration.

        Collects experimenter information, subject selection, and session notes
        to create a new session configuration with appropriate metadata.

        Args:
            model: The session model type to instantiate. Defaults to AindBehaviorSessionModel

        Returns:
            TSession: The created or selected session configuration
        """

        experimenter = self.prompt_experimenter(strict=True)
        subject = self.choose_subject(self.subject_dir)

        if not (self.subject_dir / subject).exists():
            logger.info("Directory for subject %s does not exist. Creating a new one.", subject)
            os.makedirs(self.subject_dir / subject)

        notes = self.ui_helper.prompt_text("Enter notes: ")
        session = model(
            root_path=str(Path(self._launcher.settings.data_dir).resolve() / subject),
            subject=subject,
            notes=notes,
            experimenter=experimenter if experimenter is not None else [],
            commit_hash=self._launcher.repository.head.commit.hexsha,
            allow_dirty_repo=self._launcher.settings.debug_mode or self._launcher.settings.allow_dirty,
            skip_hardware_validation=self._launcher.settings.skip_hardware_validation,
        )
        self._session = session
        return session

    def pick_task_logic(self, model: Type[TTaskLogic]) -> TTaskLogic:
        """
        Prompts the user to select or create a task logic configuration.

        Attempts to load task logic in the following order:
        1. From CLI if already set
        2. From subject-specific folder
        3. From user selection in task logic library

        Args:
            model: The task logic model type to validate against

        Returns:
            TTaskLogic: The created or selected task logic configuration

        Raises:
            ValueError: If no valid task logic file is found
        """
        task_logic: Optional[TTaskLogic] = None
        if self._session is None:
            raise ValueError("Session must be picked (pick_session) before picking task logic.")

        try:
            f = self.subject_dir / self._session.subject / (ByAnimalFiles.TASK_LOGIC.value + ".json")
            logger.info("Attempting to load task logic from subject folder: %s", f)
            task_logic = model_from_json_file(f, model)
        except (ValueError, FileNotFoundError, pydantic.ValidationError) as e:
            logger.warning("Failed to find a valid task logic file. %s", e)
        else:
            logger.info("Found a valid task logic file in subject folder!")
            _is_manual = not self.ui_helper.prompt_yes_no_question("Would you like to use this task logic?")
            if not _is_manual:
                if task_logic is not None:
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
                task_logic = model_from_json_file(path, model)
                logger.info("User entered: %s.", path)
            except pydantic.ValidationError as e:
                logger.error("Failed to validate pydantic model. Try again. %s", e)
            except (ValueError, FileNotFoundError) as e:
                logger.info("Invalid choice. Try again. %s", e)
        if task_logic is None:
            logger.error("No task logic file found.")
            raise ValueError("No task logic file found.")

        return task_logic

    def pick_trainer_state(self, task_logic_model: Type[TTaskLogic]) -> tuple[TrainerState, TTaskLogic]:
        """
        Prompts the user to select or create a trainer state configuration.

        Attempts to load trainer state in the following order:
        1. If task_logic already exists in launcher, will return an empty TrainerState
        2. From subject-specific folder

        It will launcher.set_task_logic if the deserialized TrainerState is valid.

        Args:
            task_logic_model: The task logic model type to validate against

        Returns:
            tuple[TrainerState, TTaskLogic]: The deserialized TrainerState object and validated task logic

        Raises:
            ValueError: If no valid task logic file is found or session is not set
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
            logger.error("Failed to find a valid task logic file. %s", e)
            raise
        else:
            self._trainer_state = trainer_state

        if not self._trainer_state.is_on_curriculum:
            logging.warning("Deserialized TrainerState is NOT on curriculum.")

        assert self._trainer_state.stage is not None
        return (
            self.trainer_state,
            task_logic_model.model_validate_json(self.trainer_state.stage.task.model_dump_json()),
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

    def dump_model(
        self,
        model: Union[AindBehaviorRigModel, AindBehaviorTaskLogicModel, TrainerState],
    ) -> Optional[Path]:
        """
        Saves the provided model to the appropriate configuration file.

        Args:
            model: The model instance to save

        Returns:
            Optional[Path]: The path to the saved model file, or None if not saved
        """

        path: Path
        if isinstance(model, AindBehaviorRigModel):
            path = self.rig_dir / ("rig.json")
        elif isinstance(model, AindBehaviorTaskLogicModel):
            if self._session is None:
                raise ValueError("Session must be picked (pick_session) before dumping task logic.")
            path = Path(self.subject_dir) / self._session.subject / (ByAnimalFiles.TASK_LOGIC.value + ".json")
        elif isinstance(model, TrainerState):
            if self._session is None:
                raise ValueError("Session must be picked (pick_session) before dumping trainer state.")
            path = Path(self.subject_dir) / self._session.subject / (ByAnimalFiles.TRAINER_STATE.value + ".json")
        else:
            raise ValueError("Model type not supported for dumping.")

        os.makedirs(path.parent, exist_ok=True)
        if path.exists():
            overwrite = self.ui_helper.prompt_yes_no_question(f"File {path} already exists. Overwrite?")
            if not overwrite:
                logger.info("User chose not to overwrite the existing file: %s", path)
                return None
        with open(path, "w", encoding="utf-8") as f:
            f.write(model.model_dump_json(indent=2))
            logger.info("Saved model to %s", path)
        return path


@runtime_checkable
class _IByAnimalModifier(Protocol, Generic[TRig]):
    """
    Protocol defining the interface for by-animal modifiers.

    This protocol defines the contract that any by-animal modifier must implement
    to inject and dump subject-specific configurations.
    """

    def inject(self, rig: TRig) -> TRig:
        """Injects subject-specific configuration into the rig model."""
        ...

    def dump(self) -> None:
        """Dumps the configuration to a JSON file."""
        ...


class ByAnimalModifier(abc.ABC, _IByAnimalModifier[TRig]):
    """
    Abstract base class for modifying rig configurations with subject-specific data.

    This class provides a framework for loading and saving subject-specific
    configuration data to/from JSON files. It uses reflection to access nested
    attributes in the rig model and automatically handles serialization.

    Attributes:
        _subject_db_path: Path to the directory containing subject-specific files
        _model_path: Dot-separated path to the attribute in the rig model (e.g., "nested.field")
        _model_name: Base name for the JSON file (without extension)
        _tp: TypeAdapter for the model type, set during inject()

    Example:
        ```python
        from pathlib import Path
        from clabe.pickers.default_behavior import ByAnimalModifier
        import pydantic

        class MyModel(pydantic.BaseModel):
            nested: "NestedConfig"

        class NestedConfig(pydantic.BaseModel):
            value: int

        class MyModifier(ByAnimalModifier[MyModel]):
            def __init__(self, subject_db_path: Path, **kwargs):
                super().__init__(
                    subject_db_path=subject_db_path,
                    model_path="nested",
                    model_name="nested_config",
                    **kwargs
                )

            def _process_before_dump(self):
                return NestedConfig(value=42)

        modifier = MyModifier(Path("./subject_db"))
        model = MyModel(nested=NestedConfig(value=1))
        modified = modifier.inject(model)
        modifier.dump()
        ```
    """

    def __init__(self, subject_db_path: Path, model_path: str, model_name: str, **kwargs) -> None:
        """
        Initializes the ByAnimalModifier.

        Args:
            subject_db_path: Path to the directory containing subject-specific JSON files
            model_path: Dot-separated path to the target attribute in the rig model
            model_name: Base name for the JSON file (without .json extension)
            **kwargs: Additional keyword arguments (reserved for future use)
        """
        self._subject_db_path = Path(subject_db_path)
        self._model_path = model_path
        self._model_name = model_name
        self._tp: TypeAdapter[Any] | None = None

    def _process_before_inject(self, deserialized: T) -> T:
        """
        Hook method called after deserialization but before injection.

        Override this method to modify the deserialized data before it's
        injected into the rig model.

        Args:
            deserialized: The deserialized object from the JSON file

        Returns:
            The processed object to be injected
        """
        return deserialized

    @abc.abstractmethod
    def _process_before_dump(self) -> Any:
        """
        Abstract method to generate the data to be dumped to JSON.

        Subclasses must implement this method to return the object that
        should be serialized and saved to the JSON file.

        Returns:
            The object to be serialized and dumped to JSON
        """
        ...

    def inject(self, rig: TRig) -> TRig:
        """
        Injects subject-specific configuration into the rig model.

        Loads configuration from a JSON file and injects it into the specified
        path in the rig model. If the file doesn't exist, the rig is returned
        unmodified with a warning logged.

        Args:
            rig: The rig model to modify

        Returns:
            The modified rig model
        """
        target_file = self._subject_db_path / f"{self._model_name}.json"
        if not target_file.exists():
            logger.warning(f"File not found: {target_file}. Using default.")
        else:
            target = rgetattr(rig, self._model_path)
            self._tp = TypeAdapter(type(target))
            deserialized = self._tp.validate_json(target_file.read_text(encoding="utf-8"))
            logger.info(f"Loading {self._model_name} from: {target_file}. Deserialized: {deserialized}")
            self._process_before_inject(deserialized)
            rsetattr(rig, self._model_path, deserialized)
        return rig

    def dump(self) -> None:
        """
        Dumps the configuration to a JSON file.

        Calls _process_before_dump() to get the data, then serializes it
        to JSON and writes it to the target file. Creates parent directories
        if they don't exist.

        Raises:
            Exception: If _process_before_dump() fails or serialization fails
        """
        target_folder = self._subject_db_path
        target_file = target_folder / f"{self._model_name}.json"

        if (tp := self._tp) is None:
            logger.warning("TypeAdapter is not set. Using TypeAdapter(Any) as fallback.")
            tp = TypeAdapter(Any)

        try:
            to_inject = self._process_before_dump()
            logger.info(f"Saving {self._model_name} to: {target_file}. Serialized: {to_inject}")
            target_folder.mkdir(parents=True, exist_ok=True)
            target_file.write_text(tp.dump_json(to_inject, indent=2).decode("utf-8"), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to process before dumping modifier: {e}")
            raise


def rsetattr(obj, attr, val):
    """
    Sets an attribute value using a dot-separated path.

    Args:
        obj: The object to modify
        attr: Dot-separated attribute path (e.g., "nested.field.value")
        val: The value to set

    Returns:
        The result of setattr on the final attribute

    Example:
        ```python
        class Inner:
            value = 1

        class Outer:
            inner = Inner()

        obj = Outer()
        rsetattr(obj, "inner.value", 42)
        assert obj.inner.value == 42
        ```
    """
    pre, _, post = attr.rpartition(".")
    return setattr(rgetattr(obj, pre) if pre else obj, post, val)


def rgetattr(obj, attr, *args):
    """
    Gets an attribute value using a dot-separated path.

    Args:
        obj: The object to query
        attr: Dot-separated attribute path (e.g., "nested.field.value")
        *args: Optional default value if attribute doesn't exist

    Returns:
        The attribute value at the specified path

    Example:
        ```python
        class Inner:
            value = 42

        class Outer:
            inner = Inner()

        obj = Outer()
        result = rgetattr(obj, "inner.value")
        assert result == 42

        default = rgetattr(obj, "nonexistent.path", "default")
        assert default == "default"
        ```
    """

    def _getattr(obj, attr):
        """Helper function to get attribute with optional default."""
        return getattr(obj, attr, *args)

    return functools.reduce(_getattr, [obj] + attr.split("."))
