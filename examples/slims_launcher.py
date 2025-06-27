import logging
import subprocess
from pathlib import Path
from typing import Literal, Optional, Self

from aind_behavior_services.rig import AindBehaviorRigModel
from aind_behavior_services.session import AindBehaviorSessionModel
from aind_behavior_services.task_logic import AindBehaviorTaskLogicModel
from pydantic import Field
from pydantic_settings import CliApp
from typing_extensions import override

from clabe import resource_monitor
from clabe.apps import App
from clabe.behavior_launcher import (
    BehaviorCliArgs,
    BehaviorLauncher,
    BehaviorServicesFactoryManager,
    SlimsPicker,
)

# TODO: refactor this someday to use a common module

logger = logging.getLogger(__name__)

TASK_NAME = "RandomTask"


class RigModel(AindBehaviorRigModel):
    rig_name: str = Field("TestRig", description="Rig name")
    version: Literal["0.0.0"] = "0.0.0"


class TaskLogicModel(AindBehaviorTaskLogicModel):
    version: Literal["0.0.0"] = "0.0.0"
    name: Literal[TASK_NAME] = TASK_NAME


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


DATA_DIR = Path(r"./local/data")
srv = BehaviorServicesFactoryManager()
srv.attach_app(EchoApp("hello world"))
srv.attach_resource_monitor(
    resource_monitor.ResourceMonitor(
        constrains=[
            resource_monitor.available_storage_constraint_factory(DATA_DIR, 2e11),
            resource_monitor.remote_dir_exists_constraint_factory(Path(r"C:/")),
        ]
    )
)


def make_launcher():
    behavior_cli_args = CliApp.run(
        BehaviorCliArgs,
        cli_args=["--temp-dir", "./local/.temp", "--allow-dirty", "--skip-hardware-validation", "--data-dir", "."],
    )

    return BehaviorLauncher(
        rig_schema_model=RigModel,
        session_schema_model=AindBehaviorSessionModel,
        task_logic_schema_model=TaskLogicModel,
        picker=SlimsPicker(),
        services=srv,
        settings=behavior_cli_args,
    )


def main():
    launcher = make_launcher()
    launcher.main()
    return None


if __name__ == "__main__":
    main()
