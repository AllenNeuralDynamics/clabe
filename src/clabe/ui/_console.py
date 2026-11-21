"""Console client: rich-styled, line-based prompts that work on a terminal and degrade gracefully when piped."""

import functools
import logging
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar, TypeVar

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from . import _keys
from ._messages import MessageLevel
from ._requests import (
    AcknowledgeRequest,
    AnswerRejected,
    AutoCompleteRequest,
    ConfirmRequest,
    FieldRequest,
    FormRequest,
    GroupRequest,
    MarkdownRequest,
    MediaRequest,
    NumberRequest,
    PathRequest,
    PickRequest,
    ReadOnlyTable,
    Request,
    TextRequest,
)
from ._session import PromptCancelledError

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


def _stdin_is_tty() -> bool:
    """Whether stdin is an interactive terminal that can be read key by key."""
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _new_prompt(method: Callable[..., _T]) -> Callable[..., _T]:
    """Marks an ``_ask_*`` method as the start of a prompt, discarding any stale cancellation."""

    @functools.wraps(method)
    def wrapper(self: "ConsoleClient", *args, **kwargs) -> _T:
        self._cancel.clear()
        return method(self, *args, **kwargs)

    return wrapper


class _KeyLineReader:
    """A ``readline()`` source for rich prompts built on the cancellable key reader.

    ``input()`` cannot be interrupted from another thread, so on an interactive
    terminal line prompts read key by key instead, echoing as they go, which
    lets :meth:`ConsoleClient.cancel` abort them.
    """

    def __init__(self, console: Console, cancel: threading.Event) -> None:
        self._out = console.file
        self._cancel = cancel

    def _echo(self, text: str) -> None:
        """Writes ``text`` to the console's output, since key-by-key reads do not echo."""
        self._out.write(text)
        self._out.flush()

    def readline(self) -> str:
        """Reads one line, raising if the prompt is cancelled or interrupted."""
        chars: list[str] = []
        while True:
            key = _keys.read_key(self._cancel)
            if key == _keys.CANCELLED:
                self._echo("\n")
                raise PromptCancelledError("Prompt cancelled.")
            if key == _keys.INTERRUPT:
                raise KeyboardInterrupt
            if key == _keys.ENTER:
                self._echo("\n")
                return "".join(chars) + "\n"
            if key == _keys.BACKSPACE:
                if chars:
                    chars.pop()
                    self._echo("\b \b")
            elif len(key) == 1 and key.isprintable():
                chars.append(key)
                self._echo(key)


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


