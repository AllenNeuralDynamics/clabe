# Deploy a rig

Install clabe and its daemon on a rig PC with one line, then update and operate it over SSH.

## 1. Prepare the PC

- A Windows user that stays **logged on** (e.g. auto-logon): the daemon runs in its desktop session.
- **OpenSSH Server** enabled, with SSH access as that user.
- The experiment repositories cloned.
- Port 8070 reachable from the hub and browsers.
- Internet access while installing (uv is installed if missing).

## 2. Write the rig's config

`%PROGRAMDATA%\clabe.yml` (machine-wide; no token in it):

```yaml
daemon:
  host: 0.0.0.0
  repositories:
    - path: C:/git/Aind.Experiment.VrForaging
    - path: C:/git/Aind.Experiment.DynamicForaging
      experiments: src/experiments
  run_arguments: [--skip-hardware-validation]
```

## 3. Install

As the rig's user, in PowerShell or `cmd`:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1 | iex"
```

It installs uv if missing, sets `CLABE_TOKEN` and `%PROGRAMDATA%\clabe.yml` (when given), installs clabe as a uv tool,
registers the `clabe-daemon` logon task, restarts the daemon and prints its status.

- Safe to repeat: the same line installs, updates and repairs.
- Never interrupts a running session (it fails instead) unless `-Force`.
- Any failure exits non-zero.

| Parameter | Variable | Meaning |
|---|---|---|
| `-Version` | `CLABE_VERSION` | clabe version (default: latest). **Pin it on rigs.** |
| `-Package` | `CLABE_PACKAGE` | Any uv spec instead, e.g. `aind-clabe[agents]==0.18.0` |
| `-Token` | `CLABE_TOKEN` | Daemon token, saved as the user's `CLABE_TOKEN` |
| `-Config` | `CLABE_CONFIG` | A `clabe.yml` to install as `%PROGRAMDATA%\clabe.yml` |
| `-Force` | `CLABE_FORCE=1` | Restart even during a session |
| `-NoInstallUv` | | Fail if uv is missing |
| `-DryRun` | `CLABE_DRY_RUN=1` | Print the commands only |
| `-Timeout` | | Seconds to wait for the daemon (default 120) |

```powershell
# with an environment variable
$env:CLABE_VERSION = "0.18.0"; irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1 | iex
# with parameters
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1))) -Version 0.18.0
```

To pin the installer too, replace `main` with the tag, e.g. `.../clabe/v0.18.0/scripts/install.ps1`.

## 4. Install over SSH

```bash
scp rig-config/clabe.yml rig-3:clabe.yml
ssh rig-3 'powershell -NoProfile -ExecutionPolicy ByPass -c "& ([scriptblock]::Create((irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1))) -Version 0.18.0 -Config clabe.yml"'
```

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
- Without SSH, a rig admin can run `schtasks /S rig-3 /Run /TN clabe-daemon`.

## 6. Deploy the hub

From a checkout of this repository on a Docker host:

```bash
cp docker/hub/hub.example.yml docker/hub/hub.yml     # list the rigs: {id, url}
CLABE_HUB_TOKEN=... CLABE_TOKEN=... docker compose -f docker/hub/compose.yml up -d --build
```

Update by pulling and running the same command. See [The hub](hub.md).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `restart`: task `Ready`, daemon starts at logon | Log the rig's user on. |
| `restart` times out, task `Running` | Read `daemon.log`: port taken, or `%PROGRAMDATA%\clabe.yml` invalid. |
| Cannot download uv or clabe | Give the rig internet (or a uv index mirror) while installing. |
| Daemon answers with no repositories | Add `daemon: repositories:`; check no `clabe.yml` in `%LOCALAPPDATA%\clabe` overrides it. |
| 401 from the hub or a browser | Token differs from the rig's `CLABE_TOKEN`; restart the daemon after changing it. |
| `... restarting would interrupt it` (busy or leased) | Deploy later, or `-Force`. |
| `The clabe-daemon task is not installed` | Run the installer (or `clabe daemon install`). |
