# Copyright (C) 2026 2351485 ALBERTA INC.
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# PAE launcher for Windows — runs the native bundle (no containers,
# no runtimes to install: the engine is a native binary and the
# analytics service is a self-contained frozen executable).
#
#   pae start     start engine + analytics hidden, in the background
#   pae stop      stop both
#   pae restart   stop, then start
#   pae status    process + health status, prints the dashboard URL
#   pae seed      load the fictional demo data into a running stack
#   pae logs      show the logs ([engine|analytics] [lines])
#   pae serve     run both in the foreground (service wrappers, tasks)
#   pae version   print the bundle version
#
# Data lives in PAE_HOME (default %LOCALAPPDATA%\PAE):
#   data\   the two SQLite stores
#   run\    PID files
#   logs\   engine.log, analytics.log
#
# Ports: engine 3001, analytics 3002, both loopback. Override with
# PAE_ENGINE_PORT / PAE_ANALYTICS_PORT.

param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status', 'seed', 'logs', 'serve', 'version')]
    [string]$Command = 'status',

    [Parameter(Position = 1)]
    [string]$Arg1 = '',

    [Parameter(Position = 2)]
    [string]$Arg2 = ''
)

$ErrorActionPreference = 'Stop'

$Root = $PSScriptRoot
if (-not $env:PAE_HOME) {
    $env:PAE_HOME = Join-Path $env:LOCALAPPDATA 'PAE'
}
$DataDir = Join-Path $env:PAE_HOME 'data'
$RunDir = Join-Path $env:PAE_HOME 'run'
$LogDir = Join-Path $env:PAE_HOME 'logs'
foreach ($d in @(
        (Join-Path $DataDir 'engine'), (Join-Path $DataDir 'analytics'),
        $RunDir, $LogDir)) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

$EnginePort = if ($env:PAE_ENGINE_PORT) { [int]$env:PAE_ENGINE_PORT } else { 3001 }
$AnalyticsPort = if ($env:PAE_ANALYTICS_PORT) { [int]$env:PAE_ANALYTICS_PORT } else { 3002 }
$EngineUrl = "http://127.0.0.1:$EnginePort"
$AnalyticsUrl = "http://127.0.0.1:$AnalyticsPort"

$EngineBin = Join-Path $Root 'bin\pae-engine.exe'
$AnalyticsBin = Join-Path $Root 'analytics\pae-analytics.exe'
$SeedBin = Join-Path $Root 'bin\pae-seed.exe'
$EnginePidFile = Join-Path $RunDir 'engine.pid'
$AnalyticsPidFile = Join-Path $RunDir 'analytics.pid'

function Get-PidFromFile([string]$Path) {
    if (Test-Path $Path) {
        $raw = (Get-Content $Path -Raw).Trim()
        if ($raw -match '^\d+$') { return [int]$raw }
    }
    return $null
}

function Test-PidAlive($ProcId) {
    if ($null -eq $ProcId) { return $false }
    return $null -ne (Get-Process -Id $ProcId -ErrorAction SilentlyContinue)
}

function Test-HttpOk([string]$Url) {
    try {
        $r = Invoke-WebRequest -Uri $Url -TimeoutSec 3 -UseBasicParsing
        return $r.StatusCode -ge 200 -and $r.StatusCode -lt 400
    }
    catch { return $false }
}

function Wait-Healthy([string]$Name, [string]$Url, [int]$TimeoutSec) {
    for ($i = 0; $i -lt $TimeoutSec; $i++) {
        if (Test-HttpOk $Url) {
            Write-Host "$Name healthy at $Url"
            return $true
        }
        Start-Sleep -Seconds 1
    }
    Write-Warning "$Name did not answer $Url within ${TimeoutSec}s. See: pae logs"
    return $false
}

