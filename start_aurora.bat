@echo off
rem One-click start: ComfyUI :8188, aurora-api :8000, aurora-ui :5173 (skips ports already in use).
rem Logs: logs\comfyui.log, logs\api.log, logs\ui.log. Stop with stop_aurora.bat.
set PYTHONUTF8=1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_aurora.ps1" %*
