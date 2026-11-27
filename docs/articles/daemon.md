# The clabe daemon

The daemon runs on every rig PC. It lists the PC's experiments, starts them, and serves the current run at one address.

| URL | Shows |
|---|---|
| `http://<rig>:24070/` | The machine and its experiments |
| `http://<rig>:24070/run/` | The run in progress (web UI and API) |
| `http://<rig>:24070/docs` | The API |

To install it on a rig, use the one-line installer: [Deploy a rig](deployment.md).

## Repositories

A repository's workflows are those declared (or imported) in its top-level `main.py`. The daemon finds repositories
on its own:

- Every folder directly under a **root** (default `C:/git`) that has a `main.py` and a `pyproject.toml` depending on
  `aind-clabe`.
- Any repository listed under `repositories`, in any location.
- A repository's name is its folder's name. The first one found is the worker's default.

## Configure

The daemon reads environment variables of the rig's user, else the same `NAME=value` lines in
`%LOCALAPPDATA%\clabe\daemon.env` (environment variables win); never `clabe.yml`. None are required. Lists are
written as `a;b` or as JSON. Restart the daemon after changing them.

```bat
setx DAEMON_CLABE_TOKEN "..."                                  :: optional; the installer's -Token sets it
setx DAEMON_CLABE_REPOSITORY_ROOTS "C:/git;D:/experiments"     :: folders to search (default: C:/git)
setx DAEMON_CLABE_OTEL_ENDPOINT "collector:4317"               :: where to send its logs (service clabe-daemon)
```

