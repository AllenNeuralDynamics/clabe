"""Static checks of workflows — wiring and types — without running any step (for CI)."""

import dataclasses
import inspect
import types
import typing
from pathlib import Path
from typing import Any, Literal

from ._discovery import collect_workflows, load_module
from ._workflow import Workflow, WorkflowDefinitionError

Severity = Literal["error", "warning"]

_UNKNOWN: Any = object()
#: Implicit numeric conversions type checkers accept (PEP 484 numeric tower).
_NUMERIC: dict[type, tuple[type, ...]] = {float: (int,), complex: (int, float)}


@dataclasses.dataclass(frozen=True)
class Issue:
    """A problem found by a static check.

    Attributes:
        severity: ``"error"`` always fails a check; ``"warning"`` fails only in strict mode.
        message: What is wrong and how to fix it.
        workflow: The workflow concerned, if any.
        step: The step concerned, if any.
        source: The file checked, when the issue comes from `check_path`.
    """

    severity: Severity
    message: str
    workflow: str | None = None
    step: str | None = None
    source: str | None = None

    def __str__(self) -> str:
        where = ".".join(part for part in (self.workflow, self.step) if part)
        prefix = ": ".join(part for part in (self.source, where) if part)
        return f"{prefix}: {self.severity}: {self.message}" if prefix else f"{self.severity}: {self.message}"


def _name(annotation: Any) -> str:
    """A short, readable name for a type annotation."""
    if annotation is type(None):
        return "None"
    if (isinstance(annotation, type) or hasattr(annotation, "__supertype__")) and not typing.get_args(annotation):
        return annotation.__name__
    return str(annotation).replace("typing.", "").replace("NoneType", "None")


def _members(annotation: Any) -> tuple[Any, ...]:
    """The members of a union (flattened, ``Annotated`` stripped); a single annotation otherwise."""
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        return _members(typing.get_args(annotation)[0])
    if origin in (typing.Union, types.UnionType):
        return tuple(member for arg in typing.get_args(annotation) for member in _members(arg))
    return (annotation,)


def _as_class(annotation: Any) -> Any:
    """The runtime class an annotation stands for: generics compare by their origin, NewTypes by their supertype."""
    if annotation is None:
        return type(None)
    while hasattr(annotation, "__supertype__"):
        annotation = annotation.__supertype__
    return typing.get_origin(annotation) or annotation


def _member_fits(produced: Any, expected: tuple[Any, ...]) -> bool | None:
    """Whether one produced type fits any of the expected members; ``None`` when undecidable."""
    produced_cls = _as_class(produced)
    undecided = False
    for candidate in expected:
        expected_cls = _as_class(candidate)
        if expected_cls is Any:
            return True
        if not (isinstance(produced_cls, type) and isinstance(expected_cls, type)):
            undecided = True
            continue
        if produced_cls in _NUMERIC.get(expected_cls, ()):
            return True
        try:
            if issubclass(produced_cls, expected_cls):
                return True
        except TypeError:  # e.g. non-runtime protocols
            undecided = True
    return None if undecided else False


def _fits(produced: Any, expected: Any) -> bool | None:
    """Whether a value typed ``produced`` fits a parameter typed ``expected``; ``None`` when undecidable."""
    expected_members = _members(expected)
    if Any in expected_members:
        return True
    verdicts = []
    for member in _members(produced):
        if member is Any:
            return None
        verdicts.append(_member_fits(member, expected_members))
    if False in verdicts:
        return False
    return None if None in verdicts else True


def _resolve(func: Any, names: typing.Iterable[str]) -> tuple[dict[str, Any], dict[str, Exception]]:
    """Resolves the annotations of ``names`` (parameters or ``"return"``) one by one.

    Annotations are read from ``func`` and evaluated in the globals of the function it wraps, if any. Each is
    evaluated on its own, so an unresolvable annotation elsewhere (e.g. ``launcher: Launcher`` imported only
    for type checkers) does not hide the others. Callables that are not plain functions are treated as unannotated.

    Returns:
        The resolved annotations, and the error raised by each annotation that cannot be resolved.
    """
    if not (inspect.isfunction(func) or inspect.ismethod(func)):
        return {}, {}
    raw = inspect.get_annotations(func)
    globalns = getattr(inspect.unwrap(func), "__globals__", {})  # a decorated step's own module, not the decorator's
    hints: dict[str, Any] = {}
    errors: dict[str, Exception] = {}
    for name in names:
        if name not in raw:
            continue
        try:
            holder = types.SimpleNamespace(__annotations__={name: raw[name]})
            hints[name] = typing.get_type_hints(holder, globalns=globalns)[name]
        except Exception as exc:  # noqa: BLE001 -- any resolution failure is reported, not raised
            errors[name] = exc
    return hints, errors


