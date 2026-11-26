<#
.SYNOPSIS
    clabe on its own: the one-minute demo with nothing else running (no daemon, no hub, no Docker).

.DESCRIPTION
    From a checkout of clabe (needs only uv):

        powershell -ExecutionPolicy ByPass -File scripts/demo-local.ps1

    Runs examples/demo/main.py: the terminal UI in this window and the same run in a browser tab
    (http://localhost:8090, this computer only, so no token). Answer in either; the first answer wins. This is all a
    PC needs to run experiments: uv, git, and "aind-clabe[frontend]" in the experiment's repository.

    The run would announce itself to a daemon on this PC if one were running (so a hub could follow it); this
    demo makes sure it does not, even when one is.

    -NoBrowser opens no tab (the address is printed); -Frontend headless answers only in the browser (no terminal
    UI), console a plain console, tui the terminal UI.
    The web UI is built first when it is missing or older than ui-web/ (needs the .NET SDK; -SkipBuild skips that).

    -Lan opens the run to other PCs on the network (port 8090, or the next free one). Anyone who can reach the port
    could answer, so it then requires a token made for this demo, which is in the printed address. Windows Firewall
    may still block the port: allow it when asked, or as admin
        New-NetFirewallRule -DisplayName "clabe demo" -Direction Inbound -Protocol TCP -LocalPort 8090-8099 -Action Allow
#>
param(
    [switch]$NoBrowser,
    [switch]$Lan,
    [switch]$SkipBuild,
    [ValidateSet('auto', 'tui', 'console', 'headless')]
    [string]$Frontend = 'auto'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command 'uv' -ErrorAction SilentlyContinue)) { throw 'The demo needs uv on PATH (https://docs.astral.sh/uv/).' }

# The run's browser tab and the daemon's page need the built web UI (gitignored, so a fresh checkout lacks it).
$builtUi = Join-Path $root 'src/clabe/server/static/index.html'
$uiSources = Get-ChildItem (Join-Path $root 'ui-web') -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '[\/](bin|obj)[\/]' }
$uiStale = -not (Test-Path $builtUi) -or
    ($uiSources | Where-Object { $_.LastWriteTimeUtc -gt (Get-Item $builtUi).LastWriteTimeUtc } | Select-Object -First 1)
if (-not $SkipBuild -and $uiStale) {
    if (-not (Get-Command 'dotnet' -ErrorAction SilentlyContinue)) {
        throw 'The web UI is not built and the .NET SDK (dotnet) is needed to build it: install it, or pass -SkipBuild to run without the web UI.'
    }
    Write-Host '==> Building the web UI (missing or older than ui-web/; scripts/build_web.py)'
    Push-Location $root
    try {
        & uv run python scripts/build_web.py
        if ($LASTEXITCODE -ne 0) { throw 'Building the web UI failed.' }
    } finally {
        Pop-Location
    }
}

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
$savedToken = $env:CLABE_TOKEN
# The rig's name is the machine's aibs_comp_id (as on AIND rigs): give this PC one for the demo, unless it has one.
$savedRig = $env:aibs_comp_id
if (-not $env:aibs_comp_id) { $env:aibs_comp_id = 'DEMO-RIG' }
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
    if ($Lan) {
        # Open to the network: anyone reaching the port could answer, so require a token made for this demo.
        $bytes = New-Object byte[] 18
        [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $env:CLABE_TOKEN = 'demo' + [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
        $arguments += @('--host', '0.0.0.0')
        Write-Host "==> Open to the LAN: from another PC, use the address the run prints (it has the token)"
    }
    & uv @arguments
} finally {
    $env:DAEMON_CLABE_PORT = $saved
    $env:CLABE_TOKEN = $savedToken
    $env:aibs_comp_id = $savedRig
    Pop-Location
}
