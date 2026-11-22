"""The daemon's HTTP app: its own API under /api/daemon, the run it hosts proxied under /run/, and the web UI."""

from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..server._app import STATIC_DIR
from ..server._proxy import forward, token_check
from ..workflow import ExperimentListing
from ._host import Host, HostError, HostInfo, LaunchBody, RepositoryInfo, RunInfo
from ._settings import DaemonSettings
from ._worker import Worker, worker_router


def _guarded(call):
    """Calls ``call``, answering a `HostError` with its HTTP status."""
    try:
        return call()
    except HostError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


def create_daemon_app(
    host: Host, *, token: str | None = None, static_dir: Path | None = STATIC_DIR, worker: Worker | None = None
) -> FastAPI:
    """Builds the daemon's app.

    Args:
        host: The host role (repositories and runs).
        token: When set, every API call (the daemon's and the proxied run's) must carry it.
        worker: The worker role (jobs and files for runs hosted elsewhere), served under ``/api/worker``.
        static_dir: The web UI, served at ``/`` (the daemon's page) and ``/run/`` (the run's page).
    """
    app = FastAPI(
        title="clabe daemon API",
        version=__version__,
        description="Run experiments on this machine: list repositories and experiments, launch one, and reach the "
        "run's own API under /run/api.",
    )
    check = token_check(token)
    api = APIRouter(prefix="/api/daemon", dependencies=[Depends(check)])

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
        return _guarded(lambda: host.experiments(name))

    @api.post("/runs", response_model=RunInfo)
    def launch(body: LaunchBody) -> RunInfo:
        """Starts an experiment; 409 while another one runs. Follow it under /run/."""
        return _guarded(lambda: host.launch(body))

    @api.get("/runs/current", response_model=RunInfo | None)
    def current() -> RunInfo | None:
        """The run launched last (running or not), with the end of its output."""
        launched = host.current()
        return launched.info() if launched is not None else None

    @api.post("/runs/current/stop", response_model=RunInfo)
    def stop() -> RunInfo:
        """Stops the current run's process. Prefer aborting it (POST /run/api/abort): that lets it clean up."""
        return _guarded(host.stop)

    app.include_router(api)
    if worker is not None:
        app.include_router(worker_router(worker, check))
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


def create_daemon(settings: DaemonSettings, *, token: str | None = None) -> FastAPI:
    """The daemon's app with both roles: the host (runs started here) and the worker (jobs for runs elsewhere)."""
    holder: list[Worker] = []
    host = Host(settings, leased_by=lambda: lease.owner if (lease := holder[0].lease()) else None)
    holder.append(Worker(settings, busy=host.busy))
    return create_daemon_app(host, token=token, worker=holder[0])


async def _proxy(host: Host, client: httpx.AsyncClient, path: str, request: Request) -> Response:
    """Forwards ``request`` to the current run's server, with the run's own token; streams the answer back."""
    launched = host.current()
    port = launched.poll_port() if launched is not None and launched.process.poll() is None else None
    if launched is None or port is None:
        if path in ("", "index.html"):
            return HTMLResponse(_NO_RUN, status_code=503)
        raise HTTPException(status_code=503, detail="No run is being served on this machine.")
    url = f"http://127.0.0.1:{port}/{path}"
    return await forward(client, request, url, launched.token, unreachable="The run is not reachable")


_PLACEHOLDER = """<!doctype html><html><head><meta charset="utf-8"><title>clabe daemon</title></head>
<body style="font-family: sans-serif"><h1>clabe daemon</h1>
<p>The web UI is not part of this build. The API is at <a href="/docs">/docs</a>; a run is served under
<a href="/run/">/run/</a>.</p></body></html>"""

_NO_RUN = """<!doctype html><html><head><meta charset="utf-8"><title>clabe</title></head>
<body style="font-family: sans-serif"><h1>No run</h1>
<p>Nothing is running on this machine. <a href="../">Start an experiment</a>.</p></body></html>"""
