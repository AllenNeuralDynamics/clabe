# Deploy a rig

Install clabe and its daemon on a rig PC with one command, then update and operate it over SSH.

## 1. Prepare the PC

- A Windows user that stays logged on (e.g. with auto-logon). The daemon runs in that user's desktop session.
- OpenSSH Server enabled, with SSH access as that user.
- The experiment repositories cloned under `C:/git`, each with a `main.py`.
- Port 24070 reachable from the hub and from browsers.
- Internet access during the install. The installer installs [uv](https://docs.astral.sh/uv/) for that user if it is
  missing.

## 2. Configure (optional)

- The daemon reads `DAEMON_CLABE_*` settings from the rig user's environment variables, else from
  `%LOCALAPPDATA%\clabe\daemon.env`. With repositories under `C:/git`, it needs none except its token. See
  [the daemon's variables](daemon.md#configure). `clabe daemon deploy --hubs` writes the hubs there.
- Experiments read the rig's `%PROGRAMDATA%\clabe.yml` (do not put a token in it), e.g. `watchdog:` and `otel:`
  settings for runs. The installer can write this file (`-Config`).

## 3. Install

As the rig's user, in PowerShell or `cmd`:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1 | iex"
```

The installer:

1. Installs uv if it is missing. Stops if the daemon runs a session (busy or leased) and `-Force` is not set.
2. Sets `DAEMON_CLABE_TOKEN` and `%PROGRAMDATA%\clabe.yml`, when given.
3. Installs or updates the `clabe` command to the latest release (`uv tool install --upgrade`), and adds it to the user's PATH (`uv tool update-shell`).
4. Runs [`clabe daemon deploy`](#update-and-go-back), which installs the daemon's clabe and starts the daemon.

Running it again is safe: the same command installs, updates and repairs.

| Parameter | Variable | Meaning |
|---|---|---|
| `-Version` | `DAEMON_CLABE_VERSION` | clabe release for the daemon (default: latest). **Pin it on rigs.** |
| `-Lock` | `DAEMON_CLABE_LOCK` | A repository (or its `uv.lock`): the clabe release it locks |
| `-Package` | `DAEMON_CLABE_PACKAGE` | Any uv requirement instead, e.g. `aind-clabe[frontend,otel,agents]==0.18.0` |
| `-Extras` | `DAEMON_CLABE_EXTRAS` | aind-clabe's extras for the daemon (default `frontend,otel`) |
| `-Hubs` | `DAEMON_CLABE_DEPLOY_HUBS` | Hubs to enroll with, `;`-separated; `none` for no hub (default: unchanged) |
| `-Token` | `DAEMON_CLABE_TOKEN` | Daemon token, saved as the user's `DAEMON_CLABE_TOKEN` |
| `-Config` | `DAEMON_CLABE_CONFIG` | The experiments' `clabe.yml` to install as `%PROGRAMDATA%\clabe.yml`; `-` reads it from standard input |
| `-CliPackage` | `DAEMON_CLABE_CLI_PACKAGE` | uv requirement of the `clabe` command (default: the latest release, whatever `-Version` the daemon runs) |
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

## Update and go back

Once the `clabe` command is installed, `clabe daemon deploy` does the rest:

```powershell
clabe daemon deploy --version 0.18.0                     # a release
clabe daemon deploy --lock C:/git/Aind.Experiment.X      # the clabe that repository's uv.lock pins
clabe daemon deploy --version 0.18.0 --hubs "http://hub-vm/clabe/"   # and the hubs (--hubs none: no hub)
clabe daemon deploy --version 0.17.2                     # go back
```

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

## 4. Install over SSH

```bash
ssh rig-3 'powershell -NoProfile -ExecutionPolicy ByPass -c "& ([scriptblock]::Create((irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1))) -Version 0.18.0 -Config -"' < rig-config/clabe.yml
ssh rig-3 clabe daemon deploy --version 0.18.1   # later updates
```

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

## 5. Operate it

```bash
ssh rig-3 clabe daemon status      # the task, and whether the daemon answers (idle, busy, leased)
ssh rig-3 clabe daemon restart     # refused during a session; --force interrupts it
ssh rig-3 clabe daemon stop        # same; it starts again at logon or with `clabe daemon start`
ssh rig-3 clabe daemon uninstall   # stop the daemon and remove the task
ssh rig-3 type %LOCALAPPDATA%\clabe\daemon.log
```

- Do not start the daemon with `clabe daemon run` over SSH. Started that way, it cannot show windows and stops when
  the SSH session disconnects.
- To check that rigs are up, the hub shows each rig as online, offline, idle, busy or leased. `clabe daemon status`
  checks a single rig.
- Without SSH, a rig admin can run `schtasks /S rig-3 /Run /TN clabe-daemon`.

## 6. Deploy the hub

From a checkout of this repository on a Docker host:

```bash
cp docker/hub/.env.example docker/hub/.env     # the hub's token, the rigs' token, the rigs
docker compose -f docker/hub/compose.yml up -d --build
```

To update, pull and run the same command. See [The hub](hub.md).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `restart`: task `Ready`, daemon starts at logon | Log the rig's user on. |
| `restart` or `deploy` times out, task `Running` | Read `daemon.log`: port taken, or a `DAEMON_CLABE_*` variable invalid. |
| `The installed clabe cannot run the daemon` | The extras lack `frontend` (`--extras`), or that clabe is broken. The daemon kept running. |
| `... no web UI` / `... not a release` | The daemon runs PyPI releases only: their wheels carry the web UI CI builds. A git commit built on the rig has none. Lock or pass a release (a pre-release such as `0.19.0rc1` works). |
| `uv's installer did not install uv` | Install uv for the rig's user, then run the installer again. |
| Cannot download clabe | Give the rig internet (or a uv index mirror) while installing. |
| Daemon answers with no repositories | Each needs a `main.py` and a `pyproject.toml` depending on `aind-clabe` in a folder right under a root (default `C:/git`); or list it under `repositories`. |
| 401 from the hub or a browser | Token differs from the rig's `DAEMON_CLABE_TOKEN`; restart the daemon after changing it. |
| `... would interrupt it` (busy or leased) | Deploy later, or `--force` / `-Force`. |
| Hubs set with `--hubs` are ignored | A `DAEMON_CLABE_HUBS` variable in the daemon's environment wins over `daemon.env`. Deploy removes the user variable; remove a machine one. |
| `ModuleNotFoundError: No module named 'clabe'` | An install failed while the daemon ran (installers before this fix). Run the installer again. |
| `The clabe-daemon task is not installed` | Run the installer (or `clabe daemon deploy`). |