| Variable | Default | What it does |
|---|---|---|
| `DAEMON_CLABE_TOKEN` | none | Optional: when set, every API call must carry it |
| `DAEMON_CLABE_REPOSITORY_ROOTS` | `C:/git` | Folders whose repositories are found |
| `DAEMON_CLABE_REPOSITORIES` | none | More repositories, anywhere |
| `DAEMON_CLABE_RUN_ARGUMENTS` | none | Added to every run it starts, e.g. `--skip-hardware-validation` |
| `DAEMON_CLABE_OTEL_ENDPOINT` | none | OTLP endpoint for its logs |
| `DAEMON_CLABE_PORT` | `24070` | The daemon's port |
| `DAEMON_CLABE_HOST` | `0.0.0.0` | Interface to bind (`127.0.0.1`: this PC only) |
| `DAEMON_CLABE_COMMAND` | `uv;run;--directory;{repository};clabe` | How clabe is called in a repository |
| `DAEMON_CLABE_RUNS_DIRECTORY` | `%TEMP%\clabe-daemon` | Logs of the runs it starts |
| `DAEMON_CLABE_REPORTS_DIRECTORY` | `%LOCALAPPDATA%\clabe\reports` | Issue reports waiting for the hub |
| `DAEMON_CLABE_MAX_JOBS` | `4` | Worker: commands run at once for a remote run |
| `DAEMON_CLABE_MAX_FILE_SIZE` | 50 MB | Worker: largest file a remote run uploads |
| `DAEMON_CLABE_FILE_TRANSFER_DIR` | `%TEMP%\clabe-worker` | Worker: where uploaded files go |
| `DAEMON_CLABE_LEASE_GRACE` | `300` | Worker: seconds a crashed run can take the machine back |
| `DAEMON_CLABE_HUBS` | none | [Hubs](#hubs) to enroll with, e.g. `https://hub-vm/clabe` |
| `DAEMON_CLABE_ENROLL_NAME` | this computer's name | The rig's name in the hubs |
| `DAEMON_CLABE_ENROLL_HOST` | this computer's name | Where the hubs reach the daemon (a hostname or an IP) |
| `DAEMON_CLABE_ENROLL_INTERVAL` | `300` | Seconds between enrollments (30 s at most after a failure) |

### Hubs

With `DAEMON_CLABE_HUBS` set, the daemon enrolls with each hub when it starts and every 5 minutes after that. The
[hub](hub.md#rigs-enroll-themselves) then follows the rig. A hub that is down, or refuses the rig (excluded, wrong
token), changes nothing on the rig: the daemon logs it and tries again. Deploying a rig with a file instead of
`setx`:

```ini
# %LOCALAPPDATA%\clabe\daemon.env
DAEMON_CLABE_HUBS=https://hub-vm/clabe
DAEMON_CLABE_TOKEN=...
```

## Commands

| Command | Does |
|---|---|
| `clabe daemon run [--open-browser]` | Run in the foreground, e.g. to try it |
| `clabe daemon open [--run-page]` | Open the daemon's page (or its run's) signed in, e.g. from a desktop shortcut |
| `clabe daemon deploy [--version V \| --lock REPO \| --package SPEC] [--hubs H] [--force]` | Install that clabe for the daemon and switch it there; nothing when it already runs it. See [Deploy a rig](deployment.md#update-and-go-back) |
| `clabe daemon install` | Register the `clabe-daemon` task (at logon and every 5 minutes; see [Running hidden](#running-hidden)) to run this clabe, and start it |
| `clabe daemon stop [--force] [--if-installed] [--timeout 15]` | Disable and end the task, and wait until the port is free: it stays stopped until `start`, `restart` or `deploy`. Refused during a session unless `--force`; `--if-installed`: no task is not an error |
| `clabe daemon start [--timeout 60]` | Enable and start the task, and wait until the daemon answers |
| `clabe daemon restart [--force] [--timeout 60]` | `stop`, then `start`; refused during a session unless `--force` |
| `clabe daemon status` | The task, whether the daemon answers, and the reports waiting |
| `clabe daemon upgrade` | Deprecated. Use `clabe daemon deploy --version <version>` |
| `clabe daemon uninstall` | Stop it and remove the task |

- It runs as the logged-on user (not as a service), so experiments can open windows.
- It runs without a window: see [Running hidden](#running-hidden).
- Logs go to `%LOCALAPPDATA%\clabe\daemon.log`. They are also sent through OpenTelemetry when
  `DAEMON_CLABE_OTEL_ENDPOINT` is set.
- To update, run `clabe daemon deploy --version <version>` (or [the installer](deployment.md#3-install)). The new
  clabe installs in its own environment while the daemon keeps running, then the daemon restarts on it.

## Running hidden

The task runs `clabe-daemon.exe run`: the same daemon, from an executable without a console window, so there is no
window to close by mistake. What it starts (uv, git, the run's `clabe run`) gets no window either; the windows
experiments open (Bonsai) show as usual. Its output is in `daemon.log` (and OpenTelemetry).

It comes back by itself:

- **Not running** (it crashed, or was ended in Task Manager): the task starts it again within 5 minutes. A start
  while it runs does nothing.
- **Hanging**: a watchdog in the daemon checks its own page every 30 s; after 2 minutes without an answer it logs why
  and ends the daemon, which the task then starts again.
- **Stopped on purpose** (`clabe daemon stop`): the task is disabled, so it stays stopped until `clabe daemon start`,
  `restart` or `deploy`.

From the [hub](hub.md#restarting-a-rigs-daemon), a rig's daemon can be restarted whether it answers or not.

Older clabe versions (before 0.18) have no `clabe-daemon.exe`: deployed there, the task runs `clabe.exe daemon run`,
in a console window, as before.

## Starting a run

When a run is started from the page or with `POST /api/daemon/runs`, the daemon runs this in the repository:

```bash
uv run --directory <repository> clabe run main.py --experiment-name <name> --frontend headless --serve ...
```

- The run uses the repository's own locked environment (and its clabe version).
- Prompts are answered in the web UI.
- Only one run at a time: a second start returns `409`.

## Runs started from a terminal

A `clabe run` started in a terminal (or from a [desktop shortcut](#desktop-shortcuts)) registers itself with the PC's
daemon:

- The daemon serves it under `/run/` and reports the PC as busy, so the hub shows the run.
- If there is no daemon, or the daemon is busy, the run continues but the daemon does not show it. Registering never
  waits more than a second.
- The daemon's page cannot stop it. Abort it from the run itself.

## Issue reports

"Report an issue" saves a zip (`report.json` and the run's records) in `DAEMON_CLABE_REPORTS_DIRECTORY`.

- The [hub](hub.md) collects each report and then deletes it from the rig. Without a hub, reports stay on the rig.
- `GET /api/daemon/info` returns how many are waiting (`reports_waiting`).

## Desktop shortcuts

To add a desktop icon that runs one of a repository's experiments in a terminal, run this in the repository:

```bash
uv run clabe shortcut --experiment-name vr-foraging --name "VR Foraging"
```

## API

| Endpoint | Does |
|---|---|
| `GET /api/daemon/info` | Idle, busy or leased |
| `GET /api/daemon/repositories` | The repositories |
| `GET /api/daemon/repositories/{name}/experiments` | Their experiments |
| `POST /api/daemon/runs` | Start a run |
| `GET /api/daemon/runs/current` | The current run, with the end of its output |
| `POST /api/daemon/restart[?force=true]` | Restart: the daemon answers `202`, ends itself, and its task starts a new one within seconds. `409` during a session unless `force` (the run is stopped), or when the task did not start it |
| `POST /api/daemon/runs/current/stop` | Stop it (only runs the daemon started) |
| `POST /api/daemon/runs/attached` | A run started from a terminal announces itself |
| `DELETE /api/daemon/runs/attached/{pid}` | It has ended |
| `GET /api/daemon/reports` | The issue reports waiting here |
| `GET /api/daemon/reports/{id}` | One, as a zip |
| `DELETE /api/daemon/reports/{id}` | Delete it (the hub, once stored) |
| `/run/api/...` | The run's own API (see [Frontends](frontends.md)) |
