# Materialize the persistent-storage directory tree under $env:ISTORE_DATA_ROOT.
# Idempotent -- re-running is safe.

$ErrorActionPreference = "Stop"

$root = if ($env:ISTORE_DATA_ROOT) { $env:ISTORE_DATA_ROOT } else { "./data" }
Write-Host "ISTORE_DATA_ROOT=$root"

$dirs = @(
    "postgres",
    "redis",
    "minio",
    "models",
    "backups"
)

foreach ($d in $dirs) {
    $path = Join-Path $root $d
    if (-not (Test-Path $path)) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
    Write-Host "  $path"
}

Write-Host "ok"
