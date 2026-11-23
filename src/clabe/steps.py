"""Reusable workflow steps.

Each function here builds a step for [`Workflow.step`][clabe.workflow.Workflow.step]. The built step is a plain
function whose parameters wire it to other steps by name — ``session`` (a `Session`),
``rig`` (anything with a ``data_directory``) and the built-in ``launcher`` — and which carries sensible default
options (``register`` is a barrier, ``waterlog`` only warns when it fails); options given when declaring it win:

```python
wf = Workflow("vr-foraging")
wf.step(steps.session())
...                                         # store, trainer_state, rig, ...
wf.step(steps.review("session", "trainer_state", back_to="session"))
wf.step(steps.preflight(checks.free_space(200e9), checks.exists(SHARE)))
wf.step(steps.register())                   # barrier: nothing before it can run again
...                                         # run the task, transfer the data
```
"""

import inspect
import os
import typing
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from aind_behavior_services import Session
from pydantic import BaseModel

from . import checks as _checks
from . import ui
from .aind.validators import validate_username
from .launcher import Launcher
from .session import SessionBuilder
from .workflow import GoBack, step_defaults

__all__ = ["free_space", "preflight", "register", "remote", "review", "session", "waterlog"]


def session(
    *, experimenter_validator: Callable[[str], str | None] | None = validate_username, use_cache: bool = True
) -> Callable[[Launcher], Session]:
    """A ``session`` step asking who is running and on which animal (see [`SessionBuilder`][clabe.session.SessionBuilder]).

    Args:
        experimenter_validator: Validates each experimenter name; ``None`` accepts names as typed.
        use_cache: Whether to seed the prompts with previously entered values.
    """

    @step_defaults(title="Session")
    def session(launcher: Launcher) -> Session:
        return SessionBuilder(launcher, experimenter_validator=experimenter_validator, use_cache=use_cache).build()

    return session


def review(*names: str, back_to: str | None = None, title: str = "Confirm session") -> Callable[..., None]:
    """A ``review`` step showing the outputs of the steps ``names`` and asking whether they are right.

    "Looks good" continues; "Go back" goes back to ``back_to`` (by default the first reviewed step), which runs
    again along with everything depending on it.

    Args:
        *names: The steps whose outputs to show, in order.
        back_to: The step to go back to when the user does not confirm.
        title: Title of the review table.
    """
    if not names:
        raise ValueError("review() needs at least one step to show.")
    target = back_to or names[0]

    def review(**outputs: Any) -> None:
        rows = []
        for name in names:
            value = outputs[name]
            fields = value.model_dump(mode="json") if isinstance(value, BaseModel) else {"": value}
            rows += [[name if key == "" else f"{name}.{key}", _cell(item)] for key, item in fields.items()]
        confirmed = ui.table(
            ["Item", "Value"],
            rows,
            title=title,
            prompt="Is this right?",
            confirm_label="Looks good",
            cancel_label="Go back",
        )
        if not confirmed:
            raise GoBack(target)

    # wired by name: the step takes exactly the reviewed outputs
    review.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, annotation=Any) for name in names],
        return_annotation=None,
    )
    review.__annotations__ = {**dict.fromkeys(names, Any), "return": None}
    return step_defaults(title=title)(review)


def preflight(
    *checks: Callable[..., Any], name: str = "preflight", title: str = "Preflight checks"
) -> Callable[..., None]:
    """A step running ``checks`` (see [`clabe.checks`][clabe.checks]) and failing once, listing every failure.

    The step takes what its checks take, by name (e.g. ``rig``); Retry runs them all again.

    Args:
        *checks: The checks, e.g. ``checks.free_space(200e9)`` or your own functions.
        name: The step's name.
        title: The step's title.
    """
    if not checks:
        raise ValueError("preflight() needs at least one check.")
    names = _checks.inputs(*checks)

    def preflight(**outputs: Any) -> None:
        _checks.run(*checks, **outputs)

    # wired by name: the step takes exactly what its checks take
    preflight.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [inspect.Parameter(n, inspect.Parameter.KEYWORD_ONLY, annotation=Any) for n in names],
        return_annotation=None,
    )
    preflight.__annotations__ = {**dict.fromkeys(names, Any), "return": None}
    preflight.__name__ = preflight.__qualname__ = name
    return step_defaults(title=title)(preflight)


