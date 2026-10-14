<#
.SYNOPSIS
  Starts Tapo Cloud Personal (MediaMTX + backend + camera service) and opens the browser.
  Close the window or press Ctrl+C to stop everything cleanly.
#>
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "Primero ejecuta scripts\install-windows.ps1" -ForegroundColor Yellow
    exit 1
}
# Use the Windows Credential Manager for the encryption key (DPAPI-protected).
$env:TAPOCLOUD_USE_KEYRING = "1"
& $venvPy (Join-Path $Root "scripts\run_all.py") --open
