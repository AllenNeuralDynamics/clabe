import threading
import typing as t
from pathlib import Path

import pydantic
import pytest
from pydantic_settings import CliApp

from clabe import services
from clabe.launcher import LauncherCliArgs
from clabe.services import ServiceSettings, get_clabe_yml, override_clabe_yml, set_clabe_yml


class _MySettings(ServiceSettings):
    __yml_section__: t.ClassVar[str | None] = "my_service"

    host: str = "localhost"
    port: int = 8080


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    local = tmp_path / "local.yml"
    shared = tmp_path / "shared.yml"
    monkeypatch.setattr(services, "KNOWN_CONFIG_FILES", [str(local), str(shared)])
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


def test_in_memory_beats_shared_file_but_not_local_file(_isolated_config):
    local, shared = _isolated_config
    shared.write_text("my_service:\n  port: 1111\n  host: shared-host\n")
    set_clabe_yml({"my_service": {"port": 2222, "host": "memory-host"}})
    settings = _MySettings()
    assert settings.port == 2222
    assert settings.host == "memory-host"

    local.write_text("my_service:\n  port: 3333\n")
    settings = _MySettings()
    assert settings.port == 3333
    assert settings.host == "memory-host"


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
