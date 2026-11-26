import logging
from pathlib import Path

from clabe.workflow.events import EventLog, Notify, RunStarted, StepStarted


def test_a_log_that_cannot_be_written_keeps_recording_in_memory(tmp_path, monkeypatch, caplog):
    path = tmp_path / "events.jsonl"
    log = EventLog("r1", path)
    got = []
    log.subscribe(got.append)
    opened = []

    def failing_open(self, *args, **kwargs):
        opened.append(self)
        raise OSError("disk gone")

    monkeypatch.setattr(Path, "open", failing_open)
    with caplog.at_level(logging.ERROR, logger="clabe.workflow._events"):
        first = log.append(StepStarted(step="a"))
        second = log.append(StepStarted(step="b"))

    assert (first.seq, first.run_id, second.seq) == (1, "r1", 2)
    assert got == [first, second]
    assert log.since(0) == [first, second]
    assert opened == [path]  # tried once, then stopped mirroring
    assert len([r for r in caplog.records if r.exc_info]) == 1


def test_append_assigns_seq_and_run_id():
    log = EventLog("r1")
    first = log.append(RunStarted(workflow="w", steps=["a"]))
    second = log.append(StepStarted(step="a"))
    assert (first.seq, second.seq, second.run_id) == (1, 2, "r1")
    assert log.last_seq == 2
    assert log.since(1) == [second]


def test_log_is_persisted_as_json_lines(tmp_path):
    path = tmp_path / "Logs" / "events.jsonl"
    log = EventLog("r1", path)
    log.append(Notify(message="hi", level="info"))
    log.append(StepStarted(step="a"))
    assert EventLog.read(path) == log.since(0)


def test_subscribers_get_events_until_they_unsubscribe():
    log = EventLog("r1")
    got = []
    unsubscribe = log.subscribe(got.append)
    log.append(StepStarted(step="a"))
    unsubscribe()
    log.append(StepStarted(step="b"))
    assert [e.step for e in got] == ["a"]


def test_a_failing_subscriber_does_not_break_the_log():
    log = EventLog("r1")
    log.subscribe(lambda event: 1 / 0)
    log.append(StepStarted(step="a"))
    assert log.last_seq == 1
