import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clabe.apps import BonsaiApp, BonsaiAppSettings, PythonScriptApp


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

    @patch("clabe.apps._bonsai.run_bonsai_process")
    @patch("pathlib.Path.exists", return_value=True)
    def test_run(self, mock_pathlib: MagicMock, mock_run_bonsai_process: MagicMock, bonsai_app: BonsaiApp) -> None:
        """Test run."""
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_run_bonsai_process.return_value = mock_result
        result = bonsai_app.run()._completed_process
        assert result == mock_result
        mock_run_bonsai_process.assert_called_once()

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
            bonsai_app.result(allow_stderr=True)

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
        assert python_script_app.result(allow_stderr=True).returncode == 0

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
            _ = python_script_app.result()

    def test_add_uv_project_directory(self, python_script_app: PythonScriptApp) -> None:
        """Test add uv project directory."""
        assert python_script_app._add_uv_project_directory() == f" --directory {Path('/test/project').resolve()}"

    def test_add_uv_optional_toml_dependencies(self, python_script_app: PythonScriptApp) -> None:
        """Test add uv optional toml dependencies."""
        assert python_script_app._add_uv_optional_toml_dependencies() == "--extra dep1 --extra dep2"
