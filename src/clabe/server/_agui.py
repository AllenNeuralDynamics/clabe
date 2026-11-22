"""The run as an AG-UI agent (``POST /api/agui``), for AG-UI clients (e.g. an agent panel, CopilotKit).

Each AG-UI run is one turn: it follows the clabe run — steps as ``STEP_STARTED``/``STEP_FINISHED``, messages as text
messages, the run's state as ``STATE_SNAPSHOT`` — until the run needs the user. An open prompt becomes a call of
the frontend tool ``clabe_prompt`` (its arguments: the prompt's kind, spec and JSON answer schema) and the turn
ends; the client answers by sending the next turn with a ``tool`` message for that call (its content: the answer,
as JSON). The native API stays the source of truth: this is a projection of it.
"""

import json
import uuid
from collections.abc import AsyncIterable, Callable
from typing import Any

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from ..workflow import Rejected, Run
from ..workflow.events import (
    Event,
    Notify,
    PromptOpened,
    RunFinished,
    StepFailed,
    StepFinished,
    StepSkipped,
    StepStarted,
)

#: The frontend tool an open prompt is asked through.
PROMPT_TOOL = "clabe_prompt"


class RunAgentInput(BaseModel):
    """What an AG-UI client sends (only the fields clabe uses are read)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    thread_id: str = Field(default_factory=lambda: uuid.uuid4().hex, alias="threadId")
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex, alias="runId")
    messages: list[dict[str, Any]] = Field(default_factory=list)
    forwarded_props: dict[str, Any] = Field(default_factory=dict, alias="forwardedProps")


def _sse(event: dict[str, Any]) -> str:
    """One AG-UI event, framed as a Server-Sent Event."""
    return f"data: {json.dumps(event, default=str)}\n\n"


def _text(message: str) -> list[dict[str, Any]]:
    """A complete assistant text message."""
    message_id = uuid.uuid4().hex
    return [
        {"type": "TEXT_MESSAGE_START", "messageId": message_id, "role": "assistant"},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": message_id, "delta": message},
        {"type": "TEXT_MESSAGE_END", "messageId": message_id},
    ]


def _prompt_call(prompt: PromptOpened) -> list[dict[str, Any]]:
    """An open prompt, as a call of the frontend tool."""
    args = {"step": prompt.step, "kind": prompt.kind, "spec": prompt.spec, "answer_schema": prompt.answer_schema}
    return [
        {"type": "TOOL_CALL_START", "toolCallId": prompt.prompt_id, "toolCallName": PROMPT_TOOL},
        {"type": "TOOL_CALL_ARGS", "toolCallId": prompt.prompt_id, "delta": json.dumps(args, default=str)},
        {"type": "TOOL_CALL_END", "toolCallId": prompt.prompt_id},
    ]


def _answers(body: RunAgentInput) -> list[tuple[str, Any]]:
    """The tool results the client sent: (prompt id, answer)."""
    answers = []
    for message in body.messages:
        if message.get("role") != "tool" or not message.get("toolCallId"):
            continue
        content = message.get("content")
        try:
            value = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError:
            value = content
        answers.append((message["toolCallId"], value))
    return answers


async def agui_stream(
    run: Run,
    body: RunAgentInput,
    request: Request,
    follow: Callable[[Run, Request, int], AsyncIterable[Event]],
) -> AsyncIterable[str]:
    """One AG-UI turn over ``run`` (see the module's description)."""
    started = {"threadId": body.thread_id, "runId": body.run_id}
    yield _sse({"type": "RUN_STARTED", **started})
    for prompt_id, value in _answers(body):
        if run.state().prompt is not None and run.state().prompt.prompt_id == prompt_id:
            outcome = run.answer(prompt_id, value, client="ag-ui")
            if isinstance(outcome, Rejected):
                for event in _text(f"That answer was rejected: {outcome.reason}"):
                    yield _sse(event)
    state = run.state()
    yield _sse({"type": "STATE_SNAPSHOT", "snapshot": state.model_dump(mode="json")})
    if state.prompt is not None:
        for event in _prompt_call(state.prompt):
            yield _sse(event)
        yield _sse({"type": "RUN_FINISHED", **started})
        return
    if state.status.value in ("completed", "aborted", "failed"):
        yield _sse({"type": "RUN_FINISHED", **started})
        return
    async for event in follow(run, request, state.last_seq):
        for out in _project(event):
            yield _sse(out)
        if isinstance(event, PromptOpened | RunFinished):
            yield _sse({"type": "STATE_SNAPSHOT", "snapshot": run.state().model_dump(mode="json")})
            if isinstance(event, PromptOpened):
                for out in _prompt_call(event):
                    yield _sse(out)
            yield _sse({"type": "RUN_FINISHED", **started})
            return


def _project(event: Event) -> list[dict[str, Any]]:
    """The AG-UI events a run event becomes."""
    if isinstance(event, StepStarted):
        return [{"type": "STEP_STARTED", "stepName": event.step}]
    if isinstance(event, StepFinished | StepSkipped):
        return [{"type": "STEP_FINISHED", "stepName": event.step}]
    if isinstance(event, StepFailed):
        return [*_text(f"Step {event.step} failed: {event.error}"), {"type": "STEP_FINISHED", "stepName": event.step}]
    if isinstance(event, Notify):
        return _text(event.message)
    return []
