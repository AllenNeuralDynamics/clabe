"""Request and response bodies of the run API (the single source of truth for the OpenAPI document)."""

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
