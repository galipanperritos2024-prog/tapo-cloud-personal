<#
.SYNOPSIS
  Installs Tapo Cloud Personal on Windows 10/11 (per-user, no admin needed).

.DESCRIPTION
  1. Checks Python 3.11+ (PATH, py launcher and default install folders).
  2. Creates .venv and installs the backend.
  3. Downloads FFmpeg (gyan.dev "essentials" build) and MediaMTX into .\bin.
  4. Builds the web UI with Node.js if present, otherwise downloads it prebuilt.
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

# Download with a visible progress bar. curl.exe ships with Windows 10/11 and is
# far faster than Invoke-WebRequest; fall back to it only if curl is missing.
function Download($url, $dest) {
    Write-Host "    $url"
    if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
        & curl.exe -L --fail --retry 3 --progress-bar -o $dest $url
        if ($LASTEXITCODE -ne 0) { throw "Descarga fallida: $url" }
    } else {
        Invoke-WebRequest $url -OutFile $dest
    }
}

# --- 1. Python ----------------------------------------------------------------
Step "Comprobando Python 3.11+"
$candidates = @(
    @{ Exe = "py"; Args = @("-3.13") },
    @{ Exe = "py"; Args = @("-3.12") },
    @{ Exe = "py"; Args = @("-3.11") },
    @{ Exe = "python"; Args = @() }
)
# Also look where the python.org / winget installers put Python, in case PATH
# was not refreshed (or "Add to PATH" was unchecked).
foreach ($base in @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles", "${env:ProgramFiles(x86)}")) {
    if (-not $base -or -not (Test-Path $base)) { continue }
    Get-ChildItem $base -Directory -Filter "Python3*" -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        ForEach-Object {
            $exe = Join-Path $_.FullName "python.exe"
            if (Test-Path $exe) { $candidates += @{ Exe = $exe; Args = @() } }
        }
}
$py = $null
# Windows PowerShell 5.1 turns a native command's stderr into a terminating
# error under "Stop"; probing missing versions must not abort the script.
$ErrorActionPreference = "Continue"
foreach ($c in $candidates) {
    if (-not (Test-Path $c.Exe) -and -not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
    try {
        $v = & $c.Exe @($c.Args) -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    } catch { continue }
    # "python" may be the Microsoft Store alias, which prints nothing useful.
    if ($LASTEXITCODE -eq 0 -and "$v" -match '^\d+\.\d+$' -and [version]"$v" -ge [version]"3.11") { $py = $c; break }
}
$ErrorActionPreference = "Stop"
if (-not $py) {
    Write-Host "Python 3.11+ no encontrado. Instálalo con:" -ForegroundColor Yellow
    Write-Host "  winget install Python.Python.3.12"
    Write-Host "Después cierra y vuelve a abrir PowerShell y ejecuta de nuevo este script."
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
    Step "Descargando FFmpeg (~100 MB, puede tardar unos minutos)"
    $zip = Join-Path $env:TEMP "ffmpeg-essentials.zip"
    Download "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" $zip
    $tmp = Join-Path $env:TEMP "ffmpeg-extract"
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive $zip -DestinationPath $tmp
    Get-ChildItem $tmp -Recurse -Include ffmpeg.exe, ffprobe.exe | Copy-Item -Destination $Bin
    Remove-Item $zip, $tmp -Recurse -Force
}
if (-not (Test-Path (Join-Path $Bin "mediamtx.exe"))) {
    Step "Descargando MediaMTX v$MediaMtxVersion"
    $zip = Join-Path $env:TEMP "mediamtx.zip"
    Download "https://github.com/bluenviron/mediamtx/releases/download/v$MediaMtxVersion/mediamtx_v${MediaMtxVersion}_windows_amd64.zip" $zip
    $tmp = Join-Path $env:TEMP "mediamtx-extract"
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive $zip -DestinationPath $tmp
    Copy-Item (Join-Path $tmp "mediamtx.exe") $Bin
    Remove-Item $zip, $tmp -Recurse -Force
}

# --- 4. Web UI -------------------------------------------------------------------
$outDir = Join-Path $Root "frontend\out"
if (-not (Test-Path (Join-Path $outDir "index.html"))) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Step "Compilando la interfaz web"
        Push-Location (Join-Path $Root "frontend")
        npm ci --no-audit --no-fund
        npm run build
        Pop-Location
    } else {
        # No Node.js: download the prebuilt UI published on the "ui-build" branch.
        Step "Descargando la interfaz web precompilada"
        $zip = Join-Path $env:TEMP "tapocloud-ui.zip"
        Download "https://github.com/galipanperritos2024-prog/tapo-cloud-personal/archive/refs/heads/ui-build.zip" $zip
        $tmp = Join-Path $env:TEMP "tapocloud-ui"
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
        Expand-Archive $zip -DestinationPath $tmp
        New-Item -ItemType Directory -Force -Path $outDir | Out-Null
        Copy-Item (Join-Path $tmp "tapo-cloud-personal-ui-build\*") $outDir -Recurse -Force
        Remove-Item $zip, $tmp -Recurse -Force
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
