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
from pydantic import BaseModel, Field

from .. import __version__
from ..server._app import STATIC_DIR
from ..server._proxy import RequireToken, TokenCookies, forward, token_check
from ._fleet import EnrollBody, Excluded, Fleet, RigStatus
from ._registry import Exclusion, RigConfig
from ._report_api import report_routes
from ._reports import InspectCache

logger = logging.getLogger(__name__)


class ExcludeBody(BaseModel):
    """An exclusion to add."""

    pattern: str = Field(
        description="A glob matched without case against a rig's name and host, e.g. BEH.* or rig-3", min_length=1
    )
    comment: str | None = Field(default=None, description="Why it is excluded")


class TokenBody(BaseModel):
    """A rig's own daemon token, or null to clear it."""

    token: str | None = Field(description="The rig's DAEMON_CLABE_TOKEN; null or empty deletes it")


class ExcludeResult(BaseModel):
    """The exclusion added, and the rigs it removed from the hub."""

    exclusion: Exclusion
    removed: list[str]


def _enroll_routes(app: FastAPI, fleet: Fleet) -> None:
    """``POST /api/hub/enroll``, open to any rig: what a rig's daemon calls. A rig that enrolls under a known name
    from another address is held until its move is confirmed (`Fleet.enroll`)."""
    enroll = APIRouter(prefix="/api/hub")

    @enroll.post("/enroll", response_model=RigConfig, responses={403: {"description": "The rig is excluded"}})
    def enroll_rig(body: EnrollBody) -> RigConfig:
        """A rig's daemon enrolls (again): the hub follows it at ``http://<host>:<port>``. A known rig enrolling from
        another address is held (``moved_from``) until the move is confirmed."""
        try:
            return fleet.enroll(body)
        except Excluded as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    app.include_router(enroll)


def _rig_routes(api: APIRouter, fleet: Fleet) -> None:
    """Removing rigs, confirming their moves, their tokens, and the exclusions that keep rigs out."""

    @api.delete("/rigs/{rig_id}", status_code=204)
    def remove(rig_id: str) -> None:
        """Removes a rig and its token. A rig whose daemon still enrolls with this hub comes back; exclude it to keep it
        out."""
        if not fleet.remove(rig_id):
            raise HTTPException(status_code=404, detail=f"No rig {rig_id!r}.")

    @api.post("/rigs/{rig_id}/confirm", status_code=204)
    def confirm(rig_id: str) -> None:
        """Confirms that a rig really moved to the address it enrolled from: the hub follows it there again."""
        if not fleet.confirm(rig_id):
            raise HTTPException(status_code=404, detail=f"No rig {rig_id!r}.")

    @api.put("/rigs/{rig_id}/token", status_code=204)
    def set_token(rig_id: str, body: TokenBody) -> None:
        """Enters the rig's daemon token (its DAEMON_CLABE_TOKEN), which the hub then presents to it; ``null``
        deletes it. It is never shown back."""
        token = (body.token or "").strip() or None
        if not fleet.set_token(rig_id, token):
            raise HTTPException(status_code=404, detail=f"No rig {rig_id!r}.")

    @api.get("/exclusions", response_model=list[Exclusion])
    def exclusions() -> list[Exclusion]:
        """The patterns that keep rigs out, oldest first."""
        return fleet.registry.exclusions()

    @api.post("/exclusions", response_model=ExcludeResult)
    def exclude(body: ExcludeBody) -> ExcludeResult:
        """Keeps out the rigs matching a pattern: those followed now are removed, and they cannot enroll."""
        exclusion, removed = fleet.exclude(body.pattern.strip(), (body.comment or "").strip() or None)
        return ExcludeResult(exclusion=exclusion, removed=removed)

    @api.delete("/exclusions", status_code=204)
    def unexclude(pattern: str) -> None:
        """Removes an exclusion: the rigs it kept out are followed again once they enroll (within minutes)."""
        if not fleet.registry.unexclude(pattern):
            raise HTTPException(status_code=404, detail=f"No exclusion {pattern!r}.")


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
        "under /rigs/<id>/. Rigs enroll themselves (POST /api/hub/enroll).",
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
    _rig_routes(api, fleet)
    _enroll_routes(app, fleet)
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
        if rig.moved_from is not None:
            raise HTTPException(
                status_code=409,
                detail=f"{rig_id} enrolled from {rig.url}, was {rig.moved_from}: confirm the move on the hub's page",
            )
        token = fleet.rig_token(rig_id)
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
