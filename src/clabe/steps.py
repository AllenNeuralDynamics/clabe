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

import asyncio
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
from .workflow._workflow import BACK_TARGETS

__all__ = ["curriculum", "free_space", "preflight", "register", "remote", "review", "session", "waterlog"]


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
    setattr(review, BACK_TARGETS, (target,))  # for clabe check
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


#: Where `curriculum` saves the trainer state the session started from, in the session folder.
TRAINER_STATE_PATH = Path("behavior") / "trainer_state.json"
#: Where `curriculum` saves the suggestion, in the session folder.
SUGGESTION_PATH = Path("Behavior") / "Logs" / "suggestion.json"


def curriculum(
    *,
    before: Callable[..., Any] | None = None,
    upload: bool | Callable[[], bool] = True,
    settings: Mapping[str, Any] | None = None,
    title: str = "Curriculum",
) -> Callable[..., Any]:
    """A ``curriculum`` step suggesting the animal's next stage from this session's data; returns a
    [`CurriculumOutcome`][clabe.apps.CurriculumOutcome], or ``None`` when the animal is not on a curriculum.

    It takes ``launcher``, ``session``, ``store`` and ``trainer_state`` by name, and needs a registered session.
    When the trainer state is on a curriculum (``is_on_curriculum`` and a ``stage``), it:

    1. saves the trainer state the session started from to ``<session>/behavior/trainer_state.json`` (an existing
       file, e.g. when recovering a session, is kept);
    2. calls ``before``, if given, e.g. to write the past sessions' metrics a curriculum reads;
    3. runs [`CurriculumApp`][clabe.apps.CurriculumApp] on the session folder, and saves its suggestion to
       ``<session>/Behavior/Logs/suggestion.json``;
    4. writes the suggested trainer state to the store (`Kind.from_trainer_state`), unless ``upload`` says not to.

    ```python
    wf.step(steps.curriculum(before=write_past_metrics))
    recovery.step(steps.curriculum(upload=lambda: ui.confirm("Upload the suggestion?")))
    ```

    Args:
        before: Called before the curriculum runs, with what it takes of ``launcher``, ``session``, ``store`` and
            ``trainer_state`` (by name); sync or async. An error it raises fails the step.
        upload: Whether to write the suggestion to the store, or a function asked when there is one (e.g. a
            confirmation).
        settings: Other [`CurriculumSettings`][clabe.apps.CurriculumSettings] fields, e.g. ``{"curriculum": ...}``.
        title: The step's title.
    """
    from aind_behavior_curriculum import TrainerState

    from .apps import CurriculumApp, CurriculumOutcome, CurriculumSettings
    from .stores import Kind, Store

    @step_defaults(title=title)
    async def curriculum(
        launcher: Launcher, session: Session, store: Store, trainer_state: TrainerState
    ) -> CurriculumOutcome | None:
        """Runs the curriculum on this session, when the animal is on one."""
        if not (trainer_state.is_on_curriculum and trainer_state.stage is not None):
            ui.notify("The animal is not on a curriculum; no suggestion.", ui.MessageLevel.INFO)
            return None
        directory = launcher.session_directory
        input_trainer_state = directory / TRAINER_STATE_PATH
        if not input_trainer_state.exists():
            input_trainer_state.parent.mkdir(parents=True, exist_ok=True)
            input_trainer_state.write_text(trainer_state.model_dump_json(indent=2), encoding="utf-8")
        if before is not None:
            available = {"launcher": launcher, "session": session, "store": store, "trainer_state": trainer_state}
            wanted = inspect.signature(before).parameters
            await _call(before, **{name: value for name, value in available.items() if name in wanted})
        run_settings = CurriculumSettings(
            **{**(settings or {}), "input_trainer_state": input_trainer_state.resolve(), "data_directory": directory}
        )
        app = CurriculumApp(settings=run_settings)
        await app.run_async()
        suggestion = app.process_suggestion()
        suggestion_path = directory / SUGGESTION_PATH
        suggestion_path.parent.mkdir(parents=True, exist_ok=True)
        suggestion_path.write_text(suggestion.model_dump_json(indent=2), encoding="utf-8")
        uploaded = bool(await _call(upload)) if callable(upload) else upload
        if uploaded:
            store.write(Kind.from_trainer_state(), suggestion.trainer_state)
        else:
            ui.notify("The curriculum suggestion was not uploaded.", ui.MessageLevel.WARNING)
        return CurriculumOutcome(
            suggestion=suggestion,
            suggestion_path=suggestion_path,
            input_trainer_state=input_trainer_state,
            settings=run_settings,
            uploaded=uploaded,
        )

    return curriculum


async def _call(func: Callable[..., Any], **kwargs: Any) -> Any:
    """Calls a hook from an async step: an async one on the loop, a sync one in a thread (it may ask the user or wait
    on the network)."""
    if inspect.iscoroutinefunction(func):
        return await func(**kwargs)
    return await asyncio.to_thread(func, **kwargs)


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
