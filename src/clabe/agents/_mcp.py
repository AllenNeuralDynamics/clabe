"""An MCP server for clabe runs: agents read a run's state and answer its prompts through the run API.

It is a client like any other: point it at a run server, a rig's daemon (its run under ``/run/``) or a hub (every
rig under ``/rigs/<id>/``; tools then take a ``rig``). Needs the ``agents`` extra (the ``mcp`` package).
"""

import asyncio
import logging
from typing import Any, Literal

import requests
from pydantic import Field, create_model

from ..server import RunClient

logger = logging.getLogger(__name__)

#: Prompt kinds an MCP client can ask its user directly (elicitation supports flat, primitive forms).
_ELICITABLE = {"text", "number", "confirm", "pick", "autocomplete"}


class ClabeTarget:
    """Where the MCP server's tools act: one run (directly or through a daemon), or a hub's rigs."""

    def __init__(self, url: str, token: str | None = None) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.kind = self._detect()

    def _get(self, path: str) -> requests.Response:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        return requests.get(f"{self.url}{path}", headers=headers, timeout=10)

    def _detect(self) -> Literal["hub", "daemon", "run"]:
        """What the URL serves: a hub, a daemon, or a run."""
        for path, kind in (("/api/hub/rigs", "hub"), ("/api/daemon/info", "daemon")):
            try:
                if self._get(path).status_code in (200, 401):
                    return kind  # type: ignore[return-value]
            except requests.RequestException:
                break
        return "run"

    def rigs(self) -> list[dict[str, Any]]:
        """The hub's rigs (a single entry for a run or a daemon)."""
        if self.kind != "hub":
            return [{"id": None, "url": self.url, "kind": self.kind}]
        response = self._get("/api/hub/rigs")
        response.raise_for_status()
        return response.json()

    def run(self, rig: str | None = None) -> RunClient:
        """A client of the run (of ``rig``, through a hub)."""
        if self.kind == "hub":
            if not rig:
                raise ValueError("This is a hub: pass `rig` (see list_rigs).")
            return RunClient(f"{self.url}/rigs/{rig}/run", token=self.token, name="mcp")
        base = f"{self.url}/run" if self.kind == "daemon" else self.url
        return RunClient(base, token=self.token, name="mcp")


def _state(client: RunClient) -> dict[str, Any]:
    """A run's state, for an agent: steps, the current one, and the open prompt with its answer schema."""
    state = client.state()
    prompt = state.prompt
    return {
        "run_id": state.run_id,
        "workflow": state.workflow,
        "status": state.status.value,
        "current_step": state.current_step,
        "steps": [
            {"name": s.name, "title": s.title, "status": s.status.value, "locked": s.locked, "summary": s.summary}
            for s in state.steps
        ],
        "prompt": None
        if prompt is None
        else {
            "prompt_id": prompt.prompt_id,
            "step": prompt.step,
            "kind": prompt.kind,
            "spec": prompt.spec,
            "answer_schema": prompt.answer_schema,
        },
    }


def _answer_model(prompt: dict[str, Any]):
    """A one-field pydantic model to elicit ``prompt``'s answer, or None when it cannot be elicited."""
    kind, spec = prompt["kind"], prompt["spec"]
    label = str(spec.get("label") or prompt["kind"])
    if kind not in _ELICITABLE:
        return None
    if kind == "pick":
        labels = tuple(str(option["label"]) for option in spec.get("options", []))
        annotation: Any = Literal[labels] if labels else str  # type: ignore[valid-type]
    else:
        annotation = {"number": float, "confirm": bool}.get(kind, str)
    return create_model("Answer", answer=(annotation, Field(description=label)))


async def _ask_user(ctx: Any, client: RunClient) -> dict[str, Any]:
    """Elicits the open prompt's answer from the MCP client's user and submits it."""
    prompt = _state(client)["prompt"]
    if prompt is None:
        return {"ok": False, "reason": "No prompt is open."}
    model = _answer_model(prompt)
    if model is None:
        return {"ok": False, "reason": f"A {prompt['kind']} prompt cannot be asked here: use answer_prompt."}
    result = await ctx.elicit(message=str(prompt["spec"].get("label") or "Answer the prompt"), schema=model)
    if result.action != "accept" or result.data is None:
        return {"ok": False, "reason": f"The user did not answer ({result.action})."}
    value = result.data.answer
    if prompt["kind"] == "pick":
        value = next(o["value"] for o in prompt["spec"]["options"] if str(o["label"]) == value)
    return client.answer(prompt["prompt_id"], value).model_dump()


async def _wait_for_prompt(client: RunClient, timeout: float) -> dict[str, Any]:
    """The run's state once it opens a prompt or ends, or when ``timeout`` passes."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        state = await asyncio.to_thread(_state, client)
        if state["prompt"] is not None or state["status"] in ("completed", "aborted", "failed"):
            return state
        if asyncio.get_running_loop().time() >= deadline:
            return state
        await asyncio.sleep(0.5)


def create_mcp_server(target: ClabeTarget):
    """Builds the MCP server acting on ``target``."""
    from mcp.server.mcpserver import Context, MCPServer

    server = MCPServer(
        name="clabe",
        instructions="Follow and drive clabe experiment runs. Read the run with get_run; when it has an open prompt, "
        "answer it with answer_prompt (a value valid for its answer_schema) or ask_the_user. On a hub, pass `rig` "
        "(see list_rigs). Prefer asking the user over guessing answers about animals or hardware.",
    )

    @server.tool()
    def list_rigs() -> list[dict[str, Any]]:
        """The rigs this server can act on, with their status and whether they wait for an answer (hub only;
        otherwise the single run or daemon)."""
        return target.rigs()

    @server.tool()
    def get_run(rig: str | None = None) -> dict[str, Any]:
        """The run's state: workflow, status, steps, current step, and the open prompt (with its JSON answer schema)."""
        return _state(target.run(rig))

    @server.tool()
    def answer_prompt(prompt_id: str, value: Any, rig: str | None = None) -> dict[str, Any]:
        """Answers the open prompt. The value must be valid for the prompt's answer_schema; a rejected answer
        returns the reason and the prompt stays open."""
        return target.run(rig).answer(prompt_id, value).model_dump()

    @server.tool()
    async def ask_the_user(ctx: Context, rig: str | None = None) -> dict[str, Any]:
        """Asks the MCP client's user to answer the run's open prompt (simple prompts only), then submits it."""
        return await _ask_user(ctx, target.run(rig))

    @server.tool()
    async def wait_for_prompt(timeout_seconds: float = 60.0, rig: str | None = None) -> dict[str, Any]:
        """Waits until the run opens a prompt or ends (or the timeout passes), then returns its state."""
        return await _wait_for_prompt(target.run(rig), timeout_seconds)

    @server.tool()
    def go_back(step: str, rig: str | None = None) -> dict[str, Any]:
        """Goes back to a step: it and the steps depending on it run again (refused for locked steps)."""
        return target.run(rig).back(step).model_dump()

    @server.tool()
    def retry_step(step: str, rig: str | None = None) -> dict[str, Any]:
        """Retries a step that failed."""
        return target.run(rig).retry(step).model_dump()

    @server.tool()
    def skip_step(step: str, rig: str | None = None) -> dict[str, Any]:
        """Skips an optional step that asks whether to run."""
        return target.run(rig).skip(step).model_dump()

    @server.tool()
    def abort_run(reason: str, rig: str | None = None) -> dict[str, Any]:
        """Aborts the run. Only when the user asked for it."""
        return target.run(rig).abort(reason).model_dump()

    return server
