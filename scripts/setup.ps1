# neuthek -- full server setup (Windows / PowerShell).
#
# Idempotent. Re-running is safe.
#
# Usage:
#   ./scripts/setup.ps1                       # base setup
#   ./scripts/setup.ps1 -Ml                   # CPU torch + open_clip (~3 GB)
#   ./scripts/setup.ps1 -Ml -Gpu cu128        # GPU torch (Blackwell / RTX 50-series)
#   ./scripts/setup.ps1 -Ml -Gpu cu126        # GPU torch (modern Ada / 40-series)
#   ./scripts/setup.ps1 -Ml -Gpu cu124        # GPU torch (older Ada)
#   ./scripts/setup.ps1 -Start                # also starts uvicorn after setup

param(
    [switch]$Ml,
    [switch]$Start,
    [ValidateSet("", "cu128", "cu126", "cu124", "cu121", "cu118")]
    [string]$Gpu = ""
)

$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "  ok $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "  fail $msg" -ForegroundColor Red; exit 1 }

Step "Prerequisites"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail "docker not on PATH" }
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail "docker daemon unreachable -- start Docker Desktop" }
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { Fail "python not on PATH" }
Ok "docker running, python at $($python.Source)"

Step "Storage layout"
& powershell -ExecutionPolicy Bypass -File scripts\init_storage.ps1
Ok "data tree ready"

Step "Environment file"
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Ok "created .env from .env.example (edit JWT_SECRET before prod)"
} else {
    Ok ".env already present"
}

Step "Docker compose up"
docker compose up -d
if ($LASTEXITCODE -ne 0) { Fail "docker compose up failed" }
Ok "containers requested"

Step "Waiting for healthchecks"
foreach ($svc in @("istore-postgres", "istore-minio", "istore-redis")) {
    $status = "missing"
    for ($i = 0; $i -lt 60; $i++) {
        $status = (docker inspect -f '{{.State.Health.Status}}' $svc 2>$null)
        if ($status -eq "healthy") { break }
        Start-Sleep -Seconds 2
    }
    if ($status -ne "healthy") { Fail "$svc not healthy after 120s (status=$status)" }
    Ok "$svc healthy"
}

Step "Python venv"
if (-not (Test-Path .venv)) {
    python -m venv .venv
    Ok "created .venv"
} else {
    Ok ".venv already present"
}

$vpy = ".venv/Scripts/python.exe"

Step "Installing deps"
& $vpy -m pip install --quiet --upgrade pip
if ($Gpu) {
    $gpuIndex = "https://download.pytorch.org/whl/$Gpu"
    Ok "pre-installing GPU torch from $gpuIndex"
    & $vpy -m pip install --quiet --upgrade --index-url $gpuIndex torch torchvision
}
if ($Ml) {
    & $vpy -m pip install --quiet -e ".[dev,ml]"
    Ok "installed [dev,ml]"
} else {
    & $vpy -m pip install --quiet -e ".[dev]"
    Ok "installed [dev]  (re-run with -Ml for vision pipeline)"
}

Step "Alembic migrations"
& $vpy -m alembic upgrade head
Ok "schema is at head"

Step "Done"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  Start the API:    $vpy -m uvicorn backend.app:app --host 0.0.0.0 --port 8000"
Write-Host "  Open MinIO admin: http://localhost:9001  (user/pass from .env)"
Write-Host "  Tear down:        docker compose down"
Write-Host "  Wipe data:        docker compose down -v ; Remove-Item -Recurse -Force `$env:ISTORE_DATA_ROOT"

if ($Start) {
    Step "Starting uvicorn (foreground; Ctrl+C to stop)"
    & $vpy -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
}
