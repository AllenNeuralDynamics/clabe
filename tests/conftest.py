import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from aind_behavior_services import Rig, Session, Task

from clabe import ui
from clabe.launcher import Launcher
from clabe.launcher._cli import LauncherCliArgs


class MockFrontend(ui.FrontendBase):
    """Non-interactive frontend for tests; primitives are mockable."""

    def __init__(self):
        super().__init__()
        self._render_mock = Mock()
        self._ask_text_mock = Mock(return_value="")
        self._ask_pick_mock = Mock(return_value="")
        self._ask_confirm_mock = Mock(return_value=True)
        self._ask_autocomplete_mock = Mock(return_value="")

    def _render(self, message, level):
        return self._render_mock(message, level)

    def _ask_text(self, request):
        return self._ask_text_mock(request)

    def _ask_pick(self, request):
        return self._ask_pick_mock(request)

    def _ask_confirm(self, request):
        return self._ask_confirm_mock(request)

    def _ask_autocomplete(self, request):
        return self._ask_autocomplete_mock(request)


@pytest.fixture
def mock_frontend():
    return MockFrontend()


@pytest.fixture
def mock_session():
    return Session(
        experiment="mock",
        subject="mock_subject",
        session_name="mock_session",
    )


@pytest.fixture
def mock_rig():
    return Rig(rig_name="mock_rig", version="0.0.0", data_directory="mock_data_dir", computer_name="mock_computer")


@pytest.fixture
def mock_task():
    return Task(version="0.0.0", task_parameters={}, name="mock_task")


@pytest.fixture
def mock_base_launcher(mock_rig, mock_session, mock_task, mock_frontend, tmp_path: Path):
    os.environ["COMPUTERNAME"] = "TEST_COMPUTER"
    launcher_args = LauncherCliArgs()
    # Ensure directories exist for os.chdir
    with (
        patch("clabe.launcher._base.GitRepository") as mock_git,
        patch("os.chdir"),
        patch("pathlib.Path.mkdir"),
        patch("clabe.logging_helper.add_file_handler"),
        patch("clabe.launcher.Launcher._ensure_directory_structure"),
        patch("clabe.launcher.Launcher.validate", return_value=True),
        patch("os.environ", {"COMPUTERNAME": "TEST_COMPUTER"}),
    ):
        mock_git.return_value.working_dir = tmp_path / "repo"
        launcher = Launcher(
            frontend=mock_frontend,
            settings=launcher_args,
        )
        launcher.temp_dir.mkdir(parents=True, exist_ok=True)

        return launcher
