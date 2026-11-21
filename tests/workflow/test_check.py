from typing import NewType

import pytest
from pydantic import BaseModel

from clabe.workflow import Workflow, as_workflow, check_path, check_workflow, experiment
from tests import REPO_ROOT, TESTS_ASSETS


class Session(BaseModel):
    subject: str = "x"


class VrSession(Session):
    pass


class Rig(BaseModel):
    name: str = "r"


def test_compatible_wiring_passes():
    wf = Workflow("ok")

    @wf.step
    def session() -> VrSession: ...

    @wf.step
    def rig(session: Session) -> Rig: ...

    @wf.step
    def count() -> int: ...

    @wf.step
    def ratio(count: float, rig: Rig | None, launcher) -> list[int]: ...

    @wf.step
    def total(ratio: list) -> None: ...

    assert check_workflow(wf) == []


def test_mismatched_types_are_errors():
    wf = Workflow("bad")

    @wf.step
    def session() -> Session: ...

    @wf.step
    def rig(session: Rig) -> None: ...

    [issue] = check_workflow(wf)
    assert (issue.severity, issue.workflow, issue.step) == ("error", "bad", "rig")
    assert "expects Rig" in issue.message and "produces Session" in issue.message


def qc() -> str: ...


def test_optional_and_warn_steps_may_produce_none():
    wf = Workflow("qc")
    wf.step(qc, optional="Generate QC?")

    @wf.step
    def upload(qc: str) -> None: ...

    [issue] = check_workflow(wf)
    assert "may be None" in issue.message

    fixed = Workflow("qc")
    fixed.step(qc, optional="Generate QC?")

    @fixed.step
    def upload_any(qc: str | None) -> None: ...

    assert check_workflow(fixed) == []

    warn = Workflow("qc")
    warn.step(qc, on_error="warn")

    @warn.step
    def upload_warn(qc: str) -> None: ...

    [issue] = check_workflow(warn)
    assert "may be None" in issue.message


def test_unannotated_wiring_is_reported_only_in_strict_mode():
    wf = Workflow("loose")
    wf.step(lambda: 1, name="a")
    wf.step(lambda a: a, name="b")

    assert check_workflow(wf) == []
    [issue] = check_workflow(wf, strict=True)
    assert (issue.severity, issue.step) == ("warning", "b")
    assert "annotate this parameter and the return of step 'a'" in issue.message


def test_unresolvable_annotations_are_errors():
    def a() -> "Missing": ...  # noqa: F821

    wf = Workflow("typo")
    wf.step(a)
    [issue] = check_workflow(wf)
    assert issue.severity == "error" and "return annotation cannot be resolved" in issue.message


def test_unresolvable_parameter_annotation_names_the_parameter():
    def b(a: "Missing") -> None: ...  # noqa: F821

    wf = Workflow("typo")
    wf.step(lambda: 1, name="a")
    wf.step(b)
    [issue] = check_workflow(wf)
    assert (issue.severity, issue.step) == ("error", "b")
    assert "annotation of 'a' cannot be resolved (NameError" in issue.message


@pytest.mark.parametrize(
    ("future", "annotation"), [(True, "Launcher"), (False, '"Launcher"')], ids=["future", "string"]
)
def test_builtins_annotated_with_type_checking_only_imports_pass(tmp_path, future, annotation):
    source = tmp_path / f"_clabe_check_type_checking_{future}.py"
    source.write_text(
        ("from __future__ import annotations\n" if future else "")
        + "from typing import TYPE_CHECKING\nfrom clabe.workflow import Workflow\n"
        + "if TYPE_CHECKING:\n    from clabe.launcher import Launcher\n"
        + "wf = Workflow('vr')\n"
        + f"@wf.step\nasync def session(launcher: {annotation}) -> int: ...\n"
        + "@wf.step\ndef rig(session: int) -> None: ...\n"
    )
    assert check_path(source, strict=True) == []


