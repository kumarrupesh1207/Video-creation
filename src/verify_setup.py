"""
verify_setup.py
---------------
Run this script BEFORE main.py to catch environment issues early.
Checks: folder structure, config.yaml, ComfyUI, Ollama, FFmpeg, VRAM.

Usage:
    python src/verify_setup.py
"""

import sys
import yaml
import subprocess
import requests
import torch
from pathlib import Path


PASS = "✅"
WARN = "⚠️ "
FAIL = "❌"

results = []

def check(label: str, ok: bool, detail: str = "", warn_only=False):
    icon = PASS if ok else (WARN if warn_only else FAIL)
    msg = f"  {icon}  {label}"
    if detail:
        msg += f"\n       {detail}"
    print(msg)
    results.append(ok or warn_only)


# ── 1. Config ────────────────────────────────────────────────────────────────
print("\n═══ 1. CONFIG ═══════════════════════════════════")
cfg_path = Path("config.yaml")
try:
    cfg = yaml.safe_load(cfg_path.read_text())
    check("config.yaml loaded", True)
except Exception as e:
    check("config.yaml loaded", False, str(e))
    cfg = {}

# ── 2. Folder Structure ──────────────────────────────────────────────────────
print("\n═══ 2. FOLDER STRUCTURE ════════════════════════")
dirs = [
    "outputs/scripts", "outputs/images", "outputs/videos",
    "outputs/audio", "outputs/final", "src/modules", "src/workflows",
    "models", "assets"
]
for d in dirs:
    check(d, Path(d).exists())

# ── 3. ComfyUI ───────────────────────────────────────────────────────────────
print("\n═══ 3. COMFYUI ═════════════════════════════════")
comfy_url = cfg.get("models", {}).get("comfyui", {}).get("api_url", "http://127.0.0.1:8188")
try:
    r = requests.get(f"{comfy_url}/system_stats", timeout=3)
    stats = r.json()
    gpu_vram = stats.get("devices", [{}])[0].get("vram_total", 0) / (1024**3)
    check("ComfyUI reachable", True, f"VRAM reported: {gpu_vram:.1f} GB")
except Exception as e:
    check("ComfyUI reachable", False, f"{e}  →  Start ComfyUI first", warn_only=True)

# ── 4. Ollama ────────────────────────────────────────────────────────────────
print("\n═══ 4. OLLAMA ═══════════════════════════════════")
ollama_url = "http://127.0.0.1:11434"
try:
    r = requests.get(f"{ollama_url}/api/tags", timeout=3)
    models = [m["name"] for m in r.json().get("models", [])]
    check("Ollama server running", True)
    model_name = cfg.get("models", {}).get("llm", {}).get("model_name", "llama3:8b")
    check(f"Model '{model_name}' installed", model_name in models,
          f"Run: ollama pull {model_name}" if model_name not in models else "")
except Exception as e:
    check("Ollama server running", False,
          "Run: python src/ollama_fix.py", warn_only=True)

# ── 5. FFmpeg ────────────────────────────────────────────────────────────────
print("\n═══ 5. FFMPEG ═══════════════════════════════════")
ffmpeg_bin = cfg.get("paths", {}).get("ffmpeg_bin", "ffmpeg")
try:
    r = subprocess.run([ffmpeg_bin, "-version"], capture_output=True, text=True, timeout=5)
    ver_line = r.stdout.splitlines()[0] if r.stdout else "unknown"
    check("FFmpeg found", r.returncode == 0, ver_line[:60])
except FileNotFoundError:
    # Try system ffmpeg
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        ver_line = r.stdout.splitlines()[0] if r.stdout else "unknown"
        check("FFmpeg (system PATH)", r.returncode == 0, ver_line[:60])
    except Exception:
        check("FFmpeg found", False,
              "Download gyan.dev full build → set paths.ffmpeg_bin in config.yaml",
              warn_only=True)

# ── 6. CUDA / VRAM ───────────────────────────────────────────────────────────
print("\n═══ 6. CUDA / VRAM ══════════════════════════════")
try:
    cuda_ok = torch.cuda.is_available()
    check("CUDA available", cuda_ok)
    if cuda_ok:
        vram_total = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        vram_free  = (torch.cuda.get_device_properties(0).total_memory
                      - torch.cuda.memory_allocated(0)) / (1024**3)
        check(f"GPU: {torch.cuda.get_device_name(0)}", True,
              f"VRAM: {vram_total:.1f} GB total | {vram_free:.1f} GB free")
        check("VRAM ≥ 10 GB (SVD minimum)", vram_total >= 10,
              "SVD-XT needs ~10 GB at fp16. Run 576×1024 / 14 frames max.")
except Exception as e:
    check("CUDA check", False, str(e), warn_only=True)

# ── 7. Wav2Lip Venv ──────────────────────────────────────────────────────────
print("\n═══ 7. WAV2LIP VENV (Python 3.8) ════════════════")
wav2lip_py = cfg.get("models", {}).get("wav2lip", {}).get("python_bin", "")
wav2lip_ckpt = cfg.get("models", {}).get("wav2lip", {}).get("checkpoint", "")
try:
    r = subprocess.run([wav2lip_py, "--version"], capture_output=True, text=True, timeout=5)
    ver = r.stdout.strip() or r.stderr.strip()
    is_38 = "3.8" in ver
    check("Wav2Lip Python 3.8 venv", is_38,
          f"Found: {ver}" + ("" if is_38 else "  ⚠️ Must be Python 3.8!"))
except Exception:
    check("Wav2Lip Python 3.8 venv", False,
          f"Not found at: {wav2lip_py}\nCreate: python3.8 -m venv C:/tools/Wav2Lip/venv",
          warn_only=True)

check("Wav2Lip checkpoint exists", Path(wav2lip_ckpt).exists() if wav2lip_ckpt else False,
      f"Download wav2lip_gan.pth → {wav2lip_ckpt}" if not Path(wav2lip_ckpt).exists() else "",
      warn_only=True)

# ── Summary ──────────────────────────────────────────────────────────────────
total = len(results)
passed = sum(results)
print(f"\n{'═'*50}")
print(f"  RESULT: {passed}/{total} checks passed")
if passed == total:
    print("  🚀 Environment READY — run main.py")
else:
    print("  🔧 Fix the ❌ items above, then re-run verify_setup.py")
print(f"{'═'*50}\n")
