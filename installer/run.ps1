#Requires -Version 5.1
<#
.SYNOPSIS
  Entry orchestrator: first-time install, then launch the web UI.
#>
[CmdletBinding()]
param(
  [Alias('help')]
  [switch] $ShowHelp,
  [Alias('check-only')]
  [switch] $CheckOnly,
  [Alias('no-browser')]
  [switch] $NoBrowser,
  [Alias('skip-install')]
  [switch] $SkipInstall,
  [int] $Port = 8080,
  [string] $BindHost = '127.0.0.1'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$VenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$InstalledFile = Join-Path $RepoRoot '.dukascopy\installed.json'

function Show-RunHelp {
  Write-Host ''
  Write-Host 'Tick Downloader - run.bat' -ForegroundColor White
  Write-Host ''
  Write-Host 'Usage:  run.bat [flags]' -ForegroundColor White
  Write-Host ''
  Write-Host '  -help          Show this help and exit'
  Write-Host '  -check-only    Validate setup without starting the server'
  Write-Host '  -no-browser    Do not open the browser automatically'
  Write-Host '  -skip-install  Do not auto-run first-time install'
  Write-Host '  -port <n>      Web port (default 8080)'
  Write-Host '  -Host <addr>   Bind address (default 127.0.0.1)'
  Write-Host ''
}

function Test-ApplicationInstalled {
  if (-not (Test-Path -LiteralPath $VenvPython)) { return $false }
  if (-not (Test-Path -LiteralPath $InstalledFile)) { return $false }
  & $VenvPython -c 'import fastapi, uvicorn' 2>$null
  return $LASTEXITCODE -eq 0
}

if ($ShowHelp) {
  Show-RunHelp
  exit 0
}

Write-Host ''
Write-Host 'Tick Downloader' -ForegroundColor White
Write-Host "Repo: $RepoRoot"

if (-not $SkipInstall -and -not (Test-ApplicationInstalled)) {
  if ($CheckOnly) {
    Write-Host ''
    Write-Host 'First-time setup not complete (run without -check-only to install).' -ForegroundColor Yellow
  } else {
    Write-Host ''
    Write-Host 'First-time setup required.' -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot 'install.ps1')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  }
}

$launchParams = @{
  CheckOnly = $CheckOnly
  NoBrowser = $NoBrowser
  Port = $Port
  BindHost = $BindHost
}

& (Join-Path $PSScriptRoot 'launch.ps1') @launchParams
exit $LASTEXITCODE
