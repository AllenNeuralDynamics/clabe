from unittest.mock import Mock

from clabe.launcher import collect_clabe_experiments
from clabe.launcher._experiments import _select_experiment
from tests import TESTS_ASSETS


def test_collect_clabe_experiments_discovers_decorated_function() -> None:
    experiments = list(collect_clabe_experiments(__import__("tests.assets.experiment_mock", fromlist=["*"])))
    names = {e.name for e in experiments}
    assert "simple_experiment" in names


def test_select_experiment_single_choice_uses_default_ui_helper() -> None:
    module_path = TESTS_ASSETS / "experiment_mock.py"
    selected = _select_experiment(module_path)
    assert selected.name == "simple_experiment"


def test_select_experiment_multiple_experiments_discovered_and_logs_constant(caplog) -> None:
    mock_ui_helper = Mock()
    mock_ui_helper.prompt_pick_from_list.return_value = "first_experiment"

    module_path = TESTS_ASSETS / "experiment_import_mocks.py"
    selected = _select_experiment(module_path, ui_helper=mock_ui_helper)

    experiments = list(collect_clabe_experiments(__import__("tests.assets.experiment_import_mocks", fromlist=["*"])))
    names = {e.name for e in experiments}
    assert {"first_experiment", "second_experiment"}.issubset(names)
    assert selected.name == "first_experiment"

    launcher = Mock()
    selected.func(launcher)
