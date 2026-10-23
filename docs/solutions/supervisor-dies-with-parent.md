# Servers silently down ("Failed to fetch", ComfyUI :8188 dead)

Problem: API/UI/ComfyUI kept stopping; the UI showed "Failed to fetch" and a restyle job failed because ComfyUI was down for hours.
Root cause: the start_aurora supervisors were child processes of the shell/tool call that launched them, so they died with it; the Claude app also stops its preview servers.
Fix: one detached hidden supervisor started via WMI with a 1-min heartbeat in `logs/supervisor.log`; restarts any service whose port is free and never kills a running one (98c7c37, earlier 1343568).
How to recognise: `logs/<svc>.log` has a "starting" line but no later restart lines; `curl :8188/system_stats` fails.
