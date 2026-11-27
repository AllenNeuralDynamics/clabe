"""Reviving a rig's daemon over SSH: against a real SSH server (asyncssh's), and through the hub's API."""

import asyncio
import base64
import logging

import pytest
from fastapi.testclient import TestClient

from clabe.hub import EnrollBody, Fleet, HubSettings, create_hub_app
from clabe.hub import _app as hub_app
from clabe.hub._revive import Revived, ReviveError, revive

asyncssh = pytest.importorskip("asyncssh")

PASSWORD = "s3cret-pa55word"
_HUB = {"Authorization": "Bearer hub-secret"}


class _Rig(asyncssh.SSHServer):
    """A rig's SSH server: one user, one password."""

    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return username == "rig-user" and password == PASSWORD


async def _serve(key, answer: str, exit_status: int = 0):
    """An SSH server on a free port that runs nothing: it records the command and answers ``answer``."""
    commands = []

    async def process(proc):
        commands.append(proc.command)
        proc.stdout.write(answer + "\n")
        proc.exit(exit_status)

    server = await asyncssh.create_server(
        _Rig, "127.0.0.1", 0, server_host_keys=[key], process_factory=process, encoding="utf-8"
    )
    port = server.sockets[0].getsockname()[1]
    return server, port, commands


def _decoded(command: str) -> str:
    """The PowerShell a command line carries (-EncodedCommand)."""
    return base64.b64decode(command.rsplit(" ", 1)[1]).decode("utf-16-le")


@pytest.fixture
def key():
    return asyncssh.generate_private_key("ssh-ed25519")


def test_it_restarts_the_task_and_remembers_the_host_key(key):
    async def scenario():
        server, port, commands = await _serve(key, '{"state":"Running","ended":[4242]}')
        try:
            first = await revive("127.0.0.1", "rig-user", PASSWORD, known_key=None, port=port)
            again = await revive("127.0.0.1", "rig-user", PASSWORD, known_key=first.host_key, port=port)
        finally:
            server.close()
        return first, again, commands

    first, again, commands = asyncio.run(scenario())
    assert (first.state, first.ended, first.first_connection) == ("Running", [4242], True)
    assert first.host_key.startswith("ssh-ed25519 ") and first.fingerprint.startswith("SHA256:")
    assert not again.first_connection and again.host_key == first.host_key
    script = _decoded(commands[0])
    assert "Stop-ScheduledTask" in script and "Enable-ScheduledTask" in script and "Start-ScheduledTask" in script
    assert PASSWORD not in commands[0]


def test_a_changed_host_key_is_refused_before_the_password_is_sent(key):
    other = asyncssh.generate_private_key("ssh-ed25519").export_public_key("openssh").decode()

    async def scenario():
        server, port, commands = await _serve(key, '{"state":"Running","ended":[]}')
        try:
            with pytest.raises(ReviveError, match="not the one it had") as refused:
                await revive("127.0.0.1", "rig-user", PASSWORD, known_key=other, port=port)
        finally:
            server.close()
        return refused.value, commands

    refused, commands = asyncio.run(scenario())
    assert refused.status == 409 and commands == []


def test_wrong_credentials_are_refused(key):
    async def scenario():
        server, port, _ = await _serve(key, "")
        try:
            with pytest.raises(ReviveError, match="refused these credentials") as refused:
                await revive("127.0.0.1", "rig-user", "wrong", known_key=None, port=port)
        finally:
            server.close()
        return refused.value

    assert asyncio.run(scenario()).status == 403


@pytest.mark.parametrize(
    "answer, exit_status, status, message",
    [
        ('{"error": "no-task"}', 3, 409, "no clabe-daemon task"),
        ("Access is denied.", 1, 502, "logged-on user or an administrator"),
    ],
)
def test_a_rig_that_cannot_restart_it_says_why(key, answer, exit_status, status, message):
    async def scenario():
        server, port, _ = await _serve(key, answer, exit_status)
        try:
            with pytest.raises(ReviveError, match=message) as failed:
                await revive("127.0.0.1", "rig-user", PASSWORD, known_key=None, port=port)
        finally:
            server.close()
        return failed.value

    assert asyncio.run(scenario()).status == status


def test_an_unreachable_rig():
    async def scenario():
        with pytest.raises(ReviveError, match="Could not reach") as failed:
            await revive("127.0.0.1", "rig-user", PASSWORD, known_key=None, port=1, timeout=5)
        return failed.value

    assert asyncio.run(scenario()).status == 502


class TestTheHubsApi:
    """POST /api/hub/rigs/{id}/revive, with the SSH part stubbed."""

    @pytest.fixture
    def hub(self, monkeypatch):
        fleet = Fleet(HubSettings())
        fleet.enroll(EnrollBody(id="rig-3", host="wxvs-rig3", port=24070))
        calls = []

        async def fake_revive(host, username, password, *, known_key):
            calls.append((host, username, password, known_key))
            return Revived("Running", [], "ssh-ed25519 AAAAkey", "SHA256:abc", known_key is None)

        monkeypatch.setattr(hub_app, "revive", fake_revive)
        client = TestClient(create_hub_app(fleet, token="hub-secret", static_dir=None, poll=False))
        return client, fleet, calls

    def _revive(self, client, rig="rig-3"):
        return client.post(
            f"/api/hub/rigs/{rig}/revive", json={"username": "rig-user", "password": PASSWORD}, headers=_HUB
        )

    def test_it_reaches_the_rig_by_its_address_and_remembers_its_key(self, hub, caplog):
        client, fleet, calls = hub
        caplog.set_level(logging.INFO, logger="clabe.hub")
        first = self._revive(client)
        assert first.status_code == 200 and first.json()["first_connection"] is True
        assert calls[0] == ("wxvs-rig3", "rig-user", PASSWORD, None)
        assert fleet.registry.host_key("rig-3") == "ssh-ed25519 AAAAkey"
        second = self._revive(client)
        assert second.json()["first_connection"] is False and calls[1][3] == "ssh-ed25519 AAAAkey"
        assert PASSWORD not in first.text + second.text + caplog.text  # never answered, never logged

    def test_removing_the_rig_forgets_its_key(self, hub):
        client, fleet, _ = hub
        self._revive(client)
        assert client.delete("/api/hub/rigs/rig-3", headers=_HUB).status_code == 204
        assert fleet.registry.host_key("rig-3") is None

    def test_it_needs_the_hubs_token_and_a_known_rig(self, hub):
        client, _, calls = hub
        assert client.post("/api/hub/rigs/rig-3/revive", json={"username": "u", "password": "p"}).status_code == 401
        assert self._revive(client, rig="nobody").status_code == 404
        assert calls == []

    def test_a_rig_whose_move_is_unconfirmed_gets_no_credentials(self, hub):
        client, fleet, calls = hub
        fleet.enroll(EnrollBody(id="rig-3", host="somewhere-else", port=24070))  # held until confirmed
        assert self._revive(client).status_code == 409 and calls == []

    def test_a_failure_answers_its_status_without_the_password(self, hub, monkeypatch, caplog):
        client, _, _ = hub
        caplog.set_level(logging.INFO, logger="clabe.hub")

        async def refused(host, username, password, *, known_key):
            raise ReviveError("wxvs-rig3 refused these credentials.", 403)

        monkeypatch.setattr(hub_app, "revive", refused)
        answer = self._revive(client)
        assert answer.status_code == 403 and "refused" in answer.json()["detail"]
        assert PASSWORD not in answer.text + caplog.text
