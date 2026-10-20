"""
first_light.py  — "First Light" ComfyUI Handshake Test
─────────────────────────────────────────────────────────
Triggers one image generation through ComfyUI and verifies
the full Python → WebSocket → GPU → output pipeline.

Usage (run from project root):
    python src/first_light.py

Or with custom prompt / workflow:
    python src/first_light.py --prompt "Neon city at night" --workflow src/workflows/workflow_api.json

Success Criteria:
    ✅ ComfyUI terminal shows "Prompt queued"
    ✅ Task Manager → GPU spikes during generation
    ✅ Image appears in outputs/images/
"""

import sys
import time
import yaml
import argparse
from pathlib import Path

# ── Make src/ importable from project root ────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))   # add project root
sys.path.insert(0, str(Path(__file__).parent))          # add src/

from modules.comfy_orchestrator import ComfyOrchestrator


DEFAULT_PROMPT   = (
    "Cinematic portrait of a fintech entrepreneur, photorealistic, 8k, "
    "soft studio lighting, shallow depth of field"
)
DEFAULT_WORKFLOW = "src/workflows/workflow_api.json"
CONFIG_PATH      = "config.yaml"


def load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config.yaml not found at: {p.resolve()}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def build_overrides(cfg: dict, prompt: str) -> dict:
    """
    Read node IDs from config.yaml image_overrides and build the
    override dict that ComfyOrchestrator.run_workflow() expects.
    """
    overrides_cfg = cfg.get("models", {}).get("comfyui", {}).get("image_overrides", {})

    prompt_node  = str(overrides_cfg.get("positive_prompt_node", "6"))
    neg_node     = str(overrides_cfg.get("negative_prompt_node", "7"))
    latent_node  = str(overrides_cfg.get("latent_size_node",     "5"))
    sampler_node = str(overrides_cfg.get("sampler_node",         "3"))

    res = cfg.get("project", {}).get("resolution", [576, 1024])

    overrides = {
        prompt_node:  {"text": prompt},
        neg_node:     {"text": "blurry, low quality, watermark, distorted, ugly"},
        latent_node:  {"width": res[0], "height": res[1]},
        sampler_node: {"seed": int(time.time()) % 2**31},  # random-ish seed
    }
    print("\n[FirstLight] Override map:")
    for nid, vals in overrides.items():
        for k, v in vals.items():
            v_str = (str(v)[:60] + "...") if len(str(v)) > 60 else str(v)
            print(f"  Node [{nid}] → {k}: {v_str}")
    print()
    return overrides


def main():
    parser = argparse.ArgumentParser(description="First Light — ComfyUI Handshake Test")
    parser.add_argument("--prompt",   default=DEFAULT_PROMPT,   help="Image prompt text")
    parser.add_argument("--workflow", default=DEFAULT_WORKFLOW,  help="Path to workflow_api.json")
    parser.add_argument("--config",   default=CONFIG_PATH,       help="Path to config.yaml")
    args = parser.parse_args()

    print("=" * 60)
    print("  🚀 VIDEO FACTORY — First Light Test")
    print("=" * 60)

    # 1. Load config
    print(f"\n[Config] Loading: {args.config}")
    try:
        cfg = load_config(args.config)
        print("[Config] ✅ Loaded.")
    except FileNotFoundError as e:
        print(f"[Config] ❌ {e}")
        sys.exit(1)

    # 2. Check workflow exists
    wf_path = Path(args.workflow)
    if not wf_path.exists():
        print(f"\n[FirstLight] ❌ Workflow not found: {wf_path.resolve()}")
        print("  Step 1: Open ComfyUI in browser (http://127.0.0.1:8188)")
        print("  Step 2: Settings → Enable Dev Mode Options")
        print("  Step 3: Load your image workflow")
        print(f"  Step 4: Click 'Save (API Format)' → save to: {wf_path.resolve()}")
        print("\n  Then re-run: python src/first_light.py")
        sys.exit(1)

    # 3. Connect to ComfyUI
    print("\n[ComfyUI] Connecting...")
    try:
        orch = ComfyOrchestrator(cfg)
    except RuntimeError as e:
        print(str(e))
        print("\n  → Start ComfyUI first, then re-run this script.")
        sys.exit(1)

    # 4. Build overrides
    overrides = build_overrides(cfg, args.prompt)

    # 5. Run workflow
    t_start = time.time()
    print(f"[FirstLight] Sending to ComfyUI: {wf_path}")
    try:
        outputs = orch.run_workflow(str(wf_path), overrides)
    except Exception as e:
        print(f"\n[FirstLight] ❌ Generation failed: {e}")
        sys.exit(1)

    elapsed = time.time() - t_start

    # 6. Report results
    print("\n" + "=" * 60)
    print(f"  ✅ FIRST LIGHT COMPLETE  ({elapsed:.1f}s)")
    print("=" * 60)
    if outputs:
        print(f"\n  📁 Output(s):")
        for p in outputs:
            print(f"     {p.resolve()}")
    else:
        print("\n  ⚠️  No outputs downloaded.")
        print("     Check ComfyUI for errors, or verify output node ID in config.yaml")

    print(f"\n  Next step → run: python src/inspect_workflow.py")
    print("              to confirm all node IDs before full pipeline.\n")


if __name__ == "__main__":
    from aurora import ensure_utf8_console

    ensure_utf8_console()  # emoji prints must not crash on Windows cp1252 consoles
    main()
