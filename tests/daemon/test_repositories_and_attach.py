"""Repositories found under roots, runs started from a terminal followed by the daemon, and desktop shortcuts."""

import subprocess
import sys
from pathlib import Path

import pytest

from clabe.daemon import AttachBody, DaemonSettings, Host, HostError
from clabe.daemon._announce import announce
from clabe.daemon._host import pid_alive
from clabe.daemon._settings import is_experiment_repository
from clabe.server._server import ANNOUNCE_ENV, free_port


def _repo(root: Path, name: str, *, depends: str = "aind-clabe>=0.18", main: bool = True, group: bool = False) -> Path:
    path = root / name
    path.mkdir(parents=True)
    if main:
        (path / "main.py").write_text("", encoding="utf-8")
    if group:
        text = f'[project]\nname = "{name}"\ndependencies = []\n[dependency-groups]\ndev = ["{depends}"]\n'
    else:
        text = f'[project]\nname = "{name}"\ndependencies = ["{depends}"]\n'
    (path / "pyproject.toml").write_text(text, encoding="utf-8")
    return path


class TestRepositories:
    def test_an_experiment_repository_has_a_main_py_and_depends_on_clabe(self, tmp_path):
        assert is_experiment_repository(_repo(tmp_path, "a"))
        assert is_experiment_repository(_repo(tmp_path, "b", depends="aind-clabe[otel]==0.18.0", group=True))
        assert not is_experiment_repository(_repo(tmp_path, "c", main=False))
        assert not is_experiment_repository(_repo(tmp_path, "d", depends="aind-clabe-other"))
        assert not is_experiment_repository(_repo(tmp_path, "e", depends="numpy"))
        assert not is_experiment_repository(tmp_path / "missing")

    def test_found_under_every_root_after_the_listed_ones(self, tmp_path):
        _repo(tmp_path / "git", "Zeta")
        _repo(tmp_path / "git", "Alpha")
        _repo(tmp_path / "git", "NotClabe", depends="numpy")
        _repo(tmp_path / "other", "Beta")
        special = _repo(tmp_path / "elsewhere" / "deep", "Special")
        settings = DaemonSettings(
            repository_roots=[tmp_path / "git", tmp_path / "other", tmp_path / "missing"],
            repositories=[str(special)],
        )
        assert [r.display_name for r in settings.find_repositories()] == ["Special", "Alpha", "Zeta", "Beta"]

    def test_a_name_is_offered_once(self, tmp_path):
        _repo(tmp_path / "one", "Same")
        _repo(tmp_path / "two", "Same")
        settings = DaemonSettings(repository_roots=[tmp_path / "one", tmp_path / "two"])
        assert [r.path.parent.name for r in settings.find_repositories()] == ["one"]

    def test_it_listens_on_the_network_with_or_without_a_token(self):
        assert DaemonSettings().host == "0.0.0.0" and DaemonSettings().token is None


def _ended_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


class TestAttach:
    def _host(self, tmp_path) -> Host:
        return Host(DaemonSettings(repository_roots=[], runs_directory=tmp_path))

    def test_a_terminal_run_makes_the_machine_busy_until_it_ends(self, tmp_path):
        host = self._host(tmp_path)
        info = host.attach(AttachBody(port=9999, token="t", pid=_ended_pid(), repository="Repo", experiment="demo"))
        assert info.origin == "terminal"
        assert not host.busy() and host.info().status == "idle"  # its process has already ended

        import os

        host.attach(AttachBody(port=9999, token="t", pid=os.getpid(), repository="Repo"))
        assert host.busy() and host.info().status == "busy"
        assert host.current().poll_port() == 9999 and host.current().token == "t"
        host.attach(AttachBody(port=9998, pid=os.getpid(), repository="Repo"))  # announcing again is fine
        with pytest.raises(HostError, match="started from a terminal"):
            host.stop()
        host.detach(os.getpid())
        assert not host.busy()

    def test_refused_while_another_run_is_going_or_the_machine_is_leased(self, tmp_path):
        import os

        host = self._host(tmp_path)
        host.attach(AttachBody(port=9999, pid=os.getpid(), repository="Repo"))
        with pytest.raises(HostError, match="already going") as caught:
            host.attach(AttachBody(port=9999, pid=_ended_pid(), repository="Repo"))
        assert caught.value.status == 409
        leased = Host(DaemonSettings(repository_roots=[]), leased_by=lambda: "rig-a")
        with pytest.raises(HostError, match="rig-a"):
            leased.attach(AttachBody(port=9999, pid=os.getpid(), repository="Repo"))

    def test_pid_alive(self):
        import os

        assert pid_alive(os.getpid())
        assert not pid_alive(_ended_pid())


