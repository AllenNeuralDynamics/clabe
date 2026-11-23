"""Static checks of workflows — wiring and types — without running any step (for CI)."""

import ast
import dataclasses
import inspect
import textwrap
import types
import typing
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from ._discovery import collect_workflows, load_module
from ._workflow import BACK_TARGETS, Step, Workflow, WorkflowDefinitionError

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
    declared: dict[str, Any] = {}  # each step's return annotation
    unresolved_returns: set[str] = set()  # already reported as errors; not worth a strict warning downstream
    for step in workflow.steps:

        def report(severity: Severity, message: str, step_name: str = step.name) -> None:
            issues.append(Issue(severity, message, workflow=workflow.name, step=step_name))

        inputs = tuple(d for d in step.params if d in step.deps)
        hints, errors = _resolve(step.func, (*inputs, "return"))
        # The condition's inputs are checked when annotated; a lambda's are not, and need not be.
        condition_inputs = tuple(d for d in step.when_params if d in step.deps and d not in inputs)
        if step.when is not None:
            condition_hints, condition_errors = _resolve(step.when, condition_inputs)
            hints.update(condition_hints)
            errors.update(condition_errors)
        for name, exc in errors.items():
            what = "return annotation" if name == "return" else f"annotation of {name!r}"
            report("error", f"{what} cannot be resolved ({type(exc).__name__}: {exc}).")
        if "return" in errors:
            unresolved_returns.add(step.name)
        for dep in (*inputs, *condition_inputs):
            if dep in errors or dep in unresolved_returns:
                continue
            expected, given = hints.get(dep, _UNKNOWN), declared.get(dep, _UNKNOWN)
            if given is not _UNKNOWN and _may_receive_none(workflow.get(dep), step):
                given = typing.Optional[given]  # noqa: UP045 -- a runtime value, not an annotation
            if expected is _UNKNOWN or given is _UNKNOWN:
                if strict and dep in inputs:
                    report("warning", f"cannot check {dep!r}: annotate this parameter and the return of step {dep!r}.")
                continue
            verdict = _fits(given, expected)
            if verdict is False and _fits(declared[dep], expected) is not False:
                report(
                    "error",
                    f"step {dep!r} is optional, conditional or on_error='warn', so {dep!r} may be None; "
                    f"annotate it as {_name(expected)} | None.",
                )
            elif verdict is False:
                report(
                    "error", f"parameter {dep!r} expects {_name(expected)} but step {dep!r} produces {_name(given)}."
                )
            elif verdict is None and strict:
                report("warning", f"cannot decide whether {_name(given)} fits {_name(expected)} for {dep!r}.")
        declared[step.name] = hints.get("return", _UNKNOWN)
    issues += _check_back_targets(workflow)
    return issues


def _check_back_targets(workflow: Workflow) -> list[Issue]:
    """Reports ``GoBack`` targets found statically that are not an earlier step (or the step itself)."""
    position = {step.name: index for index, step in enumerate(workflow.steps)}
    issues = []
    for index, step in enumerate(workflow.steps):
        for target in _back_targets(step.func):
            if target not in position:
                message = f"goes back to {target!r}, which is not a step of this workflow."
            elif position[target] > index:
                message = f"goes back to {target!r}, which comes after it."
            else:
                continue
            issues.append(Issue("error", message, workflow=workflow.name, step=step.name))
    return issues


def _back_targets(func: Callable[..., Any]) -> list[str]:
    """The step names ``func`` may go back to, as far as can be told without running it: those a built step declares
    (`BACK_TARGETS`), and the literal names in its ``GoBack("...")`` calls."""
    targets = list(getattr(func, BACK_TARGETS, ()))
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    except (OSError, TypeError, SyntaxError):  # no source (built-in, lambda in a REPL, ...)
        return targets
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        if name != "GoBack":
            continue
        arguments = [*node.args[:1], *(k.value for k in node.keywords if k.arg == "to")]
        targets += [a.value for a in arguments if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    return targets


def _may_receive_none(producer: Step, consumer: Step) -> bool:
    """Whether ``consumer`` may receive None from ``producer``: an optional or ``on_error='warn'`` step may give None,
    and a conditional one too, unless ``consumer`` has the same condition (it then runs only when ``producer`` did)."""
    if producer.optional is not None or producer.on_error == "warn":
        return True
    if producer.when is None:
        return False
    return consumer.when is None or not _same_condition(producer.when, consumer.when)


def _same_condition(first: Callable[..., Any], second: Callable[..., Any]) -> bool:
    """Whether two ``when=`` conditions always agree: the same function, or two functions with the same code, globals,
    defaults and closure values (e.g. the same lambda written twice)."""
    if first is second:
        return True
    a, b = getattr(first, "__code__", None), getattr(second, "__code__", None)
    if a is None or b is None:
        return False
    same_code = (a.co_code, a.co_consts, a.co_names, a.co_varnames) == (
        b.co_code,
        b.co_consts,
        b.co_names,
        b.co_varnames,
    )
    cells_a, cells_b = first.__closure__ or (), second.__closure__ or ()  # type: ignore[attr-defined]
    return (
        same_code
        and first.__globals__ is second.__globals__  # type: ignore[attr-defined]
        and first.__defaults__ == second.__defaults__  # type: ignore[attr-defined]
        and len(cells_a) == len(cells_b)
        and all(x.cell_contents is y.cell_contents for x, y in zip(cells_a, cells_b, strict=True))
    )


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
        issues.append(Issue("error", "no workflows found.", source=str(path)))
    return issues
