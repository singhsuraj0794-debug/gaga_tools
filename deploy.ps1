#Requires -Version 5.1
# deploy.ps1 - pull the latest code pushed from the Mac, rebuild the API bundle,
# and restart the local stack. Intended to run from a scheduled task (see
# WINDOWS-MIGRATION.md) or by hand.
#
#   powershell -File deploy.ps1            # pull + rebuild + restart
#   powershell -File deploy.ps1 -NoRestart # pull + rebuild only
param([switch]$NoRestart)

. "$PSScriptRoot\windows-common.ps1"

Set-Location $RepoDir

Write-Host "== git pull =="
git pull --ff-only
if ($LASTEXITCODE -ne 0) { throw "git pull failed ($LASTEXITCODE)" }

Write-Host "== rebuild api-server =="
Push-Location $ApiDir
pnpm install --no-frozen-lockfile
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "pnpm install failed" }
pnpm --filter @workspace/api-server run build
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "api-server build failed" }
Pop-Location

if (-not $NoRestart) {
    Write-Host "== restart stack =="
    & "$PSScriptRoot\stop-all.ps1"
    Start-Sleep -Seconds 2
    & "$PSScriptRoot\start-all.ps1"
}

Write-Host "Deploy complete."
