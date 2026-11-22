# The clabe daemon

The daemon runs on every rig PC: it lists the machine's experiments, starts them, and serves the run at one address.

| URL | Shows |
|---|---|
| `http://<rig>:8070/` | The machine and its experiments |
| `http://<rig>:8070/run/` | The run in progress (web UI and API) |
| `http://<rig>:8070/docs` | The API |

To install it on a rig, use the one-line installer: [Deploy a rig](deployment.md).

## Repositories

A repository's workflows are those declared (or imported) in its top-level `main.py`. The daemon finds repositories
by itself:

- Every folder right under a **root** (default `C:/git`) with a `main.py` and a `pyproject.toml` depending on
  `aind-clabe`.
- Plus any listed under `repositories`, wherever they are.
- A repository's name is its folder's name; the first one is the worker's default.

## Configure

Nothing is required. When the defaults don't fit:

```yaml
daemon:
  repository_roots: [C:/git, D:/experiments]           # where to look (default: C:/git)
  repositories: [C:/somewhere/Aind.Experiment.Special] # more, anywhere
  run_arguments: [--skip-hardware-validation]          # added to every run it starts
```

- **Token**: `$CLABE_TOKEN` of the logged-on user (or `token:` here).
- **Listening**: on the network when a token is set, else only this PC; `host:` overrides it.

Rarely needed:

| Setting | Default | What it does |
|---|---|---|
| `port` | `8070` | The daemon's port |
| `host` | from the token | Interface to bind |
| `command` | `uv run --directory {repository} clabe` | How clabe is called in a repository |
| `runs_directory` | `%TEMP%\clabe-daemon` | Logs of the runs it starts |
| `max_jobs` | `4` | Worker: commands run at once for a remote run |
| `max_file_size` | 50 MB | Worker: largest file a remote run uploads |
| `file_transfer_dir` | `%TEMP%\clabe-worker` | Worker: where uploaded files go |
| `lease_grace` | `300` | Worker: seconds a crashed run can take the machine back |

## Commands

| Command | Does |
|---|---|
| `clabe daemon run [--open-browser]` | Run in the foreground, e.g. to try it |
| `clabe daemon install` | Register the `clabe-daemon` logon task (restarts on failure) and start it |
| `clabe daemon restart [--force] [--timeout 60]` | Restart from the task and wait; refused during a session unless `--force` |
| `clabe daemon status` | The task, and whether the daemon answers |
| `clabe daemon upgrade` | Upgrade the uv tool install; restart afterwards |
| `clabe daemon uninstall` | Stop it and remove the task |

- It runs as the logged-on user (not a service), so experiments can open windows.
- Logs: `%LOCALAPPDATA%\clabe\daemon.log`; also sent through OpenTelemetry (service `clabe-daemon`) when
  `otel: enabled: true` in clabe.yml.
- After reinstalling or upgrading, `clabe daemon restart` to pick up the new version.

## Starting a run

From the page, or `POST /api/daemon/runs`, the daemon runs in the repository:

```bash
uv run --directory <repository> clabe run main.py --experiment-name <name> --frontend headless --serve ...
```

- Each repository's own locked environment (and clabe version) is used.
- Prompts are answered in the web UI.
- One run at a time: another start returns `409`.

## Runs started from a terminal

`clabe run` in a terminal (or from a [desktop shortcut](#desktop-shortcuts)) tells the PC's daemon about itself:

- The daemon serves it under `/run/` and reports the PC busy, so the hub follows it.
- No daemon, or a busy one: the run goes on, just unseen. It never waits more than a second.
- It can't be stopped from the daemon's page; abort it from the run.

## Desktop shortcuts

In a repository, put an icon on the desktop that runs one of its experiments in a terminal:

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
| `POST /api/daemon/runs/current/stop` | Stop it (only runs the daemon started) |
| `POST /api/daemon/runs/attached` | A run started from a terminal announces itself |
| `DELETE /api/daemon/runs/attached/{pid}` | It has ended |
| `/run/api/...` | The run's own API (see [Frontends](frontends.md)) |
