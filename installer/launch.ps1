#Requires -Version 5.1
<#
.SYNOPSIS
  Launch the Tick Downloader web UI.

.PARAMETER CheckOnly
  Validate setup (doctor) and exit without starting the server.

.PARAMETER NoBrowser
  Do not open the browser automatically.

.PARAMETER Port
  Web server port (default 8080).

.PARAMETER Host
  Bind address (default 127.0.0.1).
#>
[CmdletBinding()]
param(
  [Alias('check-only')]
  [switch] $CheckOnly,
  [Alias('no-browser')]
  [switch] $NoBrowser,
  [int] $Port = 8080,
  [string] $BindHost = '127.0.0.1'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$VenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$MainPy = Join-Path $RepoRoot 'main.py'
$LocalConfigFile = Join-Path $RepoRoot '.dukascopy.local.ps1'

function Write-Step([string] $Message) {
  Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Write-Ok([string] $Message) {
  Write-Host "    OK  $Message" -ForegroundColor Green
}

function Write-WarnLine([string] $Message) {
  Write-Host "    !!  $Message" -ForegroundColor Yellow
}

function Write-DoctorRow([string] $Label, [string] $Detail, [ValidateSet('ok', 'warn', 'fail')] [string] $Level = 'ok') {
  $prefix = switch ($Level) {
    'ok' { '    OK  ' }
    'warn' { '    !!  ' }
    'fail' { '    XX  ' }
  }
  $color = switch ($Level) {
    'ok' { 'Green' }
    'warn' { 'Yellow' }
    'fail' { 'Red' }
  }
  $line = $prefix + $Label
  if ($Detail) { $line += " - $Detail" }
  Write-Host $line -ForegroundColor $color
}

function Import-LocalLauncherConfig {
  if (Test-Path -LiteralPath $LocalConfigFile) {
    Write-Step 'Loading .dukascopy.local.ps1'
    . $LocalConfigFile
    Write-Ok 'local overrides loaded'
  }
}

function Ensure-VenvPython {
  Write-Step 'Checking virtual environment'
  if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw '.venv not found - run run.bat to install first.'
  }
  $ver = & $VenvPython --version 2>&1
  Write-Ok "$ver at $VenvPython"
  return $VenvPython
}

function Test-FastApiInstalled([string] $PythonExe) {
  & $PythonExe -c 'import fastapi, uvicorn' 2>$null
  return $LASTEXITCODE -eq 0
}

function Get-PortListener([int] $LocalPort) {
  try {
    return Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction Stop |
      Select-Object -First 1
  } catch {
    return $null
  }
}

function Get-WebSettingsMt5([string] $PythonExe) {
  $script = @"
import json
from pathlib import Path
p = Path(r'$RepoRoot') / 'data' / 'web_settings.json'
if not p.is_file():
    print('{}')
else:
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
        print(json.dumps(data.get('mt5', {})))
    except Exception:
        print('{}')
"@
  $raw = & $PythonExe -c $script 2>$null
  if (-not $raw) { return @{} }
  try {
    return $raw | ConvertFrom-Json
  } catch {
    return @{}
  }
}

function Invoke-LauncherDoctor([string] $PythonExe) {
  Write-Host ''
  Write-Host 'Launcher doctor' -ForegroundColor White

  if (Test-Path -LiteralPath $VenvPython) {
    Write-DoctorRow 'venv' $VenvPython
  } else {
    Write-DoctorRow 'venv' 'missing - run run.bat' 'fail'
  }

  if (Test-FastApiInstalled $PythonExe) {
    Write-DoctorRow 'dependencies' 'fastapi + uvicorn importable'
  } else {
    Write-DoctorRow 'dependencies' 'missing - run run.bat' 'fail'
  }

  $dataDir = Join-Path $RepoRoot 'data'
  if (Test-Path -LiteralPath $dataDir) {
    Write-DoctorRow 'data/' $dataDir
  } else {
    Write-DoctorRow 'data/' 'missing' 'warn'
  }

  $bundledEx5 = Join-Path $RepoRoot 'mt5\DukascopyTickImport.ex5'
  if (Test-Path -LiteralPath $bundledEx5) {
    Write-DoctorRow 'DukascopyTickImport.ex5' 'bundled in repo'
  } else {
    Write-DoctorRow 'DukascopyTickImport.ex5' 'not compiled - compile in MetaEditor before MT5 import' 'warn'
  }

  $mt5 = Get-WebSettingsMt5 -PythonExe $PythonExe
  $terminalExe = [string]$mt5.terminal_exe
  if ($terminalExe -and (Test-Path -LiteralPath $terminalExe)) {
    Write-DoctorRow 'MT5 terminal' $terminalExe
  } elseif ($terminalExe) {
    Write-DoctorRow 'MT5 terminal' "$terminalExe (path missing)" 'warn'
  } else {
    Write-DoctorRow 'MT5 terminal' 'not configured - set in Settings tab' 'warn'
  }

  $dataPath = [string]$mt5.data_path
  if ($dataPath -and (Test-Path -LiteralPath $dataPath)) {
    Write-DoctorRow 'MT5 data folder' $dataPath
    $eaPath = Join-Path $dataPath 'MQL5\Scripts\dukascopy\DukascopyTickImport.ex5'
    if (Test-Path -LiteralPath $eaPath) {
      Write-DoctorRow 'Import script in MT5' $eaPath
    } else {
      Write-DoctorRow 'Import script in MT5' 'not installed yet (copied on first import)' 'warn'
    }
  } elseif ($dataPath) {
    Write-DoctorRow 'MT5 data folder' "$dataPath (path missing)" 'warn'
  }

  $listener = Get-PortListener -LocalPort $Port
  if ($listener) {
    Write-DoctorRow "port $Port" "already listening (PID $($listener.OwningProcess))" 'warn'
  } else {
    Write-DoctorRow "port $Port" 'available'
  }

  Write-DoctorRow 'web UI' "http://${BindHost}:$Port"
}

function Open-Browser([string] $Url) {
  if ($NoBrowser) { return }
  Write-Ok "opening $Url"
  Start-Process $Url
}

function Resolve-ServerPort([string] $PythonExe, [string] $BindHost, [int] $PreferredPort) {
  $script = @"
import socket
host = '$BindHost'
port = $PreferredPort
for candidate in range(port, port + 20):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, candidate))
        except OSError:
            continue
        print(candidate)
        break
