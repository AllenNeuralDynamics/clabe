from pathlib import Path

import pytest

from clabe.apps import CurriculumApp, CurriculumSettings, CurriculumSuggestion, PythonScriptApp

from .. import TESTS_ASSETS, SubmoduleManager

SubmoduleManager.initialize_submodules()


@pytest.fixture
def curriculum_app() -> CurriculumApp:
    """Fixture to create a CurriculumApp for the curriculum tests."""
    submodule_path = TESTS_ASSETS / "Aind.Behavior.curriculumTemplate" / "src" / "aind_behavior_curriculum_template"

    return CurriculumApp(
        settings=CurriculumSettings(
            module_path=Path(submodule_path),
            input_trainer_state=Path("MockPath"),
            data_directory="Demo",
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
        curriculum_app.result.check_returncode()
        parsed_output = CurriculumSuggestion.model_validate_json(curriculum_app.output_from_result().result.stdout)
        with open(TESTS_ASSETS / "expected_curriculum_suggestion.json", "r", encoding="utf-8") as f:
            expected = f.read()
            assert parsed_output == CurriculumSuggestion.model_validate_json(expected)
