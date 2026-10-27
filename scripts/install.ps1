# Copyright (C) 2026 2351485 ALBERTA INC.
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Install the PAE native bundle (the directory this script lives in)
# into a prefix on Windows.
#
#   .\install.ps1 [-Prefix DIR] [-RegisterTask] [-RegisterService]
#
# Default prefix: %LOCALAPPDATA%\Programs\PAE. The prefix is added to
# your *user* PATH so `pae` works from any prompt. Your data is NOT
# installed here — it lives in PAE_HOME (%LOCALAPPDATA%\PAE) and
# survives upgrades. To upgrade, unpack the new bundle and re-run
# its install.ps1.
#
# -RegisterTask: register a logon scheduled task that runs
#   `pae serve` hidden at sign-in. This is the supported autostart.
#
# -RegisterService: register a real Windows service. PAE's two
#   processes are ordinary executables, not service binaries, so
#   this needs a service wrapper (WinSW or NSSM) on PATH: the
#   wrapper hosts `pae.ps1 serve`. If neither wrapper is found, this
#   falls back to the scheduled task and says so — sc.exe alone
#   cannot host a script, and pretending otherwise would install a
#   service that fails with error 1053 on first start.

param(
    [string]$Prefix = (Join-Path $env:LOCALAPPDATA 'Programs\PAE'),
    [switch]$RegisterTask,
    [switch]$RegisterService
)

$ErrorActionPreference = 'Stop'

$Src = $PSScriptRoot
if (-not (Test-Path (Join-Path $Src 'VERSION')) -or
    -not (Test-Path (Join-Path $Src 'pae.ps1'))) {
    throw "This script must run from inside an unpacked PAE bundle (expected VERSION and pae.ps1 next to install.ps1)."
}

$version = (Get-Content (Join-Path $Src 'VERSION') -Raw).Trim()

# --- Copy the bundle into the prefix -----------------------------------
if ($Src -ne $Prefix) {
    if ((Test-Path $Prefix) -and -not (Test-Path (Join-Path $Prefix 'VERSION'))) {
        throw "Refusing to overwrite $Prefix - it exists and is not a previous PAE install (no VERSION file). Move it away or choose another -Prefix."
    }
    Write-Host "Installing PAE $version to $Prefix ..."
    New-Item -ItemType Directory -Force -Path $Prefix | Out-Null
    Get-ChildItem -Path $Prefix -Force | Remove-Item -Recurse -Force
    Copy-Item -Path (Join-Path $Src '*') -Destination $Prefix -Recurse -Force
}
else {
    Write-Host "Bundle already lives in the prefix $Prefix - skipping copy."
}

# --- User PATH ------------------------------------------------------------
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (($userPath -split ';') -notcontains $Prefix) {
    [Environment]::SetEnvironmentVariable(
        'Path', ($userPath.TrimEnd(';') + ';' + $Prefix), 'User')
    Write-Host "Added $Prefix to your user PATH (new prompts pick it up)."
}

function Register-PaeTask {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Prefix\pae.ps1`" serve"
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName 'PAE' -Action $action -Trigger $trigger `
        -Settings $settings -Description 'PAE - Personal Analytics Engine (pae serve)' `
        -Force | Out-Null
    Write-Host "Registered scheduled task 'PAE' (runs pae serve at logon)."
}

if ($RegisterTask) {
    Register-PaeTask
}

if ($RegisterService) {
    # A Windows service must be hosted by a service binary. Prefer a
    # wrapper if the user already has one; never fake it with sc.exe
    # pointed at a script.
    $winsw = Get-Command 'winsw' -ErrorAction SilentlyContinue
    $nssm = Get-Command 'nssm' -ErrorAction SilentlyContinue
    if ($winsw) {
        $svcExe = Join-Path $Prefix 'pae-service.exe'
        Copy-Item $winsw.Source $svcExe -Force
        $config = @"
<service>
  <id>PAE</id>
  <name>PAE - Personal Analytics Engine</name>
  <description>PAE native bundle (engine + analytics via pae serve).</description>
  <executable>powershell.exe</executable>
  <arguments>-NoProfile -ExecutionPolicy Bypass -File "$Prefix\pae.ps1" serve</arguments>
  <logpath>$env:LOCALAPPDATA\PAE\logs</logpath>
</service>
"@
        Set-Content -Path (Join-Path $Prefix 'pae-service.xml') -Value $config
        & $svcExe install
        Write-Host "Registered Windows service 'PAE' via WinSW. Start it with: Start-Service PAE"
    }
    elseif ($nssm) {
        & $nssm.Source install PAE 'powershell.exe' `
            "-NoProfile -ExecutionPolicy Bypass -File `"$Prefix\pae.ps1`" serve"
        & $nssm.Source set PAE DisplayName 'PAE - Personal Analytics Engine'
        & $nssm.Source set PAE Start SERVICE_AUTO_START
        Write-Host "Registered Windows service 'PAE' via NSSM. Start it with: Start-Service PAE"
    }
    else {
        Write-Warning "No service wrapper (WinSW or NSSM) found on PATH, so a true Windows service cannot be registered honestly - sc.exe cannot host a script."
        Write-Warning "Falling back to the logon scheduled task instead."
        Register-PaeTask
    }
}

Write-Host ""
Write-Host "PAE $version installed. Next steps:"
Write-Host "  1. Open a NEW prompt (so the PATH change applies)."
Write-Host "  2. Start PAE:          pae start"
Write-Host "  3. Open the dashboard: http://localhost:3001"
Write-Host "  4. First run? Load the fictional demo data: pae seed"
Write-Host "  5. Stop anytime:       pae stop"
Write-Host ""
Write-Host "Your data lives in %LOCALAPPDATA%\PAE (override with PAE_HOME)"
Write-Host "and is not touched by upgrades."
