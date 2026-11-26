"""The run API: a FastAPI app serving one run — snapshot, live event stream, answers and commands."""

import asyncio
import contextlib
import logging
import mimetypes
import re
import socket
from collections.abc import AsyncIterable
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from fastapi.staticfiles import StaticFiles

from .. import __version__, reports
from ..reports import ReportInfo
from ..ui import AutoCompleteRequest, GroupRequest
from ..ui._requests import normalize_extensions
from ..workflow import Ack, Rejected, Run, RunState
from ..workflow.events import Event, PromptOpened, RunClosed, RunFinished, RunStarted
from ._agui import RunAgentInput, agui_stream
from ._logs import LogBuffer
from ._models import (
    AbortBody,
    AnswerBody,
    ClientBody,
    CommandResult,
    DirEntry,
    DirListing,
    ListDirBody,
    LogLine,
    ReportBody,
    ServerInfo,
    SessionInfo,
)
from ._proxy import TokenCookies, token_check

logger = logging.getLogger(__name__)

# The web UI's types, which the platform's registry may not know (Windows often lacks .wasm).
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("application/json", ".json")

#: Where the built web UI (milestone 3) is packaged.
STATIC_DIR = Path(__file__).parent / "static"

_PLACEHOLDER = """<!doctype html><html><head><meta charset="utf-8"><title>clabe</title></head>
<body style="font-family: sans-serif"><h1>clabe</h1>
<p>The web UI is not part of this build. The run API is available: <a href="/docs">/docs</a>,
<a href="/openapi.json">/openapi.json</a>.</p></body></html>"""


def _result(outcome: Ack | Rejected) -> CommandResult:
    """The API form of a run's answer to a command."""
    if isinstance(outcome, Rejected):
        return CommandResult(ok=False, reason=outcome.reason)
    return CommandResult(ok=True)


