"""A run started outside the daemon (``clabe run`` in a terminal) announces itself to this machine's daemon.

The daemon then serves it under ``/run/`` and reports the machine busy, so the hub sees it like a run the daemon
started. Announcing is best effort: when no daemon answers, the run goes on unseen.
"""

import logging
import os
from collections.abc import Callable

import httpx

from ..server._server import ANNOUNCE_ENV

logger = logging.getLogger(__name__)

#: Seconds to wait for the daemon; a session must not wait on it.
TIMEOUT = 1.0


def announce(*, port: int, token: str | None, repository: str, experiment: str | None) -> Callable[[], None] | None:
    """Tells this machine's daemon about the run served on ``port``; returns how to withdraw it, or ``None``.

    Never raises: a missing, stopped or busy daemon is logged and the run goes on.

    Args:
        port: Where the run is served on this machine.
        token: The run server's token.
        repository: The experiment repository's name.
        experiment: The workflow's name.
    """
    if os.environ.get(ANNOUNCE_ENV):
        return None  # started by the daemon: it already follows the run
    try:
        from ._settings import DaemonSettings

        settings = DaemonSettings()
        daemon_token = (settings.token.get_secret_value() if settings.token else None) or os.environ.get("CLABE_TOKEN")
        base = f"http://127.0.0.1:{settings.port}/api/daemon/runs/attached"
        headers = {"Authorization": f"Bearer {daemon_token}"} if daemon_token else {}
        body = {"port": port, "token": token, "pid": os.getpid(), "repository": repository, "experiment": experiment}
        response = httpx.post(base, json=body, headers=headers, timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001 -- no daemon (or no settings) must never stop a session
        logger.debug("No daemon to announce the run to: %s", exc)
        return None
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        logger.warning("The daemon does not follow this run (%s): %s", response.status_code, detail)
        return None
    logger.info("The daemon on this machine follows this run")

    def withdraw() -> None:
        """Tells the daemon the run has ended."""
        try:
            httpx.delete(f"{base}/{os.getpid()}", headers=headers, timeout=TIMEOUT)
        except Exception as exc:  # noqa: BLE001 -- the daemon also notices the process is gone
            logger.debug("Could not tell the daemon the run ended: %s", exc)

    return withdraw
