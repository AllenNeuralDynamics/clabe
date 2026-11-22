"""Recording which store records a run used (``record_resolved`` events, and the records as artifacts)."""

import hashlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._base import Candidate, Kind

logger = logging.getLogger(__name__)


def record_resolution(store: Any, kind: "Kind[Any]", candidate: "Candidate[Any]", value: Any, scope: dict) -> None:
    """Inside a running step, records the resolved record and saves it as an artifact; never raises."""
    from ..workflow._records import ACTIVE_STEP

    context = ACTIVE_STEP.get()
    if context is None:
        return
    try:
        from ..workflow import export_artifact
        from ..workflow.events import RecordResolved

        content = kind.adapter.dump_json(value, indent=2)
        artifact = None
        if context.run.record_dir is not None:  # a run that keeps records keeps the record too
            artifact = export_artifact(content, f"stores/{kind.name}.json").relative_to(context.run.record_dir)
        context.run.events.append(
            RecordResolved(
                step=context.step.name,
                attempt=context.attempt,
                kind=kind.name,
                store=str(store),
                label=candidate.label,
                scope={k: str(v) for k, v in scope.items()},
                sha256=hashlib.sha256(content).hexdigest(),
                revision=candidate.revision,
                artifact=artifact.as_posix() if artifact is not None else None,
            )
        )
    except Exception:
        logger.warning("Could not record the %s resolved from %s", kind.name, store, exc_info=True)
