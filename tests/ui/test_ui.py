from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from clabe.launcher import Launcher, LauncherCliArgs, DefaultBehaviorPicker, DefaultBehaviorPickerSettings
from clabe.ui import DefaultUIHelper
from tests import suppress_stdout


@pytest.fixture
def launcher(mock_rig, mock_session, mock_task_logic):
    with patch("clabe.launcher._base.GitRepository") as mock_git:
        mock_git.return_value.working_dir = Path("/path/to/data")
        with patch("os.chdir"):
            return Launcher[Any, Any, Any](
                rig=mock_rig,
                task_logic=None,
                session=mock_session,
                settings=LauncherCliArgs(
                    data_dir=Path("/path/to/data"),
                    temp_dir=Path("/path/to/temp"),
                    repository_dir=None,
                    allow_dirty=False,
                    skip_hardware_validation=False,
                    debug_mode=False,
                ),
                attached_logger=None,
            )


@pytest.fixture
def picker(launcher):
    picker = DefaultBehaviorPicker(
        ui_helper=DefaultUIHelper(print_func=MagicMock(), input_func=input),
        settings=DefaultBehaviorPickerSettings(config_library_dir=Path("/path/to/config")),
    )
    picker.initialize(launcher=launcher)
    return picker


class TestDefaultBehaviorPicker:
    @patch("builtins.input", side_effect=["John Doe"])
    def test_prompt_experimenter(self, mock_input, picker):
        assert isinstance(picker, DefaultBehaviorPicker)
        picker._experimenter_validator = lambda x: x in ["John", "Doe"]
        result = picker.prompt_experimenter()
        assert result == ["John", "Doe"]

    @patch("clabe.launcher._picker.model_from_json_file")
    @patch("glob.glob")
    def test_prompt_rig_input(self, mock_glob, mock_model_from_json_file, picker, launcher):
        with suppress_stdout():
            mock_glob.return_value = ["/path/to/rig1.json"]
            mock_model_from_json_file.return_value = MagicMock()
            rig = picker.pick_rig(launcher)
            assert rig is not None

    @patch("clabe.launcher._picker.model_from_json_file")
    @patch("glob.glob")
    @patch("os.path.isfile", return_value=True)
    @patch("builtins.input", return_value="1")
    @patch("clabe.launcher._base.Launcher.set_task_logic")
    def test_prompt_task_logic_input(
        self, mock_set_task_logic, mock_input, mock_is_file, mock_glob, mock_model_from_json_file, picker, launcher
    ):
        with suppress_stdout():
            mock_glob.return_value = ["/path/to/task1.json"]
            mock_task_logic_instance = MagicMock()
            mock_model_from_json_file.return_value = mock_task_logic_instance
            task_logic = picker.pick_task_logic(launcher)
            mock_set_task_logic.assert_called_once_with(mock_task_logic_instance)
            assert task_logic is not None


@pytest.fixture
def ui_helper():
    return DefaultUIHelper(print_func=MagicMock())


class TestDefaultUiHelper:
    @patch("builtins.input", side_effect=["Some notes"])
    def test_prompt_get_text(self, mock_input, ui_helper):
        result = ui_helper.prompt_text("")
        assert isinstance(result, str)

    @patch("builtins.input", side_effect=["Y"])
    def test_prompt_yes_no_question(self, mock_input, ui_helper):
        result = ui_helper.prompt_yes_no_question("Continue?")
        assert isinstance(result, bool)

    @patch("builtins.input", side_effect=["1"])
    def test_prompt_pick_from_list(self, mock_input, ui_helper):
        result = ui_helper.prompt_pick_from_list(["item1", "item2"], "Choose an item")
        assert isinstance(result, str)
        assert result == "item1"

    @patch("builtins.input", side_effect=["0"])
    def test_prompt_pick_from_list_none(self, mock_input, ui_helper):
        result = ui_helper.prompt_pick_from_list(["item1", "item2"], "Choose an item")
        assert result is None
