"""The clabe demo: a behavior session declared as a workflow, shaped like a real rig's launcher.

Every step is a plain function; its parameters name the earlier steps whose outputs it needs. Run it with
``uv run examples/behavior_launcher.py`` (TUI) or ``uv run clabe run examples/behavior_launcher.py`` (also served in
the browser). Try going back from the review, retrying the flaky step, or skipping the optional ones.
"""

import logging
import random
from enum import Enum
from pathlib import Path
from typing import Any, Literal

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
from pydantic import BaseModel, Field
from pydantic_settings import CliApp

from clabe import checks, steps, ui
from clabe.apps import CommandResult, CurriculumApp, CurriculumSettings, PythonScriptApp
from clabe.cache_manager import CacheManager
from clabe.launcher import Launcher, LauncherCliArgs
from clabe.logging import otel
from clabe.stores import LocalFileStore, Store
from clabe.ui import MessageLevel, NumberRequest, TextRequest
from clabe.workflow import Workflow, concurrently

logger = logging.getLogger(__name__)


class RecordingMode(Enum):
    EPHYS = "ephys"
    CALCIUM = "calcium"
    BEHAVIOR_ONLY = "behavior_only"


class SessionConfig(BaseModel):
    """Demonstration model — exercises every form widget type."""

    notes: str | None = Field(
        default=None,
        title="Experimenter Notes",
        description="Free-text notes attached to this session. Leave blank to skip.",
    )
    session_type: Literal["behavior", "physiology", "combined"] = Field(
        default="behavior",
        title="Session Type",
        description="High-level classification of this recording session.",
    )
    recording_mode: RecordingMode = Field(
        default=RecordingMode.BEHAVIOR_ONLY,
        title="Recording Mode",
        description="Hardware modality used for this session (ephys, calcium imaging, or behavior-only).",
    )
    trial_count: int = Field(
        default=200,
        title="Trial Count",
        description="Number of trials to run. Must be a positive integer greater than zero.",
        gt=0,
    )
    debug_mode: bool = Field(
        default=False,
        title="Debug Mode",
        description="When enabled, skips hardware checks and logs extra diagnostics.",
    )
    output_dir: Path = Field(
        default=Path("./local/output"),
        title="Output Directory",
        description="Directory where session data and logs will be written.",
    )


_WELCOME = """\
This demo walks through a behavior session the way a rig's launcher does:

1. **Who and what** — the session, the subject's trainer state and the rig, from a config store.
2. **Review** — confirm it, or go back and change the session.
3. **Register** — a *barrier*: once the session folder exists, nothing before it runs again.
4. **Run** — the task apps, then the curriculum, metadata and a QC report.

Every step shows on the left; click a finished one to go back to it.
"""

demo = Workflow("demo", exit_prompt_timeout=30)


@demo.step(title="Welcome")
def welcome() -> None:
    """Seeds the mock rig, subjects and caches, then greets the user."""
    create_fake_subjects()
    create_fake_rig()
    _seed_cache()
    # A custom span, in addition to the automatic run and step spans (see the run's trace in OpenObserve).
    with otel.span("demo-preflight", attributes={"example": "behavior_launcher"}):
        otel.event("preflight-checks-started")
    ui.markdown(_WELCOME, title="The clabe demo")
    ui.acknowledge(
        "Ensure the animal is properly head-fixed and all cables are connected before proceeding.",
        title="Safety check",
        button_label="Confirmed",
    )


demo.step(steps.session(experimenter_validator=None))


@demo.step
def store(session: Session) -> Store:
    """The config store, scoped to the subject."""
    return LocalFileStore(LIB_CONFIG).scoped(subject=session.subject)


@demo.step(title="Trainer state")
def trainer_state(store: Store) -> TrainerState:
    """The subject's current stage, chosen from the store."""
    return store.resolve(SUGGESTION)


@demo.step(title="Rig")
def rig(store: Store) -> RigModel:
    """The rig's configuration, chosen from the store."""
    return store.resolve(RIG)


@demo.step(title="Session configuration")
def config() -> SessionConfig:
    """A form of every widget type; cancelling it keeps the defaults."""
    config = ui.form(SessionConfig, title="Session configuration")
    if config is None:
        ui.notify("Form cancelled — using defaults.", MessageLevel.WARNING)
        return SessionConfig()
    return config


@demo.step
def tuning() -> dict[str, Any]:
    """Several prompts answered together, including a slider."""
    return ui.group(
        "Tuning",
        operator_note=TextRequest(label="Operator note"),
        gain=NumberRequest(label="Reward gain", default=0.5, minimum=0, maximum=1, step=0.05, widget="slider"),
    )


