import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from aind_behavior_services import AindBehaviorRigModel, AindBehaviorSessionModel, AindBehaviorTaskLogicModel

from clabe import ui
from clabe.launcher import Launcher
from clabe.launcher._cli import LauncherCliArgs


class MockUiHelper(ui.UiHelper):
    def __init__(self):
        self._print_func = lambda x: None
        self._input_func = lambda x: "1"
        self._prompt_pick_from_list = Mock(return_value="")
        self._prompt_yes_no_question = Mock(return_value=True)
        self._prompt_text = Mock(return_value="")
        self._prompt_float = Mock(return_value=0.0)

    def print(self, message: str) -> None:
        return self._print_func(message)

    def input(self, prompt: str) -> str:
        return self._input_func(prompt)

    def prompt_pick_from_list(self, *args, **kwargs):
        return self._prompt_pick_from_list(*args, **kwargs)

    def prompt_yes_no_question(self, prompt: str) -> bool:
        return self._prompt_yes_no_question(prompt)

    def prompt_text(self, prompt: str) -> str:
        return self._prompt_text(prompt)

    def prompt_float(self, prompt):
        return self._prompt_float(prompt)


@pytest.fixture
def mock_ui_helper():
    return MockUiHelper()


@pytest.fixture
def mock_session():
    return AindBehaviorSessionModel(
        experiment="mock",
        subject="mock_subject",
        session_name="mock_session",
    )


@pytest.fixture
def mock_rig():
    return AindBehaviorRigModel(rig_name="mock_rig", version="0.0.0", data_directory="mock_data_dir")


@pytest.fixture
def mock_task_logic():
    return AindBehaviorTaskLogicModel(version="0.0.0", task_parameters={}, name="mock_task_logic")


@pytest.fixture
def mock_base_launcher(mock_rig, mock_session, mock_task_logic, mock_ui_helper, tmp_path: Path):
    os.environ["COMPUTERNAME"] = "TEST_COMPUTER"
    launcher_args = LauncherCliArgs(
        data_dir=tmp_path / "data",
    )
    # Ensure directories exist for os.chdir
    launcher_args.data_dir.mkdir(parents=True, exist_ok=True)

    with (
        patch("clabe.launcher._base.GitRepository") as mock_git,
        patch("os.chdir"),
        patch("pathlib.Path.mkdir"),
        patch("clabe.logging_helper.add_file_handler"),
        patch("clabe.launcher.Launcher._ensure_directory_structure"),
        patch("clabe.launcher.Launcher.validate", return_value=True),
        patch("os.environ", {"COMPUTERNAME": "TEST_COMPUTER"}),
    ):
        mock_git.return_value.working_dir = launcher_args.data_dir
        launcher = Launcher(
            ui_helper=mock_ui_helper,
            settings=launcher_args,
        )
        launcher.temp_dir.mkdir(parents=True, exist_ok=True)

        return launcher
