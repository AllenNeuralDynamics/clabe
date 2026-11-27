"""Reviving a rig's daemon over SSH: for a daemon that hangs or is not running (its API cannot restart it).

The hub connects to the rig with the operator's credentials, which come with that one request: they are used for
that connection only, never stored and never logged. On the rig, PowerShell ends the ``clabe-daemon`` task and any
daemon process left over, enables the task, and starts it.

The rig's SSH host key is remembered the first time (in hub.db); a later connection to a rig that shows another key
is refused before the password is sent (the key exchange fails before authentication).
"""

import base64
import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: The PowerShell run on the rig. Its last line is JSON: the task's state after, and the processes ended.
REVIVE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$name = 'clabe-daemon'
if (-not (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue)) {
    Write-Output '{"error": "no-task"}'; exit 3
}
Stop-ScheduledTask -TaskName $name
# a daemon left over (ended badly, or started by hand): clabe-daemon.exe, its Python, or an older `clabe daemon run`
$ended = @(Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and ($_.CommandLine -like '*clabe-daemon*run*' -or $_.CommandLine -like '*clabe*daemon run*')
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $_.ProcessId })
Enable-ScheduledTask -TaskName $name | Out-Null
Start-ScheduledTask -TaskName $name
$deadline = (Get-Date).AddSeconds(20)
do { Start-Sleep -Milliseconds 500; $state = "$((Get-ScheduledTask -TaskName $name).State)" }
while ($state -ne 'Running' -and (Get-Date) -lt $deadline)
Write-Output (@{ state = $state; ended = $ended } | ConvertTo-Json -Compress)
"""


class ReviveError(Exception):
    """Reviving failed; ``status`` is the HTTP status the hub answers with."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Revived:
    """What reviving did on the rig."""

    state: str
    ended: list[int]
    host_key: str
    fingerprint: str
    first_connection: bool


def _command() -> str:
    """The command line run over SSH (it works under cmd.exe and PowerShell, the shells of Windows' OpenSSH)."""
    encoded = base64.b64encode(REVIVE_SCRIPT.encode("utf-16-le")).decode("ascii")
    return f"powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {encoded}"


def _result(stdout: str) -> dict:
    """The script's JSON line (its last non-empty one), or an empty dict."""
    for line in reversed(stdout.strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return {}


async def revive(
    host: str,
    username: str,
    password: str,
    *,
    known_key: str | None,
    port: int = 22,
    timeout: float = 30.0,
) -> Revived:
    """Restarts the ``clabe-daemon`` task on ``host`` over SSH.

    Args:
        host: The rig's address.
        username: The SSH user: the rig's logged-on user (the task's owner) or an administrator.
        password: Their password, used for this connection only.
        known_key: The rig's host key from before (OpenSSH format), or None the first time.
        port: The SSH port.
        timeout: Seconds to connect.

    Raises:
        ReviveError: The ``ssh`` extra is missing (501), the credentials are refused (403), the host key changed or
            the rig has no task (409), or the connection or the restart failed (502).
    """
    try:
        import asyncssh
    except ImportError as exc:
        raise ReviveError("The hub was installed without SSH support (the ssh extra: aind-clabe[ssh]).", 501) from exc

    known_hosts = ([asyncssh.import_public_key(known_key)], [], []) if known_key else None
    try:
        async with asyncssh.connect(
            host,
            port=port,
            username=username,
            password=password,
            known_hosts=known_hosts,
            client_keys=None,
            agent_path=None,
            connect_timeout=timeout,
        ) as connection:
            key = connection.get_server_host_key()
            done = await connection.run(_command(), check=False, timeout=90)
    except asyncssh.HostKeyNotVerifiable as exc:
        raise ReviveError(
            f"{host}'s SSH host key is not the one it had: nothing was sent. If the rig was reinstalled, remove it "
            "from the hub (it enrolls again) and try again.",
            409,
        ) from exc
    except asyncssh.PermissionDenied as exc:
        raise ReviveError(f"{host} refused these credentials.", 403) from exc
    except (OSError, asyncssh.Error, TimeoutError) as exc:
        raise ReviveError(f"Could not reach {host} over SSH: {exc}", 502) from exc

    stdout = done.stdout if isinstance(done.stdout, str) else ""
    stderr = done.stderr if isinstance(done.stderr, str) else ""
    result = _result(stdout)
    if result.get("error") == "no-task":
        raise ReviveError(f"{host} has no clabe-daemon task: deploy the daemon there first.", 409)
    if done.exit_status != 0 or "state" not in result:
        detail = (stderr or stdout).strip()[-1000:]
        raise ReviveError(
            f"Restarting the daemon on {host} failed (the SSH user must be the rig's logged-on user or an "
            f"administrator): {detail}",
            502,
        )
    exported = key.export_public_key("openssh").decode("ascii").split()
    return Revived(
        state=str(result["state"]),
        ended=[int(pid) for pid in result.get("ended") or []],
        host_key=" ".join(exported[:2]),
        fingerprint=key.get_fingerprint(),
        first_connection=known_key is None,
    )
