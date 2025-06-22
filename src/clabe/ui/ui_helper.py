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
    
    This class defines the interface for user interface helpers that handle various
    types of user input and interaction patterns in a consistent manner.
    
    Attributes:
        _print (_PrintFunc): Function used for printing messages to the user
        _input (_InputFunc): Function used for receiving input from the user
    """

    _print: _PrintFunc
    _input: _InputFunc

    def __init__(
        self, *args, print_func: Optional[_PrintFunc] = None, input_func: Optional[_PrintFunc] = None, **kwargs
    ):
        """
        Initializes the UI helper with optional custom print and input functions.

        Args:
            print_func: Custom function for printing messages
            input_func: Custom function for receiving input
        """
        self._print = print_func if print_func is not None else _DEFAULT_PRINT_FUNC
        self._input = input_func if input_func is not None else _DEFAULT_INPUT_FUNC

    def print(self, message: str) -> Any:
        """
        Prints a message using the configured print function.

        Args:
            message: The message to print

        Returns:
            Any: The result of the print function
        """
        return self._print(message)

    def input(self, prompt: str) -> str:
        """
        Prompts the user for input using the configured input function.

        Args:
            prompt: The prompt message

        Returns:
            str: The user input
        """
        return self._input(prompt)

    @abc.abstractmethod
    def prompt_pick_from_list(self, value: List[str], prompt: str, **kwargs) -> Optional[str]:
        """
        Abstract method to prompt the user to pick an item from a list.

        Args:
            value: The list of items to choose from
            prompt: The prompt message

        Returns:
            Optional[str]: The selected item or None
        """

    @abc.abstractmethod
    def prompt_yes_no_question(self, prompt: str) -> bool:
        """
        Abstract method to prompt the user with a yes/no question.

        Args:
            prompt: The question to ask

        Returns:
            bool: True for yes, False for no
        """

    @abc.abstractmethod
    def prompt_text(self, prompt: str) -> str:
        """
        Abstract method to prompt the user for generic text input.

        Args:
            prompt: The prompt message

        Returns:
            str: The user input
        """

    @abc.abstractmethod
    def prompt_float(self, prompt: str) -> float:
        """
        Abstract method to prompt the user for a float input.

        Args:
            prompt: The prompt message

        Returns:
            float: The parsed user input
        """
        pass


UiHelper: TypeAlias = _UiHelperBase


class DefaultUIHelper(_UiHelperBase):
    """
    Default implementation of the UI helper for user interaction.
    
    This class provides a concrete implementation of the UI helper interface
    using standard console input/output for user interactions.
    
    Example:
        ```python
        helper = DefaultUIHelper()
        
        # Get user choice from list
        options = ["Option A", "Option B", "Option C"]
        choice = helper.prompt_pick_from_list(options, "Choose an option:")
        
        # Ask yes/no question
        proceed = helper.prompt_yes_no_question("Continue with operation?")
        
        # Get text input
        name = helper.prompt_text("Enter your name: ")
        
        # Get numeric input
        value = helper.prompt_float("Enter a number: ")
        ```
    """

    def prompt_pick_from_list(
        self, value: List[str], prompt: str, allow_0_as_none: bool = True, **kwargs
    ) -> Optional[str]:
        """
        Prompts the user to pick an item from a list.
        
        Displays a numbered list of options and prompts the user to select
        one by entering the corresponding number.

        Args:
            value: The list of items to choose from
            prompt: The prompt message
            allow_0_as_none: Whether to allow 0 as a choice for None

        Returns:
            Optional[str]: The selected item or None
            
        Example:
            ```python
            helper = DefaultUIHelper()
            files = ["file1.txt", "file2.txt", "file3.txt"]
            selected = helper.prompt_pick_from_list(files, "Choose a file:")
            
            # With None option disabled
            selected = helper.prompt_pick_from_list(
                files, "Must choose a file:", allow_0_as_none=False
            )
            ```
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
        
        Continues prompting until a valid yes/no response is received.

        Args:
            prompt: The question to ask

        Returns:
            bool: True for yes, False for no
            
        Example:
            ```python
            helper = DefaultUIHelper()
            
            if helper.prompt_yes_no_question("Save changes?"):
                save_data()
            
            proceed = helper.prompt_yes_no_question("Delete all files?")
            if proceed:
                delete_files()
            ```
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
        
        Simple text input prompt that returns the user's input as a string.

        Args:
            prompt: The prompt message

        Returns:
            str: The user input
            
        Example:
            ```python
            helper = DefaultUIHelper()
            
            name = helper.prompt_text("Enter your name: ")
            description = helper.prompt_text("Enter description: ")
            path = helper.prompt_text("Enter file path: ")
            ```
        """
        notes = str(input(prompt))
        return notes

    def prompt_float(self, prompt: str) -> float:
        """
        Prompts the user for a float input.
        
        Continues prompting until a valid float value is entered.

        Args:
            prompt: The prompt message

        Returns:
            float: The parsed user input
            
        Example:
            ```python
            helper = DefaultUIHelper()
            
            temperature = helper.prompt_float("Enter temperature: ")
            weight = helper.prompt_float("Enter weight in kg: ")
            price = helper.prompt_float("Enter price: $")
            ```
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
    
    Uses the model's field information to prompt for input and validates the
    entered value against the field's type annotation.

    Args:
        model: The model containing the field
        field_name: The name of the field
        default: The default value if no input is provided

    Returns:
        Optional[_T]: The validated input value or the default value
        
    Example:
        ```python
        from pydantic import BaseModel, Field
        
        class UserModel(BaseModel):
            name: str = Field(description="User's full name")
            age: int = Field(description="User's age in years")
        
        # Prompt for name field
        name = prompt_field_from_input(UserModel, "name", "Anonymous")
        
        # Prompt for age field
        age = prompt_field_from_input(UserModel, "age", 18)
        ```
    """
    _field = model.model_fields[field_name]
    _type_adaptor: TypeAdapter = TypeAdapter(_field.annotation)
    value: Optional[_T] | str
    _in = input(f"Enter {field_name} ({_field.description}): ")
    value = _in if _in != "" else default
    return _type_adaptor.validate_python(value)
