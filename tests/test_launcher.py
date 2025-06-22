import unittest
from pathlib import Path
from unittest.mock import create_autospec, patch

from aind_behavior_services import AindBehaviorRigModel, AindBehaviorSessionModel, AindBehaviorTaskLogicModel

from clabe import ui
from clabe.launcher import BaseLauncher
from clabe.launcher.cli import BaseCliArgs
from clabe.services import ServicesFactoryManager


class BaseLauncherMock(BaseLauncher):
    def _pre_run_hook(self, *args, **kwargs):
        pass

    def _run_hook(self, *args, **kwargs):
        pass

    def _post_run_hook(self, *args, **kwargs):
        pass


class TestBaseLauncher(unittest.TestCase):
    @patch("clabe.launcher.BaseLauncher.validate", return_value=True)
    def setUp(self, mock_validate):
        self.rig_schema_model = create_autospec(AindBehaviorRigModel)
        self.session_schema_model = create_autospec(AindBehaviorSessionModel)
        self.task_logic_schema_model = create_autospec(AindBehaviorTaskLogicModel)
        self.data_dir = Path("/tmp/fake/data/dir")
        self.config_library_dir = Path("/tmp/fake/config/dir")
        self.temp_dir = Path("/tmp/fake/temp/dir")
        self.launcher = BaseLauncherMock(
            rig_schema_model=self.rig_schema_model,
            session_schema_model=self.session_schema_model,
            task_logic_schema_model=self.task_logic_schema_model,
            picker=ui.DefaultPicker(),
            settings=BaseCliArgs(data_dir=self.data_dir, temp_dir=self.temp_dir),
        )

    def test_init(self):
        self.assertEqual(self.launcher.rig_schema_model, self.rig_schema_model)
        self.assertEqual(self.launcher.session_schema_model, self.session_schema_model)
        self.assertEqual(self.launcher.task_logic_schema_model, self.task_logic_schema_model)
        self.assertEqual(self.launcher.data_dir.resolve(), self.data_dir.resolve())
        self.assertTrue(self.launcher.temp_dir.exists())

    def test_rig_schema_property(self):
        with self.assertRaises(ValueError):
            _ = self.launcher.rig_schema
        self.launcher._rig_schema = self.rig_schema_model
        self.assertEqual(self.launcher.rig_schema, self.rig_schema_model)

    def test_session_schema_property(self):
        with self.assertRaises(ValueError):
            _ = self.launcher.session_schema
        self.launcher._session_schema = self.session_schema_model
        self.assertEqual(self.launcher.session_schema, self.session_schema_model)

    def test_task_logic_schema_property(self):
        with self.assertRaises(ValueError):
            _ = self.launcher.task_logic_schema
        self.launcher._task_logic_schema = self.task_logic_schema_model
        self.assertEqual(self.launcher.task_logic_schema, self.task_logic_schema_model)

    def test_services_factory_manager_property(self):
        with self.assertRaises(ValueError):
            _ = self.launcher.services_factory_manager
        services_manager = create_autospec(ServicesFactoryManager)
        self.launcher._services_factory_manager = services_manager
        self.assertEqual(self.launcher.services_factory_manager, services_manager)

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
