import dataclasses
from typing import Callable, List, Optional, Sequence, Union

#: A validator takes a candidate answer and returns ``None`` when the value is
#: acceptable, or an error message (to surface to the user) when it is not.
Validator = Callable[[str], Optional[str]]


@dataclasses.dataclass
class Choice:
    """
    A single selectable option in a :class:`PickRequest`.

    Attributes:
        value: The value returned when this option is chosen.
        label: Optional human-readable label. Defaults to ``value``.
    """

    value: str
    label: Optional[str] = None

    @property
    def display(self) -> str:
        """The text shown to the user for this choice."""
        return self.label if self.label is not None else self.value


@dataclasses.dataclass
class PickRequest:
    """
    A declarative request to pick one option from a list.

    Frontends render this however is appropriate (a numbered list on a console,
    a dropdown/option-list in a TUI/GUI, a ``<select>`` on the web). Application
    code only describes *what* it needs, never *how* to ask.

    Attributes:
        label: The prompt shown to the user.
        options: The available options (plain strings or :class:`Choice`).
        default: Value selected by default when the frontend supports it.
        allow_none: Whether a "none" sentinel option is offered.
        none_label: Label for the "none" option when ``allow_none`` is set.
        field: Logical field name used in the persisted transcript.
        help: Optional supplementary help text.
    """

    label: str
    options: Sequence[Union[str, Choice]]
    default: Optional[str] = None
    allow_none: bool = True
    none_label: str = "None"
    field: Optional[str] = None
    help: Optional[str] = None

    def choices(self) -> List[Choice]:
        """Normalize ``options`` into a list of :class:`Choice`."""
        return [opt if isinstance(opt, Choice) else Choice(value=opt) for opt in self.options]


@dataclasses.dataclass
class ConfirmRequest:
    """
    A declarative yes/no question.

    Attributes:
        label: The question shown to the user.
        default: The value used when the user accepts the default.
        field: Logical field name used in the persisted transcript.
    """

    label: str
    default: bool = False
    field: Optional[str] = None


@dataclasses.dataclass
class TextRequest:
    """
    A declarative request for free-form text.

    Attributes:
        label: The prompt shown to the user.
        default: Value returned when the user submits an empty answer.
        multiline: Hint that a multi-line editor is appropriate.
        validators: Validators applied to the answer; the frontend re-prompts
            and surfaces the returned error until they all pass.
        field: Logical field name used in the persisted transcript.
    """

    label: str
    default: Optional[str] = None
    multiline: bool = False
    validators: List[Validator] = dataclasses.field(default_factory=list)
    field: Optional[str] = None


@dataclasses.dataclass
class AutoCompleteRequest:
    """
    A declarative request for text with autocompletion against a list.

    As the user types, frontends narrow the offered options; the user may keep
    typing a free-form value to the end, or pick a suggestion with the arrow
    keys. Unlike :class:`PickRequest`, the answer is not constrained to the
    provided options unless ``strict`` is set.

    Attributes:
        label: The prompt shown to the user.
        options: The suggestions to complete against.
        default: Value returned when the user submits an empty answer.
        strict: When set, only a value present in ``options`` is accepted.
        validators: Validators applied to the answer; the frontend re-prompts
            and surfaces the returned error until they all pass.
        field: Logical field name used in the persisted transcript.
        help: Optional supplementary help text.
    """

    label: str
    options: Sequence[str]
    default: Optional[str] = None
    strict: bool = False
    validators: List[Validator] = dataclasses.field(default_factory=list)
    field: Optional[str] = None
    help: Optional[str] = None

    def suggestions(self) -> List[str]:
        """Return ``options`` normalized to a list of strings."""
        return [str(option) for option in self.options]


@dataclasses.dataclass
class NumberRequest:
    """
    A declarative request for a floating-point number.

    Attributes:
        label: The prompt shown to the user.
        default: Value returned when the user submits an empty answer.
        field: Logical field name used in the persisted transcript.
    """

    label: str
    default: Optional[float] = None
    field: Optional[str] = None
