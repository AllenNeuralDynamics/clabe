import json
from unittest.mock import Mock

import pytest
import requests
from clabe.apps.open_ephys import (
    OpenEphysApp,
    OpenEphysAppSettings,
    OpenEphysGuiClient,
    _OpenEphysGuiClient,
)


@pytest.fixture
def put(monkeypatch):
    """Never send real HTTP requests in these tests."""
    mocked = Mock()
    monkeypatch.setattr("clabe.apps.open_ephys.requests.put", mocked)
    return mocked


def test_public_client_preserves_compatibility():
    assert _OpenEphysGuiClient is OpenEphysGuiClient
    app = OpenEphysApp(
        OpenEphysAppSettings(signal_chain="example.xml", address="test-host", port=12345),
        skip_validation=True,
    )
    assert isinstance(app.client, OpenEphysGuiClient)
    assert app.client.base_url == "http://test-host:12345/api"


@pytest.mark.parametrize("metadata", [[], [{"notes": "first"}, {"notes": "second"}]])
def test_get_processor_metadata(put, metadata):
    put.return_value.json.return_value = {"info": json.dumps(metadata)}
    client = OpenEphysGuiClient(host="test-host", port=12345, timeout=2.5)

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
        OpenEphysGuiClient().get_processor_metadata(42)


def test_http_error_is_not_decoded(put):
    put.return_value.raise_for_status.side_effect = requests.HTTPError("404")
    with pytest.raises(requests.HTTPError):
        OpenEphysGuiClient().get_processor_metadata(42)
    put.return_value.json.assert_not_called()


def test_timeout_is_propagated(put):
    put.side_effect = requests.Timeout("unavailable")
    with pytest.raises(requests.Timeout):
        OpenEphysGuiClient().get_processor_metadata(42)


def test_invalid_outer_json_is_propagated(put):
    put.return_value.json.side_effect = ValueError("invalid HTTP response JSON")
    with pytest.raises(ValueError, match="invalid HTTP response JSON"):
        OpenEphysGuiClient().get_processor_metadata(42)


def test_send_processor_config_still_returns_raw_response(put):
    response = {"info": "Metadata history cleared."}
    put.return_value.json.return_value = response
    assert OpenEphysGuiClient().send_processor_config(42, "clear history") == response


@pytest.fixture
def get(monkeypatch):
    """Mock configuration requests without contacting a running GUI."""
    mocked = Mock()
    monkeypatch.setattr("clabe.apps.open_ephys.requests.get", mocked)
    return mocked


def test_get_config_returns_xml_unchanged(get):
    xml_text = '<?xml version="1.0"?>\r\n<SETTINGS name="A &amp; B">\r\n  <SIGNALCHAIN />\r\n</SETTINGS>'
    get.return_value.json.return_value = {"info": xml_text}
    client = OpenEphysGuiClient(host="test-host", port=12345, timeout=2.5)

    assert client.get_config() == xml_text
    get.assert_called_once_with("http://test-host:12345/api/config", timeout=2.5)
    get.return_value.raise_for_status.assert_called_once_with()


@pytest.mark.parametrize("response", [{}, [], {"info": None}, {"info": 123}, {"info": {}}])
def test_get_config_rejects_invalid_response(get, response):
    get.return_value.json.return_value = response
    with pytest.raises(ValueError, match="XML text in 'info'"):
        OpenEphysGuiClient().get_config()


def test_get_config_http_error(get):
    get.return_value.raise_for_status.side_effect = requests.HTTPError("500")
    with pytest.raises(requests.HTTPError):
        OpenEphysGuiClient().get_config()
    get.return_value.json.assert_not_called()


def test_get_config_timeout(get):
    get.side_effect = requests.Timeout("unavailable")
    with pytest.raises(requests.Timeout):
        OpenEphysGuiClient().get_config()


def test_get_config_invalid_json(get):
    get.return_value.json.side_effect = ValueError("invalid HTTP response JSON")
    with pytest.raises(ValueError, match="invalid HTTP response JSON"):
        OpenEphysGuiClient().get_config()
