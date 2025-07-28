import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import patch

from clabe.launcher import BaseLauncher
from clabe.launcher._cli import BaseLauncherCliArgs

from .fixtures import MockPicker, mock_rig, mock_session, mock_task_logic


class BaseLauncherMock(BaseLauncher):
    pass


class TestBaseLauncher(unittest.TestCase):
    @patch("clabe.launcher.BaseLauncher.validate", return_value=True)
    def setUp(self, mock_validate):
        self.rig_schema_model = mock_rig
        self.session_schema_model = mock_session
        self.task_logic_schema_model = mock_task_logic
        self.data_dir = Path("/tmp/fake/data/dir")
        self.config_library_dir = Path("/tmp/fake/config/dir")
        self.temp_dir = Path("/tmp/fake/temp/dir")
        self.launcher = BaseLauncherMock(
            rig=self.rig_schema_model,
            session=self.session_schema_model,
            task_logic=self.task_logic_schema_model,
            picker=MockPicker(),
            settings=BaseLauncherCliArgs(data_dir=self.data_dir, temp_dir=self.temp_dir),
        )

    def test_init(self):
        self.assertEqual(self.launcher.get_rig(), self.rig_schema_model)
        self.assertEqual(self.launcher.get_session(), self.session_schema_model)
        self.assertEqual(self.launcher.get_task_logic(), self.task_logic_schema_model)
        self.assertEqual(self.launcher.get_rig_model(), type(self.rig_schema_model))
        self.assertEqual(self.launcher.get_session_model(), type(self.session_schema_model))
        self.assertEqual(self.launcher.get_task_logic_model(), type(self.task_logic_schema_model))
        self.assertEqual(Path(self.launcher.settings.data_dir).resolve(), self.data_dir.resolve())
        self.assertTrue(Path(self.launcher.settings.temp_dir).exists())

    @patch("os.makedirs")
    @patch("os.path.exists", return_value=False)
    def test_create_directory(self, mock_path_exists, mock_makedirs):
        directory = Path("/tmp/fake/directory")
        BaseLauncher.create_directory(directory)
        mock_makedirs.assert_called_once_with(directory)

    @patch("clabe.launcher.BaseLauncher._create_directory_structure")
    @patch("os.path.exists", return_value=False)
    def test_create_directory_structure(self, mock_path_exists, mock_makedirs):
        self.launcher._create_directory_structure()
        mock_makedirs.assert_called()


if __name__ == "__main__":
    unittest.main()
