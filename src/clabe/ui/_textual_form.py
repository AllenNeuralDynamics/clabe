import logging
import os
import re
from collections.abc import Callable, Sequence
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, cast, get_args, get_origin

from pydantic import TypeAdapter, ValidationError
from pydantic_core import PydanticUndefined
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.css.query import QueryError
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Label, Markdown, OptionList, Select, Switch
from textual_fspicker import FileOpen, Filters, SelectDirectory

from ._requests import (
    AcknowledgeRequest,
    FormRequest,
    MarkdownRequest,
    MediaRequest,
    ReadOnlyTable,
    normalize_extensions,
)
from ._validation import resolve_field_type

logger = logging.getLogger(__name__)


def _humanize(name: str) -> str:
    """Convert snake_case or CamelCase to Title Case words."""
    name = re.sub(r"(?<=[a-z0-9])([A-Z])", r" \1", name)
    return name.replace("_", " ").replace("-", " ").title()


def _field_label(field_name: str, field_info: Any, is_optional: bool) -> str:
    """Return a human-readable label, appending '*' for required fields."""
    base = getattr(field_info, "title", None) or _humanize(field_name)
    required = not is_optional and getattr(field_info, "is_required", lambda: False)()
    return f"{base} *" if required else base


def _field_default(field_name: str, field_info: Any, initial: Any) -> Any:
    """Return the value from initial instance (preferred) or the field's declared default."""
    if initial is not None:
        try:
            return getattr(initial, field_name)
        except AttributeError:
            pass
    default = getattr(field_info, "default", PydanticUndefined)
    if default is not PydanticUndefined:
        return default
    factory = getattr(field_info, "default_factory", None)
    return factory() if factory is not None else None


def _build_field_widget(field_name: str, inner_type: Any, initial: Any) -> Any:
    """Create the appropriate Textual widget for a Pydantic field type."""
    wid = f"field-{field_name}"
    if inner_type is bool:
        return Switch(value=bool(initial) if initial is not None else False, id=wid)
    if get_origin(inner_type) is Literal:
        opts = [(str(v), v) for v in get_args(inner_type)]
        return Select(opts, value=initial if initial is not None else Select.NULL, id=wid, allow_blank=True)
    if isinstance(inner_type, type) and issubclass(inner_type, Enum):
        opts = [(m.name, m) for m in inner_type]
        return Select(opts, value=initial if initial is not None else Select.NULL, id=wid, allow_blank=True)
    if isinstance(inner_type, type) and issubclass(inner_type, Path):
        return Input(value=str(initial) if initial is not None else "", id=wid, placeholder="Enter path…")
    if inner_type is int:
        return Input(
            value=str(initial) if initial is not None else "", id=wid, restrict=r"-?[0-9]*", placeholder="Integer"
        )
    if inner_type is float:
        return Input(
            value=str(initial) if initial is not None else "",
            id=wid,
            restrict=r"-?[0-9]*\.?[0-9]*",
            placeholder="Number",
        )
    return Input(value=str(initial) if initial is not None else "", id=wid)


def _read_field_widget(widget: Any, is_optional: bool) -> Any:
    """Read the current value from a field widget as a Python object."""
    if isinstance(widget, Switch):
        return widget.value
    if isinstance(widget, Select):
        v = widget.value
        return None if v is Select.NULL else v
    if isinstance(widget, Input):
        raw = widget.value.strip()
        return None if raw == "" else raw
    return None


def _path_completions(partial: str, max_results: int = 12) -> list[str]:
    """Return filesystem paths completing partial, up to max_results entries."""
    if not partial:
        return []
    p = Path(partial)
    try:
        if partial[-1] in ("/", "\\"):
            parent, prefix = p, ""
        else:
            parent, prefix = p.parent, p.name.lower()
        if not parent.exists():
            return []
        return sorted(str(child) for child in parent.iterdir() if child.name.lower().startswith(prefix))[:max_results]
    except (PermissionError, OSError):
        return []


def _resolve_existing_ancestor(candidate: Path) -> Path:
    """Walk up from an already-resolved ``candidate`` until it names an existing directory."""
    while not candidate.is_dir() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.is_dir() else Path.home()


def _resolve_start_dir(raw: str) -> Path:
    """Walk up from raw until we reach an existing directory (for DirectoryTree)."""
    candidate = Path(raw).expanduser().resolve() if raw else Path.home()
    return _resolve_existing_ancestor(candidate)


