"""Run events: the append-only, sequenced record of a :class:`~clabe.workflow.Run`.

Every state change of a run is one of the event models below, appended to the run's :class:`EventLog`
(``run.events``) and mirrored to ``events.jsonl`` when the run keeps records. :data:`Event` is the discriminated
union of all of them (on ``type``); :meth:`EventLog.read` loads a persisted log back.
"""

from ._events import (
    ActivityEnded,
    ActivityOutput,
    ActivityStarted,
    ArtifactExported,
    BranchEnded,
    BranchStarted,
    Event,
    EventLog,
    Header,
    Notify,
    PromptClosed,
    PromptOpened,
    RunFinished,
    RunStarted,
    StepFailed,
    StepFinished,
    StepsInvalidated,
    StepSkipped,
    StepStarted,
)

__all__ = [
    "ActivityEnded",
    "ActivityOutput",
    "ActivityStarted",
    "ArtifactExported",
    "BranchEnded",
    "BranchStarted",
    "Event",
    "EventLog",
    "Header",
    "Notify",
    "PromptClosed",
    "PromptOpened",
    "RunFinished",
    "RunStarted",
    "StepFailed",
    "StepFinished",
    "StepSkipped",
    "StepStarted",
    "StepsInvalidated",
]
