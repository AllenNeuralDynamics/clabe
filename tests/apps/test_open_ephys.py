import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
import requests

from clabe.apps import CommandError
from clabe.apps.open_ephys import (
    OpenEphysApp,
    OpenEphysAppSettings,
    RecordingResponse,
    RecordNode,
    Status,
    _OpenEphysGuiClient,
)


@pytest.fixture
def put(monkeypatch):
    """Never send real HTTP requests in these tests."""
    mocked = Mock()
    monkeypatch.setattr("clabe.apps.open_ephys.requests.put", mocked)
    return mocked


def test_app_exposes_private_client():
    app = OpenEphysApp(
        OpenEphysAppSettings(signal_chain="example.xml", address="test-host", port=12345),
        skip_validation=True,
    )
    assert isinstance(app.client, _OpenEphysGuiClient)
    assert app.client.base_url == "http://test-host:12345/api"


@pytest.mark.parametrize("metadata", [[], [{"notes": "first"}, {"notes": "second"}]])
def test_get_processor_metadata(put, metadata):
    put.return_value.json.return_value = {"info": json.dumps(metadata)}
    client = _OpenEphysGuiClient(host="test-host", port=12345, timeout=2.5)

    assert client.get_processor_metadata(42) == metadata
    put.assert_called_once_with(
        "http://test-host:12345/api/processors/42/config",
        json={"text": "get metadata"},
        timeout=2.5,
    )
    put.return_value.raise_for_status.assert_called_once_with()


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({}, "JSON string in 'info'"),
        ([], "JSON string in 'info'"),
        ({"info": None}, "JSON string in 'info'"),
        ({"info": []}, "JSON string in 'info'"),
        ({"info": "Unknown config message"}, "valid metadata JSON"),
        ({"info": "{"}, "valid metadata JSON"),
        ({"info": "{}"}, "array of objects"),
        ({"info": "null"}, "array of objects"),
        ({"info": "[1]"}, "array of objects"),
        ({"info": '["{}"]'}, "array of objects"),
    ],
)
def test_reject_invalid_metadata_response(put, response, message):
    put.return_value.json.return_value = response
    with pytest.raises(ValueError, match=message):
        _OpenEphysGuiClient().get_processor_metadata(42)


def test_http_error_is_not_decoded(put):
    put.return_value.raise_for_status.side_effect = requests.HTTPError("404")
    with pytest.raises(requests.HTTPError):
        _OpenEphysGuiClient().get_processor_metadata(42)
    put.return_value.json.assert_not_called()


def test_timeout_is_propagated(put):
    put.side_effect = requests.Timeout("unavailable")
    with pytest.raises(requests.Timeout):
        _OpenEphysGuiClient().get_processor_metadata(42)


def test_invalid_outer_json_is_propagated(put):
    put.return_value.json.side_effect = ValueError("invalid HTTP response JSON")
    with pytest.raises(ValueError, match="invalid HTTP response JSON"):
        _OpenEphysGuiClient().get_processor_metadata(42)


def test_send_processor_config_still_returns_raw_response(put):
    response = {"info": "Metadata history cleared."}
    put.return_value.json.return_value = response
    assert _OpenEphysGuiClient().send_processor_config(42, "clear history") == response


@pytest.fixture
def get(monkeypatch):
    """Mock configuration requests without contacting a running GUI."""
    mocked = Mock()
    monkeypatch.setattr("clabe.apps.open_ephys.requests.get", mocked)
    return mocked


def test_get_config_returns_xml_unchanged(get):
    xml_text = '<?xml version="1.0"?>\r\n<SETTINGS name="A &amp; B">\r\n  <SIGNALCHAIN />\r\n</SETTINGS>'
    get.return_value.json.return_value = {"info": xml_text}
    client = _OpenEphysGuiClient(host="test-host", port=12345, timeout=2.5)

    assert client.get_config() == xml_text
    get.assert_called_once_with("http://test-host:12345/api/config", timeout=2.5)
    get.return_value.raise_for_status.assert_called_once_with()


@pytest.mark.parametrize("response", [{}, [], {"info": None}, {"info": 123}, {"info": {}}])
def test_get_config_rejects_invalid_response(get, response):
    get.return_value.json.return_value = response
    with pytest.raises(ValueError, match="XML text in 'info'"):
        _OpenEphysGuiClient().get_config()


def test_get_config_http_error(get):
    get.return_value.raise_for_status.side_effect = requests.HTTPError("500")
    with pytest.raises(requests.HTTPError):
        _OpenEphysGuiClient().get_config()
    get.return_value.json.assert_not_called()


def test_get_config_timeout(get):
    get.side_effect = requests.Timeout("unavailable")
    with pytest.raises(requests.Timeout):
        _OpenEphysGuiClient().get_config()


def test_get_config_invalid_json(get):
    get.return_value.json.side_effect = ValueError("invalid HTTP response JSON")
    with pytest.raises(ValueError, match="invalid HTTP response JSON"):
        _OpenEphysGuiClient().get_config()


