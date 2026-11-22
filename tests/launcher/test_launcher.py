import asyncio
import contextlib
import json
import logging
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from clabe import ui
from clabe.launcher import Launcher
from clabe.launcher._cli import LauncherCliArgs
from clabe.ui import ConfirmRequest, PromptCancelledError
from clabe.workflow import Abort, Workflow
from tests.conftest import MockFrontend


def test_base_launcher_with_attached_logger(mock_base_launcher, mock_frontend):
    """Test launcher initialization with attached logger."""
    with patch("clabe.logging.add_file_handler") as mock_add_file_handler:
        mock_attached_logger = MagicMock()
        launcher = Launcher(
            client=mock_frontend,
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
                client=mock_frontend,
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
                client=mock_frontend,
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
                client=mock_frontend,
                settings=launcher_args,
                attached_logger=log_mod.return_value,
            ).register_session(mock_session, data_directory=tmp_path / "data")
            mock_create_directory.assert_any_call(launcher.session_directory)
            mock_create_directory.assert_any_call(launcher.temp_dir)


def test_a_second_session_in_an_existing_data_directory_gets_its_folder(mock_base_launcher, mock_session, tmp_path):
    """The data directory exists from earlier sessions: the new session's own folder must still be created."""
    (tmp_path / "data" / "an-earlier-session").mkdir(parents=True)
    mock_base_launcher.repository.get_metadata.return_value.model_dump_json.return_value = "{}"
    mock_base_launcher.register_session(mock_session, data_directory=tmp_path / "data")
    assert mock_base_launcher.session_directory.is_dir()


def test_register_session_saves_repository_state(mock_base_launcher, mock_session, tmp_path: Path):
    """Registering a session writes the repository snapshot with launcher output."""
    expected_json = '{"name":"test-repository"}'
    mock_base_launcher.repository.get_metadata.return_value.model_dump_json.return_value = expected_json

    mock_base_launcher.register_session(mock_session, data_directory=tmp_path / "data")

    state_file = mock_base_launcher.temp_dir / "repository-state.json"
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
        patch.object(
            Launcher, "_exit", lambda self, code=0, _force=False, timeout=None: order.append(f"_exit({code})")
        ),
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
        patch.object(
            Launcher, "_exit", lambda self, code=0, _force=False, timeout=None: order.append(f"_exit({code})")
        ),
    ):
        mock_base_launcher.run_experiment(lambda launcher: None)

    assert order == ["span opened", "span closed", "_exit(-1)"]


def _record_exit_call(launcher, experiment):
    """Run ``experiment`` and return the kwargs ``_exit`` was called with."""
    calls = []
    with (
        patch.object(Launcher, "validate", return_value=True),
        patch.object(Launcher, "copy_logs", lambda self, *a, **k: None),
        patch.object(
            Launcher,
            "_exit",
            lambda self, code=0, _force=False, timeout=None: calls.append(
                {"code": code, "_force": _force, "timeout": timeout}
            ),
        ),
    ):
        launcher.run_experiment(experiment)
    assert len(calls) == 1
    return calls[0]


def test_exit_prompt_shown_by_default(mock_base_launcher):
    call = _record_exit_call(mock_base_launcher, lambda launcher: None)
    assert call == {"code": 0, "_force": False, "timeout": None}


@pytest.mark.parametrize("timeout", [0, 60.0, None])
def test_the_workflow_exit_prompt_timeout_is_forwarded(mock_base_launcher, timeout):
    wf = Workflow("w", exit_prompt_timeout=timeout)
    wf.step(lambda launcher: None, name="experiment")

    assert _record_exit_call(mock_base_launcher, wf)["timeout"] == timeout


def test_a_workflow_rejects_a_negative_exit_prompt_timeout():
    with pytest.raises(ValueError, match="exit_prompt_timeout"):
        Workflow("w", exit_prompt_timeout=-1)


def test_exit_prompt_timeout_is_not_a_launcher_setting():
    assert "exit_prompt_timeout" not in LauncherCliArgs.model_fields


class _CancellableFrontend:
    """A client whose prompt blocks until ``cancel`` is called.

    ``ignore_cancels`` drops the first few cancellations, standing in for ones that
    land before the prompt is on screen.
    """

    def __init__(self, ignore_cancels: int = 0):
        self._cancelled = threading.Event()
        self._ignore = ignore_cancels
        self.cancel_calls = 0
        self.labels: list[str] = []

    def ask(self, request):
        self.labels.append(request.label)
        if not self._cancelled.wait(5):
            raise AssertionError("prompt was never cancelled")
        raise PromptCancelledError()

    def notify(self, message, level=None):
        pass

    def header(self, text):
        pass

    def close(self):
        pass

    def cancel(self):
        self.cancel_calls += 1
        if self.cancel_calls > self._ignore:
            self._cancelled.set()


def test_exit_timeout_cancels_prompt(mock_base_launcher):
    frontend = _CancellableFrontend()
    mock_base_launcher.client = frontend
    started = time.monotonic()
    mock_base_launcher._exit(0, timeout=0.2)
    assert 0.15 <= time.monotonic() - started < 2  # Windows waits can return a timer tick early
    assert frontend.labels == ["Press Enter to exit (closing automatically in 0.2 s)..."]


