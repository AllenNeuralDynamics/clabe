<#
.SYNOPSIS
    Installs or updates clabe and its daemon on this Windows PC: installs uv and the clabe command if needed, then runs
    `clabe daemon deploy`.

.DESCRIPTION
    One line, like uv's installer (run it as the user the daemon runs for — the user logged on to the rig):

        powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1 | iex"

    Options come from parameters, or — for `irm ... | iex`, which cannot pass any — from environment variables:

        -Version    / DAEMON_CLABE_VERSION      clabe release for the daemon (default: the latest)
        -Lock       / DAEMON_CLABE_LOCK         a repository (or its uv.lock): the clabe it locks, instead
        -Package    / DAEMON_CLABE_PACKAGE      any uv requirement for the daemon instead
        -Extras     / DAEMON_CLABE_EXTRAS       aind-clabe's extras for the daemon (default frontend,otel)
        -Hubs       / DAEMON_CLABE_DEPLOY_HUBS  hubs to enroll with, ';'-separated ("none": no hub; default: unchanged)
        -Token      / DAEMON_CLABE_TOKEN        the daemon's token, saved as this user's DAEMON_CLABE_TOKEN
        -Config     / DAEMON_CLABE_CONFIG       a clabe.yml to install as %PROGRAMDATA%\clabe.yml: a path on this PC, or -
                                                to read it from standard input (over SSH: ssh rig "..." < clabe.yml)
        -CliPackage / DAEMON_CLABE_CLI_PACKAGE  the uv requirement of the `clabe` command (default: the latest
                                                release, whatever -Version the daemon runs)
        -StopDaemon / DAEMON_CLABE_STOP=1       only install the clabe command and stop the daemon (e.g. before updating
                                                an experiment's environment); deploy later
        -Force      / DAEMON_CLABE_FORCE=1      stop or restart the daemon even if it runs a session (it is interrupted)
        -DryRun     / DAEMON_CLABE_DRY_RUN=1    print what would be done

    With arguments, from a script or over SSH:

        & ([scriptblock]::Create((irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1))) -Version 0.18.0

    The daemon's own clabe is not the `clabe` command's: `clabe daemon deploy` installs it in its own environment
    (%LOCALAPPDATA%\clabe\versions), next to the running one, and switches the daemon only when it changes. So
    updating the `clabe` command (`uv tool install`) never touches the running daemon. Daemons installed by earlier
    versions of this script ran from the `clabe` command's own files: those are stopped once, before it is replaced.
    Any failure throws (a non-zero exit code for `powershell -c`).
#>
param(
    [string]$Version = $env:DAEMON_CLABE_VERSION,
    [string]$Lock = $env:DAEMON_CLABE_LOCK,
    [string]$Package = $env:DAEMON_CLABE_PACKAGE,
    [string]$Extras = $env:DAEMON_CLABE_EXTRAS,
    [string]$Hubs = $env:DAEMON_CLABE_DEPLOY_HUBS,
    [string]$Token = $env:DAEMON_CLABE_TOKEN,
    [string]$Config = $env:DAEMON_CLABE_CONFIG,
    [string]$CliPackage = $env:DAEMON_CLABE_CLI_PACKAGE,
    [switch]$StopDaemon = ($env:DAEMON_CLABE_STOP -eq '1'),
    [switch]$Force = ($env:DAEMON_CLABE_FORCE -eq '1'),
    [switch]$DryRun = ($env:DAEMON_CLABE_DRY_RUN -eq '1'),
    [int]$Timeout = 120
)

function Install-Clabe {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    $taskName = 'clabe-daemon'

    function Invoke-Step([string]$Description, [string]$Program, [string[]]$Arguments) {
        Write-Host "==> $Description"
        if ($DryRun) {
            Write-Host "    $Program $($Arguments -join ' ')"
            return
        }
        & $Program @Arguments
        if ($LASTEXITCODE -ne 0) { throw "clabe install failed ($LASTEXITCODE) at: $Description" }
    }

    function Get-DaemonPort {
        $port = $env:DAEMON_CLABE_PORT
        if (-not $port) { $port = [Environment]::GetEnvironmentVariable('DAEMON_CLABE_PORT', 'User') }
        if ($port) { [int]$port } else { 24070 }
    }

    # The daemon's /api/daemon/info status (idle, busy, leased, unauthorized), or $null when nothing answers.
    function Get-DaemonStatus {
        $headers = @{}
        $tokenNow = @($Token, $env:DAEMON_CLABE_TOKEN, [Environment]::GetEnvironmentVariable('DAEMON_CLABE_TOKEN', 'User')) |
            Where-Object { $_ } | Select-Object -First 1
        if ($tokenNow) { $headers['Authorization'] = "Bearer $tokenNow" }
        try {
            return (Invoke-RestMethod -Uri "http://127.0.0.1:$(Get-DaemonPort)/api/daemon/info" -Headers $headers -TimeoutSec 3 -UseBasicParsing)
        } catch {
            $response = $_.Exception.Response
            if ($response -and [int]$response.StatusCode -eq 401) { return [pscustomobject]@{ status = 'unauthorized' } }
            return $null
        }
    }

    # uv. A fresh session (e.g. over SSH) may not have the user's PATH yet: look where uv's installer puts it too.
    $userBin = Join-Path $env:USERPROFILE '.local\bin'
    if (-not (($env:PATH -split ';') -contains $userBin)) { $env:PATH = "$userBin;$env:PATH" }
    $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $uv) {
        Write-Host "==> Installing uv for $env:USERNAME"
        if ($DryRun) {
            Write-Host '    irm https://astral.sh/uv/install.ps1 | iex'
            $uv = Join-Path $userBin 'uv.exe'
        } else {
            Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
            $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
            if (-not $uv) { $uv = Join-Path $userBin 'uv.exe' }
            if (-not (Test-Path -LiteralPath $uv)) { throw "uv's installer did not install uv for $env:USERNAME." }
        }
    }
    $toolBin = if ($DryRun -and -not (Test-Path -LiteralPath $uv)) { $userBin } else { (& $uv tool dir --bin | Select-Object -Last 1).Trim() }
    $toolDir = if ($DryRun -and -not (Test-Path -LiteralPath $uv)) { '' } else { (& $uv tool dir | Select-Object -Last 1).Trim() }
    $clabe = Join-Path $toolBin 'clabe.exe'

    # Never interrupt a session: check before changing anything.
    $daemon = Get-DaemonStatus
    if ($daemon -and ($daemon.status -in @('busy', 'leased')) -and -not $Force) {
        $what = if ($daemon.status -eq 'busy') { 'runs an experiment' } else { "is leased by $($daemon.leased_by)" }
        throw "The daemon ${what}: installing would interrupt it. Wait, or pass -Force. Nothing was changed."
    }

    if ($Token) {
        Write-Host "==> Setting DAEMON_CLABE_TOKEN for $env:USERNAME"
        if (-not $DryRun) {
            [Environment]::SetEnvironmentVariable('DAEMON_CLABE_TOKEN', $Token, 'User')
            $env:DAEMON_CLABE_TOKEN = $Token
        }
    }
    if ($Config) {
        $target = Join-Path $env:ProgramData 'clabe.yml'
        if ($Config -eq '-') {
            $text = [Console]::In.ReadToEnd()
            if (-not $text.Trim()) { throw 'No configuration on standard input (-Config -).' }
            Write-Host "==> Installing the configuration from standard input as $target"
            if ($DryRun) { Write-Host $text } else { Set-Content -LiteralPath $target -Value $text -Encoding UTF8 -NoNewline }
        } else {
            if (-not (Test-Path -LiteralPath $Config)) { throw "No configuration at $Config." }
            Write-Host "==> Installing $Config as $target"
            if (-not $DryRun) { Copy-Item -LiteralPath $Config -Destination $target -Force }
        }
    }

    # A daemon installed by an earlier version of this script runs the `clabe` command's own files, which Windows
    # locks: stop it before replacing them (once: `clabe daemon deploy` moves it to its own environment).
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    $runs = if ($task) { "$($task.Actions[0].Execute)".Trim('"') } else { '' }
    $fromTool = $runs -and $toolDir -and (
        $runs.StartsWith($toolBin, [StringComparison]::OrdinalIgnoreCase) -or $runs.StartsWith($toolDir, [StringComparison]::OrdinalIgnoreCase))
    $stoppedOld = $false
    if ($fromTool -and (($task.State -eq 'Running') -or $daemon)) {
        Write-Host "==> Stopping the daemon (it runs $runs, which is replaced)"
        if (-not $DryRun) {
            & schtasks /End /TN $taskName | Out-Null
            $deadline = (Get-Date).AddSeconds(30)
            while (((Get-ScheduledTask -TaskName $taskName).State -eq 'Running') -or (Get-DaemonStatus)) {
                if ((Get-Date) -gt $deadline) { throw "The daemon still answers on port $(Get-DaemonPort) 30 s after ending the $taskName task: nothing was installed." }
                Start-Sleep -Milliseconds 500
            }
        }
        $stoppedOld = $true
    }

    # The latest clabe, whatever the daemon runs: it is only the command (`clabe daemon deploy` installs the daemon's
    # own), and an older release has no `clabe daemon deploy`.
    $cliSpec = if ($CliPackage) { $CliPackage } else { 'aind-clabe[frontend,otel]' }
    try {
        Invoke-Step "Installing the clabe command ($cliSpec)" $uv @('tool', 'install', '--upgrade', $cliSpec)
    } catch {
        if ($stoppedOld) {
            & schtasks /Run /TN $taskName | Out-Null
            throw "$_ The daemon was started again with the clabe it had."
        }
        throw
    }
    # So later sessions (a new SSH session, the next pipeline step) find `clabe` on their PATH; nothing when it is there.
    Invoke-Step "Adding $toolBin to $env:USERNAME's PATH" $uv @('tool', 'update-shell')

    if ($StopDaemon) {
        $stop = @('daemon', 'stop', '--if-installed')
        if ($Force) { $stop += '--force' }
        Invoke-Step 'Stopping the daemon' $clabe $stop
        return
    }

    $deploy = @('daemon', 'deploy', '--timeout', "$Timeout")
    if ($Version) { $deploy += @('--version', $Version) }
    if ($Lock) { $deploy += @('--lock', $Lock) }
    if ($Package) { $deploy += @('--package', $Package) }
    if ($Extras) { $deploy += @('--extras', $Extras) }
    if ($Hubs) { $deploy += @('--hubs', $Hubs) }
    if ($Force) { $deploy += '--force' }
    Invoke-Step 'Deploying the daemon' $clabe $deploy
    Invoke-Step 'Status' $clabe @('daemon', 'status')
    Write-Host '==> Done. Update or go back with: clabe daemon deploy --version <version>'
}

Install-Clabe
