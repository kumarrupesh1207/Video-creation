"""
inspect_workflow.py
-------------------
Drop your workflow_api.json into the project root, then run:
    python src/inspect_workflow.py --workflow workflow_api.json

It prints a clean table of every node with:
  - Node ID (the key you need for config.yaml overrides)
  - Class type (what kind of node it is)
  - Key inputs (prompt, image, width, height, etc.)

It also auto-detects the "Key Nodes" needed by main.py and suggests
the override block to paste into config.yaml.
"""

import json
import argparse
from pathlib import Path

# ── Node types we care about ──────────────────────────────────────────────────
KEY_NODE_TYPES = {
    "CLIPTextEncode":   "TEXT PROMPT",
    "CLIPTextEncodeSDXL": "TEXT PROMPT (SDXL)",
    "LoadImage":        "IMAGE LOADER",
    "SaveImage":        "IMAGE OUTPUT",
    "VHS_VideoCombine": "VIDEO OUTPUT (VHS)",
    "VideoCombine":     "VIDEO OUTPUT",
    "KSampler":         "SAMPLER",
    "KSamplerAdvanced": "SAMPLER (Advanced)",
    "EmptyLatentImage": "LATENT SIZE",
    "SVD_img2vid_Conditioning": "SVD CONDITIONING",
    "ImageOnlyCheckpointLoader": "SVD CHECKPOINT",
    "CheckpointLoaderSimple":    "CHECKPOINT",
    "VAEDecode":        "VAE DECODE",
    "LoraLoader":       "LORA",
}

HIGHLIGHT = {
    "TEXT PROMPT", "TEXT PROMPT (SDXL)",
    "IMAGE LOADER", "IMAGE OUTPUT",
    "VIDEO OUTPUT (VHS)", "VIDEO OUTPUT",
    "SVD CONDITIONING", "SVD CHECKPOINT",
}


def load_workflow(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"[ERROR] Invalid JSON in {path}: {e}")


def summarise_inputs(inputs: dict) -> str:
    """Return a short human-readable summary of the most useful inputs."""
    keys = ["text", "image", "width", "height", "seed", "steps",
            "cfg", "sampler_name", "scheduler", "ckpt_name",
            "video_frames", "motion_bucket_id", "filename_prefix"]
    parts = []
    for k in keys:
        if k in inputs:
            v = inputs[k]
            if isinstance(v, list):       # node link reference
                v = f"→node[{v[0]}]"
            elif isinstance(v, str) and len(v) > 50:
                v = v[:47] + "..."
            parts.append(f"{k}={repr(v)}")
    return "  |  ".join(parts) if parts else ""


def analyse(workflow: dict) -> None:
    print("\n" + "═" * 80)
    print("  WORKFLOW NODE MAP")
    print("═" * 80)
    print(f"  {'ID':>4}  {'CLASS TYPE':<35}  {'ROLE':<25}  KEY INPUTS")
    print("─" * 80)

    key_nodes = {}   # role → node_id

    for node_id, node in sorted(workflow.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 9999):
        class_type = node.get("class_type", "unknown")
        role = KEY_NODE_TYPES.get(class_type, "")
        inputs = node.get("inputs", {})
        inp_str = summarise_inputs(inputs)

        flag = "★ " if role in HIGHLIGHT else "  "
        print(f"  {flag}{node_id:>4}  {class_type:<35}  {role:<25}  {inp_str}")

        if role:
            key_nodes.setdefault(role, []).append(node_id)

    print("═" * 80)

    # ── Suggested config.yaml overrides ──────────────────────────────────────
    print("\n  📋  SUGGESTED config.yaml OVERRIDE BLOCK\n")
    print("  Paste these IDs into your config.yaml under")
    print("  models → comfyui → image_workflow_overrides / video_workflow_overrides\n")

    for role, ids in key_nodes.items():
        for nid in ids:
            node = workflow[nid]
            class_type = node.get("class_type", "")
            inputs = node.get("inputs", {})

            if class_type in ("CLIPTextEncode", "CLIPTextEncodeSDXL"):
                print(f'  # {role} — set your prompt here')
                print(f'  "{nid}":')
                print(f'    text: "YOUR POSITIVE PROMPT HERE"\n')

            elif class_type == "EmptyLatentImage":
                print(f'  # LATENT SIZE — 576×1024 for 9:16 vertical')
                print(f'  "{nid}":')
                print(f'    width: 576')
                print(f'    height: 1024\n')

            elif class_type in ("SaveImage", "VHS_VideoCombine", "VideoCombine"):
                print(f'  # {role} — output node ID for polling')
                print(f'  output_node_id: "{nid}"\n')

            elif class_type == "KSampler":
                print(f'  # SAMPLER — seed control')
                print(f'  "{nid}":')
                print(f'    seed: 42  # or -1 for random\n')

    print("─" * 80)
    print("  ★ = High-priority nodes to wire into main.py overrides")
    print("═" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Inspect a ComfyUI workflow_api.json and extract node IDs."
    )
    parser.add_argument(
        "--workflow",
        default="workflow_api.json",
        help="Path to the workflow_api.json file (default: workflow_api.json)"
    )
    args = parser.parse_args()

    path = Path(args.workflow)
    if not path.exists():
        print(f"\n[ERROR] File not found: {path}")
        print("  1. Open ComfyUI → Settings → Enable Dev Mode")
        print("  2. Click 'Save (API Format)' → save as workflow_api.json")
        print(f"  3. Place it at: {Path.cwd() / 'workflow_api.json'}\n")
        raise SystemExit(1)

    workflow = load_workflow(path)
    analyse(workflow)


if __name__ == "__main__":
    main()
