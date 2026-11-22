# Machines: run servers, daemons, workers and the hub

A session involves more than one process and often more than one computer. Each has one role.

## The run server

Every run is served over HTTP while it executes: a **snapshot** of its state, a resumable **stream** of its events
(Server-Sent Events), and **commands** (answer, go back, retry, skip, abort). The API is described by OpenAPI at
`/docs`. The web UI is a client of exactly this API, and so is everything below. `clabe run` serves the run by
default, on localhost unless told otherwise, protected by a token when one is set.

## The daemon

Every rig PC runs one **daemon**, started at logon in the user's session (experiments open windows on the rig's
desktop). It has two roles.

**Host.** It knows the experiment repositories of the machine and lists their workflows. Starting one runs
`clabe run` in that repository's own environment (`uv run`), headless, and the daemon proxies the run under `/run/`
— so the machine has one stable address whatever runs on it. One run at a time.

**Worker.** It runs commands and keeps files for a run hosted on **another** machine: the second PC of a two-PC
rig, for example, that acquires ephys while the first runs behavior. The run reaches it through
[`RemoteRepository`](../articles/remote_execution.md), which checks the remote checkout runs the same commit and runs
apps there as if they were local.

## Leases

A machine should serve one session at a time. A run that uses a worker takes its **lease**: until it gives it back,
the worker refuses other runs and its own host starts no experiment; its page says who holds it. The run renews the
lease in the background and gives it back when it ends, stopping its jobs there unless it completed. If the run's
machine crashes the lease expires, the jobs carry on, and for a grace period only the same run may take the worker
back — so a restarted launcher can reattach to them.

## The hub

The **hub** is an optional service, typically in Docker on a server. It follows a list of rigs through their daemons
and shows the **fleet** (what each rig runs, which step, who leases it, which are offline) and the **inbox** (every
run waiting for an answer, longest first). It also re-exposes each rig under `/rigs/<id>/`, so the web UI, scripts and
agents work fleet-wide with one address and one token. Rigs never depend on it.

## Agents

Agents are clients too. `clabe mcp` serves a run, a daemon or the hub to an MCP client, with tools to read a run,
wait for a prompt, answer it (or ask the agent's user), and command the run. Every run also speaks AG-UI, for agent
UIs. They use the same commands, validation and records as any other client; an answer from an agent is recorded as
such.

## Tokens and topology

Each server checks one shared token (`Authorization: Bearer ...` or `?token=`). A run started by the daemon gets a
private token only the daemon knows; the daemon checks its own and forwards. The hub holds the rigs' tokens and checks
its own. Connections always go from the observer to the rig: rig → nothing, hub → rigs, browser → rig or hub.

## Where things run

| Process | Machine | Started by |
|---|---|---|
| Launcher, run, run server | the rig PC running the session | `clabe run`, or the daemon |
| Daemon (host + worker) | every rig PC | Task Scheduler at logon; deployed over SSH ([Deploying](../articles/deployment.md)) |
| Apps (Bonsai, Open Ephys) | the rig PC, or a worker | a step |
| Hub | a server | Docker |
| Web UI | the browser | the page served by any of the above |