class TestAnnounce:
    def test_no_daemon_does_not_stop_the_run(self, monkeypatch):
        monkeypatch.setenv("DAEMON_CLABE_PORT", str(free_port("127.0.0.1", 19370)))
        monkeypatch.delenv(ANNOUNCE_ENV, raising=False)
        assert announce(port=1, token=None, repository="Repo", experiment="demo") is None

    def test_a_run_started_by_the_daemon_does_not_announce(self, monkeypatch):
        monkeypatch.setenv(ANNOUNCE_ENV, "x.json")
        called = []
        monkeypatch.setattr("clabe.daemon._announce.httpx.post", lambda *a, **k: called.append(a))
        assert announce(port=1, token=None, repository="Repo", experiment="demo") is None
        assert not called

    def test_it_tells_the_daemon_and_withdraws(self, monkeypatch):
        import httpx

        monkeypatch.delenv(ANNOUNCE_ENV, raising=False)
        monkeypatch.setenv("DAEMON_CLABE_TOKEN", "d4emon")
        sent = []

        def post(url, json, headers, timeout):
            sent.append(("post", url, json, headers))
            return httpx.Response(200, json={})

        def delete(url, headers, timeout):
            sent.append(("delete", url, None, headers))
            return httpx.Response(204)

        monkeypatch.setattr("clabe.daemon._announce.httpx.post", post)
        monkeypatch.setattr("clabe.daemon._announce.httpx.delete", delete)
        withdraw = announce(port=8123, token="run", repository="Repo", experiment="demo")
        assert withdraw is not None
        _, url, body, headers = sent[0]
        assert url.endswith("/api/daemon/runs/attached") and headers == {"Authorization": "Bearer d4emon"}
        assert body["port"] == 8123 and body["token"] == "run" and body["repository"] == "Repo"
        withdraw()
        assert sent[1][0] == "delete" and sent[1][1].endswith(f"/runs/attached/{body['pid']}")

    def test_a_busy_daemon_is_only_logged(self, monkeypatch):
        import httpx

        monkeypatch.delenv(ANNOUNCE_ENV, raising=False)
        monkeypatch.setattr(
            "clabe.daemon._announce.httpx.post", lambda *a, **k: httpx.Response(409, json={"detail": "busy"})
        )
        assert announce(port=1, token=None, repository="Repo", experiment=None) is None


@pytest.mark.skipif(sys.platform != "win32", reason="desktop shortcuts are made on Windows")
def test_a_desktop_shortcut_runs_the_experiment_in_a_terminal(tmp_path):
    import shutil

    if shutil.which("uv") is None:
        pytest.skip("needs uv")
    from clabe._shortcut import create_shortcut

    repo = tmp_path / "Aind.Experiment.Demo"
    repo.mkdir()
    (repo / "main.py").write_text("", encoding="utf-8")
    path = create_shortcut(repo, experiment="vr-foraging", directory=tmp_path)
    assert path == tmp_path / "vr-foraging.lnk" and path.is_file()
    with pytest.raises(FileNotFoundError, match="main.py"):
        create_shortcut(tmp_path, directory=tmp_path)
