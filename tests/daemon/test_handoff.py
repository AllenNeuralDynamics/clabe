"""``clabe run`` at the rig hands its run to the daemon, asking first about a run already going."""

import datetime
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from clabe.daemon import _handoff
from clabe.daemon._host import HostInfo, RepositoryInfo, RunInfo
from clabe.daemon._local import DaemonRefused


def _cli(**fields):
    defaults = {
        "here": False,
        "frontend": "auto",
        "experiment_path": Path("main.py"),
        "experiment_name": None,
        "debug_mode": False,
        "verbose": False,
        "quiet": False,
        "allow_dirty": False,
        "skip_hardware_validation": False,
        "clabe_yml": None,
    }
    return SimpleNamespace(**(defaults | fields))


def _run(**fields) -> RunInfo:
    defaults = {
        "repository": "Aind.Experiment.Vr",
        "file": "main.py",
        "experiment": "vr",
        "status": "running",
        "started_at": datetime.datetime(2026, 11, 27, 15, 42, tzinfo=datetime.UTC),
        "pid": 7,
    }
    return RunInfo(**(defaults | fields))


class TestWhenItApplies:
    @staticmethod
    def _in_a_terminal(monkeypatch):
        """On Windows, in a terminal (patched in the test itself: pytest restores sys.stdout after fixtures)."""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delenv("CLABE_SERVER_ANNOUNCE", raising=False)
        monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
        monkeypatch.setattr(sys, "stdout", SimpleNamespace(isatty=lambda: True))

    def test_a_terminal_ui_run_in_a_terminal(self, monkeypatch):
        self._in_a_terminal(monkeypatch)
        assert _handoff.applies(_cli())
        assert _handoff.applies(_cli(frontend="tui"))

    def test_not_when_told_to_run_here_or_without_the_terminal_ui(self, monkeypatch):
        self._in_a_terminal(monkeypatch)
        assert not _handoff.applies(_cli(here=True))
        assert not _handoff.applies(_cli(frontend="console"))

    def test_not_for_the_run_the_daemon_itself_started(self, monkeypatch):
        self._in_a_terminal(monkeypatch)
        monkeypatch.setenv("CLABE_SERVER_ANNOUNCE", "x.json")
        assert not _handoff.applies(_cli())

    def test_not_off_windows_or_outside_a_terminal(self, monkeypatch):
        self._in_a_terminal(monkeypatch)
        monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: False))
        assert not _handoff.applies(_cli())
        self._in_a_terminal(monkeypatch)
        monkeypatch.setattr(sys, "platform", "linux")
        assert not _handoff.applies(_cli())


def test_the_arguments_its_run_needs_too(tmp_path):
    yml = tmp_path / "clabe.yml"
    forwarded = _handoff.arguments(_cli(debug_mode=True, skip_hardware_validation=True, clabe_yml=yml))
    assert forwarded == ["--debug-mode", "--skip-hardware-validation", f"--clabe-yml={yml.resolve()}"]
    assert _handoff.arguments(_cli()) == []


def test_the_repository_holding_the_experiment(tmp_path):
    root = tmp_path / "git"
    (root / "Vr" / "experiments").mkdir(parents=True)
    repositories = [("Other", str(tmp_path / "elsewhere")), ("Vr", str(root / "Vr"))]
    assert _handoff.locate(repositories, root / "Vr" / "main.py") == ("Vr", "main.py")
    assert _handoff.locate(repositories, root / "Vr" / "experiments" / "a.py") == ("Vr", "experiments/a.py")
    assert _handoff.locate(repositories, tmp_path / "loose.py") is None


def test_the_question_says_whether_the_run_was_left_at_the_rig_or_started_remotely():
    assert _handoff.describe(_run(from_rig=True)).startswith("A previous run was not closed: Aind.Experiment.Vr · vr")
    assert _handoff.describe(_run()).startswith("A run started remotely is in progress")


def test_the_answer_is_one_key(capsys):
    keys = iter("xA")
    assert _handoff.ask(_run(), read_key=lambda: next(keys)) == "abort"
    assert "[R] Resume" in capsys.readouterr().out
    assert _handoff.ask(_run(), read_key=lambda: "\x1b") == "quit"


class _Daemon:
    """A daemon that records what it is asked."""

    def __init__(self, tmp_path, current=None, launch_refusals=()):
        self.root = tmp_path / "Vr"
        self.root.mkdir(exist_ok=True)
        self._current = current
        self.refusals = list(launch_refusals)
        self.calls = []

    def info(self):
        return HostInfo(hostname="rig", clabe_version="0", terminals=True, status="idle", run=None)

    def repositories(self):
        return [RepositoryInfo(name="Vr", path=str(self.root), exists=True)]

    def current(self):
        return self._current

    def stop(self, *, wait):
        self.calls.append(("stop", wait))
        self._current = None

    def launch(self, body):
        if self.refusals:
            status, current = self.refusals.pop(0)
            self._current = current
            raise DaemonRefused(status, "refused")
        self.calls.append(("launch", body))
        return _run(from_rig=True, terminal=True)