_FORM_CSS = """
_FormScreen { align: center middle; }
#form-outer { width: 80%; height: 80%; background: $surface; border: round $primary; }
#form-header { height: 3; padding: 0 1; border-bottom: solid $primary; align-vertical: middle; }
#form-title { width: 1fr; color: $accent; text-style: bold; content-align: left middle; }
#form-close { width: auto; min-width: 5; }
#form-scroll { height: 1fr; padding: 1 2; }
.form-field { height: auto; margin-bottom: 1; }
.field-label { height: 1; }
.field-error { display: none; color: $error; height: 1; padding: 0; margin: 0; }
.field-error.--active { display: block; }
.path-row { height: 3; }
.path-row Input { width: 1fr; height: 3; }
.browse-btn { width: 12; min-width: 12; margin-left: 1; height: 3; }
.path-complete { display: none; max-height: 8; border: none; padding: 0; margin: 0; }
.path-complete.--active { display: block; }
"""

_HELP_POPUP_CSS = """
_HelpPopup { align: center middle; }
#help-box { width: 60; height: auto; background: $surface; border: round $accent; padding: 1 2; }
#help-title { color: $accent; text-style: bold; margin-bottom: 1; }
#help-body { margin-bottom: 1; }
#help-ok { width: 100%; }
"""

_READ_ONLY_TABLE_CSS = """
_ReadOnlyTableScreen { align: center middle; }
#table-outer {
    width: auto; height: auto;
    max-width: 90vw; max-height: 90vh;
    background: $surface; border: round $primary;
    align-horizontal: center; padding: 1 2;
}
#table-title { width: auto; color: $accent; text-style: bold; margin-bottom: 1; }
#table-prompt { width: auto; margin-bottom: 1; }
#table-data { width: auto; height: auto; max-width: 86vw; max-height: 78vh; }
#table-buttons { width: auto; min-width: 100%; height: 3; margin-top: 1; align-horizontal: center; }
#table-buttons Button { margin: 0 1; }
"""

_ACKNOWLEDGE_CSS = """
_AcknowledgeScreen { align: center middle; }
#ack-box {
    width: 60; height: auto; max-width: 90%;
    background: $surface; border: thick $warning; padding: 1 2;
}
#ack-title  { color: $warning; text-style: bold; margin-bottom: 1; }
#ack-body   { margin-bottom: 1; }
#ack-markdown { height: auto; max-height: 30; margin-bottom: 1; }
_AcknowledgeScreen.wide #ack-box { width: 100; }
#ack-ok     { width: 100%; }
"""


class _HelpPopup(ModalScreen):
    """Field-help dialog shown on F1; dismissed with Enter, Space, Escape, or F1."""

    DEFAULT_CSS = _HELP_POPUP_CSS
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter", "ok", "", show=False),
        Binding("space", "ok", "", show=False),
        Binding("escape", "ok", "", show=False),
        Binding("f1", "ok", "", show=False),
    ]

    def __init__(self, title: str, body: str) -> None:
        """Initialize with field title and description text."""
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        """Build the help popup layout."""
        with Vertical(id="help-box"):
            yield Label(self._title, id="help-title")
            yield Label(self._body, id="help-body")
            yield Button("OK  [Enter]", id="help-ok", variant="primary")

    async def on_button_pressed(self, _: Button.Pressed) -> None:
        """Dismiss the popup on OK button press."""
        self.dismiss()

    def action_ok(self) -> None:
        """Dismiss the popup."""
        self.dismiss()


class _AcknowledgeScreen(ModalScreen):
    """Modal acknowledgement dialog — blocks until the user dismisses it."""

    DEFAULT_CSS = _ACKNOWLEDGE_CSS
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter", "ok", "", show=False),
        Binding("space", "ok", "", show=False),
        Binding("escape", "ok", "", show=False),
    ]

    def __init__(self, request: AcknowledgeRequest | MarkdownRequest | MediaRequest) -> None:
        """Initialize with the request to show: a message, a Markdown document, or media the TUI cannot show."""
        super().__init__(classes="wide" if isinstance(request, MarkdownRequest) else "")
        self._request = request

    def compose(self) -> ComposeResult:
        """Build the acknowledge dialog layout."""
        request = self._request
        with Vertical(id="ack-box"):
            if request.title:
                yield Label(request.title, id="ack-title")
            if isinstance(request, MarkdownRequest):
                yield Markdown(request.markdown, id="ack-markdown")
            elif isinstance(request, MediaRequest):
                shown = "" if request.location == "inline" else f": {request.source}"
                text = f"The terminal cannot show this {request.media}{shown}"
                yield Label(f"{text}\n\n{request.caption}" if request.caption else text, id="ack-body")
            else:
                yield Label(request.message, id="ack-body")
            yield Button(
                f"{self._request.button_label}  [Enter]",
                id="ack-ok",
                variant="warning",
            )

    async def on_button_pressed(self, _: Button.Pressed) -> None:
        """Dismiss on button press."""
        self.dismiss()

    def action_ok(self) -> None:
        """Dismiss the dialog."""
        self.dismiss()


