# Prints (does NOT run) the commands that register a nightly Aurora autorun in Windows Task Scheduler.
# You decide: copy/paste the printed block into an elevated-not-required PowerShell to register it.
#   .\scripts\schedule_autorun.ps1 [-Time 02:30] [-Topics topics.txt] [-Providers local_comfyui] [-Extra "--voiceover"]
# Default provider is local_comfyui (free, CPU) so the nightly run never spends NVIDIA credits.
param(
    [string]$Time = "02:30",
    [string]$Topics = "topics.txt",
    [string]$Providers = "local_comfyui",
    [string]$Extra = "",
    [string]$TaskName = "AuroraNightlyAutorun"
)

$root = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { $python = "python" }
$args = "-m aurora.autorun --topics `"$Topics`" --providers $Providers $Extra".Trim()

Write-Host "# --- Aurora nightly autorun: NOT registered. Copy the block below to register it. ---"
Write-Host @"
`$action   = New-ScheduledTaskAction -Execute '$python' -Argument '$args' -WorkingDirectory '$root'
`$trigger  = New-ScheduledTaskTrigger -Daily -At $Time
`$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 6) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName '$TaskName' -Action `$action -Trigger `$trigger -Settings `$settings -Description 'Aurora topics -> reels'
"@
Write-Host "# Remove later with:  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
Write-Host "# Run once now to test:  & '$python' $args"