def test_decorated_steps_resolve_annotations_in_their_own_module(tmp_path):
    (tmp_path / "_clabe_check_deco.py").write_text(
        "import functools\n"
        "def deco(func):\n"
        "    @functools.wraps(func)\n"
        "    def wrapper(*args, **kwargs):\n"
        "        return func(*args, **kwargs)\n"
        "    return wrapper\n"
    )
    source = tmp_path / "_clabe_check_decorated.py"
    source.write_text(
        "from __future__ import annotations\n"
        "from pydantic import BaseModel\nfrom clabe.workflow import Workflow\nfrom _clabe_check_deco import deco\n"
        "class Session(BaseModel):\n    subject: str = 'x'\n"
        "wf = Workflow('decorated')\n"
        "@wf.step\n@deco\ndef session() -> Session: ...\n"
        "@wf.step\n@deco\ndef rig(session: Session) -> None: ...\n"
    )
    assert check_path(source, strict=True) == []


def test_strict_mode_does_not_warn_about_an_unresolvable_return_twice():
    def a() -> "Missing": ...  # noqa: F821

    def b(a: int) -> None: ...

    wf = Workflow("typo")
    wf.step(a)
    wf.step(b)
    [issue] = check_workflow(wf, strict=True)
    assert (issue.severity, issue.step) == ("error", "a")


UserId = NewType("UserId", int)


def test_new_types_compare_through_their_supertype():
    ok = Workflow("ok")

    @ok.step
    def user() -> UserId: ...

    @ok.step
    def total(user: int) -> None: ...

    assert check_workflow(ok) == []

    bad = Workflow("bad")

    @bad.step
    def user_id() -> UserId: ...

    @bad.step
    def name(user_id: str) -> None: ...

    [issue] = check_workflow(bad)
    assert "expects str" in issue.message


def test_experiment_functions_pass_even_in_strict_mode():
    @experiment()
    async def legacy(launcher: "Launcher") -> None: ...  # noqa: F821 -- unresolvable on purpose

    assert check_workflow(as_workflow(legacy)) == []
    assert check_workflow(as_workflow(legacy), strict=True) == []


def test_check_path_reports_definition_and_import_errors(tmp_path):
    wiring = tmp_path / "_clabe_check_wiring.py"
    wiring.write_text("from clabe.workflow import Workflow\nwf = Workflow('w')\n@wf.step\ndef a(missing): ...\n")
    [issue] = check_path(wiring)
    assert issue.severity == "error" and "missing" in issue.message and issue.source == str(wiring)

    broken = tmp_path / "_clabe_check_broken.py"
    broken.write_text("import does_not_exist_anywhere\n")
    [issue] = check_path(broken)
    assert "cannot import" in issue.message


def test_check_path_on_a_directory_skips_helpers_and_requires_a_workflow(tmp_path):
    (tmp_path / "_clabe_check_helpers.py").write_text("X = 1\n")
    (tmp_path / "_clabe_check_exp.py").write_text(
        "from clabe.workflow import experiment\n@experiment()\ndef e(launcher): ...\n"
    )
    assert check_path(tmp_path) == []

    empty = tmp_path / "empty"
    empty.mkdir()
    [issue] = check_path(empty)
    assert "no workflows" in issue.message


def test_check_path_reports_a_missing_path(tmp_path):
    missing = tmp_path / "nope.py"
    [issue] = check_path(str(missing))
    assert (issue.severity, issue.message, issue.source) == ("error", "path does not exist.", str(missing))


def test_check_path_reports_duplicate_workflow_names(tmp_path):
    dup = tmp_path / "_clabe_check_dup.py"
    dup.write_text(
        "from clabe.workflow import experiment\n"
        "@experiment(name='x')\ndef a(launcher): ...\n"
        "@experiment(name='x')\ndef b(launcher): ...\n"
    )
    [issue] = check_path(dup)
    assert "duplicate workflow name 'x'" in issue.message


def test_repository_examples_and_assets_pass_strictly():
    assert check_path(REPO_ROOT / "examples", strict=True) == []
    for asset in sorted(TESTS_ASSETS.glob("experiment_*.py")):
        assert check_path(asset, strict=True) == [], asset
