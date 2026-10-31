"""Tests for RemoteRepository, staged inputs and running apps on a remote executor."""

import json
import sys
import threading
import time
from ipaddress import IPv4Address
from pathlib import Path, PureWindowsPath
from unittest.mock import MagicMock, patch

import git
import pytest
from pydantic import BaseModel, SecretStr

from clabe.apps import AindBehaviorServicesBonsaiApp, Command, CommandError, identity_parser
from clabe.xml_rpc import (
    RemoteRepository,
    RepositoryMismatchError,
    XmlRpcClient,
    XmlRpcClientSettings,
    XmlRpcExecutor,
    XmlRpcServer,
    XmlRpcServerSettings,
)
from clabe.xml_rpc._staged_command import StagedCommand, StagedFile, main, run_staged

_PRINT_FILE = [sys.executable, "-c", "import sys; print(open(sys.argv[1]).read())"]

# Stands in for bonsai.exe: reports where it runs from and the content of each -p:Name=path property
_FAKE_WORKFLOW = """
import json, pathlib, sys
props = dict(a[3:].split("=", 1) for a in sys.argv if a.startswith("-p:"))
print(json.dumps({"workflow": str(pathlib.Path(__file__).resolve()),
                  "inputs": {k: json.loads(pathlib.Path(v).read_text()) for k, v in props.items()}}))
"""


class _Rig(BaseModel):
    name: str = "rig-b"
    channels: int = 3


class _Session(BaseModel):
    subject: str = "123"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    repository = git.Repo.init(path)
    repository.create_remote("origin", "https://github.com/AllenNeuralDynamics/Example.git")
    (path / "workflow.py").write_text(_FAKE_WORKFLOW)
    repository.index.add(["workflow.py"])
    repository.index.commit("Initial commit")
    return path


@pytest.fixture
def local_clone(repo: Path, tmp_path: Path) -> Path:
    """A second checkout of the same commit, standing in for the local repository on another machine."""
    path = tmp_path / "local"
    clone = git.Repo.clone_from(repo, path)
    clone.delete_remote("origin")
    clone.create_remote("origin", "https://github.com/AllenNeuralDynamics/Example")
    return path


@pytest.fixture
def server(tmp_path: Path, repo: Path):
    settings = XmlRpcServerSettings(
        token=SecretStr("test-token"),
        address=IPv4Address("127.0.0.1"),
        port=0,
        file_transfer_dir=tmp_path / "transfer",
        working_dir=repo,
    )
    rpc_server = XmlRpcServer(settings)
    settings.port = rpc_server.server.server_address[1]
    thread = threading.Thread(target=rpc_server.server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)
    yield rpc_server
    rpc_server.server.shutdown()
    rpc_server.executor.shutdown(wait=True)


@pytest.fixture
def client(server: XmlRpcServer) -> XmlRpcClient:
    return XmlRpcClient(
        XmlRpcClientSettings(
            server_url=f"http://127.0.0.1:{server.settings.port}", token="test-token", timeout=30.0, poll_interval=0.1
        )
    )


@pytest.fixture
def remote(client: XmlRpcClient, local_clone: Path) -> RemoteRepository:
    return RemoteRepository(client, local_path=local_clone)


def _command(cmd: list[str], inputs: dict[str, Path] | None = None) -> Command:
    return Command(cmd=cmd, output_parser=identity_parser, inputs=inputs)


class TestStagedCommand:
    def test_inputs_are_written_and_substituted(self, capfd):
        spec = StagedCommand(
            cmd=[*_PRINT_FILE, "{Rig}"], files={"Rig": StagedFile(filename="rig.json", content='{"name": "rig-b"}')}
        )
        assert run_staged(spec) == 0
        assert '{"name": "rig-b"}' in capfd.readouterr().out

    def test_same_filename_inputs_do_not_collide(self, capfd):
        script = "import sys; print(open(sys.argv[1]).read() + open(sys.argv[2]).read())"
        spec = StagedCommand(
            cmd=[sys.executable, "-c", script, "{A}", "{B}"],
            files={"A": StagedFile(filename="x.json", content="1"), "B": StagedFile(filename="x.json", content="2")},
        )
        assert run_staged(spec) == 0
        assert "12" in capfd.readouterr().out

    def test_exit_code_is_returned(self):
        assert run_staged(StagedCommand(cmd=[sys.executable, "-c", "raise SystemExit(4)"])) == 4

    def test_main_deletes_spec(self, tmp_path: Path):
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(StagedCommand(cmd=[sys.executable, "-c", "pass"]).model_dump_json())
        assert main(["prog", str(spec_path)]) == 0
        assert not spec_path.exists()


