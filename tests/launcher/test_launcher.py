import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

from clabe.launcher import Launcher
from clabe.launcher._cli import LauncherCliArgs


def test_base_launcher_with_attached_logger(
    mock_base_launcher, mock_rig, mock_session, mock_task_logic, mock_ui_helper
):
    """Test launcher initialization with attached logger."""
    with patch("clabe.logging_helper.add_file_handler") as mock_add_file_handler:
        mock_attached_logger = MagicMock()
        launcher = Launcher(
            ui_helper=mock_ui_helper,
            settings=mock_base_launcher.settings,
            attached_logger=mock_attached_logger,
        )
        assert launcher.logger == mock_add_file_handler.return_value
        mock_add_file_handler.assert_called()


def test_base_launcher_debug_mode(mock_rig, mock_session, mock_task_logic, mock_ui_helper, tmp_path: Path):
    """Test launcher initialization with debug mode enabled."""
    launcher_args_debug = LauncherCliArgs(
        debug_mode=True,
    )
    with patch("clabe.launcher._base.GitRepository") as mock_git, patch("os.chdir"), patch("pathlib.Path.mkdir"):
        mock_git.return_value.working_dir = tmp_path / "repo"
        with patch("clabe.logging_helper.add_file_handler") as mock_add_file_handler:
            mock_logger = MagicMock()
            mock_add_file_handler.return_value = mock_logger
            Launcher(
                ui_helper=mock_ui_helper,
                settings=launcher_args_debug,
            )
            mock_logger.setLevel.assert_called_with(logging.DEBUG)


def test_base_launcher_create_directories(mock_rig, mock_session, mock_task_logic, mock_ui_helper, tmp_path: Path):
    """Test launcher initialization with create_directories option."""
    launcher_args_create_dirs = LauncherCliArgs()
    with (
        patch("clabe.launcher._base.GitRepository") as mock_git,
        patch("os.chdir"),
        patch("pathlib.Path.mkdir"),
        patch("clabe.logging_helper.add_file_handler") as log_mod,
    ):
        log_mod.return_value = MagicMock()
        mock_git.return_value.working_dir = launcher_args_create_dirs.repository_directory
        with patch("clabe.launcher.Launcher._ensure_directory_structure") as mock_create_dirs:
            Launcher(
                ui_helper=mock_ui_helper,
                settings=launcher_args_create_dirs,
                attached_logger=log_mod.return_value,
            ).register_session(mock_session, data_directory=tmp_path / "data")
            assert mock_create_dirs.call_count == 2


def test_create_directory():
    with patch("os.makedirs") as mock_makedirs, patch("os.path.exists", return_value=False):
        directory = Path("/tmp/fake/directory")
        Launcher.create_directory(directory)
        mock_makedirs.assert_called_once_with(directory)


def test_ensure_directory_structure(mock_rig, mock_session, mock_task_logic, mock_ui_helper, tmp_path: Path):
    """Test that _ensure_directory_structure calls create_directory for data_dir and temp_dir."""
    launcher_args = LauncherCliArgs()

    with (
        patch("clabe.launcher._base.GitRepository") as mock_git,
        patch("os.chdir"),
        patch("pathlib.Path.mkdir"),
        patch("clabe.logging_helper.add_file_handler") as log_mod,
        patch("os.path.exists", return_value=False),
    ):
        mock_git.return_value.working_dir = tmp_path / "repo"
        log_mod.return_value = MagicMock()
        with patch("clabe.launcher.Launcher.create_directory") as mock_create_directory:
            launcher = Launcher(
                ui_helper=mock_ui_helper,
                settings=launcher_args,
                attached_logger=log_mod.return_value,
            ).register_session(mock_session, data_directory=tmp_path / "data")
            mock_create_directory.assert_any_call(launcher.session_directory)
            mock_create_directory.assert_any_call(launcher.temp_dir)
