"""The daemon's HTTP app: its own API under /api/daemon, the run it hosts proxied under /run/, and the web UI."""

import hmac
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from .. import __version__
from ..server._app import STATIC_DIR
from ..workflow import ExperimentListing
from ._host import Host, HostError, HostInfo, LaunchBody, RepositoryInfo, RunInfo

#: Hop-by-hop headers, which a proxy must not forward.
_HOP_HEADERS = frozenset(
    {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
     "transfer-encoding", "upgrade", "host", "content-length", "authorization"}
)  # fmt: skip


def _given_token(authorization: str | None, token_param: str | None) -> str | None:
    """The token a request carries, from ``Authorization: Bearer`` or ``?token=``."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:]
    return token_param


def _token_check(token: str | None):
    """A dependency rejecting requests without the daemon's token (when one is set)."""

    async def check(
        authorization: str | None = Header(default=None),
        token_param: str | None = Query(default=None, alias="token"),
    ) -> None:
        """Raises 401 unless the request carries the token."""
        if token is None:
            return
        given = _given_token(authorization, token_param)
        if given is None or not hmac.compare_digest(given, token):
            raise HTTPException(status_code=401, detail="A valid token is required.")

    return check


def create_daemon_app(host: Host, *, token: str | None = None, static_dir: Path | None = STATIC_DIR) -> FastAPI:
    """Builds the daemon's app.

    Args:
        host: The host role (repositories and runs).
        token: When set, every API call (the daemon's and the proxied run's) must carry it.
        static_dir: The web UI, served at ``/`` (the daemon's page) and ``/run/`` (the run's page).
    """
    app = FastAPI(
        title="clabe daemon API",
        version=__version__,
        description="Run experiments on this machine: list repositories and experiments, launch one, and reach the "
        "run's own API under /run/api.",
    )
    check = _token_check(token)
    api = APIRouter(prefix="/api/daemon", dependencies=[Depends(check)])

    def guarded(call):
        try:
            return call()
        except HostError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    @api.get("/info", response_model=HostInfo)
    def info() -> HostInfo:
        """The machine, its clabe version, and whether it is running an experiment."""
        return host.info()

    @api.get("/repositories", response_model=list[RepositoryInfo])
    def repositories() -> list[RepositoryInfo]:
        """The experiment repositories this machine offers."""
        return host.repositories()

    @api.get("/repositories/{name}/experiments", response_model=ExperimentListing)
    def experiments(name: str) -> ExperimentListing:
        """The experiments of a repository (listed in its own environment; may take a while the first time)."""
        return guarded(lambda: host.experiments(name))

    @api.post("/runs", response_model=RunInfo)
    def launch(body: LaunchBody) -> RunInfo:
        """Starts an experiment; 409 while another one runs. Follow it under /run/."""
        return guarded(lambda: host.launch(body))

    @api.get("/runs/current", response_model=RunInfo | None)
    def current() -> RunInfo | None:
        """The run launched last (running or not), with the end of its output."""
        launched = host.current()
        return launched.info() if launched is not None else None

    @api.post("/runs/current/stop", response_model=RunInfo)
    def stop() -> RunInfo:
        """Stops the current run's process. Prefer aborting it (POST /run/api/abort): that lets it clean up."""
        return guarded(host.stop)

    app.include_router(api)
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))

    @app.get("/run", include_in_schema=False)
    def run_root(request: Request) -> RedirectResponse:
        """The run's page needs the trailing slash (its links are relative)."""
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/run/{query}")

    @app.api_route("/run/{path:path}", methods=["GET", "POST"], include_in_schema=False)
    async def proxy(path: str, request: Request) -> Response:
        """The current run's server: its web UI and (with the daemon's token) its API."""
        if path.startswith("api/"):
            await check(request.headers.get("authorization"), request.query_params.get("token"))
        return await _proxy(host, client, path, request)

    if static_dir is not None and (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")
    else:

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def placeholder() -> str:
            """A page pointing at the API when the web UI is not built."""
            return _PLACEHOLDER

    return app


async def _proxy(host: Host, client: httpx.AsyncClient, path: str, request: Request) -> Response:
    """Forwards ``request`` to the current run's server, with the run's own token; streams the answer back."""
    launched = host.current()
    port = launched.poll_port() if launched is not None and launched.process.poll() is None else None
    if launched is None or port is None:
        if path in ("", "index.html"):
            return HTMLResponse(_NO_RUN, status_code=503)
        raise HTTPException(status_code=503, detail="No run is being served on this machine.")
    query = [(k, v) for k, v in request.query_params.multi_items() if k != "token"]
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_HEADERS}
    headers["authorization"] = f"Bearer {launched.token}"
    upstream = client.build_request(
        request.method, f"http://127.0.0.1:{port}/{path}", params=query, headers=headers, content=await request.body()
    )
    try:
        response = await client.send(upstream, stream=True)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"The run is not reachable: {exc}") from exc
    return StreamingResponse(
        response.aiter_raw(),
        status_code=response.status_code,
        headers={k: v for k, v in response.headers.items() if k.lower() not in _HOP_HEADERS},
        background=BackgroundTask(response.aclose),
    )


_PLACEHOLDER = """<!doctype html><html><head><meta charset="utf-8"><title>clabe daemon</title></head>
<body style="font-family: sans-serif"><h1>clabe daemon</h1>
<p>The web UI is not part of this build. The API is at <a href="/docs">/docs</a>; a run is served under
<a href="/run/">/run/</a>.</p></body></html>"""

_NO_RUN = """<!doctype html><html><head><meta charset="utf-8"><title>clabe</title></head>
<body style="font-family: sans-serif"><h1>No run</h1>
<p>Nothing is running on this machine. <a href="../">Start an experiment</a>.</p></body></html>"""