def _launchable_app(script: Path, port: int) -> OpenEphysApp:
    return OpenEphysApp(
        OpenEphysAppSettings(signal_chain=script, executable=sys.executable, address="127.0.0.1", port=port)
    )


class TestLaunch:
    @pytest.mark.asyncio
    async def test_launch_waits_for_api_and_close_quits(self, fake_open_ephys_gui):
        app = _launchable_app(*fake_open_ephys_gui(delay=0.5))
        client = await app.launch(poll_interval=0.05)
        assert client is app.client
        assert client.get_status() == Status.IDLE

        result = await app.close()
        assert result is not None and result.exit_code == 0
        assert "quit" in (result.stdout or "")
        assert await app.close() is None, "closing again does nothing"

    @pytest.mark.asyncio
    async def test_raises_when_gui_exits_before_api(self, fake_open_ephys_gui):
        app = _launchable_app(*fake_open_ephys_gui(exit_code=3))
        with pytest.raises(CommandError):
            await app.launch(poll_interval=0.05)
        assert await app.close() is None, "a failed launch closes the GUI itself"

    @pytest.mark.asyncio
    async def test_times_out_when_api_never_responds(self, fake_open_ephys_gui):
        app = _launchable_app(*fake_open_ephys_gui(delay=2.0, exit_code=0))
        with pytest.raises(TimeoutError):
            await app.launch(timeout=0.3, poll_interval=0.05)
        assert await app.close() is None

    @pytest.mark.asyncio
    async def test_launch_twice_raises(self, fake_open_ephys_gui):
        app = _launchable_app(*fake_open_ephys_gui())
        await app.launch(poll_interval=0.05)
        try:
            with pytest.raises(RuntimeError, match="already launched"):
                await app.launch(poll_interval=0.05)
        finally:
            await app.close()

    @pytest.mark.asyncio
    async def test_close_without_launch_does_nothing(self):
        app = OpenEphysApp(OpenEphysAppSettings(signal_chain="x.xml"), skip_validation=True)
        assert await app.close() is None

    def test_start_and_stop_use_the_client(self):
        app = OpenEphysApp(OpenEphysAppSettings(signal_chain="x.xml"), skip_validation=True)
        app._client = Mock(spec=_OpenEphysGuiClient)
        app.start()
        app.start(record=False)
        app.stop()
        app._client.start_recording.assert_called_once_with()
        app._client.start_acquisition.assert_called_once_with()
        app._client.stop.assert_called_once_with()

    def test_set_recording_directory_moves_default_and_stray_nodes(self):
        app = OpenEphysApp(OpenEphysAppSettings(signal_chain="x.xml"), skip_validation=True)
        app._client = Mock(spec=_OpenEphysGuiClient)

        def node(node_id: int, parent: str) -> RecordNode:
            return RecordNode(
                node_id=node_id,
                parent_directory=parent,
                record_engine="BINARY",
                experiment_number=1,
                recording_number=0,
                is_synchronized=True,
            )

        def response(*nodes: RecordNode) -> RecordingResponse:
            return RecordingResponse(
                parent_directory=r"D:\data\s",
                base_text="ecephys",
                prepend_text="",
                append_text="",
                record_nodes=list(nodes),
            )

        app._client.set_recording_config.return_value = response(node(101, r"D:\data\s"), node(105, r"E:\custom"))
        app._client.set_record_node_config.return_value = response(node(101, r"D:\data\s"), node(105, r"D:\data\s"))

        result = app.set_recording_directory(r"D:\data\s\ecephys")

        app._client.set_recording_config.assert_called_once_with(
            parent_directory=r"D:\data\s", base_text="ecephys", prepend_text="", append_text=""
        )
        app._client.set_record_node_config.assert_called_once_with(105, parent_directory=r"D:\data\s")
        assert all(n.parent_directory == r"D:\data\s" for n in result.record_nodes)

    def test_set_recording_directory_raises_when_gui_ignores_it(self):
        app = OpenEphysApp(OpenEphysAppSettings(signal_chain="x.xml"), skip_validation=True)
        app._client = Mock(spec=_OpenEphysGuiClient)
        # What the GUI answers when the parent does not exist: the previous directory, unchanged
        app._client.set_recording_config.return_value = RecordingResponse(
            parent_directory=r"C:\Users\me\Documents\Open Ephys",
            base_text="ecephys",
            prepend_text="",
            append_text="",
            record_nodes=[],
        )
        with pytest.raises(ValueError, match=r"Documents\\\\Open Ephys"):
            app.set_recording_directory(r"D:\data\missing\ecephys")

    def test_loopback_address_follows_remote_executor_host(self):
        app = OpenEphysApp(OpenEphysAppSettings(signal_chain="x.xml", port=1), skip_validation=True)
        executor = Mock(host="rig-b")
        executor.run_async = AsyncMock(side_effect=RuntimeError("not reachable"))
        with pytest.raises(RuntimeError, match="not reachable"):
            asyncio.run(app.launch(executor=executor, poll_interval=0.01))
        assert app.client.base_url == "http://rig-b:1/api"

    def test_explicit_address_is_kept_with_remote_executor(self):
        app = OpenEphysApp(OpenEphysAppSettings(signal_chain="x.xml", address="ephys-pc", port=1), skip_validation=True)
        executor = Mock(host="rig-b")
        executor.run_async = AsyncMock(side_effect=RuntimeError("not reachable"))
        with pytest.raises(RuntimeError):
            asyncio.run(app.launch(executor=executor, poll_interval=0.01))
        assert app.client.base_url == "http://ephys-pc:1/api"


