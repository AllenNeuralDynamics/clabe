import threading
import typing as t
from pathlib import Path

import pydantic
import pytest
from pydantic_settings import CliApp

from clabe import settings as clabe_settings
from clabe.launcher import LauncherCliArgs
from clabe.settings import ServiceSettings, get_clabe_yml, override_clabe_yml, set_clabe_yml


class _MySettings(ServiceSettings):
    __yml_section__: t.ClassVar[str | None] = "my_service"

    host: str = "localhost"
    port: int = 8080


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    local = tmp_path / "local.yml"
    shared = tmp_path / "shared.yml"
    monkeypatch.setattr(clabe_settings, "KNOWN_CONFIG_FILES", [str(local), str(shared)])
    set_clabe_yml(None)
    yield local, shared
    set_clabe_yml(None)


def test_no_config_uses_defaults():
    assert get_clabe_yml() is None
    settings = _MySettings()
    assert settings.host == "localhost"
    assert settings.port == 8080


def test_set_clabe_yml_is_used():
    set_clabe_yml({"my_service": {"port": 9000}})
    settings = _MySettings()
    assert settings.port == 9000
    assert settings.host == "localhost"


def test_missing_section_is_ignored():
    set_clabe_yml({"other_service": {"port": 9000}})
    assert _MySettings().port == 8080


def test_in_memory_beats_every_file(_isolated_config):
    """--clabe-yml (the in-memory document) first, then ./local/clabe.yml, then %PROGRAMDATA%/clabe.yml."""
    local, shared = _isolated_config
    shared.write_text("my_service:\n  port: 1111\n  host: shared-host\n")
    local.write_text("my_service:\n  port: 3333\n")
    assert (_MySettings().port, _MySettings().host) == (3333, "shared-host")
    set_clabe_yml({"my_service": {"port": 2222}})
    assert (_MySettings().port, _MySettings().host) == (2222, "shared-host")


def test_environment_variables_are_prefixed_and_rank_below_the_files(_isolated_config, monkeypatch):
    local, _ = _isolated_config
    monkeypatch.setenv("PORT", "1")  # an unrelated variable never reaches clabe
    assert _MySettings().port == 8080
    monkeypatch.setenv("CLABE_MY_SERVICE_PORT", "4444")
    assert _MySettings().port == 4444
    local.write_text("my_service:\n  port: 3333\n")
    assert _MySettings().port == 3333


def test_the_repository_clabe_yml_is_not_read(monkeypatch):
    monkeypatch.undo()  # the real list, not the isolated one
    from clabe.settings import KNOWN_CONFIG_FILES

    assert "./clabe.yml" not in KNOWN_CONFIG_FILES and KNOWN_CONFIG_FILES[0] == "./local/clabe.yml"


def test_the_daemon_and_the_hub_read_only_their_environment(_isolated_config, monkeypatch):
    from clabe.daemon import DaemonSettings
    from clabe.hub._fleet import HubSettings

    local, _ = _isolated_config
    local.write_text("daemon:\n  port: 1\nhub:\n  port: 2\n")
    set_clabe_yml({"daemon": {"port": 3}, "hub": {"port": 4}})
    assert DaemonSettings().port == 24070 and HubSettings().port == 8080
    monkeypatch.setenv("DAEMON_CLABE_PORT", "5")
    monkeypatch.setenv("DAEMON_CLABE_REPOSITORY_ROOTS", "C:/a;D:/b")
    monkeypatch.setenv("DAEMON_CLABE_REPOSITORIES", '["C:/x", {"path": "C:/y", "name": "why"}]')
    monkeypatch.setenv("DAEMON_CLABE_HUBS", "https://hub-vm/clabe;http://hub-2:8080")
    monkeypatch.setenv("TOKEN", "hub")
    daemon, hub = DaemonSettings(), HubSettings()
    assert daemon.port == 5 and [str(p) for p in daemon.repository_roots] == [str(Path("C:/a")), str(Path("D:/b"))]
    assert [r.display_name for r in daemon.repositories] == ["x", "why"]
    assert daemon.hubs == ["https://hub-vm/clabe", "http://hub-2:8080"]
    assert hub.token.get_secret_value() == "hub"


