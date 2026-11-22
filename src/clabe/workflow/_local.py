"""Driving a run from an in-process client (console or Textual TUI)."""

import dataclasses
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any

from ..ui import (
    AutoCompleteRequest,
    Client,
    ConfirmRequest,
    FormRequest,
    GroupRequest,
    MessageLevel,
    NavigationRequested,
    NumberRequest,
    PathRequest,
    PickRequest,
    PromptCancelledError,
    Request,
    TextRequest,
)
from ._events import Event, Header, Notify, PromptClosed, PromptOpened, RunFinished
from ._run import Ack, Rejected, Run

logger = logging.getLogger(__name__)


def prefill(request: Request, value: Any) -> Request:
    """A copy of ``request`` whose default (or a form's initial value) is ``value``, its earlier answer.

    Clients show the default, so a prompt answered before opens with that answer; the run still validates with the
    original request. Requests with nothing to prefill, and values that do not fit, give ``request`` unchanged.
    """
    try:
        if isinstance(request, TextRequest | AutoCompleteRequest) and isinstance(value, str):
            return dataclasses.replace(request, default=value)
        if isinstance(request, NumberRequest) and isinstance(value, int | float) and not isinstance(value, bool):
            return dataclasses.replace(request, default=float(value))
        if isinstance(request, ConfirmRequest) and isinstance(value, bool):
            return dataclasses.replace(request, default=value)
        if isinstance(request, PickRequest) and (value is None or any(c.value == value for c in request.choices())):
            return dataclasses.replace(request, default=value)
        if isinstance(request, PathRequest) and isinstance(value, str | Path):
            return dataclasses.replace(request, default=str(value))
        if isinstance(request, FormRequest) and value is not None:
            initial = value if isinstance(value, request.model) else request.model.model_validate(value)
            return dataclasses.replace(request, initial=initial)
        if isinstance(request, GroupRequest) and isinstance(value, dict):
            items = {
                name: prefill(item, value[name]) if name in value else item for name, item in request.items.items()
            }
            return dataclasses.replace(request, items=items)
    except Exception:  # prefilling is a convenience: an earlier answer that does not fit is ignored
        logger.debug("Could not prefill %s prompt with %r", request.prompt_kind, value, exc_info=True)
    return request


