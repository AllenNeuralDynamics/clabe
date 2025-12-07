from clabe.launcher import collect_clabe_experiments
from clabe.launcher._experiments import _select_experiment
from tests import TESTS_ASSETS


def test_collect_clabe_experiments_discovers_decorated_function(tmp_path):
    experiments = list(collect_clabe_experiments(__import__("tests.assets.experiment_mock", fromlist=["*"])))
    names = {e.name for e in experiments}
    assert "simple_experiment" in names


def test_select_experiment_single_choice_uses_default_ui_helper(tmp_path):
    module_path = TESTS_ASSETS / "experiment_mock.py"
    selected = _select_experiment(module_path)
    assert selected.name == "simple_experiment"
