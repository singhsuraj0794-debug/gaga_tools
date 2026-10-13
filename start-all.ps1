#Requires -Version 5.1
# start-all.ps1 - start the whole local stack on Windows (idempotent).
#   API 8090 + CLIP 8001 + analysis 8003 + cloudflared   (compute)
#   scraper 9000 + ngrok permanent domain                (scraping)
#   Chrome 9223 (+ Webshare proxy 9224) and 9225          (browsers)
. "$PSScriptRoot\windows-common.ps1"
Import-DotEnv

# Rebuild the API bundle first. dist/ is gitignored and start-prelisting-tunnel.ps1
# runs dist/index.mjs, so a git pull that changes TypeScript is NOT live until this
# runs — a "deployed" fix silently did nothing because this step was missing.
if (Test-Path (Join-Path $ApiDir "build.mjs")) {
    Write-Host "[build] api-server bundle ..."
    Push-Location $ApiDir
    try {
        npm run build 2>&1 | Select-Object -Last 8 | ForEach-Object { Write-Host "  $_" }
    } catch {
        Write-Warning "api-server build failed: $_"
    } finally {
        Pop-Location
    }
}

& "$PSScriptRoot\start-prelisting-tunnel.ps1"
& "$PSScriptRoot\start-scraper-tunnel.ps1"
& "$PSScriptRoot\start-chrome-extract.ps1"
& "$PSScriptRoot\start-chrome-scraper.ps1"

Write-Host ""
Write-Host "=== STATUS ==="
$services = @(
    @{ Port = $ClipPort;          Name = "CLIP" },
    @{ Port = $AnalysisPort;      Name = "ANALYSIS" },
    @{ Port = $ApiPort;           Name = "API" },
    @{ Port = $ScraperPort;       Name = "SCRAPER" },
    @{ Port = $ChromeScraperPort; Name = "CHROME-SCRAPE" },
    @{ Port = $ChromeExtractPort; Name = "CHROME-EXTRACT" },
    @{ Port = $LocalProxyPort;    Name = "WEBSHARE-PROXY" }
)
foreach ($s in $services) {
    $state = if (Test-PortOpen $s.Port) { "up" } else { "DOWN" }
    Write-Host ("  :{0,-5} {1,-16} {2}" -f $s.Port, $s.Name, $state)
}
Write-Host ""
Write-Host "Compute API URL: https://$NgrokDomain"
