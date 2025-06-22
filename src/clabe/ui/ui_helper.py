import abc
import logging
from typing import Any, Callable, List, Optional, Type, TypeAlias, TypeVar

from pydantic import BaseModel, TypeAdapter

logger = logging.getLogger(__name__)

_PrintFunc = Callable[[str], Any]
_InputFunc = Callable[[str], str]
_DEFAULT_PRINT_FUNC: _PrintFunc = print
_DEFAULT_INPUT_FUNC: _InputFunc = input
_T = TypeVar("_T", bound=Any)
_TModel = TypeVar("_TModel", bound=BaseModel)


class _UiHelperBase(abc.ABC):
    """
    Abstract base class for UI helpers that provide methods for user interaction.
    """

    _print: _PrintFunc
    _input: _InputFunc

    def __init__(
        self, *args, print_func: Optional[_PrintFunc] = None, input_func: Optional[_PrintFunc] = None, **kwargs
    ):
        """
        Initializes the UI helper with optional custom print and input functions.

        Args:
            print_func (Optional[_PrintFunc]): Custom function for printing messages.
            input_func (Optional[_PrintFunc]): Custom function for receiving input.
        """
        self._print = print_func if print_func is not None else _DEFAULT_PRINT_FUNC
        self._input = input_func if input_func is not None else _DEFAULT_INPUT_FUNC

    def print(self, message: str) -> Any:
        """
        Prints a message using the configured print function.

        Args:
            message (str): The message to print.

        Returns:
            Any: The result of the print function.
        """
        return self._print(message)

    def input(self, prompt: str) -> str:
        """
        Prompts the user for input using the configured input function.

        Args:
            prompt (str): The prompt message.

        Returns:
            str: The user input.
        """
        return self._input(prompt)

    @abc.abstractmethod
    def prompt_pick_from_list(self, value: List[str], prompt: str, **kwargs) -> Optional[str]:
        """
        Abstract method to prompt the user to pick an item from a list.

        Args:
            value (List[str]): The list of items to choose from.
            prompt (str): The prompt message.

        Returns:
            Optional[str]: The selected item or None.
        """

    @abc.abstractmethod
    def prompt_yes_no_question(self, prompt: str) -> bool:
        """
        Abstract method to prompt the user with a yes/no question.

        Args:
            prompt (str): The question to ask.

        Returns:
            bool: True for yes, False for no.
        """

    @abc.abstractmethod
    def prompt_text(self, prompt: str) -> str:
        """
        Abstract method to prompt the user for generic text input.

        Args:
            prompt (str): The prompt message.

        Returns:
            str: The user input.
        """

    @abc.abstractmethod
    def prompt_float(self, prompt: str) -> float:
        """
        Abstract method to prompt the user for a float input.

        Args:
            prompt (str): The prompt message.

        Returns:
            float: The parsed user input.
        """
        pass


UiHelper: TypeAlias = _UiHelperBase


class DefaultUIHelper(_UiHelperBase):
    """
    Default implementation of the UI helper for user interaction.
    """

    def prompt_pick_from_list(
        self, value: List[str], prompt: str, allow_0_as_none: bool = True, **kwargs
    ) -> Optional[str]:
        """
        Prompts the user to pick an item from a list.

        Args:
            value (List[str]): The list of items to choose from.
            prompt (str): The prompt message.
            allow_0_as_none (bool): Whether to allow 0 as a choice for None.

        Returns:
            Optional[str]: The selected item or None.
        """
        while True:
            try:
                self.print(prompt)
                if allow_0_as_none:
                    self.print("0: None")
                for i, item in enumerate(value):
                    self.print(f"{i + 1}: {item}")
                choice = int(input("Choice: "))
                if choice < 0 or choice >= len(value) + 1:
                    raise ValueError
                if choice == 0:
                    if allow_0_as_none:
                        return None
                    else:
                        raise ValueError
                return value[choice - 1]
            except ValueError as e:
                logger.error("Invalid choice. Try again. %s", e)

    def prompt_yes_no_question(self, prompt: str) -> bool:
        """
        Prompts the user with a yes/no question.

        Args:
            prompt (str): The question to ask.

        Returns:
            bool: True for yes, False for no.
        """
        while True:
            reply = input(prompt + " (Y\\N): ").upper()
            if reply == "Y" or reply == "1":
                return True
            elif reply == "N" or reply == "0":
                return False
            else:
                self.print("Invalid input. Please enter 'Y' or 'N'.")

    def prompt_text(self, prompt: str) -> str:
        """
        Prompts the user for text input.

        Args:
            prompt (str): The prompt message.

        Returns:
            str: The user input.
        """
        notes = str(input(prompt))
        return notes

    def prompt_float(self, prompt: str) -> float:
        """
        Prompts the user for a float input.

        Args:
            prompt (str): The prompt message.

        Returns:
            float: The parsed user input.
        """
        while True:
            try:
                value = float(input(prompt))
                return value
            except ValueError:
                self.print("Invalid input. Please enter a valid float.")


def prompt_field_from_input(model: Type[_TModel], field_name: str, default: Optional[_T] = None) -> Optional[_T]:
    """
    Prompts the user to input a value for a specific field in a model.

    Args:
        model (_TModel): The model containing the field.
        field_name (str): The name of the field.
        default (Optional[_T]): The default value if no input is provided.

    Returns:
        Optional[_T]: The validated input value or the default value.
    """
    _field = model.model_fields[field_name]
    _type_adaptor: TypeAdapter = TypeAdapter(_field.annotation)
    value: Optional[_T] | str
    _in = input(f"Enter {field_name} ({_field.description}): ")
    value = _in if _in != "" else default
    return _type_adaptor.validate_python(value)
