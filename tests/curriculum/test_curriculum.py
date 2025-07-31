import json
from typing import Callable

import pytest
from aind_behavior_curriculum import Metrics, TrainerState
from semver import Version

from clabe.apps import PythonScriptApp

from .. import TESTS_ASSETS, SubmoduleManager

SubmoduleManager.initialize_submodules()


@pytest.fixture
def curriculum_app_factory() -> Callable[[str], PythonScriptApp]:
    """Fixture to create a PythonScriptApp for the curriculum tests."""
    submodule_path = TESTS_ASSETS / "Aind.Behavior.curriculumTemplate"

    def _make_app(script: str) -> PythonScriptApp:
        return PythonScriptApp(script, project_directory=submodule_path, timeout=20, append_python_exe=True)

    return _make_app


@pytest.fixture
def executable_script():
    return "src/aind_behavior_curriculum_template/app.py"


@pytest.fixture
def executable_script_w_demo():
    return "src/aind_behavior_curriculum_template/app.py run --data-directory demo --input-trainer-state NA"


class TestCurriculumIntegration:
    """Tests the integration with the aind-behavior-curriculum submodule."""

    def test_can_create_venv(
        self, curriculum_app_factory: Callable[[str], PythonScriptApp], executable_script: str
    ) -> None:
        """Tests that the virtual environment can be created."""
        curriculum_app = curriculum_app_factory(executable_script)
        proc = curriculum_app.create_environment()
        proc.check_returncode()

    def test_curriculum_pkg_version(
        self, curriculum_app_factory: Callable[[str], PythonScriptApp], executable_script: str
    ) -> None:
        """Tests that the curriculum package version can be retrieved."""
        curriculum_app = curriculum_app_factory(f"{executable_script} version")
        curriculum_app.run()
        output = curriculum_app.result.stdout
        Version.parse(output)
        curriculum_app.result.check_returncode()

    def test_curriculum_aind_behavior_curriculum_version(
        self, curriculum_app_factory: Callable[[str], PythonScriptApp], executable_script: str
    ) -> None:
        """Tests that the aind-behavior-curriculum package version can be retrieved."""
        curriculum_app = curriculum_app_factory(f"{executable_script} abc-version")
        curriculum_app.run()
        output = curriculum_app.result.stdout
        Version.parse(output)
        curriculum_app.result.check_returncode()

    def test_curriculum_run(
        self, curriculum_app_factory: Callable[[str], PythonScriptApp], executable_script_w_demo: str
    ) -> None:
        """Tests that the curriculum can be run."""
        curriculum_app = curriculum_app_factory(executable_script_w_demo)

        curriculum_app.run()
        curriculum_app.result.check_returncode()
        output: str = curriculum_app.result.stdout
        json_output = json.loads(output)
        trainer_state = TrainerState.model_validate_json(json.dumps(json_output["trainer_state"]))
        metrics = Metrics.model_validate_json(json.dumps(json_output["metrics"]))
        _ = Version.parse(json_output["version"])
        _ = Version.parse(json_output["abc_version"])

        with open(TESTS_ASSETS / "expected_curriculum_suggestion.json", "r", encoding="utf-8") as f:
            expected = f.read()
            assert trainer_state == TrainerState.model_validate_json(expected)

        with open(TESTS_ASSETS / "expected_curriculum_metrics.json", "r", encoding="utf-8") as f:
            expected = f.read()
            assert metrics == Metrics.model_validate_json(expected)
