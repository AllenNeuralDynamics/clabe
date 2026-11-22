"""The hub's HTTP app: the fleet and its inbox under /api/hub, each rig's daemon under /rigs/<id>/, an MCP server
at /mcp, and the web UI."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..server._app import STATIC_DIR
from ..server._proxy import RequireToken, TokenCookies, forward, token_check
from ._fleet import Fleet, RigStatus, rig_token
from ._report_api import report_routes
from ._reports import InspectCache

logger = logging.getLogger(__name__)


def create_hub_app(
    fleet: Fleet,
    *,
    token: str | None = None,
    static_dir: Path | None = STATIC_DIR,
    poll: bool = True,
    self_url: str | None = None,
) -> FastAPI:
    """Builds the hub's app.

    Args:
        fleet: The rigs to follow.
        token: When set, every API call (the hub's and each rig's) must carry it.
        static_dir: The web UI, served at ``/`` (the fleet) and under each ``/rigs/<id>/``.
        poll: Whether to poll the rigs in the background while the app runs.
        self_url: Where the hub reaches itself (``http://127.0.0.1:8080``); with it (and the ``agents`` extra), an
            MCP server is served at ``/mcp`` (streamable HTTP), acting on every rig through the hub.
    """

    inspections = InspectCache()
    mcp = _mcp_server(self_url, token)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Polls the rigs (and runs the MCP sessions) while the app serves."""
        if poll:
            fleet.start()
        async with mcp.session_manager.run() if mcp is not None else contextlib.nullcontext():
            yield
        await fleet.stop()
        inspections.close()

    app = FastAPI(
        title="clabe hub API",
        version=__version__,
        description="Follow a fleet of rigs: what each runs, which ones wait for an answer, and each rig's own API "
        "under /rigs/<id>/.",
        lifespan=lifespan,
    )
    app.add_middleware(TokenCookies, token=token)
    app.add_middleware(RequireToken, token=token, prefix="/mcp")
    check = token_check(token)
    api = APIRouter(prefix="/api/hub", dependencies=[Depends(check)])

    @api.get("/rigs", response_model=list[RigStatus])
    def rigs() -> list[RigStatus]:
        """Every rig, as last polled."""
        return fleet.statuses()

    @api.get("/inbox", response_model=list[RigStatus])
    def inbox() -> list[RigStatus]:
        """The rigs whose run waits for an answer, longest-waiting first."""
        return fleet.statuses(fleet.inbox())

    @api.post("/refresh", response_model=list[RigStatus])
    async def refresh() -> list[RigStatus]:
        """Polls every rig now (and collects their reports)."""
        await fleet.poll_once(collect=True)
        return await asyncio.to_thread(fleet.statuses)

    report_routes(api, fleet, inspections)
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
            await check(request)
        token = rig_token(rig)
        return await forward(
            client, request, f"{rig.url.rstrip('/')}/{path}", token, unreachable=f"{rig_id} is not reachable"
        )

    if mcp is not None:
        app.router.routes.extend(mcp.streamable_http_app(streamable_http_path="/mcp", host="0.0.0.0").routes)

    if static_dir is not None and (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")
    else:

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def placeholder() -> str:
            """A page pointing at the API when the web UI is not built."""
            return '<!doctype html><h1>clabe hub</h1><p>The API is at <a href="/docs">/docs</a>.</p>'

    return app


def _mcp_server(self_url: str | None, token: str | None):
    """The MCP server of the hub (its tools reach the rigs through the hub itself), or None without the ``agents``
    extra or a ``self_url``."""
    if self_url is None:
        return None
    try:
        from ..agents import ClabeTarget, create_mcp_server

        return create_mcp_server(ClabeTarget(self_url, token, kind="hub"))
    except ImportError:
        logger.info("No MCP server at /mcp: the hub was installed without the agents extra.")
        return None
