from typing import Any, Callable, Optional

from ._frontend import FrontendBase
from ._messages import MessageLevel
from ._requests import AutoCompleteRequest, ConfirmRequest, PickRequest, TextRequest

_PrintFunc = Callable[[str], Any]
_InputFunc = Callable[[str], str]

_PREFIXES = {
    MessageLevel.INFO: "",
    MessageLevel.SUCCESS: "[ok] ",
    MessageLevel.WARNING: "[!] ",
    MessageLevel.ERROR: "[x] ",
}


class ConsoleFrontend(FrontendBase):
    """
    Plain stdin/stdout frontend using the standard ``print``/``input``.

    This is the fallback used on non-interactive consoles (e.g. output piped to
    a file or CI) where a full TUI cannot run. It is also handy for tests and
    scripting, since both functions can be injected.
    """

    def __init__(self, print_func: _PrintFunc = print, input_func: _InputFunc = input) -> None:
        """
        Initializes the console frontend.

        Args:
            print_func: Function used to display output. Defaults to ``print``.
            input_func: Function used to collect input. Defaults to ``input``.
        """
        super().__init__()
        self._print = print_func
        self._input = input_func

    def _render(self, message: str, level: MessageLevel) -> None:
        """Prints ``message`` with a level-appropriate prefix."""
        self._print(f"{_PREFIXES[level]}{message}")

    def _ask_text(self, request: TextRequest) -> str:
        """Collects a single line of text."""
        suffix = f" [{request.default}]" if request.default is not None else ""
        return str(self._input(f"{request.label}{suffix}: "))

    def _ask_pick(self, request: PickRequest) -> Optional[str]:
        """Displays a numbered list and collects a selection."""
        choices = request.choices()
        while True:
            self._print(request.label)
            if request.allow_none:
                self._print(f"0: {request.none_label}")
            for index, choice in enumerate(choices):
                self._print(f"{index + 1}: {choice.display}")
            raw = self._input("Choice: ")
            try:
                selected = int(raw)
            except ValueError:
                self._render("Invalid choice. Try again.", MessageLevel.ERROR)
                continue
            if selected == 0 and request.allow_none:
                return None
            if 1 <= selected <= len(choices):
                return choices[selected - 1].value
            self._render("Invalid choice. Try again.", MessageLevel.ERROR)

    def _ask_autocomplete(self, request: AutoCompleteRequest) -> str:
        """Lists the available suggestions, then collects a line of text.

        Plain consoles cannot offer live completion, so the suggestions are
        shown up-front and any typed value is accepted (subject to the caller's
        strictness/validators).
        """
        suggestions = request.suggestions()
        if suggestions:
            self._print(f"{request.label} (suggestions: {', '.join(suggestions)})")
        suffix = f" [{request.default}]" if request.default is not None else ""
        return str(self._input(f"{request.label}{suffix}: "))

    def _ask_confirm(self, request: ConfirmRequest) -> bool:
        """Collects a yes/no answer."""
        default_hint = "Y/n" if request.default else "y/N"
        while True:
            reply = str(self._input(f"{request.label} ({default_hint}): ")).strip().upper()
            if reply == "":
                return request.default
            if reply in ("Y", "1"):
                return True
            if reply in ("N", "0"):
                return False
            self._render("Invalid input. Please enter 'Y' or 'N'.", MessageLevel.ERROR)
