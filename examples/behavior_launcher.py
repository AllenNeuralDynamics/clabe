import asyncio
import datetime
import logging
import os
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Union

import git
from aind_behavior_curriculum import Stage, TrainerState
from aind_behavior_services.rig import AindBehaviorRigModel
from aind_behavior_services.session import AindBehaviorSessionModel
from aind_behavior_services.task_logic import AindBehaviorTaskLogicModel
from pydantic import Field
from pydantic_settings import CliApp

from clabe import resource_monitor
from clabe.apps import CurriculumApp, CurriculumSettings, PythonScriptApp
from clabe.data_mapper import DataMapper
from clabe.launcher import (
    Launcher,
    LauncherCliArgs,
)
from clabe.pickers import DefaultBehaviorPicker, DefaultBehaviorPickerSettings

logger = logging.getLogger(__name__)

TASK_NAME = "RandomTask"
LIB_CONFIG = rf"local\AindBehavior.db\{TASK_NAME}"


### Task-specific definitions
class RigModel(AindBehaviorRigModel):
    rig_name: str = Field(default="TestRig", description="Rig name")
    version: Literal["0.0.0"] = "0.0.0"


class TaskLogicModel(AindBehaviorTaskLogicModel):
    version: Literal["0.0.0"] = "0.0.0"
    name: Literal[TASK_NAME] = TASK_NAME


mock_trainer_state = TrainerState[Any](
    curriculum=None,
    is_on_curriculum=False,
    stage=Stage(name="TestStage", task=TaskLogicModel(name=TASK_NAME, task_parameters={"foo": "bar"})),
)


class MockAindDataSchemaSession:
    def __init__(
        self,
        computer_name: Optional[str] = None,
        repository: Optional[Union[os.PathLike, git.Repo]] = None,
        task_name: Optional[str] = None,
    ):
        self.computer_name = computer_name
        self.repository = repository
        self.task_name = task_name

    def __str__(self) -> str:
        return f"MockAindDataSchemaSession(computer_name={self.computer_name}, repository={self.repository}, task_name={self.task_name})"


class DemoAindDataSchemaSessionDataMapper(DataMapper[MockAindDataSchemaSession]):
    def __init__(
        self,
        rig_model: RigModel,
        session_model: AindBehaviorSessionModel,
        task_logic_model: TaskLogicModel,
        repository: Union[os.PathLike, git.Repo],
        script_path: os.PathLike,
        session_end_time: Optional[datetime.datetime] = None,
        output_parameters: Optional[Dict] = None,
    ):
        super().__init__()
        self.session_model = session_model
        self.rig_model = rig_model
        self.task_logic_model = task_logic_model
        self.repository = repository
        self.script_path = script_path
        self.session_end_time = session_end_time
        self.output_parameters = output_parameters
        self._mapped: Optional[MockAindDataSchemaSession] = None

    def map(self) -> MockAindDataSchemaSession:
        self._mapped = MockAindDataSchemaSession(
            computer_name=self.rig_model.computer_name, repository=self.repository, task_name=self.task_logic_model.name
        )
        print("#" * 50)
        print("THIS IS MAPPED DATA!")
        print("#" * 50)
        print(self._mapped)
        return self._mapped


async def experiment(launcher: Launcher) -> None:
    monitor = resource_monitor.ResourceMonitor(
        constrains=[
            resource_monitor.available_storage_constraint_factory(launcher.settings.data_dir, 2e11),
            resource_monitor.remote_dir_exists_constraint_factory(Path(r"C:/")),
        ]
    )

    picker = DefaultBehaviorPicker(
        launcher=launcher,
        settings=DefaultBehaviorPickerSettings(config_library_dir=LIB_CONFIG),
        experimenter_validator=lambda _: True,
    )

    session = picker.pick_session(AindBehaviorSessionModel)
    launcher.register_session(session)
    trainer_state, task_logic = picker.pick_trainer_state(TaskLogicModel)
    _temp_trainer_state_path = launcher.save_temp_model(trainer_state)
    rig = picker.pick_rig(RigModel)

    monitor.run()

    def fmt(value: str) -> str:
        return f"python -c \"import time; print('Hello {value}'); time.sleep(2); print('DONE')\""

    app_1 = PythonScriptApp(script=fmt("Behavior"))
    app_2 = PythonScriptApp(script=fmt("Physiology"))

    await asyncio.gather(app_1.run_async(), app_2.run_async())

    app_1.get_result()
    app_2.get_result()

    suggestion = (
        CurriculumApp(
            settings=CurriculumSettings(
                curriculum="template",
                data_directory=Path("demo"),
                project_directory=Path("./tests/assets/Aind.Behavior.VrForaging.Curricula"),
                input_trainer_state=_temp_trainer_state_path,
            )
        )
        .run()
        .get_result(allow_stderr=True)
    )

    DemoAindDataSchemaSessionDataMapper(
        rig,
        session,
        task_logic,
        repository=launcher.repository,
        script_path=Path("./mock/script.py"),
        output_parameters={"suggestion": suggestion.model_dump()},
    )
    launcher.copy_logs()

    return


def create_fake_subjects():
    subjects = ["00000", "123456"]
    for subject in subjects:
        os.makedirs(f"{LIB_CONFIG}/Subjects/{subject}", exist_ok=True)
        with open(f"{LIB_CONFIG}/Subjects/{subject}/task_logic.json", "w", encoding="utf-8") as f:
            f.write(TaskLogicModel(task_parameters={"subject": subject}).model_dump_json(indent=2))
        with open(f"{LIB_CONFIG}/Subjects/{subject}/trainer_state.json", "w", encoding="utf-8") as f:
            f.write(mock_trainer_state.model_dump_json(indent=2))


def create_fake_rig():
    computer_name = os.getenv("COMPUTERNAME")
    os.makedirs(_dir := f"{LIB_CONFIG}/Rig/{computer_name}", exist_ok=True)
    with open(f"{_dir}/rig1.json", "w", encoding="utf-8") as f:
        f.write(RigModel().model_dump_json(indent=2))


def main():
    create_fake_subjects()
    create_fake_rig()
    behavior_cli_args = CliApp.run(
        LauncherCliArgs,
        cli_args=[
            "--debug-mode",
            "--temp-dir",
            "./local/.temp",
            "--allow-dirty",
            "--skip-hardware-validation",
            "--data-dir",
            "./local",
        ],
    )

    launcher = Launcher(settings=behavior_cli_args)
    launcher.run_experiment(experiment)
    return None


if __name__ == "__main__":
    main()