def check_workflow(workflow: Workflow, *, strict: bool = False) -> list[Issue]:
    """Checks a workflow's wiring against its type annotations, without running it.

    Built-in parameters (``launcher``, ``run``) are never checked. Generics are compared by their origin only
    (``list[int]`` fits ``list[str]``).

    Args:
        workflow: The workflow to check.
        strict: Also report unannotated or undecidable wiring as warnings.

    Returns:
        The issues found (empty when the workflow is fine).
    """
    issues: list[Issue] = []
    produced: dict[str, Any] = {}
    declared: dict[str, Any] = {}
    unresolved_returns: set[str] = set()  # already reported as errors; not worth a strict warning downstream
    for step in workflow.steps:

        def report(severity: Severity, message: str, step_name: str = step.name) -> None:
            issues.append(Issue(severity, message, workflow=workflow.name, step=step_name))

        hints, errors = _resolve(step.func, (*step.deps, "return"))
        for name, exc in errors.items():
            what = "return annotation" if name == "return" else f"annotation of {name!r}"
            report("error", f"{what} cannot be resolved ({type(exc).__name__}: {exc}).")
        if "return" in errors:
            unresolved_returns.add(step.name)
        for dep in step.deps:
            if dep in errors or dep in unresolved_returns:
                continue
            expected, given = hints.get(dep, _UNKNOWN), produced.get(dep, _UNKNOWN)
            if expected is _UNKNOWN or given is _UNKNOWN:
                if strict:
                    report("warning", f"cannot check {dep!r}: annotate this parameter and the return of step {dep!r}.")
                continue
            verdict = _fits(given, expected)
            if verdict is False and _fits(declared[dep], expected) is not False:
                report(
                    "error",
                    f"step {dep!r} is optional or on_error='warn', so {dep!r} may be None; "
                    f"annotate it as {_name(expected)} | None.",
                )
            elif verdict is False:
                report(
                    "error", f"parameter {dep!r} expects {_name(expected)} but step {dep!r} produces {_name(given)}."
                )
            elif verdict is None and strict:
                report("warning", f"cannot decide whether {_name(given)} fits {_name(expected)} for {dep!r}.")
        returned = hints.get("return", _UNKNOWN)
        declared[step.name] = returned
        if returned is not _UNKNOWN and (step.optional is not None or step.on_error == "warn"):
            returned = typing.Optional[returned]  # noqa: UP045 -- a runtime value, not an annotation
        produced[step.name] = returned
    return issues


def _check_file(path: Path, strict: bool) -> tuple[list[Issue], int]:
    """Imports one file and checks its workflows; returns the issues and how many workflows it offers."""
    source = str(path)
    try:
        module = load_module(path)
    except WorkflowDefinitionError as exc:
        return [Issue("error", str(exc), source=source)], 0
    except Exception as exc:  # noqa: BLE001 -- an unimportable file is a check failure, not a crash
        return [Issue("error", f"cannot import: {type(exc).__name__}: {exc}", source=source)], 0
    workflows = collect_workflows(module)
    names = [workflow.name for workflow in workflows]
    issues = [
        Issue("error", f"duplicate workflow name {name!r}.", source=source)
        for name in sorted({n for n in names if names.count(n) > 1})
    ]
    for workflow in workflows:
        issues += [dataclasses.replace(issue, source=source) for issue in check_workflow(workflow, strict=strict)]
    return issues, len(workflows)


def check_path(path: str | Path, *, strict: bool = False) -> list[Issue]:
    """Checks the workflows offered by a file, or by every ``*.py`` file in a directory, without running them.

    Files that import fine but offer no workflow (helper modules) are skipped; a path offering none at all is
    an error.

    Args:
        path: An experiment file, or a directory of experiment files (not searched recursively).
        strict: Also report unannotated or undecidable wiring as warnings.

    Returns:
        The issues found, with ``source`` set to the file concerned.
    """
    path = Path(path)
    if not path.exists():
        return [Issue("error", "path does not exist.", source=str(path))]
    files = sorted(p for p in path.glob("*.py") if p.name != "__init__.py") if path.is_dir() else [path]
    issues: list[Issue] = []
    found = 0
    for file in files:
        file_issues, count = _check_file(file, strict)
        issues += file_issues
        found += count
    if found == 0 and not issues:
        issues.append(Issue("error", "no workflows or @experiment functions found.", source=str(path)))
    return issues
