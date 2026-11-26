"""Run events: the append-only, sequenced record of a [`Run`][clabe.workflow.Run].

Every state change of a run is one of the event models below, appended to the run's `EventLog`
(``run.events``) and mirrored to ``events.jsonl`` when the run keeps records. `Event` is the discriminated
union of all of them (on ``type``); `EventLog.read` loads a persisted log back.
"""

from ._events import (
    ActivityEnded,
    ActivityOutput,
    ActivityStarted,
    ArtifactExported,
    BranchEnded,
    BranchStarted,
    CommandFinished,
    ContextChanged,
    Event,
    EventLog,
    Header,
    Notify,
    PromptClosed,
    PromptOpened,
    RecordResolved,
    RunClosed,
    RunClosing,
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
    "CommandFinished",
    "ContextChanged",
    "Event",
    "EventLog",
    "Header",
    "Notify",
    "PromptClosed",
    "PromptOpened",
    "RecordResolved",
    "RunClosed",
    "RunClosing",
    "RunFinished",
    "RunStarted",
    "StepFailed",
    "StepFinished",
    "StepSkipped",
    "StepStarted",
    "StepsInvalidated",
]