def test_exit_timeout_retries_until_prompt_is_cancelled(mock_base_launcher):
    frontend = _CancellableFrontend(ignore_cancels=2)
    mock_base_launcher.client = frontend
    mock_base_launcher._exit(0, timeout=0.05)
    assert frontend.cancel_calls == 3


def test_exit_answered_before_timeout_does_not_cancel(mock_base_launcher, mock_frontend):
    mock_frontend.cancel_prompt = MagicMock()
    mock_base_launcher._exit(0, timeout=0.2)
    time.sleep(0.3)
    mock_frontend._ask_text_mock.assert_called_once()
    mock_frontend.cancel_prompt.assert_not_called()


def test_exit_force_skips_prompt(mock_base_launcher, mock_frontend):
    mock_base_launcher._exit(0, _force=True)
    mock_frontend._ask_text_mock.assert_not_called()


def test_exit_zero_timeout_skips_prompt(mock_base_launcher, mock_frontend):
    mock_base_launcher._exit(0, timeout=0)
    mock_frontend._ask_text_mock.assert_not_called()


def test_run_experiment_runs_a_workflow_whose_prompts_reach_the_run(mock_base_launcher):
    seen = []
    wf = Workflow("wf", exit_prompt_timeout=0)

    @wf.step
    def a(launcher, run):
        seen.append(ui.current_session() is run)
        return ui.ask(ConfirmRequest(label="?"))

    call = _record_exit_call(mock_base_launcher, wf)

    assert call == {"code": 0, "_force": False, "timeout": 0}
    assert seen == [True]
    assert isinstance(mock_base_launcher.client, MockFrontend)  # the client given at construction is kept
    events = (mock_base_launcher.temp_dir / "events.jsonl").read_text(encoding="utf-8")
    assert '"step":"clabe_preflight"' in events.replace(" ", "")  # validate() runs inside the run
    assert (mock_base_launcher.temp_dir / "events.jsonl").exists()


def test_an_aborted_run_exits_with_an_error_code(mock_base_launcher):
    wf = Workflow("wf")

    @wf.step
    def a():
        raise Abort("no rig")

    assert _record_exit_call(mock_base_launcher, wf)["code"] == -1


def test_a_failing_legacy_experiment_can_be_aborted(mock_base_launcher, mock_frontend):
    mock_frontend._ask_pick_mock.return_value = "abort"

    def boom(launcher):
        raise RuntimeError("x")

    assert _record_exit_call(mock_base_launcher, boom)["code"] == -1
    mock_frontend._ask_pick_mock.assert_called_once()


def test_a_legacy_sync_experiment_can_call_asyncio_run(mock_base_launcher, mock_frontend):
    mock_frontend._ask_pick_mock.return_value = "abort"  # fail fast instead of re-asking on a failure
    ran = []

    async def work():
        ran.append(True)

    def legacy(launcher):
        asyncio.run(work())

    assert _record_exit_call(mock_base_launcher, legacy)["code"] == 0
    assert ran == [True]


def test_run_experiment_writes_a_finished_manifest(mock_base_launcher):
    _record_exit_call(mock_base_launcher, lambda launcher: None)

    manifest = json.loads((mock_base_launcher.temp_dir / "run.json").read_text(encoding="utf-8"))
    assert manifest["outcome"]["status"] == "completed"
    assert manifest["outcome"]["exit_code"] == 0


def test_a_failed_log_copy_is_recorded_as_the_manifest_exit_code(mock_base_launcher):
    def boom(self, *a, **k):
        raise ValueError("no session directory")

    with (
        patch.object(Launcher, "validate", return_value=True),
        patch.object(Launcher, "copy_logs", boom),
        patch.object(Launcher, "_exit", lambda self, code=0, _force=False, timeout=None: None),
    ):
        mock_base_launcher.run_experiment(lambda launcher: None)

    manifest = json.loads((mock_base_launcher.temp_dir / "run.json").read_text(encoding="utf-8"))
    assert manifest["outcome"]["status"] == "completed"
    assert manifest["outcome"]["exit_code"] == -1


def test_a_launcher_runs_a_single_experiment(mock_base_launcher):
    _record_exit_call(mock_base_launcher, lambda launcher: None)
    with pytest.raises(RuntimeError, match="single experiment"):
        mock_base_launcher.run_experiment(lambda launcher: None)


def test_a_served_run_is_reachable_while_it_executes(mock_base_launcher, mock_frontend):
    from clabe.server import RunClient

    mock_base_launcher._settings = mock_base_launcher.settings.model_copy(
        update={"serve": True, "port": 18190, "token": "s3cret"}
    )
    seen = {}

    def announced_url() -> str:
        messages = [call.args[0] for call in mock_frontend._render_mock.call_args_list]
        return next(m.removeprefix("Run served at ") for m in messages if m.startswith("Run served at "))

    wf = Workflow("served", exit_prompt_timeout=0)

    @wf.step
    def probe():
        url = announced_url()
        assert url.endswith("/?token=s3cret")
        client = RunClient(url.split("/?")[0], token="s3cret")
        seen["step"] = client.state().current_step
        seen["url"] = url

    assert _record_exit_call(mock_base_launcher, wf)["code"] == 0
    assert seen["step"] == "probe"
    with pytest.raises(requests.ConnectionError):  # the server stops with the run
        RunClient(seen["url"].split("/?")[0], token="s3cret", timeout=1).info()
