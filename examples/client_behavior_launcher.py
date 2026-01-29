import logging
from pathlib import Path

from _mocks import (
    LIB_CONFIG,
    MockTask,
    RigModel,
    Session,
    create_fake_rig,
    create_fake_subjects,
)
from pydantic_settings import CliApp

from clabe import resource_monitor
from clabe.apps import BonsaiApp
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

    session = picker.pick_session(Session)
    rig = picker.pick_rig(RigModel)
    launcher.register_session(session, rig.data_directory)
    trainer_state, task = picker.pick_trainer_state(MockTask)

    resource_monitor.ResourceMonitor(
        constrains=[
            resource_monitor.available_storage_constraint_factory_from_rig(rig, 2e11),
        ]
    ).run()

    xml_rpc_client = XmlRpcClient(settings=XmlRpcClientSettings(server_url="http://localhost:8000", token="42"))

    bonsai_root = Path(r"C:\git\AllenNeuralDynamics\Aind.Behavior.VrForaging")
    session_response = xml_rpc_client.upload_model(session, "session.json")
    rig_response = xml_rpc_client.upload_model(rig, "rig.json")
    task_response = xml_rpc_client.upload_model(task, "task.json")
    assert rig_response.path is not None
    assert session_response.path is not None
    assert task_response.path is not None

    bonsai_app_result = await xml_rpc_client.run_async(
        BonsaiApp(
            workflow=bonsai_root / "src/test_deserialization.bonsai",
            executable=bonsai_root / "bonsai/bonsai.exe",
            additional_externalized_properties={
                "RigPath": rig_response.path,
                "SessionPath": session_response.path,
                "TaskPath": task_response.path,
            },
        ).command
    )
    print(bonsai_app_result)
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
