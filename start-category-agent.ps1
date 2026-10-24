#Requires -Version 5.1
# start-category-agent.ps1 - local category agent: Qwen2.5-14B-Instruct (GGUF,
# Q4_K_M) served by llama.cpp on :8082, GPU offload. Used by _category_agent.py
# to pick the L4 category from a shortlist of real taxonomy paths.
#
# Separate from the embedding server on :8081 so the two models do not fight for
# the same process; the watchdog re-runs this whenever :8082 is closed.
. "$PSScriptRoot\windows-common.ps1"

$Model = "C:\gajab\models\Qwen2.5-14B-Instruct-Q4_K_M.gguf"
$ContextSize = 4096

if (Test-PortOpen $AgentPort) {
    Write-Host "Category agent already listening on $AgentPort"
    return
}
if (-not (Test-Path $Model)) {
    Write-Warning "Category agent model missing: $Model"
    return
}

$exe = (Get-Command llama-server -ErrorAction SilentlyContinue).Source
if (-not $exe) {
    $exe = (Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter "llama-server.exe" -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
}
if (-not $exe) {
    Write-Warning "llama-server not found - category agent unavailable"
    return
}

Write-Host "Starting category agent on $AgentPort ($Model)"
Start-Bg -FilePath $exe -Arguments @(
    "-m", $Model,
    "-ngl", "99",
    "-c", "$ContextSize",
    "--port", "$AgentPort",
    "--host", "127.0.0.1",
    "--jinja",
    "-t", "8"
) -WorkingDirectory $RepoDir -LogName "category-agent.log" | Out-Null

for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Seconds 2
    if (Test-PortOpen $AgentPort) { Write-Host "OK: category agent up"; return }
}
Write-Warning "FAILED: category agent did not come up (see $env:TEMP\category-agent.log.err)"
