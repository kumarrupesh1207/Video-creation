# ComfyUI comic render silently spent NIM credits

Problem: a ComfyUI (IP-Adapter) comic render timed out on CPU and silently fell back to NVIDIA NIM, spending 12 credits vs. the 7 allowed.
Root cause: the default 900 s per-image timeout is too short for CPU image/IP-Adapter workflows, and the comic fallback to NIM was implicit.
Fix: 3600 s timeout for `image_gen*`/`img2img*` workflows in `comfy_orchestrator.py`; comic `image_mode: comfy` never falls back to NIM (65e825f). Any credit-spending fallback must be capped in config and logged in the sidecar.
How to recognise: sidecar/job shows provider `nvidia_nim` on a comfy-mode render; ComfyUI log shows a cancelled/late prompt.
Note: IP-Adapter weight too high made every panel a copy of the reference image — keep it moderate (~0.4).
