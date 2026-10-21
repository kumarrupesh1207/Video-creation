# Aurora Kaggle worker (runs INSIDE a Kaggle GPU notebook/script kernel; T4/P100 ~16 GB).
# The provider prepends `REQUEST = {...}`. To use standalone in a notebook, define REQUEST yourself:
#   mode "t2i": {"mode":"t2i","model":"stabilityai/stable-diffusion-xl-base-1.0" | "black-forest-labs/FLUX.1-schnell",
#                "prompt":"...","negative":"...","width":768,"height":1344,"steps":30,"seed":1}
#   mode "i2v": {"mode":"i2v","model":"Wan-AI/Wan2.2-TI2V-5B-Diffusers","prompt":"...","image_b64":"...",
#                "width":832,"height":480,"num_frames":49,"steps":30,"seed":1}
# Outputs land in /kaggle/working (.png for t2i, .mp4 for i2v). OFF until the user supplies Kaggle credentials.
# CAVEAT (docs/research/GPU_BACKENDS.md): TI2V-5B is officially a 24 GB model. On 16 GB Kaggle GPUs this uses
# CPU offload + 480p + ~2 s clips; expect slow or OOM-prone runs. Use a Kaggle T4x2/P100 and lower num_frames if it fails.
import base64
import io
import os
import subprocess
import sys

subprocess.run([sys.executable, "-m", "pip", "install", "-q", "diffusers>=0.35", "transformers", "accelerate",
                "sentencepiece", "imageio[ffmpeg]", "ftfy"], check=False)

import torch  # noqa: E402
from PIL import Image  # noqa: E402

OUT = "/kaggle/working" if os.path.isdir("/kaggle/working") else "."
seed = REQUEST.get("seed") or 1234  # noqa: F821 - REQUEST is injected
gen = torch.Generator("cuda").manual_seed(int(seed))
mode = REQUEST["mode"]  # noqa: F821

if mode == "t2i":
    model = REQUEST.get("model", "stabilityai/stable-diffusion-xl-base-1.0")  # noqa: F821
    if "FLUX" in model:
        from diffusers import FluxPipeline

        pipe = FluxPipeline.from_pretrained(model, torch_dtype=torch.bfloat16)
        pipe.enable_model_cpu_offload()
        image = pipe(REQUEST["prompt"], width=REQUEST.get("width", 768), height=REQUEST.get("height", 1344),  # noqa: F821
                     num_inference_steps=4, guidance_scale=0.0, generator=gen).images[0]
    else:
        from diffusers import StableDiffusionXLPipeline

        pipe = StableDiffusionXLPipeline.from_pretrained(model, torch_dtype=torch.float16, variant="fp16")
        pipe.enable_model_cpu_offload()
        image = pipe(REQUEST["prompt"], negative_prompt=REQUEST.get("negative", "lowres, bad anatomy, bad hands, extra fingers, deformed"),  # noqa: F821
                     width=REQUEST.get("width", 768), height=REQUEST.get("height", 1344),  # noqa: F821
                     num_inference_steps=REQUEST.get("steps", 30), guidance_scale=5.5, generator=gen).images[0]  # noqa: F821
    image.save(os.path.join(OUT, "out.png"))
elif mode == "i2v":
    from diffusers import AutoencoderKLWan, WanImageToVideoPipeline
    from diffusers.utils import export_to_video

    vae = AutoencoderKLWan.from_pretrained(REQUEST["model"], subfolder="vae", torch_dtype=torch.float32)  # noqa: F821
    pipe = WanImageToVideoPipeline.from_pretrained(REQUEST["model"], vae=vae, torch_dtype=torch.bfloat16)  # noqa: F821
    pipe.enable_model_cpu_offload()
    print("loaded", torch.cuda.get_device_name(0), flush=True)
    pipe.vae.enable_tiling()
    image = Image.open(io.BytesIO(base64.b64decode(REQUEST["image_b64"]))).convert("RGB")  # noqa: F821
    w, h = REQUEST.get("width", 832), REQUEST.get("height", 480)  # noqa: F821
    frames = pipe(image=image.resize((w, h)), prompt=REQUEST["prompt"], width=w, height=h,  # noqa: F821
                  num_frames=REQUEST.get("num_frames", 49), num_inference_steps=REQUEST.get("steps", 30),  # noqa: F821
                  guidance_scale=5.0, generator=gen).frames[0]
    export_to_video(frames, os.path.join(OUT, "out.mp4"), fps=24)
else:
    raise SystemExit(f"unknown mode {mode}")
print("done", mode)
