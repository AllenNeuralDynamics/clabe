"""
Shared models for RPC client and server communication.

This module contains Pydantic models used for data exchange between
the RPC client and server, ensuring consistent data structures and validation.
"""

from enum import Enum
from typing import Optional

from pydantic import Base64Bytes, BaseModel, Field


class JobStatus(str, Enum):
    """Enumeration of possible job statuses."""

    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class JobResult(BaseModel):
    """Represents the result of a command execution."""

    job_id: str = Field(description="Unique identifier for the job")
    status: JobStatus = Field(description="Job status")
    stdout: Optional[str] = Field(default=None, description="Standard output from the command")
    stderr: Optional[str] = Field(default=None, description="Standard error from the command")
    returncode: Optional[int] = Field(default=None, description="Exit code of the command")
    error: Optional[str] = Field(default=None, description="Error message if command failed")


class FileInfo(BaseModel):
    """Represents information about a file on the server."""

    name: str = Field(description="Name of the file")
    size: int = Field(description="Size of the file in bytes")
    modified: float = Field(description="Last modified time as Unix timestamp")
    path: str = Field(description="Full path of the file on the server")


class RpcResponse(BaseModel):
    """Base response model for RPC operations."""

    success: bool = Field(description="Whether the operation was successful")
    error: Optional[str] = Field(default=None, description="Error message if operation failed")


class JobSubmissionResponse(RpcResponse):
    """Response model for job submission."""

    job_id: Optional[str] = Field(default=None, description="Unique identifier for the submitted job")


class JobStatusResponse(RpcResponse):
    """Response model for job status queries."""

    job_id: str = Field(description="Job identifier")
    status: JobStatus = Field(description="Current job status")
    result: Optional[dict] = Field(default=None, description="Job result if completed")


class JobListResponse(RpcResponse):
    """Response model for listing jobs."""

    running: list[str] = Field(default_factory=list, description="List of running job IDs")
    finished: list[str] = Field(default_factory=list, description="List of finished job IDs")


class FileUploadResponse(RpcResponse):
    """Response model for file upload operations."""

    filename: Optional[str] = Field(default=None, description="Name of the uploaded file")
    size: Optional[int] = Field(default=None, description="Size of the uploaded file in bytes")
    overwritten: bool = Field(default=False, description="Whether an existing file was overwritten")
    path: Optional[str] = Field(default=None, description="Full path of the uploaded file on the server")


class FileDownloadResponse(RpcResponse):
    """Response model for file download operations."""

    filename: Optional[str] = Field(default=None, description="Name of the downloaded file")
    size: Optional[int] = Field(default=None, description="Size of the downloaded file in bytes")
    data: Optional[Base64Bytes] = Field(default=None, description="Base64-encoded file content")


class FileListResponse(RpcResponse):
    """Response model for file listing operations."""

    files: list[FileInfo] = Field(default_factory=list, description="List of files on the server")
    count: int = Field(default=0, description="Total number of files")


class FileDeleteResponse(RpcResponse):
    """Response model for file deletion operations."""

    filename: Optional[str] = Field(default=None, description="Name of the deleted file")


class FileBulkDeleteResponse(RpcResponse):
    """Response model for bulk file deletion operations."""

    deleted_count: int = Field(default=0, description="Number of files deleted")
    deleted_files: list[str] = Field(default_factory=list, description="List of deleted file names")
