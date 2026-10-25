# Aurora autopilot scheduling for a PC that is OFF at night.
#   .\scripts\autopilot_task.ps1              print the commands only (registers NOTHING)
#   .\scripts\autopilot_task.ps1 -Register    register both tasks for the current user
#   .\scripts\autopilot_task.ps1 -Unregister  remove them
# Tasks (hidden window, output appended to logs\autopilot_render.task.log / logs\autopilot_publish.task.log):
#   "Aurora Render"  : at logon + 11:00 + 15:00  -> python -m aurora.autopilot render   (fills only the day's deficit)
#   "Aurora Servers" : at logon -> scripts\start_aurora.ps1 -Ensure (hidden supervisor for ComfyUI/API/UI; idempotent)
#   "Aurora Publish" : at logon + hourly 09:00-23:00 -> python -m aurora.autopilot publish-due (uploads ahead as private + publishAt)
# StartWhenAvailable = a run missed while the PC was off starts as soon as it is on again.
param(
    [switch]$Register,
    [switch]$Unregister,
    [switch]$ServersOnly,
    [string]$ServersTask = "Aurora Servers",
    [string]$RenderTask = "Aurora Render",
    [string]$PublishTask = "Aurora Publish"
)

$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv\Scripts\python.exe"
$python = if (Test-Path $venv) { $venv } else { (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $python) { $python = "python" }
$logs = Join-Path $root "logs"

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $RenderTask, $PublishTask, $ServersTask -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed '$RenderTask', '$PublishTask' and '$ServersTask'."
    return
}

function New-AuroraAction([string]$cmd, [string]$log, [switch]$EnsureServers) {
    # Hidden; output is appended to the log as UTF-8 (a plain `*>>` in Windows PowerShell 5.1 writes UTF-16).
    # No double quotes inside $inner: it is embedded in a double-quoted -Command argument.
    $ensure = ""
    if ($EnsureServers) {
        # make sure the supervisor is alive, then wait (<=120 s) for ComfyUI
        $ensure = "& '$PSScriptRoot\start_aurora.ps1' -Ensure | Out-Null; `$t = 0; while (`$t -lt 120 -and -not (Get-NetTCPConnection -State Listen -LocalPort 8188 -ErrorAction SilentlyContinue)) { Start-Sleep 5; `$t += 5 }; "
    }
    $inner = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; `$env:PYTHONUTF8 = '1'; Set-Location -LiteralPath '$root'; $ensure& '$python' -m aurora.autopilot $cmd 2>&1 | ForEach-Object { `$_.ToString() } | Add-Content -Encoding utf8 -LiteralPath '$logs\$log'"
    New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -Command `"$inner`"" -WorkingDirectory $root
}
function New-ServersAction {
    New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSScriptRoot\start_aurora.ps1`" -Ensure" -WorkingDirectory $root
}

$user = "$env:USERDOMAIN\$env:USERNAME"
$render  = New-AuroraAction "render" "autopilot_render.task.log" -EnsureServers
$publish = New-AuroraAction "publish-due" "autopilot_publish.task.log"
$renderTriggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $user),
    (New-ScheduledTaskTrigger -Daily -At 11:00),
    (New-ScheduledTaskTrigger -Daily -At 15:00))
# -Daily cannot take repetition parameters: borrow the Repetition block of a -Once trigger (every hour for 14 h = 09:00..23:00)
$hourly = New-ScheduledTaskTrigger -Daily -At 09:00
$hourly.Repetition = (New-ScheduledTaskTrigger -Once -At 09:00 -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Hours 14)).Repetition
$publishTriggers = @((New-ScheduledTaskTrigger -AtLogOn -User $user), $hourly)
$serversTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$serversSet = New-ScheduledTaskSettingsSet -StartWhenAvailable -Hidden -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$renderSet  = New-ScheduledTaskSettingsSet -StartWhenAvailable -Hidden -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 5) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$publishSet = New-ScheduledTaskSettingsSet -StartWhenAvailable -Hidden -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

if ($Register) {
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    Register-ScheduledTask -TaskName $ServersTask -Action (New-ServersAction) -Trigger $serversTrigger -Settings $serversSet -Description "Aurora: start the supervisor for ComfyUI/API/UI at logon (idempotent)" -Force | Out-Null
    if ($ServersOnly) { Write-Host "Registered '$ServersTask' only. Check: Get-ScheduledTask '$ServersTask'"; return }
    Register-ScheduledTask -TaskName $RenderTask  -Action $render  -Trigger $renderTriggers  -Settings $renderSet  -Description "Aurora autopilot: research + render + queue" -Force | Out-Null
    Register-ScheduledTask -TaskName $PublishTask -Action $publish -Trigger $publishTriggers -Settings $publishSet -Description "Aurora autopilot: upload ahead (private + publishAt) + daily report" -Force | Out-Null
    Write-Host "Registered '$ServersTask', '$RenderTask' and '$PublishTask'. Check: Get-ScheduledTask 'Aurora*'"
    Write-Host "Note: publishing still needs autopilot.publish.enabled / the channel's publish_enabled in config.yaml."
    return
}

Write-Host "# --- NOT registered. To register, run:   .\scripts\autopilot_task.ps1 -Register   (PowerShell, from $root)"
Write-Host "# Only the servers task:                .\scripts\autopilot_task.ps1 -Register -ServersOnly"
Write-Host "# Remove later:                         .\scripts\autopilot_task.ps1 -Unregister"
Write-Host "# Python used: $python"
Write-Host "# '$RenderTask'  : at logon, daily 11:00, daily 15:00  -> aurora.autopilot render      (log logs\autopilot_render.task.log)"
Write-Host "# '$PublishTask' : at logon, hourly 09:00-23:00        -> aurora.autopilot publish-due (log logs\autopilot_publish.task.log)"
Write-Host "# Both: hidden, StartWhenAvailable (run missed tasks when the PC is next on), no overlapping runs."
Write-Host "# One-time YouTube consent first:  & '$python' -m aurora.publish.youtube auth --channel shinobi"
