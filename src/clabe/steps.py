"""Reusable workflow steps.

Each function here builds a step for [`Workflow.step`][clabe.workflow.Workflow.step]. The built step is a plain
function whose parameters wire it to other steps by name — ``session`` (a `Session`),
``rig`` (anything with a ``data_directory``) and the built-in ``launcher`` — and which carries sensible default
options (``register`` is a barrier, ``waterlog`` only warns when it fails); options given when declaring it win::

    wf = Workflow("vr-foraging")
    wf.step(steps.session())
    ...                                         # store, trainer_state, rig, ...
    wf.step(steps.review("session", "trainer_state", back_to="session"))
    wf.step(steps.free_space(200e9))
    wf.step(steps.register())                   # barrier: nothing before it can run again
    ...                                         # run the task
    wf.step(steps.transfer(make_watchdog))
"""

import inspect
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aind_behavior_services import Session
from pydantic import BaseModel

from . import ui
from .data_transfer import DataTransfer
from .launcher import Launcher
from .session import SessionBuilder
from .utils.aind_validators import validate_username
from .workflow import GoBack, step_defaults

__all__ = ["free_space", "register", "review", "session", "transfer", "waterlog"]


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


def free_space(minimum_bytes: float, *, directory: Callable[[Any], os.PathLike] | None = None) -> Callable[..., None]:
    """A ``free_space`` step failing (Retry / Go back / Abort) when the rig's data drive is short of space.

    Args:
        minimum_bytes: Free space required.
        directory: Where the data goes, from the ``rig``; by default ``rig.data_directory``.
    """

    @step_defaults(title="Check free space")
    def free_space(rig: Any) -> None:
        target = Path(directory(rig) if directory is not None else rig.data_directory)
        existing = next((p for p in (target, *target.parents) if p.exists()), None)
        if existing is None:
            raise FileNotFoundError(f"Neither {target} nor any of its parents exist.")
        free = shutil.disk_usage(existing).free
        if free < minimum_bytes:
            raise RuntimeError(f"Only {free / 1e9:.1f} GB free on {existing}; {minimum_bytes / 1e9:.1f} GB needed.")

    return free_space


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


def transfer(service: Callable[[Launcher, Session], DataTransfer]) -> Callable[..., None]:
    """A ``transfer`` step (a barrier) handing the session's data to a data-transfer service.

    Args:
        service: Builds the service from the launcher and the session, e.g. a
            [`WatchdogDataTransferService`][clabe.data_transfer.aind_watchdog.WatchdogDataTransferService].
    """

    @step_defaults(title="Transfer data", barrier=True)
    def transfer(launcher: Launcher, session: Session) -> None:
        transfer_service = service(launcher, session)
        if not transfer_service.validate():
            raise RuntimeError(f"{type(transfer_service).__name__} is not ready to transfer the data.")
        transfer_service.transfer()

    return transfer


def waterlog(earned_water: Callable[[Launcher], float]) -> Callable[..., None]:
    """A ``waterlog`` step logging the water the animal earned (only warns when it fails). Needs ``aind-services``.

    Args:
        earned_water: The water earned (mL), computed from the launcher (e.g. from its session directory).
    """

    @step_defaults(title="Water log", on_error="warn")
    def waterlog(launcher: Launcher, session: Session) -> None:
        from .aind_apps import WaterlogApp, WaterlogSettings

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
