[CmdletBinding()]
param(
    [string]$ComfyUIDir = '',
    [string]$BackendUrl = 'http://127.0.0.1:8189',
    [string]$Python = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonArgs = @()
if (-not $Python) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $Python = 'py'; $pythonArgs = @('-3')
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $Python = 'python'
    } else { throw 'Install Python 3.10+ from python.org, then rerun setup.ps1.' }
}
& $Python @pythonArgs -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ required"'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ with venv is required. You can pass -Python C:\path\python.exe.' }
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & $Python @pythonArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create .venv. Use a standard Python install with venv support.' }
}
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $venvPython -m pip install --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your network and rerun setup.ps1.' }
if (Test-Path -LiteralPath 'config.json') {
    Write-Host 'Existing config.json preserved. Edit it to change your ComfyUI directory or port.'
} else {
    if (-not $ComfyUIDir) { $ComfyUIDir = Read-Host 'ComfyUI folder (the folder containing main.py)' }
    $resolvedComfy = (Resolve-Path -LiteralPath $ComfyUIDir).Path
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedComfy 'main.py'))) { throw 'Choose the ComfyUI directory containing main.py.' }
    $config = [ordered]@{comfyui_dir=$resolvedComfy; backend_url=$BackendUrl; port=8190; data_dir='./data'; max_upload_mb=20}
    [System.IO.File]::WriteAllText((Join-Path $PSScriptRoot 'config.json'), ($config | ConvertTo-Json), [System.Text.UTF8Encoding]::new($false))
}
Write-Host 'Setup complete. Start ComfyUI first, then run start.cmd.'
Write-Host 'Model download plan: .venv\Scripts\python.exe download_models.py'
Write-Host 'Read docs/INSTALL.md and docs/MODEL-LICENSE.md before downloading models.'
