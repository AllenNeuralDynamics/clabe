import functools
import getpass
import subprocess
import sys
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from clabe.git_manager import GitRepository
from clabe.launcher import _manifest
from clabe.launcher._cli import LauncherCliArgs
from clabe.launcher._manifest import collect_manifest, describe_steps
from clabe.workflow import Workflow, as_workflow, experiment


def _git(path, *args):
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "uv.lock").write_text("lock")
    (path / "a.txt").write_text("one\n")
    _git(path, "add", ".")
    _git(path, "-c", "commit.gpgsign=false", "commit", "-m", "init")
    return path


def _launcher(tmp_path, repository):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    return SimpleNamespace(temp_dir=out, repository=repository, settings=LauncherCliArgs())


def _one_step() -> Workflow:
    wf = Workflow("w")
    wf.step(lambda: 1, name="a")
    return wf


def test_describe_steps_points_at_the_real_function():
    @experiment(name="legacy")
    def legacy(launcher): ...

    [step] = describe_steps(as_workflow(legacy))
    assert step.name == "experiment"
    assert step.function.endswith("legacy")
    assert step.file == __file__ and step.source_sha256


def test_the_manifest_collects_every_section(tmp_path, repo, monkeypatch):
    monkeypatch.setenv("CLABE_MODE", "dev")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "Authorization=abc")
    launcher = _launcher(tmp_path, GitRepository(path=repo))

    manifest = collect_manifest(launcher, _one_step(), run_id="r1", directory=launcher.temp_dir)

    assert manifest.errors == []
    assert manifest.code.repository.commit_sha and manifest.code.diff_file is None
    assert manifest.code.workflow == "w" and [s.name for s in manifest.code.steps] == ["a"]
    assert manifest.environment.uv_lock_sha256
    assert (launcher.temp_dir / "uv.lock").read_text() == "lock"
    assert "pydantic" in {name.lower() for name in manifest.environment.distributions}
    assert manifest.machine.hostname and manifest.machine.cpu_count
    assert manifest.config.env["CLABE_MODE"] == "dev"
    assert manifest.config.env["OTEL_EXPORTER_OTLP_HEADERS"] == "***"


def test_a_dirty_repository_saves_its_diff_capped_in_size(tmp_path, repo, monkeypatch):
    (repo / "a.txt").write_text("two\n")
    (repo / "new.txt").write_text("x")
    launcher = _launcher(tmp_path, GitRepository(path=repo))

    manifest = collect_manifest(launcher, _one_step(), run_id="r1", directory=launcher.temp_dir)
    assert "+two" in (launcher.temp_dir / "repository.diff").read_text(encoding="utf-8")
    assert manifest.code.untracked_files == ["new.txt"] and not manifest.code.diff_truncated

    monkeypatch.setattr(_manifest, "MAX_DIFF_BYTES", 10)
    manifest = collect_manifest(launcher, _one_step(), run_id="r2", directory=launcher.temp_dir)
    assert manifest.code.diff_truncated
    assert "+two" not in (launcher.temp_dir / "repository.diff").read_text(encoding="utf-8")


def test_a_failing_section_is_recorded_not_raised(tmp_path):
    launcher = _launcher(tmp_path, repository=None)

    manifest = collect_manifest(launcher, _one_step(), run_id="r1", directory=launcher.temp_dir)

    assert manifest.machine is not None
    assert any(error.startswith("code:") for error in manifest.errors)


def test_secrets_in_argv_are_masked(tmp_path, repo, monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["clabe", "--api-token", "abc", "--db-password=hunter2", "--name", "mouse", "--auth", "xyz", "--key"],
    )
    launcher = _launcher(tmp_path, GitRepository(path=repo))

    manifest = collect_manifest(launcher, _one_step(), run_id="r1", directory=launcher.temp_dir)

    assert manifest.code.argv == [
        "clabe",
        "--api-token",
        "***",
        "--db-password=***",
        "--name",
        "mouse",
        "--auth",
        "***",
        "--key",
    ]


def test_more_secret_looking_env_names_are_masked(tmp_path, monkeypatch):
    monkeypatch.setenv("CLABE_AUTH_BASIC", "a")
    monkeypatch.setenv("CLABE_DB_PASS", "b")
    monkeypatch.setenv("OTEL_CREDENTIALS", "c")
    launcher = _launcher(tmp_path, repository=None)

    env = collect_manifest(launcher, _one_step(), run_id="r1", directory=launcher.temp_dir).config.env

    assert env["CLABE_AUTH_BASIC"] == env["CLABE_DB_PASS"] == env["OTEL_CREDENTIALS"] == "***"


class _Nested(BaseModel):
    host: str = "db"
    password: str = "hunter2"


class _SecretSettings(LauncherCliArgs):
    api_token: str = "abc"
    database: _Nested = _Nested()


def test_secret_looking_settings_are_masked(tmp_path):
    launcher = _launcher(tmp_path, repository=None)
    launcher.settings = _SecretSettings()

    settings = collect_manifest(launcher, _one_step(), run_id="r1", directory=launcher.temp_dir).config.settings

    assert settings["api_token"] == "***"
    assert settings["database"] == {"host": "db", "password": "***"}
    assert settings["frontend"] == LauncherCliArgs().frontend


def test_the_environment_survives_a_missing_repository(tmp_path):
    launcher = _launcher(tmp_path, repository=None)

    manifest = collect_manifest(launcher, _one_step(), run_id="r1", directory=launcher.temp_dir)

    assert manifest.environment is not None and manifest.environment.uv_lock_sha256 is None
    assert not any(error.startswith("environment:") for error in manifest.errors)


def test_describe_steps_handles_a_partial():
    def add(x, y):
        return x + y

    wf = Workflow("w")
    wf.step(functools.partial(add, 1, 2), name="a")

    [step] = describe_steps(wf)
    assert step.name == "a" and step.function == "functools.partial"


def test_the_user_falls_back_to_the_environment(tmp_path, monkeypatch):
    def fail():
        raise OSError("no user")

    monkeypatch.setattr(getpass, "getuser", fail)
    monkeypatch.setenv("USERNAME", "someone")
    monkeypatch.setenv("USER", "someone")
    launcher = _launcher(tmp_path, repository=None)

    manifest = collect_manifest(launcher, _one_step(), run_id="r1", directory=launcher.temp_dir)

    assert manifest.machine.user == "someone"


def test_commands_open_no_console_window_on_windows(monkeypatch):
    calls = []
    monkeypatch.setattr(_manifest.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        _manifest.subprocess,
        "run",
        lambda args, **kwargs: calls.append(kwargs) or SimpleNamespace(stdout="v1\n"),
    )

    assert _manifest._command(["tool", "--version"]) == "v1"
    if sys.platform == "win32":
        assert calls[0]["creationflags"] == subprocess.CREATE_NO_WINDOW
    else:
        assert "creationflags" not in calls[0]


def test_untracked_files_alone_write_no_diff(tmp_path, repo):
    (repo / "new.txt").write_text("x")
    launcher = _launcher(tmp_path, GitRepository(path=repo))

    manifest = collect_manifest(launcher, _one_step(), run_id="r1", directory=launcher.temp_dir)

    assert manifest.code.diff_file is None and manifest.code.untracked_files == ["new.txt"]
    assert not (launcher.temp_dir / "repository.diff").exists()