def create_app(
    run: Run, *, token: str | None = None, static_dir: Path | None = STATIC_DIR, logs: LogBuffer | None = None
) -> FastAPI:
    """Builds the API serving ``run``.

    Args:
        run: The run to serve.
        token: When set, every API request must carry it (``Authorization: Bearer <token>`` or ``?token=``).
        static_dir: Directory of the web UI to serve at ``/`` (a placeholder page when it is missing).
        logs: The process' log records to serve at ``/api/logs`` (attached by `RunServer`); none: an empty stream.
    """
    logs = logs if logs is not None else LogBuffer()
    app = FastAPI(
        title="clabe run API",
        version=__version__,
        description="Observe and drive a clabe run: a snapshot, a resumable event stream, answers and commands.",
    )
    app.add_middleware(TokenCookies, token=token)
    api = APIRouter(prefix="/api", dependencies=[Depends(token_check(token))])

    @api.get("/info", response_model=ServerInfo)
    def info() -> ServerInfo:
        """Identity of the server and its run."""
        return ServerInfo(
            clabe_version=__version__,
            hostname=socket.gethostname(),
            run_id=run.id,
            workflow=run.workflow.name,
            status=run.status.value,
        )

    @api.get("/session", response_model=SessionInfo)
    def session() -> SessionInfo:
        """What the run is about so far: session id, subject, experimenter, repository, progress, start and end."""
        return _session_info(run)

    @api.get("/state", response_model=RunState)
    def state() -> RunState:
        """A snapshot of the run; follow the events after its ``last_seq``."""
        return run.state()

    @api.get("/events", response_class=EventSourceResponse)
    async def events(
        request: Request,
        after: int | None = Query(default=None, description="Only events after this sequence number."),
        last_event_id: str | None = Header(default=None),
    ) -> AsyncIterable[Event]:
        """The run's events as Server-Sent Events, from ``after`` (or ``Last-Event-ID``); ends after the run ends."""
        start = after if after is not None else int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
        async for event in _event_stream(run, request, start):
            yield ServerSentEvent(data=event, id=str(event.seq), event=event.type)

    _add_log_route(api, run, logs)

    @api.post("/prompts/{prompt_id}/answer", response_model=CommandResult)
    def answer(prompt_id: str, body: AnswerBody) -> CommandResult:
        """Answers an open prompt; an invalid answer is rejected and the prompt stays open."""
        return _result(run.answer(prompt_id, body.value, client=body.client))

    @api.get("/prompts/{prompt_id}/media", response_class=FileResponse)
    def media(prompt_id: str) -> FileResponse:
        """The local file shown by a media prompt (an image or an HTML page); nothing else is served."""
        return FileResponse(_media_file(run, prompt_id))

    @api.post("/steps/{name}/back", response_model=CommandResult)
    def back(name: str, body: ClientBody | None = None) -> CommandResult:
        """Goes back to step ``name``: it and the steps depending on it run again."""
        return _result(run.go_back(name))

    @api.post("/steps/{name}/retry", response_model=CommandResult)
    def retry(name: str, body: ClientBody | None = None) -> CommandResult:
        """Retries step ``name`` after it failed."""
        return _result(run.retry(name, client=(body or ClientBody()).client))

    @api.post("/steps/{name}/skip", response_model=CommandResult)
    def skip(name: str, body: ClientBody | None = None) -> CommandResult:
        """Skips the optional step ``name``."""
        return _result(run.skip(name, client=(body or ClientBody()).client))

    _add_navigation_and_session_routes(api, run)

    @api.post("/abort", response_model=CommandResult)
    def abort(body: AbortBody | None = None) -> CommandResult:
        """Aborts the run."""
        return _result(run.abort((body or AbortBody()).reason))

    @api.post("/agui", response_class=StreamingResponse)
    async def agui(body: RunAgentInput, request: Request) -> StreamingResponse:
        """The run as an AG-UI agent: one turn follows the run until it needs the user (see ``clabe.server._agui``)."""
        return StreamingResponse(agui_stream(run, body, request, _event_stream), media_type="text/event-stream")

    @api.post("/fs/list", response_model=DirListing)
    def list_dir(body: ListDirBody) -> DirListing:
        """Lists a directory of the machine running the run (for remote path pickers)."""
        return _list_dir(body)

    app.include_router(api)
    _mount_ui(app, static_dir)
    return app


def _add_log_route(api: APIRouter, run: Run, logs: LogBuffer) -> None:
    """Adds the stream of the process' log records (the web UI's Log pane)."""

    @api.get("/logs", response_class=EventSourceResponse)
    async def log_lines(
        request: Request,
        after: int | None = Query(default=None, description="Only lines after this id."),
        last_event_id: str | None = Header(default=None),
    ) -> AsyncIterable[LogLine]:
        """The process' log records (info and above) as Server-Sent Events (``log``), the latest kept ones first, then
        live ones; ends when the run's session is over or the client leaves."""
        start = after if after is not None else int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
        async for line in _log_stream(run, logs, request, start):
            yield ServerSentEvent(data=line, id=str(line.id), event="log")


