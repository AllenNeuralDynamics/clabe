"""Enrolling with hubs: the daemon tells each hub of its ``hubs`` setting its name and where to reach it
(``POST <hub>/api/hub/enroll``), when it starts and then every ``enroll_interval`` seconds. Enrolling again is
harmless, and lets a hub that restarted, moved or lost its database learn of the rig without anyone touching it.

The hub never depends on the rig calling: once enrolled, the hub polls the daemon as any client would. A hub that
is down or refuses the rig changes nothing on the rig; it is retried sooner (every 30 s at most) until it answers.
"""

import asyncio
import contextlib
import logging
import socket
from collections.abc import AsyncGenerator

import httpx

from .. import __version__
from ._settings import DaemonSettings

logger = logging.getLogger(__name__)

#: Seconds before retrying a hub that did not enroll the rig (at most ``enroll_interval``).
RETRY_INTERVAL = 30.0


def enroll_body(settings: DaemonSettings) -> dict[str, object]:
    """What the daemon sends to enroll: its name, where it is reached, and its clabe."""
    hostname = socket.gethostname()
    return {
        "id": settings.enroll_name or hostname,
        "host": settings.enroll_host or hostname,
        "port": settings.port,
        "clabe_version": __version__,
    }


def enroll_url(hub: str) -> str:
    """The enroll endpoint of a hub given by its address (``https://hub-vm/clabe`` → ``…/clabe/api/hub/enroll``)."""
    return f"{hub.rstrip('/')}/api/hub/enroll"


async def enroll_once(settings: DaemonSettings, client: httpx.AsyncClient) -> dict[str, str | None]:
    """Enrolls with every hub; for each, None when it enrolled the rig, else why not."""
    body = enroll_body(settings)

    async def one(hub: str) -> str | None:
        """Enrolls with ``hub``: None, else why it did not."""
        try:
            response = await client.post(enroll_url(hub), json=body)
        except httpx.HTTPError as exc:
            return f"not reachable ({exc or type(exc).__name__})"
        if response.is_success:
            return None
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        return f"{response.status_code} {detail or response.reason_phrase}"

    results = await asyncio.gather(*(one(hub) for hub in settings.hubs))
    return dict(zip(settings.hubs, results, strict=True))


@contextlib.asynccontextmanager
async def enrolling(settings: DaemonSettings) -> AsyncGenerator[None]:
    """Enrolls with the hubs of ``settings`` (`enroll_forever`) for as long as the context lasts; nothing without
    hubs. The daemon's app holds it while it serves."""
    task = asyncio.ensure_future(enroll_forever(settings)) if settings.hubs else None
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def enroll_forever(settings: DaemonSettings, client: httpx.AsyncClient | None = None) -> None:
    """Enrolls with every hub now, then every ``enroll_interval`` seconds (sooner after a failure); logs changes."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    last: dict[str, str | None] = {}
    try:
        while True:
            outcome = await enroll_once(settings, client)
            for hub, error in outcome.items():
                if hub in last and last[hub] == error:
                    continue
                if error is None:
                    logger.info("Enrolled with the hub %s as %s", hub, enroll_body(settings)["id"])
                else:
                    logger.warning("Could not enroll with the hub %s: %s", hub, error)
            last = outcome
            failed = any(error is not None for error in outcome.values())
            await asyncio.sleep(min(settings.enroll_interval, RETRY_INTERVAL) if failed else settings.enroll_interval)
    finally:
        if own:
            await client.aclose()
