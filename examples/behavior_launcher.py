import asyncio
import logging
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from _mocks import (
    LIB_CONFIG,
    DemoAindDataSchemaSessionDataMapper,
    MockTask,
    RigModel,
    Session,
    create_fake_rig,
    create_fake_subjects,
)
from pydantic import BaseModel, Field
from pydantic_settings import CliApp

from clabe import resource_monitor
from clabe.apps import CurriculumApp, CurriculumSettings, PythonScriptApp
from clabe.cache_manager import CacheManager
from clabe.launcher import Launcher, LauncherCliArgs, experiment
from clabe.pickers import DefaultBehaviorPicker, DefaultBehaviorPickerSettings
from clabe.runnable import runnable
from clabe.ui import (
    AcknowledgeRequest,
    ConfirmRequest,
    FieldRequest,
    FormRequest,
    MessageLevel,
    ReadOnlyTable,
    notify,
)

logger = logging.getLogger(__name__)


class RecordingMode(Enum):
    EPHYS = "ephys"
    CALCIUM = "calcium"
    BEHAVIOR_ONLY = "behavior_only"


class SessionConfig(BaseModel):
    """Demonstration model — exercises every form widget type."""

    notes: Optional[str] = Field(
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


@experiment()
async def demo_experiment(launcher: Launcher) -> None:
    """Demo experiment showcasing CLABE functionality."""
    # Seed the mock rig/subjects/cache here so the demo also works when launched
    # via ``clabe run``/``clabe serve`` (which call this function but not main()).
    create_fake_subjects()
    create_fake_rig()
    _seed_cache()

    logger.info("Starting the demo experiment")
    notify("Welcome to the CLABE demo experiment!", MessageLevel.INFO)

    # --- AcknowledgeRequest demo: modal acknowledgement gate --------------
    launcher.frontend.prompt_acknowledge(
        AcknowledgeRequest(
            title="Safety Check",
            message="Ensure the animal is properly head-fixed and all cables are connected before proceeding.",
            button_label="Confirmed",
        )
    )
    # ----------------------------------------------------------------------

    config = launcher.frontend.prompt_form(FormRequest(model=SessionConfig, title="Session Configuration"))
    if config is not None:
        notify(
            f"Config: type={config.session_type!r}  mode={config.recording_mode.name}"
            f"  trials={config.trial_count}  debug={config.debug_mode}",
            MessageLevel.SUCCESS,
        )
    else:
        notify("Form cancelled — using defaults.", MessageLevel.WARNING)
        config = SessionConfig()

    # --- ReadOnlyTable demo (from_object): review the filled config ---------
    # Renders the model as a read-only "Parameter | Value" table with OK/Cancel.
    confirmed = launcher.frontend.prompt_read_only_table(
        ReadOnlyTable.from_object(
            config,
            title="Confirm Session Configuration",
            prompt="Are these settings correct?",
            confirm_label="Looks good",
            cancel_label="Go back",
        )
    )
    if not confirmed:
        notify("Configuration not confirmed — using defaults.", MessageLevel.WARNING)
        config = SessionConfig()
    # ----------------------------------------------------------------------

    updated_type = launcher.frontend.prompt_field(
        FieldRequest(
            model=SessionConfig,
            field_name="session_type",
            initial=config.session_type,
        )
    )
    notify(f"Final session type: {updated_type!r}", MessageLevel.INFO)

    picker = DefaultBehaviorPicker(
        launcher=launcher,
        settings=DefaultBehaviorPickerSettings(config_library_dir=LIB_CONFIG),
        experimenter_validator=lambda _: True,
    )

    if not picker.frontend.prompt_confirm(
        ConfirmRequest(label="Is this True", default=True),
    ):
        notify("hahaha", MessageLevel.INFO)

    if not picker.frontend.prompt_confirm(
        ConfirmRequest(label="Proceed with the experiment?"),
    ):
        notify("Experiment cancelled by user.", MessageLevel.WARNING)

    picker.frontend.prompt_acknowledge(
        AcknowledgeRequest(
            title="Experiment Starting",
            message="All checks passed. The experiment is about to begin. Press OK to continue.",
        )
    )

    session = picker.pick_session(Session)
    rig = picker.pick_rig(RigModel)
    launcher.register_session(session, rig.data_directory)
    trainer_state, task = picker.pick_trainer_state(MockTask)
    _temp_trainer_state_path = launcher.save_temp_model(trainer_state)

    resource_monitor.ResourceMonitor(
        constrains=[
            resource_monitor.available_storage_constraint_factory_from_rig(rig, 1e9),
        ]
    ).run()

    def fmt(value: str) -> list[str]:
        return ["python", "-c", f"import time; print('Hello {value}'); time.sleep(2); print('DONE')"]

    app_1 = PythonScriptApp(script=fmt("Behavior"))
    app_2 = PythonScriptApp(script=fmt("Physiology"))

    # --- ReadOnlyTable demo (from_records): review the app run plan ---------
    # A general grid built from a list of dicts; columns are inferred from keys.
    if not launcher.frontend.prompt_read_only_table(
        ReadOnlyTable.from_records(
            [
                {"App": "Behavior", "Timeout (s)": 2, "Blocking": False},
                {"App": "Physiology", "Timeout (s)": 2, "Blocking": False},
            ],
            title="App Run Plan",
            prompt="Run these apps?",
        )
    ):
        notify("App run cancelled by user.", MessageLevel.WARNING)
    # ----------------------------------------------------------------------

    notify("Running the behavior and physiology apps…", MessageLevel.INFO)
    app_1_result, app_2_result = await asyncio.gather(
        runnable(app_1.run_async, name="Running Behavior App")(),
        runnable(app_2.run_async, name="Running Physiology App")(),
    )
    logger.debug("App results: behavior=%r, physiology=%r", app_1_result, app_2_result)
    notify("Both apps finished", MessageLevel.SUCCESS)

    suggestion = CurriculumApp(
        settings=CurriculumSettings(
            curriculum="template",
            data_directory=Path("demo"),
            project_directory=Path("./tests/assets/Aind.Behavior.VrForaging"),
            input_trainer_state=_temp_trainer_state_path,
        )
    ).run()

    DemoAindDataSchemaSessionDataMapper(
        rig,
        session,
        task,
        repository=launcher.repository,
        script_path=Path("./mock/script.py"),
        output_parameters={"suggestion": suggestion.model_dump()},
    ).map()

    logger.info("Demo experiment finished")
    notify("Demo experiment complete!", MessageLevel.SUCCESS)
    return


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
        cli_args=[
            "--allow-dirty",
            "--skip-hardware-validation",
            "--verbose",
            "--frontend",
            "tui",
        ],
    )
    Launcher(settings=settings).run_experiment(demo_experiment)
    return None


if __name__ == "__main__":
    main()
