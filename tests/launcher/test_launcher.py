from pathlib import Path
import pytest
from unittest.mock import MagicMock
import logging

from clabe.launcher import BaseLauncher
from clabe.launcher._cli import BaseLauncherCliArgs
from clabe.git_manager import GitRepository
from clabe.ui import DefaultUIHelper
from clabe.utils import abspath

from ..fixtures import MockPicker, mock_rig, mock_session, mock_task_logic

class BaseLauncherMock(BaseLauncher):
    pass

@pytest.fixture
def launcher_args():
    return BaseLauncherCliArgs(data_dir=Path("/tmp/fake/data/dir"), temp_dir=Path("/tmp/fake/temp/dir"))

@pytest.fixture
def base_launcher(mocker, launcher_args):
    mocker.patch("clabe.launcher.BaseLauncher.validate", return_value=True)
    mocker.patch("os.environ", {"COMPUTERNAME": "TEST_COMPUTER"})
    mock_git_repo_instance = MagicMock(working_dir="/tmp/fake/repo")
    mocker.patch("clabe.git_manager.GitRepository", return_value=mock_git_repo_instance)
    mocker.patch("os.chdir")
    mocker.patch("pathlib.Path.mkdir")
    mocker.patch("clabe.logging_helper.add_file_handler")
    mocker.patch("clabe.launcher.BaseLauncher._create_directory_structure")

    launcher = BaseLauncherMock(
        rig=mock_rig,
        session=mock_session,
        task_logic=mock_task_logic,
        picker=MockPicker(),
        settings=launcher_args,
    )
    return launcher

def test_base_launcher_init(base_launcher, launcher_args, mocker):
    assert base_launcher.get_rig() == mock_rig
    assert base_launcher.get_session() == mock_session
    assert base_launcher.get_task_logic() == mock_task_logic
    assert base_launcher.get_rig_model() == type(mock_rig)
    assert base_launcher.get_session_model() == type(mock_session)
    assert base_launcher.get_task_logic_model() == type(mock_task_logic)
    assert Path(base_launcher.settings.temp_dir).exists()

    # Test attached_logger
    mock_add_file_handler = mocker.patch("clabe.logging_helper.add_file_handler")
    mock_attached_logger = MagicMock()
    launcher_with_attached_logger = BaseLauncherMock(
        rig=mock_rig,
        session=mock_session,
        task_logic=mock_task_logic,
        picker=MockPicker(),
        settings=launcher_args,
        attached_logger=mock_attached_logger
    )
    assert launcher_with_attached_logger.logger == mock_add_file_handler.return_value
    mock_add_file_handler.assert_called_with(mock_attached_logger, mocker.ANY)

    # Test debug_mode
    launcher_args_debug = BaseLauncherCliArgs(data_dir=Path("/tmp/fake/data/dir"), temp_dir=Path("/tmp/fake/temp/dir"), debug_mode=True)
    mock_add_file_handler_debug = mocker.patch("clabe.logging_helper.add_file_handler")
    mock_logger_debug = MagicMock()
    mock_add_file_handler_debug.return_value = mock_logger_debug
    launcher_debug = BaseLauncherMock(
        rig=mock_rig,
        session=mock_session,
        task_logic=mock_task_logic,
        picker=MockPicker(),
        settings=launcher_args_debug,
    )
    mock_logger_debug.setLevel.assert_called_with(logging.DEBUG)

    # Test repository_dir
    launcher_args_repo = BaseLauncherCliArgs(data_dir=Path("/tmp/fake/data/dir"), temp_dir=Path("/tmp/fake/temp/dir"), repository_dir="/tmp/custom/repo")
    launcher_repo = BaseLauncherMock(
        rig=mock_rig,
        session=mock_session,
        task_logic=mock_task_logic,
        picker=MockPicker(),
        settings=launcher_args_repo,
    )
    mocker.patch("clabe.git_manager.GitRepository").assert_called_with(path=Path("/tmp/custom/repo"))

    # Test create_directories
    mock_create_directory_structure = mocker.patch("clabe.launcher.BaseLauncher._create_directory_structure")
    launcher_args_create_dirs = BaseLauncherCliArgs(data_dir=Path("/tmp/fake/data/dir"), temp_dir=Path("/tmp/fake/temp/dir"), create_directories=True)
    launcher_create_dirs = BaseLauncherMock(
        rig=mock_rig,
        session=mock_session,
        task_logic=mock_task_logic,
        picker=MockPicker(),
        settings=launcher_args_create_dirs,
    )
    mock_create_directory_structure.assert_called_once()

def test_create_directory(mocker):
    mock_makedirs = mocker.patch("os.makedirs")
    mocker.patch("os.path.exists", return_value=False)
    directory = Path("/tmp/fake/directory")
    BaseLauncher.create_directory(directory)
    mock_makedirs.assert_called_once_with(directory)

def test_create_directory_structure(mocker, launcher_args):
    mock_create_directory = mocker.patch("clabe.launcher.BaseLauncher.create_directory")
    mocker.patch("clabe.launcher.BaseLauncher.validate", return_value=True)
    mocker.patch("os.environ", {"COMPUTERNAME": "TEST_COMPUTER"})
    mock_git_repo_instance = MagicMock(working_dir="/tmp/fake/repo")
    mocker.patch("clabe.git_manager.GitRepository", return_value=mock_git_repo_instance)
    mocker.patch("os.chdir")
    mocker.patch("pathlib.Path.mkdir")
    mocker.patch("clabe.logging_helper.add_file_handler")

    launcher = BaseLauncherMock(
        rig=mock_rig,
        session=mock_session,
        task_logic=mock_task_logic,
        picker=MockPicker(),
        settings=launcher_args,
        create_directories=True
    )
    mock_create_directory.assert_any_call(launcher.settings.data_dir)
    mock_create_directory.assert_any_call(launcher.temp_dir)
    assert mock_create_directory.call_count == 2

