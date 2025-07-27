import abc
from typing import TYPE_CHECKING, Generic, Optional, Self, TypeVar

from aind_behavior_services.rig import AindBehaviorRigModel
from aind_behavior_services.session import AindBehaviorSessionModel
from aind_behavior_services.task_logic import AindBehaviorTaskLogicModel

from .ui_helper import _UiHelperBase

if TYPE_CHECKING:
    from ..launcher import BaseLauncher
else:
    BaseLauncher = "BaseLauncher"

_L = TypeVar("_L", bound=BaseLauncher)
_R = TypeVar("_R", bound=AindBehaviorRigModel)
_S = TypeVar("_S", bound=AindBehaviorSessionModel)
_T = TypeVar("_T", bound=AindBehaviorTaskLogicModel)


class PickerBase(abc.ABC, Generic[_L, _R, _S, _T]):
    """
    Abstract base class for pickers that handle the selection of rigs, sessions, and task logic.

    This class defines the interface for picker implementations that manage the selection
    and configuration of experiment components including rigs, sessions, and task logic.

    Type Parameters:
        _L: Type of the launcher
        _R: Type of the rig model
        _S: Type of the session model
        _T: Type of the task logic model

    Example:
        ```python
        class MyPicker(PickerBase):
            def pick_rig(self):
                return MyRigModel(name="test_rig")

            def pick_session(self):
                return MySessionModel(subject="test_subject")

            def pick_task_logic(self):
                return MyTaskLogicModel(name="test_task")

            def initialize(self):
                pass

        picker = MyPicker()
        picker.register_launcher(launcher)
        picker.initialize()
        rig = picker.pick_rig()
        ```
    """

    def __init__(self, *, ui_helper: Optional[_UiHelperBase] = None, **kwargs) -> None:
        """
        Initializes the picker with an optional launcher and UI helper.

        Args:
            launcher: The launcher instance
            ui_helper: The UI helper instance

        Example:
            ```python
            # Create picker without dependencies
            picker = MyPicker()

            # Create picker with launcher and UI helper
            launcher = MyLauncher(...)
            ui_helper = DefaultUIHelper()
            picker = MyPicker(launcher=launcher, ui_helper=ui_helper)
            ```
        """
        self._ui_helper = ui_helper

    def register_ui_helper(self, ui_helper: _UiHelperBase) -> Self:
        """
        Registers a UI helper with the picker.

        Associates a UI helper instance with this picker for user interactions.

        Args:
            ui_helper: The UI helper to register

        Returns:
            Self: The picker instance for method chaining

        Raises:
            ValueError: If a UI helper is already registered
        """
        if self._ui_helper is None:
            self._ui_helper = ui_helper
        else:
            raise ValueError("UI Helper is already registered")
        return self

    @property
    def has_ui_helper(self) -> bool:
        """
        Checks if a UI helper is registered.

        Returns:
            bool: True if a UI helper is registered, False otherwise
        """
        return self._ui_helper is not None

    @property
    def ui_helper(self) -> _UiHelperBase:
        """
        Retrieves the registered UI helper.

        Returns:
            _UiHelperBase: The registered UI helper

        Raises:
            ValueError: If no UI helper is registered
        """
        if self._ui_helper is None:
            raise ValueError("UI Helper is not registered")
        return self._ui_helper

    @abc.abstractmethod
    def pick_rig(self, launcher: _L) -> _R:
        """
        Abstract method to pick a rig.

        Subclasses must implement this method to provide rig selection functionality.

        Returns:
            _R: The selected rig
        """
        ...

    @abc.abstractmethod
    def pick_session(self, launcher: _L) -> _S:
        """
        Abstract method to pick a session.

        Subclasses must implement this method to provide session selection/creation functionality.

        Returns:
            _S: The selected session
        """
        ...

    @abc.abstractmethod
    def pick_task_logic(self, launcher: _L) -> _T:
        """
        Abstract method to pick task logic.

        Subclasses must implement this method to provide task logic selection functionality.

        Returns:
            _T: The selected task logic
        """
        ...
