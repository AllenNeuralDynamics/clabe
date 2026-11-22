<#
.SYNOPSIS
    A one-minute tour of clabe: an experiment answered from the terminal or the browser, followed by the hub.

.DESCRIPTION
    From a checkout of clabe (needs uv and Docker):

        powershell -ExecutionPolicy ByPass -File scripts/demo.ps1

    1. Starts the hub in Docker (docker/demo/compose.yml), following this computer.
    2. Starts a daemon on this computer, unless one already answers (it is stopped at the end).
    3. Opens the hub in the browser, then runs examples/demo/main.py here: the terminal UI in this window and the
       same run in another browser tab. Answer in either; the hub shows the run, its step and what it waits for.

    -KeepHub leaves the hub running afterwards; -NoBrowser opens nothing; -Frontend headless answers only in the
    browser (no terminal UI).
#>
param(
    [int]$HubPort = 8080,
    [int]$DaemonPort = 24070,
    [switch]$KeepHub,
    [switch]$NoBrowser,
    [ValidateSet('auto', 'tui', 'console', 'headless')]
    [string]$Frontend = 'auto'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$compose = Join-Path $root 'docker/demo/compose.yml'

function Test-Answers([string]$Url) {
    try {
        Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Wait-Answers([string]$Url, [string]$What, [int]$Seconds = 120) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while (-not (Test-Answers $Url)) {
        if ((Get-Date) -gt $deadline) { throw "$What did not answer at $Url within $Seconds s." }
        Start-Sleep -Milliseconds 500
    }
}

foreach ($tool in 'uv', 'docker') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "The demo needs $tool on PATH." }
}

Push-Location $root
$daemon = $null
try {
    Write-Host '==> Starting the hub (Docker; the first build takes a few minutes)'
    $env:HUB_PORT = "$HubPort"
    $env:DAEMON_PORT = "$DaemonPort"
    docker compose -f $compose up -d --build
    if ($LASTEXITCODE -ne 0) { throw 'docker compose failed: is Docker running?' }

    $daemonUrl = "http://127.0.0.1:$DaemonPort/api/daemon/info"
    if (Test-Answers $daemonUrl) {
        Write-Host "==> Using the daemon already running on port $DaemonPort"
    } else {
        Write-Host "==> Starting a daemon on port $DaemonPort"
        $env:DAEMON_CLABE_PORT = "$DaemonPort"
        $env:DAEMON_CLABE_REPOSITORIES = (Join-Path $root 'examples/demo')  # also startable from its page
        $log = Join-Path ([IO.Path]::GetTempPath()) 'clabe-demo-daemon.log'
        $daemon = Start-Process -FilePath 'uv' -ArgumentList @('run', 'clabe', 'daemon', 'run', '--log-file', $log) `
            -WorkingDirectory $root -WindowStyle Hidden -PassThru
        Wait-Answers $daemonUrl 'The daemon' 60
    }

    Wait-Answers "http://127.0.0.1:$HubPort/api/hub/rigs" 'The hub' 60
    if (-not $NoBrowser) { Start-Process "http://localhost:$HubPort/" }

    Write-Host '==> Running the demo: answer here or in the browser tab it opens'
    $arguments = @('run', 'clabe', 'run', 'examples/demo/main.py', '--allow-dirty', '--skip-hardware-validation')
    $arguments += @('--frontend', $Frontend)
    if (-not $NoBrowser) { $arguments += '--open-browser' }
    & uv @arguments
} finally {
    if ($null -ne $daemon -and -not $daemon.HasExited) {
        Write-Host '==> Stopping the demo daemon'
        & taskkill /PID $daemon.Id /T /F | Out-Null
    }
    if (-not $KeepHub) {
        Write-Host '==> Stopping the hub (-KeepHub keeps it)'
        docker compose -f $compose down | Out-Null
    } else {
        Write-Host "==> The hub stays at http://localhost:$HubPort/ (docker compose -f docker/demo/compose.yml down)"
    }
    Pop-Location
}