function Set-CommonEnv {
    # The dashboard is served by the engine (origin :$EnginePort) and
    # calls analytics at :$AnalyticsPort; analytics' CORS allowlist
    # must name both loopback origins.
    $origins = @(
        "http://localhost:$EnginePort", "http://127.0.0.1:$EnginePort",
        "http://localhost:$AnalyticsPort", "http://127.0.0.1:$AnalyticsPort"
    )
    if ($env:PAE_CORS_ORIGINS) { $origins += $env:PAE_CORS_ORIGINS }
    $env:PAE_CORS_ORIGINS = ($origins | Select-Object -Unique) -join ','
}

function Start-Engine {
    $existing = Get-PidFromFile $EnginePidFile
    if (Test-PidAlive $existing) {
        Write-Host "engine already running (pid $existing)"
        return
    }
    Write-Host "starting engine on 127.0.0.1:$EnginePort ..."
    $env:PAE_DB_PATH = Join-Path $DataDir 'engine\pae.db'
    $env:PAE_UI_DIR = Join-Path $Root 'ui'
    $env:PAE_ENGINE_PORT = "$EnginePort"
    $env:PAE_ANALYTICS_URL = $AnalyticsUrl
    $p = Start-Process -FilePath $EngineBin -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $LogDir 'engine.out.log') `
        -RedirectStandardError (Join-Path $LogDir 'engine.log')
    Set-Content -Path $EnginePidFile -Value $p.Id
}

function Start-Analytics {
    $existing = Get-PidFromFile $AnalyticsPidFile
    if (Test-PidAlive $existing) {
        Write-Host "analytics already running (pid $existing)"
        return
    }
    Write-Host "starting analytics on 127.0.0.1:$AnalyticsPort ..."
    $env:PAE_DB_PATH = Join-Path $DataDir 'analytics\pae.db'
    $env:PAE_ENGINE_URL = $EngineUrl
    $env:PAE_ANALYTICS_PORT = "$AnalyticsPort"
    $p = Start-Process -FilePath $AnalyticsBin -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $LogDir 'analytics.out.log') `
        -RedirectStandardError (Join-Path $LogDir 'analytics.log')
    Set-Content -Path $AnalyticsPidFile -Value $p.Id
}

function Start-Pae {
    Set-CommonEnv
    Start-Engine
    Start-Analytics
    $okE = Wait-Healthy 'engine' "$EngineUrl/health" 60
    $okA = Wait-Healthy 'analytics' "$AnalyticsUrl/health" 60
    if ($okE -and $okA) {
        Write-Host ""
        Write-Host "PAE is running. Dashboard: http://localhost:$EnginePort"
        Write-Host "First run? Load the fictional demo data with: pae seed"
    }
}

function Stop-One([string]$Name, [string]$PidFile) {
    $procId = Get-PidFromFile $PidFile
    if (-not (Test-PidAlive $procId)) {
        Write-Host "$Name not running"
        return
    }
    Write-Host "stopping $Name (pid $procId) ..."
    Stop-Process -Id $procId -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 15; $i++) {
        if (-not (Test-PidAlive $procId)) { break }
        Start-Sleep -Seconds 1
    }
    if (Test-PidAlive $procId) {
        Write-Host "$Name did not stop in 15s; forcing"
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
}

function Stop-Pae {
    Stop-One 'analytics' $AnalyticsPidFile
    Stop-One 'engine' $EnginePidFile
    Remove-Item $EnginePidFile, $AnalyticsPidFile -ErrorAction SilentlyContinue
    Write-Host "PAE stopped. Your data is untouched in $DataDir"
}

function Show-Status {
    $ep = Get-PidFromFile $EnginePidFile
    $ap = Get-PidFromFile $AnalyticsPidFile
    if (Test-PidAlive $ep) { Write-Host "engine:    running (pid $ep, $EngineUrl)" }
    else { Write-Host "engine:    not running" }
    if (Test-PidAlive $ap) { Write-Host "analytics: running (pid $ap, $AnalyticsUrl)" }
    else { Write-Host "analytics: not running" }
    if (Test-PidAlive $ep) {
        if (Test-HttpOk "$EngineUrl/health") {
            Write-Host "dashboard: http://localhost:$EnginePort (healthy)"
        }
        else {
            Write-Host "dashboard: http://localhost:$EnginePort (not answering yet)"
        }
    }
    Write-Host "data dir:  $env:PAE_HOME"
}

