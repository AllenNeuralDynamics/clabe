import json
import unittest

from aind_behavior_curriculum import Metrics, TrainerState
from semver import Version

from clabe.apps import PythonScriptApp
from tests import TESTS_ASSETS, SubmoduleManager

SubmoduleManager.initialize_submodules()


class TestCurriculumIntegration(unittest.TestCase):
    def setUp(self):
        self.submodule_path = TESTS_ASSETS / "Aind.Behavior.curriculumTemplate"
        self.executable_script = "src/aind_behavior_curriculum_template/app.py"
        self.executable_script_w_demo = (
            "src/aind_behavior_curriculum_template/app.py run --data-directory demo --input-trainer-state NA"
        )

        def _make_app(script: str):
            return PythonScriptApp(script, project_directory=self.submodule_path, timeout=20, append_python_exe=True)

        self.make_app = _make_app

    def test_can_create_venv(self):
        curriculum_app = self.make_app(f"{self.executable_script}")
        proc = curriculum_app.create_environment()
        proc.check_returncode()

    def test_curriculum_pkg_version(self):
        curriculum_app = self.make_app(f"{self.executable_script} version")
        curriculum_app.run()
        output = curriculum_app.result.stdout
        Version.parse(output)
        curriculum_app.result.check_returncode()

    def test_curriculum_aind_behavior_curriculum_version(self):
        curriculum_app = self.make_app(f"{self.executable_script} abc-version")
        curriculum_app.run()
        output = curriculum_app.result.stdout
        Version.parse(output)
        curriculum_app.result.check_returncode()

    def test_curriculum_run(self):
        curriculum_app = self.make_app(f"{self.executable_script_w_demo}")

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
            self.assertEqual(trainer_state, TrainerState.model_validate_json(expected))

        with open(TESTS_ASSETS / "expected_curriculum_metrics.json", "r", encoding="utf-8") as f:
            expected = f.read()
            self.assertEqual(metrics, Metrics.model_validate_json(expected))


if __name__ == "__main__":
    unittest.main()
