# Migrate an experiment

Move an experiment repository to workflows, clients and the daemon, one step at a time. Background:
[Concepts](../concepts/index.md).

## 1. Run it unchanged

An `@experiment` function still works: it runs as a workflow of one step.

## 2. Replace the frontend calls

Ask through `clabe.ui`; answers come back validated and typed. See [Frontends](frontends.md).

| Before | Now |
| --- | --- |
| `launcher.frontend.prompt_text(TextRequest(label=...))` (every `prompt_*`) | `ui.text(...)` or `ui.ask(TextRequest(...))` |
| `ui.prompt_confirm(ConfirmRequest(...))` (every `ui.prompt_*`) | `ui.confirm(...)` or `ui.ask(ConfirmRequest(...))` |
| `launcher.frontend.prompt_path(PathRequest(...))` | `ui.path(...)` or `ui.ask(PathRequest(...))` |
| `Launcher(settings=..., frontend=...)` | `Launcher(settings=..., client=...)` |
| `ConsoleFrontend` / `TextualFrontend` / `make_frontend` | `ConsoleClient` / `TextualClient` / `make_client` |
| `ui.use_frontend(x)` / `ui.set_current_frontend(x)` | `ui.bind(ClientSession(client))` |
| `ui.NoFrontendError` | `ui.NoSessionError` |
| subclassing `FrontendBase` | implement the `Client` protocol |
| a loop re-asking until the text parses | a validator on the request, or `ui.number(..., minimum=, maximum=)` |

## 3. Split it into steps

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

| Before | Now |
| --- | --- |
| a block a user might redo, or a failure might interrupt | its own step |
| several values passed on | return a model or a `NamedTuple` |
| "Would you like to ...?" confirm | `optional="..."` on the step |
| `try/except: log` | `on_error="warn"` |
| early `return` on a "no" | `raise GoBack(...)` or `raise Abort(...)` |
| register, start acquisition, transfer | a `barrier` step |
| `ResourceMonitor` and its constraints | checks in `steps.preflight(...)` (or `checks.run(...)`): see [Reusable steps and checks](reusable_steps.md) |

- Annotate parameters and returns, and add `clabe check experiments --strict` to CI.
- See [Workflows](workflows.md).

## 4. Replace `clabe.xml_rpc`

| Before | Now |
| --- | --- |
| `from clabe.xml_rpc import RemoteRepository, to_unc` | `from clabe.remote import RemoteRepository, to_unc` |
| `XmlRpcClient(XmlRpcClientSettings(server_url=..., token=...))` | `WorkerClient(WorkerClientSettings(url=..., token=...))` |
| `XmlRpcExecutor(client)` | `RemoteExecutor(client)` |
| `xml_rpc_client: {server_url, token}` | `worker_client: {url, token, repository}` |
| `xml_rpc_server: {...}` on the other PC | the daemon there, with the checkout under `C:/git` (found by itself) |
| `uv run clabe xml-rpc-server register-task` | the [one-line installer](deployment.md) (or `uv tool install aind-clabe` + `clabe daemon install`) |
| port 25223 | port 24070 |
| `client.upload_file(...)` response `.path` | a `FileInfo` (`.path`, `.size`) |

- `from_computer_name`, `ensure_running`, `verify`, `register_session`, `write_file`, `session_directory_unc` and
  `executor=remote` work as before.
- New: a run leases the remote PC, and aborting stops its commands. See [Run on two PCs](remote_execution.md).

## 5. Update the command line

| Before | Now |
| --- | --- |
| `clabe serve file.py` | `clabe run file.py` (serves by default; `--no-serve` to turn off) |
| the `web` extra | removed; the web UI ships in the package |
| — | `clabe list`, `clabe check`, `clabe replay`, `clabe daemon`, `clabe hub`, `clabe mcp` (`agents` extra) |

## 6. Records

Nothing to do: `events.jsonl`, `outputs.jsonl`, `artifacts/` and `run.json` sit next to `launcher.log`. The
`clabe.transcript` logger is gone; use `events.jsonl`. See [Run records](records.md).
