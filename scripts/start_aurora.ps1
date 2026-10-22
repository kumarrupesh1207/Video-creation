# Start / stop the three Aurora services (ComfyUI :8188, aurora-api :8000, aurora-ui :5173).
# Commands mirror .claude/launch.json. Usage (normally via start_aurora.bat / stop_aurora.bat):
#   start_aurora.ps1                 start whatever is not already listening, open the UI
#   start_aurora.ps1 -Stop           stop only these three (ComfyUI is spared while its queue is busy)
#   start_aurora.ps1 -Supervise api  internal: restart loop for one service
param([switch]$Stop, [string]$Supervise = "", [switch]$NoBrowser)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$Logs = Join-Path $Root "logs"
$Comfy = "C:/Rupesh/App/ComfyUI_windows_portable_nvidia/ComfyUI_windows_portable"

$Services = @{
    comfyui = @{ Port = 8188; Title = "Aurora ComfyUI"; Exe = "$Comfy/python_embeded/python.exe"
                 Args = "-s `"$Comfy/ComfyUI/main.py`" --cpu --windows-standalone-build --listen 127.0.0.1 --port 8188" }
    api     = @{ Port = 8000; Title = "Aurora API"; Exe = "python"; Args = "server.py" }
    ui      = @{ Port = 5173; Title = "Aurora UI"; Exe = "cmd.exe"
                 Args = "/c npm --prefix ui run dev -- --port 5173 --strictPort" }
}
$Order = "comfyui", "api", "ui"

function Test-Port([int]$Port) {
    [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

# ---- supervisor: runs inside its own minimized window --------------------------------------
if ($Supervise) {
    $s = $Services[$Supervise]
    $Host.UI.RawUI.WindowTitle = $s.Title
    $env:PYTHONUTF8 = "1"
    Set-Location $Root
    New-Item -ItemType Directory -Force $Logs | Out-Null
    $log = Join-Path $Logs "$Supervise.log"
    $delay = 2
    $waiting = $false
    while ($true) {
        if (Test-Port $s.Port) {   # someone else owns the port: never kill it, take over when it frees
            if (-not $waiting) { "[$(Get-Date -Format s)] $Supervise :$($s.Port) in use by another process; watching" | Add-Content $log; $waiting = $true }
            Start-Sleep 5; continue
        }
        $waiting = $false
        $t0 = Get-Date
        "[$(Get-Date -Format s)] starting $Supervise" | Add-Content $log
        $p = Start-Process -FilePath $s.Exe -ArgumentList $s.Args -WorkingDirectory $Root -NoNewWindow -PassThru `
             -RedirectStandardOutput "$log.out" -RedirectStandardError "$log.err"
        $null = $p.Handle          # cache handle so ExitCode is readable after exit
        $p.WaitForExit()
        foreach ($f in "$log.out", "$log.err") { if (Test-Path $f) { Get-Content $f | Add-Content $log; Remove-Item $f -ErrorAction SilentlyContinue } }
        $up = ((Get-Date) - $t0).TotalSeconds
        if ($up -gt 60) { $delay = 2 } else { $delay = [Math]::Min($delay * 2, 30) }
        "[$(Get-Date -Format s)] $Supervise exited (code $($p.ExitCode)) after $([int]$up)s; restarting in ${delay}s" | Add-Content $log
        Start-Sleep $delay
    }
}

# ---- stop ----------------------------------------------------------------------------------
if ($Stop) {
    foreach ($name in $Order) {
        $s = $Services[$name]
        if ($name -eq "comfyui" -and (Test-Port $s.Port)) {
            try {
                $q = Invoke-RestMethod "http://127.0.0.1:8188/queue" -TimeoutSec 5
                $busy = @($q.queue_running).Count + @($q.queue_pending).Count
            } catch { $busy = -1 }
            if ($busy -ne 0) {
                Write-Warning "ComfyUI NOT stopped: queue busy or unreadable (running+pending=$busy). Stop it manually when idle."
                continue
            }
        }
        # kill the supervisor first so it cannot restart the service
        Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" |
            Where-Object { $_.CommandLine -match "start_aurora\.ps1.*-Supervise $name(\s|$)" } |
            ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null }
        Get-NetTCPConnection -State Listen -LocalPort $s.Port -ErrorAction SilentlyContinue |
            ForEach-Object { taskkill /PID $_.OwningProcess /T /F | Out-Null }
        Write-Host "stopped $name (:$($s.Port))"
    }
    exit 0
}

# ---- start ---------------------------------------------------------------------------------
New-Item -ItemType Directory -Force $Logs | Out-Null
function Test-Supervisor([string]$name) {
    [bool](Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" |
        Where-Object { $_.CommandLine -match "start_aurora\.ps1.*-Supervise $name(\s|$)" })
}
foreach ($name in $Order) {
    $s = $Services[$name]
    if (Test-Supervisor $name) { Write-Host "$name supervisor already running - skipped"; continue }
    # Always supervise, even when the port is already taken: the supervisor only starts the service
    # once the port is free, and never kills a running instance.
    Start-Process powershell -WindowStyle Minimized -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"", "-Supervise", $name
    if (Test-Port $s.Port) { Write-Host "supervising $name (:$($s.Port) already in use; will take over if it drops)" }
    else { Write-Host "starting $name (:$($s.Port))" }
}

$deadline = (Get-Date).AddSeconds(180)
foreach ($name in $Order) {
    $s = $Services[$name]
    while (-not (Test-Port $s.Port) -and (Get-Date) -lt $deadline) { Start-Sleep 2 }
    if (Test-Port $s.Port) { Write-Host "OK   $name :$($s.Port)" } else { Write-Warning "TIMEOUT $name :$($s.Port) - see logs\$name.log" }
}
if (-not $NoBrowser -and (Test-Port 5173)) { Start-Process "http://localhost:5173" }
