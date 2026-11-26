"""Live sessions in the hub (a run's /api/session, carried by its rig's status), and MCP at /mcp."""

import asyncio
import json
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clabe import ui
from clabe.hub import EnrollBody, Fleet, HubSettings, create_hub_app
from clabe.server import create_app, free_port
from clabe.workflow import Run, Workflow


def _workflow() -> Workflow:
    wf = Workflow("demo")

    @wf.step(title="Subject")
    def subject() -> str:
        return ui.text("Subject")

    return wf


class _Launcher:
    """What a launcher tells about its session (as ``Launcher.report_info``)."""

    def __init__(self) -> None:
        self.info: dict[str, object] = {"repository": "Aind.Behavior.Demo", "commit": "abc123"}

    def report_info(self) -> dict[str, object]:
        return dict(self.info)


def test_a_run_tells_its_session_as_it_goes():
    run = Run(_workflow())
    launcher = _Launcher()
    run.launcher = launcher
    client = TestClient(create_app(run, static_dir=None))
    run.start()
    try:
        deadline = time.monotonic() + 10
        while run.state().prompt is None and time.monotonic() < deadline:
            time.sleep(0.02)
        session = client.get("/api/session").json()
        assert session["experiment"] == "demo" and session["status"] == "running" and session["waiting"] is True
        assert session["repository"] == "Aind.Behavior.Demo" and session["session_id"] is None
        assert session["started_at"] is not None and session["finished_at"] is None
        launcher.info |= {"session_id": "mouse-7_2026-11-22", "subject": "mouse-7", "experimenter": ["ana"]}
        run.answer(run.state().prompt.prompt_id, "mouse-7")
        assert run.wait(10)
        session = client.get("/api/session").json()
        assert session["status"] == "completed" and session["finished_at"] is not None
        assert (session["subject"], session["experimenter"]) == ("mouse-7", ["ana"])
        assert session["steps_done"] == session["steps_total"] == 1
    finally:
        if not run.wait(0):
            run.abort("teardown")


def _session(run_id: str, **info) -> dict:
    return {
        "run_id": run_id,
        "experiment": "demo",
        "status": "running",
        "hostname": "RIG-3",
        "clabe_version": "0",
    } | info


def test_the_hub_shows_each_rigs_live_session(tmp_path: Path):
    answers = {"session": _session("r1"), "busy": True}

    def rig(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/daemon/info":
            run = {"status": "running", "experiment": "demo"} if answers["busy"] else None
            body = {
                "hostname": "RIG-3",
                "clabe_version": "0",
                "status": "busy" if answers["busy"] else "idle",
                "run": run,
            }
            return httpx.Response(200, json=body)
        if request.url.path == "/run/api/state":
            return httpx.Response(200, json={"status": "running", "workflow": "demo", "steps": [], "prompt": None})
        if request.url.path == "/run/api/session":
            return httpx.Response(200, json=answers["session"])
        return httpx.Response(404)

    settings = HubSettings(data_dir=tmp_path)
    fleet = Fleet(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(rig)))
    fleet.enroll(EnrollBody(id="rig-3", host="rig-3", port=24070))
    hub = TestClient(create_hub_app(fleet, static_dir=None, poll=False))
    asyncio.run(fleet.poll_once())
    assert hub.get("/api/hub/rigs").json()[0]["session"]["status"] == "running"
    answers["session"] = _session("r1", session_id="mouse-7_x", subject="mouse-7")
    asyncio.run(fleet.poll_once())
    assert hub.get("/api/hub/rigs").json()[0]["session"]["session_id"] == "mouse-7_x"
    answers["busy"] = False  # the run ended: nothing is kept
    asyncio.run(fleet.poll_once())
    assert hub.get("/api/hub/rigs").json()[0]["session"] is None


def test_the_hub_serves_mcp_for_every_rig(tmp_path: Path):
    pytest.importorskip("mcp")
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    run = Run(_workflow())
    rig = FastAPI()
    rig.mount("/run", create_app(run, static_dir=None))  # a daemon serves its run under /run/
    rig_port, hub_port = free_port("127.0.0.1", 18610), free_port("127.0.0.1", 18640)
    servers = [_serve(rig, rig_port)]
    fleet = Fleet(HubSettings(data_dir=tmp_path))
    fleet.enroll(EnrollBody(id="rig-3", host="127.0.0.1", port=rig_port))
    hub = create_hub_app(fleet, token="hub-secret", static_dir=None, poll=False,
                         self_url=f"http://127.0.0.1:{hub_port}")  # fmt: skip
    servers.append(_serve(hub, hub_port))
    run.start()
    url = f"http://127.0.0.1:{hub_port}/mcp"

    async def flow() -> None:
        auth = create_mcp_http_client(headers={"Authorization": "Bearer hub-secret"})
        async with (
            auth,
            streamable_http_client(url, http_client=auth) as (read, write, *_),
            ClientSession(read, write) as s,
        ):
            await s.initialize()
            names = {tool.name for tool in (await s.list_tools()).tools}
            assert {"list_rigs", "get_run", "answer_prompt"} <= names
            state = await s.call_tool("wait_for_prompt", {"timeout_seconds": 10, "rig": "rig-3"})
            prompt = json.loads(state.content[0].text)["prompt"]
            assert prompt["spec"]["label"] == "Subject"
            await s.call_tool("answer_prompt", {"prompt_id": prompt["prompt_id"], "value": "mouse-9", "rig": "rig-3"})

    try:
        assert httpx.post(url, json={}).status_code == 401  # the hub's token is required
        asyncio.run(flow())
        assert run.wait(10) and run.output("subject") == "mouse-9"
    finally:
        if not run.wait(0):
            run.abort("teardown")
        for server, thread in servers:
            server.should_exit = True
            thread.join(5)


def _serve(app, port: int):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    return server, thread


def test_a_run_tells_its_rig_and_subject_from_its_context(monkeypatch):
    from dataclasses import dataclass

    from clabe.workflow._context import RIG_NAME_ENV_VAR

    @dataclass
    class _Session:
        subject: str
        experimenter: list[str]

    monkeypatch.setenv(RIG_NAME_ENV_VAR, "RIG-3")
    wf = Workflow("demo")
    wf.step(lambda: _Session("mouse-9", ["bo"]), name="session")
    run = Run(wf)
    client = TestClient(create_app(run, static_dir=None))
    run.start()
    assert run.wait(10)
    session = client.get("/api/session").json()
    assert (session["rig"], session["subject"], session["experimenter"]) == ("RIG-3", "mouse-9", ["bo"])
