"""What the hub logs about a rig, from one poll to the next: each change once."""

import datetime
import logging

import pytest

from clabe.hub._fleet import RigStatus, Waiting, log_change

NOW = datetime.datetime(2026, 11, 27, tzinfo=datetime.UTC)


def _seen(**fields) -> RigStatus:
    return RigStatus(id="rig-3", url="http://rig-3:24070", seen_at=NOW, **fields)


IDLE = {"reachable": True, "status": "idle", "clabe_version": "0.18.0"}
BUSY = IDLE | {"status": "busy", "experiment": "vr-foraging", "repository": "Aind.Experiment.VrForaging"}


@pytest.fixture
def logged(caplog):
    caplog.set_level(logging.INFO, logger="clabe.hub")

    def log(previous, status):
        caplog.clear()
        log_change(previous, status)
        return [record.getMessage() for record in caplog.records]

    return log


def test_first_seen_and_unchanged(logged):
    assert logged(None, _seen(**IDLE)) == ["rig-3 is reachable: clabe 0.18.0, idle"]
    assert logged(_seen(**IDLE), _seen(**IDLE)) == []


def test_lost_once(logged):
    gone = _seen(error="ConnectError")
    assert logged(_seen(**IDLE), gone) == ["rig-3 is not reachable: ConnectError"]
    assert logged(gone, gone) == []
    assert logged(RigStatus(id="rig-3", url="u"), gone) == ["rig-3 is not reachable: ConnectError"]  # never polled


def test_a_run_starts_waits_and_finishes(logged):
    asking = _seen(**BUSY, waiting=Waiting(prompt_id="p", kind="text", label="Subject", step="subject", since=NOW))
    assert logged(_seen(**IDLE), _seen(**BUSY)) == ["rig-3 started vr-foraging (Aind.Experiment.VrForaging)"]
    assert logged(_seen(**BUSY), asking) == ["rig-3 waits for an answer: Subject"]
    assert logged(asking, asking) == []
    assert logged(asking, _seen(**IDLE)) == ["rig-3 finished vr-foraging"]


def test_a_new_clabe_and_a_refused_token(logged):
    assert logged(_seen(**IDLE), _seen(**IDLE | {"clabe_version": "0.19.0"})) == [
        "rig-3 runs clabe 0.19.0 (was 0.18.0)"
    ]
    refused = _seen(needs_token=True, error="401")
    assert logged(_seen(**IDLE), refused)[0].startswith("rig-3 refuses the hub's token")
    assert logged(refused, refused) == []
