"""Tests for running the XML-RPC server across rigs: auth, working directory, server info, tracing and task registration."""

import os
import sys
import threading
import time
import xml.etree.ElementTree as ET
from ipaddress import IPv4Address
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import SecretStr
from pydantic_settings import CliApp

from clabe import __version__
from clabe.cli import CliAppSettings
from clabe.xml_rpc import XmlRpcClient, XmlRpcClientSettings
from clabe.xml_rpc._scheduled_task import TASK_NAME, build_task_xml, register_task, start_task, unregister_task
from clabe.xml_rpc._server import XmlRpcServer, XmlRpcServerSettings, _RegisterTaskCli, _ServeCli, _stdin_is_console

_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
_PRINT_CWD = [sys.executable, "-c", "import os; print(os.getcwd())"]


@pytest.fixture
def working_dir(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


@pytest.fixture
def server(tmp_path: Path, working_dir: Path):
    settings = XmlRpcServerSettings(
        token=SecretStr("test-token"),
        address=IPv4Address("127.0.0.1"),
        port=0,
        file_transfer_dir=tmp_path / "transfer",
        working_dir=working_dir,
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
    settings = server.settings
    return XmlRpcClient(
        XmlRpcClientSettings(server_url=f"http://127.0.0.1:{settings.port}", token="test-token", timeout=10.0)
    )


class TestToken:
    def test_token_is_required(self):
        assert XmlRpcServerSettings.model_fields["token"].is_required()

    def test_empty_token_is_rejected(self, tmp_path: Path):
        settings = XmlRpcServerSettings(token=SecretStr(""), port=0, file_transfer_dir=tmp_path)
        with pytest.raises(ValueError, match="non-empty"):
            XmlRpcServer(settings)

    def test_cli_uses_configured_token(self):
        cli = _ServeCli(token=SecretStr("from-config"))
        assert cli._resolve_token().get_secret_value() == "from-config"

    def test_cli_raises_without_token_when_not_interactive(self):
        cli = _ServeCli(token=None)
        with (
            patch("clabe.xml_rpc._server._stdin_is_console", return_value=False),
            pytest.raises(SystemExit, match="clabe.yml"),
        ):
            cli._resolve_token()

    def test_cli_prompts_for_token_when_interactive(self):
        cli = _ServeCli(token=None)
        with (
            patch("clabe.xml_rpc._server._stdin_is_console", return_value=True),
            patch("clabe.xml_rpc._server.getpass.getpass", return_value="typed"),
        ):
            assert cli._resolve_token().get_secret_value() == "typed"

    def test_cli_non_interactive_never_prompts(self):
        cli = _ServeCli(token=None, non_interactive=True)
        with (
            patch("clabe.xml_rpc._server._stdin_is_console", return_value=True),
            patch("clabe.xml_rpc._server.getpass.getpass") as prompt,
            pytest.raises(SystemExit),
        ):
            cli._resolve_token()
        prompt.assert_not_called()

    def test_nul_stdin_is_not_a_console(self):
        with open(os.devnull) as devnull, patch("sys.stdin", devnull):
            assert not _stdin_is_console()


class TestWorkingDirectory:
    def test_missing_working_dir_is_rejected(self, tmp_path: Path):
        settings = XmlRpcServerSettings(
            token=SecretStr("t"), port=0, file_transfer_dir=tmp_path, working_dir=tmp_path / "missing"
        )
        with pytest.raises(ValueError, match="Working directory"):
            XmlRpcServer(settings)

    def test_commands_run_in_working_dir_by_default(self, client: XmlRpcClient, working_dir: Path):
        result = client.run_command(_PRINT_CWD)
        assert Path(result.stdout.strip()) == working_dir

    def test_cwd_overrides_working_dir(self, client: XmlRpcClient, tmp_path: Path):
        result = client.run_command(_PRINT_CWD, cwd=str(tmp_path))
        assert Path(result.stdout.strip()) == tmp_path


class TestServerInfo:
    def test_server_info(self, client: XmlRpcClient, working_dir: Path):
        info = client.server_info()
        assert info.success
        assert info.clabe_version == __version__
        assert info.hostname
        assert Path(info.working_dir) == working_dir.resolve()
        assert Path(info.python_executable) == Path(sys.executable)


class TestTracing:
    def test_job_span_nests_under_client_trace(self, client: XmlRpcClient):
        sdk_trace = pytest.importorskip("opentelemetry.sdk.trace")
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        exporter = InMemorySpanExporter()
        provider = sdk_trace.TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("test")

        with patch("clabe.xml_rpc._server._tracer", tracer), tracer.start_as_current_span("launcher-run") as parent:
            client.run_command([sys.executable, "-c", "raise SystemExit(3)"])

        job = next(s for s in exporter.get_finished_spans() if s.name == "xml_rpc.job")
        assert job.parent is not None
        assert job.parent.span_id == parent.get_span_context().span_id
        assert job.context.trace_id == parent.get_span_context().trace_id
        assert job.attributes["process.exit.code"] == 3
        assert not job.status.is_ok


class TestScheduledTask:
    def test_task_xml(self, working_dir: Path):
        command = [
            r"C:\repo\.venv\Scripts\python.exe",
            "-m",
            "clabe.cli",
            "xml-rpc-server",
            "serve",
            "--dump",
            r"C:\a b\log",
        ]
        root = ET.fromstring(build_task_xml(command, working_dir, user="DOMAIN\\rig"))

        assert root.find("t:Principals/t:Principal/t:LogonType", _NS).text == "InteractiveToken"
        assert root.find("t:Principals/t:Principal/t:UserId", _NS).text == "DOMAIN\\rig"
        assert root.find("t:Triggers/t:LogonTrigger/t:UserId", _NS).text == "DOMAIN\\rig"
        assert root.find("t:Settings/t:ExecutionTimeLimit", _NS).text == "PT0S"
        assert root.find("t:Settings/t:RestartOnFailure/t:Interval", _NS).text == "PT1M"
        exec_ = root.find("t:Actions/t:Exec", _NS)
        assert exec_.find("t:Command", _NS).text == command[0]
        assert exec_.find("t:Arguments", _NS).text == '-m clabe.cli xml-rpc-server serve --dump "C:\\a b\\log"'
        assert exec_.find("t:WorkingDirectory", _NS).text == str(working_dir)

    @pytest.mark.skipif(sys.platform != "win32", reason="schtasks is Windows-only")
    def test_register_creates_and_starts_task(self, working_dir: Path):
        seen = {}

        def fake_run(args, **kwargs):
            if args[1] == "/create":
                xml_path = Path(args[args.index("/xml") + 1])
                seen["xml"] = xml_path.read_text(encoding="utf-16")
                seen["xml_path"] = xml_path

        with patch("clabe.xml_rpc._scheduled_task.subprocess.run", side_effect=fake_run) as run:
            register_task(["python.exe", "-m", "clabe.cli"], working_dir)

        assert [call.args[0][:3] for call in run.call_args_list] == [
            ["schtasks", "/create", "/tn"],
            ["schtasks", "/run", "/tn"],
        ]
        assert "<WorkingDirectory>" in seen["xml"]
        assert not seen["xml_path"].exists()

    @pytest.mark.skipif(sys.platform != "win32", reason="schtasks is Windows-only")
    def test_start_task_on_remote_host(self):
        with patch("clabe.xml_rpc._scheduled_task.subprocess.run") as run:
            start_task(host="rig-b")
        assert run.call_args.args[0] == ["schtasks", "/run", "/s", "rig-b", "/tn", TASK_NAME]

    @pytest.mark.skipif(sys.platform != "win32", reason="schtasks is Windows-only")
    def test_unregister_deletes_task(self):
        with patch("clabe.xml_rpc._scheduled_task.subprocess.run") as run:
            unregister_task()
        assert run.call_args.args[0] == ["schtasks", "/delete", "/tn", TASK_NAME, "/f"]

    def test_register_task_cli_uses_current_directory(self, working_dir: Path, monkeypatch):
        monkeypatch.chdir(working_dir)
        with patch("clabe.xml_rpc._server.register_task") as register:
            _RegisterTaskCli(dump=Path("server.log")).cli_cmd()

        command, task_working_dir = register.call_args.args
        assert task_working_dir == working_dir
        assert command[:6] == [sys.executable, "-m", "clabe.cli", "xml-rpc-server", "serve", "--non-interactive"]
        assert command[command.index("--dump") + 1] == str(working_dir / "server.log")
        assert "--token" not in command
        assert "--port" not in command, "settings are read from clabe.yml when the server starts"


class TestCli:
    def test_register_task_subcommand(self):
        with patch("clabe.xml_rpc._server.register_task") as register:
            CliApp.run(CliAppSettings, cli_args=["xml-rpc-server", "register-task"])
        register.assert_called_once()

    def test_unregister_task_subcommand(self):
        with patch("clabe.xml_rpc._server.unregister_task") as unregister:
            CliApp.run(CliAppSettings, cli_args=["xml-rpc-server", "unregister-task"])
        unregister.assert_called_once_with()

    def test_serve_subcommand(self, tmp_path: Path):
        with patch("clabe.xml_rpc._server.XmlRpcServer") as server:
            CliApp.run(
                CliAppSettings,
                cli_args=[
                    "xml-rpc-server",
                    "serve",
                    "--token",
                    "abc",
                    "--port",
                    "9100",
                    "--working-dir",
                    str(tmp_path),
                ],
            )
        settings = server.call_args.kwargs["settings"]
        assert settings.token.get_secret_value() == "abc"
        assert settings.port == 9100
        assert settings.working_dir == tmp_path
        server.return_value.server.serve_forever.assert_called_once()
