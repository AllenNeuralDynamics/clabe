# Daemon reference

Settings, commands and API of the daemon. [Set up a rig](../rigs/set-up.md) explains what it does.

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
| `DAEMON_CLABE_STOP_GRACE` | `30` | Seconds a stopped run has to abort and close before it is ended |
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
| `clabe daemon deploy [--version V \| --commit C \| --lock REPO \| --package SPEC] [--hubs H] [--force]` | Install that clabe for the daemon and switch it there; nothing when it already runs it. See [Deploy](#deploy) |
| `clabe daemon install` | Register the `clabe-daemon` task (at logon and every 5 minutes) and start it |
| `clabe daemon stop [--force] [--if-installed] [--timeout 15]` | Disable and end the task, and wait until the port is free; refused during a session unless `--force`; `--if-installed`: no task is not an error |
| `clabe daemon start [--timeout 60]` | Enable and start the task, and wait until the daemon answers |
| `clabe daemon restart [--force] [--timeout 60]` | `stop`, then `start`; refused during a session unless `--force` |
| `clabe daemon status` | The task, whether the daemon answers, and the reports waiting |
| `clabe daemon view` | Show the terminal UI of the run it hosts in this window; closing the window stops the run |
| `clabe daemon upgrade` | Deprecated. Use `clabe daemon deploy --version <version>` |
| `clabe daemon uninstall` | Stop it and remove the task |

- It runs as the logged-on user (not as a service), so experiments can open windows.
- Logs go to `%LOCALAPPDATA%\clabe\daemon.log`. They are also sent through OpenTelemetry when
  `DAEMON_CLABE_OTEL_ENDPOINT` is set.
- To update, run `clabe daemon deploy --version <version>` (or [the installer](#installer)). The new
  clabe installs in its own environment while the daemon keeps running, then the daemon restarts on it.

## Installer { #installer }

The installer:

1. Installs uv if it is missing. Stops if the daemon runs a session (busy or leased) and `-Force` is not set.
2. Sets `DAEMON_CLABE_TOKEN` and `%PROGRAMDATA%\clabe.yml`, when given.
3. Installs or updates the `clabe` command to the latest release, or to the build of `-Commit` or `-Lock` (`uv tool install --upgrade --force`), and adds it to the user's PATH (`uv tool update-shell`).
4. Runs [`clabe daemon deploy`](#deploy), which installs the daemon's clabe and starts the daemon.

Running it again is safe: the same command installs, updates and repairs.

| Parameter | Variable | Meaning |
|---|---|---|
| `-Version` | `DAEMON_CLABE_VERSION` | clabe release for the daemon (default: latest). **Pin it on rigs.** |
| `-Commit` | `DAEMON_CLABE_COMMIT` | A commit of clabe's main branch: its [build](#builds-of-main) |
| `-Lock` | `DAEMON_CLABE_LOCK` | A repository (or its `uv.lock`): the clabe it locks (a release or a build) |
| `-Package` | `DAEMON_CLABE_PACKAGE` | Any uv requirement instead, e.g. `aind-clabe[frontend,otel,agents]==0.18.0` |
| `-Extras` | `DAEMON_CLABE_EXTRAS` | aind-clabe's extras for the daemon (default `frontend,otel`) |
| `-Hubs` | `DAEMON_CLABE_DEPLOY_HUBS` | Hubs to enroll with, `;`-separated; `none` for no hub (default: unchanged) |
| `-Token` | `DAEMON_CLABE_TOKEN` | Daemon token, saved as the user's `DAEMON_CLABE_TOKEN` |
| `-Config` | `DAEMON_CLABE_CONFIG` | The experiments' `clabe.yml` to install as `%PROGRAMDATA%\clabe.yml`; `-` reads it from standard input |
| `-CliPackage` | `DAEMON_CLABE_CLI_PACKAGE` | uv requirement of the `clabe` command (default: the build of `-Commit`, or the build `-Lock` pins; else the latest release) |
| `-StopDaemon` | `DAEMON_CLABE_STOP=1` | Only install the `clabe` command and stop the daemon; deploy later |
| `-Force` | `DAEMON_CLABE_FORCE=1` | Stop or restart the daemon even during a session (it is interrupted) |
| `-DryRun` | `DAEMON_CLABE_DRY_RUN=1` | Print the commands only |
| `-Timeout` | | Seconds to wait for the daemon (default 120) |

```powershell
# with an environment variable
$env:DAEMON_CLABE_VERSION = "0.18.0"; irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1 | iex
# with parameters
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1))) -Version 0.18.0
```

## Deploy

`clabe daemon deploy` installs a clabe (a release or a [build](#builds-of-main)) for the daemon and switches the
daemon to it.

- Each clabe gets its own environment in `%LOCALAPPDATA%\clabe\versions` (e.g. `0.18.0-frontend-otel`), and the
  `clabe-daemon` task runs that environment's `clabe.exe`.
- A new clabe installs while the daemon keeps running. On Windows a running `clabe.exe` locks its own files, but the
  new install touches none of them. The daemon is down only during its restart.
- Before the switch, deploy checks that the daemon can start from the new environment. An install that fails or is
  interrupted leaves the daemon as it was.
- The daemon restarts only when its clabe or its hubs change. Otherwise deploy starts it if it is stopped, or does
  nothing. `--restart` restarts it anyway.
- During a session (busy or leased), deploy refuses before changing anything, unless `--force`.
- If the new clabe does not answer after `--timeout`, the previous one is started again and deploy exits non-zero.
- The last 3 environments are kept, so going back to one of them is just a restart.
- `--dry-run` says what would be done.
- This is the daemon's own install. Experiments still run in their repository's environment.

## Builds of main

CI builds a wheel of every commit pushed to main, web UI included, into one pre-release,
[`builds`](https://github.com/AllenNeuralDynamics/clabe/releases/tag/builds). Its version is the release it follows
plus the commit (`0.18.0rc8+g82d2107…`). The newest 100 are kept.

- `--commit 82d2107` (7 to 40 characters) installs that commit's build, in the environment `git-<commit>`. It looks
  the build up through GitHub's API (60 requests an hour per address without a token).
- An experiment pins one with the wheel's URL in `[tool.uv.sources]`. `--lock` then installs the same wheel,
  without asking GitHub.
- The installer's `-Commit`, and its `-Lock` on such a repository, also install that build as the `clabe` command.

## Over SSH

```bash
ssh rig-3 clabe daemon status      # the task, and whether the daemon answers (idle, busy, leased)
ssh rig-3 clabe daemon restart     # refused during a session; --force interrupts it
ssh rig-3 clabe daemon stop        # same; it stays stopped until `clabe daemon start`
ssh rig-3 clabe daemon uninstall   # stop the daemon and remove the task
ssh rig-3 type %LOCALAPPDATA%\clabe\daemon.log
```

- Do not start the daemon with `clabe daemon run` over SSH. Started that way, it cannot show windows and stops when
  the SSH session disconnects.
- To check that rigs are up, the hub shows each rig as online, offline, idle, busy or leased. `clabe daemon status`
  checks a single rig.
- A daemon that stops or hangs comes back by itself ([Set up a rig](../rigs/set-up.md#install)). The hub can also
  restart it ([Manage rigs](../rigs/hub.md#manage-rigs)).
- Without SSH, a rig admin can run `schtasks /S rig-3 /Run /TN clabe-daemon`.
- `-Config -` reads the rig's `clabe.yml` from the pipe, so you do not copy the file first. Leave it out to keep the
  rig's current `clabe.yml`.
- Pass the token through the SSH session's environment, or set it once on the rig. Do not put it on the command line.
- A new SSH session may not have `%USERPROFILE%\.local\bin` (where `clabe.exe` is) on its PATH yet: call it by its
  full path, or prepend that folder.

### From a deployment pipeline

To update an experiment repository and the daemon together, stop the daemon first: a session must not start while
the repository's environment is being rebuilt. For example:

```powershell
# 1. Install or update the `clabe` command and stop the daemon (nothing to stop on a new rig).
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1))) -StopDaemon
# 2. Update the repository and its environment (git, uv sync...).
# 3. Run the clabe it locks, enrolled with the hubs; this starts the daemon again.
clabe daemon deploy --lock C:/git/Aind.Experiment.X --hubs "http://hub-vm/clabe/"
```

## Starting a run

When a run is started from the page or with `POST /api/daemon/runs`, the daemon runs this in the repository:

```bash
uv run --directory <repository> clabe run main.py --experiment-name <name> --frontend headless --serve ...
```

- The run uses the repository's own locked environment (and its clabe version).
- Prompts are answered in the web UI.
- **Show on the rig** (`frontend`) also shows the run on the rig's screen: `tui` shows the terminal UI in a window
  (see [The terminal UI on the rig](#the-terminal-ui-on-the-rig)), `browser` opens the run's page in the default
  browser, `none` (default) shows nothing. Through the hub, the page offers the rig's last choice.
- Only one run at a time: a second start returns `409`.
- A restarted daemon finds the run it had started again.

## Stopping a run

Stop (the page, the hub, `POST /api/daemon/runs/current/stop`) asks the run to abort and close. A run that has not
ended within `DAEMON_CLABE_STOP_GRACE` seconds is ended.

A run ends every process it started, however it ends (stopped, crashed, ended from the Task Manager): nothing it
started keeps the rig's hardware. Waterlog, and the browser `--open-browser` opens, stay open.

## The terminal UI on the rig

`clabe run` at the rig (in a terminal, or from a [desktop shortcut](../rigs/set-up.md#run-an-experiment)) gives the
run to the daemon, which shows it in the same window:

- Closing the window stops the run.
- A window that dies without closing (ended from the Task Manager) leaves the run going. The next `clabe run` asks:

  ```text
   A previous run was not closed: Aind.Experiment.Vr · vr · started 15:42
     [R] Resume    [A] Abort it and start a new run    [Q] Quit
  ```

  For a run started remotely it says `A run started remotely is in progress`. Abort stops it, then starts the new
  run.
- When the daemon does not answer, `clabe run` starts its task and waits up to 10 seconds. Without a daemon, outside
  its repositories, on a rig reserved remotely, or with `--here`, the run happens in the window and registers itself
  with the daemon, which serves it under `/run/` and reports the PC as busy.

## Issue reports

"Report an issue" saves a zip (`report.json` and the run's records) in `DAEMON_CLABE_REPORTS_DIRECTORY`.

- The [hub](../rigs/hub.md) collects each report and then deletes it from the rig. Without a hub, reports stay on the rig.
- `GET /api/daemon/info` returns how many are waiting (`reports_waiting`).

## API

| Endpoint | Does |
|---|---|
| `GET /api/daemon/info` | Idle, busy or leased |
| `GET /api/daemon/repositories` | The repositories |
| `GET /api/daemon/repositories/{name}/experiments` | Their experiments |
| `POST /api/daemon/runs` | Start a run: `{"repository", "file", "experiment", "frontend", "window", "arguments"}` |
| `GET /api/daemon/runs/current` | The current run, with the end of its output |
| `POST /api/daemon/restart[?force=true]` | Restart it; `409` during a session unless `force` |
| `POST /api/daemon/runs/current/stop[?wait=true]` | Stop it (see [Stopping a run](#stopping-a-run)) |
| `GET /api/daemon/runs/current/terminal` | Where a window on the rig reaches the run's terminal UI (this PC only) |
| `POST /api/daemon/runs/attached` | A run started from a terminal announces itself |
| `DELETE /api/daemon/runs/attached/{pid}` | It has ended |
| `GET /api/daemon/reports` | The issue reports waiting here |
| `GET /api/daemon/reports/{id}` | One, as a zip |
| `DELETE /api/daemon/reports/{id}` | Delete it (the hub, once stored) |
| `/run/api/...` | The run's own API (see [Run server](run-server.md)) |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `restart`: task `Ready`, daemon starts at logon | Log the rig's user on. |
| `restart` or `deploy` times out, task `Running` | Read `daemon.log`: port taken, or a `DAEMON_CLABE_*` variable invalid. |
| `The installed clabe cannot run the daemon` | The extras lack `frontend` (`--extras`), or that clabe is broken. The daemon kept running. |
| `... no web UI` | clabe was built from source (a `git+` or path pin), without the web UI CI builds. Lock a release or a [build](#builds-of-main). |
| `No build of clabe for commit …` | Only commits pushed to main have a build, and only the newest 100 are kept. |
| `uv's installer did not install uv` | Install uv for the rig's user, then run the installer again. |
| Cannot download clabe | Give the rig internet (or a uv index mirror) while installing. |
| Daemon answers with no repositories | Each needs a `main.py` and a `pyproject.toml` depending on `aind-clabe` in a folder right under a root (default `C:/git`); or list it under `repositories`. |
| 401 from the hub or a browser | Token differs from the rig's `DAEMON_CLABE_TOKEN`; restart the daemon after changing it. |
| `... would interrupt it` (busy or leased) | Deploy later, or `--force` / `-Force`. |
| Hubs set with `--hubs` are ignored | A `DAEMON_CLABE_HUBS` variable in the daemon's environment wins over `daemon.env`. Deploy removes the user variable; remove a machine one. |
| `ModuleNotFoundError: No module named 'clabe'` | An install failed while the daemon ran (installers before this fix). Run the installer again. |
| `The clabe-daemon task is not installed` | Run the installer (or `clabe daemon deploy`). |
