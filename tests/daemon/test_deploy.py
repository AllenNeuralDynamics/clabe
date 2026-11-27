"""``clabe daemon deploy``: one environment per clabe, installed next to the running one, then switched to."""

import json
import shutil
import sys
import textwrap
from pathlib import Path

import pytest

from clabe.daemon import _deploy, _install
from clabe.daemon._cli import _DeployCli


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """The daemon's working directory and settings file in tmp_path; no user variables read or removed."""
    monkeypatch.setattr(_install, "default_working_directory", lambda: tmp_path / "clabe")
    monkeypatch.setattr(_deploy, "_env_file", lambda: tmp_path / "clabe" / "daemon.env")
    monkeypatch.setattr(_deploy, "_user_variable", lambda name: None)
    monkeypatch.setattr(_install.time, "sleep", lambda s: None)
    return tmp_path


def _environment(version: str, extras=_deploy.DEFAULT_EXTRAS, activated: float | None = None) -> _deploy.Environment:
    """A complete environment (its marker and an empty clabe executable) under the versions directory."""
    path = _deploy.versions_directory() / _deploy.environment_key(version, extras)
    clabe = _deploy.Environment(path, version, f"aind-clabe=={version}", extras).clabe
    clabe.parent.mkdir(parents=True, exist_ok=True)
    clabe.write_text("")
    marker = {"version": version, "spec": f"aind-clabe=={version}", "extras": list(extras), "activated": activated}
    (path / _deploy.MARKER).write_text(json.dumps(marker))
    return _deploy.read_environment(path)


class TestRequests:
    def test_a_release(self):
        request = _deploy.request_version("v0.18.0")
        assert request.spec == "aind-clabe[frontend,otel]==0.18.0" and request.key == "0.18.0-frontend-otel"

    def test_extras_are_normalized(self):
        assert _deploy._extras(" OTEL,frontend,otel ") == ("frontend", "otel")
        assert _deploy.request_version("0.18.0", ()).spec == "aind-clabe==0.18.0"

    def test_a_lock_s_release(self, tmp_path):
        (tmp_path / "uv.lock").write_text(
            'version = 1\n[[package]]\nname = "aind-clabe"\nversion = "0.18.0"\n'
            'source = { registry = "https://pypi.org/simple" }\n'
        )
        assert _deploy.request_lock(tmp_path).spec == "aind-clabe[frontend,otel]==0.18.0"

    @pytest.mark.parametrize(
        "content, message",
        [
            (
                (
                    '[[package]]\nname = "aind_clabe"\nversion = "0.19.0"\n'
                    'source = { git = "https://github.com/AllenNeuralDynamics/clabe?branch=main#c7cc84fb" }\n'
                ),
                "git source .* not a release",
            ),
            ('[[package]]\nname = "aind-clabe"\nversion = "0.1"\nsource = { editable = "." }\n', "not a release"),
            ('[[package]]\nname = "other"\nversion = "1"\n', "not in"),
        ],
    )
    def test_a_lock_without_a_usable_clabe(self, tmp_path, content, message):
        (tmp_path / "uv.lock").write_text(content)
        with pytest.raises(OSError, match=message):
            _deploy.request_lock(tmp_path)

    def test_one_source_only(self):
        with pytest.raises(SystemExit, match="one of"):
            _DeployCli(version="0.18.0", package="aind-clabe").request()


