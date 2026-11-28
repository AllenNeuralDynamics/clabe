"""The hub's issue-report routes: browse, triage, download and delete collected reports; open an export for a look."""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as FastApiPath
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from ..reports import REPORT_ID_PATTERN
from ._fleet import Fleet
from ._reports import (
    MAX_INSPECT_SIZE,
    FileTooLarge,
    InspectCache,
    InspectResult,
    ReportPage,
    ReportSort,
    ReportStatus,
    ReportStore,
    StoredReport,
    ZipEntry,
    zip_entries,
    zip_text,
)

logger = logging.getLogger(__name__)


class StatusBody(BaseModel):
    """A report's new status."""

    status: ReportStatus


class DeleteBody(BaseModel):
    """Reports to delete."""

    ids: list[str] = Field(description="Their ids")


class DeleteResult(BaseModel):
    """How many reports were deleted."""

    deleted: int


_ReportId = Annotated[str, FastApiPath(pattern=REPORT_ID_PATTERN, description="The report's id (32 hex digits)")]
_InspectId = Annotated[str, FastApiPath(pattern=REPORT_ID_PATTERN, description="The id the upload was given")]
_TEXT = {200: {"content": {"text/plain": {}}, "description": "The file's text (UTF-8)"}}
_ZIP = {200: {"content": {"application/zip": {}}, "description": "The report's zip"}}


def _file_text(path: Path, name: str) -> PlainTextResponse:
    """The text of file ``name`` in the zip at ``path``: 404 if it is not an entry, 413 if too large."""
    try:
        return PlainTextResponse(zip_text(path, name))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"No file {name!r} in this report.") from exc
    except FileTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc


def report_routes(api: APIRouter, fleet: Fleet, inspections: InspectCache) -> None:
    """Adds the report routes to the hub's API (they answer 503 when the hub stores no reports)."""

    def store() -> ReportStore:
        """The hub's report store; 503 without one."""
        if fleet.reports is None:
            raise HTTPException(status_code=503, detail="This hub does not store reports.")
        return fleet.reports

    def stored_zip(report_id: str) -> Path:
        """The zip of a stored report; 404 if unknown."""
        path = store().zip_path(report_id)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail=f"No report {report_id}.")
        return path

    @api.get("/reports", response_model=ReportPage)
    def reports(
        rig: Annotated[str | None, Query(description="Only from this rig")] = None,
        experiment: Annotated[str | None, Query(description="Only of this experiment")] = None,
        status: Annotated[ReportStatus | None, Query(description="Only in this status")] = None,
        q: Annotated[str | None, Query(description="Text found (any case) in any field")] = None,
        sort: ReportSort = "created_at",
        order: Literal["asc", "desc"] = "desc",
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> ReportPage:
        """The collected reports matching the filters, sorted, a page at a time (``total`` counts every match)."""
        return store().query(
            rig=rig, experiment=experiment, status=status, q=q, sort=sort, order=order, limit=limit, offset=offset
        )

    @api.get("/reports/{report_id}", response_model=StoredReport)
    def report(report_id: _ReportId) -> StoredReport:
        """One collected report."""
        found = store().get(report_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"No report {report_id}.")
        return found

    @api.patch("/reports/{report_id}", response_model=StoredReport)
    def set_status(report_id: _ReportId, body: StatusBody) -> StoredReport:
        """Moves a report to ``new``, ``looking`` or ``closed``."""
        found = store().set_status(report_id, body.status)
        if found is None:
            raise HTTPException(status_code=404, detail=f"No report {report_id}.")
        return found

    @api.delete("/reports/{report_id}", status_code=204)
    def delete(report_id: _ReportId) -> None:
        """Deletes a report (its row and its zip)."""
        if not store().delete([report_id]):
            raise HTTPException(status_code=404, detail=f"No report {report_id}.")
        logger.info("Deleted report %s", report_id)

    @api.post("/reports/delete", response_model=DeleteResult)
    def delete_many(body: DeleteBody) -> DeleteResult:
        """Deletes several reports; unknown ids are skipped."""
        deleted = store().delete(body.ids)
        logger.info("Deleted %s report(s)", deleted)
        return DeleteResult(deleted=deleted)

    _zip_routes(api, store, stored_zip)
    _inspect_routes(api, inspections)


def _zip_routes(api: APIRouter, store: Callable[[], ReportStore], stored_zip: Callable[[str], Path]) -> None:
    """The routes downloading a stored report's zip and reading its files."""

    @api.get("/reports/{report_id}/zip", response_class=FileResponse, responses=_ZIP)
    def download(report_id: _ReportId) -> FileResponse:
        """A report's zip, as collected."""
        found = store().get(report_id)
        name = f"report-{found.rig_id}-{report_id}.zip" if found else f"report-{report_id}.zip"
        return FileResponse(stored_zip(report_id), media_type="application/zip", filename=name)

    @api.get("/reports/{report_id}/files", response_model=list[ZipEntry])
    def files(report_id: _ReportId) -> list[ZipEntry]:
        """The files of a report's zip."""
        return zip_entries(stored_zip(report_id))

    @api.get("/reports/{report_id}/files/{path:path}", response_class=PlainTextResponse, responses=_TEXT)
    def file(report_id: _ReportId, path: str) -> PlainTextResponse:
        """A file of a report's zip, as text; 413 beyond 20 MB."""
        return _file_text(stored_zip(report_id), path)


def _inspect_routes(api: APIRouter, inspections: InspectCache) -> None:
    """The routes opening an export for a look, without storing it."""

    @api.post(
        "/inspect",
        response_model=InspectResult,
        openapi_extra={
            "requestBody": {
                "required": True,
                "description": "The zip: the raw body, or a multipart form's ``file``",
                "content": {
                    "application/zip": {"schema": {"type": "string", "format": "binary"}},
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "properties": {"file": {"type": "string", "format": "binary"}},
                            "required": ["file"],
                        }
                    },
                },
            }
        },
    )
    async def inspect(request: Request) -> InspectResult:
        """Opens an export (or a report's zip) for a look, without storing it; kept for an hour after last use."""
        data = await _upload(request)
        try:
            return await asyncio.to_thread(inspections.add, data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.get("/inspect/{inspect_id}/files/{path:path}", response_class=PlainTextResponse, responses=_TEXT)
    def inspect_file(inspect_id: _InspectId, path: str) -> PlainTextResponse:
        """A file of an opened export, as text; 413 beyond 20 MB."""
        zipped = inspections.path(inspect_id)
        if zipped is None:
            raise HTTPException(status_code=404, detail="This export is no longer open: open it again.")
        return _file_text(zipped, path)


async def _upload(request: Request) -> bytes:
    """The zip sent: a multipart form's ``file`` (or its first file), else the raw body; 413 if too large."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_INSPECT_SIZE:
        raise HTTPException(status_code=413, detail=f"Exports over {MAX_INSPECT_SIZE // 2**20} MB are not opened.")
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        try:
            form = await request.form(max_part_size=MAX_INSPECT_SIZE)
        except AssertionError as exc:  # python-multipart is not installed
            raise HTTPException(status_code=415, detail="Send the zip as the request's body.") from exc
        upload = form.get("file") or next((v for v in form.values() if not isinstance(v, str)), None)
        if upload is None or isinstance(upload, str):
            raise HTTPException(status_code=400, detail="No file in the form.")
        return await upload.read()
    data = await request.body()
    if len(data) > MAX_INSPECT_SIZE:
        raise HTTPException(status_code=413, detail=f"Exports over {MAX_INSPECT_SIZE // 2**20} MB are not opened.")
    return data
