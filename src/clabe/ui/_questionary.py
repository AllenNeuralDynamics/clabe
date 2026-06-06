import asyncio
import sys
from typing import Optional

import questionary
from questionary import Style

from ._frontend import FrontendBase
from ._messages import MessageLevel
from ._requests import AutoCompleteRequest, ConfirmRequest, PickRequest, TextRequest

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

_LEVEL_STYLES = {
    MessageLevel.INFO: "bold italic",
    MessageLevel.SUCCESS: "bold fg:ansigreen",
    MessageLevel.WARNING: "bold fg:ansiyellow",
    MessageLevel.ERROR: "bold fg:ansired",
}


def _flush_input() -> None:
    """Discard any characters already queued in the stdin buffer.

    Prevents type-ahead from a previous prompt being passed straight to the
    next one when there is processing work between prompts.
    """
    if sys.platform == "win32":
        import msvcrt

        while msvcrt.kbhit():
            msvcrt.getwch()
    else:
        import termios

        termios.tcflush(sys.stdin, termios.TCIFLUSH)


def _ask_sync(question: questionary.Question):
    """Ask question, handling both sync and async contexts.

    When in an async context, runs the questionary prompt in a thread pool
    to avoid the "asyncio.run() cannot be called from a running event loop" error.

    Uses unsafe_ask() to ensure KeyboardInterrupt is raised instead of being
    caught and converted to None with "Cancelled by user" message.

    Raises:
        KeyboardInterrupt: If user presses Ctrl+C to terminate
    """
    try:
        asyncio.get_running_loop()
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(question.unsafe_ask)
            return future.result()
    except RuntimeError:
        return question.unsafe_ask()


class QuestionaryFrontend(FrontendBase):
    """Frontend implementation using Questionary for styled console prompts."""

    def __init__(self, style: Optional[questionary.Style] = None) -> None:
        """Initializes the frontend with an optional custom style."""
        super().__init__()
        self.style = style or custom_style

    def _render(self, message: str, level: MessageLevel) -> None:
        """Prints a message with level-appropriate styling."""
        questionary.print(message, _LEVEL_STYLES[level])

    def _ask_text(self, request: TextRequest) -> str:
        """Collects free-form text."""
        _flush_input()
        return _ask_sync(questionary.text(request.label, default=request.default or "", style=self.style)) or ""

    def _ask_pick(self, request: PickRequest) -> Optional[str]:
        """Collects a single choice via an interactive list."""
        _flush_input()
        choices = request.choices()
        labels = [choice.display for choice in choices]
        by_label = {choice.display: choice.value for choice in choices}

        if request.allow_none:
            labels = [request.none_label] + labels

        result = _ask_sync(
            questionary.select(
                request.label,
                choices=labels,
                style=self.style,
                use_arrow_keys=True,
                use_indicator=True,
                use_shortcuts=True,
            )
        )

        if result is None:
            return None
        if request.allow_none and result == request.none_label:
            return None
        return by_label[result]

    def _ask_autocomplete(self, request: AutoCompleteRequest) -> str:
        """Collects text with type-to-filter autocompletion."""
        _flush_input()
        return (
            _ask_sync(
                questionary.autocomplete(
                    request.label,
                    choices=request.suggestions(),
                    default=request.default or "",
                    style=self.style,
                )
            )
            or ""
        )

    def _ask_confirm(self, request: ConfirmRequest) -> bool:
        """Collects a yes/no answer."""
        _flush_input()
        result = _ask_sync(questionary.confirm(request.label, default=request.default, style=self.style))
        return bool(result)
