"""Perf bench for PERF_BASELINE.md: python scripts/perf_bench.py {nim|comfy|reel} [--w 512 --h 512 --steps 15]."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["nim", "comfy", "reel"])
    ap.add_argument("--w", type=int, default=512)
    ap.add_argument("--h", type=int, default=512)
    ap.add_argument("--steps", type=int, default=None)
    ap.add_argument("--image", default=None)
    ap.add_argument("--tag", default="run")
    a = ap.parse_args()
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    prompt = "portrait photo of a man in a navy suit, studio light"
    t = time.perf_counter()
    if a.what == "nim":
        from backend.app.services.image_switchboard import ImageSwitchboard
        cfg["providers"]["fallback_order"] = ["nvidia_nim"]
        r = ImageSwitchboard(cfg).generate(prompt, "cinematic", [a.w, a.h], f"perf_nim_{a.tag}", force_new_image=True)
        print("RESULT", r.provider, r.width, r.height, r.output_path, f"{time.perf_counter()-t:.1f}s")
    elif a.what == "comfy":
        from backend.app.providers.local_comfyui import LocalComfyUIProvider
        kw = {"steps": a.steps} if a.steps else {}
        r = LocalComfyUIProvider(cfg).generate_with_params(
            "a woman in a red ethnic dress, garden, natural light", "cinematic", [a.w, a.h], f"perf_comfy_{a.tag}", **kw)
        print("RESULT", r.provider, r.width, r.height, r.output_path, f"{time.perf_counter()-t:.1f}s")
    else:
        from aurora.pipeline import run
        out = ROOT / "outputs" / "perf" / f"reel_{a.tag}.mp4"
        run(Path(a.image), out, 15.0)
        print("RESULT reel", out, f"{time.perf_counter()-t:.1f}s")


if __name__ == "__main__":
    from aurora import ensure_utf8_console

    ensure_utf8_console()  # emoji prints must not crash on Windows cp1252 consoles
    main()
