import asyncio
import logging
from typing import List, Optional

import questionary
from questionary import Style

from .ui_helper import _UiHelperBase

logger = logging.getLogger(__name__)

custom_style = Style(
    [
        ("qmark", "fg:#5f87ff bold"),  # Question mark - blue
        ("question", "fg:#ffffff bold"),  # Question text - white bold
        ("answer", "fg:#5f87ff bold"),  # Selected answer - blue
        ("pointer", "fg:#5f87ff bold"),  # Pointer - blue arrow
        ("highlighted", "fg:#000000 bg:#5f87ff bold"),  # INVERTED: black text on blue background
        ("selected", "fg:#5f87ff"),  # After selection
        ("separator", "fg:#666666"),  # Separator
        ("instruction", "fg:#888888"),  # Instructions
        ("text", ""),  # Plain text
        ("disabled", "fg:#858585 italic"),  # Disabled
    ]
)


def _ask_sync(question: questionary.Question):
    # TODO: We should just implement an async version of the UIHelper and avoid this complexity.
    """Ask question, handling both sync and async contexts.

    When in an async context, runs the questionary prompt in a thread pool
    to avoid the "asyncio.run() cannot be called from a running event loop" error.

    Uses unsafe_ask() to ensure KeyboardInterrupt is raised instead of being
    caught and converted to None with "Cancelled by user" message.

    Raises:
        KeyboardInterrupt: If user presses Ctrl+C to terminate
    """
    try:
        # Check if we're in an async context
        asyncio.get_running_loop()
        # We are in an async context - use thread pool to avoid nested event loop
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            # Use unsafe_ask() to propagate KeyboardInterrupt instead of catching it
            future = executor.submit(question.unsafe_ask)
            return future.result()

    except RuntimeError:
        # No running loop - use unsafe_ask() to propagate KeyboardInterrupt
        return question.unsafe_ask()


class QuestionaryUIHelper(_UiHelperBase):
    """UI helper implementation using Questionary for interactive prompts."""

    def __init__(self, style: Optional[questionary.Style] = None) -> None:
        """Initializes the QuestionaryUIHelper with an optional custom style."""
        self.style = style or custom_style

    def print(self, message: str) -> None:
        """Prints a message with custom styling."""
        questionary.print(message, "bold italic")

    def input(self, prompt: str) -> str:
        """Prompts the user for input with custom styling."""
        return _ask_sync(questionary.text(prompt, style=self.style)) or ""

    def prompt_pick_from_list(self, value: List[str], prompt: str, **kwargs) -> Optional[str]:
        """Interactive list selection with visual highlighting using arrow keys or number shortcuts."""
        allow_0_as_none = kwargs.get("allow_0_as_none", True)
        zero_label = kwargs.get("zero_label", "None")

        choices = []

        if allow_0_as_none:
            choices.append(zero_label)

        choices.extend(value)

        result = _ask_sync(
            questionary.select(
                prompt,
                choices=choices,
                style=self.style,
                use_arrow_keys=True,
                use_indicator=True,
                use_shortcuts=True,
            )
        )

        if result is None:
            return None

        if result == zero_label and allow_0_as_none:
            return None

        return result

    def prompt_yes_no_question(self, prompt: str) -> bool:
        """Prompts the user with a yes/no question using custom styling."""
        return _ask_sync(questionary.confirm(prompt, style=self.style)) or False

    def prompt_text(self, prompt: str) -> str:
        """Prompts the user for generic text input using custom styling."""
        return _ask_sync(questionary.text(prompt, style=self.style)) or ""

    def prompt_float(self, prompt: str) -> float:
        """Prompts the user for a float input using custom styling."""
        while True:
            try:
                value_str = _ask_sync(questionary.text(prompt, style=self.style))
                if value_str:
                    return float(value_str)
            except ValueError:
                self.print("Invalid input. Please enter a valid float.")
