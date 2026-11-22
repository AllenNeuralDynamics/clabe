<#
.SYNOPSIS
    Installs or updates the clabe daemon on this rig PC, then restarts it and checks it answers. Safe to run over SSH.

.DESCRIPTION
    Run it as the user the daemon runs for (the user logged on to the rig), for example over SSH:

        scp Deploy-ClabeDaemon.ps1 rig-3:
        ssh rig-3 powershell -NoProfile -ExecutionPolicy Bypass -File Deploy-ClabeDaemon.ps1 -Package aind-clabe==0.18.0

    Steps, each idempotent:
      1. finds uv (installs it with -InstallUv);
      2. optionally sets the daemon's token (the user's CLABE_TOKEN) and its configuration (%PROGRAMDATA%\clabe.yml);
      3. installs the package as a uv tool (a stable clabe.exe that starts without network), or with -Uvx registers
         `uvx --from <package> clabe daemon run` instead;
      4. registers the clabe-daemon scheduled task (at logon, restart on failure, in the user's desktop session);
      5. restarts the daemon and waits until it answers — refusing while it runs a session, unless -Force.

    Exits with a non-zero code when a step fails, so deployment tools can stop.

.PARAMETER Package
    What to install: aind-clabe==0.18.0, aind-clabe[agents]==0.18.0, or git+https://github.com/AllenNeuralDynamics/clabe@v0.18.0

.PARAMETER Config
    A clabe.yml on this machine to install as %PROGRAMDATA%\clabe.yml (the daemon's section lists the repositories).

.PARAMETER Token
    The daemon's token, saved as this user's CLABE_TOKEN environment variable (kept out of the machine-wide config).

.PARAMETER Uvx
    Register `uvx --from <Package> clabe daemon run` instead of installing a uv tool (needs uv's cache, or the network,
    at every logon).

.PARAMETER InstallUv
    Install uv for this user if it is missing (downloads https://astral.sh/uv/install.ps1).

.PARAMETER Force
    Restart even if the daemon runs a session or is leased by another rig (that session is interrupted).

.PARAMETER Timeout
    Seconds to wait for the restarted daemon to answer.

.PARAMETER DryRun
    Print the commands instead of running them.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Package,
    [string]$Config,
    [string]$Token,
    [switch]$Uvx,
    [switch]$InstallUv,
    [switch]$Force,
    [int]$Timeout = 120,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Invoke-Step([string]$Description, [string]$Program, [string[]]$Arguments) {
    Write-Host "==> $Description"
    if ($DryRun) {
        Write-Host "    $Program $($Arguments -join ' ')"
        return
    }
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed ($LASTEXITCODE): $Program $($Arguments -join ' ')"
        exit $LASTEXITCODE
    }
}

# 1. uv. An SSH session may not have the user's PATH yet: look where uv's installer puts it too.
$userBin = Join-Path $env:USERPROFILE '.local\bin'
if (-not ($env:PATH -split ';' -contains $userBin)) { $env:PATH = "$userBin;$env:PATH" }
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    if (-not $InstallUv) {
        Write-Error "uv is not installed for $env:USERNAME. Install it, or pass -InstallUv."
        exit 1
    }
    Write-Host "==> Installing uv"
    if (-not $DryRun) { Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression }
}
$uv = if ($DryRun -and -not (Get-Command uv -ErrorAction SilentlyContinue)) { 'uv' } else { (Get-Command uv).Source }
$uvxExe = Join-Path (Split-Path $uv) 'uvx.exe'

# 2. Token and configuration.
if ($Token) {
    Write-Host "==> Setting CLABE_TOKEN for $env:USERNAME"
    if (-not $DryRun) {
        [Environment]::SetEnvironmentVariable('CLABE_TOKEN', $Token, 'User')
        $env:CLABE_TOKEN = $Token
    }
}
if ($Config) {
    $target = Join-Path $env:ProgramData 'clabe.yml'
    Write-Host "==> Installing $Config as $target"
    if (-not $DryRun) { Copy-Item -LiteralPath $Config -Destination $target -Force }
}

# 3-4. The package and the task.
if ($Uvx) {
    Invoke-Step "Fetching $Package" $uvxExe @('--from', $Package, 'clabe', 'daemon', '--help')
    Invoke-Step 'Registering the clabe-daemon task (uvx)' $uvxExe @('--from', $Package, 'clabe', 'daemon', 'install', '--uvx', $Package)
    $clabe = @($uvxExe, '--from', $Package, 'clabe')
} else {
    Invoke-Step "Installing $Package as a uv tool" $uv @('tool', 'install', '--force', $Package)
    $clabeExe = Join-Path $userBin 'clabe.exe'
    Invoke-Step 'Registering the clabe-daemon task' $clabeExe @('daemon', 'install')
    $clabe = @($clabeExe)
}

# 5. Restart and check.
$restart = @('daemon', 'restart', '--timeout', "$Timeout")
if ($Force) { $restart += '--force' }
$program, $prefix = $clabe[0], @($clabe | Select-Object -Skip 1)
Invoke-Step 'Restarting the daemon' $program ($prefix + $restart)
Invoke-Step 'Status' $program ($prefix + @('daemon', 'status'))
Write-Host "==> Done"