class TestPathMapping:
    @pytest.fixture
    def executor(self) -> XmlRpcExecutor:
        return XmlRpcExecutor(MagicMock(), path_mapping={r"C:\git\repo": r"D:\rigs\repo"})

    def test_rewrites_paths_under_local_root(self, executor: XmlRpcExecutor):
        assert executor._map_paths(r"C:\git\repo\src\main.bonsai") == r"D:\rigs\repo\src\main.bonsai"
        assert executor._map_paths(r"-p:Path=C:\git\repo\x") == r"-p:Path=D:\rigs\repo\x"
        assert executor._map_paths(r"C:\git\repo") == r"D:\rigs\repo"

    def test_leaves_sibling_directories_alone(self, executor: XmlRpcExecutor):
        assert executor._map_paths(r"C:\git\repo2\x") == r"C:\git\repo2\x"

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows paths compare case-insensitively")
    def test_is_case_insensitive_on_windows(self, executor: XmlRpcExecutor):
        assert executor._map_paths(r"c:\GIT\repo\x") == r"D:\rigs\repo\x"

    def test_command_without_inputs_or_mapping_is_unchanged(self):
        executor = XmlRpcExecutor(MagicMock())
        assert executor._prepare(_command(["echo", "hi"])) == ["echo", "hi"]


class TestRun:
    def test_runs_in_remote_repository(self, remote: RemoteRepository, repo: Path):
        result = remote.run(_command([sys.executable, "-c", "import os; print(os.getcwd())"]))
        assert Path(result.stdout.strip()) == repo

    def test_inputs_are_staged_on_remote(self, remote: RemoteRepository, server: XmlRpcServer, tmp_path: Path):
        rig_file = tmp_path / "rig.json"
        rig_file.write_text(_Rig().model_dump_json())
        result = remote.run(_command([*_PRINT_FILE, str(rig_file)], inputs={"RigPath": rig_file}))
        assert json.loads(result.stdout) == {"name": "rig-b", "channels": 3}
        assert not any(server.settings.file_transfer_dir.iterdir()), "staged spec should be cleaned up"

    def test_failure_raises(self, remote: RemoteRepository):
        with pytest.raises(CommandError):
            remote.run(_command([sys.executable, "-c", "raise SystemExit(2)"]))


class TestBonsaiAppOnRemote:
    def test_app_registers_models_as_inputs(self, tmp_path: Path):
        app = AindBehaviorServicesBonsaiApp(
            "main.bonsai",
            executable=Path(sys.executable),
            temp_directory=tmp_path,
            rig=_Rig(),
            session=_Session(),
            skip_validation=True,
        )
        assert set(app.command.inputs) == {"RigPath", "SessionPath"}
        for name, path in app.command.inputs.items():
            assert f"-p:{name}={path}" in app.command.cmd

    @pytest.mark.asyncio
    async def test_app_runs_unchanged_on_remote(
        self, remote: RemoteRepository, repo: Path, local_clone: Path, tmp_path
    ):
        app = AindBehaviorServicesBonsaiApp(
            local_clone / "workflow.py",
            executable=Path(sys.executable),
            is_editor_mode=False,
            temp_directory=tmp_path / "launcher_temp",
            rig=_Rig(name="from-launcher"),
            session=_Session(),
        )
        result = await app.run_async(executor=remote)

        output = json.loads(result.stdout)
        assert Path(output["workflow"]) == (repo / "workflow.py").resolve(), "local repo path should map to remote"
        assert output["inputs"] == {
            "RigPath": {"name": "from-launcher", "channels": 3},
            "SessionPath": {"subject": "123"},
        }

    def test_executor_and_executor_kwargs_are_exclusive(self, remote: RemoteRepository, tmp_path: Path):
        app = AindBehaviorServicesBonsaiApp(
            "main.bonsai", executable=Path(sys.executable), temp_directory=tmp_path, skip_validation=True
        )
        with pytest.raises(ValueError, match="either executor or executor_kwargs"):
            app.run(executor_kwargs={"cwd": "."}, executor=remote)


