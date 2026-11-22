<#
.SYNOPSIS
    clabe on its own: the one-minute demo with nothing else running (no daemon, no hub, no Docker).

.DESCRIPTION
    From a checkout of clabe (needs only uv):

        powershell -ExecutionPolicy ByPass -File scripts/demo-local.ps1

    Runs examples/demo/main.py: the terminal UI in this window and the same run in a browser tab
    (http://localhost:8090, this computer only, so no token). Answer in either; the first answer wins. This is all a
    PC needs to run experiments: uv, git, and the aind-clabe package in the experiment's repository.

    The run would announce itself to a daemon on this PC if one were running (so a hub could follow it); this
    demo makes sure it does not, even when one is.

    -NoBrowser opens no tab (the address is printed); -Frontend headless answers only in the browser (no terminal
    UI), console a plain console, tui the terminal UI.
#>
param(
    [switch]$NoBrowser,
    [ValidateSet('auto', 'tui', 'console', 'headless')]
    [string]$Frontend = 'auto'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command 'uv' -ErrorAction SilentlyContinue)) { throw 'The demo needs uv on PATH (https://docs.astral.sh/uv/).' }

function Test-PortFree([int]$Port) {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    try {
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        $listener.Stop()
    }
}

# No daemon: if one runs on this PC (its port: DAEMON_CLABE_PORT, else 24070), point this run elsewhere, where
# nothing answers. Announcing is best effort, so the run just goes on unseen, as on a PC without a daemon.
$daemonPort = if ($env:DAEMON_CLABE_PORT) { [int]$env:DAEMON_CLABE_PORT } else { 24070 }
$saved = $env:DAEMON_CLABE_PORT
if (-not (Test-PortFree $daemonPort)) {
    $unused = 24170
    while (-not (Test-PortFree $unused)) { $unused++ }
    Write-Host "==> A daemon (or something) listens on port $daemonPort; this run will not announce itself to it"
    $env:DAEMON_CLABE_PORT = "$unused"
}

Push-Location $root
try {
    Write-Host '==> Running the demo on its own: answer here or in the browser tab it opens'
    $arguments = @('run', 'clabe', 'run', 'examples/demo/main.py', '--allow-dirty', '--skip-hardware-validation')
    $arguments += @('--frontend', $Frontend)
    if (-not $NoBrowser) { $arguments += '--open-browser' }
    & uv @arguments
} finally {
    $env:DAEMON_CLABE_PORT = $saved
    Pop-Location
}
