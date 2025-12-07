import asyncio
import logging
from pathlib import Path

from pydantic_settings import CliApp

from clabe import resource_monitor
from clabe.apps import CurriculumApp, CurriculumSettings, PythonScriptApp
from clabe.launcher import Launcher, LauncherCliArgs, experiment
from clabe.pickers import DefaultBehaviorPicker, DefaultBehaviorPickerSettings
from examples._mocks import (
    LIB_CONFIG,
    AindBehaviorSessionModel,
    DemoAindDataSchemaSessionDataMapper,
    RigModel,
    TaskLogicModel,
    create_fake_rig,
    create_fake_subjects,
)

logger = logging.getLogger(__name__)


@experiment()
async def demo_experiment(launcher: Launcher) -> None:
    """Demo experiment showcasing CLABE functionality."""
    picker = DefaultBehaviorPicker(
        launcher=launcher,
        settings=DefaultBehaviorPickerSettings(config_library_dir=LIB_CONFIG),
        experimenter_validator=lambda _: True,
    )

    session = picker.pick_session(AindBehaviorSessionModel)
    rig = picker.pick_rig(RigModel)
    launcher.register_session(session, rig.data_directory)
    trainer_state, task_logic = picker.pick_trainer_state(TaskLogicModel)
    _temp_trainer_state_path = launcher.save_temp_model(trainer_state)

    resource_monitor.ResourceMonitor(
        constrains=[
            resource_monitor.available_storage_constraint_factory_from_rig(rig, 2e11),
        ]
    ).run()

    def fmt(value: str) -> str:
        return f"python -c \"import time; print('Hello {value}'); time.sleep(2); print('DONE')\""

    app_1 = PythonScriptApp(script=fmt("Behavior"))
    app_2 = PythonScriptApp(script=fmt("Physiology"))

    app_1_result, app_2_result = await asyncio.gather(app_1.run_async(), app_2.run_async())

    suggestion = CurriculumApp(
        settings=CurriculumSettings(
            curriculum="template",
            data_directory=Path("demo"),
            project_directory=Path("./tests/assets/Aind.Behavior.VrForaging.Curricula"),
            input_trainer_state=_temp_trainer_state_path,
        )
    ).run()

    DemoAindDataSchemaSessionDataMapper(
        rig,
        session,
        task_logic,
        repository=launcher.repository,
        script_path=Path("./mock/script.py"),
        output_parameters={"suggestion": suggestion.model_dump()},
    ).map()
    return


def main():
    create_fake_subjects()
    create_fake_rig()
    behavior_cli_args = CliApp.run(
        LauncherCliArgs,
        cli_args=[
            "--debug-mode",
            "--allow-dirty",
            "--skip-hardware-validation",
            "--data-directory",
            "./local",
        ],
    )

    launcher = Launcher(settings=behavior_cli_args)
    launcher.run_experiment(demo_experiment)
    return None


if __name__ == "__main__":
    main()