def _cell(value: Any) -> str:
    """Render a cell value as display text (``None`` becomes an empty cell)."""
    return "" if value is None else str(value)


class _ReadOnlyTableScreen(ModalScreen):
    """Modal that displays read-only tabular data and collects an OK/Cancel answer."""

    DEFAULT_CSS = _READ_ONLY_TABLE_CSS
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, request: ReadOnlyTable) -> None:
        """Initialize with the declarative read-only-table request."""
        super().__init__()
        self._request = request
        self._title_text = request.title or "Review"

    def compose(self) -> ComposeResult:
        """Build the table dialog layout."""
        with Vertical(id="table-outer"):
            yield Label(self._title_text, id="table-title")
            if self._request.prompt:
                yield Label(self._request.prompt, id="table-prompt")
            yield DataTable(id="table-data", cursor_type="row", zebra_stripes=True)
            with Horizontal(id="table-buttons"):
                yield Button(self._request.cancel_label, id="table-cancel", variant="default")
                yield Button(self._request.confirm_label, id="table-ok", variant="primary")

    def on_mount(self) -> None:
        """Populate the table and focus the confirm button."""
        table = self.query_one("#table-data", DataTable)
        table.add_columns(*[str(column) for column in self._request.columns])
        for row in self._request.rows:
            table.add_row(*[_cell(value) for value in row])
        self.query_one("#table-ok", Button).focus()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dismiss True on the confirm button, False on cancel."""
        btn_id = event.button.id or ""
        if btn_id == "table-ok":
            self.dismiss(True)
        elif btn_id == "table-cancel":
            self.dismiss(False)

    def action_cancel(self) -> None:
        """Dismiss the dialog with a negative answer."""
        self.dismiss(False)


def build_picker_screen(
    *,
    label: str,
    start: str | os.PathLike | None,
    kind: Literal["file", "dir"] = "file",
    extensions: Sequence[str] | None = None,
    must_exist: bool = True,
) -> FileOpen | SelectDirectory:
    """Builds the ``textual-fspicker`` dialog matching a path request.

    ``textual-fspicker`` (unlike a plain ``DirectoryTree``-based browser) lets
    the user navigate anywhere on disk regardless of ``start`` — Backspace
    walks up past it, and on Windows a drive selector sits alongside the
    listing — which is what makes this feel like a native file-open dialog
    rather than a browser confined to one subtree.

    ``kind`` must already be resolved to ``"file"`` or ``"dir"`` — a
    ``PathRequest(kind="any")`` is disambiguated one level up, by
    :func:`push_path_picker`, before this is called. There's no single
    ``textual-fspicker`` dialog that can return either: ``FileOpen`` treats
    picking a directory as "cd into it" and never dismisses with one.

    Args:
        label: Shown as the dialog's title.
        start: Directory (or a not-yet-existing path under one) to open the
            browser in. ``textual-fspicker`` lists ``location`` unconditionally
            and crashes if it doesn't exist, so a ``start`` that doesn't exist
            (a suggested default like ``Path("./local/output")`` before it's
            been created) is walked up to its nearest existing ancestor; the
            missing leaf is preserved as a pre-filled filename instead of
            silently dropped.
        kind: ``"dir"`` opens a directory-only picker; ``"file"`` opens a file
            picker (typing a directory path just navigates into it).
        extensions: When set (and ``kind`` isn't ``"dir"``), only files with
            one of these suffixes are selectable. Matching is case-insensitive
            and tolerates a missing leading dot.
        must_exist: Whether the chosen file must already exist. Directory
            selection is always constrained to existing directories, since
            there's no "new folder" affordance in the dialog.

    Returns:
        FileOpen | SelectDirectory: The modal screen to push.
    """
    raw = str(start) if start else ""
    resolved_raw = Path(raw).expanduser().resolve() if raw else None
    location = _resolve_existing_ancestor(resolved_raw) if resolved_raw else Path.home()
    if kind == "dir":
        return SelectDirectory(location=location, title=label)

    # If `raw` names something under `location` that doesn't exist yet, keep
    # its name as a suggested filename rather than discarding it.
    default_file = resolved_raw.name if resolved_raw is not None and resolved_raw != location else None

    filters = None
    allowed = normalize_extensions(extensions)
    if allowed:
        filters = Filters(("Allowed files", lambda p: p.suffix.lower() in allowed))
    return FileOpen(
        location=location,
        title=label,
        filters=filters,
        must_exist=must_exist,
        default_file=default_file,
    )


class _PathKindScreen(ModalScreen):
    """Tiny modal disambiguating a ``PathRequest(kind="any")`` into "file" or "dir".

    No ``textual-fspicker`` dialog can return either a file or a directory
    (``FileOpen`` always treats a directory as "cd into it", never as an
    answer), so this asks the one extra question needed to pick the right
    dialog rather than silently guessing.
    """

    DEFAULT_CSS = """
    _PathKindScreen { align: center middle; }
    #kind-box { width: 50; height: auto; background: $surface; border: round $primary; padding: 1 2; }
    #kind-buttons { height: 3; margin-top: 1; align-horizontal: center; }
    #kind-buttons Button { margin: 0 1; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, label: str) -> None:
        """Initialize with the label shown above the File/Folder/Cancel buttons."""
        super().__init__()
        self._label = label

    def compose(self) -> ComposeResult:
        """Build the tiny choice dialog."""
        with Vertical(id="kind-box"):
            yield Label(self._label)
            with Horizontal(id="kind-buttons"):
                yield Button("File", id="kind-file", variant="primary")
                yield Button("Folder", id="kind-dir", variant="primary")
                yield Button("Cancel", id="kind-cancel", variant="default")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dismiss with the chosen kind (``"file"``/``"dir"``), or ``None`` on Cancel."""
        if event.button.id == "kind-file":
            self.dismiss("file")
        elif event.button.id == "kind-dir":
            self.dismiss("dir")
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        """Dismiss without choosing a kind."""
        self.dismiss(None)


async def push_path_picker(
    pusher: Any,
    *,
    label: str,
    start: str | os.PathLike | None,
    kind: Literal["file", "dir", "any"],
    extensions: Sequence[str] | None,
    must_exist: bool,
    on_result: Callable[[Path | None], None],
) -> None:
    """Pushes the picker screen(s) matching ``kind``, delivering the answer to ``on_result``.

    For ``kind="file"``/``"dir"`` this is just :func:`build_picker_screen`
    pushed directly. For ``kind="any"`` -- used for a generic ``Path``-typed
    field, where neither "file" nor "dir" is known -- a tiny disambiguation
    modal (:class:`_PathKindScreen`) is pushed first, since no single
    ``textual-fspicker`` dialog can return either. ``on_result`` fires exactly
    once with the final path or ``None``, however many screens it took.

    Args:
        pusher: The ``App`` or ``Screen`` to push onto (anything exposing
            ``push_screen``).
        label: Shown as the dialog's title (and the disambiguation prompt's
            label, for ``kind="any"``).
        start: Forwarded to :func:`build_picker_screen`.
        kind: ``"file"``, ``"dir"``, or ``"any"``.
        extensions: Forwarded to :func:`build_picker_screen`.
        must_exist: Forwarded to :func:`build_picker_screen`.
        on_result: Called exactly once with the chosen ``Path``, or ``None``
            if the user cancelled at any point.
    """

    async def _push(resolved_kind: Literal["file", "dir"]) -> None:
        """Build and push the concrete file/dir dialog for ``resolved_kind``."""

        async def _on_dismiss(result: Path | None) -> None:
            """Forward the dialog's result to the caller."""
            on_result(result)

        screen = build_picker_screen(
            label=label, start=start, kind=resolved_kind, extensions=extensions, must_exist=must_exist
        )
        await pusher.push_screen(screen, _on_dismiss)

    if kind == "file" or kind == "dir":
        await _push(kind)
        return

    async def _on_kind_chosen(chosen: str | None) -> None:
        """Push the picker for the chosen kind, or report cancellation."""
        if chosen != "file" and chosen != "dir":
            on_result(None)
            return
        await _push(cast(Literal["file", "dir"], chosen))

    await pusher.push_screen(_PathKindScreen(label), _on_kind_chosen)


