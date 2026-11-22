"""The hub's HTTP app: the fleet and its inbox under /api/hub, each rig's daemon under /rigs/<id>/, and the web UI."""

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..server._app import STATIC_DIR
from ..server._proxy import forward, token_check
from ._fleet import Fleet, RigStatus, rig_token


def create_hub_app(
    fleet: Fleet, *, token: str | None = None, static_dir: Path | None = STATIC_DIR, poll: bool = True
) -> FastAPI:
    """Builds the hub's app.

    Args:
        fleet: The rigs to follow.
        token: When set, every API call (the hub's and each rig's) must carry it.
        static_dir: The web UI, served at ``/`` (the fleet) and under each ``/rigs/<id>/``.
        poll: Whether to poll the rigs in the background while the app runs.
    """

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if poll:
            fleet.start()
        yield
        await fleet.stop()

    app = FastAPI(
        title="clabe hub API",
        version=__version__,
        description="Follow a fleet of rigs: what each runs, which ones wait for an answer, and each rig's own API "
        "under /rigs/<id>/.",
        lifespan=lifespan,
    )
    check = token_check(token)
    api = APIRouter(prefix="/api/hub", dependencies=[Depends(check)])

    @api.get("/rigs", response_model=list[RigStatus])
    def rigs() -> list[RigStatus]:
        """Every rig, as last polled."""
        return list(fleet.status.values())

    @api.get("/inbox", response_model=list[RigStatus])
    def inbox() -> list[RigStatus]:
        """The rigs whose run waits for an answer, longest-waiting first."""
        return fleet.inbox()

    @api.post("/refresh", response_model=list[RigStatus])
    async def refresh() -> list[RigStatus]:
        """Polls every rig now."""
        await fleet.poll_once()
        return list(fleet.status.values())

    app.include_router(api)
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))

    @app.get("/rigs/{rig_id}", include_in_schema=False)
    def rig_root(rig_id: str, request: Request) -> RedirectResponse:
        """A rig's page needs the trailing slash (its links are relative)."""
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/rigs/{rig_id}/{query}")

    @app.api_route("/rigs/{rig_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"], include_in_schema=False)
    async def proxy(rig_id: str, path: str, request: Request) -> Response:
        """A rig's daemon: its page, its run (``run/``) and their APIs, with the hub's token."""
        rig = fleet.rig(rig_id)
        if rig is None:
            raise HTTPException(status_code=404, detail=f"No rig {rig_id!r}.")
        if "api/" in path:
            await check(request.headers.get("authorization"), request.query_params.get("token"))
        token = rig_token(rig)
        return await forward(
            client, request, f"{rig.url.rstrip('/')}/{path}", token, unreachable=f"{rig_id} is not reachable"
        )

    if static_dir is not None and (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")
    else:

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def placeholder() -> str:
            """A page pointing at the API when the web UI is not built."""
            return '<!doctype html><h1>clabe hub</h1><p>The API is at <a href="/docs">/docs</a>.</p>'

    return app