demo.step(steps.review("session", "rig", "config", back_to="session"))
demo.step(steps.preflight(checks.free_space(1e9), checks.exists(Path.home())))  # every check, one failure
demo.step(steps.register())


@demo.step(title="Calibrate")
def calibrate(rig: RigModel) -> float:
    """A flaky step: it fails now and then, so the run offers Retry / Go back / Abort."""
    if random.random() < 0.5:
        raise RuntimeError(f"{rig.rig_name}: the valve did not respond. Check the tubing and retry.")
    return 4.2


@demo.step(title="Run the task", cancellable=True)
async def task(launcher: Launcher, config: SessionConfig, tuning: dict[str, Any], calibrate: float) -> None:
    """Runs the behavior and physiology apps side by side."""
    if not ui.table(
        ["App", "Trials", "Gain", "Valve (µL)"],
        [["Behavior", config.trial_count, tuning["gain"], calibrate], ["Physiology", "–", "–", "–"]],
        title="Run plan",
        prompt="Start these apps?",
        confirm_label="Start",
        cancel_label="Not yet",
    ):
        ui.notify("Run plan not confirmed: starting anyway (it is a demo).", MessageLevel.WARNING)

    def script(label: str) -> list[str]:
        return ["python", "-c", f"import time; print('Hello {label}'); time.sleep(2); print('DONE')"]

    async def physiology() -> None:
        # flaky on purpose: retry it alone while the behavior app keeps running
        await PythonScriptApp(script=script("Physiology")).run_async()
        if random.random() < 0.5:
            raise RuntimeError("the probe stopped streaming")

    # Branches run at the same time; a failed one is retried (or dropped) on its own, "Stop all" stops both.
    await concurrently(behavior=lambda: PythonScriptApp(script=script("Behavior")).run_async(), physiology=physiology)
    ui.notify("Both apps finished", MessageLevel.SUCCESS)


@demo.step(title="Curriculum", optional="Run the curriculum on this session?")
def curriculum(launcher: Launcher, trainer_state: TrainerState, task: None) -> CommandResult:
    """Suggests the next stage from this session's data."""
    return CurriculumApp(
        settings=CurriculumSettings(
            curriculum="template",
            data_directory=Path("demo"),
            project_directory=Path("./tests/assets/Aind.Behavior.VrForaging"),
            input_trainer_state=launcher.save_temp_model(trainer_state),
        )
    ).run()


@demo.step(title="Metadata", on_error="warn")
def metadata(
    launcher: Launcher,
    session: Session,
    rig: RigModel,
    trainer_state: TrainerState,
    curriculum: CommandResult | None,
) -> None:
    """Maps the session to the data schema (a failure only warns)."""
    assert trainer_state.stage is not None
    DemoAindDataSchemaSessionDataMapper(
        rig,
        session,
        MockTask.model_validate_json(trainer_state.stage.task.model_dump_json()),
        repository=launcher.repository,
        script_path=Path("./mock/script.py"),
        output_parameters={"suggestion": curriculum.model_dump() if curriculum is not None else None},
    ).map()


@demo.step(title="QC report", optional="Show the QC report?")
def qc(session: Session, config: SessionConfig) -> None:
    """A report the web UI shows as a page (other clients warn and name it)."""
    ui.html(
        f"<h2 style='font-family: sans-serif'>QC — {session.subject}</h2>"
        f"<p style='font-family: sans-serif'>{config.trial_count} trials planned; all checks passed.</p>",
        title="QC report",
    )


def _seed_cache() -> None:
    """Pre-populate the selection caches so autocompletion has options to filter."""
    cache = CacheManager.get_instance()
    cache.register_cache("subjects", max_history=20)
    cache.register_cache("experimenters", max_history=20)
    for subject in ["00000", "123456", "mouse_42", "mouse_77", "test_subject", "demo_animal", "alpha_01", "beta_02"]:
        cache.add_to_cache("subjects", subject)
    for experimenter in ["bruno.cruz", "jane.doe", "john.smith", "alex.kim"]:
        cache.add_to_cache("experimenters", experimenter)


def main():
    settings = CliApp.run(
        LauncherCliArgs,
        cli_args=["--allow-dirty", "--skip-hardware-validation", "--verbose", "--frontend", "tui"],
    )
    Launcher(settings=settings).run_experiment(demo)


if __name__ == "__main__":
    main()
