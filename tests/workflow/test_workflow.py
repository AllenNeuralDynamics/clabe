import functools

import pytest

from clabe.workflow import Workflow, WorkflowDefinitionError


def _demo() -> Workflow:
    wf = Workflow("demo")

    @wf.step
    def session(launcher):
        return "s"

    @wf.step(title="Rig config")
    def rig(session):
        return "r"

    @wf.step
    def confirm(session):
        return None

    @wf.step(barrier=True)
    def register(rig, confirm):
        return None

    return wf


def test_steps_keep_declaration_order_titles_and_deps():
    wf = _demo()
    assert [s.name for s in wf.steps] == ["session", "rig", "confirm", "register"]
    assert wf.get("session").deps == ()  # launcher is a built-in, not a step dependency
    assert wf.get("rig").deps == ("session",)
    assert wf.get("rig").title == "Rig config"
    assert wf.get("confirm").title == "Confirm"
    assert wf.get("register").barrier


def test_decorator_returns_the_plain_function():
    wf = Workflow("demo")

    @wf.step
    def session():
        return 42

    assert session() == 42


def test_unknown_or_forward_dependency_fails_at_definition():
    wf = Workflow("demo")
    with pytest.raises(WorkflowDefinitionError, match="later"):

        @wf.step
        def a(later): ...

    assert wf.steps == ()


def test_duplicate_and_reserved_names_fail():
    wf = Workflow("demo")

    @wf.step
    def a(): ...

    with pytest.raises(WorkflowDefinitionError, match="Duplicate"):
        wf.step(a)
    assert [s.name for s in wf.steps] == ["a"]
    with pytest.raises(WorkflowDefinitionError, match="reserved"):

        @wf.step
        def run(): ...

    assert [s.name for s in wf.steps] == ["a"]


def test_var_args_are_rejected():
    wf = Workflow("demo")
    with pytest.raises(WorkflowDefinitionError, match="args"):

        @wf.step
        def a(*args): ...


def test_positional_only_parameters_are_rejected():
    with pytest.raises(WorkflowDefinitionError, match="positional-only"):
        Workflow("demo").step(lambda a, /: None, name="x")


@pytest.mark.parametrize(
    ("func", "options"),
    [
        (lambda: None, {}),
        (lambda: None, {"name": "my-step"}),
        (lambda: None, {"name": ""}),
        (functools.partial(lambda: None), {}),
    ],
    ids=["lambda", "dashed", "empty", "partial"],
)
def test_step_names_must_be_identifiers(func, options):
    with pytest.raises(WorkflowDefinitionError, match="identifier"):
        Workflow("demo").step(func, **options)


def test_parameters_with_defaults_are_still_dependencies():
    wf = Workflow("demo")
    with pytest.raises(WorkflowDefinitionError, match="retries"):

        @wf.step
        def f(retries=3): ...


def test_barrier_and_undo_are_exclusive():
    with pytest.raises(WorkflowDefinitionError, match="barrier"):
        Workflow("demo").step(lambda: None, name="a", barrier=True, undo=lambda out: None)


def test_cancellable_only_applies_to_async_steps():
    with pytest.raises(WorkflowDefinitionError, match="async"):
        Workflow("demo").step(lambda: None, name="a", cancellable=True)


def test_undo_must_accept_the_step_output():
    with pytest.raises(WorkflowDefinitionError, match="undo"):
        Workflow("demo").step(lambda: None, name="a", undo=lambda: None)


def test_dependents_are_transitive_and_ordered():
    wf = _demo()
    assert wf.dependents("session") == ["rig", "confirm", "register"]
    assert wf.dependents("rig") == ["register"]
    assert wf.dependents("register") == []


def test_negative_exit_prompt_timeout_is_rejected():
    with pytest.raises(ValueError):
        Workflow("demo", exit_prompt_timeout=-1)


def _vr() -> Workflow:
    wf = Workflow("vr", exit_prompt_timeout=60, order=3)

    @wf.step
    def session(): ...

    @wf.step
    def rig(session): ...

    @wf.step(barrier=True, title="Quality control")
    def qc():
        return "qc"

    return wf


def fip_rig(rig):
    return "fip"


def my_qc():
    return "mine"


def test_variant_inserts_and_replaces_without_touching_the_original():
    vr = _vr()
    fip = vr.variant("vr-fip").insert_after("rig", fip_rig).replace("qc", my_qc)

    assert [s.name for s in fip.steps] == ["session", "rig", "fip_rig", "qc"]
    assert fip.get("qc").func is my_qc
    assert (fip.name, fip.exit_prompt_timeout, fip.order) == ("vr-fip", 60, 3)
    assert [s.name for s in vr.steps] == ["session", "rig", "qc"]


def test_replace_keeps_the_original_options_unless_overridden():
    fip = _vr().variant("x").replace("qc", my_qc)
    assert fip.get("qc").barrier and fip.get("qc").title == "Quality control"
    assert not _vr().variant("y").replace("qc", my_qc, barrier=False).get("qc").barrier


def test_insert_after_validates_wiring():
    with pytest.raises(WorkflowDefinitionError, match="rig"):
        _vr().variant("x").insert_after("session", fip_rig)


def test_failed_insert_or_replace_leaves_the_variant_unchanged():
    variant = _vr().variant("x")
    with pytest.raises(WorkflowDefinitionError):
        variant.insert_after("session", fip_rig)
    with pytest.raises(WorkflowDefinitionError):
        variant.insert_after("qc", my_qc, name="rig")
    with pytest.raises(WorkflowDefinitionError):
        variant.replace("qc", my_qc, undo=lambda output: None)
    with pytest.raises(KeyError):
        variant.replace("nope", my_qc)
    assert [s.name for s in variant.steps] == ["session", "rig", "qc"]
    assert variant.get("qc").func is not my_qc
