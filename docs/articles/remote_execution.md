# Running on other rigs

An experiment often needs more than one computer: one machine runs the behavior task, another acquires ephys or
imaging. CLABE lets a single launcher drive all of them. The launcher stays in charge of the session, and apps
that belong to another machine run there, while the rest of your experiment code stays unchanged.

There are three pieces, and each has one job:

| Piece | Job |
| --- | --- |
| The **clabe daemon** on the other machine | Runs commands there and keeps files, for the run that leased it. |
| [`RemoteRepository`][clabe.remote.RemoteRepository] on the launcher | Points at a checkout of **this repository** on that machine, checks it runs the same code, and runs apps in it. |
| **Network shares** | Let the launcher (and the watchdog) read the data the other machine saved. |

```python
remote = RemoteRepository.from_computer_name("rig-b", data_roots={r"D:\data": r"\\rig-b\data"})
remote.ensure_running()
remote.verify()
remote.register_session(session, r"D:\data")

await concurrently(behavior=local_app.run_async, ephys=lambda: remote_app.run_async(executor=remote))
```

A complete, runnable version is in the [remote execution example](../docs_examples/remote_execution.md).

## 1. Run the daemon on the other machine

Every rig PC runs the [clabe daemon](daemon.md), installed once with `uv tool install aind-clabe` and
`clabe daemon install` (it then starts at logon, in the user's desktop session, so apps it launches can show
windows and use the user's network credentials). Besides starting experiments of its own, it has a **worker** role:
it runs commands and keeps files for a run hosted on another machine.

List this repository's checkout on that machine in its `clabe.yml`, so the worker knows where to run commands and
which environment to use (the checkout's `.venv`):

```yaml
daemon:
  host: 0.0.0.0
  token: "<a secret shared with the launchers>"
  repositories:
    - path: C:/git/Aind.Experiment.VrForaging
  max_jobs: 4                    # commands running at the same time
  max_file_size: 52428800        # largest file transferred (50 MB)
  file_transfer_dir: D:/clabe-transfer
```

!!! warning "The token is the only protection"
    The worker runs **any command** it is given, as the user it runs as, and that user's network access comes with
    it. Anyone who has the token and can reach the port controls the machine. Keep the token out of the repository,
    for example in `./local/clabe.yml` or `%PROGRAMDATA%\clabe.yml`, which your deployment writes, and bind
    `0.0.0.0` only on the rig network.

### One run at a time: the lease

The first command a run sends **leases** the worker: until the run gives it back, the machine refuses commands from
any other run (and starts no experiment of its own), and its page shows "in use by `<launcher>`". The launcher renews
the lease in the background and gives it back when the run ends. If the run did not complete (it was aborted, or
failed), the commands still running there are stopped too — unless you pass `detach_on_abort=True` to
`RemoteRepository`, for recordings that must outlive the launcher.

If the launcher crashes, its jobs keep running and the lease expires after a minute. For five more minutes
(`daemon: lease_grace`) only the same run may take the machine back, so a restarted launcher can
[recover](#recovering-after-a-crash) them.

### Telemetry

If the `otel` section of `clabe.yml` enables telemetry, the daemon exports it too. Each command it runs becomes a
`clabe.worker.job` span with the command, working directory and exit code. The launcher sends its trace context with
each command, so jobs on the other machine appear **under the launcher's trace**, as one trace for the whole session.

## 2. Connect from the launcher

The client reads its settings from the `worker_client` section, so a launcher does not need to hard-code anything:

```yaml
worker_client:
  url: http://rig-b:8070
  token: "<the same secret>"
```

```python
remote = RemoteRepository(WorkerClient(WorkerClientSettings()), data_roots={r"D:\data": r"\\rig-b\data"})
```

[`RemoteRepository`][clabe.remote.RemoteRepository] takes these arguments:

| Argument | Meaning |
| --- | --- |
| `client` | The client for that machine's worker. Its `repository` setting names the checkout (the daemon's name for it); by default the daemon's first repository. |
| `path` | Where the repository is on that machine. Optional: it defaults to the worker's repository. When given, `verify()` checks it is that repository. |
| `local_path` | The local repository to compare with. Defaults to the current folder. |
| `data_roots` | Maps data folders on that machine to the network share that exposes them. Optional: other folders are reached through the drive's administrative share. Used by [`to_unc`](#reading-the-data-it-saved). |

When the other machine is described by a rig, connect by its computer name instead. The URL is built from the name
and port, the repository defaults to this repository's folder name, and the rest of the client settings, such as
the token, still come from `worker_client`:

```python
remote = RemoteRepository.from_computer_name(ephys_rig.computer_name)
```

`ensure_running()` fails with a `ConnectionError` naming the machine when its name does not resolve to an address,
for example because it is misspelled or on another network. To decide whether a rig is this machine, and so whether
to run its app locally instead, use `clabe.utils.is_this_computer(rig.computer_name)`.

### Starting the daemon if it is down

`remote.ensure_running()` pings the daemon. If it does not answer, it starts the `clabe-daemon` task **on that
machine** (`schtasks /run /s <host>`) and waits for it to respond. This needs administrator rights on the other
machine and a firewall that allows remote task scheduling. If you cannot have that, the logon trigger plus
restart-on-failure usually keeps the daemon up.

### Checking it runs the same code

`remote.verify()` fails if the two checkouts differ, and lists every difference at once. It compares:

- the worker's repository path (when you passed `path`);
- the repository's `origin` URL;
- the commit of the repository **and of every submodule**;
- uncommitted changes on the remote, unless you pass `allow_dirty=True`.

Because the lockfile is tracked, a matching clean commit also means the same locked dependencies. A different CLABE
version of the daemon is only logged as a warning.

## 3. Run apps on it

Build the app exactly as you would for a local run, and say where to run it:

```python
app = AindBehaviorServicesBonsaiApp(
    workflow=Path("./Aind.Physiology.Fip/src/main.bonsai"),
    executable=Path("./Aind.Physiology.Fip/.bonsai/bonsai.exe"),
    temp_directory=launcher.temp_dir,
    rig=fip_rig,
    session=session,
)
result = await app.run_async(executor=remote)
```

Without `executor`, the app runs locally as before. Apps validate their paths on the launcher machine when they are
built, which is meaningful after `verify()`: identical commits mean that what exists here exists there.

Running on the remote executor does three things on top of a local run:

1. **Commands run in the remote repository:** the `path` you gave, or the worker's repository, with its environment.
2. **Paths in your repository are rewritten.** Any argument that starts with the local repository's path, including
   inside `-p:Name=<path>` properties, is changed to the remote repository's path. Only whole folders match
   (`C:\repo` does not rewrite `C:\repo2`), and matching ignores case on Windows.
3. **Input files are copied.** An app lists the local files its command reads in
   [`Command.inputs`][clabe.apps.Command]. `AindBehaviorServicesBonsaiApp` lists its rig, session and task files.
   The executor sends their contents along with the command, the worker writes them to a temporary folder, points
   the command at those copies, and deletes them when the command exits.

### Things to know

- **Paths outside the repository are not rewritten.** They are sent as they are and must exist at the same
  location on the other machine.
- **Inputs only exist while the command runs.** If a workflow needs its rig or session files afterwards, it has to
  copy them into the session folder itself.
- **Inputs are text.** They are read as UTF-8, which suits the JSON models CLABE passes.
- **Its output is live.** Inside a run, a remote command shows as an activity tagged with the other machine's name,
  with its latest output lines (the web UI shows them as a collapsible tail). The full output comes back when it
  ends. The remote process cannot ask the user anything.
- **Cancelling stops it.** When the run is aborted (or a branch is stopped), the command is stopped on the other
  machine too.
- **It is on record.** The run's `run.json` lists each machine it used (`workers`: host, clabe version, the remote
  checkout as `verify()` found it) and each command it ran there, with its step.
- **The launcher machine needs what validation looks for**, such as `.bonsai/`. Pass `skip_validation=True` to the
  app if it does not have it.

### Running a command directly

Everything above is built on the [`WorkerClient`][clabe.remote.WorkerClient], which you can also use on its own:

```python
result = client.run_command(["python", "--version"])  # waits for the result
job = client.submit_command(["python", "slow_script.py"])  # returns a job id, poll with get_result(job.job_id)
client.upload_model(session, "session.json")  # files, up to max_file_size
```

### Recovering after a crash

If the launcher dies while the other machine records, its commands keep running there. A restarted launcher (or a
recovery workflow) reattaches to them with the crashed session's run id, from its `run.json`:

```python
remote = RemoteRepository.from_computer_name("rig-b")
for job in remote.reattach(crashed_run_id):  # takes the machine back for that run
    result = remote.wait_for(job.job_id)  # or await remote.wait_for_async(...)
```

## 4. Data that the other machine saves

The other machine will often record data too. Give it a session folder named like the launcher's:

```python
remote.register_session(session, r"D:\data")
```

This is the remote counterpart of [`Launcher.register_session`][clabe.launcher.Launcher.register_session]: it
creates `D:\data\<session name>` **on the other machine**. The data folder is the one on **that** machine, for
example the remote rig's `data_directory`. It is optional: nothing else needs it except the three properties below.

| Property | Example | Use it for |
| --- | --- | --- |
| `remote.session_directory` | `D:\data\2026-10-30_12-00-00_subject` | Telling the **other machine** where to write: app properties, a recorder's output folder. |
| `remote.session_directory_unc` | `\\rig-b\data\2026-10-30_12-00-00_subject` | Anything the **launcher** reads: the watchdog, data mappers. |
| `remote.data_directory` | `D:\data` | The root, as that machine sees it. |

Use the one that is valid on the machine that opens the path. A path given to the wrong machine simply does not
exist there. These are Windows paths, because they describe the other machine's file system, and they are
`PureWindowsPath` objects: wrap them in `Path(...)` or `str(...)` where you need to.

### Reading the data it saved

`session_directory_unc` translates the session folder with `remote.to_unc(path)`, which you can also use for other
paths. A path under one of the `data_roots` goes to that share; when roots are nested, the most specific one wins.
Any other path is reached through its drive's administrative share, e.g. `D:\data` on `rig-b` as `\\rig-b\D$\data`,
which every Windows machine has but which needs administrator rights there, like `ensure_running()`. Pass
`data_roots` when the reader is not an administrator, or the data lives on a dedicated share.

To find data saved earlier, for example when recovering a session after the launcher crashed, the other machine's
daemon does not need to be running: `clabe.remote.to_unc(path, data_roots, host=...)` does the same translation
without a connection.

The share must be readable by the account the reader runs as. That is the launcher's user for mappers, and the
watchdog's user for transfers, which are not always the same account.

Reading small files over a share is cheap: metadata mappers that only read a few XML or JSON files can run on the
launcher against the share. For anything heavy, run the step on the machine that holds the data, with
`app.run_async(executor=remote)`, so the bulk of the data never crosses the network.

## 5. Transferring both machines' data with the watchdog

[`WatchdogDataTransferService`][clabe.data_transfer.aind_watchdog.WatchdogDataTransferService] takes a list of
sources. Pass the launcher's session folder and the other machine's one through its share, and the watchdog gets one
manifest that covers both:

```python
watchdog = WatchdogDataTransferService(
    source=[launcher.session_directory, remote.session_directory_unc],
    settings=WatchdogSettings(destination=..., project_name=...),
    session=session,
)
watchdog.transfer()
```

Each source is searched for folders named after a modality (`behavior`, `fib`, ...) and for `*.json` files, so
folders found on both machines under the same modality name are all added to that modality.

- **Every source must be reachable.** If one cannot be found, `transfer()` fails before writing anything, and the
  error lists **all** unreachable sources.
- **The watchdog reads them later.** It copies the data at the manifest's `schedule_time`, under its own account.
  That account needs read access to the share, and the other machine has to be on at that time.
- **The manifest is always written.** `transfer()` writes the manifest first, then starts the watchdog service if it
  is not running. The service processes manifests that are already there when it starts, so a failure to start it is
  logged as an error that names the manifest, instead of raised.
- **Deleting sources.** `delete_modalities_source_after_success` deletes the original folders, including on the other
  machine, over the network.

To upload the other machine's modalities as their own job, e.g. so the behavior data does not wait for them, give them
their own upload group. See [Uploading data with the watchdog](data_transfer.md#data-recorded-on-another-machine).

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `401` errors from the client | The `worker_client` token differs from the daemon's. |
| `423 In use by ...` | Another run holds the machine's lease; it is freed when that run ends (or a minute after it crashed). |
| `404 No repository named ...` | List the checkout under `daemon: repositories:` on that machine, or set `worker_client.repository`. |
| `ensure_running()` times out | `clabe daemon install` was not run on that machine, the user is not logged on, or remote task scheduling is blocked. |
| `verify()` lists commits | The two machines are on different commits: update the other checkout and run `uv sync`. |
| A windowed app shows nothing on the rig | The daemon runs as a service, not from its logon task. |
| `Watchdog source directories not found or not reachable` | The share is not mapped for the watchdog's account, or the other machine is off. |
| A path from `session_directory` fails on the launcher | Use `session_directory_unc` for anything the launcher reads. |
