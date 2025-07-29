import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from clabe.apps import BonsaiApp, PythonScriptApp

from ..fixtures import MockUiHelper


class TestBonsaiApp(unittest.TestCase):
    def setUp(self):
        self.workflow = Path("test_workflow.bonsai")
        self.executable = Path("bonsai/bonsai.exe")
        self.app = BonsaiApp(workflow=self.workflow, executable=self.executable, ui_helper=MockUiHelper())

    @patch("clabe.apps._bonsai.run_bonsai_process")
    @patch("pathlib.Path.exists", return_value=True)
    def test_run(self, mock_pathlib, mock_run_bonsai_process):
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_run_bonsai_process.return_value = mock_result
        result = self.app.run()
        self.assertEqual(result, mock_result)
        mock_run_bonsai_process.assert_called_once()

    def test_validate(self):
        with patch("pathlib.Path.exists", return_value=True):
            self.assertTrue(self.app.validate())

    def test_validate_missing_file(self):
        with patch("pathlib.Path.exists", side_effect=[False, True, True]):
            with self.assertRaises(FileNotFoundError):
                self.app.validate()

    def test_result_property(self):
        with self.assertRaises(RuntimeError):
            _ = self.app.result
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        self.app._result = mock_result
        self.assertEqual(self.app.result, mock_result)

    @patch.object(MockUiHelper, "prompt_yes_no_question", return_value=True)
    def test_output_from_result(self, mock_prompt):
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.stdout = "output"
        mock_result.stderr = ""
        self.app._result = mock_result
        with patch.object(mock_result, "check_returncode", side_effect=subprocess.CalledProcessError(1, "cmd")):
            with self.assertRaises(subprocess.CalledProcessError):
                self.app.output_from_result(allow_stderr=True)
        with patch.object(mock_result, "check_returncode", return_value=None):
            self.assertEqual(self.app.output_from_result(allow_stderr=True), self.app)

    @patch.object(MockUiHelper, "prompt_pick_from_list", return_value="picked_layout.bonsai.layout")
    def test_prompt_visualizer_layout_input(self, mock_ui_helper):
        with patch("glob.glob", return_value=["layout1.bonsai.layout", "layout2.bonsai.layout"]):
            layout = self.app.prompt_visualizer_layout_input()
            self.assertEqual(str(layout), "picked_layout.bonsai.layout")


class TestPythonScriptApp(unittest.TestCase):
    def setUp(self):
        self.app = PythonScriptApp(
            script="test_script.py",
            project_directory=Path("/test/project").as_posix(),
            optional_toml_dependencies=["dep1", "dep2"],
            append_python_exe=True,
            timeout=30,
        )

    @patch("subprocess.run")
    def test_create_environment(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        result = self.app.create_environment()
        mock_run.assert_called_once()
        self.assertEqual(result.returncode, 0)

    @patch("subprocess.run")
    @patch("clabe.apps._python_script.PythonScriptApp._has_venv", return_value=True)
    def test_run(self, mock_has_env, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        result = self.app.run()
        mock_run.assert_called_once()
        self.assertEqual(result.returncode, 0)

    def test_output_from_result_success(self):
        self.app._result = subprocess.CompletedProcess(args="test", returncode=0, stdout="output", stderr="")
        result = self.app.output_from_result()
        self.assertEqual(result, self.app)

    def test_output_from_result_failure(self):
        self.app._result = subprocess.CompletedProcess(args="test", returncode=1, stdout="output", stderr="error")
        with self.assertRaises(subprocess.CalledProcessError):
            self.app.output_from_result()

    def test_result_property(self):
        with self.assertRaises(RuntimeError):
            _ = self.app.result

    def test_add_uv_project_directory(self):
        self.assertEqual(self.app._add_uv_project_directory(), " --directory /test/project")

    def test_add_uv_optional_toml_dependencies(self):
        self.assertEqual(self.app._add_uv_optional_toml_dependencies(), "--extra dep1 --extra dep2")


if __name__ == "__main__":
    unittest.main()
