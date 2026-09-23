import contextlib
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

from clabe.launcher import Launcher
from clabe.launcher._cli import LauncherCliArgs


def test_base_launcher_with_attached_logger(mock_base_launcher, mock_frontend):
    """Test launcher initialization with attached logger."""
    with patch("clabe.logging.add_file_handler") as mock_add_file_handler:
        mock_attached_logger = MagicMock()
        launcher = Launcher(
            frontend=mock_frontend,
            settings=mock_base_launcher.settings,
            attached_logger=mock_attached_logger,
        )
        assert launcher.logger == mock_add_file_handler.return_value
        mock_add_file_handler.assert_called()


def test_base_launcher_debug_mode(mock_frontend, tmp_path: Path):
    """Test launcher initialization with debug mode enabled."""
    launcher_args_debug = LauncherCliArgs(
        debug_mode=True,
    )
    with patch("clabe.launcher._base.GitRepository") as mock_git, patch("os.chdir"), patch("pathlib.Path.mkdir"):
        mock_git.return_value.working_dir = tmp_path / "repo"
        with patch("clabe.logging.add_file_handler") as mock_add_file_handler:
            mock_logger = MagicMock()
            mock_add_file_handler.return_value = mock_logger
            Launcher(
                frontend=mock_frontend,
                settings=launcher_args_debug,
            )
            mock_logger.setLevel.assert_called_with(logging.DEBUG)


def test_base_launcher_create_directories(mock_session, mock_frontend, tmp_path: Path):
    """Test launcher initialization with create_directories option."""
    launcher_args_create_dirs = LauncherCliArgs()
    with (
        patch("clabe.launcher._base.GitRepository") as mock_git,
        patch("os.chdir"),
        patch("pathlib.Path.mkdir"),
        patch("clabe.logging.add_file_handler") as log_mod,
    ):
        log_mod.return_value = MagicMock()
        mock_git.return_value.working_dir = launcher_args_create_dirs.repository_directory
        mock_git.return_value.get_metadata.return_value.model_dump_json.return_value = "{}"
        with (
            patch("clabe.launcher.Launcher._ensure_directory_structure") as mock_create_dirs,
            patch("clabe.launcher.Launcher._save_repository_state"),
        ):
            Launcher(
                frontend=mock_frontend,
                settings=launcher_args_create_dirs,
                attached_logger=log_mod.return_value,
            ).register_session(mock_session, data_directory=tmp_path / "data")
            assert mock_create_dirs.call_count == 2


def test_create_directory():
    with patch("os.makedirs") as mock_makedirs, patch("os.path.exists", return_value=False):
        directory = Path("/tmp/fake/directory")
        Launcher.create_directory(directory)
        mock_makedirs.assert_called_once_with(directory)


def test_ensure_directory_structure(mock_session, mock_frontend, tmp_path: Path):
    """Test that _ensure_directory_structure calls create_directory for data_dir and temp_dir."""
    launcher_args = LauncherCliArgs()

    with (
        patch("clabe.launcher._base.GitRepository") as mock_git,
        patch("os.chdir"),
        patch("pathlib.Path.mkdir"),
        patch("clabe.logging.add_file_handler") as log_mod,
        patch("os.path.exists", return_value=False),
    ):
        mock_git.return_value.working_dir = tmp_path / "repo"
        mock_git.return_value.get_metadata.return_value.model_dump_json.return_value = "{}"
        log_mod.return_value = MagicMock()
        with (
            patch("clabe.launcher.Launcher.create_directory") as mock_create_directory,
            patch("clabe.launcher.Launcher._save_repository_state"),
        ):
            launcher = Launcher(
                frontend=mock_frontend,
                settings=launcher_args,
                attached_logger=log_mod.return_value,
            ).register_session(mock_session, data_directory=tmp_path / "data")
            mock_create_directory.assert_any_call(launcher.session_directory)
            mock_create_directory.assert_any_call(launcher.temp_dir)