@pytest.fixture
def viewed(monkeypatch):
    shown = []
    monkeypatch.setattr("clabe.daemon._viewer.view", lambda daemon: shown.append(daemon) or 0)
    return shown


def test_an_idle_rig_launches_the_run_and_shows_it_here(tmp_path, viewed):
    daemon = _Daemon(tmp_path)
    cli = _cli(experiment_path=daemon.root / "main.py", experiment_name="vr", debug_mode=True)
    assert _handoff._hand_off(daemon, cli) == 0
    ((_, body),) = daemon.calls
    assert body == {
        "repository": "Vr",
        "file": "main.py",
        "experiment": "vr",
        "frontend": "tui",
        "window": False,
        "from_rig": True,
        "arguments": ["--debug-mode"],
    }
    assert viewed == [daemon]


def test_aborting_the_going_run_stops_it_first(tmp_path, viewed, monkeypatch):
    daemon = _Daemon(tmp_path, current=_run(from_rig=True, terminal=True))
    monkeypatch.setattr(_handoff, "ask", lambda run: "abort")
    assert _handoff._hand_off(daemon, _cli(experiment_path=daemon.root / "main.py")) == 0
    assert [c[0] for c in daemon.calls] == ["stop", "launch"] and daemon.calls[0] == ("stop", True)


def test_resuming_shows_the_going_run(tmp_path, viewed, monkeypatch):
    daemon = _Daemon(tmp_path, current=_run(terminal=True))
    monkeypatch.setattr(_handoff, "ask", lambda run: "resume")
    assert _handoff._hand_off(daemon, _cli(experiment_path=daemon.root / "main.py")) == 0
    assert daemon.calls == [] and viewed == [daemon]


def test_resuming_a_run_without_the_terminal_ui_opens_its_page(tmp_path, viewed, monkeypatch):
    daemon = _Daemon(tmp_path, current=_run(terminal=False))
    monkeypatch.setattr(_handoff, "ask", lambda run: "resume")
    opened = []
    monkeypatch.setattr("clabe._process.open_in_browser", opened.append)
    assert _handoff._hand_off(daemon, _cli(experiment_path=daemon.root / "main.py")) == 0
    assert viewed == [] and opened and "/run/" in opened[0]


def test_quitting_leaves_the_going_run_alone(tmp_path, viewed, monkeypatch):
    daemon = _Daemon(tmp_path, current=_run())
    monkeypatch.setattr(_handoff, "ask", lambda run: "quit")
    assert _handoff._hand_off(daemon, _cli(experiment_path=daemon.root / "main.py")) == 0
    assert daemon.calls == [] and viewed == []


def test_a_run_started_meanwhile_is_asked_about(tmp_path, viewed, monkeypatch):
    daemon = _Daemon(tmp_path, launch_refusals=[(409, _run())])
    asked = []
    monkeypatch.setattr(_handoff, "ask", lambda run: asked.append(run) or "abort")
    assert _handoff._hand_off(daemon, _cli(experiment_path=daemon.root / "main.py")) == 0
    assert len(asked) == 1 and [c[0] for c in daemon.calls] == ["stop", "launch"]


def test_a_rig_reserved_remotely_runs_here(tmp_path, viewed):
    daemon = _Daemon(tmp_path, launch_refusals=[(423, None)])
    assert _handoff._hand_off(daemon, _cli(experiment_path=daemon.root / "main.py")) is None
    assert viewed == []


def test_an_experiment_outside_the_daemon_s_repositories_runs_here(tmp_path, viewed):
    daemon = _Daemon(tmp_path)
    assert _handoff._hand_off(daemon, _cli(experiment_path=tmp_path / "loose" / "main.py")) is None


def test_without_a_daemon_the_run_happens_here(monkeypatch):
    monkeypatch.setattr(_handoff, "applies", lambda cli: True)
    monkeypatch.setattr(_handoff, "reach", lambda start: None)
    assert _handoff.run_through_daemon(_cli()) is None


def test_clabe_run_exits_with_the_daemon_s_run_or_runs_here(monkeypatch):
    from clabe import cli

    monkeypatch.setattr("clabe.daemon._handoff.run_through_daemon", lambda args: 4)
    with pytest.raises(SystemExit) as exited:
        cli._RunCli.model_construct(single_session=False).cli_cmd()
    assert exited.value.code == 4

    ran = []
    monkeypatch.setattr("clabe.daemon._handoff.run_through_daemon", lambda args: None)
    monkeypatch.setattr("clabe._process.contain_children", lambda: ran.append("contained"))
    monkeypatch.setattr(cli._RunCli, "_run", lambda self: ran.append("ran"))
    cli._RunCli.model_construct(single_session=False).cli_cmd()
    assert ran == ["contained", "ran"]
