# The clabe daemon

The daemon runs on every rig PC: it lists the machine's experiments, starts them, and serves the run at one address.

| URL | Shows |
|---|---|
| `http://<rig>:8070/` | The machine and its experiments |
| `http://<rig>:8070/run/` | The run in progress (web UI and API) |
| `http://<rig>:8070/docs` | The API |

To install it on a rig, use the one-line installer: [Deploy a rig](deployment.md).

## Configure

```yaml
daemon:
  host: 0.0.0.0            # reachable from the LAN (default: 127.0.0.1)
  port: 8070
  repositories:
    - path: C:/git/Aind.Experiment.VrForaging
    - path: C:/git/Aind.Experiment.DynamicForaging
      experiments: src/experiments   # file or folder of experiment files (default: experiments)
  run_arguments: [--skip-hardware-validation]
```

- Token: `$CLABE_TOKEN` of the logged-on user (or `token:` here).
- Remote-execution settings (`max_jobs`, `max_file_size`, `file_transfer_dir`, `lease_grace`): see
  [Run on two PCs](remote_execution.md).

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
- Logs: `%LOCALAPPDATA%\clabe\daemon.log`.
- After reinstalling or upgrading, `clabe daemon restart` to pick up the new version.

## Starting a run

From the page, or `POST /api/daemon/runs`, the daemon runs in the repository:

```bash
uv run --directory <repository> clabe run <file> --experiment-name <name> --frontend headless --serve ...
```

- Each repository's own locked environment (and clabe version) is used.
- Prompts are answered in the web UI.
- One run at a time: another start returns `409`.

## API

| Endpoint | Does |
|---|---|
| `GET /api/daemon/info` | Idle, busy or leased |
| `GET /api/daemon/repositories` | The repositories |
| `GET /api/daemon/repositories/{name}/experiments` | Their experiments |
| `POST /api/daemon/runs` | Start a run |
| `GET /api/daemon/runs/current` | The current run, with the end of its output |
| `POST /api/daemon/runs/current/stop` | Stop it |
| `/run/api/...` | The run's own API (see [Frontends](frontends.md)) |
