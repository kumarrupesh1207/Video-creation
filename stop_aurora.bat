@echo off
rem Stops only Aurora's three services. ComfyUI is left running if its queue has running/pending items.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_aurora.ps1" -Stop