class ConsoleClient:
    """
    A :class:`~clabe.ui.Client` using `rich` for styled, line-based prompts.

    Renders messages and prompts with colour and structure on an interactive terminal while degrading gracefully:
    rich strips styling automatically and falls back to plain ``input`` when output is not a terminal (e.g. piped to
    a file or running in CI), so this also serves as the non-interactive fallback.

    Like every client it never validates: :meth:`ask` returns the raw answer and whoever drives the client (a run,
    or a :class:`~clabe.ui.ClientSession`) validates it and asks again if needed.

    On an interactive terminal every prompt reads the keyboard key by key, so :meth:`cancel` can abort it from
    another thread. When stdin or stdout is not a terminal, prompts fall back to ``input()`` and cannot be cancelled.

    Args:
        console: The rich console to render on. Defaults to the shared ``clabe`` console so output coordinates with
            the logging handler.
    """

    #: The method that asks each primitive prompt kind.
    _HANDLERS: ClassVar[dict[str, str]] = {
        "text": "_ask_text",
        "number": "_ask_number",
        "confirm": "_ask_confirm",
        "pick": "_ask_pick",
        "autocomplete": "_ask_autocomplete",
        "path": "_ask_path",
        "form": "_ask_form",
        "group": "_ask_group",
        "table": "_ask_table",
        "acknowledge": "_ask_acknowledge",
        "markdown": "_ask_markdown",
        "media": "_ask_media",
    }

    def __init__(self, console: Console | None = None) -> None:
        if console is None:
            from ..logging import clabe_console

            console = clabe_console
        self._console = console
        self._cancel = threading.Event()

    # --- Client -------------------------------------------------------------
    def ask(self, request: Request) -> Any:
        """Shows ``request`` and returns the raw answer.

        Raises:
            PromptCancelledError: :meth:`cancel` was called while the prompt was shown.
            ValueError: ``request`` is not a primitive prompt (e.g. an unresolved field request).
        """
        handler = self._HANDLERS.get(request.prompt_kind)
        if handler is None:
            raise ValueError(f"Clients cannot ask {request.prompt_kind!r} prompts; resolve the request first.")
        return getattr(self, handler)(request)

    def cancel(self) -> None:
        """Aborts the waiting prompt; it raises :class:`PromptCancelledError`."""
        self._cancel.set()

    def notify(self, message: str, level: MessageLevel = MessageLevel.INFO) -> None:
        """Prints a message with level-appropriate styling."""
        self._console.print(Text(f"{_LEVEL_PREFIXES[level]}{message}", style=_LEVEL_STYLES[level]))

    def header(self, text: str) -> None:
        """Prints a prominent header as a horizontal rule."""
        self._console.rule(Text(text, style="bold"))

    def close(self) -> None:
        """Nothing to release for a console."""

    def _read_key(self) -> str:
        """Reads one key, raising if the prompt is cancelled or interrupted."""
        key = _keys.read_key(self._cancel)
        if key == _keys.CANCELLED:
            raise PromptCancelledError("Prompt cancelled.")
        if key == _keys.INTERRUPT:
            raise KeyboardInterrupt
        return key

    def _line_stream(self) -> _KeyLineReader | None:
        """The cancellable line source for rich prompts, or ``None`` to use ``input()``."""
        if self._console.is_terminal and _stdin_is_tty():
            return _KeyLineReader(self._console, self._cancel)
        return None

    @_new_prompt
    def _ask_text(self, request: TextRequest) -> str:
        """Collects a single line of text."""
        return Prompt.ask(
            Text(request.label, style="bold"),
            console=self._console,
            stream=self._line_stream(),
            default=request.default if request.default is not None else "",
            show_default=request.default is not None,
        )

    @_new_prompt
    def _ask_number(self, request: NumberRequest) -> str:
        """Collects a number as typed text (validation coerces it)."""
        default = f"{request.default:g}" if request.default is not None else ""
        return Prompt.ask(
            Text(request.label, style="bold"),
            console=self._console,
            stream=self._line_stream(),
            default=default,
            show_default=request.default is not None,
        )

    def _ask_form(self, request: FormRequest) -> dict[str, Any]:
        """Asks each field of the form's model in turn, with the same rules as a single field prompt.

        Each field is checked as it is entered so mistakes are caught early; the whole model is validated again
        by whoever drives the client.
        """
        model = request.model
        self.header(request.title or model.__name__)
        values: dict[str, Any] = {}
        for name in model.model_fields:
            initial = getattr(request.initial, name, None) if request.initial is not None else None
            prompt, convert = FieldRequest(model=model, field_name=name, initial=initial).resolve()
            while True:
                raw = self.ask(prompt)
                try:
                    values[name] = convert(prompt.validate_answer(raw))
                    break
                except AnswerRejected as exc:
                    self.notify(str(exc), MessageLevel.ERROR)
        return values

    def _ask_group(self, request: GroupRequest) -> dict[str, Any]:
        """Asks a group as a form of its compiled model."""
        return self._ask_form(FormRequest(model=request.model(), title=request.title or "Group"))

    def _ask_table(self, request: ReadOnlyTable) -> bool:
        """Prints the table, then asks a yes/no question."""
        table = Table(*(str(column) for column in request.columns), title=request.title)
        for row in request.rows:
            table.add_row(*(str(value) for value in row))
        self._console.print(table)
        label = request.prompt or f"{request.confirm_label}?"
        return self._ask_confirm(ConfirmRequest(label=label, default=True))

    @_new_prompt
    def _ask_pick(self, request: PickRequest) -> str | None:
        """Collects a single choice, using an arrow-key picker on a terminal."""
        if self._console.is_terminal:
            return self._pick_interactive(request)
        return self._pick_numbered(request)

    @_new_prompt
    def _ask_autocomplete(self, request: AutoCompleteRequest) -> str:
        """Collects text, using a live type-to-filter picker on a terminal."""
        if self._console.is_terminal:
            return self._autocomplete_interactive(request)
        return self._autocomplete_listed(request)

    @_new_prompt
    def _ask_path(self, request: PathRequest) -> Path | None:
        """Collects a candidate path, browsing the filesystem live on a terminal."""
        if self._console.is_terminal:
            return self._path_interactive(request)
        return self._path_typed(request)

    # --- interactive (terminal) variants ---------------------------------
    def _pick_interactive(self, request: PickRequest) -> str | None:
        """Renders an arrow-key navigable list and returns the chosen value."""
        rows: list[tuple[str | None, str]] = []
        if request.allow_none:
            rows.append((None, request.none_label))
        rows.extend((choice.value, choice.display) for choice in request.choices())

        index = 0
        if request.default is not None:
            index = next((i for i, (value, _) in enumerate(rows) if value == request.default), 0)

        with Live(self._render_menu(request.label, rows, index), console=self._console, transient=True) as live:
            while True:
                key = self._read_key()
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
                key = self._read_key()
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

    def _path_interactive(self, request: PathRequest) -> Path | None:
        """Renders a live, filesystem-aware browser and returns the typed/completed path.

        The query line behaves like a shell path prompt: typing narrows nothing
        (the filesystem itself is the source of truth), arrow keys cycle which
        entry Tab would complete to, and Tab/→ completes the query into that
        entry (appending a separator on a directory, to invite drilling further
        in). Enter submits the query text as-is; whoever drives the client
        validates it and asks again if it doesn't satisfy the request.
        """
        query = str(request.start or request.default or "") or (str(Path.cwd()) + os.sep)
        index = 0
        suggestions = self._path_completions(query, request)

        with Live(console=self._console, transient=True) as live:
            while True:
                index = max(0, min(index, len(suggestions) - 1))
                live.update(self._render_path_picker(request.label, query, suggestions, index))
                key = self._read_key()
                if key == _keys.ESCAPE:
                    return None
                if key == _keys.ENTER:
                    break
                previous_query = query
                if key in (_keys.TAB, _keys.RIGHT):
                    if suggestions:
                        query = suggestions[index]
                elif key == _keys.UP:
                    index -= 1
                elif key == _keys.DOWN:
                    index += 1
                elif key == _keys.BACKSPACE:
                    query = query[:-1]
                elif len(key) == 1 and key.isprintable():
                    query += key
                if query != previous_query:
                    suggestions = self._path_completions(query, request)

        # An empty query on Enter falls back to the default, matching every other prompt type.
        if not query and request.default is not None:
            query = request.default
        self._console.print(Text(f"{request.label} ", style="bold").append(query, style="cyan"))
        return Path(query) if query else None

    @staticmethod
    def _path_completions(query: str, request: PathRequest, max_results: int = 8) -> list[str]:
        """Returns filesystem entries completing ``query``, filtered by kind/extensions.

        Directories are always offered (they're always valid waypoints while
        browsing) and are suffixed with the path separator; files are offered
        only when ``kind`` allows a file and, if ``extensions`` is set, their
        suffix matches.
        """
        expanded = Path(query).expanduser()
        if query and query[-1] in ("/", os.sep):
            parent, prefix = expanded, ""
        else:
            parent, prefix = expanded.parent, expanded.name.lower()
        if not parent.is_dir():
            return []
        exts = request.normalized_extensions()
        try:
            children = sorted(parent.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))
        except OSError:
            return []
        results: list[str] = []
        for child in children:
            if not child.name.lower().startswith(prefix):
                continue
            if child.is_dir():
                results.append(str(child) + os.sep)
            elif request.kind != "dir" and (not exts or child.suffix.lower() in exts):
                results.append(str(child))
            if len(results) >= max_results:
                break
        return results

    def _render_path_picker(self, label: str, query: str, suggestions: list[str], index: int) -> Group:
        """Builds the renderable for the path browser at the given cursor."""
        lines: list[Text] = [Text(f"{label}: ", style="bold").append(query, style="cyan").append("▏", style="dim")]
        for position, suggestion in enumerate(suggestions):
            glyph = "📁" if suggestion.endswith(os.sep) else "📄"
            style = "bold cyan" if position == index else "none"
            lines.append(Text(f"{'❯ ' if position == index else '  '}{glyph} {suggestion}", style=style))
        return Group(*lines)

    def _path_typed(self, request: PathRequest) -> Path | None:
        """Lists the start directory's contents, then collects a line of typed text.

        A non-interactive console cannot offer a live browser, so the directory
        is shown up front (mirroring :meth:`_autocomplete_listed`) and any typed
        value is returned; whoever drives the client enforces the request's
        constraints.
        """
        start = Path(request.start or request.default or Path.cwd()).expanduser()
        if start.is_dir():
            try:
                entries = sorted(p.name + (os.sep if p.is_dir() else "") for p in start.iterdir())
            except OSError:
                entries = []
            if entries:
                self._console.print(
                    Text(f"Contents of {start}: ", style="dim").append(", ".join(entries), style="cyan")
                )
        default_text = str(request.start) if request.start else (request.default or "")
        raw = Prompt.ask(
            Text(request.label, style="bold"),
            console=self._console,
            stream=self._line_stream(),
            default=default_text,
            show_default=bool(default_text),
        )
        return Path(raw) if raw else None

    @staticmethod
    def _filter(suggestions: list[str], query: str) -> list[str]:
        """Returns the suggestions containing ``query`` (case-insensitive)."""
        if not query:
            return suggestions
        lowered = query.lower()
        return [suggestion for suggestion in suggestions if lowered in suggestion.lower()]

    def _render_menu(self, label: str, rows: list[tuple[str | None, str]], index: int) -> Group:
        """Builds the renderable for the interactive picker at the given cursor."""
        lines: list[Text] = [Text(label, style="bold")]
        for position, (_, display) in enumerate(rows):
            if position == index:
                lines.append(Text(f"❯ {display}", style="bold cyan"))
            else:
                lines.append(Text(f"  {display}", style="none"))
        return Group(*lines)

    def _render_autocomplete(self, label: str, query: str, filtered: list[str], index: int) -> Group:
        """Builds the renderable for the autocomplete prompt and its matches."""
        lines: list[Text] = [Text(f"{label}: ", style="bold").append(query, style="cyan").append("▏", style="dim")]
        for position, suggestion in enumerate(filtered[:_MAX_VISIBLE_SUGGESTIONS]):
            if position == index:
                lines.append(Text(f"❯ {suggestion}", style="bold cyan"))
            else:
                lines.append(Text(f"  {suggestion}", style="none"))
        return Group(*lines)

    # --- non-interactive (piped/CI) fallbacks ----------------------------
    def _pick_numbered(self, request: PickRequest) -> str | None:
        """Displays a styled numbered list and collects a selection."""
        choices = request.choices()
        self._console.print(Text(request.label, style="bold"))

        indices: list[str] = []
        values: dict[str, str | None] = {}
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
            stream=self._line_stream(),
            choices=indices,
            show_choices=False,
        )
        return values[selection]

    def _autocomplete_listed(self, request: AutoCompleteRequest) -> str:
        """Lists the available suggestions, then collects a line of text.

        A non-interactive console cannot offer live, type-to-filter completion,
        so the suggestions are shown up front and any typed value is returned
        (strictness and validators are enforced by whoever drives the client).
        """
        suggestions = request.suggestions()
        if suggestions:
            self._console.print(Text("Suggestions: ", style="dim").append(", ".join(suggestions), style="cyan"))
        return Prompt.ask(
            Text(request.label, style="bold"),
            console=self._console,
            stream=self._line_stream(),
            default=request.default if request.default is not None else "",
            show_default=request.default is not None,
        )

    @_new_prompt
    def _ask_confirm(self, request: ConfirmRequest) -> bool:
        """Collects a yes/no answer."""
        return bool(
            Confirm.ask(
                Text(request.label, style="bold"),
                console=self._console,
                default=request.default,
                stream=self._line_stream(),
            )
        )

    @_new_prompt
    def _ask_acknowledge(self, request: AcknowledgeRequest) -> None:
        """Prints the title and message, then waits for Enter."""
        self._console.print(Text(f"\n{request.title}", style="bold yellow"))
        self._console.print(request.message)
        self._console.input(f"  Press Enter to {request.button_label.lower()}\u2026", stream=self._line_stream())

    @_new_prompt
    def _ask_markdown(self, request: MarkdownRequest) -> None:
        """Prints the document with Rich, then waits for Enter."""
        if request.title:
            self._console.print(Text(f"\n{request.title}", style="bold"))
        self._console.print(Markdown(request.markdown))
        self._console.input(f"  Press Enter to {request.button_label.lower()}\u2026", stream=self._line_stream())

    @_new_prompt
    def _ask_media(self, request: MediaRequest) -> None:
        """Cannot show media: warns, names the file or URL, then waits for Enter."""
        logger.warning("The console cannot show the %s; open it yourself: %s", request.media, request.describe())
        if request.title:
            self._console.print(Text(f"\n{request.title}", style="bold"))
        if request.location != "inline":
            self._console.print(Text(f"  {request.media}: {request.source}", style="yellow"))
        if request.caption:
            self._console.print(request.caption)
        self._console.input(f"  Press Enter to {request.button_label.lower()}\u2026", stream=self._line_stream())
