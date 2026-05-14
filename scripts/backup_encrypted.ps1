param(
    [string]$OutputDir = ".\backups",
    [string]$AgeRecipient = $env:BACKUP_AGE_RECIPIENT
)

if (-not $AgeRecipient) {
    throw "BACKUP_AGE_RECIPIENT is required."
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$work = Join-Path $OutputDir "neuthek-$timestamp"
$archive = Join-Path $OutputDir "neuthek-$timestamp.zip"
$encrypted = "$archive.age"

New-Item -ItemType Directory -Force -Path $work, $OutputDir | Out-Null

$pgUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { "istore" }
$pgDb = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { "istore" }

docker compose exec -T postgres pg_dump -U $pgUser $pgDb `
    | Out-File -Encoding utf8 (Join-Path $work "postgres.sql")

if (Test-Path ".\data\minio") {
    Compress-Archive -Path ".\data\minio\*" -DestinationPath (Join-Path $work "minio.zip") -Force
}

Compress-Archive -Path (Join-Path $work "*") -DestinationPath $archive -Force
age -r $AgeRecipient -o $encrypted $archive
Remove-Item -LiteralPath $archive -Force
Remove-Item -LiteralPath $work -Recurse -Force

Write-Output "Encrypted backup written to $encrypted"
