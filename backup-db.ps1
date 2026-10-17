#Requires -Version 5.1
# backup-db.ps1 - nightly backup of the local Gajab database + storage.
#
# Supabase used to provide managed backups; self-hosting means we own them. The
# database is tiny (~63 MB of tables, ~10 MB compressed) so a daily dump is cheap;
# the storage folder is ~400 MB and changes slowly, so it is mirrored weekly.
#
# Registered as scheduled task "Gajab-DB-Backup" (daily 03:30).
. "$PSScriptRoot\windows-common.ps1"

$PGBIN = Join-Path $PgRoot "pgsql\bin"
$backupDir = Join-Path $DbRoot "backups"
$stamp = Get-Date -Format "yyyyMMdd-HHmm"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

if (-not (Test-Path (Join-Path $DbRoot ".db-secrets.json"))) {
    Write-Warning "[backup] no .db-secrets.json - cannot authenticate"
    return
}
$sec = Get-Content (Join-Path $DbRoot ".db-secrets.json") -Raw | ConvertFrom-Json
$env:PGPASSWORD = $sec.superuser

# --- database (custom format, restorable with pg_restore) ---
$dump = Join-Path $backupDir "gajab-$stamp.dump"
Write-Host "[backup] pg_dump -> $dump"
& "$PGBIN\pg_dump.exe" -U postgres -h 127.0.0.1 -p $PgPort -d gajab -Fc -f $dump
if ($LASTEXITCODE -ne 0) {
    Write-Warning "[backup] pg_dump failed (exit $LASTEXITCODE)"
    return
}
Write-Host ("[backup] database: " + [math]::Round((Get-Item $dump).Length / 1MB, 1) + " MB")

# --- storage (weekly: mirror to a dated folder) ---
if ((Get-Date).DayOfWeek -eq [System.DayOfWeek]::Sunday) {
    $dest = Join-Path $backupDir "storage-$stamp"
    Write-Host "[backup] mirroring storage -> $dest"
    robocopy (Join-Path $DbRoot "storage") $dest /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
    Write-Host "[backup] storage mirrored"
}

# --- retention: keep 14 daily dumps, 4 weekly storage mirrors ---
$dumps = Get-ChildItem $backupDir -Filter "gajab-*.dump" | Sort-Object LastWriteTime -Descending
if ($dumps.Count -gt 14) {
    $dumps | Select-Object -Skip 14 | ForEach-Object { Remove-Item $_.FullName -Force; Write-Host ("[backup] pruned " + $_.Name) }
}
$mirrors = Get-ChildItem $backupDir -Directory -Filter "storage-*" | Sort-Object LastWriteTime -Descending
if ($mirrors.Count -gt 4) {
    $mirrors | Select-Object -Skip 4 | ForEach-Object { Remove-Item $_.FullName -Recurse -Force; Write-Host ("[backup] pruned " + $_.Name) }
}
Write-Host "[backup] done"
