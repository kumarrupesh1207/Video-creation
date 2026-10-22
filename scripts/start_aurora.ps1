# Start / stop the three Aurora services (ComfyUI :8188, aurora-api :8000, aurora-ui :5173).
# Commands mirror .claude/launch.json. Usage (normally via start_aurora.bat / stop_aurora.bat):
#   start_aurora.ps1                 launch ONE hidden, detached supervisor for all three services, open the UI
#   start_aurora.ps1 -Stop           stop only these three (ComfyUI is spared while its queue is busy)
#   start_aurora.ps1 -Supervise all  internal: the supervisor loop
# The supervisor is created through WMI (Win32_Process.Create) so it is NOT a child of the caller:
# closing the launching console / killing the caller's process tree / job object does not kill it.
param([switch]$Stop, [string]$Supervise = "", [switch]$NoBrowser)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$Logs = Join-Path $Root "logs"
$Comfy = "C:/Rupesh/App/ComfyUI_windows_portable_nvidia/ComfyUI_windows_portable"

$Services = @{
    comfyui = @{ Port = 8188; Exe = "$Comfy/python_embeded/python.exe"
                 Args = "-s `"$Comfy/ComfyUI/main.py`" --cpu --windows-standalone-build --listen 127.0.0.1 --port 8188" }
    api     = @{ Port = 8000; Exe = "python"; Args = "server.py" }
    ui      = @{ Port = 5173; Exe = "cmd.exe"; Args = "/c npm --prefix ui run dev -- --port 5173 --strictPort" }
}
$Order = "comfyui", "api", "ui"

function Test-Port([int]$Port) {
    [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}
function Get-Supervisors {
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" |
        Where-Object { $_.CommandLine -match "start_aurora\.ps1.*-Supervise all" }
}

# ---- supervisor: one hidden long-running process ---------------------------------------------
if ($Supervise) {
    $env:PYTHONUTF8 = "1"
    Set-Location $Root
    New-Item -ItemType Directory -Force $Logs | Out-Null
    $sup = Join-Path $Logs "supervisor.log"
    function Log([string]$svc, [string]$msg) {
        $line = "[$(Get-Date -Format s)] $svc : $msg"
        Add-Content $sup $line
        if ($svc -ne "supervisor") { Add-Content (Join-Path $Logs "$svc.log") $line }
    }
    $st = @{}
    foreach ($n in $Order) { $st[$n] = @{ Proc = $null; Started = $null; NextStart = Get-Date; Delay = 2; Watching = $false } }
    Log "supervisor" "started pid=$PID"
    $lastBeat = Get-Date
    while ($true) {
        $now = Get-Date
        foreach ($n in $Order) {
            $s = $Services[$n]; $x = $st[$n]
            $listening = Test-Port $s.Port
            if ($x.Proc -ne $null -and $x.Proc.HasExited) {
                $up = ($now - $x.Started).TotalSeconds
                $x.Delay = if ($up -gt 60) { 2 } else { [Math]::Min($x.Delay * 2, 30) }
                $x.NextStart = $now.AddSeconds($x.Delay)
                Log $n "exited (code $($x.Proc.ExitCode)) after $([int]$up)s; restarting in $($x.Delay)s"
                $x.Proc = $null
            }
            if ($x.Proc -ne $null) {   # ours and still alive: kill only if it never opened its port
                if (-not $listening -and ($now - $x.Started).TotalSeconds -gt 300) {
                    Log $n "no listener after 300s; killing hung start (pid $($x.Proc.Id))"
                    taskkill /PID $x.Proc.Id /T /F | Out-Null
                }
                continue
            }
            if ($listening) {          # someone else owns the port: never kill it, take over when it frees
                if (-not $x.Watching) { Log $n ":$($s.Port) in use by another process; watching"; $x.Watching = $true }
                continue
            }
            $x.Watching = $false
            if ($now -lt $x.NextStart) { continue }
            Log $n "starting"
            try {
                $p = Start-Process -FilePath $s.Exe -ArgumentList $s.Args -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
                     -RedirectStandardOutput (Join-Path $Logs "$n.out.log") -RedirectStandardError (Join-Path $Logs "$n.err.log")
                $null = $p.Handle      # cache handle so ExitCode is readable after exit
                $x.Proc = $p; $x.Started = Get-Date
            } catch {
                $x.Delay = [Math]::Min($x.Delay * 2, 30); $x.NextStart = $now.AddSeconds($x.Delay)
                Log $n "START FAILED: $($_.Exception.Message); retry in $($x.Delay)s"
            }
        }
        if (($now - $lastBeat).TotalSeconds -ge 60) {
            $lastBeat = $now
            $state = ($Order | ForEach-Object { "$_=" + $(if (Test-Port $Services[$_].Port) { "up" } else { "DOWN" }) }) -join " "
            Log "supervisor" "heartbeat $state"
        }
        Start-Sleep 5
    }
}

# ---- stop ------------------------------------------------------------------------------------
if ($Stop) {
    # kill the supervisor first (no /T: its children are handled below, ComfyUI may be mid-job)
    $comfyBusy = $false
    if (Test-Port $Services.comfyui.Port) {
        try {
            $q = Invoke-RestMethod "http://127.0.0.1:8188/queue" -TimeoutSec 5
            $busy = @($q.queue_running).Count + @($q.queue_pending).Count
        } catch { $busy = -1 }
        if ($busy -ne 0) {
            $comfyBusy = $true
            Write-Warning "ComfyUI NOT stopped: queue busy or unreadable (running+pending=$busy). Stop it manually when idle."
        }
    }
    Get-Supervisors | ForEach-Object { taskkill /PID $_.ProcessId /F | Out-Null }
    if ($comfyBusy) {
        Write-Warning "Supervisor stopped, so ComfyUI is no longer auto-restarted; re-run start_aurora.bat to re-attach."
    }
    foreach ($name in $Order) {
        if ($name -eq "comfyui" -and $comfyBusy) { continue }
        $s = $Services[$name]
        Get-NetTCPConnection -State Listen -LocalPort $s.Port -ErrorAction SilentlyContinue |
            ForEach-Object { taskkill /PID $_.OwningProcess /T /F | Out-Null }
        Write-Host "stopped $name (:$($s.Port))"
    }
    exit 0
}

# ---- start -----------------------------------------------------------------------------------
New-Item -ItemType Directory -Force $Logs | Out-Null
if (Get-Supervisors) {
    Write-Host "supervisor already running"
} else {
    $cmd = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Supervise all"
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = $Root }
    if ($r.ReturnValue -eq 0) { Write-Host "supervisor started (pid $($r.ProcessId)); logs\supervisor.log" }
    else { Write-Warning "could not start supervisor (WMI code $($r.ReturnValue))"; exit 1 }
}

$deadline = (Get-Date).AddSeconds(180)
foreach ($name in $Order) {
    $s = $Services[$name]
    while (-not (Test-Port $s.Port) -and (Get-Date) -lt $deadline) { Start-Sleep 2 }
    if (Test-Port $s.Port) { Write-Host "OK   $name :$($s.Port)" } else { Write-Warning "TIMEOUT $name :$($s.Port) - see logs\$name.log" }
}
if (-not $NoBrowser -and (Test-Port 5173)) { Start-Process "http://localhost:5173" }
