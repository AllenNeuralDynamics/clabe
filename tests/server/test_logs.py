"""The run's Python log records, served at /api/logs for the web UI's Log pane."""

import json
import logging

import pytest
import requests

from clabe.server import RunServer
from clabe.server._logs import LogBuffer
from clabe.workflow import Run, Workflow

logger = logging.getLogger("clabe.tests.logs")


@pytest.fixture(autouse=True)
def info_logging():
    """Lets info records through: tests/__init__.py disables logging for the suite, and the root logger may be set
    higher."""
    root = logging.getLogger()
    level, disabled = root.level, logging.root.manager.disable
    logging.disable(logging.NOTSET)
    root.setLevel(logging.INFO)
    yield
    root.setLevel(level)
    logging.disable(disabled)


def test_the_buffer_keeps_info_and_above_numbered_and_bounded():
    buffer = LogBuffer(maxlen=3)
    log = logging.getLogger("clabe.tests.buffer")
    log.setLevel(logging.DEBUG)
    log.propagate = False
    log.addHandler(buffer)
    seen = []
    unsubscribe = buffer.subscribe(seen.append)
    try:
        log.debug("hidden")
        for n in range(4):
            log.info("line %d", n)
        try:
            raise ValueError("boom")
        except ValueError:
            log.exception("failed")
    finally:
        unsubscribe()
        log.removeHandler(buffer)
    kept = buffer.since(0)
    assert [line.id for line in kept] == [3, 4, 5]  # the oldest dropped; ids keep counting
    assert [line.message for line in kept[:2]] == ["line 2", "line 3"]
    assert kept[-1].level == "error" and "ValueError: boom" in kept[-1].message
    assert [line.id for line in seen] == [1, 2, 3, 4, 5]
    assert buffer.since(4) == kept[-1:]


def _log_lines(url: str) -> list[dict]:
    """The ``log`` events of the stream at ``url`` (which ends when the session is over)."""
    lines = []
    with requests.get(url, stream=True, timeout=10) as response:
        response.raise_for_status()
        for raw in response.iter_lines():
            text = raw.decode("utf-8")
            if text.startswith("data:"):
                lines.append(json.loads(text.removeprefix("data:").strip()))
    return lines


def test_the_server_streams_the_process_log_records():
    wf = Workflow("demo")

    @wf.step
    def a():
        logger.info("calibrating %s", "rig-3")
        logger.warning("low water")
        return 1

    run = Run(wf)
    server = RunServer(run, port=18190)
    server.start()
    try:
        run.start()
        assert run.wait(5)
        lines = [
            line for line in _log_lines(f"http://127.0.0.1:{server.port}/api/logs") if line["logger"] == logger.name
        ]
        assert [(line["level"], line["message"]) for line in lines] == [
            ("info", "calibrating rig-3"),
            ("warning", "low water"),
        ]
        after = lines[0]["id"]
        resumed = _log_lines(f"http://127.0.0.1:{server.port}/api/logs?after={after}")
        assert all(line["id"] > after for line in resumed) and lines[1] in resumed
    finally:
        server.stop()
    assert not any(isinstance(h, LogBuffer) for h in logging.getLogger().handlers)  # detached on stop
