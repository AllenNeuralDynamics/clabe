from typing import List, Optional, Tuple

from rich.console import Console, Group
from rich.live import Live
from rich.prompt import Confirm, Prompt
from rich.text import Text

from . import _keys
from ._frontend import FrontendBase
from ._messages import MessageLevel
from ._requests import AutoCompleteRequest, ConfirmRequest, PickRequest, TextRequest

#: Maximum suggestions shown at once while filtering an autocomplete prompt.
_MAX_VISIBLE_SUGGESTIONS = 8

#: Per-level styling applied to surfaced messages.
_LEVEL_STYLES = {
    MessageLevel.INFO: "cyan",
    MessageLevel.SUCCESS: "bold green",
    MessageLevel.WARNING: "bold yellow",
    MessageLevel.ERROR: "bold red",
}

#: Short glyph shown ahead of a message to reinforce its level at a glance.
_LEVEL_PREFIXES = {
    MessageLevel.INFO: "",
    MessageLevel.SUCCESS: "✓ ",
    MessageLevel.WARNING: "⚠ ",
    MessageLevel.ERROR: "✗ ",
}


class ConsoleFrontend(FrontendBase):
    """
    Console frontend using `rich` for styled, line-based prompts.

    Renders messages and prompts with colour and structure on an interactive
    terminal while degrading gracefully: rich strips styling automatically and
    falls back to plain ``input`` when output is not a terminal (e.g. piped to a
    file or running in CI), so this also serves as the non-interactive fallback.

    Prompts are line-based — the user types an answer and presses Enter — with
    rich handling validation and re-prompting for the primitive types.
    """

    def __init__(self, console: Optional[Console] = None) -> None:
        """
        Initializes the console frontend.

        Args:
            console: The rich console to render on. Defaults to the shared
                ``clabe`` console so output coordinates with the logging handler.
        """
        super().__init__()
        if console is None:
            from ..logging_helper import clabe_console

            console = clabe_console
        self._console = console

    def _render(self, message: str, level: MessageLevel) -> None:
        """Prints a message with level-appropriate styling."""
        self._console.print(Text(f"{_LEVEL_PREFIXES[level]}{message}", style=_LEVEL_STYLES[level]))

    def _render_header(self, text: str) -> None:
        """Prints a prominent header as a horizontal rule."""
        self._console.rule(Text(text, style="bold"))

    def _ask_text(self, request: TextRequest) -> str:
        """Collects a single line of text."""
        return Prompt.ask(
            Text(request.label, style="bold"),
            console=self._console,
            default=request.default if request.default is not None else "",
            show_default=request.default is not None,
        )

    def _ask_pick(self, request: PickRequest) -> Optional[str]:
        """Collects a single choice, using an arrow-key picker on a terminal."""
        if self._console.is_terminal:
            return self._pick_interactive(request)
        return self._pick_numbered(request)

    def _ask_autocomplete(self, request: AutoCompleteRequest) -> str:
        """Collects text, using a live type-to-filter picker on a terminal."""
        if self._console.is_terminal:
            return self._autocomplete_interactive(request)
        return self._autocomplete_listed(request)

    # --- interactive (terminal) variants ---------------------------------
    def _pick_interactive(self, request: PickRequest) -> Optional[str]:
        """Renders an arrow-key navigable list and returns the chosen value."""
        rows: List[Tuple[Optional[str], str]] = []
        if request.allow_none:
            rows.append((None, request.none_label))
        rows.extend((choice.value, choice.display) for choice in request.choices())

        index = 0
        if request.default is not None:
            index = next((i for i, (value, _) in enumerate(rows) if value == request.default), 0)

        with Live(self._render_menu(request.label, rows, index), console=self._console, transient=True) as live:
            while True:
                key = _keys.read_key()
                if key == _keys.INTERRUPT:
                    raise KeyboardInterrupt
                if key == _keys.ENTER:
                    break
                if key == _keys.UP:
                    index = (index - 1) % len(rows)
                elif key == _keys.DOWN:
                    index = (index + 1) % len(rows)
                live.update(self._render_menu(request.label, rows, index))

        value, display = rows[index]
        self._console.print(Text(f"{request.label} ", style="bold").append(display, style="cyan"))
        return value

    def _autocomplete_interactive(self, request: AutoCompleteRequest) -> str:
        """Renders a live-filtered suggestion list and returns the chosen value.

        Typing narrows the suggestions in place. Enter accepts the highlighted
        match when there is one, otherwise the free-form text typed so far (so a
        value not in the list — e.g. a comma-separated set — is still allowed).
        Tab/→ completes the query to the highlighted suggestion without
        submitting.
        """
        suggestions = request.suggestions()
        query = request.default or ""
        index = 0

        result = query
        with Live(console=self._console, transient=True) as live:
            while True:
                filtered = self._filter(suggestions, query)
                index = max(0, min(index, len(filtered) - 1))
                live.update(self._render_autocomplete(request.label, query, filtered, index))
                key = _keys.read_key()
                if key == _keys.INTERRUPT:
                    raise KeyboardInterrupt
                if key == _keys.ENTER:
                    result = filtered[index] if filtered else query
                    break
                if key in (_keys.TAB, _keys.RIGHT):
                    if filtered:
                        query = filtered[index]
                elif key == _keys.UP:
                    index -= 1
                elif key == _keys.DOWN:
                    index += 1
                elif key == _keys.BACKSPACE:
                    query, index = query[:-1], 0
                elif len(key) == 1 and key.isprintable():
                    query, index = query + key, 0

        self._console.print(Text(f"{request.label} ", style="bold").append(result, style="cyan"))
        return result

    @staticmethod
    def _filter(suggestions: List[str], query: str) -> List[str]:
        """Returns the suggestions containing ``query`` (case-insensitive)."""
        if not query:
            return suggestions
        lowered = query.lower()
        return [suggestion for suggestion in suggestions if lowered in suggestion.lower()]

    def _render_menu(self, label: str, rows: List[Tuple[Optional[str], str]], index: int) -> Group:
        """Builds the renderable for the interactive picker at the given cursor."""
        lines: List[Text] = [Text(label, style="bold")]
        for position, (_, display) in enumerate(rows):
            if position == index:
                lines.append(Text(f"❯ {display}", style="bold cyan"))
            else:
                lines.append(Text(f"  {display}", style="none"))
        return Group(*lines)

    def _render_autocomplete(self, label: str, query: str, filtered: List[str], index: int) -> Group:
        """Builds the renderable for the autocomplete prompt and its matches."""
        lines: List[Text] = [Text(f"{label}: ", style="bold").append(query, style="cyan").append("▏", style="dim")]
        for position, suggestion in enumerate(filtered[:_MAX_VISIBLE_SUGGESTIONS]):
            if position == index:
                lines.append(Text(f"❯ {suggestion}", style="bold cyan"))
            else:
                lines.append(Text(f"  {suggestion}", style="none"))
        return Group(*lines)

    # --- non-interactive (piped/CI) fallbacks ----------------------------
    def _pick_numbered(self, request: PickRequest) -> Optional[str]:
        """Displays a styled numbered list and collects a selection."""
        choices = request.choices()
        self._console.print(Text(request.label, style="bold"))

        indices: List[str] = []
        values: dict[str, Optional[str]] = {}
        if request.allow_none:
            self._console.print(Text("  0", style="bold cyan").append(f"  {request.none_label}", style="dim"))
            indices.append("0")
            values["0"] = None
        for index, choice in enumerate(choices, start=1):
            self._console.print(Text(f"  {index}", style="bold cyan").append(f"  {choice.display}", style="none"))
            indices.append(str(index))
            values[str(index)] = choice.value

        selection = Prompt.ask(
            Text("Choice", style="bold"),
            console=self._console,
            choices=indices,
            show_choices=False,
        )
        return values[selection]

    def _autocomplete_listed(self, request: AutoCompleteRequest) -> str:
        """Lists the available suggestions, then collects a line of text.

        A non-interactive console cannot offer live, type-to-filter completion,
        so the suggestions are shown up front and any typed value is accepted
        (subject to the caller's strictness/validators, enforced by the base
        class).
        """
        suggestions = request.suggestions()
        if suggestions:
            self._console.print(Text("Suggestions: ", style="dim").append(", ".join(suggestions), style="cyan"))
        return Prompt.ask(
            Text(request.label, style="bold"),
            console=self._console,
            default=request.default if request.default is not None else "",
            show_default=request.default is not None,
        )

    def _ask_confirm(self, request: ConfirmRequest) -> bool:
        """Collects a yes/no answer."""
        return bool(Confirm.ask(Text(request.label, style="bold"), console=self._console, default=request.default))
