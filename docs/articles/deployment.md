# Deploying

How to put clabe on rig PCs and keep it up to date from a deployment machine over SSH, and how to deploy the hub.
For what the daemon and the hub are, see [Machines](../concepts/machines.md).

## What a rig PC needs

| Requirement | Why |
|---|---|
| A Windows user that stays **logged on** (e.g. auto-logon) | The daemon runs in that user's desktop session, so the experiments it starts can open windows (Bonsai, Open Ephys). It starts at that user's logon. |
| **OpenSSH Server** enabled, and SSH access as that user | Deployments run as that user, over SSH. |
| **uv** for that user | clabe runs from uv (the deployment script can install it with `-InstallUv`). |
| The experiment repositories cloned | The daemon runs each experiment in its repository's own environment (`uv run`). |
| Port 8070 reachable from the hub and browsers (if the daemon binds `0.0.0.0`) | The daemon's page, its runs and its worker API. |

## Configure the rig

Configuration is machine-wide, in `%PROGRAMDATA%\clabe.yml` — the daemon finds it whatever its working directory:

```yaml
daemon:
  host: 0.0.0.0
  repositories:
    - path: C:/git/Aind.Experiment.VrForaging
    - path: C:/git/Aind.Experiment.DynamicForaging
      experiments: src/experiments
  run_arguments: [--skip-hardware-validation]
```

The **token** is not in that file (every user of the machine can read it): it is the logged-on user's `CLABE_TOKEN`
environment variable. The deployment script sets both.

## Deploy over SSH

`scripts/deploy/Deploy-ClabeDaemon.ps1` installs or updates clabe and its daemon on the machine it runs on, restarts
the daemon and checks it answers. Every step is idempotent, so run it for a first install and for every update.

```bash
# copy the script (and a config) to the rig, then run it there as the rig's user
scp scripts/deploy/Deploy-ClabeDaemon.ps1 rig-config/clabe.yml rig-3:
ssh rig-3 powershell -NoProfile -ExecutionPolicy Bypass -File Deploy-ClabeDaemon.ps1 \
    -Package aind-clabe==0.18.0 -Config clabe.yml -Token "$CLABE_TOKEN"
```

What it does:

1. finds uv (or installs it with `-InstallUv`), even if the SSH session does not have the user's `PATH` yet;
2. sets `CLABE_TOKEN` for the user (`-Token`) and installs `%PROGRAMDATA%\clabe.yml` (`-Config`), when given;
3. installs the package as a uv tool (`uv tool install --force <Package>`): a stable `clabe.exe` that starts without
   the network;
4. registers the `clabe-daemon` scheduled task: at the user's logon, in their desktop session, restarted every
   minute if it fails, no time limit;
5. restarts the daemon and waits until it answers (`clabe daemon restart`), then prints its status.

It exits with a non-zero code as soon as a step fails, so a deployment pipeline stops there. `-DryRun` prints what it
would run.

`-Package` takes anything uv does: `aind-clabe==0.18.0`, `aind-clabe[agents]==0.18.0`, or a Git reference
(`git+https://github.com/AllenNeuralDynamics/clabe@v0.18.0`). **Pin a version**: the rig then runs exactly what you
deployed, and rolling back is running the script again with the previous one.

The script never interrupts a session: when the daemon runs an experiment, or another rig's run has leased it, the
restart is refused and the script fails. Run it again later, or pass `-Force` to restart anyway.

### Without installing: `-Uvx`

`-Uvx` registers `uvx --from <Package> clabe daemon run` as the task instead of installing a tool, so the version
lives in the task alone. The daemon then needs uv's cache — or the network — at every logon; prefer the default on
rigs.

The same works by hand, without the script:

```bash
ssh rig-3 uvx --from aind-clabe==0.18.0 clabe daemon install --uvx aind-clabe==0.18.0
ssh rig-3 uvx --from aind-clabe==0.18.0 clabe daemon restart
```

## Operate it remotely

All over SSH, as the rig's user (with the default tool install, `clabe` is in `%USERPROFILE%\.local\bin`):

```bash
ssh rig-3 clabe daemon status            # the task, and whether the daemon answers (idle, busy, leased)
ssh rig-3 clabe daemon restart           # refused during a session; --force to interrupt it
ssh rig-3 clabe daemon uninstall         # stop and remove the task
ssh rig-3 type %LOCALAPPDATA%\clabe\daemon.log
```

Without SSH, an administrator of the rig can start or stop the task from another Windows machine:
`schtasks /S rig-3 /Run /TN clabe-daemon` (this is what `RemoteRepository.ensure_running()` does).

Always restart through the task (`clabe daemon restart`, or `schtasks /End` then `/Run`), never by running
`clabe daemon run` in the SSH session: a daemon started there would live in the SSH session, could not show windows on
the rig's desktop, and would stop when you disconnect.

## Deploy the hub

The hub runs anywhere Docker does (no rig hardware). From a checkout of this repository on the server:

```bash
cp docker/hub/hub.example.yml docker/hub/hub.yml     # list the rigs: {id, url}
CLABE_HUB_TOKEN=... CLABE_TOKEN=... docker compose -f docker/hub/compose.yml up -d --build
```

`CLABE_HUB_TOKEN` protects the hub; `CLABE_TOKEN` is the rigs' daemon token (a rig may have its own `token:` in
`hub.yml`). Update it by pulling the new version and running the same command; see [The hub](hub.md).

## When something is wrong

| Symptom | Look at |
|---|---|
| `restart` says the task is `Ready` and the daemon starts at logon | The rig's user is not logged on: the daemon runs in their session. |
| `restart` times out with the task `Running` | `%LOCALAPPDATA%\clabe\daemon.log`: usually the port is taken or `%PROGRAMDATA%\clabe.yml` is invalid. |
| `uv is not installed` | uv is missing for that user: pass `-InstallUv`, or install it once. |
| The daemon answers but its repositories list is empty | `%PROGRAMDATA%\clabe.yml` has no `daemon: repositories:` (or another `./clabe.yml` takes precedence in the task's working directory, `%LOCALAPPDATA%\clabe`). |
| 401 from the hub or a browser | The token differs from the rig's `CLABE_TOKEN` (restart the daemon after changing it: it reads it when it starts). |
| `The daemon runs an experiment: restarting would interrupt it` | A session is going; deploy later, or `-Force`. |
