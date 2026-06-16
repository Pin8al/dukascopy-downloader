#Requires -Version 5.1
<#
.SYNOPSIS
  First-time setup for Tick Downloader (Windows).

.DESCRIPTION
  - Ensures Python 3.10+ (installs via winget when missing)
  - Creates .venv and installs pip dependencies
  - Creates data/ directory
  - Detects MetaTrader 5 install + terminal data folder
  - Writes MT5 paths into data/web_settings.json
  - Warns if DukascopyTickImport.ex5 is not compiled yet

.PARAMETER SkipPipInstall
  Skip pip install (venv must already have dependencies).

.PARAMETER SkipMt5Config
  Do not detect MT5 or update web_settings.json.

.PARAMETER Mt5TerminalExe
  Override path to terminal64.exe.
#>
[CmdletBinding()]
param(
  [switch] $SkipPipInstall,
  [switch] $SkipMt5Config,
  [string] $Mt5TerminalExe
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$VenvDir = Join-Path $RepoRoot '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$Requirements = Join-Path $RepoRoot 'requirements.txt'
$LocalConfigFile = Join-Path $RepoRoot '.dukascopy.local.ps1'
$InstalledFile = Join-Path $RepoRoot '.dukascopy\installed.json'
$MinPythonMajor = 3
$MinPythonMinor = 10
$EaScriptRel = 'Scripts\dukascopy\DukascopyTickImport.ex5'

function Write-Step([string] $Message) {
  Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Write-Ok([string] $Message) {
  Write-Host "    OK  $Message" -ForegroundColor Green
}

function Write-WarnLine([string] $Message) {
  Write-Host "    !!  $Message" -ForegroundColor Yellow
}

function Refresh-SessionPath {
  $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
  $user = [Environment]::GetEnvironmentVariable('Path', 'User')
  $env:Path = @($machine, $user) -join ';'
}

function Get-PythonVersionTuple([string] $PythonExe) {
  $raw = & $PythonExe --version 2>&1
  if (-not $raw) { return $null }
  if ($raw -match 'Python (\d+)\.(\d+)') {
    return [pscustomobject]@{ Major = [int]$Matches[1]; Minor = [int]$Matches[2] }
  }
  return $null
}

function Test-PythonVersion([string] $PythonExe) {
  $ver = Get-PythonVersionTuple $PythonExe
  if (-not $ver) { return $false }
  if ($ver.Major -gt $MinPythonMajor) { return $true }
  if ($ver.Major -eq $MinPythonMajor -and $ver.Minor -ge $MinPythonMinor) { return $true }
  return $false
}

function Get-SystemPython {
  if (Test-Path -LiteralPath $VenvPython) {
    if (Test-PythonVersion $VenvPython) { return $VenvPython }
  }

  $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
  if ($pyLauncher) {
    $candidate = & py -3 -c 'import sys; print(sys.executable)' 2>$null
    if ($candidate -and (Test-Path -LiteralPath $candidate) -and (Test-PythonVersion $candidate)) {
      return $candidate.Trim()
    }
  }

  $standard = @(
    "${env:LOCALAPPDATA}\Programs\Python\Python312\python.exe",
    "${env:LOCALAPPDATA}\Programs\Python\Python311\python.exe",
    "${env:ProgramFiles}\Python312\python.exe",
    "${env:ProgramFiles}\Python311\python.exe"
  )
  foreach ($path in $standard) {
    if ((Test-Path -LiteralPath $path) -and (Test-PythonVersion $path)) { return $path }
  }

  Refresh-SessionPath
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.Source -notmatch '(?i)\\cursor\\|\\vscode\\' -and (Test-PythonVersion $cmd.Source)) {
    return $cmd.Source
  }

  return $null
}

function Install-Python {
  Write-Step "Python $MinPythonMajor.$MinPythonMinor+ not found - installing via winget"

  $winget = Get-Command winget -ErrorAction SilentlyContinue
  if (-not $winget) {
    throw @(
      'winget is not available.',
      "Install Python $MinPythonMajor.$MinPythonMinor+ manually: https://www.python.org/downloads/",
      'Then re-run: .\run.bat'
    ) -join "`n"
  }

  & winget install --id Python.Python.3.12 -e `
    --accept-package-agreements --accept-source-agreements

  if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) {
    throw "winget install failed (exit $LASTEXITCODE)"
  }

  Refresh-SessionPath
  Start-Sleep -Seconds 3
}

function Ensure-Python {
  Write-Step "Checking Python (>= $MinPythonMajor.$MinPythonMinor)"

  $pythonExe = Get-SystemPython
  if (-not $pythonExe) {
    Install-Python
    $pythonExe = Get-SystemPython
  }

  if (-not $pythonExe) {
    throw 'Python is still not on PATH after install. Open a new terminal and run .\run.bat again.'
  }

  $ver = & $pythonExe --version 2>&1
  Write-Ok "$ver at $pythonExe"
  return $pythonExe
}

function Ensure-Venv([string] $BasePython) {
  Write-Step 'Creating virtual environment (.venv)'

  if (-not (Test-Path -LiteralPath $VenvDir)) {
    & $BasePython -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw 'python -m venv failed' }
    Write-Ok 'created .venv'
  } else {
    Write-Ok '.venv already exists'
  }

  if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "venv python not found: $VenvPython"
  }

  return $VenvPython
}

function Invoke-PipInstall([string] $PythonExe) {
  Write-Step 'Installing Python dependencies'

  $pip = Join-Path (Split-Path $PythonExe -Parent) 'pip.exe'
  & $PythonExe -m pip install --upgrade pip wheel 2>&1 | ForEach-Object { Write-Host $_ }
  & $pip install -r $Requirements 2>&1 | ForEach-Object { Write-Host $_ }
  if ($LASTEXITCODE -ne 0) { throw "pip install failed (exit $LASTEXITCODE)" }
  Write-Ok 'dependencies installed'
}

function Test-ProjectLayout {
  Write-Step 'Checking project layout'

  foreach ($required in @('main.py', 'requirements.txt', 'web\app.py', 'mt5\DukascopyTickImport.mq5')) {
    $path = Join-Path $RepoRoot $required
    if (-not (Test-Path -LiteralPath $path)) {
      throw "Missing required file: $path"
    }
  }
  Write-Ok 'core files present'

  foreach ($dirName in @('data')) {
    $dir = Join-Path $RepoRoot $dirName
    if (-not (Test-Path -LiteralPath $dir)) {
      New-Item -ItemType Directory -Path $dir | Out-Null
      Write-Ok "created $dirName/"
    }
  }
}

function Find-Mt5TerminalExe {
  if ($Mt5TerminalExe) {
    if (-not (Test-Path -LiteralPath $Mt5TerminalExe)) {
      throw "MT5 terminal not found: $Mt5TerminalExe"
    }
    return (Resolve-Path -LiteralPath $Mt5TerminalExe).Path
  }

  if ($env:DUKE_MT5_TERMINAL_EXE -and (Test-Path -LiteralPath $env:DUKE_MT5_TERMINAL_EXE)) {
    return (Resolve-Path -LiteralPath $env:DUKE_MT5_TERMINAL_EXE).Path
  }

  $candidates = [System.Collections.Generic.List[string]]::new()

  foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
    if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
    Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -like 'MetaTrader*' } |
      ForEach-Object {
        $exe = Join-Path $_.FullName 'terminal64.exe'
        if (Test-Path -LiteralPath $exe) { $candidates.Add($exe) }
      }
  }

  $default = 'C:\Program Files\MetaTrader 5\terminal64.exe'
  if ((Test-Path -LiteralPath $default) -and ($candidates -notcontains $default)) {
    $candidates.Add($default)
  }

  $unique = @($candidates | Select-Object -Unique)
  if ($unique.Length -eq 0) {
    Write-WarnLine 'MetaTrader 5 (terminal64.exe) not found - set path in Settings after launch.'
    return $null
  }

  if ($unique.Length -eq 1) { return $unique[0] }

  Write-Host ''
  Write-Host 'Multiple MT5 installations found:' -ForegroundColor Yellow
  for ($i = 0; $i -lt $unique.Length; $i++) {
    Write-Host "  [$i] $($unique[$i])"
  }
  $pick = Read-Host 'Select index (Enter = 0)'
  if ([string]::IsNullOrWhiteSpace($pick)) { $pick = '0' }
  if ($pick -notmatch '^\d+$' -or [int]$pick -ge $unique.Length) {
    throw 'Invalid selection'
  }
  return $unique[[int]$pick]
}

function Find-Mt5DataPath([string] $TerminalExe) {
  if ($env:DUKE_MT5_DATA_PATH -and (Test-Path -LiteralPath $env:DUKE_MT5_DATA_PATH)) {
    return (Resolve-Path -LiteralPath $env:DUKE_MT5_DATA_PATH).Path
  }

  if (-not $TerminalExe) { return '' }

  $originFile = Join-Path (Split-Path $TerminalExe -Parent) 'origin.txt'
  if (Test-Path -LiteralPath $originFile) {
    foreach ($enc in @('Unicode', 'UTF8')) {
      try {
        $instanceId = (Get-Content -LiteralPath $originFile -Encoding $enc -Raw).Trim()
        if ($instanceId) {
          $candidate = Join-Path $env:APPDATA "MetaQuotes\Terminal\$instanceId"
          if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
          }
        }
      } catch {}
    }
  }

  $terminalRoot = Join-Path $env:APPDATA 'MetaQuotes\Terminal'
  if (-not (Test-Path -LiteralPath $terminalRoot)) { return '' }

  $entries = @(Get-ChildItem -LiteralPath $terminalRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notin @('Common', 'Community', 'Help') } |
    ForEach-Object {
      $scripts = Join-Path $_.FullName 'MQL5\Scripts'
      if (Test-Path -LiteralPath $scripts) {
        [pscustomobject]@{
          Root = $_.FullName
          EaHit = Test-Path -LiteralPath (Join-Path $scripts 'dukascopy\DukascopyTickImport.ex5')
          LastWrite = $_.LastWriteTimeUtc
        }
      }
    })

  if ($entries.Length -eq 0) { return '' }
  $ranked = @($entries | Sort-Object @{ Expression = 'EaHit'; Descending = $true }, LastWrite -Descending)
  return $ranked[0].Root
}

function Write-LocalLauncherConfig([string] $TerminalExe, [string] $DataPath) {
  if (-not $TerminalExe) { return }
  $escapedExe = $TerminalExe.Replace("'", "''")
  $escapedData = $DataPath.Replace("'", "''")
  $content = @"
# Auto-generated by installer. Edit freely; not committed to git.
`$env:DUKE_MT5_TERMINAL_EXE = '$escapedExe'
`$env:DUKE_MT5_DATA_PATH = '$escapedData'
"@
  $utf8NoBom = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($LocalConfigFile, $content, $utf8NoBom)
}

function Update-WebSettingsMt5([string] $PythonExe, [string] $TerminalExe, [string] $DataPath) {
  if (-not $TerminalExe) {
    Write-WarnLine 'Skipped web_settings MT5 block (terminal not found)'
    return
  }

  Write-Step 'Writing MT5 paths to data/web_settings.json'

  $script = @"
import json
from pathlib import Path

path = Path(r'$RepoRoot') / 'data' / 'web_settings.json'
path.parent.mkdir(parents=True, exist_ok=True)

data = {}
if path.is_file():
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        data = {}

data.setdefault('ui', {'theme': 'light', 'default_workers': 15})
data.setdefault('automations', [])
mt5 = data.setdefault('mt5', {})
mt5['terminal_exe'] = r'''$($TerminalExe.Replace("'", "\\'"))'''
data_path = r'''$($DataPath.Replace("'", "\\'"))'''
if data_path:
    mt5['data_path'] = data_path
mt5.setdefault('custom_suffix', '.DUK')

path.write_text(json.dumps(data, indent=2), encoding='utf-8')
print('wrote', path)
"@

  & $PythonExe -c $script
  if ($LASTEXITCODE -ne 0) { throw 'Failed to update web_settings.json' }
  Write-Ok 'data/web_settings.json updated'
}

function Test-DukascopyImportScript([string] $DataPath, [string] $PythonExe) {
  Write-Step 'Checking DukascopyTickImport script'

  $bundledMq5 = Join-Path $RepoRoot 'mt5\DukascopyTickImport.mq5'
  $bundledEx5 = Join-Path $RepoRoot 'mt5\DukascopyTickImport.ex5'

  if (Test-Path -LiteralPath $bundledEx5) {
    Write-Ok 'bundled mt5/DukascopyTickImport.ex5 present'
  } else {
    Write-WarnLine 'mt5/DukascopyTickImport.ex5 not found - compile DukascopyTickImport.mq5 in MetaEditor'
    Write-WarnLine 'Import will auto-compile on first run if metaeditor64.exe is available.'
  }

  if ($DataPath) {
    $scriptPath = Join-Path $DataPath $EaScriptRel
    if (Test-Path -LiteralPath $scriptPath) {
      Write-Ok "Import script installed in MT5: $EaScriptRel"
    } else {
      Write-WarnLine "Import script not yet in MT5 Scripts (installed on first import): $scriptPath"
    }
  }
}

function Write-InstalledManifest([string] $PythonExe, [bool] $Mt5Configured) {
  $stateDir = Join-Path $RepoRoot '.dukascopy'
  if (-not (Test-Path -LiteralPath $stateDir)) {
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
  }
  $ver = & $PythonExe --version 2>&1
  $manifest = [ordered]@{
    version = 1
    installedAt = (Get-Date).ToUniversalTime().ToString('o')
    pythonVersion = "$ver"
    venvPython = $VenvPython
    mt5Configured = $Mt5Configured
  }
  ($manifest | ConvertTo-Json) | Set-Content -LiteralPath $InstalledFile -Encoding UTF8
}

# --- Main -------------------------------------------------------------------

Write-Host ''
Write-Host 'Tick Downloader - Windows installer' -ForegroundColor White
Write-Host "Repo: $RepoRoot"

$localConfig = $LocalConfigFile
if (Test-Path -LiteralPath $localConfig) {
  Write-Step 'Loading .dukascopy.local.ps1'
  . $localConfig
  Write-Ok 'local overrides loaded'
}

Test-ProjectLayout
$basePython = Ensure-Python
$pythonExe = Ensure-Venv -BasePython $basePython

if (-not $SkipPipInstall) {
  Invoke-PipInstall -PythonExe $pythonExe
} else {
  Write-WarnLine 'Skipped pip install (-SkipPipInstall)'
}

$terminalExe = $null
$dataPath = ''
$mt5Configured = $false

if (-not $SkipMt5Config) {
  Write-Step 'Detecting MetaTrader 5 paths'
  $terminalExe = Find-Mt5TerminalExe
  if ($terminalExe) {
    Write-Ok "terminal64.exe -> $terminalExe"
    $dataPath = Find-Mt5DataPath -TerminalExe $terminalExe
    if ($dataPath) {
      Write-Ok "terminal data -> $dataPath"
    } else {
      Write-WarnLine 'MT5 data folder not found - launch MT5 once, then re-run install'
    }
    Write-LocalLauncherConfig -TerminalExe $terminalExe -DataPath $dataPath
    if (Test-Path -LiteralPath $LocalConfigFile) {
      Write-Ok "wrote $LocalConfigFile"
    }
    Update-WebSettingsMt5 -PythonExe $pythonExe -TerminalExe $terminalExe -DataPath $dataPath
    $mt5Configured = $true
    Test-DukascopyImportScript -DataPath $dataPath -PythonExe $pythonExe
  }
} else {
  Write-WarnLine 'Skipped MT5 config (-SkipMt5Config)'
}

Write-InstalledManifest -PythonExe $pythonExe -Mt5Configured $mt5Configured

Write-Host ''
Write-Host 'Setup complete.' -ForegroundColor Green
Write-Host 'Start the app:     run.bat' -ForegroundColor White
Write-Host 'Open in browser:   http://127.0.0.1:8080' -ForegroundColor White
Write-Host 'Compile script:  MetaEditor -> mt5/DukascopyTickImport.mq5 -> Compile' -ForegroundColor White
Write-Host ''
