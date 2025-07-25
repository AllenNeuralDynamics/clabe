import unittest
from unittest.mock import MagicMock, create_autospec, patch

from clabe.behavior_launcher import (
    BehaviorCliArgs,
    BehaviorLauncher,
    BehaviorServicesFactoryManager,
    DefaultBehaviorPicker,
)
from clabe.behavior_launcher._launcher import DefaultBehaviorPickerSettings
from clabe.ui import DefaultUIHelper
from tests import suppress_stdout


class TestDefaultBehaviorPicker(unittest.TestCase):
    def setUp(self):
        self.services_factory_manager = create_autospec(BehaviorServicesFactoryManager)
        self.launcher = BehaviorLauncher(
            rig_schema_model=MagicMock(),
            task_logic_schema_model=MagicMock(),
            session_schema_model=MagicMock(),
            services=self.services_factory_manager,
            settings=BehaviorCliArgs(
                data_dir="/path/to/data",
                temp_dir="/path/to/temp",
                repository_dir=None,
                allow_dirty=False,
                skip_hardware_validation=False,
                debug_mode=False,
                group_by_subject_log=False,
                validate_init=False,
            ),
            attached_logger=None,
            picker=DefaultBehaviorPicker(
                ui_helper=DefaultUIHelper(print_func=MagicMock(), input_func=input),
                settings=DefaultBehaviorPickerSettings(config_library_dir="/path/to/config"),
            ),
        )
        self.picker = self.launcher.picker

    @patch("builtins.input", side_effect=["John Doe"])
    def test_prompt_experimenter(self, mock_input):
        self.assertIsInstance(self.picker, DefaultBehaviorPicker)
        self.picker._experimenter_validator = lambda x: x in ["John", "Doe"]
        result = self.picker.prompt_experimenter()
        self.assertEqual(result, ["John", "Doe"])

    @patch("clabe.behavior_launcher._launcher.model_from_json_file")
    @patch("glob.glob")
    def test_prompt_rig_input(self, mock_glob, mock_model_from_json_file):
        with suppress_stdout():
            mock_glob.return_value = ["/path/to/rig1.json"]
            mock_model_from_json_file.return_value = MagicMock()
            rig = self.picker.pick_rig()
            self.assertIsNotNone(rig)

    @patch("clabe.behavior_launcher._launcher.model_from_json_file")
    @patch("glob.glob")
    @patch("os.path.isfile", return_value=True)
    @patch("builtins.input", return_value="1")
    def test_prompt_task_logic_input(self, mock_input, mock_is_file, mock_glob, mock_model_from_json_file):
        with suppress_stdout():
            mock_glob.return_value = ["/path/to/task1.json"]
            mock_model_from_json_file.return_value = MagicMock()
            task_logic = self.picker.pick_task_logic()
            self.assertIsNotNone(task_logic)


class TestDefaultUiHelper(unittest.TestCase):
    def setUp(self):
        self.ui_helper = DefaultUIHelper(print_func=MagicMock())

    @patch("builtins.input", side_effect=["Some notes"])
    def test_prompt_get_text(self, mock_input):
        result = self.ui_helper.prompt_text("")
        self.assertIsInstance(result, str)

    @patch("builtins.input", side_effect=["Y"])
    def test_prompt_yes_no_question(self, mock_input):
        result = self.ui_helper.prompt_yes_no_question("Continue?")
        self.assertIsInstance(result, bool)

    @patch("builtins.input", side_effect=["1"])
    def test_prompt_pick_from_list(self, mock_input):
        result = self.ui_helper.prompt_pick_from_list(["item1", "item2"], "Choose an item")
        self.assertIsInstance(result, str)
        self.assertEqual(result, "item1")

    @patch("builtins.input", side_effect=["0"])
    def test_prompt_pick_from_list_none(self, mock_input):
        result = self.ui_helper.prompt_pick_from_list(["item1", "item2"], "Choose an item")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
