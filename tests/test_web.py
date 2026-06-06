import socket
import sys

import pytest

from clabe import web


class _FakeServer:
    """Records constructor arguments and whether serve() was called."""

    last = None

    def __init__(self, command, host, port, title):
        self.command = command
        self.host = host
        self.port = port
        self.title = title
        self.served = False
        _FakeServer.last = self

    def serve(self):
        self.served = True


@pytest.fixture
def fake_server(monkeypatch):
    _FakeServer.last = None
    monkeypatch.setattr("textual_serve.server.Server", _FakeServer)
    return _FakeServer


class TestServe:
    def test_passes_arguments_through(self, fake_server, monkeypatch):
        monkeypatch.setattr(web, "_announce", lambda *a, **k: None)
        web.serve("the-command", host="127.0.0.1", port=9999, title="T")
        assert fake_server.last.command == "the-command"
        assert (fake_server.last.host, fake_server.last.port, fake_server.last.title) == ("127.0.0.1", 9999, "T")
        assert fake_server.last.served is True

    def test_opens_browser_only_when_requested(self, fake_server, monkeypatch):
        monkeypatch.setattr(web, "_announce", lambda *a, **k: None)
        calls = []
        monkeypatch.setattr(web, "_open_browser_when_ready", lambda host, port: calls.append((host, port)))

        web.serve("cmd", host="127.0.0.1", port=9998, open_browser=False)
        assert calls == []
        web.serve("cmd", host="127.0.0.1", port=9998, open_browser=True)
        assert calls == [("127.0.0.1", 9998)]

    def test_friendly_error_when_port_in_use(self, fake_server, monkeypatch):
        monkeypatch.setattr(web, "_announce", lambda *a, **k: None)
        monkeypatch.setattr(_FakeServer, "serve", lambda self: (_ for _ in ()).throw(OSError("address in use")))
        with pytest.raises(RuntimeError, match="port"):
            web.serve("cmd")

    def test_requires_web_extra(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "textual_serve.server", None)
        with pytest.raises(ImportError, match="web"):
            web.serve("cmd")


class TestOpenBrowserWhenReady:
    def test_opens_once_port_is_reachable(self, monkeypatch):
        opened = []
        monkeypatch.setattr(web.webbrowser, "open", lambda url: opened.append(url))

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        try:
            web._open_browser_when_ready("127.0.0.1", port).join(timeout=5)
        finally:
            listener.close()

        assert opened == [f"http://127.0.0.1:{port}"]

    def test_normalizes_wildcard_host(self, monkeypatch):
        opened = []
        monkeypatch.setattr(web.webbrowser, "open", lambda url: opened.append(url))

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        try:
            web._open_browser_when_ready("0.0.0.0", port).join(timeout=5)
        finally:
            listener.close()

        assert opened == [f"http://127.0.0.1:{port}"]
