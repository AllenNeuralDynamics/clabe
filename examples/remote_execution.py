"""A session on two PCs: Bonsai runs on a second rig PC (through its clabe daemon), and one watchdog manifest
collects the data of both machines. See docs/articles/remote_execution.md for each step.

It needs a second PC running ``clabe daemon`` with this repository cloned under ``C:/git``, Bonsai there, and the
watchdog's ``WATCHDOG_EXE`` and ``WATCHDOG_CONFIG`` environment variables here.
"""

from pathlib import Path

from _mocks import LIB_CONFIG, RIG, SUGGESTION, MockTask, RigModel, create_fake_rig, create_fake_subjects
from aind_behavior_curriculum import TrainerState
from aind_behavior_services.session import Session

from clabe import checks, steps
from clabe.aind.watchdog import WatchdogDataTransferService, WatchdogSettings
from clabe.apps import AindBehaviorServicesBonsaiApp, CommandResult
from clabe.launcher import Launcher
from clabe.remote import RemoteRepository
from clabe.stores import LocalFileStore, Store
from clabe.workflow import Workflow

#: The other PC.
OTHER_PC = "rig-b"
#: Where the Bonsai workflow is checked out on the other PC.
BONSAI_ROOT = Path(r"C:\git\AllenNeuralDynamics\Aind.Behavior.VrForaging")

wf = Workflow("remote-execution")


@wf.step(title="Mock data")
def mock_data() -> None:
    """Writes the rig and subject files the store reads (on a real rig they already exist)."""
    create_fake_subjects()
    create_fake_rig()


wf.step(steps.session(experimenter_validator=None))


@wf.step
def store(session: Session, mock_data: None) -> Store:
    """The config store, scoped to the subject."""
    return LocalFileStore(LIB_CONFIG).scoped(subject=session.subject)


@wf.step
def rig(store: Store) -> RigModel:
    """The rig's configuration, from the store."""
    return store.resolve(RIG)


@wf.step
def task(store: Store) -> MockTask:
    """The task of the subject's current stage."""
    trainer_state: TrainerState = store.resolve(SUGGESTION)
    assert trainer_state.stage is not None
    return MockTask.model_validate_json(trainer_state.stage.task.model_dump_json())


wf.step(steps.preflight(checks.free_space(1e9)))


@wf.step
def remote() -> RemoteRepository:
    """The other PC: its daemon is started if needed, and its checkout must match this one."""
    remote = RemoteRepository.from_computer_name(OTHER_PC)
    remote.ensure_running()
    remote.verify(allow_dirty=True)
    return remote


wf.step(steps.register())


@wf.step(title="Remote session folder")
def remote_session(session: Session, rig: RigModel, remote: RemoteRepository, register: Path) -> None:
    """Creates the session folder on the other PC, named like this one's, in the data folder of its rig
    configuration. The run records it, so `launcher.session_directories` lists it."""
    remote.register_session(session, rig.data_directory)


@wf.step(title="Bonsai on the other PC", cancellable=True)
async def bonsai(
    launcher: Launcher, rig: RigModel, session: Session, task: MockTask, remote: RemoteRepository
) -> CommandResult:
    """Built as for a local run; the remote executor copies the rig, session and task there and maps paths."""
    app = AindBehaviorServicesBonsaiApp(
        workflow=BONSAI_ROOT / "src/test_deserialization.bonsai",
        executable=BONSAI_ROOT / "bonsai/bonsai.exe",
        temp_directory=launcher.temp_dir,
        rig=rig,
        session=session,
        task=task,
    )
    return await app.run_async(executor=remote)


@wf.step(title="Transfer")
def transfer(launcher: Launcher, session: Session, remote_session: None, bonsai: CommandResult) -> None:
    """One watchdog manifest for both machines: this session folder, and the other PC's through its share."""
    WatchdogDataTransferService(
        source=launcher.session_directories,
        settings=WatchdogSettings(destination=r"\\allen\aind\scratch\data-uploads", project_name="clabe"),
        session=session,
    ).transfer()
