import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from clabe.apps import BonsaiApp, BonsaiAppSettings, OpenEphysApp, OpenEphysAppSettings, PythonScriptApp
from clabe.apps._open_ephys import Status, _OpenEphysGuiClient


@pytest.fixture
def bonsai_app(mock_ui_helper) -> BonsaiApp:
    """BonsaiApp fixture."""
    workflow = Path("test_workflow.bonsai")
    executable = Path("bonsai/bonsai.exe")
    settings = BonsaiAppSettings(executable=executable, workflow=workflow)
    app = BonsaiApp(settings=settings, ui_helper=mock_ui_helper)
    return app


class TestBonsaiApp:
    """Test BonsaiApp."""

    @patch("subprocess.run")
    @patch("pathlib.Path.exists", return_value=True)
    def test_run(self, mock_pathlib: MagicMock, mock_subprocess_run: MagicMock, bonsai_app: BonsaiApp) -> None:
        """Test run."""
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_subprocess_run.return_value = mock_result
        result = bonsai_app.run()._completed_process
        assert result == mock_result
        mock_subprocess_run.assert_called_once()

    def test_validate(self, bonsai_app: BonsaiApp) -> None:
        """Test validate."""
        with patch("pathlib.Path.exists", return_value=True):
            assert bonsai_app.validate()

    def test_validate_missing_file(self, bonsai_app: BonsaiApp) -> None:
        """Test validate missing file."""
        with patch("pathlib.Path.exists", side_effect=[False, True, True]):
            with pytest.raises(FileNotFoundError):
                bonsai_app.validate()

    def test_raises_before_run(self, bonsai_app: BonsaiApp) -> None:
        """Test result property."""
        with pytest.raises(RuntimeError):
            bonsai_app.get_result(allow_stderr=True)

    def test__process_process_output(self, mock_ui_helper, bonsai_app: BonsaiApp) -> None:
        """Test output from result."""
        mock_ui_helper._prompt_yes_no_question.return_value = True
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.stdout = "output"
        mock_result.stderr = ""
        bonsai_app._completed_process = mock_result
        with patch.object(mock_result, "check_returncode", side_effect=subprocess.CalledProcessError(1, "cmd")):
            with pytest.raises(subprocess.CalledProcessError):
                bonsai_app._process_process_output(allow_stderr=True)
        with patch.object(mock_result, "check_returncode", return_value=None):
            bonsai_app._process_process_output(allow_stderr=True)


@pytest.fixture
def python_script_app() -> PythonScriptApp:
    """PythonScriptApp fixture."""
    app = PythonScriptApp(
        script="test_script.py",
        project_directory=Path("/test/project").as_posix(),
        optional_toml_dependencies=["dep1", "dep2"],
        append_python_exe=True,
        timeout=30,
    )
    return app


class TestPythonScriptApp:
    """Test PythonScriptApp."""

    @patch("subprocess.run")
    def test_create_environment(self, mock_run: MagicMock, python_script_app: PythonScriptApp) -> None:
        """Test create environment."""
        mock_run.return_value = MagicMock(returncode=0)
        result = python_script_app.create_environment()
        mock_run.assert_called_once()
        assert result.returncode == 0

    @patch("subprocess.run")
    @patch("clabe.apps._python_script.PythonScriptApp._has_venv", return_value=True)
    def test_run(self, mock_has_env: MagicMock, mock_run: MagicMock, python_script_app: PythonScriptApp) -> None:
        """Test run."""
        mock_run.return_value = MagicMock(returncode=0)
        python_script_app.run()
        mock_run.assert_called_once()
        assert python_script_app.get_result(allow_stderr=True).returncode == 0

    def test__process_process_output_failure(self, python_script_app: PythonScriptApp) -> None:
        """Test output from result failure."""
        python_script_app._completed_process = subprocess.CompletedProcess(
            args="test", returncode=1, stdout="output", stderr="error"
        )
        with pytest.raises(subprocess.CalledProcessError):
            python_script_app._process_process_output()

    def test_result_property(self, python_script_app: PythonScriptApp) -> None:
        """Test result property."""
        with pytest.raises(RuntimeError):
            _ = python_script_app.get_result()

    def test_add_uv_project_directory(self, python_script_app: PythonScriptApp) -> None:
        """Test add uv project directory."""
        assert python_script_app._add_uv_project_directory() == f"--directory {Path('/test/project').resolve()}"

    def test_add_uv_optional_toml_dependencies(self, python_script_app: PythonScriptApp) -> None:
        """Test add uv optional toml dependencies."""
        assert python_script_app._add_uv_optional_toml_dependencies() == "--extra dep1 --extra dep2"


