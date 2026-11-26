"""Recording the other machines a run uses in its manifest (``run.json``: ``workers``)."""

import datetime
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..workflow._records import ACTIVE_STEP

if TYPE_CHECKING:
    from ..launcher._manifest import WorkerRecord
    from ._client import WorkerClient

logger = logging.getLogger(__name__)


def record_worker(client: "WorkerClient", change: "Callable[[WorkerRecord], None] | None" = None) -> None:
    """Adds ``client``'s machine to the running launcher's manifest (once), then applies ``change`` to its record.

    Does nothing outside a run, or when the run has no launcher. Never raises: provenance must not fail a step.
    """
    context = ACTIVE_STEP.get()
    launcher = getattr(context.run, "launcher", None) if context is not None else None
    if launcher is None or not hasattr(launcher, "update_manifest"):
        return
    try:
        from ..launcher._manifest import WorkerRecord

        info = client.info()

        def apply(manifest) -> None:
            record = next((w for w in manifest.workers if w.url == client.base_url), None)
            if record is None:
                record = WorkerRecord(
                    url=client.base_url,
                    host=info.hostname,
                    clabe_version=info.clabe_version,
                    os_name=info.os_name,
                    repository=info.repository,
                    working_dir=info.working_dir,
                )
                manifest.workers.append(record)
            if change is not None:
                change(record)

        launcher.update_manifest(apply)
    except Exception:
        logger.warning("Could not record %s in the run manifest", client.base_url, exc_info=True)


def record_job(client: "WorkerClient", job_id: str, command: list[str]) -> None:
    """Adds a command run on ``client``'s machine to its record in the manifest."""
    from ..launcher._manifest import RemoteJobRecord

    context = ACTIVE_STEP.get()
    job = RemoteJobRecord(
        job_id=job_id,
        command=command,
        step=context.step.name if context is not None else None,
        attempt=context.attempt if context is not None else None,
        submitted_at=datetime.datetime.now(datetime.UTC),
    )
    record_worker(client, lambda record: record.jobs.append(job))
