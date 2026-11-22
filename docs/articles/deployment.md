# Deploy a rig

Install clabe and its daemon on a rig PC with one line, then update and operate it over SSH.

## 1. Prepare the PC

- A Windows user that stays **logged on** (e.g. auto-logon): the daemon runs in its desktop session.
- **OpenSSH Server** enabled, with SSH access as that user.
- **uv** installed for that user ([uv's installer](https://docs.astral.sh/uv/getting-started/installation/)).
- The experiment repositories cloned under `C:/git` (each with a `main.py`).
- Port 8070 reachable from the hub and browsers.
- Internet access while installing.

## 2. Configure (optional)

- **The daemon**: environment variables of the rig's user, e.g. `DAEMON_CLABE_OTEL_ENDPOINT`; with repositories under
  `C:/git` it needs none but its token. See [the daemon's variables](daemon.md#configure).
- **Experiments**: the rig's `%PROGRAMDATA%\clabe.yml` (no token in it), e.g. `watchdog:`, `otel:` for runs. The
  installer can write it (`-Config`).

## 3. Install

As the rig's user, in PowerShell or `cmd`:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1 | iex"
```

It sets `DAEMON_CLABE_TOKEN` and `%PROGRAMDATA%\clabe.yml` (when given), installs clabe as a uv tool (`clabe.exe`, the
daemon's own install; experiments still run in their repository's environment), registers the `clabe-daemon` logon
task, restarts the daemon and prints its status. It stops at once if uv is missing.

- Safe to repeat: the same line installs, updates and repairs.
- Never interrupts a running session (it fails instead) unless `-Force`.
- Any failure exits non-zero.

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

To pin the installer too, replace `main` with the tag, e.g. `.../clabe/v0.18.0/scripts/install.ps1`.

## 4. Install over SSH

```bash
ssh rig-3 'powershell -NoProfile -ExecutionPolicy ByPass -c "& ([scriptblock]::Create((irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1))) -Version 0.18.0 -Config -"' < rig-config/clabe.yml
```

- `-Config -` reads the rig's `clabe.yml` from the pipe: nothing to copy first. Leave it out to keep the rig's own.

- Pass the token through the SSH session's environment (or set it once on the rig), not on the command line.
- **Update** or **roll back**: run the same line with another `-Version`.

## 5. Operate it

```bash
ssh rig-3 clabe daemon status      # the task, and whether the daemon answers (idle, busy, leased)
ssh rig-3 clabe daemon restart     # refused during a session; --force to interrupt it
ssh rig-3 clabe daemon uninstall   # stop and remove the task
ssh rig-3 type %LOCALAPPDATA%\clabe\daemon.log
```

- Never start it with `clabe daemon run` over SSH: it could not show windows and would stop on disconnect.
- Is it up? The hub shows every rig online, offline, idle, busy or leased; `clabe daemon status` asks one.
- Without SSH, a rig admin can run `schtasks /S rig-3 /Run /TN clabe-daemon`.

## 6. Deploy the hub

From a checkout of this repository on a Docker host:

```bash
cp docker/hub/.env.example docker/hub/.env     # the hub's token, the rigs' token, the rigs
docker compose -f docker/hub/compose.yml up -d --build
```

Update by pulling and running the same command. See [The hub](hub.md).

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