class _FormScreen(ModalScreen):
    """Modal form that renders and validates a Pydantic BaseModel."""

    DEFAULT_CSS = _FORM_CSS
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("f1", "show_help", "Field help"),
        Binding("f5", "submit_form", "Submit"),
        Binding("escape", "close_form", "Close"),
    ]

    def __init__(self, request: FormRequest) -> None:
        """Initialize with the form request."""
        super().__init__()
        self._model = request.model
        self._title_text = request.title or _humanize(request.model.__name__)
        self._initial = request.initial
        self._field_widgets: dict[str, Any] = {}

    def compose(self) -> ComposeResult:
        """Build the form layout with one row per model field."""
        with Vertical(id="form-outer"):
            with Horizontal(id="form-header"):
                yield Label(self._title_text, id="form-title")
                yield Button("✕", id="form-close", variant="default")
            with ScrollableContainer(id="form-scroll"):
                for field_name, field_info in self._model.model_fields.items():
                    inner_type, is_optional = resolve_field_type(field_info.annotation)
                    default = _field_default(field_name, field_info, self._initial)
                    widget = _build_field_widget(field_name, inner_type, default)
                    is_path = isinstance(inner_type, type) and issubclass(inner_type, Path)
                    with Vertical(classes="form-field"):
                        yield Label(_field_label(field_name, field_info, is_optional), classes="field-label")
                        if is_path:
                            with Horizontal(classes="path-row"):
                                yield widget
                                yield Button("Browse…", id=f"browse-{field_name}", classes="browse-btn")
                            yield OptionList(id=f"complete-{field_name}", classes="path-complete")
                        else:
                            yield widget
                        yield Label("", id=f"error-{field_name}", classes="field-error")
            yield Footer()

    def on_mount(self) -> None:
        """Populate field widget references and focus the first field."""
        self._field_order = list(self._model.model_fields.keys())
        self._path_fields: set[str] = set()
        for name, info in self._model.model_fields.items():
            inner, _ = resolve_field_type(info.annotation)
            if isinstance(inner, type) and issubclass(inner, Path):
                self._path_fields.add(name)
            try:
                self._field_widgets[name] = self.query_one(f"#field-{name}")
            except QueryError as exc:
                logger.debug("No widget mounted for field %r: %s", name, exc)
        if self._field_order and (first := self._field_widgets.get(self._field_order[0])):
            first.focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Validate on Enter and advance focus; only F5 submits the form."""
        event.stop()
        wid = event.input.id or ""
        if not wid.startswith("field-"):
            return
        field_name = wid[len("field-") :]
        if self._validate_field(field_name, event.value):
            self._focus_next_field(field_name)

    def _focus_next_field(self, current: str) -> None:
        """Move focus to the field after current."""
        try:
            idx = self._field_order.index(current)
        except ValueError:
            return
        if (idx + 1) < len(self._field_order):
            widget = self._field_widgets.get(self._field_order[idx + 1])
            if widget is not None:
                widget.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Clear the inline error and refresh path completions as the user edits."""
        wid = event.input.id or ""
        if not wid.startswith("field-"):
            return
        field_name = wid[len("field-") :]
        self._clear_field_error(field_name)
        if field_name in self._path_fields:
            self._update_path_completions(field_name, event.value)

    def _update_path_completions(self, field_name: str, partial: str) -> None:
        """Refresh the path completion list for a path field."""
        try:
            opts = self.query_one(f"#complete-{field_name}", OptionList)
        except QueryError:
            return
        completions = _path_completions(partial)
        opts.clear_options()
        if completions:
            opts.add_options(completions)
            opts.add_class("--active")
        else:
            opts.remove_class("--active")

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Fill the path input when a completion is selected."""
        opt_id = event.option_list.id or ""
        if not opt_id.startswith("complete-"):
            return
        field_name = opt_id[len("complete-") :]
        path_input = self._field_widgets.get(field_name)
        if not isinstance(path_input, Input):
            return
        selected = str(event.option.prompt)
        if Path(selected).is_dir():
            selected += os.sep
        path_input.value = selected
        path_input.focus()

    def _show_field_error(self, field_name: str, message: str) -> None:
        """Show an inline error message below a field."""
        try:
            lbl = self.query_one(f"#error-{field_name}", Label)
            lbl.update(f"⚠ {message}")
            lbl.add_class("--active")
        except QueryError as exc:
            logger.debug("No error label mounted for field %r: %s", field_name, exc)

    def _clear_field_error(self, field_name: str) -> None:
        """Clear the inline error label for a field."""
        try:
            lbl = self.query_one(f"#error-{field_name}", Label)
            lbl.update("")
            lbl.remove_class("--active")
        except QueryError as exc:
            logger.debug("No error label mounted for field %r: %s", field_name, exc)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle Browse and close button presses."""
        btn_id = event.button.id or ""
        if btn_id.startswith("browse-"):
            field_name = btn_id[len("browse-") :]
            path_input = self.query_one(f"#field-{field_name}", Input)
            start = _resolve_start_dir(path_input.value.strip())

            def _on_pick(result: Path | None) -> None:
                """Apply the file picker result to the path input."""
                if result is not None:
                    path_input.value = str(result)

            # kind="any": a plain Pydantic `Path` field doesn't say whether it
            # names a file or a directory, so this asks (see push_path_picker).
            await push_path_picker(
                self.app,
                label="Browse",
                start=str(start),
                kind="any",
                extensions=None,
                must_exist=False,
                on_result=_on_pick,
            )
        elif btn_id == "form-close":
            self.dismiss(None)

    async def action_submit_form(self) -> None:
        """Submit the form."""
        await self._submit()

    def action_close_form(self) -> None:
        """Close the form without submitting."""
        self.dismiss(None)

    def _current_field_name(self) -> str | None:
        """Return the field name whose widget currently has focus, or None."""
        focused = self.focused
        if focused is None:
            return None
        fid = getattr(focused, "id", None) or ""
        return fid[len("field-") :] if fid.startswith("field-") else None

    def action_show_help(self) -> None:
        """Show the F1 help popup for the focused field."""
        name = self._current_field_name()
        if name is None:
            return
        fi = self._model.model_fields.get(name)
        if fi is None:
            return
        label = getattr(fi, "title", None) or _humanize(name)
        desc = getattr(fi, "description", None) or "No description available for this field."
        self.app.push_screen(_HelpPopup(label, desc))

    def _validate_field(self, field_name: str, raw: str) -> bool:
        """Validate a single field inline; show an error and return False if invalid."""
        field_info = self._model.model_fields.get(field_name)
        if field_info is None:
            return True
        base_annotation = field_info.annotation
        _, is_optional = resolve_field_type(base_annotation)
        annotation = (
            Annotated[tuple([base_annotation] + list(field_info.metadata))] if field_info.metadata else base_annotation
        )
        val: Any = None if (is_optional and raw.strip() == "") else raw.strip()
        label = getattr(field_info, "title", None) or _humanize(field_name)
        try:
            TypeAdapter(annotation).validate_python(val)
            self._clear_field_error(field_name)
            return True
        except ValidationError as exc:
            errs = exc.errors()
            msg = errs[0]["msg"] if errs else "Invalid value"
            self._show_field_error(field_name, f"{label}: {msg}")
            widget = self._field_widgets.get(field_name)
            if widget is not None:
                widget.focus()
            return False

    async def _submit(self) -> None:
        """Collect all field values, validate the model, and dismiss or show errors."""
        for field_name in self._field_order:
            self._clear_field_error(field_name)
        data: dict[str, Any] = {}
        for field_name, field_info in self._model.model_fields.items():
            _, is_optional = resolve_field_type(field_info.annotation)
            widget = self._field_widgets.get(field_name)
            data[field_name] = _read_field_widget(widget, is_optional) if widget is not None else None
        try:
            self.dismiss(self._model.model_validate(data))
        except ValidationError as exc:
            errors = exc.errors()
            if errors:
                first = errors[0]
                loc = first.get("loc", ())
                msg = first.get("msg", "Invalid value")
                field_name = str(loc[0]) if loc else ""
                if field_name:
                    fi = self._model.model_fields.get(field_name)
                    label = (getattr(fi, "title", None) or _humanize(field_name)) if fi else field_name
                    self._show_field_error(field_name, f"{label}: {msg}")
                    widget = self._field_widgets.get(field_name)
                    if widget is not None:
                        widget.focus()
