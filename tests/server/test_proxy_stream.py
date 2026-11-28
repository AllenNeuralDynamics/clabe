"""A proxied answer whose target stops mid-stream (a run ending while its events stream) just ends."""

import socket
import threading

import httpx
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from clabe.server._proxy import forward


def _target_that_hangs_up() -> str:
    """A server that starts a chunked answer, sends one chunk, and closes the connection."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve():
        connection, _ = listener.accept()
        connection.recv(65536)
        connection.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n")
        connection.close()
        listener.close()

    threading.Thread(target=serve, daemon=True).start()
    return f"http://127.0.0.1:{listener.getsockname()[1]}/api/events"


def test_the_answer_ends_without_an_error():
    url = _target_that_hangs_up()
    app = FastAPI()

    @app.get("/events")
    async def events(request: Request):
        return await forward(httpx.AsyncClient(), request, url, None, unreachable="gone")

    with TestClient(app) as client:  # raises if the app raises
        answer = client.get("/events")
    assert answer.status_code == 200 and answer.text == "hello"
