# Prints (does NOT run) the commands that register Aurora autopilot in Windows Task Scheduler:
#   nightly render (research -> reels -> veto queue) + hourly publish-due (uploads reels whose veto window passed).
# You decide: copy/paste the printed block into PowerShell.   .\scripts\autopilot_task.ps1 [-RenderTime 02:00]
param(
    [string]$RenderTime = "02:00",
    [string]$RenderTask = "AuroraAutopilotRender",
    [string]$PublishTask = "AuroraAutopilotPublish"
)

$root = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { $python = "python" }

Write-Host "# --- Aurora autopilot: NOT registered. Copy the block below to register it. ---"
Write-Host @"
`$render  = New-ScheduledTaskAction -Execute '$python' -Argument '-m aurora.autopilot render' -WorkingDirectory '$root'
`$publish = New-ScheduledTaskAction -Execute '$python' -Argument '-m aurora.autopilot publish-due' -WorkingDirectory '$root'
`$nightly = New-ScheduledTaskTrigger -Daily -At $RenderTime
`$hourly  = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours 1)
`$set     = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName '$RenderTask'  -Action `$render  -Trigger `$nightly -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 6)) -Description 'Aurora autopilot: research + render + queue'
Register-ScheduledTask -TaskName '$PublishTask' -Action `$publish -Trigger `$hourly  -Settings `$set -Description 'Aurora autopilot: upload due reels'
"@
Write-Host "# Remove later:  Unregister-ScheduledTask -TaskName '$RenderTask','$PublishTask' -Confirm:`$false"
Write-Host "# One-time YouTube consent first:  & '$python' -m aurora.publish.youtube auth --channel <name>"
