<#
.SYNOPSIS
    Installs or updates clabe and its daemon on this Windows PC, then restarts the daemon and checks it answers.

.DESCRIPTION
    One line, like uv's installer (run it as the user the daemon runs for — the user logged on to the rig):

        powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1 | iex"

    Options come from parameters, or — for `irm ... | iex`, which cannot pass any — from environment variables:

        -Version / CLABE_VERSION      clabe version to install (default: the latest release)
        -Package / CLABE_PACKAGE      any uv package spec instead (aind-clabe[agents]==0.18.0, git+https://...@v0.18.0)
        -Token   / CLABE_TOKEN        the daemon's token, saved as this user's CLABE_TOKEN
        -Config  / CLABE_CONFIG       a clabe.yml on this PC to install as %PROGRAMDATA%\clabe.yml
        -Force   / CLABE_FORCE=1      restart even if the daemon runs a session (it is interrupted)
        -NoInstallUv                  fail instead of installing uv when it is missing
        -DryRun  / CLABE_DRY_RUN=1    print the commands instead of running them

    With arguments, from a script or over SSH:

        & ([scriptblock]::Create((irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1))) -Version 0.18.0

    Steps, each safe to repeat: install uv if missing; set the token and the configuration; install clabe as a uv tool
    (a stable clabe.exe that starts without the network); register the clabe-daemon scheduled task (at logon, in the
    user's desktop session, restarted on failure); restart the daemon and wait until it answers. A daemon running a
    session is never interrupted unless -Force. Any failure throws (a non-zero exit code for `powershell -c`).
#>
param(
    [string]$Version = $env:CLABE_VERSION,
    [string]$Package = $env:CLABE_PACKAGE,
    [string]$Token = $env:CLABE_TOKEN,
    [string]$Config = $env:CLABE_CONFIG,
    [switch]$Force = ($env:CLABE_FORCE -eq '1'),
    [switch]$NoInstallUv,
    [switch]$DryRun = ($env:CLABE_DRY_RUN -eq '1'),
    [int]$Timeout = 120
)

function Install-Clabe {
    $ErrorActionPreference = 'Stop'

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

    $spec = if ($Package) { $Package } elseif ($Version) { "aind-clabe==$Version" } else { 'aind-clabe' }

    # uv. A fresh session (e.g. over SSH) may not have the user's PATH yet: look where uv's installer puts it too.
    $userBin = Join-Path $env:USERPROFILE '.local\bin'
    if (-not (($env:PATH -split ';') -contains $userBin)) { $env:PATH = "$userBin;$env:PATH" }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        if ($NoInstallUv) { throw "uv is not installed for $env:USERNAME (-NoInstallUv was given)." }
        Write-Host '==> Installing uv'
        if (-not $DryRun) { Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression }
    }
    $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $uv) { $uv = Join-Path $userBin 'uv.exe' }

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

    Invoke-ClabeStep "Installing $spec" $uv @('tool', 'install', '--force', $spec)
    $clabe = Join-Path $userBin 'clabe.exe'
    Invoke-ClabeStep 'Registering the clabe-daemon task' $clabe @('daemon', 'install')
    $restart = @('daemon', 'restart', '--timeout', "$Timeout")
    if ($Force) { $restart += '--force' }
    Invoke-ClabeStep 'Restarting the daemon' $clabe $restart
    Invoke-ClabeStep 'Status' $clabe @('daemon', 'status')
    Write-Host "==> clabe is installed ($spec). Manage it with: clabe daemon status | restart | uninstall"
}

Install-Clabe
