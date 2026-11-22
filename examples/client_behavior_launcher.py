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

from clabe import checks
from clabe.apps import AindBehaviorServicesBonsaiApp
from clabe.data_transfer.aind_watchdog import WatchdogDataTransferService, WatchdogSettings
from clabe.launcher import Launcher, LauncherCliArgs, experiment
from clabe.remote import RemoteRepository
from clabe.session import SessionBuilder
from clabe.stores import LocalFileStore

logger = logging.getLogger(__name__)

# Data root on the remote machine, and the network share other machines reach it through
REMOTE_DATA_DIRECTORY = r"D:\data"
REMOTE_DATA_SHARE = r"\\rig-b\data"


@experiment()
async def client_experiment(launcher: Launcher) -> None:
    """Demo experiment showcasing CLABE functionality."""

    session = SessionBuilder(launcher, experimenter_validator=lambda name: name).build()
    store = LocalFileStore(LIB_CONFIG).scoped(subject=session.subject)

    rig = store.resolve(RIG)
    launcher.register_session(session, rig.data_directory)
    trainer_state = store.resolve(SUGGESTION)
    task = MockTask.model_validate_json(trainer_state.stage.task.model_dump_json())

    checks.run(checks.free_space(1e9), rig=rig)  # in a workflow: wf.step(steps.preflight(checks.free_space(1e9)))

    # The other machine runs `clabe daemon`, with this repository cloned under C:/git (where it finds it).
    # The first command takes its lease: no other run can use it until this one gives it back.
    remote = RemoteRepository.from_computer_name(
        "localhost", local_path=".", data_roots={REMOTE_DATA_DIRECTORY: REMOTE_DATA_SHARE}
    )
    remote.ensure_running()
    remote.verify(allow_dirty=True)
    # The remote machine saves its data under its own session directory, named like the launcher's
    remote.register_session(session, REMOTE_DATA_DIRECTORY)

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

    # One watchdog manifest covering both machines: the local session directory, and the remote one
    # through its network share. Requires the WATCHDOG_EXE and WATCHDOG_CONFIG environment variables.
    watchdog = WatchdogDataTransferService(
        source=[launcher.session_directory, remote.session_directory_unc],
        settings=WatchdogSettings(destination=r"\\allen\aind\scratch\data-uploads", project_name="clabe"),
        session=session,
    )
    watchdog.transfer()


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
