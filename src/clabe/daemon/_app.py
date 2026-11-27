"""The daemon's HTTP app: its own API under /api/daemon, the run it hosts proxied under /run/, and the web UI."""

import asyncio
import logging
import os
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi import Path as FastApiPath
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..reports import REPORT_ID_PATTERN, ReportInfo, count_reports, delete_report, list_reports, report_path
from ..server._app import STATIC_DIR
from ..server._proxy import TokenCookies, forward, relative_redirect, token_check
from ..workflow import ExperimentListing
from . import _install, _watchdog
from ._enroll import enrolling
from ._host import AttachBody, Host, HostError, HostInfo, LaunchBody, RepositoryInfo, RunInfo
from ._settings import DaemonSettings
from ._worker import Worker, worker_router

logger = logging.getLogger(__name__)


def _guarded(call):
    """Calls ``call``, answering a `HostError` with its HTTP status."""
    try:
        return call()
    except HostError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


def _attach_routes(api: APIRouter, host: Host) -> None:
    """The routes through which a run started outside the daemon announces itself and withdraws."""

    @api.post("/runs/attached", response_model=RunInfo)
    def attach(body: AttachBody) -> RunInfo:
        """A run started on this machine outside the daemon (``clabe run`` in a terminal) announces itself: it is
        then served under /run/ and seen by the hub. 409 while another run is going."""
        return _guarded(lambda: host.attach(body))

    @api.delete("/runs/attached/{pid}", status_code=204)
    def detach(pid: int) -> None:
        """The announced run of process ``pid`` has ended."""
        host.detach(pid)


def _report_routes(api: APIRouter, directory: Path) -> None:
    """The routes through which the hub collects this machine's issue reports (list, download, then delete)."""
    report_id_param = FastApiPath(pattern=REPORT_ID_PATTERN, description="The report's id (32 hex digits)")

    def existing(report_id: str) -> Path:
        """The zip of report ``report_id``; 404 if there is none."""
        path = report_path(report_id, directory)
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"No report {report_id}.")
        return path

    @api.get("/reports", response_model=list[ReportInfo])
    def reports() -> list[ReportInfo]:
        """The issue reports waiting on this machine for the hub, oldest first."""
        return list_reports(directory)

    @api.get(
        "/reports/{report_id}",
        response_class=FileResponse,
        responses={200: {"content": {"application/zip": {}}, "description": "The report's zip"}},
    )
    def report(report_id: Annotated[str, report_id_param]) -> FileResponse:
        """A report's zip (its ``report.json`` and the run's records)."""
        return FileResponse(existing(report_id), media_type="application/zip", filename=f"{report_id}.zip")

    @api.delete("/reports/{report_id}", status_code=204)
    def delete(report_id: Annotated[str, report_id_param]) -> None:
        """Deletes a report from this machine (the hub does once it has stored it)."""
        if not delete_report(report_id, directory):
            raise HTTPException(status_code=404, detail=f"No report {report_id}.")


class RestartResult(BaseModel):
    """A restart that is under way."""

    detail: str = Field(description="What happens next")


def _restart_routes(api: APIRouter, host: Host) -> None:
    """``POST /restart``: the daemon ends itself and its task starts a new one (also through the hub)."""

    @api.post(
        "/restart",
        status_code=202,
        response_model=RestartResult,
        responses={409: {"description": "A run is going (without force), or the daemon was not started by its task"}},
    )
    def restart(background: BackgroundTasks, force: bool = False) -> RestartResult:
        """Restarts the daemon: it answers, ends itself, and its task starts a new one (in a few seconds). Refused
        while it runs an experiment or is leased by another machine's run, unless ``force`` (the run is stopped)."""
        status = host.info().status
        if status in ("busy", "leased") and not force:
            what = "runs an experiment" if status == "busy" else "is leased by another machine's run"
            raise HTTPException(status_code=409, detail=f"The daemon {what}: restarting would interrupt it.")
        if force and status == "busy":
            try:
                host.stop()
            except HostError:
                pass  # it ended meanwhile
        try:
            _install.restart_later(os.getpid())
        except OSError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        logger.info("Restarting, as asked through the API: ending the daemon, its task starts a new one.")
        background.add_task(_end_soon)
        return RestartResult(detail="The daemon restarts: it answers again in a few seconds.")


async def _end_soon() -> None:
    """Ends the daemon once its answer is sent (`_watchdog.end_process` flushes the logs first)."""
    await asyncio.sleep(0.5)
    _watchdog.end_process(0)


def create_daemon_app(
    host: Host, *, token: str | None = None, static_dir: Path | None = STATIC_DIR, worker: Worker | None = None
) -> FastAPI:
    """Builds the daemon's app.

    Args:
        host: The host role (repositories and runs).
        token: When set, every API call (the daemon's and the proxied run's) must carry it.
        worker: The worker role (jobs and files for runs hosted elsewhere), served under ``/api/worker``.
        static_dir: The web UI, served at ``/`` (the daemon's page) and ``/run/`` (the run's page).

    While it serves, the daemon enrolls with the hubs of its settings (``hubs``), if any.
    """
    app = FastAPI(
        title="clabe daemon API",
        version=__version__,
        description="Run experiments on this machine: list repositories and experiments, launch one, and reach the "
        "run's own API under /run/api.",
        lifespan=lambda _: enrolling(host.settings),
    )
    app.add_middleware(TokenCookies, token=token)
    check = token_check(token)
    api = APIRouter(prefix="/api/daemon", dependencies=[Depends(check)])

    reports_directory = host.settings.reports_path()

    @api.get("/info", response_model=HostInfo)
    def info() -> HostInfo:
        """The machine, its clabe version, whether it is running an experiment, and its reports waiting."""
        return host.info().model_copy(update={"reports_waiting": count_reports(reports_directory)})

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

    _attach_routes(api, host)
    _report_routes(api, reports_directory)
    _restart_routes(api, host)

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
    def run_root(request: Request) -> Response:
        """The run's page needs the trailing slash (its links are relative)."""
        return relative_redirect(request.url.path, request.url.query, slash=True)

    @app.api_route("/run/{path:path}", methods=["GET", "POST"], include_in_schema=False)
    async def proxy(path: str, request: Request) -> Response:
        """The current run's server: its web UI and (with the daemon's token) its API."""
        if path.startswith("api/"):
            await check(request)
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
    port = launched.poll_port() if launched is not None and launched.alive() else None
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
