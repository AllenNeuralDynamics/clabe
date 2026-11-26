<#
.SYNOPSIS
    Installs or updates clabe and its daemon on this Windows PC, then (re)starts the daemon and checks it answers.

.DESCRIPTION
    One line, like uv's installer (run it as the user the daemon runs for — the user logged on to the rig):

        powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1 | iex"

    Options come from parameters, or — for `irm ... | iex`, which cannot pass any — from environment variables:

        -Version   / DAEMON_CLABE_VERSION      clabe version to install (default: the latest release)
        -Package   / DAEMON_CLABE_PACKAGE      any uv package spec instead (aind-clabe[frontend,otel,agents]==0.18.0, git+https://...)
        -Token     / DAEMON_CLABE_TOKEN        the daemon's token, saved as this user's DAEMON_CLABE_TOKEN
        -Config    / DAEMON_CLABE_CONFIG       a clabe.yml to install as %PROGRAMDATA%\clabe.yml: a path on this PC, or - to
                                        read it from standard input (over SSH: ssh rig "..." < clabe.yml)
        -Force     / DAEMON_CLABE_FORCE=1      stop the daemon even if it runs a session (it is interrupted)
        -Reinstall / DAEMON_CLABE_REINSTALL=1  install even when the requested clabe is already installed
        -DryRun    / DAEMON_CLABE_DRY_RUN=1    print the decision and the commands instead of running them

    uv must be installed for this user (https://docs.astral.sh/uv/); the script stops if it is not.

    With arguments, from a script or over SSH:

        & ([scriptblock]::Create((irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1))) -Version 0.18.0

    Steps, each safe to repeat: refuse if the daemon runs a session (unless -Force); set the token and the
    configuration; if the requested clabe is already installed, only register the task and restart the daemon.
    Otherwise stop the daemon (on Windows it locks the files uv replaces), install clabe as a uv tool, register the
    clabe-daemon scheduled task (at logon, in the user's desktop session, restarted on failure) and start the daemon.
    If the install fails, the previous clabe is installed and started again. Any failure throws (a non-zero exit code
    for `powershell -c`).
#>
param(
    [string]$Version = $env:DAEMON_CLABE_VERSION,
    [string]$Package = $env:DAEMON_CLABE_PACKAGE,
    [string]$Token = $env:DAEMON_CLABE_TOKEN,
    [string]$Config = $env:DAEMON_CLABE_CONFIG,
    [switch]$Force = ($env:DAEMON_CLABE_FORCE -eq '1'),
    [switch]$Reinstall = ($env:DAEMON_CLABE_REINSTALL -eq '1'),
    [switch]$DryRun = ($env:DAEMON_CLABE_DRY_RUN -eq '1'),
    [int]$Timeout = 120
)

function Install-Clabe {
    $ErrorActionPreference = 'Stop'
    $taskName = 'clabe-daemon'

    function Invoke-ClabeStep([string]$Description, [string]$Program, [string[]]$Arguments) {
        Write-Host "==> $Description"
        if ($DryRun) {
            Write-Host "    $Program $($Arguments -join ' ')"
            return
        }
        & $Program @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "clabe install failed ($LASTEXITCODE) at: $Description"
        }
    }

    function ConvertTo-PackageName([string]$Name) { $Name.ToLowerInvariant() -replace '[._]', '-' }

    function ConvertTo-GitUrl([string]$Url) { ($Url.ToLowerInvariant() -replace '^git\+', '' -replace '/$', '' -replace '\.git$', '') }

    # What the spec asks for: name, extras, and a version or a git commit when it pins one exactly.
    function Get-RequestedClabe([string]$Spec) {
        if ($Spec -notmatch '^\s*(?<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[(?<extras>[^\]]*)\])?\s*(?<rest>.*?)\s*$') { return $null }
        $rest = $Matches['rest']
        $requested = [pscustomobject]@{
            Name    = ConvertTo-PackageName $Matches['name']
            Extras  = @($Matches['extras'] -split ',' | ForEach-Object { $_.Trim().ToLowerInvariant() } | Where-Object { $_ } | Sort-Object -Unique)
            Version = $null
            GitUrl  = $null
            Commit  = $null
        }
        if ($rest -match '^==\s*(?<version>[^\s;,]+)$') {
            $requested.Version = $Matches['version']
        } elseif ($rest -match '^@\s*git\+(?<url>[^@#\s]+)@(?<commit>[0-9a-fA-F]{40})$') {
            $requested.GitUrl = ConvertTo-GitUrl $Matches['url']
            $requested.Commit = $Matches['commit'].ToLowerInvariant()
        }
        $requested
    }

    # The installed clabe, read with the tool's own Python (no clabe command needed: works with any clabe version).
    # $null when nothing is installed; Version is $null when the tool's environment is broken.
    function Get-InstalledClabe([string]$Name) {
        $toolPath = Join-Path $toolDir $Name
        $python = Join-Path $toolPath 'Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $python)) { return $null }
        $probe = @'
import json, sys, tomllib
from importlib import metadata
out = {'version': None, 'extras': None, 'url': None, 'vcs': None, 'commit': None, 'has_stop': False}
try:
    with open(sys.argv[1], 'rb') as f:
        requirements = tomllib.load(f)['tool']['requirements']
    out['extras'] = sorted(e.lower() for e in requirements[0].get('extras', []))
except Exception:
    pass
try:
    dist = metadata.distribution(sys.argv[2])
    out['version'] = dist.version
    direct = json.loads(dist.read_text('direct_url.json') or 'null') or {}
    out['url'] = direct.get('url')
    out['vcs'] = (direct.get('vcs_info') or {}).get('vcs')
    out['commit'] = (direct.get('vcs_info') or {}).get('commit_id')
except Exception:
    pass
try:
    import clabe.daemon._install as daemon_install
    out['has_stop'] = hasattr(daemon_install, 'stop')
except Exception:
    pass
print(json.dumps(out))
'@
        $script = Join-Path ([IO.Path]::GetTempPath()) "clabe-install-probe-$PID.py"
        Set-Content -LiteralPath $script -Value $probe -Encoding UTF8
        try {
            $output = & $python -I $script (Join-Path $toolPath 'uv-receipt.toml') $Name
        } finally {
            Remove-Item -LiteralPath $script -ErrorAction SilentlyContinue
        }
        $json = @($output | Where-Object { $_ -match '^\{' }) | Select-Object -Last 1
        if (-not $json) { return [pscustomobject]@{ Version = $null; Extras = $null; Url = $null; Vcs = $null; Commit = $null; HasStop = $false } }
        $info = $json | ConvertFrom-Json
        [pscustomobject]@{
            Version = $info.version
            Extras  = if ($null -ne $info.extras) { @($info.extras) } else { $null }
            Url     = $info.url
            Vcs     = $info.vcs
            Commit  = $info.commit
            HasStop = [bool]$info.has_stop
        }
    }

    # Why the installed clabe matches the request, or $null when it does not (or we cannot tell).
    function Test-SameClabe($Requested, $Installed) {
        if (-not $Requested -or -not $Installed -or -not $Installed.Version -or $null -eq $Installed.Extras) { return $null }
        if ((@($Requested.Extras) -join ',') -ne (@($Installed.Extras) -join ',')) { return $null }
        if ($Requested.Version) {
            if ($Installed.Url -or $Installed.Version -ne $Requested.Version) { return $null }
            return "clabe $($Installed.Version) is already installed"
        }
        if ($Requested.Commit) {
            if ($Installed.Vcs -ne 'git' -or -not $Installed.Commit -or -not $Installed.Url) { return $null }
            if ((ConvertTo-GitUrl $Installed.Url) -ne $Requested.GitUrl -or $Installed.Commit.ToLowerInvariant() -ne $Requested.Commit) { return $null }
            return "clabe $($Installed.Version) at commit $($Installed.Commit) is already installed"
        }
        $null  # not pinned (e.g. the latest release): install to be sure
    }

    # The spec that reinstalls what is installed now, or $null.
    function Get-RollbackSpec([string]$Name, $Installed) {
        if (-not $Installed -or -not $Installed.Version) { return $null }
        $extras = if ($Installed.Extras) { "[$($Installed.Extras -join ',')]" } else { '' }
        if ($Installed.Vcs -eq 'git' -and $Installed.Commit) { return "$Name$extras @ git+$($Installed.Url)@$($Installed.Commit)" }
        if ($Installed.Url) { return "$Name$extras @ $($Installed.Url)" }
        "$Name$extras==$($Installed.Version)"
    }

    function Get-DaemonPort {
        $port = $env:DAEMON_CLABE_PORT
        if (-not $port) { $port = [Environment]::GetEnvironmentVariable('DAEMON_CLABE_PORT', 'User') }
        if ($port) { [int]$port } else { 24070 }
    }

    # The daemon's /api/daemon/info (status idle, busy, leased or unauthorized), or $null when nothing answers.
    function Get-DaemonStatus {
        $tokens = @($Token, $env:DAEMON_CLABE_TOKEN, [Environment]::GetEnvironmentVariable('DAEMON_CLABE_TOKEN', 'User')) |
            Where-Object { $_ } | Select-Object -Unique
        if (-not $tokens) { $tokens = @('') }
        foreach ($candidate in $tokens) {
            $headers = @{}
            if ($candidate) { $headers['Authorization'] = "Bearer $candidate" }
            try {
                return Invoke-RestMethod -Uri "http://127.0.0.1:$daemonPort/api/daemon/info" -Headers $headers -TimeoutSec 3 -UseBasicParsing
            } catch {
                $response = $_.Exception.Response
                if (-not $response -or [int]$response.StatusCode -ne 401) { return $null }
            }
        }
        [pscustomobject]@{ status = 'unauthorized' }
    }

    function Get-TaskState { (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue).State }

    function Stop-Daemon($Installed) {
        $useClabe = $Installed -and $Installed.HasStop
        if ($DryRun) {
            Write-Host '==> Stopping the daemon'
            Write-Host $(if ($useClabe) { "    $clabe daemon stop --force" } else { "    schtasks /End /TN $taskName" })
            return
        }
        if (-not (Get-TaskState)) {
            if (Get-DaemonStatus) { throw "Something answers on port $daemonPort but the $taskName task is not installed: stop it, then run the installer again." }
            Write-Host "==> The $taskName task is not installed: no daemon to stop."
            return
        }
        if ($useClabe) {
            # --force: a busy or leased daemon was refused already, unless -Force.
            Invoke-ClabeStep 'Stopping the daemon' $clabe @('daemon', 'stop', '--force')
            return
        }
        # An older clabe without `clabe daemon stop`: end the task ourselves and wait until the port is free.
        Write-Host '==> Stopping the daemon'
        & schtasks /End /TN $taskName | Out-Null
        $deadline = (Get-Date).AddSeconds(30)
        while (((Get-TaskState) -eq 'Running') -or (Get-DaemonStatus)) {
            if ((Get-Date) -gt $deadline) { throw "The daemon still answers on port $daemonPort 30 s after ending the $taskName task: nothing was installed." }
            Start-Sleep -Milliseconds 500
        }
    }

    function Start-Daemon {
        Invoke-ClabeStep "Registering the $taskName task" $clabe @('daemon', 'install')
        $installed = if ($DryRun) { $null } else { Get-InstalledClabe $packageName }
        if ($DryRun -or ($installed -and $installed.HasStop)) {
            Invoke-ClabeStep 'Starting the daemon' $clabe @('daemon', 'start', '--timeout', "$Timeout")
        } else {
            Invoke-ClabeStep 'Starting the daemon' $clabe @('daemon', 'restart', '--timeout', "$Timeout", '--force')
        }
    }

    $spec = if ($Package) { $Package } elseif ($Version) { "aind-clabe[frontend,otel]==$Version" } else { 'aind-clabe[frontend,otel]' }

    # uv. A fresh session (e.g. over SSH) may not have the user's PATH yet: look where uv's installer puts it too.
    $userBin = Join-Path $env:USERPROFILE '.local\bin'
    if (-not (($env:PATH -split ';') -contains $userBin)) { $env:PATH = "$userBin;$env:PATH" }
    $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $uv) {
        throw "uv is not installed for $env:USERNAME: install it first (https://docs.astral.sh/uv/getting-started/installation/)."
    }
    $toolDir = (& $uv tool dir | Select-Object -Last 1).Trim()
    $clabe = Join-Path (& $uv tool dir --bin | Select-Object -Last 1).Trim() 'clabe.exe'

    $requested = Get-RequestedClabe $spec
    $packageName = if ($requested) { $requested.Name } else { 'aind-clabe' }
    $daemonPort = Get-DaemonPort

    # Never interrupt a session: check before changing anything.
    $daemon = Get-DaemonStatus
    if ($daemon -and ($daemon.status -in @('busy', 'leased'))) {
        $what = if ($daemon.status -eq 'busy') { 'runs an experiment' } else { "is leased by $($daemon.leased_by)" }
        if (-not $Force) { throw "The daemon ${what}: installing would interrupt it. Wait, or pass -Force. Nothing was changed." }
        Write-Host "==> The daemon ${what}; -Force: it is interrupted."
    } elseif ($daemon -and $daemon.status -eq 'unauthorized') {
        Write-Host "==> The daemon on port $daemonPort refuses the token: cannot tell whether it runs a session."
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
            Write-Host "==> Installing $Config as $target"
            if (-not $DryRun) { Copy-Item -LiteralPath $Config -Destination $target -Force }
        }
    }

    $installed = Get-InstalledClabe $packageName
    $same = Test-SameClabe $requested $installed
    if ($same -and -not $Reinstall) {
        Write-Host "==> Decision: restart only ($same)"
        Invoke-ClabeStep "Registering the $taskName task" $clabe @('daemon', 'install')
        $restart = @('daemon', 'restart', '--timeout', "$Timeout")
        if ($Force) { $restart += '--force' }
        Invoke-ClabeStep 'Restarting the daemon' $clabe $restart
        Invoke-ClabeStep 'Status' $clabe @('daemon', 'status')
        Write-Host "==> clabe is installed ($spec). Manage it with: clabe daemon status | stop | start | restart | uninstall"
        return
    }

    $why = if ($same) { "-Reinstall, though $same" }
        elseif (-not $installed) { 'clabe is not installed' }
        elseif (-not $installed.Version) { 'the installed clabe is broken' }
        elseif (-not ($requested -and ($requested.Version -or $requested.Commit))) { "clabe $($installed.Version) is installed; $spec does not pin a version" }
        else { "clabe $($installed.Version) is installed; $spec is requested" }
    Write-Host "==> Decision: stop, install, start ($why)"

    $rollback = Get-RollbackSpec $packageName $installed
    if ($rollback) { Write-Host "==> To restore if the install fails: $rollback" }
    Stop-Daemon $installed
    try {
        Invoke-ClabeStep "Installing $spec" $uv @('tool', 'install', '--force', $spec)
    } catch {
        $installError = "$_"
        if (-not $rollback) {
            $down = if ($installed) { ' The clabe daemon is down.' } else { '' }
            throw "Installing ${spec} failed: ${installError}. There was no working clabe to restore.$down"
        }
        try {
            Invoke-ClabeStep "Restoring $rollback" $uv @('tool', 'install', '--force', $rollback)
            Start-Daemon
        } catch {
            throw "Installing ${spec} failed: ${installError}. Restoring ${rollback} failed too: $($_). The clabe daemon is down."
        }
        throw "Installing ${spec} failed: ${installError}. Restored $rollback and started the daemon again."
    }
    Start-Daemon
    Invoke-ClabeStep 'Status' $clabe @('daemon', 'status')
    Write-Host "==> clabe is installed ($spec). Manage it with: clabe daemon status | stop | start | restart | uninstall"
}

Install-Clabe
