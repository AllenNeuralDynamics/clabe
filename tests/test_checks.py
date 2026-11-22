"""clabe.checks, steps.preflight, and the deprecated resource_monitor built on them."""

import warnings
from collections import namedtuple
from pathlib import Path

import pytest
from pydantic import BaseModel

from clabe import checks, steps
from clabe.workflow import GoBack, Workflow, check_workflow

_Usage = namedtuple("_Usage", "total used free")


class _Rig(BaseModel):
    """A rig with a data directory."""

    name: str
    data_directory: Path


def _rig(path: Path) -> _Rig:
    return _Rig(name="r", data_directory=path)


class TestFreeSpace:
    def test_measures_the_nearest_existing_parent_of_the_rig_s_data_directory(self, tmp_path, monkeypatch):
        seen = []
        monkeypatch.setattr("clabe.checks.shutil.disk_usage", lambda p: seen.append(p) or _Usage(100, 90, 10e9))
        check = checks.free_space(20e9)
        with pytest.raises(RuntimeError, match=r"Only 10\.0 GB free on .*; 20\.0 GB needed"):
            check(rig=_rig(tmp_path / "not-yet" / "created"))
        assert seen == [tmp_path]
        checks.free_space(5e9)(rig=_rig(tmp_path))

    def test_a_fixed_directory_takes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clabe.checks.shutil.disk_usage", lambda p: _Usage(100, 0, 1e12))
        check = checks.free_space(1e9, directory=tmp_path)
        assert checks.inputs(check) == []
        check()

    def test_a_directory_from_the_rig(self, tmp_path, monkeypatch):
        seen = []
        monkeypatch.setattr("clabe.checks.shutil.disk_usage", lambda p: seen.append(p) or _Usage(1, 0, 1e12))
        checks.free_space(1, directory=lambda rig: rig / "sub")(rig=tmp_path)
        assert seen == [tmp_path]  # sub does not exist: its parent is measured

    def test_nothing_exists(self, monkeypatch):
        monkeypatch.setattr("clabe.checks.os.path.exists", lambda p: False)
        with pytest.raises(FileNotFoundError, match="nor any of its parents"):
            checks.free_space(1, directory="Z:/nowhere")()


class TestExistsAndThat:
    def test_exists(self, tmp_path):
        checks.exists(tmp_path)()
        with pytest.raises(FileNotFoundError, match="does not exist or cannot be reached"):
            checks.exists(tmp_path / "missing")()
        checks.exists(lambda rig: rig.data_directory)(rig=_rig(tmp_path))

    def test_that_takes_the_predicate_s_parameters(self):
        check = checks.that(lambda rig, session: rig == session, "They differ.")
        assert checks.inputs(check) == ["rig", "session"]
        assert check.__name__ == "that"
        check(rig=1, session=1)
        with pytest.raises(RuntimeError, match="They differ"):
            check(rig=1, session=2)

    def test_that_is_named_after_a_named_predicate(self):
        def has_cameras(rig):
            return rig

        assert checks.that(has_cameras, "x").__name__ == "has_cameras"
        assert checks.that(has_cameras, "x", name="cameras").__name__ == "cameras"


class TestRun:
    def test_every_check_runs_and_every_failure_is_reported(self):
        ran = []

        def first(rig):
            ran.append("first")
            raise RuntimeError("no rig")

        def second():
            ran.append("second")

        def third(session):
            ran.append("third")
            raise ValueError("no session")

        with pytest.raises(checks.ChecksFailed) as caught:
            checks.run(first, second, third, rig=1, session=2)
        assert ran == ["first", "second", "third"]
        assert [name for name, _ in caught.value.failures] == ["first", "third"]
        assert str(caught.value) == "2 checks failed:\n- no rig\n- no session"

    def test_one_failure_is_its_own_message(self):
        with pytest.raises(checks.ChecksFailed, match="^no rig$"):
            checks.run(checks.that(lambda: False, "no rig"))

    def test_a_missing_input_is_named(self):
        with pytest.raises(TypeError, match="need rig"):
            checks.run(checks.free_space(1))

    def test_parameters_with_defaults_keep_them(self):
        seen = []

        def check(rig, minimum=3):
            seen.append(minimum)

        assert checks.inputs(check) == ["rig"]
        checks.run(check, rig=1, minimum=99)
        assert seen == [3]

    def test_going_back_is_not_a_failure(self):
        def check():
            raise GoBack("session")

        with pytest.raises(GoBack):
            checks.run(check, checks.that(lambda: False, "x"))

    def test_star_args_are_refused(self):
        def check(*args):
            pass

        with pytest.raises(TypeError, match="name each input"):
            checks.inputs(check)


class TestPreflight:
    def test_it_takes_what_its_checks_take(self):
        step = steps.preflight(checks.free_space(1), checks.that(lambda session, rig: True, "x"), lambda: None)
        wf = Workflow("demo")
        wf.step(lambda: "s", name="session")
        wf.step(lambda: _rig(Path(".")), name="rig")
        wf.step(step)
        assert wf.get("preflight").params == ("rig", "session")
        assert wf.get("preflight").title == "Preflight checks"

    def test_it_fails_once_listing_every_failure(self):
        step = steps.preflight(checks.that(lambda: False, "one"), checks.that(lambda: False, "two"))
        with pytest.raises(checks.ChecksFailed, match="2 checks failed"):
            step()

    def test_name_and_title(self):
        wf = Workflow("demo")
        wf.step(steps.preflight(lambda: None, name="rig_ready", title="Is the rig ready?"))
        assert wf.get("rig_ready").title == "Is the rig ready?"

    def test_it_needs_a_check(self):
        with pytest.raises(ValueError):
            steps.preflight()

    def test_a_workflow_with_it_passes_the_static_check(self):
        wf = Workflow("demo")

        @wf.step
        def rig() -> _Rig:
            return _rig(Path("D:/data"))

        wf.step(steps.preflight(checks.free_space(1e9), checks.exists(lambda rig: rig.data_directory)))
        assert [i for i in check_workflow(wf, strict=True) if i.severity == "error"] == []


class TestDeprecatedResourceMonitor:
    def test_it_warns_and_still_works_through_the_checks(self, tmp_path):
        from clabe.resource_monitor import ResourceMonitor, remote_dir_exists_constraint_factory

        with pytest.warns(DeprecationWarning, match="clabe.checks"):
            monitor = ResourceMonitor([remote_dir_exists_constraint_factory(tmp_path)])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            assert monitor.run() is True
            missing = remote_dir_exists_constraint_factory(tmp_path / "missing")
        assert not missing()
        assert missing.on_fail().endswith("does not exist or cannot be reached.")
