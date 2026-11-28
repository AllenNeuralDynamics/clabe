from types import ModuleType
from unittest.mock import Mock

import pytest

from clabe import ui
from clabe.workflow import Run, Workflow, as_workflow, collect_workflows, load_module, select_workflow
from tests import TESTS_ASSETS


def test_a_function_of_the_launcher_runs_as_a_one_step_workflow():
    async def legacy(launcher):
        return launcher

    wf = as_workflow(legacy)

    assert wf.name == "legacy"
    assert [s.name for s in wf.steps] == ["experiment"]
    assert wf.get("experiment").title == "legacy"
    sentinel = object()
    run = Run(wf, launcher=sentinel)
    run.start()
    assert run.wait(5)
    assert run.output("experiment") is sentinel


def test_files_of_a_package_load_with_their_relative_imports(tmp_path):
    from clabe.workflow import check_path, list_experiments

    package = tmp_path / "_clabe_pkg_experiments"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "_shared.py").write_text("TITLE = 'shared'\n")
    (package / "vr.py").write_text(
        "from clabe.workflow import Workflow\nfrom ._shared import TITLE\n"
        "wf = Workflow('vr')\n@wf.step(title=TITLE)\ndef a() -> int:\n    return 1\n"
    )
    assert check_path(package, strict=True) == []
    listing = list_experiments(package)
    assert listing.errors == [] and [e.name for e in listing.experiments] == ["vr"]
    assert load_module(package / "vr.py").__name__ == "_clabe_pkg_experiments.vr"


def test_a_workflow_s_description_is_listed_and_kept_by_its_copies(tmp_path):
    from clabe.workflow import list_experiments

    (tmp_path / "main.py").write_text(
        "from clabe.workflow import Workflow\n"
        "wf = Workflow('vr', description='Patch foraging.')\n"
        "plain = Workflow('plain', order=1)\n"
    )
    listed = {e.name: e.description for e in list_experiments(tmp_path / "main.py").experiments}
    assert listed == {"vr": "Patch foraging.", "plain": None}

    wf = Workflow("vr", description="Patch foraging.")
    assert wf.variant("vr-dev").description == "Patch foraging."
    assert wf.variant("vr-dev", description=None).description is None
    assert wf.concatenate(Workflow("handoff")).description == "Patch foraging."


def test_as_workflow_accepts_workflows_and_plain_callables():
    wf = Workflow("w")
    assert as_workflow(wf) is wf

    def plain(launcher): ...

    assert as_workflow(plain).name == "plain"


def test_collect_workflows_keeps_declaration_order_within_the_same_order():
    declared = Workflow("declared")
    legacy = Workflow("legacy")
    first = Workflow("first", order=-1)

    module = ModuleType("fake_module")
    module.legacy, module.declared, module.alias, module.first = legacy, declared, declared, first

    assert [w.name for w in collect_workflows(module)] == ["first", "legacy", "declared"]


def test_collect_workflows_finds_imported_experiments():
    module = load_module(TESTS_ASSETS / "experiment_import_mocks.py")
    assert {"first_experiment", "second_experiment"} <= {w.name for w in collect_workflows(module)}


def _session_answering(answer):
    """A session whose every question gets ``answer``."""
    session = Mock()
    session.ask.return_value = answer
    return session


def test_select_workflow_returns_the_only_one_without_prompting():
    session = _session_answering(None)
    with ui.bind(session):
        assert select_workflow(TESTS_ASSETS / "experiment_mock.py").name == "simple_experiment"
    session.ask.assert_not_called()


def test_select_workflow_prompts_when_there_are_several():
    session = _session_answering("first_experiment")
    with ui.bind(session):
        selected = select_workflow(TESTS_ASSETS / "experiment_import_mocks.py")
    assert selected.name == "first_experiment"
    asked = session.ask.call_args.args[0]
    assert asked.prompt_kind == "pick" and asked.field == "experiment"
    selected.get("experiment").func(Mock())


def test_select_workflow_by_name():
    session = _session_answering(None)
    path = TESTS_ASSETS / "experiment_import_mocks.py"
    with ui.bind(session):
        assert select_workflow(path, name="second_experiment").name == "second_experiment"
        with pytest.raises(SystemExit):
            select_workflow(path, name="does_not_exist")
    session.ask.assert_not_called()
