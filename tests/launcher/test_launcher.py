import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clabe.launcher import BaseLauncher
from clabe.launcher._cli import BaseLauncherCliArgs


class BaseLauncherMock(BaseLauncher):
    pass


@pytest.fixture
def launcher_args():
    return BaseLauncherCliArgs(data_dir=Path("/tmp/fake/data/dir"), temp_dir=Path("/tmp/fake/temp/dir"))


@pytest.fixture
def base_launcher(mock_rig, mock_session, mock_task_logic, mock_ui_helper, launcher_args):
    with (
        patch("clabe.launcher.BaseLauncher.validate", return_value=True),
        patch("os.environ", {"COMPUTERNAME": "TEST_COMPUTER"}),
        patch("os.chdir"),
        patch("pathlib.Path.mkdir"),
        patch("clabe.logging_helper.add_file_handler"),
        patch("clabe.launcher.BaseLauncher._create_directory_structure"),
    ):
        mock_git_repo_instance = MagicMock(working_dir="/tmp/fake/repo")
        with patch("clabe.git_manager.GitRepository", return_value=mock_git_repo_instance):
            launcher = BaseLauncherMock(
                rig=mock_rig,
                session=mock_session,
                task_logic=mock_task_logic,
                picker=mock_ui_helper,
                settings=launcher_args,
            )
            return launcher


def test_base_launcher_init_basic(
    base_launcher,
    mock_rig,
    mock_session,
    mock_task_logic,
):
    """Test basic initialization of BaseLauncher."""
    assert base_launcher.get_rig() == mock_rig
    assert base_launcher.get_session() == mock_session
    assert base_launcher.get_task_logic() == mock_task_logic
    assert base_launcher.get_rig_model() is type(mock_rig)
    assert base_launcher.get_session_model() is type(mock_session)
    assert base_launcher.get_task_logic_model() is type(mock_task_logic)
    assert Path(base_launcher.settings.temp_dir).exists()


def test_base_launcher_with_attached_logger(launcher_args, mock_rig, mock_session, mock_task_logic, mock_ui_helper):
    """Test launcher initialization with attached logger."""
    with patch("clabe.logging_helper.add_file_handler") as mock_add_file_handler:
        mock_attached_logger = MagicMock()
        launcher = BaseLauncherMock(
            rig=mock_rig,
            session=mock_session,
            task_logic=mock_task_logic,
            picker=mock_ui_helper,
            settings=launcher_args,
            attached_logger=mock_attached_logger,
        )
        assert launcher.logger == mock_add_file_handler.return_value
        mock_add_file_handler.assert_called()


def test_base_launcher_debug_mode(mock_rig, mock_session, mock_task_logic, mock_ui_helper):
    """Test launcher initialization with debug mode enabled."""
    launcher_args_debug = BaseLauncherCliArgs(
        data_dir=Path("/tmp/fake/data/dir"), temp_dir=Path("/tmp/fake/temp/dir"), debug_mode=True
    )
    with patch("clabe.logging_helper.add_file_handler") as mock_add_file_handler:
        mock_logger = MagicMock()
        mock_add_file_handler.return_value = mock_logger
        BaseLauncherMock(
            rig=mock_rig,
            session=mock_session,
            task_logic=mock_task_logic,
            picker=mock_ui_helper,
            settings=launcher_args_debug,
        )
        mock_logger.setLevel.assert_called_with(logging.DEBUG)


def test_base_launcher_create_directories(mock_rig, mock_session, mock_task_logic, mock_ui_helper):
    """Test launcher initialization with create_directories option."""
    launcher_args_create_dirs = BaseLauncherCliArgs(
        data_dir=Path("/tmp/fake/data/dir"), temp_dir=Path("/tmp/fake/temp/dir"), create_directories=True
    )
    with patch("clabe.launcher.BaseLauncher._create_directory_structure") as mock_create_dirs:
        BaseLauncherMock(
            rig=mock_rig,
            session=mock_session,
            task_logic=mock_task_logic,
            picker=mock_ui_helper,
            settings=launcher_args_create_dirs,
        )
        mock_create_dirs.assert_called_once()


def test_create_directory():
    with patch("os.makedirs") as mock_makedirs, patch("os.path.exists", return_value=False):
        directory = Path("/tmp/fake/directory")
        BaseLauncher.create_directory(directory)
        mock_makedirs.assert_called_once_with(directory)


def test_create_directory_structure(mock_rig, mock_session, mock_task_logic, mock_ui_helper, launcher_args):
    """Test that _create_directory_structure calls create_directory for data_dir and temp_dir."""
    with patch("clabe.launcher.BaseLauncher.create_directory") as mock_create_directory:
        launcher_args.create_directories = True
        launcher = BaseLauncherMock(
            rig=mock_rig,
            session=mock_session,
            task_logic=mock_task_logic,
            picker=mock_ui_helper,
            settings=launcher_args,
        )
        mock_create_directory.assert_any_call(launcher.settings.data_dir)
        mock_create_directory.assert_any_call(launcher.temp_dir)
        assert mock_create_directory.call_count == 2
