# Deploy a rig

Install clabe and its daemon on a rig PC with one command, then update and operate it over SSH.

## 1. Prepare the PC

- A Windows user that stays logged on (e.g. with auto-logon). The daemon runs in that user's desktop session.
- OpenSSH Server enabled, with SSH access as that user.
- uv installed for that user ([uv's installer](https://docs.astral.sh/uv/getting-started/installation/)).
- The experiment repositories cloned under `C:/git`, each with a `main.py`.
- Port 24070 reachable from the hub and from browsers.
- Internet access during the install.

## 2. Configure (optional)

- The daemon reads environment variables of the rig's user, e.g. `DAEMON_CLABE_OTEL_ENDPOINT`. With repositories
  under `C:/git`, it needs none except its token. See [the daemon's variables](daemon.md#configure).
- Experiments read the rig's `%PROGRAMDATA%\clabe.yml` (do not put a token in it), e.g. `watchdog:` and `otel:`
  settings for runs. The installer can write this file (`-Config`).

## 3. Install

As the rig's user, in PowerShell or `cmd`:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1 | iex"
```

The installer sets `DAEMON_CLABE_TOKEN` and `%PROGRAMDATA%\clabe.yml` (when given) and installs clabe as a uv tool
(`clabe.exe`). This is the daemon's own install: experiments still run in their repository's environment. It then
registers the `clabe-daemon` logon task, restarts the daemon and prints its status. It stops immediately if uv is
missing.

- Running it again is safe: the same command installs, updates and repairs.
- It never interrupts a running session unless you pass `-Force`. Without it, the install fails instead.
- It exits non-zero on any failure.

| Parameter | Variable | Meaning |
|---|---|---|
| `-Version` | `DAEMON_CLABE_VERSION` | clabe version (default: latest). **Pin it on rigs.** |
| `-Package` | `DAEMON_CLABE_PACKAGE` | Any uv spec instead of `aind-clabe[otel]`, e.g. `aind-clabe[otel,agents]==0.18.0` |
| `-Token` | `DAEMON_CLABE_TOKEN` | Daemon token, saved as the user's `DAEMON_CLABE_TOKEN` |
| `-Config` | `DAEMON_CLABE_CONFIG` | The experiments' `clabe.yml` to install as `%PROGRAMDATA%\clabe.yml`; `-` reads it from standard input |
| `-Force` | `DAEMON_CLABE_FORCE=1` | Restart even during a session |
| `-DryRun` | `DAEMON_CLABE_DRY_RUN=1` | Print the commands only |
| `-Timeout` | | Seconds to wait for the daemon (default 120) |

```powershell
# with an environment variable
$env:DAEMON_CLABE_VERSION = "0.18.0"; irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1 | iex
# with parameters
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1))) -Version 0.18.0
```

To pin the installer version as well, replace `main` with the tag, e.g. `.../clabe/v0.18.0/scripts/install.ps1`.

## 4. Install over SSH

```bash
ssh rig-3 'powershell -NoProfile -ExecutionPolicy ByPass -c "& ([scriptblock]::Create((irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1))) -Version 0.18.0 -Config -"' < rig-config/clabe.yml
```

- `-Config -` reads the rig's `clabe.yml` from the pipe, so you do not copy the file first. Leave it out to keep the
  rig's current `clabe.yml`.
- Pass the token through the SSH session's environment, or set it once on the rig. Do not put it on the command line.
- To update or roll back, run the same command with another `-Version`.

## 5. Operate it

```bash
ssh rig-3 clabe daemon status      # the task, and whether the daemon answers (idle, busy, leased)
ssh rig-3 clabe daemon restart     # refused during a session; --force interrupts it
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
| `restart` times out, task `Running` | Read `daemon.log`: port taken, or a `DAEMON_CLABE_*` variable invalid. |
| `uv is not installed` | Install uv for the rig's user, then run the installer again. |
| Cannot download clabe | Give the rig internet (or a uv index mirror) while installing. |
| Daemon answers with no repositories | Each needs a `main.py` and a `pyproject.toml` depending on `aind-clabe` in a folder right under a root (default `C:/git`); or list it under `repositories`. |
| 401 from the hub or a browser | Token differs from the rig's `DAEMON_CLABE_TOKEN`; restart the daemon after changing it. |
| `... restarting would interrupt it` (busy or leased) | Deploy later, or `-Force`. |
| `The clabe-daemon task is not installed` | Run the installer (or `clabe daemon install`). |