else:
    raise SystemExit(1)
"@
  $resolved = & $PythonExe -c $script 2>$null
  if ($LASTEXITCODE -ne 0 -or -not $resolved) {
    throw "No free port found near $PreferredPort"
  }
  return [int]$resolved.Trim()
}

Write-Host ''
Write-Host 'Tick Downloader' -ForegroundColor White
Write-Host "Repo: $RepoRoot"

Push-Location $RepoRoot
try {
  Import-LocalLauncherConfig
  $pythonExe = Ensure-VenvPython

  if (-not (Test-FastApiInstalled $pythonExe)) {
    throw 'Python dependencies missing. Run run.bat without -check-only to install.'
  }

  if ($CheckOnly) {
    Invoke-LauncherDoctor -PythonExe $pythonExe
    Write-Host ''
    Write-Host 'Launcher check complete.' -ForegroundColor Green
    exit 0
  }

  $port = Resolve-ServerPort -PythonExe $pythonExe -BindHost $BindHost -PreferredPort $Port
  if ($port -ne $Port) {
    Write-WarnLine "Port $Port in use - using $port"
  }

  $url = "http://${BindHost}:$port"
  $listener = Get-PortListener -LocalPort $port
  if ($listener) {
    Write-WarnLine "port $port is already listening (PID $($listener.OwningProcess))"
    Open-Browser -Url $url
    exit 0
  }

  Open-Browser -Url $url

  Write-Host ''
  Write-Host 'Starting web server. Close this window or press Ctrl+C to stop.' -ForegroundColor White
  Write-Host "Browser: $url"
  Write-Host ''

  & $pythonExe $MainPy web --host $BindHost --port $port
  exit $LASTEXITCODE
} finally {
  Pop-Location
}
