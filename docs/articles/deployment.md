# Deploying

How to put clabe on rig PCs and keep it up to date from a deployment machine over SSH, and how to deploy the hub.
For what the daemon and the hub are, see [Machines](../concepts/machines.md).

## What a rig PC needs

| Requirement | Why |
|---|---|
| A Windows user that stays **logged on** (e.g. auto-logon) | The daemon runs in that user's desktop session, so the experiments it starts can open windows (Bonsai, Open Ephys). It starts at that user's logon. |
| **OpenSSH Server** enabled, and SSH access as that user | Deployments run as that user, over SSH. |
| **uv** for that user | clabe is installed with uv (the installer installs uv when it is missing). |
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
environment variable. The installer sets both.

## Install a rig PC

One line, like uv's installer — run as the rig's user, in PowerShell or `cmd`:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1 | iex"
```

It installs or updates clabe and its daemon, then restarts the daemon and checks it answers:

1. installs uv for the user if it is missing (even when the session does not have the user's `PATH` yet);
2. sets the user's `CLABE_TOKEN` and installs `%PROGRAMDATA%\clabe.yml`, when given;
3. installs clabe as a uv tool (`uv tool install --force`): a stable `clabe.exe` that starts without the network;
4. registers the `clabe-daemon` scheduled task: at the user's logon, in their desktop session, restarted every
   minute if it fails, no time limit;
5. restarts the daemon and waits until it answers (`clabe daemon restart`), then prints its status.

Every step is safe to repeat: the same line installs, updates and repairs. A daemon running a session is never
interrupted (the install fails instead) unless forced. Any failure stops the installer with a non-zero exit code.

### Options

`irm ... | iex` cannot pass arguments, so the installer also reads environment variables:

| Parameter | Variable | Meaning |
|---|---|---|
| `-Version` | `CLABE_VERSION` | The clabe version (default: the latest release). **Pin it on rigs.** |
| `-Package` | `CLABE_PACKAGE` | Any uv package spec instead: `aind-clabe[agents]==0.18.0`, `git+https://github.com/AllenNeuralDynamics/clabe@v0.18.0` |
| `-Token` | `CLABE_TOKEN` | The daemon's token, saved as the user's `CLABE_TOKEN` |
| `-Config` | `CLABE_CONFIG` | A `clabe.yml` on the PC to install as `%PROGRAMDATA%\clabe.yml` |
| `-Force` | `CLABE_FORCE=1` | Restart even if the daemon runs a session (it is interrupted) |
| `-NoInstallUv` | | Fail instead of installing uv |
| `-DryRun` | `CLABE_DRY_RUN=1` | Print the commands instead of running them |
| `-Timeout` | | Seconds to wait for the daemon to answer (default 120) |

```powershell
$env:CLABE_VERSION = "0.18.0"; irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1 | iex
```

or with arguments:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1))) -Version 0.18.0
```

To pin the installer itself too, take it from the release's tag instead of `main`:
`https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/v0.18.0/scripts/install.ps1`.

### Over SSH

The same line, as the rig's user (the default SSH shell on Windows is `cmd`; the line works from PowerShell too):

```bash
ssh rig-3 'powershell -NoProfile -ExecutionPolicy ByPass -c "& ([scriptblock]::Create((irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1))) -Version 0.18.0"'
```

Send the configuration first and point the installer at it; pass the token through the environment of the SSH
session (or set it once on the rig) rather than on the command line, where other users of the rig can see it:

```bash
scp rig-config/clabe.yml rig-3:clabe.yml
ssh rig-3 'powershell -NoProfile -ExecutionPolicy ByPass -c "& ([scriptblock]::Create((irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1))) -Version 0.18.0 -Config clabe.yml"'
```

The exit code is non-zero when anything failed, so a deployment pipeline stops there. **Rollback** is the same line
with the previous version.

## Operate it remotely

All over SSH, as the rig's user (`clabe` is in `%USERPROFILE%\.local\bin`):

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
| The installer cannot download uv or clabe | The rig needs internet access (or a uv index mirror) while installing; once installed, the daemon starts offline. |
| The daemon answers but its repositories list is empty | `%PROGRAMDATA%\clabe.yml` has no `daemon: repositories:` (or another `./clabe.yml` takes precedence in the task's working directory, `%LOCALAPPDATA%\clabe`). |
| 401 from the hub or a browser | The token differs from the rig's `CLABE_TOKEN` (restart the daemon after changing it: it reads it when it starts). |
| `The daemon runs an experiment: restarting would interrupt it` | A session is going; deploy later, or `-Force`. |
