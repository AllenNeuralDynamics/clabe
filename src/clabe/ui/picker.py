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
    """

    def __init__(self, launcher: Optional[_L] = None, *, ui_helper: Optional[_UiHelperBase] = None, **kwargs) -> None:
        """
        Initializes the picker with an optional launcher and UI helper.

        Args:
            launcher (Optional[_L]): The launcher instance.
            ui_helper (Optional[_UiHelperBase]): The UI helper instance.
        """
        self._launcher = launcher
        self._ui_helper = ui_helper

    def register_launcher(self, launcher: _L) -> Self:
        """
        Registers a launcher with the picker.

        Args:
            launcher (_L): The launcher to register.

        Returns:
            Self: The picker instance.
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
            bool: True if a launcher is registered, False otherwise.
        """
        return self._launcher is not None

    def register_ui_helper(self, ui_helper: _UiHelperBase) -> Self:
        """
        Registers a UI helper with the picker.

        Args:
            ui_helper (_UiHelperBase): The UI helper to register.

        Returns:
            Self: The picker instance.
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
            bool: True if a UI helper is registered, False otherwise.
        """
        return self._ui_helper is not None

    @property
    def launcher(self) -> _L:
        """
        Retrieves the registered launcher.

        Returns:
            _L: The registered launcher.

        Raises:
            ValueError: If no launcher is registered.
        """
        if self._launcher is None:
            raise ValueError("Launcher is not registered")
        return self._launcher

    @property
    def ui_helper(self) -> _UiHelperBase:
        """
        Retrieves the registered UI helper.

        Returns:
            _UiHelperBase: The registered UI helper.

        Raises:
            ValueError: If no UI helper is registered.
        """
        if self._ui_helper is None:
            raise ValueError("UI Helper is not registered")
        return self._ui_helper

    @abc.abstractmethod
    def pick_rig(self) -> _R:
        """
        Abstract method to pick a rig.

        Returns:
            _R: The selected rig.
        """
        ...

    @abc.abstractmethod
    def pick_session(self) -> _S:
        """
        Abstract method to pick a session.

        Returns:
            _S: The selected session.
        """
        ...

    @abc.abstractmethod
    def pick_task_logic(self) -> _T:
        """
        Abstract method to pick task logic.

        Returns:
            _T: The selected task logic.
        """
        ...

    @abc.abstractmethod
    def initialize(self) -> None:
        """
        Abstract method to initialize the picker.
        """
        ...

    @abc.abstractmethod
    def finalize(self) -> None:
        """
        Placeholder implementation for finalization.
        """
        ...


class DefaultPicker(PickerBase[_L, _R, _S, _T]):
    """
    Default implementation of the picker. This serves as a placeholder implementation.
    """

    def pick_rig(self) -> _R:
        """
        Raises NotImplementedError as this method is not implemented.
        """
        raise NotImplementedError("pick_rig method is not implemented")

    def pick_session(self) -> _S:
        """
        Raises NotImplementedError as this method is not implemented.
        """
        raise NotImplementedError("pick_session method is not implemented")

    def pick_task_logic(self) -> _T:
        """
        Raises NotImplementedError as this method is not implemented.
        """
        raise NotImplementedError("pick_task_logic method is not implemented")

    def initialize(self) -> None:
        """
        Placeholder implementation for initialization.
        """
        return

    def finalize(self) -> None:
        """
        Placeholder implementation for finalization.
        """
        return
