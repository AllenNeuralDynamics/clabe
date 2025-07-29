import datetime
import logging
import os
import subprocess
from pathlib import Path
from typing import Callable, Dict, Literal, Optional, Self, Union

import git
from aind_behavior_services.rig import AindBehaviorRigModel
from aind_behavior_services.session import AindBehaviorSessionModel
from aind_behavior_services.task_logic import AindBehaviorTaskLogicModel
from pydantic import Field
from pydantic_settings import CliApp
from typing_extensions import override

from clabe import resource_monitor
from clabe.apps import App
from clabe.data_mapper import DataMapper
from clabe.data_transfer.aind_watchdog import WatchdogDataTransferService, WatchdogSettings
from clabe.launcher import BaseLauncher, BaseLauncherCliArgs, DefaultBehaviorPicker, DefaultBehaviorPickerSettings

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
        session_model: AindBehaviorSessionModel,
        rig_model: RigModel,
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

    @classmethod
    def builder_runner(
        cls,
        script_path: os.PathLike,
        session_end_time: Optional[datetime.datetime] = None,
        output_parameters: Optional[Dict] = None,
    ) -> Callable[[BaseLauncher], Self]:
        def _run(launcher: BaseLauncher) -> Self:
            logger.info("Running DemoAindDataSchemaSessionDataMapper...")
            new = cls(
                session_model=launcher.get_session(strict=True),
                rig_model=launcher.get_rig(strict=True),
                task_logic_model=launcher.get_task_logic(strict=True),
                repository=launcher.repository,
                script_path=script_path,
                session_end_time=session_end_time or datetime.datetime.now(),
                output_parameters=output_parameters,
            )
            logger.info("DemoAindDataSchemaSessionDataMapper completed.")
            new.map()
            return new

        return _run


class MockWatchdogService(WatchdogDataTransferService):
    def __init__(self, *args, **kwargs):
        os.environ["WATCHDOG_EXE"] = "mock_executable"
        os.environ["WATCHDOG_CONFIG"] = "mock_config"
        super().__init__(*args, **kwargs)

    def transfer(self) -> None:
        logger.info("MockWatchdogService: Transfer method called.")
        logger.info("Validating watchdog service...")
        self.validate()
        logger.info("Watchdog service validated successfully.")
        logger.info("MockWatchdogService: Data transfer completed successfully.")

    def validate(self, *args, **kwargs) -> bool:
        return True


class EchoApp(App):
    def __init__(self, value: str) -> None:
        self._value = value
        self._result = None

    @override
    def run(self) -> subprocess.CompletedProcess:
        logger.info("Running EchoApp...")
        command = ["cmd", "/c", "echo", self._value]

        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error("%s", e)
            raise e
        self._result = proc
        logger.info("EchoApp completed.")
        return proc

    def output_from_result(self, allow_stderr: Optional[bool]) -> Self:
        proc = self.result
        try:
            proc.check_returncode()
        except subprocess.CalledProcessError as e:
            self._log_process_std_output("echo", proc)
            raise e
        else:
            self._log_process_std_output("echo", proc)
            if len(proc.stdout) > 0 and allow_stderr is False:
                raise subprocess.CalledProcessError(1, proc.args)
        return self

    def _log_process_std_output(self, process_name: str, proc: subprocess.CompletedProcess) -> None:
        if len(proc.stdout) > 0:
            logger.info("%s full stdout dump: \n%s", process_name, proc.stdout)
        if len(proc.stderr) > 0:
            logger.error("%s full stderr dump: \n%s", process_name, proc.stderr)

    @property
    def result(self) -> subprocess.CompletedProcess:
        if self._result is None:
            raise RuntimeError("The app has not been run yet.")
        return self._result


def make_launcher():
    behavior_cli_args = CliApp.run(
        BaseLauncherCliArgs,
        cli_args=["--temp-dir", "./local/.temp", "--allow-dirty", "--skip-hardware-validation", "--data-dir", "."],
    )

    DATA_DIR = Path(r"./local/data")

    monitor = resource_monitor.ResourceMonitor(
        constrains=[
            resource_monitor.available_storage_constraint_factory(DATA_DIR, 2e11),
            resource_monitor.remote_dir_exists_constraint_factory(Path(r"C:/")),
        ]
    )

    watchdog_settings = WatchdogSettings(
        destination=Path(r"./local/data"),
        project_name="my_project",
    )

    launcher = BaseLauncher(
        rig=RigModel,
        session=AindBehaviorSessionModel,
        task_logic=TaskLogicModel,
        settings=behavior_cli_args,
    )

    picker = DefaultBehaviorPicker(settings=DefaultBehaviorPickerSettings(config_library_dir=LIB_CONFIG))

    launcher.register_hook(
        [
            picker.initialize,
            picker.pick_session,
            picker.pick_task_logic,
            picker.pick_rig,
        ]
    )
    launcher.register_hook(monitor.build_runner())
    launcher.register_hook(EchoApp("Hello World!").build_runner(allow_std_error=True))
    output = launcher.register_hook(DemoAindDataSchemaSessionDataMapper.builder_runner(Path("./mock/script.py")))
    launcher.register_hook(
        MockWatchdogService.build_runner(settings=watchdog_settings, aind_session_data_mapper=output.result)
    )

    return launcher


def create_fake_subjects():
    subjects = ["00000", "123456"]
    for subject in subjects:
        os.makedirs(f"{LIB_CONFIG}/Subjects/{subject}", exist_ok=True)
        with open(f"{LIB_CONFIG}/Subjects/{subject}/task_logic.json", "w", encoding="utf-8") as f:
            f.write(TaskLogicModel(task_parameters={"subject": subject}).model_dump_json(indent=2))


def create_fake_rig():
    computer_name = os.getenv("COMPUTERNAME")
    os.makedirs(_dir := f"{LIB_CONFIG}/Rig/{computer_name}", exist_ok=True)
    with open(f"{_dir}/rig1.json", "w", encoding="utf-8") as f:
        f.write(RigModel().model_dump_json(indent=2))


def main():
    create_fake_subjects()
    create_fake_rig()
    launcher = make_launcher()
    launcher.main()
    return None


if __name__ == "__main__":
    main()
