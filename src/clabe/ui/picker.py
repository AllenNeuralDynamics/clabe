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
            
            def finalize(self):
                pass
        
        picker = MyPicker()
        picker.register_launcher(launcher)
        picker.initialize()
        rig = picker.pick_rig()
        ```
    """

    def __init__(self, launcher: Optional[_L] = None, *, ui_helper: Optional[_UiHelperBase] = None, **kwargs) -> None:
        """
        Initializes the picker with an optional launcher and UI helper.

        Args:
            launcher: The launcher instance
            ui_helper: The UI helper instance
        """
        self._launcher = launcher
        self._ui_helper = ui_helper

    def register_launcher(self, launcher: _L) -> Self:
        """
        Registers a launcher with the picker.
        
        Associates a launcher instance with this picker for accessing experiment
        configuration and state.

        Args:
            launcher: The launcher to register

        Returns:
            Self: The picker instance for method chaining
            
        Raises:
            ValueError: If a launcher is already registered
            
        Example:
            ```python
            picker = MyPicker()
            launcher = MyLauncher()
            
            picker.register_launcher(launcher)
            # Now picker can access launcher settings
            settings = picker.launcher.settings
            ```
        """
        if self._launcher is None:
            self._launcher = launcher
        else:
            raise ValueError("Launcher is already registered")
        return self

    @property
    def has_launcher(self) -> bool:
        """
        Checks if a launcher is registered.

        Returns:
            bool: True if a launcher is registered, False otherwise
            
        Example:
            ```python
            picker = MyPicker()
            print(picker.has_launcher)  # False
            
            picker.register_launcher(launcher)
            print(picker.has_launcher)  # True
            ```
        """
        return self._launcher is not None

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
    def launcher(self) -> _L:
        """
        Retrieves the registered launcher.

        Returns:
            _L: The registered launcher
            
        Raises:
            ValueError: If no launcher is registered
        """
        if self._launcher is None:
            raise ValueError("Launcher is not registered")
        return self._launcher

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
    def pick_rig(self) -> _R:
        """
        Abstract method to pick a rig.
        
        Subclasses must implement this method to provide rig selection functionality.

        Returns:
            _R: The selected rig
        """
        ...

    @abc.abstractmethod
    def pick_session(self) -> _S:
        """
        Abstract method to pick a session.
        
        Subclasses must implement this method to provide session selection/creation functionality.

        Returns:
            _S: The selected session
        """
        ...

    @abc.abstractmethod
    def pick_task_logic(self) -> _T:
        """
        Abstract method to pick task logic.
        
        Subclasses must implement this method to provide task logic selection functionality.

        Returns:
            _T: The selected task logic
        """
        ...

    @abc.abstractmethod
    def initialize(self) -> None:
        """
        Abstract method to initialize the picker.
        
        Subclasses should implement this method to perform any necessary setup operations.
        """
        ...

    @abc.abstractmethod
    def finalize(self) -> None:
        """
        Abstract method to finalize the picker.
        
        Subclasses should implement this method to perform any necessary cleanup operations.
        """
        ...


class DefaultPicker(PickerBase[_L, _R, _S, _T]):
    """
    Default implementation of the picker. This serves as a placeholder implementation.
    
    This class provides a basic implementation that raises NotImplementedError for
    all picker methods, serving as a template for actual picker implementations.
    """

    def pick_rig(self) -> _R:
        """
        Raises NotImplementedError as this method is not implemented.
        
        Raises:
            NotImplementedError: Always, as this is a placeholder implementation
        """
        raise NotImplementedError("pick_rig method is not implemented")

    def pick_session(self) -> _S:
        """
        Raises NotImplementedError as this method is not implemented.
        
        Raises:
            NotImplementedError: Always, as this is a placeholder implementation
        """
        raise NotImplementedError("pick_session method is not implemented")

    def pick_task_logic(self) -> _T:
        """
        Raises NotImplementedError as this method is not implemented.
        
        Raises:
            NotImplementedError: Always, as this is a placeholder implementation
        """
        raise NotImplementedError("pick_task_logic method is not implemented")

    def initialize(self) -> None:
        """
        Placeholder implementation for initialization.
        
        Does nothing in the default implementation.
        """
        return

    def finalize(self) -> None:
        """
        Placeholder implementation for finalization.
        
        Does nothing in the default implementation.
        """
        return
