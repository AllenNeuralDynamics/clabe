"""Request and response bodies of the run API (the single source of truth for the OpenAPI document)."""

import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AnswerBody(BaseModel):
    """An answer to an open prompt."""

    value: Any = Field(default=None, description="The answer, as described by the prompt's answer schema.")
    client: str = Field(default="remote", description="Who answers; recorded in the prompt's close event.")


class ClientBody(BaseModel):
    """Identifies who sends a command."""

    client: str = Field(default="remote", description="Who sends the command.")


class AbortBody(BaseModel):
    """Aborts the run."""

    reason: str = Field(default="Aborted by user.", description="Recorded as the run's reason.")


class ReportBody(BaseModel):
    """Reports an issue with the run (saved on this machine for the hub to collect)."""

    comment: str = Field(default="", description="What went wrong, in the user's words.")
    client: str = Field(default="remote", description="Who reports; recorded as the report's ``reported_from``.")


class LogLine(BaseModel):
    """A Python log record of the run's process (info and above), as the web UI's Log pane shows it."""

    id: int = Field(description="Increasing number of the line; follow the stream after it.")
    ts: datetime.datetime = Field(description="When it was logged (UTC).")
    level: str = Field(description="debug, info, warning, error or critical.")
    logger: str = Field(description="The logger's name (e.g. ``clabe.launcher``).")
    message: str = Field(description="The message, with the traceback when an exception was logged.")


class CommandResult(BaseModel):
    """Outcome of an answer or a command: accepted, or rejected with a reason to show the user."""

    ok: bool
    reason: str | None = None


class ServerInfo(BaseModel):
    """Identity of the server and the run it serves."""

    clabe_version: str
    hostname: str
    run_id: str
    workflow: str
    status: str


class SessionInfo(BaseModel):
    """What a run is about, as far as known so far: the hub lists it as a session."""

    run_id: str
    experiment: str
    status: str = Field(description="running, completed, aborted or failed")
    current_step: str | None = None
    waiting: bool = Field(default=False, description="Whether a prompt waits for an answer")
    steps_done: int = 0
    steps_total: int = 0
    started_at: datetime.datetime | None = None
    finished_at: datetime.datetime | None = None
    reason: str | None = Field(default=None, description="Why it ended, when it did not complete")
    hostname: str
    clabe_version: str
    rig: str | None = Field(default=None, description="The rig's name, once known")
    session_id: str | None = Field(default=None, description="The session's name, once registered")
    subject: str | None = None
    experimenter: list[str] = Field(default_factory=list)
    repository: str | None = None
    commit: str | None = None


class ListDirBody(BaseModel):
    """Lists a directory of the machine running the run (the remote path picker)."""

    path: str | None = Field(default=None, description="Directory to list; the working directory when omitted.")
    kind: Literal["file", "dir", "any"] = "any"
    extensions: list[str] | None = Field(default=None, description="Only list files with these suffixes.")


class DirEntry(BaseModel):
    """One entry of a listed directory."""

    name: str
    path: str
    is_dir: bool


class DirListing(BaseModel):
    """A listed directory."""

    path: str
    parent: str | None
    entries: list[DirEntry]
