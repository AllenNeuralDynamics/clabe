# The clabe daemon

The daemon runs on every rig PC. It lists the experiment repositories of the machine, starts an experiment in its
repository's own environment, and serves the run under one stable address: `http://<rig>:8070/` shows the machine
and its experiments, `http://<rig>:8070/run/` the run in progress (its web UI and its API).

## Configure

In the machine's `clabe.yml`:

```yaml
daemon:
  host: 0.0.0.0            # reachable from the LAN (default: localhost only)
  port: 8070              # the token comes from $CLABE_TOKEN (or `token:` here)
  repositories:
    - path: C:/git/Aind.Experiment.VrForaging
    - path: C:/git/Aind.Experiment.DynamicForaging
      experiments: src/experiments   # file or folder of experiment files (default: experiments)
  run_arguments: [--skip-hardware-validation]
  lease_grace: 300         # worker role: seconds a crashed run's lease stays reserved for it
```

## Run and install

```bash
clabe daemon run --open-browser   # in the foreground, e.g. to try it
clabe daemon install              # start it at logon, restart it on failure (Windows Task Scheduler), and now
clabe daemon install --uvx aind-clabe==0.18.0   # the task runs `uvx --from <spec> clabe daemon run`
clabe daemon restart              # after an update; refused during a session unless --force
clabe daemon status               # the task, and whether the daemon answers
clabe daemon uninstall
```

The daemon runs as the logged-in user, not as a service: the experiments it starts open windows (Bonsai, Open
Ephys) on the rig's desktop. It logs to `%LOCALAPPDATA%\clabe\daemon.log`. Installing again replaces the task, but a
daemon already running keeps its old command until it is restarted.

To install and update rigs from a deployment machine over SSH, see [Deploying](deployment.md).

## How a run is started

Starting an experiment (from the page, or `POST /api/daemon/runs`) runs, in the repository's folder:

```bash
uv run --directory <repository> clabe run <file> --experiment-name <name> --frontend headless --serve ...
```

The run uses the repository's locked environment, so each experiment repository pins its own clabe. It has no
screen (`--frontend headless`): its prompts are answered from the web UI, through the daemon. One run at a time:
starting another while one is going is refused (409). The run server listens on localhost with a token only the
daemon knows; the daemon checks its own token and forwards.

## API

Described at `/docs`: `GET /api/daemon/info` (idle or busy, for the hub), `GET /api/daemon/repositories`,
`GET /api/daemon/repositories/{name}/experiments`, `POST /api/daemon/runs`, `GET /api/daemon/runs/current` (with
the end of its output), `POST /api/daemon/runs/current/stop`, and the run's own API under `/run/api/` (see
[Frontends](frontends.md#serving-a-run-over-http)).
