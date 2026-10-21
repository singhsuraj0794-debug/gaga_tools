#Requires -Version 5.1
# start-db.ps1 - start the local Postgres + PostgREST for the Option A stack.
#
# Postgres runs as a detached daemon (pg_ctl start), so it survives on its own.
# PostgREST is a foreground exe, so it MUST be launched from a process that stays
# alive - run-stack.ps1 (the Gajab-Stack scheduled task) - otherwise it dies the
# moment the SSH session or short-lived task that started it ends.
. "$PSScriptRoot\windows-common.ps1"
if (-not (Get-Command Stage -ErrorAction SilentlyContinue)) { function Stage([string]$m) { } }
Stage "db: begin"

$PGBIN = Join-Path $PgRoot "pgsql\bin"
$SECRETS = Join-Path $DbRoot ".db-secrets.json"
$CONF = Join-Path $DbRoot "postgrest.conf"

if (-not (Test-Path $PGBIN)) {
    Write-Warning "[db] Postgres not found at $PGBIN - run the db bootstrap first"
    return
}

Stage "db: postgres check"
# --- Postgres ---
if (Test-PortOpen $PgPort) {
    Write-Host "[db] Postgres already listening on $PgPort"
} else {
    Write-Host "[db] starting Postgres on $PgPort"
    & "$PGBIN\pg_ctl.exe" -D $PgData -l (Join-Path $DbRoot "pg.log") start | Out-Null
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-PortOpen $PgPort) { break }
    }
    if (Test-PortOpen $PgPort) { Write-Host "[db] OK: Postgres up" }
    else { Write-Warning "[db] FAILED: Postgres did not come up (see pg.log)" }
}

Stage "db: postgrest check"
# --- PostgREST ---
if (Test-PortOpen $PostgrestPort) {
    Write-Host "[db] PostgREST already listening on $PostgrestPort"
} else {
    if (-not (Test-Path $CONF)) {
        Write-Warning "[db] $CONF missing - run the db bootstrap first"
        return
    }
    Write-Host "[db] starting PostgREST on $PostgrestPort"
    # postgrest.exe links libpq/OpenSSL from the Postgres bin dir, and the DLLs
    # were copied next to it as well - without them it exits with 0xC0000135
    # (STATUS_DLL_NOT_FOUND) and prints nothing at all.
    $env:PATH = "$PGBIN;$(Join-Path $DbRoot "postgrest");$env:PATH"
    Start-Process -FilePath (Join-Path $DbRoot "postgrest\postgrest.exe") `
        -ArgumentList $CONF `
        -RedirectStandardOutput (Join-Path $DbRoot "postgrest.log") `
        -RedirectStandardError (Join-Path $DbRoot "postgrest.err") `
        -WindowStyle Hidden | Out-Null
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-PortOpen $PostgrestPort) { break }
    }
    if (Test-PortOpen $PostgrestPort) { Write-Host "[db] OK: PostgREST up" }
    else { Write-Warning "[db] FAILED: PostgREST did not come up (see postgrest.err)" }
}

Stage "db: shim check"
# --- storage shim (+ /rest/v1 proxy) -----------------------------------------
# supabase-js wants ONE origin serving both /rest/v1 and /storage/v1. PostgREST
# owns /rest/v1; the shim serves /storage/v1 from a local folder and forwards the
# rest, so client code only needs its base URL changed.
if (Test-PortOpen $ShimPort) {
    Write-Host "[db] storage shim already listening on $ShimPort"
} else {
    $shim = Join-Path $PSScriptRoot "db\storage-shim.mjs"
    if (-not (Test-Path $shim)) {
        Write-Warning "[db] $shim missing"
        return
    }
    Write-Host "[db] starting storage shim on $ShimPort"
    $keys = ""
    if (Test-Path $SECRETS) {
        $sec = Get-Content $SECRETS -Raw | ConvertFrom-Json
        # -join, not Join-String: that cmdlet is PowerShell 7+ and this runs on 5.1
        $keys = (@($sec.anon_jwt, $sec.service_jwt) | Where-Object { $_ }) -join ","
    }
    $env:STORAGE_ROOT = Join-Path $DbRoot "storage"
    $env:STORAGE_PORT = "$ShimPort"
    $env:POSTGREST_URL = "http://127.0.0.1:$PostgrestPort"
    $env:SHIM_KEYS = $keys
    Start-Process -FilePath "node" -ArgumentList $shim `
        -RedirectStandardOutput (Join-Path $DbRoot "shim.log") `
        -RedirectStandardError (Join-Path $DbRoot "shim.err") `
        -WindowStyle Hidden | Out-Null
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-PortOpen $ShimPort) { break }
    }
    if (Test-PortOpen $ShimPort) { Write-Host "[db] OK: storage shim up" }
    else { Write-Warning "[db] FAILED: storage shim did not come up (see shim.err)" }
}
Stage "db: done"
