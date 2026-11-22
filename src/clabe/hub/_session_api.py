"""The hub's session routes: every run the hub saw on its rigs, filled in as it went."""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ._fleet import Fleet
from ._sessions import SessionPage, SessionSort, SessionStore


class SessionDeleteBody(BaseModel):
    """Sessions to delete, by key (``rig_id/run_id``)."""

    keys: list[str]


class SessionDeleteResult(BaseModel):
    """How many sessions were deleted."""

    deleted: int


def session_routes(api: APIRouter, fleet: Fleet) -> None:
    """Adds the session routes to the hub's API (they answer 503 when the hub keeps no sessions)."""

    def store() -> SessionStore:
        """The hub's session store; 503 without one."""
        if fleet.sessions is None:
            raise HTTPException(status_code=503, detail="This hub does not keep sessions.")
        return fleet.sessions

    @api.get("/sessions", response_model=SessionPage)
    def sessions(
        rig: Annotated[str | None, Query(description="Only on this rig")] = None,
        experiment: Annotated[str | None, Query(description="Only of this experiment")] = None,
        status: Annotated[
            str | None,
            Query(description="Only in this status (running, completed, aborted, failed, ended; live: not finished)"),
        ] = None,
        q: Annotated[str | None, Query(description="Text found (any case) in any field")] = None,
        sort: SessionSort = "started_at",
        order: Literal["asc", "desc"] = "desc",
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> SessionPage:
        """The sessions matching the filters, sorted (newest first by default), a page at a time."""
        return store().query(
            rig=rig, experiment=experiment, status=status, q=q, sort=sort, order=order, limit=limit, offset=offset
        )

    @api.get("/sessions/experiments", response_model=list[str])
    def experiments() -> list[str]:
        """The experiments the sessions are of (for a filter)."""
        return store().experiments()

    @api.post("/sessions/delete", response_model=SessionDeleteResult)
    def delete(body: SessionDeleteBody) -> SessionDeleteResult:
        """Deletes sessions from the hub's list (by ``rig_id/run_id``); unknown keys are skipped."""
        return SessionDeleteResult(deleted=store().delete(body.keys))
