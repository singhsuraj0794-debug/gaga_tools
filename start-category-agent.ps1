#Requires -Version 5.1
# start-category-agent.ps1 - local vision/chat agent: DeepSeek-V4-Pro-Qwen3.5-9B
# (UNCENSORED heretic, GGUF Q4_K_M + mmproj) served by llama.cpp on :8082, GPU
# offload, vision enabled, reasoning disabled. Used by _category_agent.py,
# _text_agent.py and the vision path (_qwen_model.py) for every local LLM call.
#
# Separate from the embedding server on :8081 so the two models do not fight for
# the same process; the watchdog re-runs this whenever :8082 is closed.
. "$PSScriptRoot\windows-common.ps1"

$Model = "C:\gajab\models\DeepSeekV4Pro-Qwen3.5-9B-Q4_K_M.gguf"
$Mmproj = "C:\gajab\models\DeepSeekV4Pro-Qwen3.5-9B-mmproj-f16.gguf"
# 8192 ctx with the default (unified) KV cache keeps image tokens inside the
# window while the 9B Q4 weights (~5.4GB) + mmproj (~0.9GB) stay on the GPU.
# Do NOT add --parallel: it makes the KV cache non-unified and doubles the
# compute buffers (~9GB extra), which pushed generation from ~66 to ~4 tok/s.
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
$llamaArgs = @(
    "-m", $Model,
    "-ngl", "99",
    "-c", "$ContextSize",
    "--parallel", "1",
    "--port", "$AgentPort",
    "--host", "127.0.0.1",
    "--jinja",
    "--reasoning", "off",
    "-t", "8"
)
if (Test-Path $Mmproj) { $llamaArgs += @("--mmproj", $Mmproj) }
Start-Bg -FilePath $exe -Arguments $llamaArgs -WorkingDirectory $RepoDir -LogName "category-agent.log" | Out-Null

for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Seconds 2
    if (Test-PortOpen $AgentPort) { Write-Host "OK: category agent up"; return }
}
Write-Warning "FAILED: category agent did not come up (see $env:TEMP\category-agent.log.err)"
