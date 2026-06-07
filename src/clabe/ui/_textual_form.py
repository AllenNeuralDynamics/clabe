import os
import re
import typing
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, get_args, get_origin

from pydantic import TypeAdapter, ValidationError
from pydantic_core import PydanticUndefined
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Footer, Input, Label, OptionList, Select, Switch

from ._requests import FormRequest


def _humanize(name: str) -> str:
    """Convert snake_case or CamelCase to Title Case words."""
    name = re.sub(r"(?<=[a-z0-9])([A-Z])", r" \1", name)
    return name.replace("_", " ").replace("-", " ").title()


def _resolve_form_type(annotation: Any) -> tuple[Any, bool]:
    """Return (inner_type, is_optional) after stripping Annotated and Optional wrappers."""
    if get_origin(annotation) is typing.Annotated:
        annotation = get_args(annotation)[0]
    if get_origin(annotation) is typing.Union:
        args = get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0], True
    return annotation, False


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


def _resolve_start_dir(raw: str) -> Path:
    """Walk up from raw until we reach an existing directory (for DirectoryTree)."""
    candidate = Path(raw).expanduser().resolve() if raw else Path.home()
    while not candidate.is_dir() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.is_dir() else Path.home()


_FILE_PICKER_CSS = """
_FilePickerScreen { align: center middle; }
#picker-container {
    width: 80%; height: 80%;
    background: $surface; border: thick $primary; padding: 1 2;
}
#picker-path { margin-bottom: 1; }
#picker-tree { height: 1fr; border: round $primary; }
#picker-buttons { height: 3; margin-top: 1; align-horizontal: right; }
#picker-buttons Button { margin-left: 1; }
"""

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


class _HelpPopup(ModalScreen):
    """Field-help dialog shown on F1; dismissed with Enter, Space, Escape, or F1."""

    DEFAULT_CSS = _HELP_POPUP_CSS
    BINDINGS = [
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


class _FilePickerScreen(ModalScreen):
    """Modal for browsing and selecting a filesystem path."""

    DEFAULT_CSS = _FILE_PICKER_CSS
    BINDINGS = [Binding("escape", "cancel_picker", "Cancel")]

    def __init__(self, start: str = "") -> None:
        """Initialize with the directory to open first."""
        super().__init__()
        self._start = start or str(Path.home())

    def compose(self) -> ComposeResult:
        """Build the file browser layout."""
        with Vertical(id="picker-container"):
            yield Label("Browse", id="picker-title")
            yield Input(value=self._start, id="picker-path", placeholder="Type a path or browse below…")
            yield DirectoryTree(self._start, id="picker-tree")
            with Horizontal(id="picker-buttons"):
                yield Button("Cancel", id="picker-cancel", variant="default")
                yield Button("Select", id="picker-select", variant="primary")

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        """Update the path input when a file is selected in the tree."""
        self.query_one("#picker-path", Input).value = str(event.path)

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        """Update the path input when a directory is selected in the tree."""
        self.query_one("#picker-path", Input).value = str(event.path)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dismiss with the typed path on Select, or None on Cancel."""
        if event.button.id == "picker-select":
            raw = self.query_one("#picker-path", Input).value.strip()
            self.dismiss(Path(raw) if raw else None)
        elif event.button.id == "picker-cancel":
            self.dismiss(None)

    def action_cancel_picker(self) -> None:
        """Dismiss without selecting a path."""
        self.dismiss(None)


class _FormScreen(ModalScreen):
    """Modal form that renders and validates a Pydantic BaseModel."""

    DEFAULT_CSS = _FORM_CSS
    BINDINGS = [
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
                    inner_type, is_optional = _resolve_form_type(field_info.annotation)
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
            inner, _ = _resolve_form_type(info.annotation)
            if isinstance(inner, type) and issubclass(inner, Path):
                self._path_fields.add(name)
            try:
                self._field_widgets[name] = self.query_one(f"#field-{name}")
            except Exception:
                pass
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
        except Exception:
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
        except Exception:
            pass

    def _clear_field_error(self, field_name: str) -> None:
        """Clear the inline error label for a field."""
        try:
            lbl = self.query_one(f"#error-{field_name}", Label)
            lbl.update("")
            lbl.remove_class("--active")
        except Exception:
            pass

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle Browse and close button presses."""
        btn_id = event.button.id or ""
        if btn_id.startswith("browse-"):
            field_name = btn_id[len("browse-") :]
            path_input = self.query_one(f"#field-{field_name}", Input)
            start = _resolve_start_dir(path_input.value.strip())

            def _on_pick(result: Optional[Path]) -> None:
                """Apply the file picker result to the path input."""
                if result is not None:
                    path_input.value = str(result)

            self.app.push_screen(_FilePickerScreen(str(start)), _on_pick)
        elif btn_id == "form-close":
            self.dismiss(None)

    async def action_submit_form(self) -> None:
        """Submit the form."""
        await self._submit()

    def action_close_form(self) -> None:
        """Close the form without submitting."""
        self.dismiss(None)

    def _current_field_name(self) -> Optional[str]:
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
        _, is_optional = _resolve_form_type(base_annotation)
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
            _, is_optional = _resolve_form_type(field_info.annotation)
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