def test_register_session_saves_repository_state(mock_base_launcher, mock_session, tmp_path: Path):
    """Registering a session writes the repository snapshot into its data directory."""
    expected_json = '{"name":"test-repository"}'
    mock_base_launcher.repository.get_metadata.return_value.model_dump_json.return_value = expected_json

    mock_base_launcher.register_session(mock_session, data_directory=tmp_path / "data")

    state_file = mock_base_launcher.session_directory / "repository-state.json"
    assert state_file.read_text(encoding="utf-8") == expected_json
    mock_base_launcher.repository.get_metadata.assert_called_once_with()


def test_copy_tmp_directory_appends_launcher_log(mock_base_launcher, tmp_path: Path):
    """launcher.log is appended (not overwritten) on a second copy; other files are overwritten."""
    src_dir = mock_base_launcher.temp_dir
    src_dir.mkdir(parents=True, exist_ok=True)

    # Seed the temp dir with two files
    (src_dir / "launcher.log").write_text("second run\n", encoding="utf-8")
    (src_dir / "other.txt").write_text("new content\n", encoding="utf-8")

    dst_launcher = tmp_path / ".launcher"
    dst_launcher.mkdir()

    # Pre-populate the destination as if a previous copy already happened
    (dst_launcher / "launcher.log").write_text("first run\n", encoding="utf-8")
    (dst_launcher / "other.txt").write_text("old content\n", encoding="utf-8")

    mock_base_launcher._copy_tmp_directory(tmp_path)

    log_content = (dst_launcher / "launcher.log").read_text(encoding="utf-8")
    assert "first run" in log_content, "original log content should be preserved"
    assert "second run" in log_content, "new log content should be appended"
    assert log_content.index("first run") < log_content.index("second run"), "first run should appear before second run"

    other_content = (dst_launcher / "other.txt").read_text(encoding="utf-8")
    assert other_content == "new content\n", "non-log files should be overwritten"


def _record_run_order(launcher, experiment):
    """Run an experiment with the run span and exit stubbed, returning the call order."""
    order = []

    @contextlib.contextmanager
    def fake_run_span(_launcher, experiment_name=None):
        order.append("span opened")
        try:
            yield MagicMock()
        finally:
            order.append("span closed")

    with (
        patch("clabe.launcher._base.run_span", fake_run_span),
        patch.object(Launcher, "validate", return_value=True),
        patch.object(Launcher, "copy_logs", lambda self, *a, **k: order.append("copy_logs")),
        patch.object(Launcher, "_exit", lambda self, code=0, _force=False: order.append(f"_exit({code})")),
    ):
        launcher.run_experiment(experiment)

    return order


def test_exit_prompt_runs_after_the_run_span_closes(mock_base_launcher):
    """The exit prompt blocks on the user, so it must not run while the root span is open.

    While it did, closing the console window at "Press Enter to exit..." meant the root span
    was never ended or exported and the finished run looked as though it never closed.
    """
    order = _record_run_order(mock_base_launcher, lambda launcher: None)

    assert order == ["span opened", "copy_logs", "span closed", "_exit(0)"]


def test_failed_log_copy_still_exits_with_an_error_code(mock_base_launcher):
    """The ValueError branch used to call _exit(-1) itself; it must still propagate the code."""
    order = []

    @contextlib.contextmanager
    def fake_run_span(_launcher, experiment_name=None):
        order.append("span opened")
        try:
            yield MagicMock()
        finally:
            order.append("span closed")

    def boom(self, *a, **k):
        raise ValueError("no session directory")

    with (
        patch("clabe.launcher._base.run_span", fake_run_span),
        patch.object(Launcher, "validate", return_value=True),
        patch.object(Launcher, "copy_logs", boom),
        patch.object(Launcher, "_exit", lambda self, code=0, _force=False: order.append(f"_exit({code})")),
    ):
        mock_base_launcher.run_experiment(lambda launcher: None)

    assert order == ["span opened", "span closed", "_exit(-1)"]
