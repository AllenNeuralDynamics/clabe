# Migrating to workflows, clients and the daemon

What changes for an experiment repository moving to this version, in the order you are likely to meet it. The
concepts behind the changes are in [Concepts](../concepts/index.md).

## 1. It still runs

An `@experiment` function keeps working unchanged: it runs as a workflow of one step. Start there, then split it into
steps when convenient (section 3).

## 2. Talking to the user

The frontend API is gone; ask through `clabe.ui`, which asks whatever is running (the run, or the launcher's client).

| Before | Now |
| --- | --- |
| `launcher.frontend.prompt_text(TextRequest(label=...))` (and every `prompt_*`) | `ui.text(...)`, or `ui.ask(TextRequest(...))` |
| `ui.prompt_confirm(ConfirmRequest(...))` (and every `ui.prompt_*`) | `ui.confirm(...)`, or `ui.ask(ConfirmRequest(...))` |
| `launcher.frontend.prompt_path(PathRequest(...))` | `ui.path(...)`, or `ui.ask(PathRequest(...))` |
| `Launcher(settings=..., frontend=...)` | `Launcher(settings=..., client=...)` |
| `ConsoleFrontend` / `TextualFrontend` / `make_frontend` | `ConsoleClient` / `TextualClient` / `make_client` |
| `ui.use_frontend(x)` / `ui.set_current_frontend(x)` | `ui.bind(ClientSession(client))` |
| `ui.NoFrontendError` | `ui.NoSessionError` |
| subclassing `FrontendBase` | implement the five-method `Client` protocol |
| a loop re-asking until the text parses (e.g. a date) | a validator on the request, or `ui.number(..., minimum=, maximum=)`; the run re-asks with the reason |

Answers come back validated and typed. See [Frontends](frontends.md).

## 3. Splitting an experiment into steps

```python
# before
@experiment(name="vr-foraging", exit_prompt_timeout=60)
async def vr_foraging(launcher: Launcher) -> None:
    session = SessionBuilder(launcher).build()
    store = behavior_store(session)
    trainer_state = store.resolve(TRAINER_STATE)
    rig = store.resolve(VR_RIG)
    if not confirm_session_info(launcher, session, trainer_state):
        return
    launcher.register_session(session, rig.data_directory)
    await asyncio.gather(bonsai_app.run_async(), fip_app.run_async())
    ...


# after
wf = Workflow("vr-foraging", exit_prompt_timeout=60)
wf.step(steps.session())


@wf.step
def store(session: Session) -> Store:
    return behavior_store(session)


@wf.step(title="Trainer state")
def trainer_state(store: Store) -> TrainerState:
    return store.resolve(TRAINER_STATE)


@wf.step
def rig(store: Store) -> AindVrForagingRig:
    return store.resolve(VR_RIG)


wf.step(steps.review("session", "trainer_state", "rig", back_to="session"))  # "Go back" replaces the early return
wf.step(steps.register())  # a barrier


@wf.step(title="Acquire", cancellable=True)
async def acquire(rig: AindVrForagingRig, session: Session, trainer_state: TrainerState) -> dict:
    return await concurrently(behavior=lambda: bonsai_app(...).run_async(), fip=lambda: fip_app(...).run_async())
```

Rules of thumb:

- One step per thing a user might want to redo or a failure might interrupt.
- A step returns what later steps need; several values → a model or a `NamedTuple`.
- "Would you like to ...?" confirms become `optional="..."` steps; best-effort work (`try/except: log`) becomes
  `on_error="warn"`; early `return`s on a "no" become `raise GoBack(...)` or `raise Abort(...)`.
- What must not run twice (registering the session, starting acquisition, transferring) is a `barrier`.
- Annotate parameters and returns, and add `clabe check experiments --strict` to CI.

See [Workflows](workflows.md).

## 4. Running on other rigs

`clabe.xml_rpc` is replaced by the [daemon](daemon.md)'s worker role and `clabe.remote`.

| Before | Now |
| --- | --- |
| `from clabe.xml_rpc import RemoteRepository, to_unc` | `from clabe.remote import RemoteRepository, to_unc` |
| `XmlRpcClient(XmlRpcClientSettings(server_url=..., token=...))` | `WorkerClient(WorkerClientSettings(url=..., token=...))` |
| `XmlRpcExecutor(client)` | `RemoteExecutor(client)` |
| `clabe.yml`: `xml_rpc_client: {server_url, token}` | `worker_client: {url, token, repository}` |
| `clabe.yml`: `xml_rpc_server: {...}` on the other PC | `daemon: {repositories: [{path: <checkout>}], ...}` on the other PC |
| `uv run clabe xml-rpc-server register-task` | `uv tool install aind-clabe` + `clabe daemon install` |
| port 25223 | port 8070 |
| `client.upload_file(...)` returned a response with `.path` | returns a `FileInfo` (`.path`, `.size`) |

`RemoteRepository.from_computer_name(...)`, `ensure_running()`, `verify()`, `register_session()`, `write_file()`,
`session_directory_unc` and `executor=remote` keep their meaning. New: the run **leases** the remote machine on its
first command, and cancelling a remote command stops it there. See [Remote execution](remote_execution.md).

## 5. Serving and the command line

| Before | Now |
| --- | --- |
| `clabe serve file.py` (textual-serve) | `clabe run file.py` serves the run by default (`--no-serve` to turn it off); open the printed URL |
| the `web` extra | removed; the web UI ships in the package |
| — | `clabe list`, `clabe check`, `clabe daemon`, `clabe hub`, `clabe mcp` (the `agents` extra) |

## 6. Records

Nothing to do: `events.jsonl`, `outputs.jsonl`, `artifacts/` and `run.json` are written next to `launcher.log`. The
`clabe.transcript` logger is gone; what the user saw and answered is in `events.jsonl`. See
[Run records](records.md).