class TestHubs:
    def test_saved_in_the_settings_file_keeping_its_other_lines(self, tmp_path):
        env = tmp_path / "clabe" / "daemon.env"
        env.parent.mkdir(parents=True)
        env.write_text('DAEMON_CLABE_TOKEN="x"\nDAEMON_CLABE_HUBS="http://old/"\n')
        changed, _ = _deploy.set_hubs(["http://a/clabe/", "http://b/"])
        assert changed
        assert env.read_text() == 'DAEMON_CLABE_TOKEN="x"\nDAEMON_CLABE_HUBS="http://a/clabe/;http://b/"\n'
        assert _deploy.set_hubs(["http://a/clabe/", "http://b/"])[0] is False  # the same again: no restart

    def test_none_clears_them_and_the_settings_read_it(self, tmp_path, monkeypatch):
        from clabe.daemon._settings import DaemonSettings

        _deploy.set_hubs([])
        monkeypatch.delenv("DAEMON_CLABE_HUBS", raising=False)
        settings = DaemonSettings(_env_file=tmp_path / "clabe" / "daemon.env")
        assert settings.hubs == []

    def test_a_user_variable_is_removed_so_the_file_applies(self, monkeypatch):
        removed = []
        monkeypatch.setattr(_deploy, "_user_variable", lambda name: "http://old/")
        monkeypatch.setattr(_deploy, "_remove_user_variable", removed.append)
        _deploy.set_hubs(["http://a/"])
        changed, notes = _deploy.set_hubs(["http://a/"])
        assert changed and removed == ["DAEMON_CLABE_HUBS", "DAEMON_CLABE_HUBS"] and "user variable" in notes[-1]


