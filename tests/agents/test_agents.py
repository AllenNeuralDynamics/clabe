"""Agents and runs: the MCP server (a real stdio session) and the AG-UI projection."""

import asyncio
import json
import sys

import pytest
import requests

from clabe import ui
from clabe.server import RunServer, free_port
from clabe.workflow import Run, RunStatus, Workflow


def _workflow() -> Workflow:
    wf = Workflow("demo")

    @wf.step(title="Subject")
    def subject() -> str:
        return ui.text("Subject")

    @wf.step(title="Trials")
    def trials(subject: str) -> float:
        return ui.number("Trials", minimum=1, maximum=500)

    return wf


@pytest.fixture
def served():
    run = Run(_workflow())
    server = RunServer(run, port=free_port("127.0.0.1", 18470))
    server.start()
    run.start()
    yield run, f"http://127.0.0.1:{server.port}"
    if not run.wait(0):
        run.abort("teardown")
    server.stop()


def _payload(result) -> dict:
    """A tool's result, as JSON."""
    if getattr(result, "structuredContent", None):
        content = result.structuredContent
        return content.get("result", content) if set(content) == {"result"} else content
    return json.loads(result.content[0].text)


def test_an_mcp_client_reads_and_answers_a_run(served):
    pytest.importorskip("mcp")
    from mcp import ClientSession, types
    from mcp.client.stdio import StdioServerParameters, stdio_client

    run, url = served

    async def elicit(context, params):
        assert params.message == "Subject"
        return types.ElicitResult(action="accept", content={"answer": "mouse-5"})

    async def session_flow() -> None:
        params = StdioServerParameters(command=sys.executable, args=["-m", "clabe.cli", "mcp", "--url", url])
        async with stdio_client(params) as (read, write), ClientSession(read, write, elicitation_callback=elicit) as s:
            await s.initialize()
            names = {tool.name for tool in (await s.list_tools()).tools}
            assert {"get_run", "answer_prompt", "ask_the_user", "wait_for_prompt", "go_back", "abort_run"} <= names
            state = _payload(await s.call_tool("wait_for_prompt", {"timeout_seconds": 10}))
            assert state["workflow"] == "demo" and state["prompt"]["spec"]["label"] == "Subject"
            assert _payload(await s.call_tool("ask_the_user", {}))["ok"]
            state = _payload(await s.call_tool("wait_for_prompt", {"timeout_seconds": 10}))
            prompt = state["prompt"]
            assert prompt["kind"] == "number" and prompt["answer_schema"]["maximum"] == 500
            rejected = _payload(await s.call_tool("answer_prompt", {"prompt_id": prompt["prompt_id"], "value": 900}))
            assert not rejected["ok"] and "500" in rejected["reason"]
            answered = _payload(await s.call_tool("answer_prompt", {"prompt_id": prompt["prompt_id"], "value": 40}))
            assert answered["ok"]

    asyncio.run(session_flow())
    assert run.wait(10) and run.status is RunStatus.COMPLETED
    assert (run.output("subject"), run.output("trials")) == ("mouse-5", 40.0)


def _agui(url: str, messages: list[dict] | None = None) -> list[dict]:
    body = {"threadId": "t1", "runId": "r1", "messages": messages or [], "tools": [], "context": [], "state": {}}
    with requests.post(f"{url}/api/agui", json=body, stream=True, timeout=30) as response:
        response.raise_for_status()
        assert response.headers["content-type"].startswith("text/event-stream")
        return [json.loads(line[5:]) for line in response.iter_lines(decode_unicode=True) if line.startswith("data:")]


def test_the_run_as_an_ag_ui_agent(served):
    run, url = served
    events = _agui(url)
    types_ = [e["type"] for e in events]
    assert types_[0] == "RUN_STARTED" and types_[-1] == "RUN_FINISHED" and "STATE_SNAPSHOT" in types_
    call = next(e for e in events if e["type"] == "TOOL_CALL_START")
    assert call["toolCallName"] == "clabe_prompt"
    args = json.loads(next(e for e in events if e["type"] == "TOOL_CALL_ARGS")["delta"])
    assert (args["kind"], args["spec"]["label"]) == ("text", "Subject")

    # the next turn answers the tool call; the run moves on to the next prompt
    events = _agui(url, [{"id": "m1", "role": "tool", "toolCallId": call["toolCallId"], "content": '"mouse-6"'}])
    types_ = [e["type"] for e in events]
    assert {"STEP_FINISHED", "STEP_STARTED"} <= set(types_)
    next_call = next(e for e in events if e["type"] == "TOOL_CALL_START")
    assert next_call["toolCallId"] != call["toolCallId"]

    # a rejected answer is said, and the same prompt is asked again
    events = _agui(url, [{"id": "m2", "role": "tool", "toolCallId": next_call["toolCallId"], "content": "900"}])
    said = "".join(e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert "rejected" in said and "500" in said
    assert next(e for e in events if e["type"] == "TOOL_CALL_START")["toolCallId"] == next_call["toolCallId"]

    events = _agui(url, [{"id": "m3", "role": "tool", "toolCallId": next_call["toolCallId"], "content": "25"}])
    assert events[-1]["type"] == "RUN_FINISHED"
    assert run.wait(10) and run.output("trials") == 25.0
