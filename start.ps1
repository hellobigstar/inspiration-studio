[CmdletBinding()]
param([string]$Config = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) { throw 'Run setup.ps1 first. See README.md for manual setup.' }
$studioArgs = @('-X','utf8','studio/server.py','--open-browser')
if ($Config) { $studioArgs += @('--config',$Config) }
& $venvPython @studioArgs
exit $LASTEXITCODE