class TestDeploy:
    """The decision, with the install, Task Scheduler and the daemon stubbed out."""

    @pytest.fixture
    def rig(self, monkeypatch):
        """A rig whose daemon runs ``state['runs']`` (``None``: no task) and answers when ``state['answers']``."""
        state = {"runs": None, "answers": False, "status": "idle", "start_fails": False, "calls": []}

        def install_environment(request, python=None):
            existing = _deploy.read_environment(_deploy.versions_directory() / request.key)
            if existing:
                return existing, False
            state["calls"].append(("install", request.spec))
            return _environment(request.spec.split("==")[1]), True

        def register(command, working_directory=None):
            state["calls"].append(("register", command[0]))
            state["runs"] = Path(command[0])

        def stop(port, token, force=False, **kw):
            state["calls"].append(("stop", force))
            state["answers"] = False
            return "stopped"

        def start(port, token, timeout=60.0):
            state["calls"].append(("start", str(state["runs"])))
            if state["start_fails"] and "0.18.0" in str(state["runs"]):
                raise OSError("nothing answers")
            state["answers"] = True
            return "answers"

        monkeypatch.setattr(_deploy, "install_environment", install_environment)
        monkeypatch.setattr(_install, "register", register)
        monkeypatch.setattr(_install, "stop", stop)
        monkeypatch.setattr(_install, "start", start)
        monkeypatch.setattr(
            _install,
            "task_state",
            lambda: None if state["runs"] is None else ("Running" if state["answers"] else "Ready"),
        )
        monkeypatch.setattr(_install, "registered_executable", lambda: state["runs"])
        monkeypatch.setattr(
            _install,
            "probe",
            lambda port, token, timeout=2.0: (
                {"status": state["status"], "leased_by": "rig-1"} if state["answers"] else None
            ),
        )
        return state

    def _deploy(self, version="0.18.0", **kwargs):
        return _deploy.deploy(_deploy.request_version(version), port=8070, token=None, **kwargs)

    def test_a_new_rig_installs_registers_and_starts(self, rig):
        message = self._deploy()
        assert [c[0] for c in rig["calls"]] == ["install", "register", "start"]
        assert rig["runs"].parent.parent.name == "0.18.0-frontend-otel" and "Installed clabe 0.18.0" in message

    def test_the_same_clabe_running_is_left_alone(self, rig):
        self._deploy()
        rig["calls"].clear()
        assert "nothing to restart" in self._deploy()
        assert rig["calls"] == []

    def test_a_stopped_daemon_with_the_same_clabe_is_started(self, rig):
        self._deploy()
        rig["answers"] = False
        rig["calls"].clear()
        self._deploy()
        assert [c[0] for c in rig["calls"]] == ["register", "start"]

    def test_another_version_installs_first_then_switches(self, rig):
        self._deploy("0.17.0")
        rig["calls"].clear()
        self._deploy("0.18.0")
        assert [c[0] for c in rig["calls"]] == ["install", "stop", "register", "start"]

    def test_going_back_reuses_the_old_environment(self, rig):
        self._deploy("0.17.0")
        self._deploy("0.18.0")
        rig["calls"].clear()
        self._deploy("0.17.0")
        assert [c[0] for c in rig["calls"]] == ["stop", "register", "start"]

    def test_new_hubs_restart_the_same_clabe(self, rig):
        self._deploy()
        rig["calls"].clear()
        assert "Hubs: http://hub/" in self._deploy(hubs=["http://hub/"])
        assert [c[0] for c in rig["calls"]] == ["stop", "register", "start"]

    @pytest.mark.parametrize("status", ["busy", "leased"])
    def test_a_session_is_never_interrupted_unless_forced(self, rig, status):
        self._deploy("0.17.0")
        rig.update(status=status, calls=[])
        with pytest.raises(OSError, match="Nothing was changed"):
            self._deploy("0.18.0", hubs=["http://hub/"])
        assert rig["calls"] == [] and not (_deploy._env_file()).exists()
        self._deploy("0.18.0", force=True)
        assert ("stop", True) in rig["calls"]

    def test_a_clabe_that_does_not_start_puts_the_previous_one_back(self, rig):
        self._deploy("0.17.0")
        old = rig["runs"]
        rig.update(start_fails=True, calls=[])
        with pytest.raises(OSError, match="previous clabe again"):
            self._deploy("0.18.0")
        assert rig["runs"] == old and rig["calls"][-2:] == [("register", str(old)), ("start", str(old))]

    def test_a_daemon_without_a_task_is_refused_before_anything_changes(self, rig):
        rig["answers"] = True
        with pytest.raises(OSError, match="Nothing was changed"):
            self._deploy(hubs=["http://hub/"])
        assert rig["calls"] == [] and not _deploy._env_file().exists()

    def test_a_daemon_from_the_old_installer_is_migrated(self, rig, tmp_path):
        rig.update(runs=tmp_path / "bin" / "clabe.exe", answers=True)
        message = self._deploy()
        assert [c[0] for c in rig["calls"]] == ["install", "stop", "register", "start"]
        assert "no longer used by the daemon" in message

    def test_the_task_runs_the_windowless_daemon_when_the_clabe_has_one(self, rig, monkeypatch):
        registered = []
        monkeypatch.setattr(_install, "register", lambda command, working_directory=None: registered.append(command))
        environment = _environment("0.18.0")
        windowless = environment.clabe.with_name("clabe-daemon" + environment.clabe.suffix)
        windowless.write_text("")
        assert environment.daemon == windowless
        self._deploy()
        assert registered == [[str(windowless), "run"]]

    def test_a_task_running_the_console_clabe_moves_to_the_windowless_daemon(self, rig):
        environment = _environment("0.18.0")
        rig.update(runs=environment.clabe, answers=True)  # deployed before clabe-daemon existed
        environment.clabe.with_name("clabe-daemon" + environment.clabe.suffix).write_text("")
        self._deploy()
        assert [c[0] for c in rig["calls"]] == ["stop", "register", "start"]
        assert rig["runs"].stem == "clabe-daemon"

    def test_an_older_clabe_without_it_runs_its_console_clabe(self):
        environment = _environment("0.17.0")
        assert environment.daemon == environment.clabe
        assert _install.daemon_command_for(environment.daemon)[1:] == ["daemon", "run"]

    def test_only_the_last_environments_are_kept(self, rig):
        for version in ("0.15.0", "0.16.0", "0.17.0", "0.18.0"):
            self._deploy(version)
        kept = sorted(e.version for e in _deploy.environments())
        assert kept == ["0.16.0", "0.17.0", "0.18.0"]

    def test_dry_run_changes_nothing(self, rig):
        message = self._deploy(hubs=["http://hub/"], dry_run=True)
        assert "Would install aind-clabe[frontend,otel]==0.18.0" in message and "Would set: Hubs" in message
        assert rig["calls"] == [] and not _deploy._env_file().exists()