def free_space(minimum_bytes: float, *, directory: Callable[[Any], os.PathLike] | None = None) -> Callable[..., None]:
    """A ``free_space`` step failing (Retry / Go back / Abort) when the rig's data drive is short of space.

    The step form of [`checks.free_space`][clabe.checks.free_space].

    Args:
        minimum_bytes: Free space required.
        directory: Where the data goes, from the ``rig``; by default ``rig.data_directory``.
    """
    return preflight(
        _checks.free_space(minimum_bytes, directory=directory), name="free_space", title="Check free space"
    )


def register(*, directory: Callable[[Any], os.PathLike] | None = None) -> Callable[..., Path]:
    """A ``register`` step (a barrier) creating the session's data directory; returns it.

    Args:
        directory: Where the data goes, from the ``rig``; by default ``rig.data_directory``.
    """

    @step_defaults(title="Register session", barrier=True)
    def register(launcher: Launcher, session: Session, rig: Any) -> Path:
        launcher.register_session(session, directory(rig) if directory is not None else rig.data_directory)
        return launcher.session_directory

    return register


def remote(
    computer_name: Callable[..., str],
    *,
    name: str = "remote",
    title: str = "Other computer",
    allow_dirty: bool = False,
    repository: str | None = None,
    data_roots: Mapping[str, str] | None = None,
) -> Callable[..., Any]:
    """A step connecting to another rig PC through its daemon; returns its
    [`RemoteRepository`][clabe.remote.RemoteRepository], or ``None`` when the computer is this one.

    It starts the other PC's daemon if it is down (`ensure_running`) and checks that its checkout matches this one
    (`verify`). The step takes what ``computer_name`` takes, by name (as checks do):

    ```python
    wf.step(steps.remote(lambda ephys_rig: ephys_rig.computer_name))
    ```

    Args:
        computer_name: Gives the other PC's name from earlier outputs, e.g. a rig's ``computer_name``.
        name: The step's name.
        title: The step's title.
        allow_dirty: Accept uncommitted changes in the other PC's checkout.
        repository: The daemon's name for the checkout (default: this repository's folder name).
        data_roots: Maps data folders there to the share that exposes them (see `RemoteRepository`).
    """
    from .remote import RemoteRepository
    from .utils import is_this_computer

    names = _checks.inputs(computer_name)

    def remote(**outputs: Any) -> RemoteRepository | None:
        host = computer_name(**outputs)
        if is_this_computer(host):
            return None
        other = RemoteRepository.from_computer_name(host, repository=repository, data_roots=data_roots)
        other.ensure_running()
        other.verify(allow_dirty=allow_dirty)
        return other

    hints = typing.get_type_hints(computer_name) if not _is_lambda(computer_name) else {}
    remote.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [inspect.Parameter(n, inspect.Parameter.KEYWORD_ONLY, annotation=hints.get(n, Any)) for n in names],
        return_annotation=RemoteRepository | None,
    )
    remote.__annotations__ = {**{n: hints.get(n, Any) for n in names}, "return": RemoteRepository | None}
    remote.__name__ = remote.__qualname__ = name
    return step_defaults(title=title)(remote)


def _is_lambda(func: Callable[..., Any]) -> bool:
    """Whether ``func`` is a lambda (its annotations, if any, are not worth resolving)."""
    return getattr(func, "__name__", "") == "<lambda>"


def waterlog(earned_water: Callable[[Launcher], float]) -> Callable[..., None]:
    """A ``waterlog`` step logging the water the animal earned (only warns when it fails). Needs ``aind-services``.

    Args:
        earned_water: The water earned (mL), computed from the launcher (e.g. from its session directory).
    """

    @step_defaults(title="Water log", on_error="warn")
    def waterlog(launcher: Launcher, session: Session) -> None:
        from .aind.waterlog import WaterlogApp, WaterlogSettings

        WaterlogApp(
            settings=WaterlogSettings(
                username=session.experimenter[0] if session.experimenter else None,
                mouse_id=session.subject,
                earned_water=earned_water(launcher),
            )
        ).run()

    return waterlog


def _cell(value: Any) -> Any:
    """A table cell: scalars as they are, anything else as text."""
    return value if value is None or isinstance(value, (str, int, float, bool)) else str(value)
