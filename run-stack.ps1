#Requires -Version 5.1
# run-stack.ps1 - scheduled-task entry point. Starts the whole local stack and
# then STAYS ALIVE, so the child processes it launched are not reaped when the
# task instance completes (on Windows, background processes started from an SSH
# session or a short-lived task are killed when that session/task ends).
#
# Registered as scheduled task "Gajab-Stack" (trigger: at logon).
. "$PSScriptRoot\windows-common.ps1"

& "$PSScriptRoot\start-all.ps1"

Write-Host "[run-stack] stack started; staying alive to keep children alive."
while ($true) { Start-Sleep -Seconds 3600 }