class TestStopIfInstalled:
    def test_nothing_to_stop_on_a_new_rig(self, monkeypatch):
        monkeypatch.setattr(_install, "task_state", lambda: None)
        monkeypatch.setattr(_install, "probe", lambda port, token, timeout=2.0: None)
        assert "no daemon to stop" in _install.stop(8070, None, if_installed=True)

    def test_something_answering_without_a_task_is_still_an_error(self, monkeypatch):
        monkeypatch.setattr(_install, "task_state", lambda: None)
        monkeypatch.setattr(_install, "probe", lambda port, token, timeout=2.0: {"status": "idle"})
        with pytest.raises(OSError, match="answers on port"):
            _install.stop(8070, None, if_installed=True)


def _fake_clabe(root: Path, *, with_daemon: bool = True, with_ui: bool = True) -> Path:
    """A local ``aind-clabe`` distribution with a ``clabe`` script, and unless not a ``clabe.daemon._app`` and a
    built web UI (``clabe/server/static/index.html``)."""
    package = root / "src" / "clabe"
    (package / "daemon").mkdir(parents=True)
    (package / "server" / "static").mkdir(parents=True)
    (package / "server" / "__init__.py").write_text("")
    (package / "server" / "_app.py").write_text(
        "from pathlib import Path\nSTATIC_DIR = Path(__file__).parent / 'static'\n"
    )
    if with_ui:
        (package / "server" / "static" / "index.html").write_text("<html></html>")
    (package / "__init__.py").write_text("def main():\n    print('fake clabe')\n")
    (package / "daemon" / "__init__.py").write_text("")
    if with_daemon:
        (package / "daemon" / "_app.py").write_text("")
    (root / "pyproject.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "aind-clabe"
            version = "9.9.9"
            [project.scripts]
            clabe = "clabe:main"
            [project.gui-scripts]
            clabe-daemon = "clabe:main"
            [build-system]
            requires = ["uv_build>=0.8"]
            build-backend = "uv_build"
            [tool.uv.build-backend]
            module-name = "clabe"
            """
        )
    )
    return root


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")
class TestInstallEnvironment:
    """A real install, of a local fake aind-clabe."""

    def test_installs_into_its_folder_and_reuses_it(self, tmp_path):
        source = _fake_clabe(tmp_path / "fake")
        request = _deploy.request_package(f"aind-clabe @ {source.as_uri()}", ())
        environment, new = _deploy.install_environment(request)
        assert new and environment.version == "9.9.9" and environment.clabe.is_file()
        assert environment.daemon.stem == "clabe-daemon" and environment.daemon.is_file()  # uv made it
        assert (
            environment.path.name.startswith("9.9.9-url-") and environment.path.parent == _deploy.versions_directory()
        )
        assert not list(_deploy.versions_directory().glob(".staging-*"))
        again, new = _deploy.install_environment(request)
        assert not new and again.path == environment.path
        pinned = _deploy.Request(request.spec, (), environment.path.name)
        assert _deploy.install_environment(pinned) == (again, False)

    def test_a_clabe_that_cannot_run_the_daemon_leaves_nothing(self, tmp_path):
        source = _fake_clabe(tmp_path / "fake", with_daemon=False)
        with pytest.raises(OSError, match="cannot run the daemon"):
            _deploy.install_environment(_deploy.request_package(f"aind-clabe @ {source.as_uri()}", ()))
        assert list(_deploy.versions_directory().iterdir()) == []

    def test_a_clabe_built_from_source_without_its_web_ui_is_refused(self, tmp_path):
        source = _fake_clabe(tmp_path / "fake", with_ui=False)
        with pytest.raises(OSError, match="no web UI"):
            _deploy.install_environment(_deploy.request_package(f"aind-clabe @ {source.as_uri()}", ()))
        assert list(_deploy.versions_directory().iterdir()) == []


@pytest.mark.skipif(sys.platform != "win32", reason="the task is a Windows scheduled task")
def test_the_task_s_program_reads_as_none_without_a_task(monkeypatch):
    monkeypatch.setattr(_install, "TASK_NAME", "clabe-daemon-test-that-does-not-exist")
    assert _install.registered_executable() is None
