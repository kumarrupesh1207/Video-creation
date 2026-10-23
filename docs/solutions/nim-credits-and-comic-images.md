# NIM credits are invisible; comic images default to NIM

Problem: comic reels need ~6-7 images each; the NVIDIA NIM credit balance cannot be read from the API, so a run could silently exhaust it.
Facts (checked 2026-10-23): `quota: 80` in config.yaml is only a UI display number, nothing enforces it; a real NIM image response carries no quota/credit/rate-limit headers (only `Nvcf-Reqid`, `Nvcf-Status`); the real balance is only visible in the user's build.nvidia.com account. The provider now keeps the API's own error body (e.g. HTTP 402/429 text) and the last response headers in `outputs/costs/nim_last_headers.json`.
Fix: `comic.image_mode: nim` (default). Every NIM image is counted in `outputs/costs/nvidia_nim-credits.json` (`aurora/costs.py`), capped by `comic.max_nim_credits_per_reel` (8) and `providers.nvidia_nim.max_credits_per_day` (24). When NIM errors or a cap is hit, the REMAINING panels use ComfyUI + IP-Adapter on the first NIM panel as the reference (`reference_weight: 0.4`); the fallback is printed and written to the sidecar `image_note`/`image_mode` ("nim+comfy_fallback").
Why not ComfyUI first: SD1.5 + IP-Adapter at weight 0.7 copied the reference pose into all 7 panels and took ~695 s per image on CPU (~80 min per reel).
How to recognise: sidecar `image_mode` is `nim+comfy_fallback`, or the log line `[comic] FALLBACK to ComfyUI IP-Adapter`.
