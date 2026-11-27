"""The daemon's watchdog: a server that stops answering ends the daemon; any answer keeps it."""

import http.server
import logging
import socket
import threading

import pytest

from clabe.daemon._watchdog import Watchdog


@pytest.fixture
def silent_url():
    """A port that accepts connections and never answers (a hung server)."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    yield f"http://127.0.0.1:{listener.getsockname()[1]}/api/daemon/info"
    listener.close()


@pytest.fixture
def refusing_url():
    """A server that answers every request with 401 (the daemon without its token)."""

    class Unauthorized(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(401)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Unauthorized)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/api/daemon/info"
    server.shutdown()


def _watch(url: str, limit: float):
    ended = threading.Event()
    codes = []

    def end(code):
        codes.append(code)
        ended.set()

    return Watchdog(url, interval=0.05, limit=limit, end=end), ended, codes


def test_a_server_that_does_not_answer_ends_the_daemon(silent_url, caplog):
    caplog.set_level(logging.WARNING, logger="clabe.daemon._watchdog")
    watchdog, ended, codes = _watch(silent_url, limit=0.3)
    watchdog.start()
    assert ended.wait(10)
    assert codes == [1] and "ending the daemon" in caplog.text


def test_any_answer_keeps_it_even_unauthorized(refusing_url):
    watchdog, ended, _ = _watch(refusing_url, limit=0.3)
    watchdog.start()
    assert not ended.wait(1.0)
    watchdog.stop()


def test_stopped_it_never_ends_the_daemon(silent_url):
    watchdog, ended, _ = _watch(silent_url, limit=0.3)
    watchdog.start()
    watchdog.stop()
    assert not ended.wait(0.6)
