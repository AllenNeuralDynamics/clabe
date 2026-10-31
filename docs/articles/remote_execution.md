# Running on other rigs

An experiment often needs more than one computer: one machine runs the behavior task, another acquires ephys or
imaging. CLABE lets a single launcher drive all of them. The launcher stays in charge of the session, and apps
that belong to another machine run there, while the rest of your experiment code stays unchanged.

There are three pieces, and each has one job:

| Piece | Job |
| --- | --- |
| The **XML-RPC server** on the other machine | Receives commands and runs them there. |
| [`RemoteRepository`][clabe.xml_rpc.RemoteRepository] on the launcher | Points at a checkout of **this repository** on that machine, checks it runs the same code, and runs apps in it. |
| **Network shares** | Let the launcher (and the watchdog) read the data the other machine saved. |

```python
remote = RemoteRepository(
    XmlRpcClient(XmlRpcClientSettings(server_url="http://rig-b:25223", token=token)),
    data_roots={r"D:\data": r"\\rig-b\data"},
)
remote.ensure_running()
remote.verify()
remote.register_session(session, r"D:\data")

await asyncio.gather(local_app.run_async(), remote_app.run_async(executor=remote))
```

A complete, runnable version is in the [remote execution example](../docs_examples/remote_execution.md).

## 1. Run the server on the other machine

The server is part of CLABE, so it runs from the same checkout as the experiment. Start it from the repository
folder:

```bash
uv run clabe xml-rpc-server serve
```

### Settings

The server reads the `xml_rpc_server` section of [`clabe.yml`](configuration.md), the same way every other service
does. Keyword arguments on the command line (`--port 9000`) win over the file.

```yaml
xml_rpc_server:
  token: "<a secret shared with the launchers>"   # required
  port: 25223
```

| Setting | Default | Meaning |
| --- | --- | --- |
| `token` | none, **required** | Shared secret every request must carry. |
| `address` | `0.0.0.0` | Interface to listen on. |
| `port` | `25223` | Port to listen on. |
| `working_dir` | the folder the server starts in | Folder commands run in, unless a call passes its own `cwd`. |
| `max_workers` | `4` | How many commands run at the same time. |
| `max_file_size` | 5 MB | Largest file the transfer calls accept. |
| `file_transfer_dir` | the temp folder | Where uploaded files are written. |

!!! warning "The token is the only protection"
    The server runs **any command** it is given, as the user it runs as, and that user's network access comes with
    it. Anyone who has the token and can reach the port controls the machine. For that reason the token is
    required: there is no default, and an empty one is rejected. Keep it out of the repository, for example in
    `./local/clabe.yml` or `%PROGRAMDATA%\clabe.yml`, which your deployment writes.

If no token is configured, `serve` prompts for one when you run it in a console, and exits with an error that names
the setting when it cannot prompt (for example in a scheduled task). `--non-interactive` always fails instead of
prompting.

### Starting it automatically

On a rig you do not want to start the server by hand. Register it once as a Windows scheduled task, from the
repository folder and inside its environment:

```bash
uv run clabe xml-rpc-server register-task
```

This creates a task named `ClabeXmlRpcServer` (the name is fixed) and starts it. The task:

- starts at logon, **in the user's desktop session**, so apps it launches can show windows and use the
  user's network credentials. A Windows service would not be able to do either;
- starts in the folder you registered it from, so the server finds that folder's `clabe.yml` and its token;
- has no time limit and is restarted after a failure;
- reads port, address and token from `clabe.yml` each time it starts, so changing them does not need a new
  registration.

`uv run clabe xml-rpc-server unregister-task` removes it. Only one task exists per machine, so registering again
replaces the old one.

### Telemetry

If the `otel` section of `clabe.yml` enables telemetry, the server exports it too. Each command it runs becomes an
`xml_rpc.job` span with the command, working directory and exit code, and a non-zero exit marks the span as failed.
The launcher sends its trace context with each command, so jobs on the other machine appear **under the launcher's
trace**, as one trace for the whole session.

## 2. Connect from the launcher

The client reads its settings from the `xml_rpc_client` section, so a launcher does not need to hard-code anything:

```yaml
xml_rpc_client:
  server_url: http://rig-b:25223
  token: "<the same secret>"
```

```python
remote = RemoteRepository(XmlRpcClient(XmlRpcClientSettings()), data_roots={r"D:\data": r"\\rig-b\data"})
```