class LocalClient:
    """Answers a run's prompts with a local [`Client`][clabe.ui.Client] and renders its messages.

    The client only collects raw answers: the run validates them, and a rejected answer is shown to the user (with
    the reason) and asked again. A prompt answered before is shown prefilled with that answer (see `prefill`), and
    a client may raise [`NavigationRequested`][clabe.ui.NavigationRequested] to go back
    ([`Run.back`][clabe.workflow.Run.back]) or forward ([`Run.forward`][clabe.workflow.Run.forward]).

    `drive` must run on the main thread (it is where Ctrl+C lands). The first Ctrl+C aborts the run;
    a second one propagates as ``KeyboardInterrupt``.

    Args:
        run: The run to drive (not yet started).
        client: The console/TUI client used to ask and render.
        name: Client name recorded on answers.
    """

    def __init__(self, run: Run, client: Client, *, name: str = "local") -> None:
        self._run = run
        self._client = client
        self._name = name
        self._prompts: queue.Queue[PromptOpened | None] = queue.Queue()
        self._lock = threading.Lock()
        self._asking: str | None = None
        self._answered_elsewhere: str | None = None  # the prompt we are asking, once another client answered it

    def drive(self) -> None:
        """Starts the run and serves it until it finishes.

        Raises:
            KeyboardInterrupt: Ctrl+C was pressed a second time (the run is aborted, not awaited).
            Exception: Whatever the client raised; the run is aborted first so it never waits on this client.
        """
        unsubscribe = self._run.events.subscribe(self._on_event)
        interrupted = False
        started = False  # a run this client did not start is someone else's: never abort it
        try:
            self._run.start()
            started = True
            while True:
                try:
                    try:
                        item = self._prompts.get(timeout=0.1)
                    except queue.Empty:
                        if self._run.wait(0):  # finished, but its RunFinished never reached us
                            break
                        continue
                    if item is None:
                        break
                    self._answer(item)
                except KeyboardInterrupt:
                    if interrupted:
                        raise
                    interrupted = True
                    self._run.abort("Interrupted by user.")
                    self._client.notify("Aborting… press Ctrl+C again to force quit.", MessageLevel.WARNING)
        except KeyboardInterrupt:
            if started:
                self._run.abort("Interrupted by user.")  # no-op if already aborting or finished
            raise
        except BaseException:
            if started and not self._run.wait(0):
                self._run.abort("The local client failed.")
                self._run.wait(5)  # let it record its end; never block on a step that ignores the abort
            raise
        finally:
            unsubscribe()
            with self._lock:  # an interrupt inside _answer's cleanup must not leave a cancel thread spinning
                self._asking = None
        self._run.wait()

    def _on_event(self, event: Event) -> None:
        """Routes a run event: prompts go to the main thread, messages are rendered right away."""
        if isinstance(event, PromptOpened):
            self._prompts.put(event)
        elif isinstance(event, PromptClosed):
            self._on_prompt_closed(event)
        elif isinstance(event, Notify):
            self._client.notify(event.message, MessageLevel(event.level))
        elif isinstance(event, Header):
            self._client.header(event.text)
        elif isinstance(event, RunFinished):
            self._prompts.put(None)

    def _on_prompt_closed(self, event: PromptClosed) -> None:
        """Cancels the local prompt when its run prompt was closed by someone else (or by the run)."""
        if event.answered_by == self._name:
            return
        with self._lock:
            if self._asking != event.prompt_id:
                return
            if event.outcome == "answered":
                self._answered_elsewhere = event.prompt_id
        # Never cancel inline: subscribers must return quickly and a client's cancel may block.
        threading.Thread(
            target=self._cancel_until_released, args=(event.prompt_id,), name="clabe-cancel-prompt", daemon=True
        ).start()

    def _cancel_until_released(self, prompt_id: str) -> None:
        """Keeps cancelling the local prompt until it returns: a cancel landing before it shows is not lost."""
        while True:
            with self._lock:
                if self._asking != prompt_id:
                    return
            self._client.cancel()
            time.sleep(0.1)

    def _answer(self, opened: PromptOpened) -> None:
        """Asks ``opened`` locally until it is answered here, closed elsewhere or the run moves on."""
        request = self._run.prompt_request(opened.prompt_id)
        if request is None:
            return
        answered_before, previous = self._run.previous_answer(opened.prompt_id)
        if answered_before:
            request = prefill(request, previous)
        with self._lock:
            self._asking = opened.prompt_id
        try:
            while self._run.is_prompt_open(opened.prompt_id):
                try:
                    value = self._client.ask(request)
                except PromptCancelledError:
                    continue  # closed elsewhere (the loop ends) or a stray cancel of an earlier prompt: ask again
                except NavigationRequested as request_:
                    outcome = self._navigate(request_.action)
                    if isinstance(outcome, Rejected):
                        self._client.notify(outcome.reason, MessageLevel.WARNING)
                    continue  # accepted: the prompt closed (the loop ends); refused: ask again
                result = self._run.answer(opened.prompt_id, value, client=self._name)
                if isinstance(result, Ack):
                    return
                if self._run.is_prompt_open(opened.prompt_id):
                    self._client.notify(result.reason, MessageLevel.ERROR)
                elif self._answered_elsewhere == opened.prompt_id:  # the client could not cancel its prompt
                    self._client.notify("This prompt was answered elsewhere.", MessageLevel.INFO)
        finally:
            with self._lock:
                self._asking = None

    def _navigate(self, action: str) -> Ack | Rejected:
        """Goes back or forward, as the client asked."""
        if action == "back":
            return self._run.back()
        if action == "forward":
            return self._run.forward(client=self._name)
        return Rejected(f"Unknown navigation: {action!r}.")