def _add_navigation_and_session_routes(api: APIRouter, run: Run) -> None:
    """Adds the routes of the run page's menus: options of a prompt, back/forward, close, export and report."""

    @api.get("/prompts/{prompt_id}/options", response_model=list[str])
    def options(
        prompt_id: str,
        q: str = Query(default="", description="The text typed so far."),
        item: str | None = Query(default=None, description="The group item, when the prompt is a group."),
    ) -> list[str]:
        """The options of the open autocomplete prompt for the text typed so far (its options function's result)."""
        return _options(run, prompt_id, q, item)

    @api.post("/back", response_model=CommandResult)
    def back_previous(body: ClientBody | None = None) -> CommandResult:
        """Goes back to the previous step (the state's ``back_target``)."""
        return _result(run.back())

    @api.post("/forward", response_model=CommandResult)
    def forward(body: ClientBody | None = None) -> CommandResult:
        """Answers the open prompt with its earlier answer (the prompt's ``previous_answer``)."""
        return _result(run.forward(client=(body or ClientBody()).client))

    @api.post("/close", response_model=CommandResult)
    def close(body: ClientBody | None = None) -> CommandResult:
        """Closes the finished run's session (its closing screen): the launcher exits."""
        return _result(run.close(client=(body or ClientBody()).client))

    @api.get(
        "/export",
        response_class=Response,
        responses={200: {"content": {"application/zip": {}}, "description": "The bundle (a zip)."}},
    )
    def export() -> Response:
        """The run's logs and records so far as a zip (see ``clabe.reports.make_bundle``), also while closing."""
        data = reports.make_bundle(run.record_dir)
        filename = f"{_safe_name(run.workflow.name)}-{run.id}.zip"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return Response(content=data, media_type="application/zip", headers=headers)

    @api.post("/report", response_model=ReportInfo)
    def report(body: ReportBody | None = None) -> ReportInfo:
        """Files an issue report (the bundle and the comment), saved on this machine for the hub to collect."""
        body = body or ReportBody()
        return reports.report_run(run, body.comment, reported_from=body.client)


