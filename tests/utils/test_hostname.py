import socket

import pytest

from clabe.utils import get_computer_name, is_this_computer


def test_computername_wins(monkeypatch):
    monkeypatch.setenv("COMPUTERNAME", "DT201256")
    monkeypatch.setenv("HOSTNAME", "ignored")
    assert get_computer_name() == "DT201256"


def test_hostname_is_the_posix_fallback(monkeypatch):
    monkeypatch.delenv("COMPUTERNAME", raising=False)
    monkeypatch.setenv("HOSTNAME", "leviathon")
    assert get_computer_name() == "leviathon"


def test_socket_is_the_last_resort(monkeypatch):
    monkeypatch.delenv("COMPUTERNAME", raising=False)
    monkeypatch.delenv("HOSTNAME", raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: "from-socket")
    assert get_computer_name() == "from-socket"


def test_it_is_not_the_aind_rig_name(monkeypatch):
    """``aibs_comp_id`` holds the AIND rig name (``FRG.4A``), a different identifier for the same
    machine. Ficus layers are keyed on the machine name, so the two must not be conflated."""
    monkeypatch.setenv("aibs_comp_id", "FRG.4A")
    monkeypatch.setenv("COMPUTERNAME", "DT201256")
    assert get_computer_name() == "DT201256"


@pytest.mark.parametrize(
    ("name", "expected"),
    [("DT201256", True), ("dt201256", True), ("DT201256.corp.alleninstitute.org", True), ("EPHYS-PC", False)],
)
def test_is_this_computer(monkeypatch, name, expected):
    monkeypatch.setenv("COMPUTERNAME", "DT201256")
    monkeypatch.setattr(socket, "gethostname", lambda: "dt201256.corp.alleninstitute.org")
    assert is_this_computer(name) is expected
