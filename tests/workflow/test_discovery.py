from types import ModuleType
from unittest.mock import Mock

import pytest

from clabe.workflow import Run, Workflow, as_workflow, collect_workflows, experiment, load_module, select_workflow
from tests import TESTS_ASSETS


def test_experiment_attaches_a_one_step_workflow():
    @experiment(name="legacy", exit_prompt_timeout=30, order=2)
    async def legacy(launcher):
        return launcher

    wf = as_workflow(legacy)

    assert (wf.name, wf.order, wf.exit_prompt_timeout) == ("legacy", 2, 30)
    assert [s.name for s in wf.steps] == ["experiment"]
    assert wf.get("experiment").title == "legacy"
    sentinel = object()
    run = Run(wf, launcher=sentinel)
    run.start()
    assert run.wait(5)
    assert run.output("experiment") is sentinel


def test_as_workflow_accepts_workflows_and_plain_callables():
    wf = Workflow("w")
    assert as_workflow(wf) is wf

    def plain(launcher): ...

    assert as_workflow(plain).name == "plain"


def test_experiment_rejects_a_negative_exit_prompt_timeout():
    with pytest.raises(ValueError, match="exit_prompt_timeout"):
        experiment(exit_prompt_timeout=-1)


def test_collect_workflows_keeps_declaration_order_within_the_same_order():
    declared = Workflow("declared")

    @experiment(name="legacy")
    def legacy(launcher): ...

    @experiment(name="first", order=-1)
    def first(launcher): ...

    module = ModuleType("fake_module")
    module.legacy, module.declared, module.alias, module.first = legacy, declared, declared, first

    assert [w.name for w in collect_workflows(module)] == ["first", "legacy", "declared"]


def test_collect_workflows_finds_imported_experiments():
    module = load_module(TESTS_ASSETS / "experiment_import_mocks.py")
    assert {"first_experiment", "second_experiment"} <= {w.name for w in collect_workflows(module)}


def test_select_workflow_returns_the_only_one_without_prompting():
    frontend = Mock()
    assert select_workflow(TESTS_ASSETS / "experiment_mock.py", frontend=frontend).name == "simple_experiment"
    frontend.prompt_pick.assert_not_called()


def test_select_workflow_prompts_when_there_are_several():
    frontend = Mock()
    frontend.prompt_pick.return_value = "first_experiment"
    selected = select_workflow(TESTS_ASSETS / "experiment_import_mocks.py", frontend=frontend)
    assert selected.name == "first_experiment"
    selected.get("experiment").func(Mock())


def test_select_workflow_by_name():
    frontend = Mock()
    path = TESTS_ASSETS / "experiment_import_mocks.py"
    assert select_workflow(path, frontend=frontend, name="second_experiment").name == "second_experiment"
    frontend.prompt_pick.assert_not_called()
    with pytest.raises(SystemExit):
        select_workflow(path, frontend=frontend, name="does_not_exist")
