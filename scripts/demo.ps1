<#
.SYNOPSIS
    A one-minute tour of clabe: an experiment answered from the terminal or the browser, followed by the hub.

.DESCRIPTION
    From a checkout of clabe (needs uv and Docker):

        powershell -ExecutionPolicy ByPass -File scripts/demo.ps1

    1. Starts the hub in Docker (docker/demo/compose.yml), following this computer.
    2. Starts a daemon on this computer (stopped at the end).
    Everything is protected by a token made for this demo (printed, and in the links it opens): the daemon runs
    any command it is given, so a demo left running must not be open to the network.
    3. Opens the hub in the browser, then runs examples/demo/main.py here: the terminal UI in this window and the
       same run in another browser tab. Answer in either; the hub shows the run, its step and what it waits for.

    -HubPort picks the hub's port (the next free one is used if it is taken); -KeepHub leaves the hub running
    afterwards; -NoBrowser opens nothing; -Frontend headless answers only in the browser (no terminal UI).
#>
param(
    [int]$HubPort = 24080,
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
        Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 -Headers @{ Authorization = "Bearer $token" } |
            Out-Null
        return $true
    } catch {
        return $false
    }
}

function Wait-Answers([string]$Url, [string]$What, [int]$Seconds = 60) {
    Write-Host "    waiting for $What at $Url"
    $deadline = (Get-Date).AddSeconds($Seconds)
    while (-not (Test-Answers $Url)) {
        if ((Get-Date) -gt $deadline) { throw "$What did not answer at $Url within $Seconds s." }
        Start-Sleep -Milliseconds 500
    }
}

function Test-PortFree([int]$Port) {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, $Port)
    try {
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        $listener.Stop()
    }
}

function Get-Listener([int]$Port) {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
}

foreach ($tool in 'uv', 'docker') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "The demo needs $tool on PATH." }
}

# A token for this demo only: the hub, the daemon and the run all require it.
$bytes = New-Object byte[] 18
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$token = 'demo' + [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')

Push-Location $root
$daemon = $null
$daemonPids = @()
try {
    $wanted = $HubPort
    while (-not (Test-PortFree $HubPort)) { $HubPort++ }
    if ($HubPort -ne $wanted) { Write-Host "==> Port $wanted is taken; the hub uses $HubPort" }

    Write-Host "==> Starting the hub on port $HubPort (Docker; the first build takes a few minutes)"
    $env:HUB_PORT = "$HubPort"
    $env:DAEMON_PORT = "$DaemonPort"
    $env:DEMO_TOKEN = $token
    docker compose -f $compose up -d --build
    if ($LASTEXITCODE -ne 0) { throw 'docker compose failed: is Docker running?' }

    $daemonUrl = "http://127.0.0.1:$DaemonPort/api/daemon/info"
    if (-not (Test-PortFree $DaemonPort)) {
        throw "Port $DaemonPort is taken (a clabe daemon already running? 'clabe daemon uninstall' stops an installed one); or pass -DaemonPort."
    } else {
        Write-Host "==> Starting a daemon on port $DaemonPort"
        $env:DAEMON_CLABE_PORT = "$DaemonPort"
        $env:DAEMON_CLABE_TOKEN = $token
        $env:DAEMON_CLABE_REPOSITORIES = (Join-Path $root 'examples/demo')  # also startable from its page
        $log = Join-Path ([IO.Path]::GetTempPath()) 'clabe-demo-daemon.log'
        $daemon = Start-Process -FilePath 'uv' -ArgumentList @('run', 'clabe', 'daemon', 'run', '--log-file', $log) `
            -WorkingDirectory $root -WindowStyle Hidden -PassThru
        Wait-Answers $daemonUrl 'the daemon'
        $daemonPids = @(Get-Listener $DaemonPort)  # uv's child: stopping uv's tree may miss it
    }

    Wait-Answers "http://127.0.0.1:$HubPort/api/hub/rigs" 'the hub'
    $hubUrl = "http://localhost:$HubPort/?token=$token"
    Write-Host "==> The hub is at $hubUrl"
    Write-Host "    (the demo's token: $token)"
    if (-not $NoBrowser) { Start-Process $hubUrl }

    Write-Host '==> Running the demo: answer here or in the browser tab it opens'
    $env:CLABE_TOKEN = $token  # the run's own server; it announces itself to the daemon with DAEMON_CLABE_TOKEN
    $arguments = @('run', 'clabe', 'run', 'examples/demo/main.py', '--allow-dirty', '--skip-hardware-validation')
    $arguments += @('--frontend', $Frontend)
    if (-not $NoBrowser) { $arguments += '--open-browser' }
    & uv @arguments
} finally {
    $ErrorActionPreference = 'Continue'  # Windows PowerShell treats a program's stderr output as errors
    if ($null -ne $daemon) {
        Write-Host '==> Stopping the demo daemon'
        if (-not $daemon.HasExited) { & taskkill /PID $daemon.Id /T /F *> $null }
        foreach ($id in $daemonPids) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
    }
    if (-not $KeepHub) {
        Write-Host '==> Stopping the hub (-KeepHub keeps it)'
        docker compose -f $compose down *> $null
    } else {
        Write-Host "==> The hub stays at http://localhost:$HubPort/?token=$token (docker compose -f docker/demo/compose.yml down)"
    }
    Pop-Location
}
