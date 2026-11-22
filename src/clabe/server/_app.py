"""The run API: a FastAPI app serving one run — snapshot, live event stream, answers and commands."""

import asyncio
import contextlib
import hmac
import logging
import mimetypes
import socket
from collections.abc import AsyncIterable
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..ui._requests import normalize_extensions
from ..workflow import Ack, Rejected, Run, RunState
from ..workflow.events import Event, PromptOpened, RunFinished
from ._agui import RunAgentInput, agui_stream
from ._models import (
    AbortBody,
    AnswerBody,
    ClientBody,
    CommandResult,
    DirEntry,
    DirListing,
    ListDirBody,
    ServerInfo,
)

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


def _token_check(token: str | None):
    """A dependency rejecting requests without the shared token (when one is set)."""

    async def check(
        authorization: str | None = Header(default=None),
        token_param: str | None = Query(default=None, alias="token"),
    ) -> None:
        """Raises 401 unless the request carries the token."""
        if token is None:
            return
        given = token_param
        if authorization and authorization.lower().startswith("bearer "):
            given = authorization[7:]
        if given is None or not hmac.compare_digest(given, token):
            raise HTTPException(status_code=401, detail="A valid token is required.")

    return check


def create_app(run: Run, *, token: str | None = None, static_dir: Path | None = STATIC_DIR) -> FastAPI:
    """Builds the API serving ``run``.

    Args:
        run: The run to serve.
        token: When set, every API request must carry it (``Authorization: Bearer <token>`` or ``?token=``).
        static_dir: Directory of the web UI to serve at ``/`` (a placeholder page when it is missing).
    """
    app = FastAPI(
        title="clabe run API",
        version=__version__,
        description="Observe and drive a clabe run: a snapshot, a resumable event stream, answers and commands.",
    )
    api = APIRouter(prefix="/api", dependencies=[Depends(_token_check(token))])

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


def _mount_ui(app: FastAPI, static_dir: Path | None) -> None:
    """Serves the web UI at ``/`` (a placeholder page pointing at the API when it is not built)."""
    if static_dir is not None and (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")
        return

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def placeholder() -> str:
        """A page pointing at the API when the web UI is not built."""
        return _PLACEHOLDER


async def _event_stream(run: Run, request: Request, start: int) -> AsyncIterable[Event]:
    """The run's events after ``start``: the backlog, then live ones, until the run ends or the client leaves."""
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
            if isinstance(event, RunFinished):
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
            if isinstance(event, RunFinished):
                return
    finally:
        unsubscribe()


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
