"""A thin Python client of the run API (tests, scripts, the daemon and the hub)."""

import json
import time
from collections.abc import Iterator
from typing import Any

import requests
from pydantic import TypeAdapter

from ..workflow import RunState
from ..workflow.events import Event
from ._models import CommandResult, DirListing, ServerInfo

_EVENT = TypeAdapter(Event)


class RunClient:
    """Talks to a served run.

    Args:
        base_url: The server's root URL, e.g. ``http://localhost:8090``.
        token: The shared token, when the server requires one.
        name: Recorded as the client in answers and commands.
        timeout: Seconds to wait for a non-streaming request.
    """

    def __init__(self, base_url: str, *, token: str | None = None, name: str = "remote", timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.name = name
        self.timeout = timeout
        self._session = requests.Session()
        if token:
            self._session.headers["Authorization"] = f"Bearer {token}"

    def _url(self, path: str) -> str:
        """The URL of API ``path``."""
        return f"{self.base_url}/api{path}"

    def _post(self, path: str, body: dict[str, Any] | None = None) -> CommandResult:
        """Posts a command and returns its result."""
        response = self._session.post(self._url(path), json=body, timeout=self.timeout)
        response.raise_for_status()
        return CommandResult.model_validate(response.json())

    def info(self) -> ServerInfo:
        """The server's identity and its run."""
        response = self._session.get(self._url("/info"), timeout=self.timeout)
        response.raise_for_status()
        return ServerInfo.model_validate(response.json())

    def state(self) -> RunState:
        """A snapshot of the run."""
        response = self._session.get(self._url("/state"), timeout=self.timeout)
        response.raise_for_status()
        return RunState.model_validate(response.json())

    def answer(self, prompt_id: str, value: Any) -> CommandResult:
        """Answers an open prompt."""
        return self._post(f"/prompts/{prompt_id}/answer", {"value": value, "client": self.name})

    def back(self, step: str) -> CommandResult:
        """Goes back to ``step``."""
        return self._post(f"/steps/{step}/back", {"client": self.name})

    def retry(self, step: str) -> CommandResult:
        """Retries a failed step."""
        return self._post(f"/steps/{step}/retry", {"client": self.name})

    def skip(self, step: str) -> CommandResult:
        """Skips an optional step."""
        return self._post(f"/steps/{step}/skip", {"client": self.name})

    def abort(self, reason: str = "Aborted by user.") -> CommandResult:
        """Aborts the run."""
        return self._post("/abort", {"reason": reason})

    def list_dir(
        self, path: str | None = None, *, kind: str = "any", extensions: list[str] | None = None
    ) -> DirListing:
        """Lists a directory of the machine running the run."""
        body = {"path": path, "kind": kind, "extensions": extensions}
        response = self._session.post(self._url("/fs/list"), json=body, timeout=self.timeout)
        response.raise_for_status()
        return DirListing.model_validate(response.json())

    def events(self, after: int = 0, *, reconnect: bool = True, retry_delay: float = 0.5) -> Iterator[Event]:
        """Follows the run's events from ``after``; reconnects (resuming) when the stream drops, until the run ends."""
        last = after
        while True:
            try:
                for event in self._stream(last):
                    last = event.seq
                    yield event
                    if event.type == "run_finished":
                        return
                return
            except (requests.ConnectionError, requests.exceptions.ChunkedEncodingError):
                if not reconnect:
                    raise
                time.sleep(retry_delay)

    def _stream(self, after: int) -> Iterator[Event]:
        """One SSE connection, parsed into events."""
        headers = {"Accept": "text/event-stream", "Last-Event-ID": str(after)}
        with self._session.get(self._url("/events"), headers=headers, stream=True, timeout=(self.timeout, None)) as r:
            r.raise_for_status()
            data: list[str] = []
            for raw in r.iter_lines(decode_unicode=True):
                if raw is None:
                    continue
                if raw == "":
                    if data:
                        yield _EVENT.validate_python(json.loads("\n".join(data)))
                        data = []
                    continue
                if raw.startswith(":"):
                    continue
                field, _, value = raw.partition(":")
                if field == "data":
                    data.append(value.removeprefix(" "))
