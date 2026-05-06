"""
download_models.py
──────────────────
Downloads the required models for the AI Video Factory.
Run this ONCE before using main.py.

Models downloaded:
  1. Realistic Vision V6 (SD 1.5) — Image generation ~2 GB
     Best for: photorealistic people, faces, skin detail

Usage:
    python src/download_models.py
    python src/download_models.py --model rv6     # image model only
    python src/download_models.py --check-only    # just verify what's installed
"""

import sys
import argparse
import requests
from pathlib import Path
from urllib.request import urlretrieve

COMFYUI_BASE = (
    r"C:\Rupesh\App\ComfyUI_windows_portable_nvidia"
    r"\ComfyUI_windows_portable\ComfyUI\models"
)

MODELS = {
    "rv6": {
        "name":    "Realistic_Vision_V6.0_NV_B1_fp16.safetensors",
        "dest":    "checkpoints",
        "size_gb": 2.0,
        "url":     (
            "https://huggingface.co/SG161222/Realistic_Vision_V6.0_B1_noVAE"
            "/resolve/main/Realistic_Vision_V6.0_NV_B1_fp16.safetensors"
        ),
        "desc": "Realistic Vision V6 fp16 — photorealistic people/faces"
    },
    "rv6_vae": {
        "name":    "vae-ft-mse-840000-ema-pruned.safetensors",
        "dest":    "vae",
        "size_gb": 0.33,
        "url":     (
            "https://huggingface.co/stabilityai/sd-vae-ft-mse-original"
            "/resolve/main/vae-ft-mse-840000-ema-pruned.safetensors"
        ),
        "desc": "SD 1.5 VAE — fixes washed-out colors"
    },
}


def check_comfyui_api() -> bool:
    try:
        r = requests.get("http://127.0.0.1:8188/models/checkpoints", timeout=3)
        models = r.json().get("value", [])
        print(f"[ComfyUI] Connected. Installed checkpoints: {models or 'none'}")
        return True
    except Exception:
        print("[ComfyUI] Not reachable — start ComfyUI before downloading if you want API verify.")
        return False


def progress_hook(block_num, block_size, total_size):
    downloaded = block_num * block_size
    pct = min(100, downloaded * 100 // total_size) if total_size > 0 else 0
    bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
    mb_done = downloaded / (1024 * 1024)
    mb_total = total_size / (1024 * 1024)
    print(f"\r  [{bar}] {pct:3d}%  {mb_done:.0f}/{mb_total:.0f} MB", end="", flush=True)


def download_model(key: str) -> bool:
    info    = MODELS[key]
    dest    = Path(COMFYUI_BASE) / info["dest"]
    dest.mkdir(parents=True, exist_ok=True)
    outfile = dest / info["name"]

    if outfile.exists():
        size_gb = outfile.stat().st_size / 1e9
        print(f"[{key}] ✅ Already exists: {info['name']} ({size_gb:.2f} GB)")
        return True

    print(f"\n[{key}] Downloading: {info['desc']}")
    print(f"       Size: ~{info['size_gb']} GB")
    print(f"       Dest: {outfile}")
    print(f"       URL : {info['url'][:70]}...")

    try:
        urlretrieve(info["url"], str(outfile), reporthook=progress_hook)
        print()
        size_gb = outfile.stat().st_size / 1e9
        print(f"[{key}] ✅ Downloaded: {info['name']} ({size_gb:.2f} GB)")
        return True
    except Exception as e:
        print(f"\n[{key}] ❌ Download failed: {e}")
        print(f"  Manual download URL: {info['url']}")
        print(f"  Save to: {outfile}")
        if outfile.exists():
            outfile.unlink()   # Remove partial file
        return False


def check_only():
    print("\n[Check] Scanning installed models...\n")
    base = Path(COMFYUI_BASE)
    found_any = False
    for key, info in MODELS.items():
        path = base / info["dest"] / info["name"]
        if path.exists():
            gb = path.stat().st_size / 1e9
            print(f"  ✅ {info['name']} ({gb:.2f} GB)")
            found_any = True
        else:
            print(f"  ❌ MISSING: {info['name']} → run without --check-only to download")

    # Also report any unknown models
    ckpt_dir = base / "checkpoints"
    if ckpt_dir.exists():
        existing = [f.name for f in ckpt_dir.glob("*.safetensors")]
        unknown  = [f for f in existing if f not in [m["name"] for m in MODELS.values()]]
        if unknown:
            print(f"\n  Other models found in checkpoints/:")
            for u in unknown:
                print(f"    • {u}")

    if not found_any:
        print("\n  No models installed yet. Run: python src/download_models.py")
    print()


def update_workflow_ckpt(model_name: str) -> None:
    """Auto-update image_gen.json with the correct ckpt_name."""
    wf_path = Path("src/workflows/image_gen.json")
    if not wf_path.exists():
        return
    import json
    wf = json.loads(wf_path.read_text(encoding="utf-8"))
    if "4" in wf and "inputs" in wf["4"]:
        old = wf["4"]["inputs"].get("ckpt_name", "")
        wf["4"]["inputs"]["ckpt_name"] = model_name
        wf_path.write_text(json.dumps(wf, indent=2), encoding="utf-8")
        if old != model_name:
            print(f"[Workflow] Updated image_gen.json ckpt_name: {old} → {model_name}")


def main():
    parser = argparse.ArgumentParser(description="Download models for AI Video Factory")
    parser.add_argument("--model",      default="all",
                        choices=["all", "rv6", "rv6_vae"],
                        help="Which model to download (default: all)")
    parser.add_argument("--check-only", action="store_true",
                        help="Just check what's installed, don't download")
    args = parser.parse_args()

    print("=" * 55)
    print("  AI Video Factory — Model Downloader")
    print("=" * 55)

    check_comfyui_api()

    if args.check_only:
        check_only()
        return

    keys = list(MODELS.keys()) if args.model == "all" else [args.model]
    success = []

    for key in keys:
        ok = download_model(key)
        if ok:
            success.append(key)

    print("\n" + "=" * 55)
    print(f"  Downloaded: {len(success)}/{len(keys)}")

    # Auto-update workflow with correct checkpoint name
    if "rv6" in success:
        update_workflow_ckpt(MODELS["rv6"]["name"])
        print(f"\n  ✅ image_gen.json updated with: {MODELS['rv6']['name']}")

    print("\n  Next steps:")
    print("  1. Restart ComfyUI (to refresh model list)")
    print("  2. Run: python main.py --preset beach_run_720p --topic \"Final Quality Test\" --frames 12 --motion 120 --skip-lipsync")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    main()
