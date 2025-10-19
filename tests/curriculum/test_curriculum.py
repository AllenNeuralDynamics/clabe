from pathlib import Path

import pytest

from clabe.apps import CurriculumApp, CurriculumSettings, PythonScriptApp

from .. import TESTS_ASSETS, SubmoduleManager

SubmoduleManager.initialize_submodules()


@pytest.fixture
def curriculum_app() -> CurriculumApp:
    """Fixture to create a CurriculumApp for the curriculum tests."""

    return CurriculumApp(
        settings=CurriculumSettings(
            script="curriculum run",
            input_trainer_state=Path("MockPath"),
            data_directory="Demo",
            project_directory=TESTS_ASSETS / "Aind.Behavior.VrForaging.Curricula",
            curriculum="template",
        )
    )


@pytest.fixture
def inner_python_app(curriculum_app: CurriculumApp):
    return curriculum_app._python_script_app


class TestCurriculumIntegration:
    """Tests the integration with the aind-behavior-curriculum submodule."""

    def test_can_create_venv(self, inner_python_app: PythonScriptApp) -> None:
        """Tests that the virtual environment can be created."""
        proc = inner_python_app.create_environment()
        proc.check_returncode()

    def test_curriculum_run(self, curriculum_app: CurriculumApp) -> None:
        """Tests that the curriculum can be run."""

        curriculum_app.run()
        curriculum_app._python_script_app.get_result(allow_stderr=True).check_returncode()
