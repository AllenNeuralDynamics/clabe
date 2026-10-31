import logging
from pathlib import Path

from _mocks import (
    LIB_CONFIG,
    RIG,
    SUGGESTION,
    MockTask,
    create_fake_rig,
    create_fake_subjects,
)
from pydantic_settings import CliApp

from clabe import resource_monitor
from clabe.apps import AindBehaviorServicesBonsaiApp
from clabe.launcher import Launcher, LauncherCliArgs, experiment
from clabe.session import SessionBuilder
from clabe.stores import LocalFileStore
from clabe.xml_rpc import RemoteRepository, XmlRpcClient, XmlRpcClientSettings

logger = logging.getLogger(__name__)


@experiment()
async def client_experiment(launcher: Launcher) -> None:
    """Demo experiment showcasing CLABE functionality."""

    session = SessionBuilder(launcher, experimenter_validator=lambda name: name).build()
    store = LocalFileStore(LIB_CONFIG).scoped(subject=session.subject)

    rig = store.resolve(RIG)
    launcher.register_session(session, rig.data_directory)
    trainer_state = store.resolve(SUGGESTION)
    task = MockTask.model_validate_json(trainer_state.stage.task.model_dump_json())

    resource_monitor.ResourceMonitor(
        constrains=[
            resource_monitor.available_storage_constraint_factory_from_rig(rig, 1e9),
        ]
    ).run()

    remote = RemoteRepository(
        XmlRpcClient(settings=XmlRpcClientSettings(server_url="http://localhost:8000", token="42")),
        local_path=".",
    )
    remote.ensure_running()
    remote.verify(allow_dirty=True)

    # The app is built exactly as for a local run. Running it on the remote executor copies the
    # rig, session and task to the remote machine and maps paths in this repository to the remote one.
    bonsai_root = Path(r"C:\git\AllenNeuralDynamics\Aind.Behavior.VrForaging")
    bonsai_app = AindBehaviorServicesBonsaiApp(
        workflow=bonsai_root / "src/test_deserialization.bonsai",
        executable=bonsai_root / "bonsai/bonsai.exe",
        temp_directory=launcher.temp_dir,
        rig=rig,
        session=session,
        task=task,
    )
    bonsai_app_result = await bonsai_app.run_async(executor=remote)
    print(bonsai_app_result)


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


if __name__ == "__main__":
    main()