def _mount_ui(app: FastAPI, static_dir: Path | None) -> None:
    """Serves the web UI at ``/`` (a placeholder page pointing at the API when it is not built)."""
    if static_dir is not None and (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")
        return

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def placeholder() -> str:
        """A page pointing at the API when the web UI is not built."""
        return _PLACEHOLDER


def _is_last(run: Run, event: Event) -> bool:
    """Whether ``event`` ends the run's event stream: ``RunClosed``, or ``RunFinished`` when the session is not kept
    open for a closing screen."""
    return isinstance(event, RunClosed) or (isinstance(event, RunFinished) and not run.kept_open)


async def _event_stream(run: Run, request: Request, start: int) -> AsyncIterable[Event]:
    """The run's events after ``start``: the backlog, then live ones, until the run ends (its session closes, when
    kept open) or the client leaves."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Event] = asyncio.Queue()

    def forward(event: Event) -> None:
        """Hands an event from the run's thread to this stream's loop."""
        with contextlib.suppress(RuntimeError):  # the server's loop is closing
            loop.call_soon_threadsafe(queue.put_nowait, event)

    unsubscribe = run.events.subscribe(forward)  # subscribe before reading the backlog: no gap
    try:
        last = start
        for event in run.events.since(start):
            last = event.seq
            yield event
            if _is_last(run, event):
                return
        while not await request.is_disconnected():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            if event.seq <= last:
                continue
            last = event.seq
            yield event
            if _is_last(run, event):
                return
    finally:
        unsubscribe()


def _session_over(run: Run) -> bool:
    """Whether the run's session is over: closed, or finished when not kept open for a closing screen."""
    return run.closed or (run.status.value in ("completed", "aborted", "failed") and not run.kept_open)


async def _log_stream(run: Run, logs: LogBuffer, request: Request, start: int) -> AsyncIterable[LogLine]:
    """The log lines after ``start``: the kept ones, then live ones, until the session is over or the client leaves."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[LogLine] = asyncio.Queue()

    def forward(line: LogLine) -> None:
        """Hands a line from the logging thread to this stream's loop."""
        with contextlib.suppress(RuntimeError):  # the server's loop is closing
            loop.call_soon_threadsafe(queue.put_nowait, line)

    unsubscribe = logs.subscribe(forward)  # subscribe before reading the backlog: no gap
    try:
        last = start
        for line in logs.since(start):
            last = line.id
            yield line
        while not await request.is_disconnected():
            try:
                line = await asyncio.wait_for(queue.get(), timeout=1.0)
            except TimeoutError:
                if _session_over(run):
                    return
                continue
            if line.id <= last:
                continue
            last = line.id
            yield line
    finally:
        unsubscribe()


def _options(run: Run, prompt_id: str, typed: str, item: str | None) -> list[str]:
    """The options of the open autocomplete prompt ``prompt_id`` (or of its group ``item``) for ``typed``."""
    request = run.prompt_request(prompt_id)
    if request is None:
        raise HTTPException(status_code=404, detail=f"Prompt {prompt_id!r} is not open.")
    if item is not None:
        if not isinstance(request, GroupRequest) or item not in request.items:
            raise HTTPException(status_code=404, detail=f"Prompt {prompt_id!r} has no item {item!r}.")
        request = request.items[item]
    if not isinstance(request, AutoCompleteRequest):
        raise HTTPException(status_code=400, detail=f"Prompt {prompt_id!r} has no options to complete.")
    try:
        return request.suggestions(typed)
    except Exception as exc:  # the options function is user code (e.g. a database search)
        logger.warning("The options of prompt %s could not be listed", prompt_id, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Could not list the options: {exc}") from exc


def _safe_name(text: str) -> str:
    """``text`` usable in a file name."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "run"


def _launcher_info(run: Run) -> dict[str, object]:
    """Session id, subject, experimenter, repository and commit, from the run's launcher (when it has one)."""
    return reports.describe_launcher(run.launcher)


def _session_info(run: Run) -> SessionInfo:
    """The run as a session (see ``GET /api/session``)."""
    state = run.state()
    started = finished = None
    reason = None
    for event in run.events.since(0):
        if isinstance(event, RunStarted) and started is None:
            started = event.ts
        elif isinstance(event, RunFinished):
            finished, reason = event.ts, event.reason
    facts = {k: v for k, v in _launcher_info(run).items() if k in SessionInfo.model_fields}
    if state.context is not None:  # the subject and experimenters may be known before a session is registered
        facts.setdefault("subject", state.context.subject)
        facts.setdefault("experimenter", state.context.experimenter)
        facts["rig"] = state.context.rig
    return SessionInfo(
        run_id=run.id,
        experiment=run.workflow.name,
        status=state.status.value,
        current_step=state.current_step,
        waiting=state.prompt is not None,
        steps_done=sum(1 for s in state.steps if s.status.value in ("done", "skipped")),
        steps_total=len(state.steps),
        started_at=started,
        finished_at=finished,
        reason=reason,
        hostname=socket.gethostname(),
        clabe_version=__version__,
        **facts,
    )


def _media_file(run: Run, prompt_id: str) -> Path:
    """The file of media prompt ``prompt_id``, as its request described it."""
    for event in run.events.since(0):
        if isinstance(event, PromptOpened) and event.prompt_id == prompt_id:
            if event.kind != "media" or event.spec.get("location") != "file":
                break
            path = Path(event.spec["source"]).expanduser()
            if not path.is_file():
                raise HTTPException(status_code=404, detail=f"The file is gone: {path}")
            return path
    raise HTTPException(status_code=404, detail=f"Prompt {prompt_id!r} shows no local file.")


def _list_dir(body: ListDirBody) -> DirListing:
    """Lists ``body.path``: directories always, files when ``kind`` allows and their suffix matches."""
    directory = Path(body.path).expanduser() if body.path else Path.cwd()
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail=f"Not a directory: {directory}")
    extensions = normalize_extensions(body.extensions)
    entries = []
    try:
        children = sorted(directory.iterdir(), key=lambda child: (not child.is_dir(), child.name.lower()))
    except OSError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    for child in children:
        is_dir = child.is_dir()
        if not is_dir and (body.kind == "dir" or (extensions and child.suffix.lower() not in extensions)):
            continue
        entries.append(DirEntry(name=child.name, path=str(child), is_dir=is_dir))
    parent = directory.parent if directory.parent != directory else None
    return DirListing(path=str(directory), parent=str(parent) if parent else None, entries=entries)