class TestVerify:
    def test_matching_repository(self, remote: RemoteRepository):
        remote.verify()

    def test_dirty_remote(self, remote: RemoteRepository, repo: Path):
        (repo / "untracked.txt").write_text("change")
        with pytest.raises(RepositoryMismatchError, match="uncommitted"):
            remote.verify()
        remote.verify(allow_dirty=True)

    def test_different_commit(self, remote: RemoteRepository, local_clone: Path):
        clone = git.Repo(local_clone)
        (local_clone / "other.txt").write_text("x")
        clone.index.add(["other.txt"])
        clone.index.commit("Local only")
        with pytest.raises(RepositoryMismatchError, match="commit"):
            remote.verify()

    def test_different_working_dir(self, client: XmlRpcClient, local_clone: Path, tmp_path: Path):
        with pytest.raises(RepositoryMismatchError, match="server runs from"):
            RemoteRepository(client, path=str(tmp_path / "elsewhere"), local_path=local_clone).verify()

    def test_path_matching_working_dir_is_accepted(self, client: XmlRpcClient, repo: Path, local_clone: Path):
        RemoteRepository(client, path=str(repo).upper(), local_path=local_clone).verify()

    def test_clabe_version_mismatch(self, remote: RemoteRepository):
        with patch("clabe.xml_rpc._remote.__version__", "0.0.0-other"), pytest.raises(RepositoryMismatchError):
            remote.verify()


class TestToUnc:
    @pytest.fixture
    def remote(self, client: XmlRpcClient) -> RemoteRepository:
        return RemoteRepository(client, data_roots={r"D:\data": r"\\rig-b\data", r"D:\data\videos": r"\\rig-b\videos"})

    def test_maps_to_share(self, remote: RemoteRepository):
        assert remote.to_unc(r"D:\data\session_1\fib") == PureWindowsPath(r"\\rig-b\data\session_1\fib")

    def test_most_specific_root_wins(self, remote: RemoteRepository):
        assert remote.to_unc(r"D:\data\videos\cam0.mp4") == PureWindowsPath(r"\\rig-b\videos\cam0.mp4")

    def test_is_case_insensitive(self, remote: RemoteRepository):
        assert remote.to_unc(r"d:\DATA\s") == PureWindowsPath(r"\\rig-b\data\s")

    def test_unmapped_path_raises(self, remote: RemoteRepository):
        with pytest.raises(ValueError, match="not under any data root"):
            remote.to_unc(r"E:\other")


class TestEnsureRunning:
    def test_does_nothing_when_server_responds(self, remote: RemoteRepository):
        with patch("clabe.xml_rpc._remote.start_task") as start:
            remote.ensure_running()
        start.assert_not_called()

    def test_starts_task_on_remote_host(self, remote: RemoteRepository):
        with (
            patch.object(remote.client, "ping", side_effect=[False, False, True]),
            patch("clabe.xml_rpc._remote.start_task") as start,
        ):
            remote.ensure_running(poll_interval=0.01)
        start.assert_called_once_with(remote.task_name, host="127.0.0.1")

    def test_times_out(self, remote: RemoteRepository):
        with (
            patch.object(remote.client, "ping", return_value=False),
            patch("clabe.xml_rpc._remote.start_task"),
            pytest.raises(TimeoutError),
        ):
            remote.ensure_running(timeout=0.05, poll_interval=0.01)