@pytest.fixture
def open_ephys_app(mock_ui_helper) -> OpenEphysApp:
    """OpenEphysApp fixture."""
    signal_chain = Path("test_signal_chain.xml")
    executable = Path(".open_ephys/open_ephys.exe")
    settings = OpenEphysAppSettings(signal_chain=signal_chain, executable=executable)
    mock_client = MagicMock(spec=_OpenEphysGuiClient)
    app = OpenEphysApp(settings=settings, ui_helper=mock_ui_helper, client=mock_client)
    return app


class TestOpenEphysApp:
    """Test OpenEphysApp."""

    @patch("subprocess.run")
    @patch("pathlib.Path.exists", return_value=True)
    def test_run(self, mock_pathlib: MagicMock, mock_subprocess_run: MagicMock, open_ephys_app: OpenEphysApp) -> None:
        """Test run."""
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_subprocess_run.return_value = mock_result
        result = open_ephys_app.run()._completed_process
        assert result == mock_result
        mock_subprocess_run.assert_called_once()

    def test_validate(self, open_ephys_app: OpenEphysApp) -> None:
        """Test validate."""
        with patch("pathlib.Path.exists", return_value=True):
            assert open_ephys_app.validate()

    def test_validate_missing_file(self, open_ephys_app: OpenEphysApp) -> None:
        """Test validate missing file."""
        with patch("pathlib.Path.exists", side_effect=[False, True, True]):
            with pytest.raises(FileNotFoundError):
                open_ephys_app.validate()

    def test_raises_before_run(self, open_ephys_app: OpenEphysApp) -> None:
        """Test result property."""
        with pytest.raises(RuntimeError):
            open_ephys_app.get_result(allow_stderr=True)

    def test_client(self, open_ephys_app: OpenEphysApp) -> None:
        """Test client method."""
        client = open_ephys_app.client()
        assert isinstance(client, MagicMock)


class TestOpenEphysGuiClient:
    """Test _OpenEphysGuiClient."""

    @pytest.fixture
    def client(self) -> _OpenEphysGuiClient:
        """Create a client instance."""
        return _OpenEphysGuiClient(host="localhost", port=37497, timeout=5.0)

    def test_client_init(self, client: _OpenEphysGuiClient) -> None:
        """Test client initialization."""
        assert client.base_url == "http://localhost:37497/api"
        assert client._timeout == 5.0

    @patch("requests.get")
    def test_get(self, mock_get: MagicMock, client: _OpenEphysGuiClient) -> None:
        """Test generic GET request."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"mode": "IDLE"}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = client._get("/status")

        assert result == {"mode": "IDLE"}
        mock_get.assert_called_once_with("http://localhost:37497/api/status", timeout=5.0)
        mock_response.raise_for_status.assert_called_once()

    @patch("requests.put")
    def test_put(self, mock_put: MagicMock, client: _OpenEphysGuiClient) -> None:
        """Test generic PUT request with Pydantic model."""
        from clabe.apps._open_ephys import StatusRequest

        mock_response = MagicMock()
        mock_response.json.return_value = {"mode": "ACQUIRE"}
        mock_response.raise_for_status = MagicMock()
        mock_put.return_value = mock_response

        request = StatusRequest(mode=Status.ACQUIRE)
        result = client._put("/status", request)

        assert result == {"mode": "ACQUIRE"}
        mock_put.assert_called_once()
        call_args = mock_put.call_args
        assert call_args[0][0] == "http://localhost:37497/api/status"
        assert "json" in call_args[1]
        assert call_args[1]["json"] == {"mode": "ACQUIRE"}
        assert call_args[1]["timeout"] == 5.0
        mock_response.raise_for_status.assert_called_once()

    @patch("requests.get")
    def test_get_request_exception(self, mock_get: MagicMock, client: _OpenEphysGuiClient) -> None:
        """Test GET request with request exception."""
        mock_get.side_effect = requests.RequestException("Connection error")

        with pytest.raises(requests.RequestException):
            client._get("/status")

    @patch("requests.put")
    def test_put_request_exception(self, mock_put: MagicMock, client: _OpenEphysGuiClient) -> None:
        """Test PUT request with request exception."""
        from clabe.apps._open_ephys import StatusRequest

        mock_put.side_effect = requests.RequestException("Connection error")

        with pytest.raises(requests.RequestException):
            request = StatusRequest(mode=Status.IDLE)
            client._put("/status", request)
