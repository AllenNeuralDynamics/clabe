import logging

from _mocks import (
    LIB_CONFIG,
    AindBehaviorSessionModel,
    RigModel,
    TaskLogicModel,
    create_fake_rig,
    create_fake_subjects,
)
from pydantic_settings import CliApp

from clabe import resource_monitor
from clabe.apps import PythonScriptApp
from clabe.launcher import Launcher, LauncherCliArgs, experiment
from clabe.pickers import DefaultBehaviorPicker, DefaultBehaviorPickerSettings
from clabe.xml_rpc import XmlRpcClient, XmlRpcClientSettings

logger = logging.getLogger(__name__)


@experiment()
async def client_experiment(launcher: Launcher) -> None:
    """Demo experiment showcasing CLABE functionality."""
    picker = DefaultBehaviorPicker(
        launcher=launcher,
        settings=DefaultBehaviorPickerSettings(config_library_dir=LIB_CONFIG),
        experimenter_validator=lambda _: True,
    )

    session = picker.pick_session(AindBehaviorSessionModel)
    rig = picker.pick_rig(RigModel)
    launcher.register_session(session, rig.data_directory)
    trainer_state, _ = picker.pick_trainer_state(TaskLogicModel)

    resource_monitor.ResourceMonitor(
        constrains=[
            resource_monitor.available_storage_constraint_factory_from_rig(rig, 2e11),
        ]
    ).run()

    xml_rpc_client = XmlRpcClient(settings=XmlRpcClientSettings(server_url="http://localhost:8000", token="42"))
    upload_response = xml_rpc_client.upload_model(trainer_state, "trainer_state.json")

    def fmt(value: str) -> str:
        python_string = f"""
import time
print('Hello {value}')
time.sleep(2)
print('DONE')
with open({upload_response.path}, 'r') as f:
    data = f.read()
    print('Uploaded Data:', data)
        """

        return f'python -c "{python_string}"'

    app_1_result = await xml_rpc_client.run_async(PythonScriptApp(script=fmt("Behavior")).command)
    print(app_1_result)
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
        ],
    )

    launcher = Launcher(settings=behavior_cli_args)
    launcher.run_experiment(client_experiment)
    return None


if __name__ == "__main__":
    main()
