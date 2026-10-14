<#
.SYNOPSIS
  Installs Tapo Cloud Personal on Windows 10/11 (per-user, no admin needed).

.DESCRIPTION
  1. Checks Python 3.11+ (offers the winget command if missing).
  2. Creates .venv and installs the backend.
  3. Downloads FFmpeg (gyan.dev "essentials" build) and MediaMTX into .\bin.
  4. Builds the web UI (needs Node.js LTS, one time only).
  5. Optionally adds Windows Firewall rules (Private networks only).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
  powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -ConfigureFirewall   # run as admin
#>
param(
    [switch]$ConfigureFirewall,
    [string]$MediaMtxVersion = "1.15.3"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # makes Invoke-WebRequest much faster
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Bin = Join-Path $Root "bin"
New-Item -ItemType Directory -Force -Path $Bin | Out-Null

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# --- 1. Python ----------------------------------------------------------------
Step "Comprobando Python 3.11+"
$candidates = @(
    @{ Exe = "py"; Args = @("-3.13") },
    @{ Exe = "py"; Args = @("-3.12") },
    @{ Exe = "py"; Args = @("-3.11") },
    @{ Exe = "python"; Args = @() }
)
$py = $null
foreach ($c in $candidates) {
    if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
    $v = & $c.Exe @($c.Args) -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and $v -and [version]$v -ge [version]"3.11") { $py = $c; break }
}
if (-not $py) {
    Write-Host "Python 3.11+ no encontrado. Instálalo con:" -ForegroundColor Yellow
    Write-Host "  winget install Python.Python.3.12"
    exit 1
}
Write-Host "Usando: $($py.Exe) $($py.Args -join ' ')"

# --- 2. venv + backend ----------------------------------------------------------
Step "Instalando el backend en .venv"
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    & $py.Exe @($py.Args) -m venv (Join-Path $Root ".venv")
}
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install --quiet -e "$(Join-Path $Root 'backend')[keyring]"

# --- 3. FFmpeg + MediaMTX -------------------------------------------------------------
if (-not (Test-Path (Join-Path $Bin "ffmpeg.exe"))) {
    Step "Descargando FFmpeg"
    $zip = Join-Path $env:TEMP "ffmpeg-essentials.zip"
    Invoke-WebRequest "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $zip
    $tmp = Join-Path $env:TEMP "ffmpeg-extract"
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive $zip -DestinationPath $tmp
    Get-ChildItem $tmp -Recurse -Include ffmpeg.exe, ffprobe.exe | Copy-Item -Destination $Bin
    Remove-Item $zip, $tmp -Recurse -Force
}
if (-not (Test-Path (Join-Path $Bin "mediamtx.exe"))) {
    Step "Descargando MediaMTX v$MediaMtxVersion"
    $zip = Join-Path $env:TEMP "mediamtx.zip"
    Invoke-WebRequest "https://github.com/bluenviron/mediamtx/releases/download/v$MediaMtxVersion/mediamtx_v${MediaMtxVersion}_windows_amd64.zip" -OutFile $zip
    $tmp = Join-Path $env:TEMP "mediamtx-extract"
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive $zip -DestinationPath $tmp
    Copy-Item (Join-Path $tmp "mediamtx.exe") $Bin
    Remove-Item $zip, $tmp -Recurse -Force
}

# --- 4. Web UI -------------------------------------------------------------------
$out = Join-Path $Root "frontend\out\index.html"
if (-not (Test-Path $out)) {
    Step "Compilando la interfaz web"
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Push-Location (Join-Path $Root "frontend")
        npm ci --no-audit --no-fund
        npm run build
        Pop-Location
    } else {
        Write-Host "Node.js no encontrado (solo hace falta una vez para compilar la interfaz):" -ForegroundColor Yellow
        Write-Host "  winget install OpenJS.NodeJS.LTS   y vuelve a ejecutar este script"
    }
}

# --- 5. Firewall (optional, admin) ---------------------------------------------------
if ($ConfigureFirewall) {
    Step "Reglas de firewall (solo redes privadas)"
    New-NetFirewallRule -DisplayName "Tapo Cloud UI (8000)" -Direction Inbound -Protocol TCP -LocalPort 8000 -Profile Private -Action Allow | Out-Null
    New-NetFirewallRule -DisplayName "Tapo Cloud WebRTC (8889 TCP)" -Direction Inbound -Protocol TCP -LocalPort 8889, 8888 -Profile Private -Action Allow | Out-Null
    New-NetFirewallRule -DisplayName "Tapo Cloud WebRTC (8189 UDP)" -Direction Inbound -Protocol UDP -LocalPort 8189 -Profile Private -Action Allow | Out-Null
}

Step "Listo"
Write-Host "Arranca la aplicación con:" -ForegroundColor Green
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\start-windows.ps1"