function Invoke-Seed {
    $ep = Get-PidFromFile $EnginePidFile
    if (-not (Test-PidAlive $ep)) {
        Write-Error "PAE is not running. Start it first: pae start"
        exit 1
    }
    Write-Host "seeding fictional demo data ..."
    $env:PAE_ENGINE_URL = $EngineUrl
    $env:PAE_ANALYTICS_URL = $AnalyticsUrl
    & $SeedBin
    exit $LASTEXITCODE
}

function Show-Logs {
    $which = if ($Arg1) { $Arg1 } else { 'all' }
    $lines = if ($Arg2) { [int]$Arg2 } else { 100 }
    switch ($which) {
        'engine' { Get-Content (Join-Path $LogDir 'engine.log') -Tail $lines }
        'analytics' { Get-Content (Join-Path $LogDir 'analytics.log') -Tail $lines }
        'all' {
            Write-Host "=== engine (last $lines) ==="
            Get-Content (Join-Path $LogDir 'engine.log') -Tail $lines -ErrorAction SilentlyContinue
            Write-Host "=== analytics (last $lines) ==="
            Get-Content (Join-Path $LogDir 'analytics.log') -Tail $lines -ErrorAction SilentlyContinue
        }
        default {
            Write-Error "usage: pae logs [engine|analytics] [lines]"
            exit 2
        }
    }
}

function Invoke-Serve {
    # Foreground mode for service wrappers / scheduled tasks: run
    # both processes as children of this script; when this script is
    # stopped, stop them too.
    Set-CommonEnv
    Write-Host "pae serve: engine :$EnginePort, analytics :$AnalyticsPort"
    $env:PAE_DB_PATH = Join-Path $DataDir 'engine\pae.db'
    $env:PAE_UI_DIR = Join-Path $Root 'ui'
    $env:PAE_ENGINE_PORT = "$EnginePort"
    $env:PAE_ANALYTICS_URL = $AnalyticsUrl
    $engine = Start-Process -FilePath $EngineBin -NoNewWindow -PassThru `
        -RedirectStandardOutput (Join-Path $LogDir 'engine.out.log') `
        -RedirectStandardError (Join-Path $LogDir 'engine.log')
    Set-Content -Path $EnginePidFile -Value $engine.Id

    $env:PAE_DB_PATH = Join-Path $DataDir 'analytics\pae.db'
    $env:PAE_ENGINE_URL = $EngineUrl
    $env:PAE_ANALYTICS_PORT = "$AnalyticsPort"
    $analytics = Start-Process -FilePath $AnalyticsBin -NoNewWindow -PassThru `
        -RedirectStandardOutput (Join-Path $LogDir 'analytics.out.log') `
        -RedirectStandardError (Join-Path $LogDir 'analytics.log')
    Set-Content -Path $AnalyticsPidFile -Value $analytics.Id

    try {
        # Exit when either child exits so the supervisor restarts us.
        while (-not $engine.HasExited -and -not $analytics.HasExited) {
            Start-Sleep -Seconds 2
            $engine.Refresh(); $analytics.Refresh()
        }
    }
    finally {
        Stop-Process -Id $engine.Id -Force -ErrorAction SilentlyContinue
        Stop-Process -Id $analytics.Id -Force -ErrorAction SilentlyContinue
        Remove-Item $EnginePidFile, $AnalyticsPidFile -ErrorAction SilentlyContinue
    }
}

switch ($Command) {
    'start' { Start-Pae }
    'stop' { Stop-Pae }
    'restart' { Stop-Pae; Start-Pae }
    'status' { Show-Status }
    'seed' { Invoke-Seed }
    'logs' { Show-Logs }
    'serve' { Invoke-Serve }
    'version' {
        $v = Join-Path $Root 'VERSION'
        if (Test-Path $v) { Get-Content $v } else { Write-Host 'unknown' }
    }
}
