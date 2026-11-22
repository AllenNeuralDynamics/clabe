"""An AIND-style behavior session: the subject's trainer state and the rig come from a config store, the task apps
run side by side, then the curriculum suggests the next stage and the session is mapped to the data schema.

The store, apps and data mapper are mocks (see ``_mocks.py``), so it runs on any PC:

    uv run clabe run examples/behavior_launcher.py --allow-dirty --skip-hardware-validation

``examples/demo/main.py`` shows the prompts, forms and clients; this one shows the AIND pieces.
"""

from pathlib import Path

from _mocks import (
    LIB_CONFIG,
    RIG,
    SUGGESTION,
    DemoAindDataSchemaSessionDataMapper,
    MockTask,
    RigModel,
    create_fake_rig,
    create_fake_subjects,
)
from aind_behavior_curriculum import TrainerState
from aind_behavior_services.session import Session

from clabe import checks, steps
from clabe.apps import CommandResult, CurriculumApp, CurriculumSettings, PythonScriptApp
from clabe.launcher import Launcher
from clabe.stores import LocalFileStore, Store
from clabe.workflow import Workflow, concurrently

wf = Workflow("behavior-launcher")


@wf.step(title="Mock data")
def mock_data() -> None:
    """Writes the rig and subject files the store reads (on a real rig they already exist)."""
    create_fake_subjects()
    create_fake_rig()


wf.step(steps.session(experimenter_validator=None))


@wf.step
def store(session: Session, mock_data: None) -> Store:
    """The config store, scoped to the subject."""
    return LocalFileStore(LIB_CONFIG).scoped(subject=session.subject)


@wf.step(title="Trainer state")
def trainer_state(store: Store) -> TrainerState:
    """The subject's current stage, picked from the store."""
    return store.resolve(SUGGESTION)


@wf.step(title="Rig")
def rig(store: Store) -> RigModel:
    """The rig's configuration, picked from the store."""
    return store.resolve(RIG)


wf.step(steps.review("session", "trainer_state", "rig", back_to="session"))
wf.step(steps.preflight(checks.free_space(1e9)))
wf.step(steps.register())


@wf.step(title="Run the task", cancellable=True)
async def task(launcher: Launcher, rig: RigModel) -> dict:
    """Runs the behavior and physiology apps at the same time (here, two short Python scripts)."""

    def app(name: str) -> PythonScriptApp:
        return PythonScriptApp(
            script=["python", "-c", f"import time; print('{name} on {rig.rig_name}'); time.sleep(2)"]
        )

    return await concurrently(behavior=app("behavior").run_async, physiology=app("physiology").run_async)


@wf.step(title="Curriculum", optional="Run the curriculum on this session?")
def curriculum(launcher: Launcher, trainer_state: TrainerState, task: dict) -> CommandResult:
    """Suggests the subject's next stage from this session's data."""
    return CurriculumApp(
        settings=CurriculumSettings(
            curriculum="template",
            data_directory=Path("demo"),
            project_directory=Path("./tests/assets/Aind.Behavior.VrForaging"),
            input_trainer_state=launcher.save_temp_model(trainer_state),
        )
    ).run()


@wf.step(title="Metadata", on_error="warn")
def metadata(
    launcher: Launcher, session: Session, rig: RigModel, trainer_state: TrainerState, curriculum: CommandResult | None
) -> None:
    """Maps the session to the data schema; a failure only warns."""
    assert trainer_state.stage is not None
    DemoAindDataSchemaSessionDataMapper(
        rig,
        session,
        MockTask.model_validate_json(trainer_state.stage.task.model_dump_json()),
        repository=launcher.repository,
        script_path=Path("./mock/script.py"),
        output_parameters={"suggestion": curriculum.model_dump() if curriculum is not None else None},
    ).map()