_OPEN_EPHYS_DIR = Path(__file__).parents[1] / "assets" / "Aind.Physiology.OpenEphys" / ".open-ephys"
# Ships with the GUI download and needs no hardware, unlike the repository's example chain
_FILE_READER_CHAIN = _OPEN_EPHYS_DIR / "configs" / "file_reader_config.xml"


@pytest.mark.skipif(
    not (_OPEN_EPHYS_DIR / "open-ephys.exe").exists(),
    reason="Open Ephys GUI not downloaded: run tests/assets/Aind.Physiology.OpenEphys/.open-ephys/setup.ps1 -NoLaunch",
)
class TestRealGuiHeadless:
    @pytest.mark.asyncio
    async def test_launch_acquire_and_quit(self):
        app = OpenEphysApp(
            OpenEphysAppSettings(
                signal_chain=_FILE_READER_CHAIN,
                executable=_OPEN_EPHYS_DIR / "open-ephys.exe",
                address="127.0.0.1",
                headless=True,
            )
        )
        client = await app.launch(timeout=120.0)
        try:
            assert client.get_status() == Status.IDLE
            assert client.get_processors().processors
            assert app.start(record=False) == Status.ACQUIRE
            assert app.stop() == Status.IDLE
        finally:
            result = await app.close()
        assert result is not None and result.exit_code == 0
        assert "headless mode" in (result.stdout or "")

    @staticmethod
    def _headless_app() -> OpenEphysApp:
        return OpenEphysApp(
            OpenEphysAppSettings(
                signal_chain=_FILE_READER_CHAIN,
                executable=_OPEN_EPHYS_DIR / "open-ephys.exe",
                address="127.0.0.1",
                headless=True,
            )
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("pre_existing", [False, True])
    async def test_records_to_recording_directory(self, tmp_path: Path, pre_existing: bool):
        directory = tmp_path / "session" / "ecephys"
        directory.parent.mkdir()
        if pre_existing:
            directory.mkdir()
        app = self._headless_app()
        await app.launch(timeout=120.0)
        try:
            app.set_recording_directory(directory)
            assert app.start() == Status.RECORD
            app.stop()
        finally:
            await app.close()
        # An existing directory is not reused, as documented by set_recording_directory
        recorded = directory.with_name("ecephys (1)") if pre_existing else directory
        assert sorted(p.name for p in recorded.iterdir()) == ["Record Node 101", "Record Node 105"]

    @pytest.mark.asyncio
    async def test_missing_parent_is_rejected(self, tmp_path: Path):
        app = self._headless_app()
        await app.launch(timeout=120.0)
        try:
            with pytest.raises(ValueError, match="did not accept"):
                app.set_recording_directory(tmp_path / "missing" / "ecephys")
        finally:
            await app.close()


def test_headless_adds_flag():
    app = OpenEphysApp(OpenEphysAppSettings(signal_chain="x.xml", headless=True), skip_validation=True)
    assert app.command.cmd[-1] == "--headless"
    assert (
        "--headless" not in OpenEphysApp(OpenEphysAppSettings(signal_chain="x.xml"), skip_validation=True).command.cmd
    )


def test_quit_uses_quit_endpoint(put):
    put.return_value.json.return_value = {"info": "Quitting application"}
    _OpenEphysGuiClient(host="h", port=1).quit()
    assert put.call_args.args[0] == "http://h:1/api/quit"


def test_quit_falls_back_to_window_command_on_older_gui(put):
    not_found = requests.HTTPError(response=Mock(status_code=404))
    put.return_value.raise_for_status.side_effect = [not_found, None]
    _OpenEphysGuiClient(host="h", port=1).quit()
    assert [call.args[0] for call in put.call_args_list] == ["http://h:1/api/quit", "http://h:1/api/window"]
    assert put.call_args.kwargs["json"] == {"command": "quit"}


def test_quit_raises_other_http_errors(put):
    put.return_value.raise_for_status.side_effect = requests.HTTPError(response=Mock(status_code=500))
    with pytest.raises(requests.HTTPError):
        _OpenEphysGuiClient(host="h", port=1).quit()