[`RemoteRepository`][clabe.xml_rpc.RemoteRepository] takes these arguments:

| Argument | Meaning |
| --- | --- |
| `client` | The client for that machine's server. |
| `path` | Where the repository is on that machine. Optional: it defaults to the server's working directory. When given, `verify()` checks the server runs from it. |
| `local_path` | The local repository to compare with. Defaults to the current folder. |
| `data_roots` | Maps data folders on that machine to the network share that exposes them. Used by [`to_unc`](#reading-the-data-it-saved). |

When the other machine is described by a rig, connect by its computer name instead. The server URL is built from the
name and port, and the rest of the client settings, such as the token, still come from `xml_rpc_client`:

```python
remote = RemoteRepository.from_computer_name(ephys_rig.computer_name, data_roots={r"D:\data": r"\\ephys-pc\data"})
```

`ensure_running()` fails with a `ConnectionError` naming the machine when its name does not resolve to an address,
for example because it is misspelled or on another network.

### Starting the server if it is down

`remote.ensure_running()` pings the server. If it does not answer, it starts the `ClabeXmlRpcServer` task **on that
machine** (`schtasks /run /s <host>`) and waits for the server to respond. This needs administrator rights on the
other machine and a firewall that allows remote task scheduling. If you cannot have that, the logon trigger plus
restart-on-failure usually keeps the server up.

### Checking it runs the same code

`remote.verify()` fails if the two checkouts differ, and lists every difference at once. It compares:

- the server's working directory (when you passed `path`);
- the repository's `origin` URL;
- the commit of the repository **and of every submodule**;
- uncommitted changes on the remote, unless you pass `allow_dirty=True`.

Because the lockfile is tracked, a matching clean commit also means the same locked dependencies. A different CLABE
version is only logged as a warning: the remote is then used on a best-effort basis.

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

1. **Commands run in the remote repository:** the `path` you gave, or the server's working directory.
2. **Paths in your repository are rewritten.** Any argument that starts with the local repository's path, including
   inside `-p:Name=<path>` properties, is changed to the remote repository's path. Only whole folders match
   (`C:\repo` does not rewrite `C:\repo2`), and matching ignores case on Windows.
3. **Input files are copied.** An app lists the local files its command reads in
   [`Command.inputs`][clabe.apps.Command]. `AindBehaviorServicesBonsaiApp` lists its rig, session and task files.
   The executor sends their contents along with the command, the server writes them to a temporary folder, points
   the command at those copies, and deletes them when the command exits.

### Things to know

- **Paths outside the repository are not rewritten.** They are sent as they are and must exist at the same
  location on the other machine.
- **Inputs only exist while the command runs.** If a workflow needs its rig or session files afterwards, it has to
  copy them into the session folder itself.
- **Inputs are text.** They are read as UTF-8, which suits the JSON models CLABE passes.
- **Output comes back when the command ends.** There is no live output, and the remote process cannot ask the user
  anything.
- **The launcher machine needs what validation looks for**, such as `.bonsai/`. Pass `skip_validation=True` to the
  app if it does not have it.

### Running a command directly

Everything above is built on the client, which you can also use on its own:

```python
result = client.run_command(["python", "--version"])        # waits for the result
job = client.submit_command(["python", "slow_script.py"])   # returns a job id, poll with get_result(job.job_id)
client.upload_model(session, "session.json")                 # small files, up to max_file_size
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

`session_directory_unc` needs the data folder to be one of the `data_roots`. For other paths, translate them
yourself with `remote.to_unc(path)`. When roots are nested, the most specific one wins.

To find data saved earlier, for example when recovering a session after the launcher crashed, the other machine's
server does not need to be running: `clabe.xml_rpc.to_unc(path, data_roots)` does the same translation without a
connection.

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

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `No authentication token configured` | Add `token` to the `xml_rpc_server` section of `clabe.yml` where the server starts, or pass `--token`. |
| Authentication errors from the client | The `xml_rpc_client` token differs from the server's. |
| `ensure_running()` times out | The task is not registered on that machine, the user is not logged on, or remote task scheduling is blocked. |
| `verify()` lists commits | The two machines are on different commits: update the other checkout and run `uv sync`. |
| A windowed app shows nothing on the rig | The server runs as a service, not from the logon task. |
| `Watchdog source directories not found or not reachable` | The share is not mapped for the watchdog's account, or the other machine is off. |
| A path from `session_directory` fails on the launcher | Use `session_directory_unc` for anything the launcher reads. |