def test_the_hub_reads_a_dot_env_file(tmp_path, monkeypatch):
    from clabe.hub._fleet import HubSettings

    (tmp_path / ".env").write_text("TOKEN=from-file\nPOLL_INTERVAL=7\n")
    monkeypatch.chdir(tmp_path)
    hub = HubSettings()
    assert hub.token.get_secret_value() == "from-file" and hub.poll_interval == 7


def test_the_daemon_reads_its_env_file_under_the_environment(tmp_path, monkeypatch):
    from clabe.daemon import DaemonSettings

    env_file = tmp_path / "daemon.env"
    env_file.write_text("DAEMON_CLABE_HUBS=http://hub-vm:8080\nDAEMON_CLABE_ENROLL_NAME=from-file\n")
    monkeypatch.setitem(DaemonSettings.model_config, "env_file", env_file)
    monkeypatch.setenv("DAEMON_CLABE_ENROLL_NAME", "from-env")
    daemon = DaemonSettings()
    assert daemon.hubs == ["http://hub-vm:8080"] and daemon.enroll_name == "from-env"


def test_init_kwargs_beat_in_memory():
    set_clabe_yml({"my_service": {"port": 9000}})
    assert _MySettings(port=1).port == 1


def test_override_clabe_yml_overrides_global_and_restores():
    set_clabe_yml({"my_service": {"port": 9000}})
    with override_clabe_yml({"my_service": {"port": 9001}}):
        assert _MySettings().port == 9001
    assert _MySettings().port == 9000


def test_override_clabe_yml_none_disables_global():
    set_clabe_yml({"my_service": {"port": 9000}})
    with override_clabe_yml(None):
        assert get_clabe_yml() is None
        assert _MySettings().port == 8080
    assert _MySettings().port == 9000


def test_override_clabe_yml_is_scoped_to_thread():
    set_clabe_yml({"my_service": {"port": 9000}})
    seen: list[int] = []
    with override_clabe_yml({"my_service": {"port": 9001}}):
        thread = threading.Thread(target=lambda: seen.append(_MySettings().port))
        thread.start()
        thread.join()
    assert seen == [9000]


def test_set_clabe_yml_copies_input():
    doc = {"my_service": {"port": 9000}}
    set_clabe_yml(doc)
    doc["my_service"] = {"port": 1}
    assert _MySettings().port == 9000


@pytest.mark.parametrize("bad", ["clabe.yml", Path("clabe.yml"), b"my_service: {}", ["my_service"]])
def test_set_clabe_yml_rejects_non_mappings(bad):
    with pytest.raises(TypeError):
        set_clabe_yml(bad)
    with pytest.raises(TypeError), override_clabe_yml(bad):
        pass


class TestCliClabeYml:
    def test_installs_file_for_later_settings(self, tmp_path: Path):
        clabe_yml = tmp_path / "somewhere" / "clabe.yml"
        clabe_yml.parent.mkdir()
        clabe_yml.write_text("my_service:\n  port: 4242\n", encoding="utf-8")

        args = CliApp.run(LauncherCliArgs, cli_args=["--clabe-yml", str(clabe_yml)])

        assert args.clabe_yml == clabe_yml.resolve()
        assert get_clabe_yml() == {"my_service": {"port": 4242}}
        assert _MySettings().port == 4242

    def test_not_passed_leaves_document_untouched(self):
        CliApp.run(LauncherCliArgs, cli_args=[])
        assert get_clabe_yml() is None

    def test_empty_file_is_an_empty_document(self, tmp_path: Path):
        clabe_yml = tmp_path / "clabe.yml"
        clabe_yml.write_text("", encoding="utf-8")
        CliApp.run(LauncherCliArgs, cli_args=["--clabe-yml", str(clabe_yml)])
        assert get_clabe_yml() == {}

    def test_missing_file_is_rejected(self, tmp_path: Path):
        with pytest.raises(pydantic.ValidationError):
            CliApp.run(LauncherCliArgs, cli_args=["--clabe-yml", str(tmp_path / "missing.yml")])

    def test_non_mapping_file_is_rejected(self, tmp_path: Path):
        clabe_yml = tmp_path / "clabe.yml"
        clabe_yml.write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises((pydantic.ValidationError, ValueError)):
            CliApp.run(LauncherCliArgs, cli_args=["--clabe-yml", str(clabe_yml)])
