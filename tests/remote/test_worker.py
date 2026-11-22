"""The worker role of the daemon, driven by WorkerClient, RemoteExecutor and RemoteRepository."""

import asyncio
import subprocess
import sys
import threading
import time
from pathlib import Path, PureWindowsPath

import pytest
import requests
import uvicorn
from aind_behavior_services import Session

from clabe.apps import Command, identity_parser
from clabe.daemon import DaemonSettings, RepositoryConfig, create_daemon
from clabe.remote import (
    JobStatus,
    RemoteExecutor,
    RemoteRepository,
    RepositoryMismatchError,
    WorkerClient,
    WorkerClientSettings,
    WorkerError,
    to_unc,
)
from clabe.server import free_port


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "Aind.Experiment.Demo"
    repo.mkdir()
    (repo / "README.md").write_text("demo\n")
    _git(repo, "init", "-q")
    _git(repo, "remote", "add", "origin", "https://github.com/example/Aind.Experiment.Demo.git")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
    return repo


@pytest.fixture
def worker_url(repository: Path, tmp_path: Path):
    settings = DaemonSettings(
        port=free_port("127.0.0.1", 18170),
        repositories=[RepositoryConfig(path=repository)],
        command=[sys.executable, "-m", "clabe.cli"],
        file_transfer_dir=tmp_path / "transfer",
        runs_directory=tmp_path / "runs",
    )
    server = uvicorn.Server(
        uvicorn.Config(
            create_daemon(settings, token="w0rker"), host="127.0.0.1", port=settings.port, log_level="warning"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{settings.port}"
    server.should_exit = True
    thread.join(5)


def _client(url: str, **overrides) -> WorkerClient:
    return WorkerClient(WorkerClientSettings(url=url, token="w0rker", poll_interval=0.05, **overrides))


def test_jobs_and_files(worker_url, repository, tmp_path):
    client = _client(worker_url)
    info = client.info()
    assert info.repository == "Aind.Experiment.Demo" and Path(info.working_dir) == repository.resolve()
    result = client.run_command([sys.executable, "-c", "import os; print(os.getcwd())"])
    assert result.status is JobStatus.DONE and Path(result.stdout.strip()) == repository.resolve()
    failed = client.run_command([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert failed.returncode == 3

    local = tmp_path / "rig.json"
    local.write_text('{"name": "rig-3"}')
    uploaded = client.upload_file(local)
    assert uploaded.size == local.stat().st_size and Path(uploaded.path).read_text() == local.read_text()
    assert [f.name for f in client.list_files()] == ["rig.json"]
    assert client.download_file("rig.json", tmp_path / "back.json").read_text() == local.read_text()
    with pytest.raises(WorkerError) as refused:
        client.upload_bytes(b"x", r"sub\x.json")
    assert refused.value.status == 422
    client.delete_file("rig.json")
    assert client.list_files() == []
    assert requests.get(f"{worker_url}/api/worker/info", timeout=5).status_code == 401


def test_a_command_s_inputs_are_staged_and_paths_mapped(worker_url, repository, tmp_path):
    config = tmp_path / "local" / "session.json"
    config.parent.mkdir()
    config.write_text('{"subject": "mouse-7"}')
    command = Command(
        cmd=[
            sys.executable,
            "-c",
            "import sys; print(open(sys.argv[1]).read(), sys.argv[2])",
            str(config),
            "C:/repo/x",
        ],
        output_parser=identity_parser,
        inputs={"session": config},
    )
    executor = RemoteExecutor(_client(worker_url), path_mapping={"C:/repo": "D:/remote-repo"})
    result = executor.run(command)
    assert '{"subject": "mouse-7"}' in result.stdout and "D:/remote-repo/x" in result.stdout


def test_cancelling_a_remote_command_stops_it_on_the_worker(worker_url, tmp_path):
    beat = tmp_path / "beat"
    script = f"import time, pathlib\nwhile True:\n    pathlib.Path({str(beat)!r}).write_text(str(time.time()))\n    time.sleep(0.05)\n"
    client = _client(worker_url)

    async def main():
        task = asyncio.ensure_future(
            RemoteExecutor(client).run_async(Command(cmd=[sys.executable, "-c", script], output_parser=identity_parser))
        )
        while not beat.exists():
            await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    last = beat.read_text()
    time.sleep(0.5)
    assert beat.read_text() == last
    assert client.list_jobs().running == []


def test_a_lease_reserves_the_worker(worker_url):
    first, second = _client(worker_url), _client(worker_url)
    lease = first.lease(owner="rig-3 / vr-foraging", run_id="r1")
    assert first.info().leased_by == "rig-3 / vr-foraging"
    assert first.run_command([sys.executable, "-c", "print(1)"]).returncode == 0
    with pytest.raises(WorkerError) as refused:
        second.run_command([sys.executable, "-c", "print(1)"])
    assert refused.value.status == 423 and "rig-3 / vr-foraging" in str(refused.value)
    with pytest.raises(WorkerError):
        second.lease(owner="someone else")
    # the machine starts no run of its own while leased
    launched = requests.post(
        f"{worker_url}/api/daemon/runs",
        json={"repository": "Aind.Experiment.Demo", "file": "x.py"},
        headers={"Authorization": "Bearer w0rker"},
        timeout=5,
    )
    assert launched.status_code == 423
    info = requests.get(f"{worker_url}/api/daemon/info", headers={"Authorization": "Bearer w0rker"}, timeout=5).json()
    assert (info["status"], info["leased_by"]) == ("leased", "rig-3 / vr-foraging")
    assert first.lease().lease_id == lease.lease_id  # renewing keeps it
    first.release()
    assert second.run_command([sys.executable, "-c", "print(2)"]).stdout.strip() == "2"


def test_an_unrenewed_lease_expires(worker_url):
    holder = _client(worker_url, lease_ttl=0.3)
    holder.lease(owner="crashed launcher")
    holder._stop_heartbeat.set()  # stands in for a launcher that died
    time.sleep(0.6)
    assert _client(worker_url).run_command([sys.executable, "-c", "print(1)"]).returncode == 0


def test_a_remote_repository_matches_runs_and_writes(worker_url, repository):
    remote = RemoteRepository(
        _client(worker_url, repository="Aind.Experiment.Demo"),
        local_path=repository,
        data_roots={str(repository.parent): r"\\rig-b\data"},
    )
    remote.ensure_running()
    remote.verify()
    assert remote.client.leased  # verifying ran a command: the machine is taken
    session = Session(subject="mouse-7", experiment="demo", session_name="mouse-7_2026")
    remote.register_session(session, repository.parent / "data")
    assert (repository.parent / "data" / "mouse-7_2026").is_dir()
    remote.write_file(remote.session_directory / "notes.txt", "hello")
    assert (repository.parent / "data" / "mouse-7_2026" / "notes.txt").read_text() == "hello"
    assert remote.session_directory_unc == PureWindowsPath(r"\\rig-b\data\data\mouse-7_2026")
    (repository / "README.md").write_text("changed\n")
    with pytest.raises(RepositoryMismatchError, match="uncommitted"):
        remote.verify()
    remote.verify(allow_dirty=True)
    remote.release()
    assert not remote.client.leased


def test_to_unc():
    assert to_unc(r"D:\data\s1", {r"D:\data": r"\\rig-b\data"}) == PureWindowsPath(r"\\rig-b\data\s1")
    assert to_unc(r"E:\raw\s1", host="rig-b") == PureWindowsPath(r"\\rig-b\E$\raw\s1")
    with pytest.raises(ValueError):
        to_unc(r"\\server\share\x")


def test_a_run_that_ends_gives_the_worker_back_and_an_aborted_one_stops_its_jobs(worker_url, repository, tmp_path):
    from clabe.workflow import Abort, Run, RunStatus, Workflow

    beat = tmp_path / "beat"
    script = f"import time, pathlib\nwhile True:\n    pathlib.Path({str(beat)!r}).write_text(str(time.time()))\n    time.sleep(0.05)\n"
    remote = RemoteRepository(_client(worker_url, repository="Aind.Experiment.Demo"), local_path=repository)
    wf = Workflow("demo")

    @wf.step
    def start_recording() -> str:
        remote.lease()
        return remote.client.submit_command([sys.executable, "-c", script]).job_id

    @wf.step
    def operator(start_recording: str) -> None:
        while not beat.exists():
            time.sleep(0.05)
        raise Abort("operator stopped the session")

    run = Run(wf)
    run.start()
    assert run.wait(30) and run.status is RunStatus.ABORTED
    assert not remote.client.leased
    last = beat.read_text()
    time.sleep(0.5)
    assert beat.read_text() == last  # the recording was stopped with the run
    other = _client(worker_url)
    assert other.run_command([sys.executable, "-c", "print(1)"]).returncode == 0  # and the machine is free

    completed = Workflow("ok")
    completed.step(
        lambda: remote.run(Command(cmd=[sys.executable, "-c", "print(2)"], output_parser=identity_parser)).stdout,
        name="a",
    )
    run = Run(completed)
    run.start()
    assert run.wait(30) and run.status is RunStatus.COMPLETED and not remote.client.leased


def test_a_remote_command_shows_as_an_activity_with_its_output(worker_url, repository):
    from clabe.workflow import Run, RunStatus, Workflow
    from clabe.workflow.events import ActivityEnded, ActivityOutput, ActivityStarted

    remote = RemoteRepository(_client(worker_url, repository="Aind.Experiment.Demo"), local_path=repository)
    script = "import time\nfor i in range(8):\n    print(f'frame {i}', flush=True)\n    time.sleep(0.25)\n"
    wf = Workflow("demo")
    wf.step(lambda: remote.run(Command(cmd=[sys.executable, "-c", script], output_parser=identity_parser)), name="a")
    run = Run(wf)
    run.start()
    assert run.wait(30) and run.status is RunStatus.COMPLETED
    events = run.events.since(0)
    started = [e for e in events if isinstance(e, ActivityStarted) and e.machine == "127.0.0.1"]
    assert len(started) == 1 and started[0].step == "a" and "on 127.0.0.1" in started[0].description
    lines = [line for e in events if isinstance(e, ActivityOutput) for line in e.lines]
    assert lines == [f"frame {i}" for i in range(8)]
    assert sum(isinstance(e, ActivityOutput) for e in events) >= 2  # streamed while running, not only at the end
    assert any(isinstance(e, ActivityEnded) and e.activity_id == started[0].activity_id for e in events)
